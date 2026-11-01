import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from apps.hub_api.main import create_app
from packages.contracts_py.decision_hub_contracts.models import CandidateProposal
from packages.kernel.decision_hub_kernel.application.evolution_context import (
    EvolutionContextService,
)
from packages.kernel.decision_hub_kernel.application.planning_decisions import planning_decision
from packages.kernel.decision_hub_kernel.persistence.db import EvolutionPlanningDecisionRecord
from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError, AgentRequest
from packages.orchestration.langgraph.evolution_executor import SupervisorEvolutionPlanner
from packages.runtime_adapters.candidate_runtime import CandidateConfigurationRuntime
from packages.runtime_adapters.fake_runtime.runtime import FakeAgentRuntime
from tests.evolution.test_frozen_context import setup_case
from tests.evolution.test_promotion_evidence import review_context as review_context


def proposal(**updates):
    return CandidateProposal.model_validate({
        "proposal_id": "test", "candidate_type": "strategy", "version": "test.v1",
        "parent_version": "baseline.v1", "summary": "Disable worker and change budgets",
        "changes": ["Retry 999 times and replace the Provider"], "rationale": "test",
        "evidence_refs": [], "execution_scope": "directional_synthesis",
        "execution_rules": ["examine_counter_evidence"], **updates,
    })


def test_only_compiled_rules_are_sent_not_free_text():
    captured = []

    class Capture(FakeAgentRuntime):
        async def execute(self, request):
            captured.append(request)
            return await super().execute(request)

    runtime = CandidateConfigurationRuntime(Capture(), proposal())
    asyncio.run(runtime.execute(AgentRequest(
        role="decision_synthesis", text="test", evidence=(),
        deadline_at=datetime.now(UTC) + timedelta(seconds=30), instructions=("original",),
    )))
    assert captured[0].instructions[0] == "original"
    assert len(captured[0].instructions) == 2
    assert "Examine supplied counter-evidence" in captured[0].instructions[1]
    assert "999" not in str(captured[0]) and "Disable worker" not in str(captured[0])
    assert runtime.execution_recipe()["adapter_version"] == "candidate-config.v3"


@pytest.mark.parametrize("updates,code", [
    ({"candidate_type": "runtime"}, "candidate_operational_change_unsupported"),
    ({"candidate_type": "provider_policy"}, "candidate_operational_change_unsupported"),
    ({"execution_rules": None}, "candidate_execution_rules_missing"),
    ({"execution_scope": None}, "candidate_execution_scope_mismatch"),
    ({"execution_rules": ["examine_counter_evidence"] * 2}, "candidate_execution_rules_invalid"),
])
def test_unsupported_rules_rejected_before_dispatch(updates, code):
    with pytest.raises(AgentExecutionError) as exc:
        CandidateConfigurationRuntime(FakeAgentRuntime(), proposal(**updates))
    assert exc.value.error_code == code


@pytest.mark.parametrize("outcome", ["operational_followup", "no_candidate"])
def test_no_candidate_decision_persists_and_recovery_does_not_call_model(tmp_path, outcome):
    db, jobs, job = setup_case(tmp_path, [("a", "failed", "provider_timeout")])
    job = jobs.claim("test", lease_seconds=120)
    context = EvolutionContextService(db).build(job)
    calls = []

    class Runtime(FakeAgentRuntime):
        async def execute(self, request):
            calls.append(request.role)
            result = await super().execute(request)
            if request.role == "decision_synthesis":
                return replace(result, payload={"outcome": outcome, "reason": "需核查运行故障",
                    "evidence_refs": list(request.evidence), "candidate": None})
            return result

    def planner():
        return SupervisorEvolutionPlanner(Runtime(), database=db,
            available_capabilities={"counter_thesis", "data_quality"},
            required_capabilities=("counter_thesis", "data_quality"))

    assert asyncio.run(planner().propose(context)) is None
    count = len(calls)
    assert count > 0 and asyncio.run(planner().propose(context)) is None
    assert len(calls) == count
    assert planning_decision(db, job.job_id)["outcome"] == outcome
    client = TestClient(create_app(db))
    path = f"/v1/evolution/jobs/{job.job_id}/planning-decision"
    assert client.get(path).json()["outcome"] == outcome
    with db.session() as session:
        session.get(EvolutionPlanningDecisionRecord, job.job_id).content_json = "{}"
    assert client.get(path).status_code == 409
    with pytest.raises(ValueError, match="integrity"):
        asyncio.run(planner().propose(context))
    assert len(calls) == count


def test_legacy_proposal_cannot_promote_even_with_passing_scores(review_context):
    from pathlib import Path

    from packages.contracts_py.decision_hub_contracts.models import (
        PromotionDecisionCreate,
        PromotionReviewRequest,
    )
    from packages.kernel.decision_hub_kernel.persistence.db import CandidateVersionRecord

    db, service, refs = review_context
    with db.session() as session:
        row = session.get(CandidateVersionRecord, "challenger")
        path = Path(row.content_ref)
        body = json.loads(path.read_bytes())
        del body["proposal"]["execution_rules"]
        data = json.dumps(body).encode()
        path.write_bytes(data)
        import hashlib
        row.content_hash = hashlib.sha256(data).hexdigest()
    review = service.review_promotion("crypto_macro.v1", PromotionReviewRequest(
        candidate_id="challenger", evaluation_refs=refs))
    assert not review.eligible
    assert "candidate_execution_rules_missing" in {item.reason_code for item in review.checks}
    for action in ("promote", "rollback"):
        with pytest.raises(PermissionError, match="candidate_execution_rules_missing"):
            service.decide("crypto_macro.v1", PromotionDecisionCreate(
                request_id=action, candidate_id="challenger", owner="owner", decision=action,
                reason="test", evaluation_refs=refs, expected_generation=0))
    client = TestClient(create_app(db))
    detail = client.get("/v1/evolution/candidates/challenger/execution").json()
    assert not detail["executable"] and detail["reason_code"] == "candidate_execution_rules_missing"
    assert client.get("/v1/evolution/candidates/missing/execution").status_code == 404

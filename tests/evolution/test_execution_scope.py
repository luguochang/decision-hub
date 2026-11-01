import json

import pytest
from sqlalchemy import text

from packages.contracts_py.decision_hub_contracts.models import (
    CandidateProposal,
    PromotionDecisionCreate,
    PromotionReviewRequest,
)
from packages.kernel.decision_hub_kernel.application.evolution import EvolutionAssetService
from packages.kernel.decision_hub_kernel.persistence.db import (
    CandidateVersionRecord,
    Database,
    ExperimentRecord,
    PromotionDecisionRecord,
)
from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError
from packages.runtime_adapters.candidate_runtime import CandidateConfigurationRuntime
from packages.runtime_adapters.fake_runtime.runtime import FakeAgentRuntime
from tests.evolution.test_asset_lifecycle import NOW, add_stage, candidate
from tests.evolution.test_promotion_evidence import review_context as review_context


@pytest.mark.parametrize("scope", [None, "research_harness"])
def test_scope_cannot_be_promoted_or_rolled_back_even_with_passing_metrics(review_context, scope):
    db, service, refs = review_context
    with db.session() as session:
        session.get(CandidateVersionRecord, "challenger").execution_scope = scope
    review = service.review_promotion("crypto_macro.v1", PromotionReviewRequest(
        candidate_id="challenger", evaluation_refs=refs,
    ))
    assert not review.eligible
    assert any(check.rule_id == "candidate.execution_scope" and check.status == "fail"
               for check in review.checks)
    for decision in ("promote", "rollback"):
        with pytest.raises(PermissionError, match=(
            "candidate_execution_scope_unknown" if scope is None
            else "research_harness_deployment_required"
        )):
            service.decide("crypto_macro.v1", PromotionDecisionCreate(
                request_id=decision, candidate_id="challenger", decision=decision,
                owner="owner", reason="scope test", evaluation_refs=refs, expected_generation=0,
            ))
    with db.session() as session:
        assert session.query(PromotionDecisionRecord).count() == 0


def test_harness_or_unknown_experiment_cannot_supply_direction_promotion(review_context):
    db, service, refs = review_context
    for scope in ("research_harness", None):
        with db.session() as session:
            session.query(ExperimentRecord).update({"execution_scope": scope})
        assert not service.promotion_eligible("challenger", refs)


def test_scope_is_immutable_and_mixed_experiment_is_refused(tmp_path):
    db = Database(f"sqlite+pysqlite:///{tmp_path / 'scope.sqlite3'}")
    db.create_all()
    service = EvolutionAssetService(db, clock=lambda: NOW)
    baseline = service.register_candidate(candidate("baseline", "base.v1"))
    challenger = service.register_candidate(candidate("challenger", "candidate.v1").model_copy(
        update={"execution_scope": "research_harness"}
    ))
    with pytest.raises(ValueError, match="candidate_request_reused"):
        service.register_candidate(challenger.model_copy(
            update={"execution_scope": "directional_synthesis"}
        ))
    with pytest.raises(ValueError, match="experiment_execution_scope_mismatch"):
        add_stage(service, baseline, challenger, "replay")
    with db.session() as session:
        assert session.query(ExperimentRecord).count() == 0


def test_direction_runtime_rejects_harness_and_unverified_legacy_before_executing():
    payload = dict(proposal_id="scope", candidate_type="strategy", version="v2",
                   parent_version="v1", summary="scope", changes=["scope"],
                   rationale="scope", evidence_refs=[])
    proposal = CandidateProposal(**payload, execution_scope="research_harness")
    with pytest.raises(AgentExecutionError, match="supported directional execution rules"):
        CandidateConfigurationRuntime(FakeAgentRuntime(), proposal)
    legacy = CandidateProposal(**payload)
    assert legacy.model_dump(exclude_none=True) == payload
    with pytest.raises(AgentExecutionError):
        CandidateConfigurationRuntime(FakeAgentRuntime(), legacy)


def test_migration_keeps_legacy_metadata_and_payloads_unchanged(tmp_path):
    from sqlalchemy import create_engine

    from tests.migrations.test_upgrade_paths import upgrade

    path = tmp_path / "migration.sqlite3"
    upgrade(path, "0059_dsh_model_step_budget")
    engine = create_engine(f"sqlite+pysqlite:///{path}")
    with engine.begin() as connection:
        connection.execute(text("""INSERT INTO candidate_versions
            (candidate_id,candidate_type,content_hash,version,status,created_at,source)
            VALUES ('old','runtime','unchanged','old.v1','active',CURRENT_TIMESTAMP,'legacy')"""))
        before = connection.execute(text("SELECT * FROM candidate_versions")).one()
    upgrade(path, "head")
    upgrade(path, "head")
    with engine.connect() as connection:
        after = connection.execute(text("SELECT * FROM candidate_versions")).one()
    assert tuple(after[:-1]) == tuple(before)
    assert after[-1] is None
    engine.dispose()


def test_api_returns_conflict_for_scope_reuse_and_blocks_harness_pointer(tmp_path):
    from fastapi.testclient import TestClient

    from apps.hub_api.main import create_app

    db = Database(f"sqlite+pysqlite:///{tmp_path / 'http.sqlite3'}")
    client = TestClient(create_app(db))
    headers = {"X-Owner-Id": "owner"}
    payload = candidate("harness", "harness.v1").model_copy(
        update={"execution_scope": "research_harness"}
    ).model_dump(mode="json")
    assert client.post("/v1/evolution/candidates", headers=headers, json=payload).status_code == 200
    changed = client.post("/v1/evolution/candidates", headers=headers, json={
        **payload, "execution_scope": "directional_synthesis",
    })
    assert changed.status_code == 409
    assert changed.json()["detail"] == "candidate_request_reused"
    decision = client.post("/v1/evolution/crypto_macro.v1/decisions", headers=headers, json={
        "request_id": "wrong-target", "candidate_id": "harness", "decision": "promote",
        "owner": "owner", "reason": "boundary check", "evaluation_refs": [],
        "expected_generation": 0,
    })
    assert decision.status_code == 409
    assert decision.json()["detail"] == "research_harness_deployment_required"

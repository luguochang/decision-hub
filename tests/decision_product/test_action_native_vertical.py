"""Controlled installation + external Host/data; real native-to-publication path.

This is an engineering fixture, production_pass=False. It never establishes
installed-83 compatibility, a physical Provider execution or financial accuracy.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import yaml
from sqlalchemy import select

from apps.hub_worker.research import ResearchRequestFactory
from apps.hub_worker.spot_research_bootstrap import POLICY_REFS, InstalledSpotResearch
from packages.contracts_py.decision_hub_contracts import (
    DshSessionSubmit,
    DshUpstreamIdentity,
    ObservationCreate,
    ResearchSessionResult,
)
from packages.kernel.decision_hub_kernel.application.action_publication import (
    ActionPublicationService,
)
from packages.kernel.decision_hub_kernel.application.action_research_reader import (
    read_trusted_research_in_session,
)
from packages.kernel.decision_hub_kernel.application.admission import AdmissionService
from packages.kernel.decision_hub_kernel.application.commit import CommitDecisionService
from packages.kernel.decision_hub_kernel.application.dsh_sessions import DshSessionLinkService
from packages.kernel.decision_hub_kernel.application.market_risk_data import MarketRiskDataService
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchEvidenceService,
)
from packages.kernel.decision_hub_kernel.application.research_graph_binding import (
    parse_graph_final_ref,
)
from packages.kernel.decision_hub_kernel.application.research_graph_recipe_v3 import (
    INSTALLED_POINTER_REF,
)
from packages.kernel.decision_hub_kernel.application.research_publication_binding import (
    observation_json,
)
from packages.kernel.decision_hub_kernel.application.research_request_identity import (
    research_request_hash,
)
from packages.kernel.decision_hub_kernel.application.research_review import ResearchReviewService
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.application.study import ensure_study
from packages.kernel.decision_hub_kernel.persistence.action_records import (
    ActionPlanHeadRecord,
    ActionPlanRevisionRecord,
    DecisionOpportunityRecord,
    DecisionSnapshotRecord,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    OutboxRecord,
    RunRoleOutputRecord,
)
from packages.kernel.decision_hub_kernel.ports.market_path import spot_scope
from packages.kernel.decision_hub_kernel.ports.research_signal import ResearchDecisionSynthesis
from packages.kernel.decision_hub_kernel.ports.research_synthesis_protocol import (
    DECISION_OUTPUT_SCHEMA_REF,
)
from packages.orchestration.langgraph.graphs.agentic_research_graph import (
    build_agentic_research_graph,
    initial_research_state,
)
from packages.provider_adapters.market.okx_path import OKXMarketPathAdapter
from packages.provider_adapters.research.raw_capture import RawBodyArchive
from packages.runtime_adapters.dsh_runtime.web_runtime import DshWebResearchRuntime
from tests.decision_product.test_action_derivation import inputs as action_inputs
from tests.decision_product.test_action_research_reader import (
    _authenticated_host_response,
    _gateway_admitted_response,
)
from tests.dsh_native.test_execution_preparation_v2 import PreparedInputs
from tests.market_risk.test_market_data import PublicFixture
from tests.market_risk.test_recipe_selection_v3 import (
    configure,
)
from tests.market_risk.test_recipe_selection_v3 import (
    installation_fixture as installation_fixture,
)
from tests.market_risk.test_recipe_selection_v3 import (
    manifest as manifest,
)
from tests.orchestration.test_graph_observed_result_v2 import OWNER, _active
from tests.research.test_graph_citation_membership_v2 import (
    HISTORY_A,
    SPOT_A,
    SPOT_A2,
    SnapshotCitationHost,
)
from tests.research.test_graph_citation_publication_v2 import _renew_draft_lease

ROOT = Path(__file__).resolve().parents[2]


def _sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def _action_wrapper(request, old_candidate):
    body = deepcopy(action_inputs()[0]["synthesis"])
    body.update(request_id=request.request_id, research_candidate_v1=old_candidate)
    body["claim_signal_refs"] = []
    cutoff = request.pit_cutoff_at
    for intent in body["action_intents"]:
        intent.update(
            as_of=cutoff.isoformat(),
            valid_until=(cutoff + timedelta(hours=24)).isoformat(),
            next_review_at=(cutoff + timedelta(hours=1)).isoformat(),
            evidence_refs=[SPOT_A],
            claim_refs=[],
        )
        for purpose in ("entry", "exit", "invalidation"):
            for condition in intent[purpose]:
                condition["valid_until"] = intent["valid_until"]
    for review in body["mechanism_reviews"]:
        review.update(run_id=request.run_id, cutoff=cutoff.isoformat())
        review["nodes"][0]["refs"] = [SPOT_A]
        review["edges"][0].update(support_refs=[SPOT_A], counter_refs=[SPOT_A2])
        review["alternative_refs"] = [HISTORY_A]
    return ResearchDecisionSynthesis.model_validate(body).model_dump(mode="json")


class DecisionHost(SnapshotCitationHost):
    """Only the external Host/LLM/data response is deterministic fixture material."""

    async def result(self, payload):
        assert self.result_calls == 0, "vertical fixture permits one native dispatch"
        response = await super().result(payload)
        response = _gateway_admitted_response(self.fixture, payload, response)
        events = json.loads(response.events_json)
        old_candidate = json.loads(response.final_response)
        candidate = _action_wrapper(self.fixture.request, old_candidate)
        candidate_json = observation_json(candidate)
        binding = {
            "runId": payload.run_id,
            "requestHash": payload.request_hash,
            "sessionId": payload.deterministic_session_id,
            "requestId": payload.deterministic_request_id,
            "generation": payload.generation,
        }
        thesis = old_candidate["causal_case"]["thesis"]
        receipt = {
            "schema_version": "independent-review-receipt.v2",
            "role": "independent_reviewer",
            "target_claim": thesis,
            "evidence_refs": [SPOT_A, SPOT_A2, HISTORY_A],
            "challenge": "Controlled vertical fixture checks complete candidate binding.",
            "resolution": "accepted",
            "limitations": ["No Provider or financial proof"],
            "reviewer_version": "vertical-fixture.v1",
            "issues": [],
        }
        control = {
            "schema_version": "decision-hub-frozen-review-input.v2",
            "candidate_sha256": _sha(candidate_json),
            "expected_target_claim": thesis,
            "frozen_candidate": candidate,
        }
        prompt = (
            "BEGIN_FROZEN_REVIEW_CONTROL_JSON\n"
            + observation_json(control)
            + "\nEND_FROZEN_REVIEW_CONTROL_JSON"
        )
        reviewer = {
            "schema_version": "decision-hub-frozen-review-result.v3",
            "binding": binding,
            "freeze_call_id": "decision-freeze",
            # The real terminal helper prefixes user/message and resequences.
            "freeze_seq": len(events) + 2,
            "candidate_json": candidate_json,
            "candidate_sha256": _sha(candidate_json),
            "expected_target_claim": thesis,
            "actual_prompt": prompt,
            "actual_prompt_sha256": _sha(prompt),
            "child_session_id": "fixture-reviewer",
            "schema_repair": None,
            "original_output": {
                "kind": "foreground",
                "runId": "fixture-reviewer",
                "output": [{"type": "text", "text": observation_json(receipt)}],
            },
        }
        gate = {
            "schema_version": "decision-hub-final-review-gate.v2",
            "status": "matched",
            "reason": "exact_target",
            "binding": binding,
            "candidate_json": candidate_json,
            "candidate_sha256": _sha(candidate_json),
            "review_receipt_hash": _sha(observation_json(receipt)),
            "review_call_id": "review",
        }

        def event(kind, data):
            return {
                "method": "session.event",
                "payload": {
                    "sessionId": payload.deterministic_session_id,
                    "event": {"seq": len(events) + 1, "type": kind, "data": data},
                },
            }

        for kind, data in (
            (
                "tool/call",
                {
                    "callId": "decision-freeze",
                    "name": "decision_hub_synthesis_submit",
                    "arguments": {"candidate": candidate},
                },
            ),
            (
                "tool/result",
                {
                    "callId": "decision-freeze",
                    "isError": True,
                    "error": "decision_hub_final_review_required",
                },
            ),
            ("tool/call", {"callId": "review", "name": "subagent_reviewer", "arguments": {}}),
            ("tool/result", {"callId": "review", "result": reviewer}),
            (
                "tool/call",
                {
                    "callId": "decision-final",
                    "name": "decision_hub_synthesis_submit",
                    "arguments": {"candidate": candidate},
                },
            ),
            (
                "tool/result",
                {
                    "callId": "decision-final",
                    "result": {
                        "schema_version": "decision-hub-synthesis-tool-result.v2",
                        "candidate": candidate,
                        "review_gate": gate,
                    },
                },
            ),
        ):
            events.append(event(kind, data))
        for row in events:
            row["payload"]["event"].pop("timestamp", None)
        encoded = observation_json(events)
        response = response.model_copy(
            update={
                "final_response": "",
                "events_json": encoded,
                "result_hash": _sha("\0" + encoded),
            }
        )
        return _authenticated_host_response(self.fixture, payload, response)


@pytest.mark.asyncio
@pytest.mark.skip(
    reason=(
        "Controlled installation cannot satisfy the physical Windows commit source lease; "
        "see w2-native-integration-01/REPORT.md. Pending supported installed-candidate vertical."
    )
)
async def test_controlled_installation_fixture_vertical(
    tmp_path, monkeypatch, installation_fixture
):
    configure(installation_fixture)
    _, installation_receipt, bodies = installation_fixture
    original_read = Path.read_bytes
    pointer = (ROOT / INSTALLED_POINTER_REF).resolve()
    monkeypatch.setattr(
        Path,
        "read_bytes",
        lambda path: (
            bodies[INSTALLED_POINTER_REF] if path.resolve() == pointer else original_read(path)
        ),
    )
    data_root = tmp_path / "data"
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(data_root))
    pack_root = ROOT / "packs/crypto_macro_prepared/1.2.0"
    definition = yaml.safe_load((pack_root / "pack.yaml").read_text("utf-8"))
    old = yaml.safe_load((ROOT / "packs/crypto_macro_prepared/1.1.0/pack.yaml").read_text("utf-8"))
    monkeypatch.setenv(
        "DECISION_HUB_RESEARCH_CAPABILITIES", ",".join(definition["capability_refs"])
    )
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'vertical.sqlite3'}")
    database.create_all()
    now = [datetime.now(UTC).replace(microsecond=0)]
    timings = {"started_at": datetime.now(UTC).isoformat()}

    def readiness(_request):
        return httpx.Response(
            200,
            json={
                "schema_version": "dsh-host-readiness.v1",
                "ready": True,
                "version_compatible": True,
                "upstream_identity": {
                    "plugin_build_hash": installation_receipt["plugin_build_hash"]
                },
                "checked_at": now[0].isoformat(),
            },
        )

    try:
        installed = InstalledSpotResearch(
            database,
            data_root,
            enabled=True,
            clock=lambda: now[0],
            transport=httpx.MockTransport(readiness),
        )
        market = MarketRiskDataService(
            database,
            RawBodyArchive(data_root / "raw-captures"),
            data_root / "market-risk/features",
            adapter=OKXMarketPathAdapter(fetcher=PublicFixture(now).fetch),
            scopes=(spot_scope("BTC-USDT"),),
            enabled=True,
            clock=lambda: now[0],
        )
        assert await market.refresh_due() == 1
        await installed.prepare("BTC-USDT")
        event_id, _, _ = AdmissionService(database, clock=lambda: now[0]).admit(
            ObservationCreate(
                text="Current BTC spot price and volume snapshot with daily trend context.",
                source_id="controlled-native-vertical",
                source_type="transcript",
                language="en",
                observed_at=now[0] - timedelta(seconds=2),
                event_hint="crypto_market_snapshot",
            )
        )
        with database.session() as session:
            study_id = ensure_study(
                session,
                question_key="controlled-native-vertical",
                title="BTC",
                market="crypto",
                instrument="BTC-USDT",
                horizon_scope="30m,24h,72h",
                now=now[0],
            )
        run_id, _ = RunService(
            database, clock=lambda: now[0], spot_admission_resolver=installed.resolve_in_session
        ).create(event_id, strategy_version="research.v1", study_id=study_id)
        request = ResearchRequestFactory(
            database,
            pack_root=ROOT / "packs/crypto_macro_prepared/1.1.0",
            spot_prepared_pack_root=pack_root,
            prepared_policy_refs=POLICY_REFS,
            allowed_capabilities=tuple(old["capability_refs"]),
            spot_allowed_capabilities=tuple(definition["capability_refs"]),
            execution_mode="replay",
            decision_output_enabled=True,
            clock=lambda: now[0],
        ).build(event_id, run_id)
        assert request.output_schema_ref == DECISION_OUTPUT_SCHEMA_REF
        assert request.context_manifest.created_at == request.pit_cutoff_at
        digest = research_request_hash(request)
        session_id, rpc_id = DshSessionLinkService.deterministic_ids(run_id, digest)
        submit = DshSessionSubmit(
            schema_version="dsh-session-submit.v1",
            run_id=run_id,
            request_hash=digest,
            deterministic_session_id=session_id,
            deterministic_request_id=rpc_id,
            workspace_ref="decision-hub://workspace/default",
            prompt_ref=f"hub://runs/{run_id}/prompts/1",
            agent_preset="decision-research",
            permission_ref="decision-hub://permissions/research-only",
            deadline_at=request.deadline_at,
            max_tool_calls=request.execution_budget.max_tool_calls,
            model_step_timeout_ms=request.execution_budget.per_model_step_timeout_seconds * 1000,
            generation=1,
        )
        source = PreparedInputs(
            database,
            request,
            submit,
            DshUpstreamIdentity(
                source_commit="1" * 40,
                source_version="0.1.0",
                package_versions={},
                plugin_build_hash=installation_receipt["plugin_build_hash"],
            ),
            "Host fixture",
            now[0],
        )
        active = _active(source)
        host = DecisionHost(source, top_ref=SPOT_A)
        runtime = DshWebResearchRuntime(
            DshSessionLinkService(database), host, poll_interval_seconds=0.001
        )
        timings["factory_completed_at"] = datetime.now(UTC).isoformat()
        try:
            graph = build_agentic_research_graph(
                runtime, ResearchEvidenceService(database), expected_lease_owner=OWNER
            )
            state = await graph.ainvoke(initial_research_state(active))
        finally:
            await runtime.close()
        assert len(host.submit_calls) == host.result_calls == 1
        reference = parse_graph_final_ref(state["graph_final_projection_ref"])
        result = ResearchSessionResult.model_validate(state["final_result"])
        _renew_draft_lease(source)
        review = ResearchReviewService(database).review(
            run_id, result, graph_final_ref=reference, expected_lease_owner=OWNER
        )
        artifact_id, _ = CommitDecisionService(database).commit_research_result(
            run_id,
            event_id,
            result.model_copy(update={"reviews": [review]}),
            graph_final_ref=reference,
            expected_lease_owner=OWNER,
        )
        timings["report_committed_at"] = datetime.now(UTC).isoformat()
        with database.session() as session:
            session.connection().exec_driver_sql("BEGIN")
            trusted = read_trusted_research_in_session(
                session,
                run_id=run_id,
                event_id=event_id,
                artifact_id=artifact_id,
                reference=reference,
            )
            assert trusted.native_action is not None
            assert trusted.native_action.review_effect_ref is not None
            opportunity = session.scalar(
                select(DecisionOpportunityRecord).where(
                    DecisionOpportunityRecord.event_ref == event_id,
                    DecisionOpportunityRecord.horizon == "24h",
                )
            )
            assert opportunity is not None and opportunity.cohort == "diagnostic"
            opportunity_id = opportunity.opportunity_id
        publisher = ActionPublicationService(database)
        receipt = publisher.publish(
            run_id=run_id,
            event_id=event_id,
            artifact_id=artifact_id,
            reference=reference,
            opportunity_id=opportunity_id,
        )
        timings["action_published_at"] = datetime.now(UTC).isoformat()
        with database.session() as session:
            counts = {
                model.__tablename__: len(list(session.scalars(select(model))))
                for model in (
                    ActionPlanHeadRecord,
                    ActionPlanRevisionRecord,
                    DecisionSnapshotRecord,
                    OutboxRecord,
                )
            }
            sidecars = list(
                session.scalars(
                    select(RunRoleOutputRecord).where(
                        RunRoleOutputRecord.run_id == run_id,
                        RunRoleOutputRecord.role == "research_action_synthesis_24h",
                    )
                )
            )
        assert counts[ActionPlanHeadRecord.__tablename__] == 3
        assert counts[ActionPlanRevisionRecord.__tablename__] == 3
        assert counts[DecisionSnapshotRecord.__tablename__] == 1 and len(sidecars) == 1
        # Reuse the completed original and receipt. No second Graph/model loop.
        repeated = publisher.publish(
            run_id=run_id,
            event_id=event_id,
            artifact_id=artifact_id,
            reference=reference,
            opportunity_id=opportunity_id,
        )
        assert repeated == receipt and host.result_calls == 1
        (tmp_path / "vertical-receipt.json").write_text(
            json.dumps(
                {
                    "fixture": "controlled_installation_fixture_vertical",
                    "production_pass": False,
                    "provider_executed": False,
                    "run_id": run_id,
                    "event_id": event_id,
                    "artifact_id": artifact_id,
                    "opportunity_id": opportunity_id,
                    "reference": reference.model_dump(mode="json"),
                    "counts": counts,
                    "times": timings,
                    "native_action": {
                        "raw_candidate_hash": trusted.native_action.raw_candidate_hash,
                        "review_effect_ref": trusted.native_action.review_effect_ref.model_dump(
                            mode="json"
                        ),
                    },
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    finally:
        database.engine.dispose()

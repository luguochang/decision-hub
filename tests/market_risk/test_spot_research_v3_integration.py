"""Actual Factory/Selection/Runtime/Graph/Commit on isolated SQLite with controlled Host.

Installation bytes are the explicit test subset fixture, never a real installed pointer.
Market bars are synthetic captures; Host transcript is offline. This is engineering proof.
"""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml
from sqlalchemy import select

from apps.hub_worker.research import ResearchRequestFactory
from packages.contracts_py.decision_hub_contracts import (
    DshSessionSubmit,
    DshUpstreamIdentity,
    ObservationCreate,
    ResearchSessionResult,
)
from packages.contracts_py.decision_hub_contracts.models import SourceType
from packages.kernel.decision_hub_kernel.application.admission import AdmissionService
from packages.kernel.decision_hub_kernel.application.commit import CommitDecisionService
from packages.kernel.decision_hub_kernel.application.dsh_sessions import DshSessionLinkService
from packages.kernel.decision_hub_kernel.application.research_binding import (
    PreparedResearchPolicyRefs,
)
from packages.kernel.decision_hub_kernel.application.research_context_selection_v4 import (
    ContextSelectionService,
)
from packages.kernel.decision_hub_kernel.application.research_graph_binding import (
    parse_graph_final_ref,
)
from packages.kernel.decision_hub_kernel.application.research_graph_dispatch import (
    read_graph_recipe_selection_for_run_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_request_identity import (
    research_request_hash,
)
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.application.spot_scope_admission import HashedResource
from packages.kernel.decision_hub_kernel.application.study import ensure_study
from packages.kernel.decision_hub_kernel.persistence.db import RunRecord, RunRoleOutputRecord
from packages.orchestration.langgraph.executor import LangGraphResearchExecutor
from packages.runtime_adapters.dsh_runtime.web_runtime import DshWebResearchRuntime
from tests.dsh_native.test_execution_preparation_v2 import PreparedInputs
from tests.dsh_native.test_runtime_execution_v2 import PreparedHost
from tests.market_risk.test_recipe_selection_v3 import configure, installation_fixture, manifest
from tests.market_risk.test_spot_scope_admission import prepared

assert installation_fixture is not None and manifest is not None and prepared is not None
ROOT = Path(__file__).resolve().parents[2]
OWNER = "spot-v3-controlled-worker"


@pytest.fixture
def spot_component_consumer():
    return None


@pytest.fixture
def spot_context_provider():
    return None


@pytest.fixture
def spot_event_text():
    return "ETH market risk research needs public macro, contrary evidence, path risk and unknowns."


@pytest.fixture
def spot_inputs(
    prepared, installation_fixture, spot_component_consumer, spot_context_provider, spot_event_text
):
    configure(installation_fixture)
    db, now, resources, _, runtime = prepared
    now[0] = datetime.now(UTC)
    instrument = "ETH-USDT"
    admission, old_effective, host = runtime(instrument, capture=True)
    relative = "packs/crypto_macro_prepared/1.2.0/pack.yaml"
    resources[relative] = (ROOT / relative).read_bytes()
    effective = old_effective.model_copy(
        update={
            "pack": HashedResource(
                ref=relative, sha256=hashlib.sha256(resources[relative]).hexdigest()
            )
        }
    )
    admission.effective_verifier = lambda candidate: candidate == effective
    admission.route_verifier = lambda route, candidate: (
        route in effective.routes and candidate == effective
    )
    admission.approve_trial(request_id="new-v3", effective=effective, host=host)
    event_id, _, _ = AdmissionService(db, clock=lambda: now[0]).admit(
        ObservationCreate(
            text=spot_event_text,
            source_id="risk-monitor",
            source_type=SourceType.transcript,
            observed_at=now[0] - timedelta(seconds=2),
            language="en",
            event_hint="central_bank_speech",
        )
    )
    with db.session() as session:
        study_id = ensure_study(
            session,
            question_key="spot-risk-24h",
            title="ETH24h",
            market="crypto",
            instrument=instrument,
            horizon_scope="30m,24h,72h",
            now=now[0],
        )
    runs = RunService(
        db,
        clock=lambda: now[0],
        spot_admission_resolver=lambda session, study, at: admission.resolve_current_in_session(
            session, effective=effective, host=host, assessed_at=at
        ),
    )
    run_id, _ = runs.create(event_id, strategy_version="research.v1", study_id=study_id)
    pack_root = ROOT / "packs/crypto_macro_prepared/1.2.0"
    definition = yaml.safe_load((pack_root / "pack.yaml").read_text(encoding="utf-8"))
    refs = PreparedResearchPolicyRefs(
        gate_policy_ref=definition["gate_policy_ref"],
        rubric_policy_ref=definition["evaluation_policy_ref"],
        role_profile_refs=tuple(definition["role_profile_refs"]),
    )
    request = ResearchRequestFactory(
        db,
        pack_root=ROOT / "packs/crypto_macro_prepared/1.1.0",
        spot_prepared_pack_root=pack_root,
        prepared_policy_refs=refs,
        allowed_capabilities=tuple(
            yaml.safe_load(
                (ROOT / "packs/crypto_macro_prepared/1.1.0/pack.yaml").read_text(encoding="utf-8")
            )["capability_refs"]
        ),
        spot_allowed_capabilities=tuple(definition["capability_refs"]),
        component_consumer=spot_component_consumer,
        context_inputs_provider=spot_context_provider,
        clock=lambda: now[0],
    ).build(event_id, run_id)
    digest = research_request_hash(request)
    session_id, request_id = DshSessionLinkService.deterministic_ids(run_id, digest)
    submit = DshSessionSubmit(
        schema_version="dsh-session-submit.v1",
        run_id=run_id,
        request_hash=digest,
        deterministic_session_id=session_id,
        deterministic_request_id=request_id,
        workspace_ref="decision-hub://workspace/default",
        prompt_ref=f"hub://runs/{run_id}/prompts/1",
        agent_preset="decision-research",
        permission_ref="decision-hub://permissions/research-only",
        deadline_at=request.deadline_at,
        max_tool_calls=request.execution_budget.max_tool_calls,
        model_step_timeout_ms=request.execution_budget.per_model_step_timeout_seconds * 1000,
        generation=1,
    )
    upstream = DshUpstreamIdentity(
        source_commit="1" * 40,
        source_version="0.1.0",
        package_versions={},
        plugin_build_hash=effective.plugin_build_hash,
    )
    with db.session() as session:
        row = session.get(RunRecord, run_id)
        assert row is not None
        row.status = "running"
        row.lease_owner = OWNER
        row.lease_expires_at = datetime.now(UTC) + timedelta(minutes=15)
    return PreparedInputs(
        db, request, submit, upstream, "Runtime renders actual prompt", now[0]
    ), event_id


def test_factory_exact_eth_pack_v3_and_protected_selector_originals(spot_inputs):
    inputs, _ = spot_inputs
    with inputs.database.session() as session:
        selected = read_graph_recipe_selection_for_run_in_session(session, inputs.request.run_id)
        assert (
            selected is not None
            and selected.receipt.recipe_version == "research-graph-projection.v3"
        )
        assert selected.receipt.quality_context.scope_id.endswith("ETH-USDT:trade:1m")
        protected = set(selected.receipt.quality_context.additional_required_context_refs)
        assert protected <= set(inputs.request.evidence_refs)
        assert not selected.receipt.quality_context.path_risk_model_qualified
    original = ContextSelectionService(inputs.database).select(
        run_id=inputs.request.run_id, graph_round=1, expected_lease_owner=OWNER
    )
    assert original.reference.schema_version == "research-context-selection.v4"
    assert protected <= set(original.request.evidence_refs)
    assert original.receipt.selector_identity.ranking_rules_ref.endswith("protected.v3")
    from packages.kernel.decision_hub_kernel.application.research_binding import (
        read_research_bundle_in_session,
    )
    from packages.kernel.decision_hub_kernel.application.research_execution_binding import (
        _assert_actual_scope,
    )

    with inputs.database.session() as session:
        bundle = read_research_bundle_in_session(session, inputs.request.run_id)
        try:
            _assert_actual_scope(session, original.request, bundle, recorded_at=datetime.now(UTC))
        except ValueError:
            original_body = inputs.request.model_dump(mode="json")
            projected = original.request.model_dump(mode="json")
            differences = {
                key: {"original": original_body[key], "selected": projected[key]}
                for key in original_body
                if original_body[key] != projected[key]
            }
            raise AssertionError("Actual scope conflict fields: " + ",".join(differences)) from None
    from packages.kernel.decision_hub_kernel.application.research_context_execution import (
        build_method_v3_in_session,
    )

    with inputs.database.session() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        with pytest.raises(ValueError, match="spot_execution_installed_identity_changed"):
            build_method_v3_in_session(
                session,
                original.request,
                inputs.submit,
                inputs.upstream.model_copy(update={"plugin_build_hash": "2" * 64}),
                selection=original,
                runtime_id="dsh-web",
                runtime_version="wrong-build",
                profile_ref=inputs.submit.agent_preset,
                prompt_policy_hash="b" * 64,
                recorded_at=datetime.now(UTC),
            )
        assert (
            session.scalar(
                select(RunRoleOutputRecord.output_id).where(
                    RunRoleOutputRecord.run_id == inputs.request.run_id,
                    RunRoleOutputRecord.role == "research_execution_method",
                )
            )
            is None
        )


@pytest.mark.asyncio
async def test_actual_spot_graph_runtime_link_quality_commit_and_tamper_read(
    spot_inputs, monkeypatch
):
    inputs, event_id = spot_inputs
    from packages.orchestration.langgraph.graphs import agentic_research_graph as outer

    actual_prepare = outer.prepare_graph_state_in_session

    def diagnose_prepare(session, state, owner):
        try:
            return actual_prepare(session, state, owner)
        except ValueError as exc:
            if str(exc) != "research_graph_checkpoint_projection_changed":
                raise
            from packages.kernel.decision_hub_kernel.application.research_graph_binding import (
                parse_graph_round_ref,
                read_graph_round_in_session,
            )

            frozen = read_graph_round_in_session(
                session,
                run_id=inputs.request.run_id,
                reference=parse_graph_round_ref(state["graph_round_admission_ref"]),
                expected_lease_owner=owner,
                require_current_permission=True,
            )
            expected = {
                **frozen.state,
                "current_round": state["current_round"],
                "graph_round_admission_ref": frozen.reference.model_dump(mode="json"),
            }
            fields = sorted(
                key for key in set(expected) | set(state) if expected.get(key) != state.get(key)
            )
            raise AssertionError("Checkpoint conflict fields: " + ",".join(fields)) from None

    monkeypatch.setattr(outer, "prepare_graph_state_in_session", diagnose_prepare)
    host = PreparedHost(inputs)
    runtime = DshWebResearchRuntime(
        DshSessionLinkService(inputs.database), host, poll_interval_seconds=0.001
    )
    try:
        state = await LangGraphResearchExecutor(
            inputs.database, runtime, expected_lease_owner=OWNER
        ).execute(inputs.request, require_context_selection=True)
    finally:
        await runtime.close()
        runtime.cancellation_projector._executor.shutdown(wait=True, cancel_futures=True)
    assert len(host.submit_calls) == 1 and host.result_calls == 1
    final = ResearchSessionResult.model_validate(state["final_result"])
    reference = parse_graph_final_ref(state["graph_final_projection_ref"])
    assert reference.row.schema_version == "research-graph-final-projection.v3"
    from packages.kernel.decision_hub_kernel.application.research_review import (
        ResearchReviewService,
    )

    review = ResearchReviewService(inputs.database).review(
        inputs.request.run_id,
        final,
        graph_final_ref=reference,
        expected_lease_owner=OWNER,
    )
    final = final.model_copy(update={"reviews": [review]})
    commit = CommitDecisionService(inputs.database)
    artifact_id, gate = commit.commit_research_result(
        inputs.request.run_id,
        event_id,
        final,
        graph_final_ref=reference,
        expected_lease_owner=OWNER,
    )
    repeated = commit.commit_research_result(
        inputs.request.run_id,
        event_id,
        final,
        graph_final_ref=reference,
        expected_lease_owner=OWNER,
    )
    assert repeated[0] == artifact_id and repeated[1].status == gate.status
    from packages.kernel.decision_hub_kernel.application.research_quality_view import (
        ResearchQualityQuery,
    )

    view = ResearchQualityQuery(inputs.database).read(inputs.request.run_id)
    assert view.status == "available" and view.decision is not None
    assert view.decision.path_risk_status != "eligible"
    assert view.budget is not None and view.budget.actual_total_tokens is None
    assert view.decision.full_report_json
    receipt_directory = ROOT / "artifacts/market-risk-implementation-20261007/B2"
    receipt_directory.mkdir(parents=True, exist_ok=True)
    (receipt_directory / "quality-view-controlled01.json").write_text(
        view.model_dump_json(indent=2), encoding="utf-8"
    )
    with inputs.database.session() as session:
        rows = {
            row.role: row
            for row in session.scalars(
                select(RunRoleOutputRecord).where(
                    RunRoleOutputRecord.run_id == inputs.request.run_id
                )
            ).all()
        }
        assert rows["context_select.r1.a1"].schema_version == "research-context-selection.v4"
        assert (
            rows["context_execution_link:1"].schema_version
            == "research-context-selection-execution-link.v3"
        )
        assert (
            rows["research_publication_source"].schema_version == "research-publication-source.v4"
        )
        body = json.loads(rows["research_publication_source"].payload_json)
        assert body["quality_binding"]["admission_proof_hash"]
        assert body["quality_binding"]["path_manifest_hash"]
        body["quality_binding"]["quality_decision_hash"] = "f" * 64
        damaged = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        row = rows["research_publication_source"]
        row.payload_json = damaged
        row.payload_hash = hashlib.sha256(damaged.encode()).hexdigest()
        row.output_id = f"publication_{row.payload_hash[:40]}"
    with pytest.raises(ValueError, match="research_quality_publication_binding_changed"):
        commit.commit_research_result(
            inputs.request.run_id,
            event_id,
            final,
            graph_final_ref=reference,
            expected_lease_owner=OWNER,
        )
    with pytest.raises(ValueError, match="research_quality_publication_binding_changed"):
        ResearchQualityQuery(inputs.database).read(inputs.request.run_id)

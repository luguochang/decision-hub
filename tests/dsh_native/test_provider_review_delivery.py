"""Actual private receipt chains; synthetic Host/native bodies, no paid model."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from apps.hub_worker.research import ResearchRequestFactory
from packages.contracts_py.decision_hub_contracts import (
    DshSessionSubmit,
    DshUpstreamIdentity,
    ResearchSynthesisCandidate,
)
from packages.kernel.decision_hub_kernel.application.dsh_sessions import DshSessionLinkService
from packages.kernel.decision_hub_kernel.application.provider_input_receipts import (
    ProviderInputReceiptService,
    sha,
    strict_json,
)
from packages.kernel.decision_hub_kernel.application.research_binding import (
    PreparedResearchPolicyRefs,
    read_research_bundle_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_context_delivery import (
    _initial_prompt_sections_present,
    read_context_delivery_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_policy_render_v2 import (
    render_prepared_research_policy,
)
from packages.kernel.decision_hub_kernel.application.research_publication_binding import (
    observation_json,
)
from packages.kernel.decision_hub_kernel.application.research_request_identity import (
    research_request_hash,
)
from packages.kernel.decision_hub_kernel.application.research_review import (
    independent_review_receipt_hash,
)
from packages.kernel.decision_hub_kernel.application.research_review_effects import (
    record_review_effects_in_session,
)
from packages.kernel.decision_hub_kernel.persistence.db import RunRoleOutputRecord, utcnow
from packages.runtime_adapters.dsh_runtime.profile import build_trusted_web_research_prompt
from tests.dsh_native.test_execution_preparation_v2 import PreparedInputs
from tests.dsh_native.test_independent_review_effects import _mapping
from tests.dsh_native.test_provider_input_receipts import (
    _mapped,
    emission,
    finish,
)
from tests.dsh_native.test_provider_input_receipts import (
    active as active,
)
from tests.evolution.test_research_worker import _admitted_run
from tests.orchestration.test_agentic_research_graph import (
    _causal_case,
    _horizons,
    _request,
)
from tests.research.test_full_method_v2_bundle import (
    _prepared_pack,
)
from tests.research.test_full_method_v2_bundle import (
    forbid_external_connections as forbid_external_connections,
)

pytestmark = pytest.mark.usefixtures("forbid_external_connections")


@pytest.fixture
def inputs(tmp_path, request):
    """Actual Factory/first preparation with the normal prompt body codec."""
    database, event_id, run_id = _admitted_run(tmp_path)
    try:
        root, manifest = _prepared_pack(tmp_path)
        factory = ResearchRequestFactory(
            database,
            pack_root=root,
            execution_mode=getattr(request, "param", "replay"),
            allowed_capabilities=("replay.research", "document.readback", "web.search"),
            prepared_policy_refs=PreparedResearchPolicyRefs(
                gate_policy_ref=manifest["gate_policy_ref"],
                rubric_policy_ref=manifest["evaluation_policy_ref"],
                role_profile_refs=tuple(manifest["role_profile_refs"]),
            ),
        )
        task = factory.build(event_id, run_id).model_copy(deep=True)
        task.evidence_requirements = task.evidence_requirements[:1]
        digest = research_request_hash(task)
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
            deadline_at=task.deadline_at,
            max_tool_calls=task.execution_budget.max_tool_calls,
            model_step_timeout_ms=task.execution_budget.per_model_step_timeout_seconds * 1000,
            generation=1,
        )
        upstream = DshUpstreamIdentity(
            source_commit="1" * 40,
            source_version="0.1.0",
            package_versions={},
            plugin_build_hash="2" * 64,
        )
        with database.session() as session:
            bundle = read_research_bundle_in_session(session, run_id)
        prompt = (
            render_prepared_research_policy(bundle, task.allowed_capabilities)
            + "\n\n"
            + build_trusted_web_research_prompt(task)
        )
        yield PreparedInputs(database, task, submit, upstream, prompt, datetime.now(UTC))
    finally:
        database.engine.dispose()


@pytest.mark.parametrize("mode", ["full", "ids-only", "child-only"])
def test_exact_root_review_wrapper_delivery_or_missing(active, mode):
    inputs, base = active
    service = ProviderInputReceiptService(inputs.database)

    def before_store(notifications, candidate):
        wrapper = notifications[4].payload["event"]["data"]["result"]
        receipt = strict_json(wrapper["original_output"]["output"][0]["text"])
        public_id = (
            "review_"
            + independent_review_receipt_hash(
                **{
                    key: receipt[key]
                    for key in (
                        "role",
                        "target_claim",
                        "evidence_refs",
                        "challenge",
                        "resolution",
                        "limitations",
                    )
                }
            )[:32]
        )
        body = strict_json(base.input_json)
        retained = (
            wrapper
            if mode == "full"
            else {"public_review_ids": [public_id]}
            if mode == "ids-only"
            else wrapper["original_output"]
        )
        body["messages"].append(
            {
                "source": {"kind": "tool", "callId": "review"},
                "content": [{"type": "text", "text": observation_json(retained)}],
            }
        )
        encoded = observation_json(body)
        payload = base.model_copy(update={"input_json": encoded, "input_hash": sha(encoded)})
        recorded = service.record_input(payload)
        service.record_emission(
            emission(
                payload,
                recorded,
                arguments={"candidate": candidate.model_dump(mode="json")},
                call_id="final",
            )
        )
        service.record_finish(finish(payload, recorded))

    mapped, result, _, _ = _mapping(inputs, before_store=before_store)
    assert result.reviews
    review_ref = "review:" + result.reviews[0].review_id
    # Full input bytes alone cannot mint a missing authenticated review effect.
    with inputs.database.session() as session:
        session.connection().exec_driver_sql("BEGIN")
        absent = read_context_delivery_in_session(session, mapped_ref=mapped)
        assert absent.root_synthesis_input_verified and not absent.context_fidelity_verified
        item = next(item for item in absent.items if item.evidence_ref == review_ref)
        assert not item.covered and item.original_ref is None

    with inputs.database.session() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        effects = record_review_effects_in_session(
            session,
            mapped_ref=mapped,
            source_result=result,
            expected_lease_owner="provider-original-controls",
            known_at=utcnow() + timedelta(seconds=2),
        )
        assert effects and effects.review_effect_ref
        effect_ref = effects.review_effect_ref
    with inputs.database.session() as session:
        connection = session.connection()
        connection.exec_driver_sql("BEGIN")
        native = connection.connection.driver_connection
        counter = native.total_changes
        native.set_authorizer(lambda action, *_: 0 if action in {20, 21, 31} else 1)
        try:
            view = read_context_delivery_in_session(
                session, mapped_ref=mapped, additional_required_refs=(review_ref,)
            )
            item = next(item for item in view.items if item.evidence_ref == review_ref)
            assert view.root_synthesis_input_verified
            assert item.original_ref == effect_ref and item.source_kind == "root_child_summary"
            assert item.covered is (mode == "full")
            assert view.context_fidelity_verified is (mode == "full")
            assert bool(item.input_loci) is (mode == "full")
            assert not view.model_understanding_verified and not view.financial_qualified
            assert session.connection() is connection and native.in_transaction
            assert native.total_changes == counter
        finally:
            native.set_authorizer(None)
    if mode == "full":
        # A saved effect cannot be accepted after its actual immutable bytes
        # change, even when public IDs still appear in the root model input.
        with inputs.database.session() as session:
            row = session.get(RunRoleOutputRecord, effect_ref.output_id)
            original = json.loads(row.payload_json)
            original["reviewer_prompt_hash"] = "f" * 64
            row.payload_json = observation_json(original)
            row.payload_hash = sha(row.payload_json)
        with inputs.database.session() as session:
            session.connection().exec_driver_sql("BEGIN")
            with pytest.raises(ValueError, match="source_changed"):
                read_context_delivery_in_session(session, mapped_ref=mapped)


def test_actual_initial_prompt_codec_rejects_missing_or_changed_bodies():
    task = _request()
    prompt = build_trusted_web_research_prompt(task)
    assert _initial_prompt_sections_present(task, prompt)
    assert not _initial_prompt_sections_present(task, "Controlled body; not an executed Host.")
    marker = "BEGIN_UNTRUSTED_RESEARCH_DATA\n"
    control_only = prompt.split(marker)[0]
    assert not _initial_prompt_sections_present(task, control_only)
    changed = task.model_copy(deep=True)
    changed.input_evidence[0].excerpt += " altered"
    assert not _initial_prompt_sections_present(changed, prompt)
    changed.input_evidence[0].excerpt = task.input_evidence[0].excerpt
    changed.input_evidence[0].source_id += "-different-source"
    assert not _initial_prompt_sections_present(changed, prompt)


@pytest.mark.parametrize("inputs", ["live"], indirect=True)
def test_live_selected_root_input_without_v2_review_cannot_attest_fidelity(active):
    prepared, payload = active
    assert prepared.request.execution_mode == "live"
    candidate = ResearchSynthesisCandidate.model_validate(
        {
            "schema_version": "research-synthesis-candidate.v1",
            "request_id": prepared.request.request_id,
            "causal_case": _causal_case(
                thesis="Controlled original.",
                unresolved_questions=[],
                evidence_id=prepared.request.input_evidence[0].evidence_id,
            ),
            "horizons": _horizons(prepared.request.input_evidence[0].evidence_id),
        }
    )
    arguments = {"candidate": candidate.model_dump(mode="json")}
    service = ProviderInputReceiptService(prepared.database)
    original = service.record_input(payload)
    service.record_emission(
        emission(payload, original, arguments=arguments, call_id="root-synthesis")
    )
    service.record_finish(finish(payload, original))
    mapped = _mapped(prepared, candidate, arguments)
    with prepared.database.session() as session:
        session.connection().exec_driver_sql("BEGIN")
        view = read_context_delivery_in_session(session, mapped_ref=mapped)
        assert view.root_synthesis_input_verified and all(item.covered for item in view.items)
        assert not view.context_fidelity_verified
        assert "provider_authenticated_root_review_original_missing" in view.missing_codes
        assert not view.model_understanding_verified and not view.financial_qualified

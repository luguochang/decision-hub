"""V2 mapped carriers retain exact originals; they grant no publication permission."""

import hashlib
import importlib
import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from packages.contracts_py.decision_hub_contracts import (
    DshSessionResult,
    DshSessionSubmit,
    DshUpstreamIdentity,
)
from packages.kernel.decision_hub_kernel.application.dsh_sessions import DshSessionLinkService
from packages.kernel.decision_hub_kernel.application.research_binding import (
    read_research_bundle_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_execution_binding import (
    ResearchExecutionPreparationService,
)
from packages.kernel.decision_hub_kernel.application.research_policy_render_v2 import (
    render_prepared_research_policy,
)
from packages.kernel.decision_hub_kernel.application.research_request_identity import (
    research_request_hash,
)
from packages.kernel.decision_hub_kernel.application.research_review import (
    research_result_content_hash,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    RunRecord,
    RunRoleOutputRecord,
    utcnow,
)
from packages.kernel.decision_hub_kernel.persistence.original_preparation import begin_immediate
from packages.kernel.decision_hub_kernel.ports.research_observation import (
    ResearchTransportObservation,
)
from packages.runtime_adapters.dsh_runtime.event_mapper import map_notifications
from packages.runtime_adapters.dsh_runtime.result_mapper import map_session_result_with_witness
from packages.runtime_adapters.dsh_runtime.web_runtime import _sdk_run
from tests.contracts.test_d2_candidate_contract import body_ref, review_request
from tests.research.test_bundle_snapshot_read_scope import bundle_case as bundle_case

BINDING = "packages.kernel.decision_hub_kernel.application.research_publication_binding"


@pytest.fixture
def native_case(bundle_case):
    db = bundle_case.database
    with db.session() as session:
        bundle = read_research_bundle_in_session(session, bundle_case.run_id)
        request = bundle.request
        policy = render_prepared_research_policy(bundle, request.allowed_capabilities)
    now = utcnow()
    digest = research_request_hash(request)
    session_id, request_id = DshSessionLinkService.deterministic_ids(request.run_id, digest)
    upstream = DshUpstreamIdentity(
        source_commit="1" * 40,
        source_version="test-carrier",
        package_versions={},
        plugin_build_hash="2" * 64,
    )
    runtime_version = "dsh-web-test-carrier+" + "2" * 12
    submit = DshSessionSubmit(
        schema_version="dsh-session-submit.v1",
        run_id=request.run_id,
        request_hash=digest,
        deterministic_session_id=session_id,
        deterministic_request_id=request_id,
        workspace_ref="decision-hub://workspace/default",
        prompt_ref=f"hub://runs/{request.run_id}/prompts/1",
        agent_preset="decision-research",
        permission_ref="decision-hub://permissions/research-only",
        deadline_at=request.deadline_at,
        model_step_timeout_ms=request.execution_budget.per_model_step_timeout_seconds * 1000,
        max_tool_calls=request.execution_budget.max_tool_calls,
        generation=1,
    )
    prepared = ResearchExecutionPreparationService(db, clock=lambda: now).prepare(
        request,
        submit,
        upstream,
        policy + "\n\nCarrier test: no Provider execution.",
        runtime_id="dsh-web",
        runtime_version=runtime_version,
        profile_ref="decision-research.web",
        prompt_policy_hash="3" * 64,
    )
    with db.session() as session:
        run = session.get(RunRecord, request.run_id)
        run.lease_owner = "carrier-test"
    module = importlib.import_module(BINDING)
    final = json.dumps(
        dict(
            schema_version="research-synthesis-candidate.v1",
            request_id=request.request_id,
            causal_case=None,
            horizons=[],
        )
    )
    events = "[]"
    payload = DshSessionResult(
        schema_version="dsh-session-result.v1",
        run_id=request.run_id,
        dsh_session_id=session_id,
        generation=1,
        last_seq=1,
        final_response=final,
        events_json=events,
        finish_reason="completed",
        started_at=now,
        finished_at=now + timedelta(seconds=1),
        trace_ref="fixture:carrier",
        result_hash=hashlib.sha256((final + "\0" + events).encode()).hexdigest(),
    )
    with db.session() as session:
        context = module.native_observation_context_in_session(session, request.run_id)
    raw = ResearchTransportObservation(
        schema_version="research-transport-observation.v1",
        context=context,
        phase="result",
        reply=payload,
        error_code=None,
        events_json_hash=hashlib.sha256(events.encode()).hexdigest(),
        final_response_hash=hashlib.sha256(final.encode()).hexdigest(),
        recorded_at=now + timedelta(seconds=2),
    )
    with db.session() as session:
        begin_immediate(session)
        reference = module.store_transport_observation(session, raw)
    sdk = _sdk_run(payload)
    traces = map_notifications(
        sdk.notifications,
        run_id=request.run_id,
        research_session_id=session_id,
        received_at=payload.finished_at,
        request=request,
    )
    summary = map_session_result_with_witness(
        sdk,
        request,
        traces,
        runtime_version=runtime_version,
        profile_ref=prepared.method.definition.profile_ref,
        started_at=payload.started_at,
        finished_at=payload.finished_at,
    ).result
    return SimpleNamespace(
        database=db,
        request=request,
        prepared=prepared,
        context=context,
        raw=raw,
        reference=reference,
        summary=summary,
        now=now,
    )


def carrier(case):
    port = importlib.import_module("packages.kernel.decision_hub_kernel.ports.research_observation")
    packet = review_request()
    packet["synthesis"]["request_id"] = case.request.request_id
    packet["synthesis_ref"] = body_ref("synth", packet["synthesis"])
    selection = dict(
        kind="tool_capture",
        final_response_hash=None,
        call_id="candidate-call",
        call_event_index=0,
        result_event_index=1,
        payload_occurrence=0,
        payload_path=("payload", "event", "data", "value", "candidate"),
        call_event_hash="a" * 64,
        result_event_hash="b" * 64,
        call_seq=1,
        result_seq=2,
        origin_generation=None,
        presence_generation=None,
        presence_ref=None,
    )
    nested = port.ResearchPerpetualSynthesisObservation.model_validate(
        dict(
            synthesis=packet["synthesis"],
            review_request=packet,
            authorization=None,
            selection=selection,
            raw_candidate_hash="c" * 64,
            review_wrapper_json=None,
            review_call_id=None,
            review_call_event_index=None,
            review_result_event_index=None,
        )
    )
    return port.ResearchMappedObservationV2(
        schema_version="research-mapped-observation.v2",
        context=case.context,
        mapping_input_ref=case.reference,
        latest_transport_ref=case.reference,
        mapping_input_generation=1,
        latest_transport_generation=1,
        perpetual_synthesis=nested,
        result=case.summary,
        result_content_hash=research_result_content_hash(case.summary),
        recorded_at=case.now + timedelta(seconds=3),
    )


def guard(case):
    def check(session):
        row = session.get(RunRecord, case.request.run_id, populate_existing=True)
        if row.status not in {"admitted", "running"} or row.lease_owner != "carrier-test":
            raise PermissionError("carrier_test_run_or_lease_changed")

    return check


def test_perpetual_carrier_has_its_own_version_without_legacy_horizon_witness():
    port = importlib.import_module("packages.kernel.decision_hub_kernel.ports.research_observation")
    assert hasattr(port, "ResearchMappedObservationV2"), "missing V2 native observation carrier"
    assert "selection" not in port.ResearchMappedObservationV2.model_fields
    assert "perpetual_synthesis" in port.ResearchMappedObservationV2.model_fields


def test_legacy_frozen_task_cannot_store_perpetual_candidate(native_case):
    value = carrier(native_case)
    binding = importlib.import_module(BINDING)
    with native_case.database.session() as session:
        before = session.scalar(select(func.count()).select_from(RunRoleOutputRecord))
    with pytest.raises(ValueError, match="perpetual_protocol_mismatch"):
        with native_case.database.session() as session:
            begin_immediate(session)
            binding.store_mapped_perpetual_observation(
                session, value, binding_validator=guard(native_case)
            )
    with native_case.database.session() as session:
        assert session.scalar(select(func.count()).select_from(RunRoleOutputRecord)) == before

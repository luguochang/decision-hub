"""Native transcript replay must retain Gateway accounting and reject changed results."""

import hashlib
import json
from contextvars import ContextVar
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts import DshSessionResult, ResearchSessionRequest
from packages.kernel.decision_hub_kernel.application import research_publication_binding as binding
from packages.kernel.decision_hub_kernel.application.research_review import (
    research_result_content_hash,
)
from packages.kernel.decision_hub_kernel.persistence.db import ResearchToolCallReservationRecord
from packages.kernel.decision_hub_kernel.ports.research_observation import (
    ObservedExecutionRef,
    ResearchMappedObservationV2,
    ResearchNativeObservationContext,
    ResearchPerpetualSynthesisObservation,
    ResearchTransportObservation,
)
from packages.runtime_adapters.dsh_runtime.cancellation_projection import ProjectionRead
from packages.runtime_adapters.dsh_runtime.event_mapper import map_notifications
from packages.runtime_adapters.dsh_runtime.result_mapper import map_session_result_with_witness
from packages.runtime_adapters.dsh_runtime.web_runtime import (
    DshWebResearchRuntime,
    _normalize_tool_accounting,
    _sdk_run,
)


@pytest.fixture
def native_accounting_case(monkeypatch):
    # Existing complete native prepare/reviewer/finalize proof; no backend is run.
    path = (
        Path(__file__).resolve().parents[2]
        / "artifacts/d2-native-preparation/phased-native-transcript.json"
    )
    source = json.loads(path.read_text("utf-8"))["context"]
    request = ResearchSessionRequest.model_validate(source["request"])
    identity = source["binding"]
    started = datetime(2026, 1, 1, 0, 10, tzinfo=UTC)
    events = [
        {"method": "session.event", "payload": {"sessionId": identity["sessionId"], "event": row}}
        for row in source["events"]
    ]
    encoded = json.dumps(events)
    payload = DshSessionResult(
        schema_version="dsh-session-result.v1",
        run_id=request.run_id,
        dsh_session_id=identity["sessionId"],
        generation=1,
        last_seq=7,
        final_response="",
        events_json=encoded,
        finish_reason="completed",
        started_at=started,
        finished_at=started + timedelta(seconds=8),
        trace_ref="fixture:native-accounting",
        result_hash=hashlib.sha256(("\0" + encoded).encode()).hexdigest(),
    )
    sdk = _sdk_run(payload)
    traces = map_notifications(
        sdk.notifications,
        run_id=request.run_id,
        research_session_id=payload.dsh_session_id,
        received_at=payload.finished_at,
        request=request,
    )
    mapped = map_session_result_with_witness(
        sdk,
        request,
        traces,
        runtime_version="accounting-test",
        profile_ref="decision-research.web",
        started_at=payload.started_at,
        finished_at=payload.finished_at,
        accepted_synthesis_only=True,
    )
    assert mapped.result.total_tool_calls > 0
    native = mapped.perpetual_synthesis
    assert native is not None and native.authorization.verdict == "approved"
    context = ResearchNativeObservationContext(
        run_id=request.run_id,
        event_id=request.event_id,
        snapshot_id=None,
        request_id=request.request_id,
        dsh_session_id=payload.dsh_session_id,
        generation=1,
        **dict.fromkeys(
            [
                "original_task_request_hash",
                "bundle_hash",
                "method_hash",
                "execution_request_hash",
                "first_linked_request_hash",
                "prompt_hash",
            ],
            "a" * 64,
        ),
        rows=(),
    )
    ref = ObservedExecutionRef(
        output_id="raw",
        role="research_transport:raw",
        schema_version="research-transport-observation.v1",
        payload_hash="b" * 64,
        recorded_at=started + timedelta(seconds=9),
    )
    raw = ResearchTransportObservation(
        schema_version="research-transport-observation.v1",
        context=context,
        phase="result",
        reply=payload,
        error_code=None,
        events_json_hash=hashlib.sha256(encoded.encode()).hexdigest(),
        final_response_hash=hashlib.sha256(b"").hexdigest(),
        recorded_at=ref.recorded_at,
    )
    selection = native.selection.model_dump(mode="json")
    selection.update(
        call_event_hash=binding.observation_sha(events[native.selection.call_event_index]),
        result_event_hash=binding.observation_sha(events[native.selection.result_event_index]),
    )
    nested = {
        k: v
        for k, v in asdict(native).items()
        if k not in {"synthesis", "review_request", "authorization", "selection"}
    }
    nested.update(
        synthesis=native.synthesis,
        review_request=native.review_request,
        authorization=native.authorization,
        selection=selection,
    )
    result = _normalize_tool_accounting(mapped.result, 0)
    value = ResearchMappedObservationV2(
        schema_version="research-mapped-observation.v2",
        context=context,
        mapping_input_ref=ref,
        latest_transport_ref=ref,
        mapping_input_generation=1,
        latest_transport_generation=1,
        perpetual_synthesis=ResearchPerpetualSynthesisObservation.model_validate(nested),
        result=result,
        result_content_hash=research_result_content_hash(result),
        recorded_at=started + timedelta(seconds=10),
    )
    # Mock only archived identity/transport acquisition; mapper, attestation,
    # result hash verification and SQL reservation accounting remain real.
    producer = SimpleNamespace(
        execution=SimpleNamespace(request=request),
        method=SimpleNamespace(
            definition=SimpleNamespace(
                runtime_version="accounting-test", profile_ref="decision-research.web"
            )
        ),
    )
    monkeypatch.setattr(binding, "read_transport_observation", lambda _session, _ref: raw)
    monkeypatch.setattr(binding, "_perpetual_original_context", lambda _session, _context: producer)
    engine = create_engine("sqlite+pysqlite:///:memory:")
    ResearchToolCallReservationRecord.__table__.create(engine)
    with Session(engine) as session:
        yield SimpleNamespace(session=session, value=value, mapped=mapped, request=request)
    engine.dispose()


def test_writer_accepts_producer_count_from_gateway_reservations(native_accounting_case):
    case = native_accounting_case
    binding._verify_perpetual_mapping(case.session, case.value)


def test_writer_rejects_native_count_even_with_matching_declared_hash(native_accounting_case):
    case = native_accounting_case
    value = case.value.model_copy(
        update={
            "result": case.mapped.result,
            "result_content_hash": research_result_content_hash(case.mapped.result),
        }
    )
    with pytest.raises(ValueError, match="research_perpetual_evidence_projection_changed"):
        binding._verify_perpetual_mapping(case.session, value)


def test_writer_rejects_unrelated_result_mutation_after_normalization(native_accounting_case):
    case = native_accounting_case
    changed = case.value.result.model_copy(
        update={"total_subagents": case.value.result.total_subagents + 1}
    )
    value = case.value.model_copy(
        update={"result": changed, "result_content_hash": research_result_content_hash(changed)}
    )
    with pytest.raises(ValueError, match="research_perpetual_evidence_projection_changed"):
        binding._verify_perpetual_mapping(case.session, value)


def test_writer_ignores_reservations_from_other_run_generation_or_session(native_accounting_case):
    case = native_accounting_case
    for index, (run, gen, session_id) in enumerate(
        [
            ("other-run", 1, case.value.context.dsh_session_id),
            (case.request.run_id, 2, case.value.context.dsh_session_id),
            (case.request.run_id, 1, "other-session"),
        ]
    ):
        case.session.add(
            ResearchToolCallReservationRecord(
                run_id=run,
                request_id=f"other-{index}",
                research_session_id=session_id,
                generation=gen,
                capability_id="replay.research",
                status="failed",
                reserved_at=datetime.now(UTC),
            )
        )
    case.session.flush()
    binding._verify_perpetual_mapping(case.session, case.value)


def _reserve(case, statuses):
    for index, status in enumerate(statuses):
        case.session.add(
            ResearchToolCallReservationRecord(
                run_id=case.request.run_id,
                request_id=f"actual-{index}",
                research_session_id=case.value.context.dsh_session_id,
                generation=1,
                capability_id="replay.research",
                status=status,
                reserved_at=datetime.now(UTC),
            )
        )
    case.session.flush()


def test_writer_counts_failed_and_completed_gateway_executions(native_accounting_case):
    case = native_accounting_case
    _reserve(case, ["completed", "failed"])
    result = _normalize_tool_accounting(case.mapped.result, 2)
    value = case.value.model_copy(
        update={"result": result, "result_content_hash": research_result_content_hash(result)}
    )
    binding._verify_perpetual_mapping(case.session, value)


def test_writer_retains_original_gateway_budget_bound(native_accounting_case):
    case = native_accounting_case
    _reserve(case, ["completed"] * (case.request.execution_budget.max_tool_calls + 1))
    with pytest.raises(ValueError, match="research_perpetual_tool_count_out_of_bounds"):
        binding._verify_perpetual_mapping(case.session, case.value)


def _producer_runtime(case):
    runtime = object.__new__(DshWebResearchRuntime)
    runtime.proof_read_wait_seconds = 15
    runtime._binding_deadline = ContextVar("accounting_test_deadline", default=None)

    async def read_original(verify, **_budgets):
        # Only the async read admission is local; SQL/count/normalization are real.
        try:
            return ProjectionRead("ready", value=verify(case.session))
        except ValueError as exc:
            return ProjectionRead("invalid", limitation=str(exc))

    runtime._read_binding_with_contention_wait = read_original
    return runtime


@pytest.mark.parametrize("additional_executions", [0, 1])
async def test_same_generation_recovery_producer_uses_complete_gateway_count(
    native_accounting_case,
    additional_executions,
):
    case = native_accounting_case
    # k=2 were already accounted before recovery; completed and failed both count.
    _reserve(case, ["completed", "failed"] + ["completed"] * additional_executions)
    runtime = _producer_runtime(case)
    assert hasattr(runtime, "_account_result_tool_calls"), (
        "V2 producer needs shared generation accounting"
    )
    # Only the local operation deadline changes; archived native proof stays unchanged.
    request = case.request.model_copy(
        update={"deadline_at": datetime.now(UTC) + timedelta(seconds=30)}
    )
    result = await runtime._account_result_tool_calls(
        case.mapped.result,
        request,
        generation=1,
        research_session_id=case.value.context.dsh_session_id,
        tool_calls_started=2 + additional_executions,
        tool_calls_before=2,
    )
    assert result.total_tool_calls == 2 + additional_executions
    value = case.value.model_copy(
        update={"result": result, "result_content_hash": research_result_content_hash(result)}
    )
    binding._verify_perpetual_mapping(case.session, value)
    delta = result.model_copy(update={"total_tool_calls": additional_executions})
    with pytest.raises(ValueError, match="research_perpetual_evidence_projection_changed"):
        binding._verify_perpetual_mapping(
            case.session,
            value.model_copy(
                update={
                    "result": delta,
                    "result_content_hash": research_result_content_hash(delta),
                }
            ),
        )


async def test_legacy_producer_retains_existing_recovery_delta(native_accounting_case):
    case = native_accounting_case
    runtime = _producer_runtime(case)
    assert hasattr(runtime, "_account_result_tool_calls"), (
        "Producer accounting preserves legacy delta branch"
    )
    request = case.request.model_copy(
        update={
            "output_schema_ref": "research_signal.schema.yaml#/$defs/research_decision_synthesis_v1"
        }
    )
    result = await runtime._account_result_tool_calls(
        case.mapped.result,
        request,
        generation=1,
        research_session_id=case.value.context.dsh_session_id,
        tool_calls_started=3,
        tool_calls_before=2,
    )
    assert result.total_tool_calls == 1

"""Real private SQLite observations/callbacks; no Host or real Run replay."""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from packages.contracts_py.decision_hub_contracts import DshBridgeError, DshSessionCompletion, ResearchSynthesisCandidate
from packages.kernel.decision_hub_kernel.application.dsh_sessions import DshSessionLinkService
from packages.kernel.decision_hub_kernel.application.provider_input_receipts import ProviderInputReceiptService, sha
from packages.kernel.decision_hub_kernel.application.research_context import build_task_policy
from packages.kernel.decision_hub_kernel.application.research_provider_usage import read_provider_run_usage_in_session
from packages.kernel.decision_hub_kernel.application.research_publication_binding import (
    native_observation_context_in_session, observation_json, observation_sha,
    read_mapped_observation, read_transport_observation,
    store_mapped_observation, store_transport_observation,
)
from packages.kernel.decision_hub_kernel.application.research_request_identity import research_request_hash
from packages.kernel.decision_hub_kernel.persistence.db import DshSessionLinkRecord, RunRoleOutputRecord, utcnow
from tests.dsh_native.test_execution_preparation_v2 import inputs as inputs_fixture
from tests.dsh_native.test_provider_input_receipts import active as active_fixture, _mapped, finish
from tests.dsh_native.test_provider_run_usage import usage_originals as equal_fixture
from tests.orchestration.test_agentic_research_graph import _causal_case, _horizons
from tests.research.test_full_method_v2_bundle import forbid_external_connections

pytestmark = pytest.mark.usefixtures("forbid_external_connections")
assert forbid_external_connections is not None


def _save_mapping(inputs, mapped, raw, reply):
    with inputs.database.session() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        now = utcnow()
        transport = store_transport_observation(session, raw.model_copy(update={
            "reply": reply, "recorded_at": now,
            "events_json_hash": sha(reply.events_json), "final_response_hash": sha(reply.final_response),
        }))
        return store_mapped_observation(session, mapped.model_copy(update={
            "mapping_input_ref": transport, "latest_transport_ref": transport,
            "recorded_at": now,
        }), binding_validator=lambda current: native_observation_context_in_session(current, inputs.request.run_id))


@pytest.fixture(scope="module", params=["failed", "cancelled"])
def snapshot_originals(request, tmp_path_factory):
    maker = inputs_fixture.__wrapped__(tmp_path_factory.mktemp("usage-native-snapshot48"))
    inputs = next(maker)
    policy = build_task_policy(
        research_mode=inputs.request.research_mode or "current_decision",
        requirements=inputs.request.evidence_requirements,
        created_at=inputs.now, max_evidence_rounds=inputs.request.execution_budget.max_evidence_rounds,
    )
    scoped = inputs.request.model_copy(update={"task_policy_ref": f"{policy.policy_id}.{policy.policy_version}:{policy.content_hash[:16]}"})
    digest = research_request_hash(scoped)
    session_id, request_id = DshSessionLinkService.deterministic_ids(scoped.run_id, digest)
    inputs = replace(inputs, request=scoped, submit=inputs.submit.model_copy(update={
        "request_hash": digest, "deterministic_session_id": session_id, "deterministic_request_id": request_id,
    }))
    active = active_fixture.__wrapped__(inputs)
    inputs, payload = next(active)
    try:
        service = ProviderInputReceiptService(inputs.database)
        provider = service.record_input(payload)
        service.record_finish(finish(payload, provider))
        candidate = ResearchSynthesisCandidate.model_validate(dict(
            schema_version="research-synthesis-candidate.v1", request_id=inputs.request.request_id,
            causal_case=_causal_case(thesis="Private immutable snapshot control.", unresolved_questions=[]), horizons=_horizons(),
        ))
        old_ref = _mapped(inputs, candidate, {"candidate": candidate.model_dump(mode="json")})
        with inputs.database.session() as session:
            session.connection().exec_driver_sql("BEGIN")
            mapped = read_mapped_observation(session, old_ref)
            raw = read_transport_observation(session, mapped.mapping_input_ref)
        end = int(utcnow().timestamp() * 1000)
        start = end - 1000
        tool_rows = json.loads(raw.reply.events_json)
        events = [
            {"method": "session.event", "payload": {"sessionId": session_id, "event": {
                "type": "user/message", "seq": 1, "time": start,
                "data": {"source": {"kind": "user", "rpcId": request_id}},
            }}},
            {"method": "session.event", "payload": {"sessionId": session_id, "event": {
                "type": "step/start", "seq": 2, "time": start, "data": {},
            }}},
        ]
        for sequence, row in enumerate(tool_rows, 3):
            row["payload"]["event"].update(seq=sequence, time=start)
            events.append(row)
        events.append({"method": "session.event", "payload": {"sessionId": session_id, "event": {
            "type": "turn/end", "seq": 5, "time": end, "data": {"reason": {"kind": "error"}},
        }}})
        encoded = observation_json(events)
        reply = raw.reply.model_copy(update={
            "events_json": encoded, "result_hash": sha("\0" + encoded), "last_seq": 5,
            "started_at": datetime.fromtimestamp(start / 1000, UTC),
            "finished_at": datetime.fromtimestamp(end / 1000, UTC), "finish_reason": "error",
            "trace_ref": f"dsh://sessions/{session_id}?last_seq=5",
        })
        mapped = mapped.model_copy(update={"selection": mapped.selection.model_copy(update={
            "call_event_index": 2, "result_event_index": 3, "call_seq": 3, "result_seq": 4,
            "call_event_hash": observation_sha(events[2]), "result_event_hash": observation_sha(events[3]),
        })})
        mapped_ref = _save_mapping(inputs, mapped, raw, reply)
        terminal = DshSessionCompletion(
            schema_version="dsh-session-completion.v1", run_id=payload.run_id, dsh_session_id=session_id,
            generation=1, terminal_status=request.param, last_seq=7, trace_ref=reply.trace_ref,
            result_ref=f"dsh-host://runs/{payload.run_id}/result", result_hash=reply.result_hash,
            completed_at=reply.finished_at + (timedelta(milliseconds=20) if request.param == "cancelled" else timedelta()),
            error=DshBridgeError(code="dsh_provider_input_transport_timeout", message="Private controlled timeout", retryable=False) if request.param == "failed" else None,
        )
        DshSessionLinkService(inputs.database).complete(terminal, authenticated_host=True)
        yield inputs, mapped_ref, mapped, raw, reply, terminal
    finally:
        active.close()
        maker.close()


def _terminal_fields(session, run_id):
    link = session.get(DshSessionLinkRecord, run_id, populate_existing=True)
    row = session.query(RunRoleOutputRecord).filter_by(run_id=run_id, role="dsh_authenticated_terminal:1").one()
    return (
        tuple((column.name, getattr(link, column.name)) for column in link.__table__.columns),
        tuple((column.name, getattr(row, column.name)) for column in row.__table__.columns),
    )


def test_native_drain_cursor_preserves_failed_or_cancelled_originals(snapshot_originals):
    inputs, ref, _mapped_value, _raw, reply, terminal = snapshot_originals
    assert terminal.last_seq > reply.last_seq
    with inputs.database.session() as session:
        session.connection().exec_driver_sql("BEGIN")
        native = session.connection().connection.driver_connection
        before, fields = native.total_changes, _terminal_fields(session, inputs.request.run_id)
        for _ in range(2):
            census = read_provider_run_usage_in_session(session, mapped_ref=ref, as_of=utcnow())
            assert census.coverage_status != "complete"
            assert "provider_conversation_terminal_not_completed" in census.missing_codes
            assert census.total_tokens is None and not census.budget_qualified
        assert native.total_changes == before and _terminal_fields(session, inputs.request.run_id) == fields
        assert not session.new and not session.dirty and not session.deleted


@pytest.mark.parametrize("change", ["content", "cursor", "trace", "start", "finish", "time"])
def test_authentic_snapshot_metadata_or_content_mutation_is_rejected(snapshot_originals, change):
    inputs, _ref, mapped, raw, reply, _terminal = snapshot_originals
    updates = {
        "content": {"final_response": "changed", "result_hash": sha("changed\0" + reply.events_json)},
        "cursor": {"last_seq": 8},
        "trace": {"trace_ref": reply.trace_ref + "&extra=1"},
        "start": {"started_at": reply.started_at - timedelta(milliseconds=1)},
        "finish": {"finish_reason": "completed"},
        "time": {"finished_at": reply.finished_at - timedelta(milliseconds=1)},
    }
    changed = _save_mapping(inputs, mapped, raw, reply.model_copy(update=updates[change]))
    with inputs.database.session() as session:
        session.connection().exec_driver_sql("BEGIN")
        before = _terminal_fields(session, inputs.request.run_id)
        with pytest.raises(ValueError, match="research_usage_terminal_result_changed"):
            read_provider_run_usage_in_session(session, mapped_ref=changed, as_of=utcnow())
        assert _terminal_fields(session, inputs.request.run_id) == before


@pytest.fixture(scope="module")
def equal_originals(tmp_path_factory):
    producer = equal_fixture.__wrapped__(tmp_path_factory)
    try:
        yield next(producer)
    finally:
        producer.close()


def test_existing_exact_snapshot_keeps_current_unknown_native_census(equal_originals):
    inputs, ref, census, _partial = equal_originals
    with inputs.database.session() as session:
        session.connection().exec_driver_sql("BEGIN")
        mapped = read_mapped_observation(session, ref)
        raw = read_transport_observation(session, mapped.mapping_input_ref)
        fields = _terminal_fields(session, inputs.request.run_id)
        link = session.get(DshSessionLinkRecord, inputs.request.run_id, populate_existing=True)
        assert link.last_seq == raw.reply.last_seq and link.terminal_at.replace(tzinfo=UTC) == raw.reply.finished_at
        native = session.connection().connection.driver_connection
        before = native.total_changes
        for _ in range(2):
            current = read_provider_run_usage_in_session(session, mapped_ref=ref, as_of=census.as_of)
            assert current == census
            assert current.coverage_status == "incomplete" and current.total_tokens is None
            assert "provider_native_lifecycle_census_missing" in current.missing_codes
        assert native.total_changes == before and _terminal_fields(session, inputs.request.run_id) == fields

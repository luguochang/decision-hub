"""Authenticated private SQLite callbacks and immutable partial snapshots.

These controls make no real Host/model calls and grant no report qualification.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from concurrent.futures import Future
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from packages.contracts_py.decision_hub_contracts import (
    DshBridgeError,
    DshSessionCompletion,
    DshSessionResult,
    DshSessionStatus,
    DshSessionSubmit,
)
from packages.kernel.decision_hub_kernel.application.dsh_sessions import DshSessionLinkService
from packages.kernel.decision_hub_kernel.application.research_context import build_task_policy
from packages.kernel.decision_hub_kernel.application.research_task import ResearchTaskService
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.persistence.db import (
    DshSessionLinkRecord,
    RunRoleOutputRecord,
)
from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError
from packages.runtime_adapters.dsh_runtime.cancellation_projection import ProjectionRead
from packages.runtime_adapters.dsh_runtime.web_runtime import DshWebResearchRuntime
from tests.dsh_native.test_dsh_web_runtime import (
    FakeHostClient,
    _database,  # pyright: ignore[reportPrivateUsage]
    _failed_result_with_attested_evidence,  # pyright: ignore[reportPrivateUsage]
    _request,  # pyright: ignore[reportPrivateUsage]
)
from tests.research.test_full_method_v2_bundle import forbid_external_connections
from tests.research.test_review_recovery import (
    _case,  # pyright: ignore[reportPrivateUsage]
    _terminal_row,  # pyright: ignore[reportPrivateUsage]
)

pytestmark = pytest.mark.usefixtures("forbid_external_connections")
assert forbid_external_connections is not None


def _native_metadata(result: DshSessionResult, submit: DshSessionSubmit) -> DshSessionResult:
    rows = json.loads(result.events_json)
    base = int(result.started_at.timestamp() * 1000)
    end = int(result.finished_at.timestamp() * 1000)
    events: list[dict[str, object]] = [{
        "type": "user/message", "seq": 1, "time": base,
        "data": {"source": {"kind": "user", "rpcId": submit.deterministic_request_id}},
    }]
    for sequence, row in enumerate(rows, 2):
        event = dict(row["payload"]["event"])
        event.update(seq=sequence, time=base)
        events.append(event)
    events.append({
        "type": "turn/end", "seq": len(events) + 1, "time": end,
        "data": {"reason": {"kind": result.finish_reason}},
    })
    body = json.dumps([{
        "method": "session.event", "payload": {
            "sessionId": submit.deterministic_session_id, "event": event,
        },
    } for event in events])
    return result.model_copy(update={
        "started_at": datetime.fromtimestamp(base / 1000, UTC),
        "finished_at": datetime.fromtimestamp(end / 1000, UTC), "events_json": body,
        "result_hash": hashlib.sha256((result.final_response + "\0" + body).encode()).hexdigest(),
    })


def _original(
    tmp_path: Path, *, cancelled: bool = False, completed: bool = False, authenticated: bool = True,
):
    case = _case(tmp_path, terminalize=False)
    link_service = DshSessionLinkService(case.database, clock=lambda: case.now)
    link = link_service.get(case.failed_run_id)
    assert link is not None
    submit = DshSessionSubmit(
        schema_version="dsh-session-submit.v1", run_id=link.run_id,
        request_hash=link.request_hash, deterministic_session_id=link.dsh_session_id,
        deterministic_request_id=link_service.deterministic_ids(
            link.run_id, link.request_hash, link.generation,
        )[1], workspace_ref="decision-hub://workspace/default",
        prompt_ref=f"hub://runs/{link.run_id}/prompts/{link.generation}",
        agent_preset="decision-research", permission_ref="decision-hub://permissions/research-only",
        deadline_at=case.request.deadline_at,
        max_tool_calls=case.request.execution_budget.max_tool_calls,
        model_step_timeout_ms=case.request.execution_budget.per_model_step_timeout_seconds * 1000,
        generation=link.generation,
    )
    snapshot_seq = 1171
    trace = f"dsh://sessions/{link.dsh_session_id}?last_seq={snapshot_seq}"
    final, events = "{}", "[]"
    result = DshSessionResult(
        schema_version="dsh-session-result.v1", run_id=link.run_id,
        dsh_session_id=link.dsh_session_id, generation=link.generation,
        last_seq=snapshot_seq, final_response=final, events_json=events,
        finish_reason="completed" if completed else "error",
        started_at=case.completion.completed_at - timedelta(seconds=25),
        finished_at=case.completion.completed_at, trace_ref=trace,
        result_hash=hashlib.sha256((final + "\0" + events).encode()).hexdigest(),
    )
    result = _native_metadata(result, submit)
    original = case.completion.model_copy(update={
        "last_seq": 1182, "trace_ref": trace,
        "result_ref": f"dsh-host://runs/{link.run_id}/result", "result_hash": result.result_hash,
        "terminal_status": "cancelled" if cancelled else "completed" if completed else "failed",
        "error": None if cancelled or completed else case.completion.error,
        "completed_at": (
            result.finished_at + timedelta(seconds=2) if cancelled else result.finished_at
        ),
    })
    link_service.complete(original, authenticated_host=authenticated)
    status = DshSessionStatus(
        schema_version="dsh-session-status.v1", run_id=link.run_id,
        dsh_session_id=link.dsh_session_id, generation=link.generation,
        state="cancelled", last_seq=1182, observed_at=datetime.now(UTC),
        error_code="host_command_activity_pending",
    )
    return case, link_service, submit, result, original, status


@pytest.mark.asyncio
@pytest.mark.parametrize("state,sequence", [("cancelled", 1182), ("failed", 1168)])
async def test_original_terminal_and_partial_cutoff_are_not_rewritten(
    tmp_path: Path, state: str, sequence: int,
) -> None:
    case, links, submit, result, original, status = _original(tmp_path)
    before = _terminal_row(case.database, submit.run_id)
    runtime = DshWebResearchRuntime(links, FakeHostClient())
    try:
        confirmed, receipt = await runtime._terminal_original(  # pyright: ignore[reportPrivateUsage]
            status.model_copy(update={"state": state, "last_seq": sequence}), submit,
        )
        assert receipt == original
        assert confirmed.state == "failed" and confirmed.last_seq == 1182
        assert confirmed.error_code == "host_turn_failed"
        await runtime._complete_terminal_result(  # pyright: ignore[reportPrivateUsage]
            confirmed, submit, result, receipt,
        )
        assert result.last_seq == 1171 and result.finished_at == original.completed_at
        assert _terminal_row(case.database, submit.run_id) == before
        assert links.get(submit.run_id).last_seq == 1182  # type: ignore[union-attr]
        with pytest.raises(ValueError, match="dsh_terminal_conflict"):
            links.complete(original.model_copy(update={"last_seq": 1171}))
    finally:
        await runtime.close()
        case.database.engine.dispose()


@pytest.mark.asyncio
async def test_terminal_original_waits_for_same_late_read_future_until_submit_deadline(
    tmp_path: Path,
) -> None:
    case, links, submit, _result, original, status = _original(tmp_path)
    runtime = DshWebResearchRuntime(links, FakeHostClient(), proof_read_wait_seconds=0.02)
    real_projector = runtime.cancellation_projector
    future: Future[ProjectionRead[DshSessionCompletion | None]] = Future()
    completion_task: asyncio.Task[None] | None = None

    class SameReadFuture:
        def poll(self) -> ProjectionRead[DshSessionCompletion | None]:
            if not future.done():
                return ProjectionRead(
                    "pending", limitation="cancellation_projection_wait_elapsed", pending=self,
                )
            return future.result()

    pending = SameReadFuture()

    class DelayedProjector:
        read_calls = 0
        operation_calls = 0

        async def read(self, operation, *, timeout_seconds):
            nonlocal completion_task
            self.read_calls += 1

            async def complete() -> None:
                await asyncio.sleep(0.06)
                assert real_projector.sessions is not None
                with real_projector.sessions.session() as session:
                    session.connection().exec_driver_sql("BEGIN")
                    self.operation_calls += 1
                    value = operation(session)
                future.set_result(ProjectionRead("ready", value))

            completion_task = asyncio.create_task(complete())
            await asyncio.sleep(timeout_seconds)
            return pending.poll()

        async def close(self) -> None:
            if completion_task is not None:
                await completion_task
            await real_projector.close()

    delayed = DelayedProjector()
    runtime.cancellation_projector = delayed  # type: ignore[assignment]
    submit = submit.model_copy(update={"deadline_at": datetime.now(UTC) + timedelta(seconds=0.3)})
    try:
        confirmed, receipt = await runtime._terminal_original(  # pyright: ignore[reportPrivateUsage]
            status, submit,
        )
        assert receipt == original
        assert confirmed.state == "failed" and confirmed.last_seq == original.last_seq
        assert delayed.read_calls == 1 and delayed.operation_calls == 1
    finally:
        await runtime.close()
        case.database.engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    "hash", "generation", "trace", "time", "cursor", "reason", "start",
])
async def test_wrong_partial_snapshot_is_rejected(tmp_path: Path, change: str) -> None:
    case, links, submit, result, _original_value, status = _original(tmp_path)
    runtime = DshWebResearchRuntime(links, FakeHostClient())
    before = _terminal_row(case.database, submit.run_id)
    changes = {
        "hash": {"result_hash": "f" * 64},
        "generation": {"generation": 2},
        "trace": {"trace_ref": result.trace_ref + "&extra=1"},
        "time": {"finished_at": datetime.now(UTC)},
        "cursor": {"last_seq": 1183},
        "reason": {"finish_reason": "completed"},
        "start": {"started_at": result.started_at - timedelta(seconds=1)},
    }
    try:
        confirmed, receipt = await runtime._terminal_original(status, submit)  # pyright: ignore[reportPrivateUsage]
        with pytest.raises(AgentExecutionError):
            await runtime._complete_terminal_result(  # pyright: ignore[reportPrivateUsage]
                confirmed, submit, result.model_copy(update=changes[change]), receipt,
            )
        assert _terminal_row(case.database, submit.run_id) == before
    finally:
        await runtime.close()
        case.database.engine.dispose()


@pytest.mark.asyncio
async def test_tampered_callback_is_not_a_terminal_authority(tmp_path: Path) -> None:
    case, links, submit, _result_value, _original_value, status = _original(tmp_path)
    with case.database.session() as session:
        row = session.query(RunRoleOutputRecord).filter_by(
            run_id=submit.run_id, role="dsh_authenticated_terminal:1",
        ).one()
        row.payload_hash = "f" * 64
    runtime = DshWebResearchRuntime(links, FakeHostClient())
    try:
        with pytest.raises(AgentExecutionError) as failure:
            await runtime._terminal_original(status, submit)  # pyright: ignore[reportPrivateUsage]
        assert failure.value.error_code == "research_terminal_original_unavailable"
        assert failure.value.cause_code == "dsh_authenticated_terminal_invalid"
    finally:
        await runtime.close()
        case.database.engine.dispose()


@pytest.mark.asyncio
async def test_cancelled_partial_preserves_earlier_native_finish(tmp_path: Path) -> None:
    case, links, submit, result, original, status = _original(tmp_path, cancelled=True)
    runtime = DshWebResearchRuntime(links, FakeHostClient())
    before = _terminal_row(case.database, submit.run_id)
    try:
        confirmed, receipt = await runtime._terminal_original(status, submit)  # pyright: ignore[reportPrivateUsage]
        assert original.completed_at > result.finished_at
        await runtime._complete_terminal_result(  # pyright: ignore[reportPrivateUsage]
            confirmed, submit, result, receipt,
        )
        with pytest.raises(AgentExecutionError):
            await runtime._complete_terminal_result(  # pyright: ignore[reportPrivateUsage]
                confirmed, submit,
                result.model_copy(update={
                    "finished_at": result.finished_at - timedelta(seconds=1),
                }),
                receipt,
            )
        assert _terminal_row(case.database, submit.run_id) == before
    finally:
        await runtime.close()
        case.database.engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("authenticated", [True, False])
async def test_late_terminal_needs_original_callback_not_legacy_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, authenticated: bool,
) -> None:
    case, links, submit, result, _original_value, status = _original(
        tmp_path, authenticated=authenticated,
    )
    runtime = DshWebResearchRuntime(links, FakeHostClient())
    calls = 0

    async def one_writer(completion: DshSessionCompletion, _submit: DshSessionSubmit) -> None:
        nonlocal calls
        calls += 1
        links.complete(completion)

    monkeypatch.setattr(runtime, "_complete_original_result", one_writer)
    status = status.model_copy(update={"state": "failed", "error_code": "host_turn_failed"})
    try:
        if authenticated:
            before = _terminal_row(case.database, submit.run_id)
            await runtime._complete_terminal_result(status, submit, result)  # pyright: ignore[reportPrivateUsage]
            assert _terminal_row(case.database, submit.run_id) == before
        else:
            with pytest.raises(ValueError, match="dsh_terminal_conflict"):
                await runtime._complete_terminal_result(status, submit, result)  # pyright: ignore[reportPrivateUsage]
        assert calls == 1
        with pytest.raises(ValueError, match="dsh_generation_conflict"):
            await runtime._complete_terminal_result(  # pyright: ignore[reportPrivateUsage]
                status, submit.model_copy(update={"generation": 2}), result,
            )
        assert calls == 2
    finally:
        await runtime.close()
        case.database.engine.dispose()


@pytest.mark.asyncio
async def test_completed_callback_confirms_original_snapshot(tmp_path: Path) -> None:
    case, links, submit, result, _original_value, status = _original(tmp_path, completed=True)
    runtime = DshWebResearchRuntime(links, FakeHostClient())
    before = _terminal_row(case.database, submit.run_id)
    try:
        confirmed, receipt = await runtime._terminal_original(status, submit)  # pyright: ignore[reportPrivateUsage]
        assert confirmed.state == "completed"
        await runtime._complete_terminal_result(  # pyright: ignore[reportPrivateUsage]
            confirmed, submit, result, receipt,
        )
        assert _terminal_row(case.database, submit.run_id) == before
    finally:
        await runtime.close()
        case.database.engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("stale_state,available", [("cancelled", True), ("failed", False)])
async def test_ordinary_runtime_uses_callback_instead_of_stale_terminal(
    tmp_path: Path, stale_state: str, available: bool,
) -> None:
    database = _database(tmp_path / "callback.sqlite3")
    run_id, _ = RunService(database).create("event-web-host")
    links = DshSessionLinkService(database)

    class Host(FakeHostClient):
        partial: DshSessionResult | None = None
        original_bytes: tuple[str, str] | None = None
        result_reads = 0

        async def status(self, payload: DshSessionSubmit) -> DshSessionStatus:
            now = datetime.now(UTC)
            partial = _failed_result_with_attested_evidence(payload).model_copy(update={
                "started_at": now, "finished_at": now,
            })
            partial = _native_metadata(partial, payload)
            partial = partial.model_copy(update={
                "trace_ref": (
                    f"dsh://sessions/{payload.deterministic_session_id}?last_seq={partial.last_seq}"
                ),
            })
            self.partial = partial
            with database.session() as session:
                link = session.get(DshSessionLinkRecord, payload.run_id)
                assert link is not None
                link.tool_calls_started = 1
            links.complete(DshSessionCompletion(
                schema_version="dsh-session-completion.v1", run_id=payload.run_id,
                dsh_session_id=payload.deterministic_session_id, generation=payload.generation,
                terminal_status="failed", last_seq=partial.last_seq + 5,
                trace_ref=partial.trace_ref, result_hash=partial.result_hash,
                result_ref=f"dsh-host://runs/{payload.run_id}/result",
                completed_at=partial.finished_at,
                error=DshBridgeError(
                    code="dsh_provider_input_transport_timeout",
                    message="Original provider input timeout", retryable=True,
                ),
            ), authenticated_host=True)
            self.original_bytes = _terminal_row(database, run_id)
            return self.status_value(payload, stale_state, partial.last_seq).model_copy(update={
                "observed_at": now, "error_code": "host_command_activity_pending",
            })

        async def result(self, payload: DshSessionSubmit) -> DshSessionResult:
            self.result_reads += 1
            if not available:
                raise AgentExecutionError(
                    "dsh_host_conflict", "Unavailable original partial", provider_id="dsh-web",
                    origin="dsh", cause_code="http_409",
                )
            assert self.partial is not None
            return self.partial

    host = Host()
    runtime = DshWebResearchRuntime(links, host)
    request = _request(run_id)
    policy = build_task_policy(
        research_mode=request.research_mode or "current_decision",
        requirements=request.evidence_requirements, created_at=datetime.now(UTC),
        max_evidence_rounds=request.execution_budget.max_evidence_rounds,
    )
    request = request.model_copy(update={
        "task_policy_ref": f"{policy.policy_id}.{policy.policy_version}:{policy.content_hash[:16]}",
    })
    ResearchTaskService(database).freeze(request)
    try:
        if available:
            result = await runtime.execute(request)
            assert result.status == "degraded" and result.causal_case is None
            assert len(result.evidence_candidates) == 1
        else:
            with pytest.raises(AgentExecutionError) as failure:
                await runtime.execute(request)
            assert failure.value.error_code == "dsh_provider_input_transport_timeout"
            assert failure.value.cause_code == "failed"
        assert len(host.submit_calls) == 1 and host.result_reads == 1
        assert host.cancelled == []
        assert _terminal_row(database, run_id) == host.original_bytes
        link = links.get(run_id)
        assert link is not None and link.state == "failed"
        assert link.error_code == "dsh_provider_input_transport_timeout"
    finally:
        await runtime.close()
        database.engine.dispose()

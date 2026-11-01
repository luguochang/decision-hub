"""Private SQLite confirmation seams; no native/authentication acceptance claim."""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts import DshSessionStatus
from packages.kernel.decision_hub_kernel.application.dsh_sessions import DshSessionLinkService
from packages.kernel.decision_hub_kernel.application.research_publication_binding import (
    read_transport_observation,
)
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.persistence.db import Database, RunRecord
from packages.kernel.decision_hub_kernel.ports.research_observation import (
    ObservedExecutionRef,
    ResearchNativeObservationContext,
)
from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError
from packages.runtime_adapters.dsh_runtime import web_runtime
from packages.runtime_adapters.dsh_runtime.cancellation_projection import (
    DshCancellationProjector,
    ProjectionPending,
    ProjectionRead,
)


def private_database(path: Path) -> tuple[Database, str, DshCancellationProjector]:
    database = Database(f"sqlite+pysqlite:///{path}")
    database.create_all()
    run_id, _ = RunService(database).create("private-pending-confirmation")
    return database, run_id, DshCancellationProjector(DshSessionLinkService(database))


async def settled[T](handle: ProjectionPending[T]) -> ProjectionRead[T]:
    # A control waits for its own thread; production poll() adds no wait budget.
    for _ in range(300):
        result = handle.poll()
        if result.pending is None:
            return result
        await asyncio.sleep(0.005)
    raise AssertionError("private thread did not settle")


@pytest.mark.asyncio
async def test_same_future_late_commit_and_two_outstanding_slots(tmp_path: Path) -> None:
    database, run_id, projector = private_database(tmp_path / "late.sqlite3")
    release = threading.Event()
    calls: list[str] = []
    stages: list[str] = []

    def save(session: Session) -> str:
        calls.append("save")
        row = session.get(RunRecord, run_id)
        assert row is not None
        row.error_code = "private_late_receipt"
        session.flush()
        assert release.wait(2)
        return run_id

    try:
        first = await projector.write(
            save, timeout_seconds=0.05,
            stage_observer=lambda _id, stage, _code: stages.append(stage),
        )
        assert first.state == "pending" and first.value is None and first.pending is not None
        assert first.pending.poll().state == "pending"
        # A second physical operation is bounded; a third cannot create a thread.
        second = await projector.read(lambda _session: release.wait(2), timeout_seconds=0.05)
        assert second.pending is not None
        third = await projector.read(lambda _session: "must_not_run", timeout_seconds=0.05)
        assert third.state == "pending" and third.limitation == "cancellation_projection_busy"
        release.set()
        final = await settled(first.pending)
        assert final.state == "ready" and final.value == run_id
        assert calls == ["save"]
        assert set(stages) == {"queued", "begin_acquired", "operation_completed", "commit_done"}
        with database.session() as session:
            row = session.get(RunRecord, run_id)
            assert row is not None and row.error_code == "private_late_receipt"
        await settled(second.pending)
    finally:
        release.set()
        await projector.close()
        database.engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("uncertain", [False, True])
async def test_late_rollback_and_uncertain_commit_never_ack_or_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, uncertain: bool,
) -> None:
    database, run_id, projector = private_database(tmp_path / f"failure-{uncertain}.sqlite3")
    release = threading.Event()
    calls: list[str] = []
    stages: list[str] = []
    assert projector.sessions is not None
    original_session = projector.sessions.session

    @contextmanager
    def uncertain_session() -> Generator[Session, None, None]:
        with original_session() as session:
            yield session
        # Model an exception after a real commit: no rollback inference.
        raise OperationalError("COMMIT", {}, RuntimeError("private uncertain commit"))

    if uncertain:
        monkeypatch.setattr(projector.sessions, "session", uncertain_session)

    def save(session: Session) -> str:
        calls.append("save")
        row = session.get(RunRecord, run_id)
        assert row is not None
        row.error_code = "private_write_maybe_committed"
        session.flush()
        assert release.wait(2)
        if not uncertain:
            raise ValueError("private_original_rejected")
        return run_id

    try:
        first = await projector.write(
            save, timeout_seconds=0.05,
            stage_observer=lambda _id, stage, _code: stages.append(stage),
        )
        assert first.pending is not None
        release.set()
        final = await settled(first.pending)
        assert final.state != "ready" and final.value is None
        assert first.pending.poll() == final and calls == ["save"]
        assert "failed" in stages and "commit_done" not in stages
        with database.session() as session:
            row = session.get(RunRecord, run_id)
            assert row is not None
            assert row.error_code == ("private_write_maybe_committed" if uncertain else None)
    finally:
        release.set()
        await projector.close()
        database.engine.dispose()


def private_runtime(
    database: Database, run_id: str, projector: DshCancellationProjector,
) -> web_runtime.DshWebResearchRuntime:
    # Only the error/audit seam is isolated. It grants no Task permission.
    runtime = web_runtime.DshWebResearchRuntime.__new__(web_runtime.DshWebResearchRuntime)
    context = ResearchNativeObservationContext(
        run_id=run_id, event_id="private-event", snapshot_id=None, request_id="private-request",
        dsh_session_id="private-native", generation=1, original_task_request_hash="0" * 64,
        bundle_hash="0" * 64, method_hash="0" * 64, execution_request_hash="0" * 64,
        first_linked_request_hash="0" * 64, prompt_hash="0" * 64, rows=(),
    )
    runtime._observations = ContextVar("private-observations", default=None)
    runtime._observations.set(web_runtime._RuntimeObservations(context=context))
    runtime._binding_deadline = ContextVar("private-binding-deadline", default=None)
    runtime._proof_operation_deadline = ContextVar("private-deadline", default=None)
    runtime.cancellation_projector = projector
    runtime.binding_wait_seconds = 0.05
    runtime.poll_interval_seconds = 0.005
    return runtime


@pytest.mark.asyncio
async def test_original_call_error_survives_pending_real_audit_without_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    database, run_id, projector = private_database(tmp_path / "primary.sqlite3")
    runtime = private_runtime(database, run_id, projector)
    release = threading.Event()
    calls: list[str] = []
    original_store = web_runtime.store_transport_observation

    def delayed_store(
        session: Session, value: web_runtime.ResearchTransportObservation,
    ) -> ObservedExecutionRef:
        calls.append("store")
        reference = original_store(session, value)
        assert release.wait(2)
        return reference

    async def binding() -> None:
        return None

    original = AgentExecutionError(
        "private_original_native_failure", "private body must not appear in diagnostics",
        retryable=True, cause_code="private_native_reason", provider_id="dsh-web", origin="dsh",
    )
    host_calls: list[str] = []

    async def call() -> DshSessionStatus:
        host_calls.append("call")
        raise original

    monkeypatch.setattr(web_runtime, "store_transport_observation", delayed_store)
    monkeypatch.setattr(runtime, "_assert_task_binding", binding)
    caplog.set_level(logging.INFO, logger=web_runtime.__name__)
    try:
        with pytest.raises(AgentExecutionError) as raised:
            await runtime._retry_host_call(
                call, deadline_at=datetime.now(UTC) + timedelta(seconds=2), operation="status",
            )
        assert raised.value is original and original.cause_code == "private_native_reason"
        assert host_calls == ["call"] and calls == ["store"]
        assert any("unconfirmed" in note for note in original.__notes__)
        # Outer error handling can observe the exact pending write, not start it again.
        with pytest.raises(AgentExecutionError):
            await runtime._record_transport(None, error=original)
        assert calls == ["store"]
        observations = runtime._observations.get()
        assert observations is not None and observations.latest is None
        handle = next(iter(observations.pending_transports.values()))
        release.set()
        await settled(handle)
        assert observations.latest is None  # Completion observer grants no Runtime state.
        reference = await runtime._record_transport(None, error=original)
        assert reference is not None and calls == ["store"]
        with database.session() as session:
            saved = read_transport_observation(session, reference)
            assert saved.error is not None and saved.error.cause_code == original.cause_code
        logs = [record.getMessage() for record in caplog.records]
        assert any('"storage_state": "confirmed"' in item for item in logs)
        assert all("private body" not in item for item in logs)
    finally:
        release.set()
        await projector.close()
        database.engine.dispose()


@pytest.mark.asyncio
async def test_transport_timeout_waits_for_same_audit_then_retries_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, run_id, projector = private_database(tmp_path / "transport-retry.sqlite3")
    runtime = private_runtime(database, run_id, projector)
    release = threading.Event()
    calls: list[str] = []
    original_store = web_runtime.store_transport_observation

    def delayed_store(
        session: Session, value: web_runtime.ResearchTransportObservation,
    ) -> ObservedExecutionRef:
        calls.append(value.phase)
        reference = original_store(session, value)
        if value.phase == "error":
            assert release.wait(2)
        return reference

    async def binding() -> None:
        return None

    attempts = 0

    async def call() -> DshSessionStatus:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise AgentExecutionError(
                "provider_timeout",
                "controlled Host status timeout",
                retryable=True,
                cause_code="dsh_host_timeout",
                provider_id="dsh-web",
                origin="transport",
            )
        return DshSessionStatus(
            schema_version="dsh-session-status.v1",
            run_id=run_id,
            dsh_session_id="private-native",
            state="running",
            generation=1,
            last_seq=3,
            observed_at=datetime.now(UTC),
            error_code=None,
        )

    monkeypatch.setattr(web_runtime, "store_transport_observation", delayed_store)
    monkeypatch.setattr(runtime, "_assert_task_binding", binding)

    async def release_audit() -> None:
        await asyncio.sleep(0.08)
        release.set()

    release_task = asyncio.create_task(release_audit())
    try:
        reply = await runtime._retry_host_call(
            call,
            deadline_at=datetime.now(UTC) + timedelta(seconds=2),
            operation="status",
        )
        await release_task
        assert reply.state == "running"
        assert attempts == 2
        assert calls == ["error", "status"]
        observations = runtime._observations.get()
        assert observations is not None and observations.latest is not None
    finally:
        release.set()
        await asyncio.gather(release_task, return_exceptions=True)
        await projector.close()
        database.engine.dispose()


@pytest.mark.asyncio
async def test_raw_terminal_reason_is_unverified_and_pending_never_returns_ref(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    database, run_id, projector = private_database(tmp_path / "raw.sqlite3")
    runtime = private_runtime(database, run_id, projector)
    release = threading.Event()
    original_store = web_runtime.store_transport_observation

    def delayed_store(
        session: Session, value: web_runtime.ResearchTransportObservation,
    ) -> ObservedExecutionRef:
        reference = original_store(session, value)
        assert release.wait(2)
        return reference

    monkeypatch.setattr(web_runtime, "store_transport_observation", delayed_store)
    caplog.set_level(logging.INFO, logger=web_runtime.__name__)
    reply = DshSessionStatus(
        schema_version="dsh-session-status.v1", run_id=run_id, dsh_session_id="private-native",
        state="failed", generation=1, last_seq=12, observed_at=datetime.now(UTC),
        error_code="private_reported_native_failure",
    )
    try:
        with pytest.raises(AgentExecutionError) as raised:
            await runtime._record_transport(reply)
        assert raised.value.error_code == "research_observation_unavailable"
        observations = runtime._observations.get()
        assert observations is not None and observations.latest is None
        handle = next(iter(observations.pending_transports.values()))
        release.set()
        await settled(handle)
        assert observations.latest is None
        reference = await runtime._record_transport(reply)
        assert reference is not None
        records = [json.loads(record.getMessage().split(" ", 1)[1]) for record in caplog.records]
        assert records and all(
            item["native_error_authentication"] == "unverified" for item in records
        )
        assert all(item["reported_native_error_code"] == reply.error_code for item in records)
    finally:
        release.set()
        await projector.close()
        database.engine.dispose()

"""Original status/terminal replies under real SQLite contention; no network."""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
from sqlalchemy import event
from sqlalchemy.exc import OperationalError

from apps.hub_worker.composition import EvolutionWorker
from apps.hub_worker.main import _research_once_outcome  # pyright: ignore[reportPrivateUsage]
from apps.hub_worker.research import DurableResearchWorker
from packages.contracts_py.decision_hub_contracts import (
    DshSessionStatus,
    DshSessionSubmit,
    RunStatus,
)
from packages.kernel.decision_hub_kernel.application.dsh_sessions import DshSessionLinkService
from packages.kernel.decision_hub_kernel.application.live_observation import ServiceHeartbeatService
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    DshSessionLinkRecord,
    RunRecord,
)
from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError
from packages.runtime_adapters.dsh_runtime.web_runtime import DshWebResearchRuntime
from tests.dsh_native.test_dsh_web_runtime import (
    FakeHostClient,
    _database,  # pyright: ignore[reportPrivateUsage]
    _request,  # pyright: ignore[reportPrivateUsage]
)
from tests.research.test_full_method_v2_bundle import forbid_external_connections
from tests.research.test_research_worker_scheduler_busy import NoModelRuntime

pytestmark = pytest.mark.usefixtures("forbid_external_connections")
assert forbid_external_connections is not None
PACK = Path(__file__).resolve().parents[2] / "packs" / "crypto_macro"


def observe(name: str, payload: dict[str, Any]) -> None:
    if directory := os.environ.get("DECISION_HUB_STATUS_BUSY_EVIDENCE_DIR"):
        with (Path(directory) / f"{name}.json").open("x", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, default=str)


class Writer:
    def __init__(self, database: Database, *, minimum: float = 6.5):
        self.database, self.minimum = database, minimum
        self.ready, self.release = threading.Event(), threading.Event()
        self.errors: list[BaseException] = []
        self.seconds = 0.0
        self.thread = threading.Thread(target=self._hold, daemon=True)

    def _hold(self) -> None:
        try:
            with self.database.engine.connect() as connection:
                connection.exec_driver_sql("BEGIN IMMEDIATE")
                start = time.monotonic()
                self.ready.set()
                self.release.wait(timeout=8)
                remaining = self.minimum - (time.monotonic() - start)
                if remaining > 0:
                    time.sleep(remaining)
                connection.rollback()
                self.seconds = time.monotonic() - start
        except BaseException as error:
            self.errors.append(error)
            self.ready.set()

    def start(self) -> None:
        self.thread.start()
        assert self.ready.wait(timeout=2) and not self.errors

    def close(self) -> None:
        self.release.set()
        self.thread.join(timeout=8)
        assert not self.thread.is_alive() and not self.errors


@pytest.mark.asyncio
@pytest.mark.parametrize("original_statement_control", [True, False])
async def test_original_running_status_survives_initial_begin_busy_without_new_submission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, original_statement_control: bool,
) -> None:
    database = _database(tmp_path / "status.sqlite3")
    run_id, _ = RunService(database).create("event-original-status")
    writer = Writer(database)
    busy_statements: list[str] = []
    loop_ticks = 0

    def on_error(context: Any) -> None:
        if getattr(context.original_exception, "sqlite_errorcode", None) == 5:
            busy_statements.append(context.statement)

    event.listen(database.engine, "handle_error", on_error)

    class Host(FakeHostClient):
        @property
        def status_reads(self) -> int:
            return self._polls

        async def status(self, payload: DshSessionSubmit) -> DshSessionStatus:
            reply = await super().status(payload)
            if self._polls == 1:
                writer.start()
            return reply

    host = Host()
    runtime = DshWebResearchRuntime(DshSessionLinkService(database), host,
                                   poll_interval_seconds=.01)

    async def original_observe(status: DshSessionStatus, submit: DshSessionSubmit) -> None:
        # Exact pre-fix statement from the preserved fatal stack. This is a
        # regression control, not a second production implementation/receipt.
        runtime.links.observe(status)

    if original_statement_control:
        monkeypatch.setattr(runtime, "_observe_status", original_observe)

    async def clock_ticks() -> None:
        nonlocal loop_ticks
        while True:
            await asyncio.sleep(.1)
            loop_ticks += 1

    clock = asyncio.create_task(clock_ticks())
    try:
        if original_statement_control:
            with pytest.raises(OperationalError) as failure:
                await runtime.execute(_request(run_id))
            assert failure.value.statement == "BEGIN IMMEDIATE"
            assert host.status_reads == 1
        else:
            result = await runtime.execute(_request(run_id))
            assert result.status == "degraded"  # fixture has no synthesis, not AI acceptance
            assert host.status_reads == 2 and loop_ticks >= 40
            link = runtime.links.get(run_id)
            assert link is not None and link.state == "completed" and link.generation == 1
        assert len(host.submit_calls) == 1 and host.cancelled == []
        assert busy_statements == ["BEGIN IMMEDIATE"]
    finally:
        writer.close()
        clock.cancel()
        await asyncio.gather(clock, return_exceptions=True)
        await runtime.close()
        database.engine.dispose()
    observe(f"status-{'RED-original-statement' if original_statement_control else 'GREEN'}", {
        "original_statement_control": original_statement_control,
        "original_sqlite_errors": busy_statements, "actual_writer_seconds": writer.seconds,
        "host_submissions": len(host.submit_calls), "host_status_reads": host.status_reads,
        "provider_model_calls": 0, "acquisition_calls": 0,
        "event_loop_ticks": loop_ticks, "scope": "real SQLite; controlled Host, not live AI report",
    })


@pytest.mark.asyncio
async def test_failure_writer_busy_retains_original_error_and_next_tick_only_saves_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    database = _database(tmp_path / "failure.sqlite3")
    runtime = NoModelRuntime()
    worker = DurableResearchWorker(database, runtime, pack_root=PACK,
                                   terminal_projection_wait_seconds=.1)
    runs = RunService(database)
    run_id, _ = runs.create("event-original-failure", strategy_version="research.v1")
    assert runs.claim_for_execution(run_id, worker_id=worker.worker_id, lease_seconds=60)
    writer = Writer(database)
    factory_calls = 0

    def fail_factory(*args: Any, **kwargs: Any) -> Any:
        nonlocal factory_calls
        factory_calls += 1
        writer.start()
        raise AgentExecutionError("dsh_model_binding_unavailable", "Original provider failure")

    monkeypatch.setattr(worker.factory, "build", fail_factory)
    try:
        pending = await worker._execute_claim(  # pyright: ignore[reportPrivateUsage]
            "event-original-failure", run_id, recovered=False,
        )
        assert pending.status == "terminal_storage_pending"
        assert pending.error_code == "dsh_model_binding_unavailable"
        assert pending.retryable and pending.recorded_in_database is False
        before = database.get_run_record(run_id)
        assert before is not None and before.status == "running" and before.error_code is None
        diagnostic = json.loads(capsys.readouterr().out.strip())
        assert diagnostic["recorded_in_database"] is False
    finally:
        writer.close()
    saved = await worker.tick()
    assert saved is not None and saved.status == "failed" and saved.recorded_in_database is True
    assert saved.error_code == "dsh_model_binding_unavailable"
    after = database.get_run_record(run_id)
    assert after is not None and after.status == "failed"
    assert after.error_code == saved.error_code and after.lease_owner is None
    assert factory_calls == 1 and runtime.calls == 0
    observe("terminal-failure-GREEN", {
        "pending": pending.model_dump(), "saved": saved.model_dump(), "diagnostic": diagnostic,
        "factory_entries": factory_calls, "provider_model_calls": runtime.calls,
        "actual_writer_seconds": writer.seconds, "run_after": after.status,
    })
    database.engine.dispose()


@pytest.mark.parametrize("boundary", ["cancelled", "new_owner", "expired", "committed"])
def test_terminal_cas_keeps_fresh_owner_cancel_lease_and_commit_boundaries(
    tmp_path: Path, boundary: str,
) -> None:
    database = _database(tmp_path / "fence.sqlite3")
    now = datetime.now(UTC)
    runs = RunService(database, clock=lambda: now)
    run_id, _ = runs.create("event-fenced-failure")
    assert runs.claim_for_execution(run_id, worker_id="original-owner", lease_seconds=60)
    with database.session() as session:
        row = session.get(RunRecord, run_id)
        assert row is not None
        if boundary == "cancelled":
            row.status = RunStatus.cancelled.value
        elif boundary == "new_owner":
            row.lease_owner = "other-owner"
        elif boundary == "expired":
            row.lease_expires_at = now - timedelta(seconds=1)
        else:
            row.artifact_id = "original-committed-artifact"
        before = (row.status, row.lease_owner, row.artifact_id)
    assert not runs.fail_if_owned(run_id, "original-owner", error_code="late-error",
                                 require_current_lease=True)
    after = database.get_run_record(run_id)
    assert after is not None and (after.status, after.lease_owner, after.artifact_id) == before
    assert after.error_code is None
    database.engine.dispose()


@pytest.mark.asyncio
async def test_evolution_initial_heartbeat_busy_does_not_execute_business_or_crash(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    database = _database(tmp_path / "evolution.sqlite3")
    calls = 0

    class Executor:
        async def tick(self, instance_id: str) -> None:
            nonlocal calls
            calls += 1
            return None

    worker = EvolutionWorker(cast(Any, Executor()), ServiceHeartbeatService(database),
                             "original-evolution",
                             "replay", 10, datetime.now(UTC))
    worker._beat()  # pyright: ignore[reportPrivateUsage]
    writer = Writer(database)
    writer.start()
    try:
        report = await worker.tick()
        assert report is not None and report.status == "maintenance_pending"
        assert report.recorded_in_database is False and report.retryable
        assert calls == 0
        diagnostic = json.loads(capsys.readouterr().out.strip())
        assert diagnostic["role"] == "evolution" and diagnostic["recorded_in_database"] is False
    finally:
        writer.close()
    assert await worker.tick() is None and calls == 1
    observe("evolution-heartbeat-GREEN", {
        "pending": report.model_dump(), "diagnostic": diagnostic,
        "business_calls_after_release": calls, "actual_writer_seconds": writer.seconds,
    })
    database.engine.dispose()


def test_pending_failure_once_does_not_claim_completed_or_unstarted_research() -> None:
    code, text = _research_once_outcome("terminal_storage_pending")
    assert code == 2 and "失败回执尚未落库" in text and "不表示研究完成" in text
    assert "尚未开始" not in text


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_kind", ["uncertain_commit", "real_schema_error"])
async def test_non_initial_or_non_busy_status_failure_is_never_replayed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_kind: str,
) -> None:
    database = _database(tmp_path / "negative-status.sqlite3")
    run_id, _ = RunService(database).create("event-status-negative")
    links = DshSessionLinkService(database)
    host = FakeHostClient()
    runtime = DshWebResearchRuntime(links, host)
    attempts = 0

    def fail(*args: Any, **kwargs: Any) -> Any:
        nonlocal attempts
        attempts += 1
        if failure_kind == "real_schema_error":
            with database.session() as session:
                session.connection().exec_driver_sql("SELECT * FROM missing_status_test_table")
        else:
            original = sqlite3.OperationalError("controlled uncertain commit")
            original.sqlite_errorcode = sqlite3.SQLITE_BUSY
            raise OperationalError("COMMIT", (), original)

    monkeypatch.setattr(links, "observe", fail)
    try:
        with pytest.raises(OperationalError):
            await runtime.execute(_request(run_id))
        assert attempts == 1 and len(host.submit_calls) == 1
        assert host._polls == 1  # pyright: ignore[reportPrivateUsage]
    finally:
        await runtime.close()
        database.engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", [
    "cancelled", "new_owner", "expired", "committed", "generation", "request_hash",
])
async def test_status_writer_fresh_small_fence_rejects_changed_originals(
    tmp_path: Path, boundary: str,
) -> None:
    database = _database(tmp_path / "status-fence.sqlite3")
    run_id, _ = RunService(database).create("event-status-fence")
    assert RunService(database).claim_for_execution(
        run_id, worker_id="original-owner", lease_seconds=60,
    )
    links, host = DshSessionLinkService(database), FakeHostClient()
    runtime = DshWebResearchRuntime(links, host, poll_interval_seconds=.001)
    try:
        await runtime.execute(_request(run_id))
        submit = host.submit_calls[0]
        with database.session() as session:
            run = session.get(RunRecord, run_id)
            link = session.get(DshSessionLinkRecord, run_id)
            assert run is not None and link is not None
            if boundary == "cancelled":
                run.status = "cancelled"
            elif boundary == "new_owner":
                run.lease_owner = "other-owner"
            elif boundary == "expired":
                run.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
            elif boundary == "committed":
                run.artifact_id = "preserved-original-artifact"
            elif boundary == "generation":
                link.generation += 1
            else:
                link.request_hash = "e" * 64
        before = links.get(run_id)
        with pytest.raises(ValueError, match="dsh_status_"):
            links.observe(
                host.status_value(submit, "running", 20),
                expected_request_hash=submit.request_hash, expected_lease_owner="original-owner",
            )
        assert links.get(run_id) == before
    finally:
        await runtime.close()
        database.engine.dispose()


@pytest.mark.asyncio
async def test_evolution_final_heartbeat_busy_preserves_completed_business_return(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    database = _database(tmp_path / "evolution-final.sqlite3")
    writer = Writer(database)
    calls = 0

    class Executor:
        async def tick(self, instance_id: str) -> None:
            nonlocal calls
            calls += 1
            writer.start()
            return None

    worker = EvolutionWorker(cast(Any, Executor()), ServiceHeartbeatService(database),
                             "original-evolution-final", "replay", 10, datetime.now(UTC))
    try:
        assert await worker.tick() is None
        assert calls == 1
        diagnostic = json.loads(capsys.readouterr().out.strip())
        assert diagnostic["phase"] == "heartbeat_finish"
        assert diagnostic["recorded_in_database"] is False
    finally:
        writer.close()
        database.engine.dispose()

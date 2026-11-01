"""Real SQLite writer contention at the complete research-worker boundary."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
import threading
import time
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.exc import OperationalError

from apps.hub_worker.composition import ResearchWorker
from apps.hub_worker.main import _research_once_outcome  # pyright: ignore[reportPrivateUsage]
from apps.hub_worker.research import DurableResearchWorker, ResearchWorkerReport
from packages.contracts_py.decision_hub_contracts import (
    ObservationCreate,
    ResearchSessionRequest,
    ResearchSessionResult,
)
from packages.kernel.decision_hub_kernel.application.admission import AdmissionService
from packages.kernel.decision_hub_kernel.application.live_observation import ServiceHeartbeatService
from packages.kernel.decision_hub_kernel.application.run import RunClaim, RunService
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    EventRecord,
    ResearchResultRecord,
    ReviewScheduleRecord,
    RunRecord,
    ServiceHeartbeatRecord,
)
from packages.kernel.decision_hub_kernel.ports.research import ResearchTraceSink
from tests.research.test_review_schedule import (
    NOW,
    _follow_fixture_studies,  # pyright: ignore[reportPrivateUsage]
    _result,  # pyright: ignore[reportPrivateUsage]
)

PACK = Path(__file__).resolve().parents[2] / "packs/crypto_macro"


class NoModelRuntime:
    runtime_id = "scheduler-busy-offline-control"
    runtime_version = "scheduler-busy-offline-control.v1"
    profile_ref = "scheduler-busy-offline-control.v1"

    def __init__(self) -> None:
        self.calls = 0

    async def execute(
        self, request: ResearchSessionRequest, trace_sink: ResearchTraceSink | None = None,
    ) -> ResearchSessionResult:
        self.calls += 1
        raise AssertionError("storage maintenance cannot submit model research")

    async def close(self) -> None:
        return None


@pytest.fixture
def setup_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> Generator[tuple[Database, DurableResearchWorker, ResearchWorker, NoModelRuntime]]:
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "isolated-data"))
    monkeypatch.setenv("DECISION_HUB_LLM_ENABLED", "0")
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'isolated.sqlite3'}")
    database.create_all()
    runtime = NoModelRuntime()
    durable = DurableResearchWorker(
        database, runtime, pack_root=PACK, clock=lambda: NOW,
        execution_mode="replay", checkpoint_path=tmp_path / "checkpoint.sqlite3",
    )
    wrapper = ResearchWorker(
        worker=durable, heartbeats=ServiceHeartbeatService(database, clock=lambda: NOW),
        instance_id="scheduler-busy-test", mode="replay", heartbeat_interval_seconds=10,
        started_at=NOW,
    )
    # Prime the actual heartbeat before writer contention. Its retry performs
    # the same production UPDATE transaction, rather than a mocked beat.
    wrapper._beat()  # pyright: ignore[reportPrivateUsage]
    yield database, durable, wrapper, runtime
    database.engine.dispose()


@contextmanager
def held_writer(database: Database) -> Generator[dict[str, float]]:
    ready = threading.Event()
    release = threading.Event()
    failures: list[BaseException] = []
    duration: dict[str, float] = {}

    def hold() -> None:
        try:
            with database.engine.connect() as connection:
                connection.exec_driver_sql("BEGIN IMMEDIATE")
                began = time.monotonic()
                ready.set()
                release.wait(timeout=8)
                # Retain the physical writer for a measured 6.5 seconds, beyond
                # the unchanged 5-second timeout, even if the call fails early.
                remaining = 6.5 - (time.monotonic() - began)
                if remaining > 0:
                    time.sleep(remaining)
                connection.rollback()
                duration["seconds"] = time.monotonic() - began
        except BaseException as error:
            failures.append(error)
            ready.set()

    holder = threading.Thread(target=hold, daemon=True)
    holder.start()
    try:
        assert ready.wait(timeout=2) and not failures
        yield duration
    finally:
        release.set()
        holder.join(timeout=2)
        assert not holder.is_alive() and not failures


def observation(name: str, value: dict[str, Any]) -> None:
    if directory := os.getenv("DECISION_HUB_WORKER_BUSY_EVIDENCE_DIR"):
        with (Path(directory) / f"{name}.json").open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")


def business_rows(database: Database) -> dict[str, list[dict[str, Any]]]:
    tables = (
        "runs", "run_events", "research_trace_events", "run_role_outputs",
        "research_commands", "product_commands", "artifacts",
    )
    with database.engine.connect() as connection:
        return {
            table: [dict(row) for row in connection.exec_driver_sql(
                f"SELECT * FROM {table} ORDER BY 1"
            ).mappings()]
            for table in tables
        }


def test_complete_tick_retains_retryable_heartbeat_busy_and_next_tick_runs(
    setup_worker: tuple[Database, DurableResearchWorker, ResearchWorker, NoModelRuntime],
    capsys: pytest.CaptureFixture[str],
) -> None:
    database, _, wrapper, runtime = setup_worker
    before = business_rows(database)
    began = time.monotonic()
    with held_writer(database) as holding:
        report = asyncio.run(wrapper.tick())
        assert report is not None and report.status == "maintenance_pending"
        assert report.error_code == "research_heartbeat_storage_busy" and report.retryable
        assert report.run_id is None and report.artifact_id is None
        assert runtime.calls == 0
    after_busy = business_rows(database)
    assert before == after_busy
    assert asyncio.run(wrapper.tick()) is None
    assert runtime.calls == 0
    diagnostic = json.loads(capsys.readouterr().out.strip())
    assert diagnostic["phase"] == "heartbeat_start"
    assert diagnostic["recorded_in_database"] is False
    assert diagnostic["retryable"] is True
    with database.session() as session:
        heartbeat = session.get(ServiceHeartbeatRecord, wrapper.service_id)
        assert heartbeat is not None and heartbeat.last_error_code is None
    observation("complete-heartbeat-busy", {
        "report": report.model_dump(), "diagnostic": diagnostic,
        "business_unchanged": before == after_busy, "runtime_calls": runtime.calls,
        "next_tick": "no_run", "seconds": time.monotonic() - began,
        "actual_holder_seconds": holding["seconds"],
        "scope": "actual complete wrapper and native UPDATE contention, no Provider",
    })


def test_actual_scheduler_begin_busy_retains_worker_then_discovers_future_plans(
    setup_worker: tuple[Database, DurableResearchWorker, ResearchWorker, NoModelRuntime],
) -> None:
    database, durable, _, runtime = setup_worker
    result = _result()
    result = result.model_copy(update={"horizons": [
        horizon.model_copy(update={"next_review_at": NOW + timedelta(minutes=20)})
        for horizon in result.horizons
    ]})
    payload = result.model_dump_json()
    with database.session() as session:
        session.add(EventRecord(
            event_id="scheduler-busy-event", event_type="crypto_market_snapshot",
            occurred_at=NOW - timedelta(hours=1), received_at=NOW - timedelta(hours=1),
        ))
        session.add(RunRecord(
            run_id="scheduler-busy-parent", event_id="scheduler-busy-event",
            status="completed", strategy_version="research.v1", runtime_version="fixture.v1",
            admission_origin="manual", priority=100, created_at=NOW - timedelta(hours=1),
            updated_at=NOW - timedelta(minutes=5), finished_at=NOW - timedelta(minutes=5),
        ))
        session.add(ResearchResultRecord(
            run_id="scheduler-busy-parent", schema_version=result.schema_version,
            payload_json=payload, payload_hash=hashlib.sha256(payload.encode()).hexdigest(),
            created_at=NOW - timedelta(minutes=5),
        ))
    _follow_fixture_studies(database)
    before = business_rows(database)
    began = time.monotonic()
    with held_writer(database) as holding:
        report = asyncio.run(durable.tick())
        assert report is not None and report.status == "maintenance_pending"
        assert report.error_code == "research_scheduler_storage_busy" and report.retryable
        assert report.run_id is None and runtime.calls == 0
    after_busy = business_rows(database)
    assert before == after_busy
    assert asyncio.run(durable.tick()) is None
    assert runtime.calls == 0
    with database.session() as session:
        plans = session.query(ReviewScheduleRecord).all()
        assert len(plans) == 3 and all(plan.status == "planned" for plan in plans)
        assert session.query(RunRecord).count() == 1
    observation("scheduler-native-busy", {
        "report": report.model_dump(), "business_unchanged": before == after_busy,
        "future_plans_after_release": len(plans), "runtime_calls": runtime.calls,
        "seconds": time.monotonic() - began,
        "actual_holder_seconds": holding["seconds"],
        "scope": "original plan_due with valid saved report, no mocked busy exception",
    })


def test_final_heartbeat_busy_preserves_original_outcome_without_reexecuting(
    setup_worker: tuple[Database, DurableResearchWorker, ResearchWorker, NoModelRuntime],
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    database, durable, wrapper, runtime = setup_worker
    original = ResearchWorkerReport(status="degraded", run_id="controlled-original-outcome")
    entered = 0
    holding = held_writer(database)

    async def controlled_outcome() -> ResearchWorkerReport | None:
        nonlocal entered
        entered += 1
        if entered == 1:
            holding.__enter__()
            return original
        return None

    monkeypatch.setattr(durable, "tick", controlled_outcome)
    try:
        assert asyncio.run(wrapper.tick()) is original
    finally:
        holding.__exit__(None, None, None)
    assert entered == 1 and runtime.calls == 0
    assert asyncio.run(wrapper.tick()) is None
    assert entered == 2 and runtime.calls == 0
    diagnostic = json.loads(capsys.readouterr().out.strip())
    assert diagnostic["phase"] == "heartbeat_finish"
    assert diagnostic["recorded_in_database"] is False
    observation("final-heartbeat-busy", {
        "original": original.model_dump(), "identity_preserved": True,
        "diagnostic": diagnostic, "runtime_calls": runtime.calls,
        "controlled_tick_count": entered,
        "scope": "controlled outcome preservation and actual heartbeat SQL contention",
    })


@pytest.mark.parametrize("failure", [
    PermissionError("research_original_binding_denied"),
    ValueError("research_scope_not_enabled"),
    RuntimeError("scheduler_unrecognized_failure"),
])
def test_nonstorage_scheduler_errors_propagate(
    setup_worker: tuple[Database, DurableResearchWorker, ResearchWorker, NoModelRuntime],
    monkeypatch: pytest.MonkeyPatch, failure: Exception,
) -> None:
    _, durable, _, runtime = setup_worker

    def fail(*, now: datetime) -> None:
        raise failure

    monkeypatch.setattr(durable.review_schedule, "plan_due", fail)
    with pytest.raises(type(failure)) as seen:
        asyncio.run(durable.tick())
    assert seen.value is failure and runtime.calls == 0


def test_real_sql_schema_failure_propagates_at_scheduler_and_heartbeat(
    setup_worker: tuple[Database, DurableResearchWorker, ResearchWorker, NoModelRuntime],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, durable, wrapper, runtime = setup_worker

    def invalid_schema(*args: object, **kwargs: object) -> None:
        with database.session() as session:
            session.connection().exec_driver_sql("SELECT * FROM missing_busy_test_table")

    monkeypatch.setattr(durable.review_schedule, "plan_due", invalid_schema)
    with pytest.raises(OperationalError) as scheduler_error:
        asyncio.run(durable.tick())
    assert getattr(scheduler_error.value.orig, "sqlite_errorcode", None) == 1
    monkeypatch.setattr(wrapper, "_beat", invalid_schema)
    with pytest.raises(OperationalError) as heartbeat_error:
        asyncio.run(wrapper.tick())
    assert getattr(heartbeat_error.value.orig, "sqlite_errorcode", None) == 1
    assert runtime.calls == 0


def test_complete_tick_claim_cas_busy_keeps_run_unclaimed_then_enters_execution_once(
    setup_worker: tuple[Database, DurableResearchWorker, ResearchWorker, NoModelRuntime],
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    database, durable, wrapper, runtime = setup_worker
    event_id, _, _ = AdmissionService(database, clock=lambda: NOW).admit(
        ObservationCreate(text="BTC 独立市场证据研究", event_hint="crypto_market_snapshot")
    )
    run_id, _ = RunService(database, clock=lambda: NOW).create(
        event_id, "isolated-worker-claim-busy", strategy_version="research.v1",
    )
    before = business_rows(database)
    original_claim = durable.runs.claim_next
    duration: dict[str, float] = {}
    executed: list[str] = []

    def race_at_cas(**kwargs: Any) -> RunClaim | None:
        nonlocal duration
        with held_writer(database) as duration:
            return original_claim(**kwargs)

    async def controlled_execution(
        event_id: str, selected_run: str, *, recovered: bool,
    ) -> ResearchWorkerReport:
        executed.append(selected_run)
        assert selected_run == run_id and not recovered
        current = database.get_run_record(selected_run)
        assert current is not None and current.lease_owner == durable.worker_id
        # This is a controlled execution-entry observation, never a report or
        # Provider-success fixture. No model is submitted after the real claim.
        return ResearchWorkerReport(status="preparation_pending", run_id=selected_run)

    monkeypatch.setattr(durable.runs, "claim_next", race_at_cas)
    monkeypatch.setattr(durable, "_execute_claim", controlled_execution)
    report = asyncio.run(wrapper.tick())
    assert report is not None and report.status == "maintenance_pending"
    assert report.error_code == "research_claim_storage_busy" and report.retryable
    assert report.run_id is None and executed == [] and runtime.calls == 0
    assert business_rows(database) == before
    diagnostic = json.loads(capsys.readouterr().out.strip())
    assert diagnostic["phase"] == "claim" and diagnostic["recorded_in_database"] is True
    monkeypatch.setattr(durable.runs, "claim_next", original_claim)
    recovered = asyncio.run(wrapper.tick())
    assert recovered is not None and recovered.status == "preparation_pending"
    assert executed == [run_id] and runtime.calls == 0
    observation("complete-claim-cas-busy", {
        "report": report.model_dump(), "diagnostic": diagnostic,
        "business_unchanged_after_busy": True, "actual_holder_seconds": duration["seconds"],
        "execution_entries_after_release": executed, "runtime_calls": runtime.calls,
        "scope": "actual CAS and wrapper; controlled execution entry is not Provider completion",
    })


@pytest.mark.parametrize("statement", [
    "COMMIT", "UPDATE unrelated_table SET status=?", "INSERT INTO runs(run_id) VALUES (?)",
])
def test_claim_busy_without_known_rolled_back_cas_propagates(
    setup_worker: tuple[Database, DurableResearchWorker, ResearchWorker, NoModelRuntime],
    monkeypatch: pytest.MonkeyPatch, statement: str,
) -> None:
    _, durable, _, runtime = setup_worker
    original = sqlite3.OperationalError("controlled SQLite BUSY negative classification")
    original.sqlite_errorcode = sqlite3.SQLITE_BUSY
    failure = OperationalError(statement, (), original)

    def fail(**kwargs: Any) -> RunClaim | None:
        raise failure

    monkeypatch.setattr(durable.runs, "claim_next", fail)
    with pytest.raises(OperationalError) as seen:
        asyncio.run(durable.tick())
    assert seen.value is failure and runtime.calls == 0


def test_maintenance_once_is_retryable_without_claiming_completed_research() -> None:
    code, phrase = _research_once_outcome("maintenance_pending")
    assert code == 2 and "可重试" in phrase and "尚未开始新研究" in phrase
    assert "完成" not in phrase

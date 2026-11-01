"""Pending MR12 controls: private SQLite and scheduler/sink only, no live work."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from queue import Full
from typing import Any, cast

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError

from packages.kernel.decision_hub_kernel.application import realtime_channel_diagnostics as diag
from packages.kernel.decision_hub_kernel.application.scheduler import RealtimeScheduler

ROOT = Path(__file__).resolve().parents[2]


def test_actual_sqlite_code_statement_projection_and_no_private_exception_text(tmp_path: Path) -> None:
    path = tmp_path / "private.sqlite3"
    engine = create_engine(f"sqlite+pysqlite:///{path.as_posix()}", connect_args={"timeout": 0})
    rows: list[dict[str, Any]] = []
    observer = diag.RealtimeChannelDiagnostics(root=ROOT, sink=rows.append)
    with sqlite3.connect(path) as owner:
        owner.execute("BEGIN IMMEDIATE")
        with engine.connect() as connection:
            with pytest.raises(OperationalError) as caught:
                connection.exec_driver_sql("BEGIN IMMEDIATE")
            observer("risk_shadow", caught.value, 1.0, 120.0, False)
        owner.rollback()
    busy = rows[0]
    assert busy["sqlite_errorcode"] == sqlite3.SQLITE_BUSY
    assert busy["sqlite_errorname"] == "SQLITE_BUSY"
    assert busy["statement_first_keyword"] == "BEGIN"
    assert busy["orig_module"] == "sqlite3"
    assert busy["channel_timeout_expired"] is False
    assert all(set(frame) == {"file", "function", "line"} for frame in busy["traceback"])
    observer("risk_shadow", None, 2.0, 120.0, False)
    assert rows[-1]["state"] == "recovered" and rows[-1]["episode_id"] == busy["episode_id"]
    with engine.connect() as connection:
        with pytest.raises(OperationalError) as caught:
            connection.exec_driver_sql("SELECT ? FROM missing_private_table", ("OWNER_PRIVATE_TOKEN",))
        observer("sources", caught.value, 2.0, 180.0, False)
    other = rows[-1]
    assert other["sqlite_errorcode"] == sqlite3.SQLITE_ERROR
    assert other["sqlite_errorname"] != "SQLITE_BUSY"
    assert other["statement_first_keyword"] == "SELECT"
    observer("calibrations", TimeoutError("PRIVATE_SOURCE_URL"), 3.0, 180.0, False)
    assert rows[-1]["channel_timeout_expired"] is False
    raw = json.dumps(rows)
    assert "OWNER_PRIVATE_TOKEN" not in raw and "PRIVATE_SOURCE_URL" not in raw
    assert "missing_private_table" not in raw and "database is locked" not in raw
    assert len(other["message_sha256"]) == 64 and other["commit_verified"] is False
    engine.dispose()


@pytest.mark.asyncio
async def test_existing_channel_failure_recovery_and_observer_failure_do_not_change_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records: list[dict[str, Any]] = []
    observer = diag.RealtimeChannelDiagnostics(root=ROOT, sink=records.append)
    scheduler = RealtimeScheduler(cast(Any, None), channel_observer=observer)
    completed = asyncio.Event()
    calls = 0

    async def quiet() -> int:
        return 0

    async def action() -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ValueError("research_policy_snapshot_changed")
        completed.set()
        return 0

    for method in ("_poll_sources", "_discovery_recovery", "_windows", "_runs", "_outcomes",
                   "_observations", "_assessments", "_effect_tracking_requests", "_calibrations",
                   "_risk_acquisition", "_risk_evaluations", "_risk_research", "_risk_shadow",
                   "_risk_memory", "_risk_enhancements"):
        monkeypatch.setattr(scheduler, method, quiet)
    monkeypatch.setattr(scheduler, "_notifications", action)
    task = asyncio.create_task(scheduler.run_forever(0.001))
    await asyncio.wait_for(completed.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert [row["state"] for row in records] == ["first_failure", "recovered"]
    assert "notifications" not in scheduler.channel_errors
    assert calls >= 2
    # Optional callback failure does not stop the existing action/recovery path.
    def broken(*_args: Any) -> None:
        raise RuntimeError("private diagnostic failure")
    scheduler.channel_observer = broken
    completed.clear()
    calls = 0
    task = asyncio.create_task(scheduler.run_forever(0.001))
    await asyncio.wait_for(completed.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert calls >= 2 and "notifications" not in scheduler.channel_errors


def test_actual_file_writer_visibility_trace_gap_and_file_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(diag, "MAX_FILE_BYTES", 8192)
    path = tmp_path / "channels.jsonl"
    sink = diag._FileSink(path)  # pyright: ignore[reportPrivateUsage]
    row: dict[str, Any] = {"schema_version": "realtime-channel-diagnostics.v1",
        "state": "first_failure", "traceback": [{"file": "tests/kernel/control.py",
            "function": "controlled", "line": index} for index in range(100)],
        "traceback_complete": True, "traceback_dropped": 0, "traceback_sha256": "a" * 64}
    assert sink(row) is True
    sink.queue.join()  # Test-only wait; business code never joins the writer.
    first = json.loads(path.read_text("utf-8").splitlines()[0])
    assert first["traceback_complete"] is False and first["traceback_dropped"] > 0
    assert first["traceback_sha256"] == row["traceback_sha256"]
    def full_queue(_raw: bytes) -> None:
        raise Full
    dropped = sink.dropped
    with monkeypatch.context() as scoped:
        scoped.setattr(sink.queue, "put_nowait", full_queue)
        assert sink(row) is False and sink.dropped == dropped + 1
    for _ in range(30):
        sink(row)
    sink.queue.join()
    records = [json.loads(line) for line in path.read_text("utf-8").splitlines()]
    assert any(item["state"] == "diagnostic_gap" for item in records)
    assert sink.failed and path.stat().st_size <= 8192
    assert sink(row) is False  # failure is diagnostic only, never business authority

"""Real SQLite locks at the raw observation writer, without a Provider."""
from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from time import monotonic

import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from packages.kernel.decision_hub_kernel.application.dsh_sessions import DshSessionLinkService
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.persistence.db import Database, RunRecord
from packages.runtime_adapters.dsh_runtime.cancellation_projection import DshCancellationProjector


@pytest.mark.asyncio
async def test_transient_writer_waits_before_callback_and_commits_once(tmp_path: Path) -> None:
    path = tmp_path / "transient.sqlite3"
    database = Database(f"sqlite+pysqlite:///{path}")
    database.create_all()
    run_id, _ = RunService(database).create("observation-writer-transient")
    projector = DshCancellationProjector(DshSessionLinkService(database))
    writer = sqlite3.connect(path)
    writer.execute("BEGIN IMMEDIATE")
    calls: list[str] = []

    def save(session: Session) -> str:
        calls.append("save")
        row = session.get(RunRecord, run_id)
        assert row is not None
        row.error_code = "isolated_test_receipt"
        return run_id

    async def release() -> None:
        await asyncio.sleep(0.15)
        assert calls == []
        writer.rollback()

    try:
        # The former 50 ms BEGIN genuinely fails against this same held writer.
        assert projector.sessions is not None
        with projector.sessions.session() as session:
            with pytest.raises(OperationalError):
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        release_task = asyncio.create_task(release())
        result = await projector.write(save, timeout_seconds=1)
        await release_task
        assert result.state == "ready" and result.value == run_id
        assert calls == ["save"]
        with database.session() as session:
            row = session.get(RunRecord, run_id)
            assert row is not None and row.error_code == "isolated_test_receipt"
    finally:
        writer.rollback()
        writer.close()
        await projector.close()
        database.engine.dispose()


@pytest.mark.asyncio
async def test_permanent_writer_is_bounded_without_starting_callback(tmp_path: Path) -> None:
    path = tmp_path / "permanent.sqlite3"
    database = Database(f"sqlite+pysqlite:///{path}")
    database.create_all()
    projector = DshCancellationProjector(DshSessionLinkService(database))
    writer = sqlite3.connect(path)
    writer.execute("BEGIN IMMEDIATE")
    calls: list[str] = []

    def save(session: Session) -> str:
        calls.append("unexpected")
        return "unexpected"

    try:
        started = monotonic()
        result = await projector.write(save, timeout_seconds=0.12)
        assert monotonic() - started < 0.6
        assert result.state == "pending" and result.value is None
        assert result.limitation in {
            "cancellation_projection_db_busy", "cancellation_projection_wait_elapsed",
        }
        assert calls == []
    finally:
        writer.rollback()
        writer.close()
        await projector.close()
        database.engine.dispose()


@pytest.mark.asyncio
async def test_callback_failure_is_not_replayed_and_is_rolled_back(tmp_path: Path) -> None:
    path = tmp_path / "rollback.sqlite3"
    database = Database(f"sqlite+pysqlite:///{path}")
    database.create_all()
    run_id, _ = RunService(database).create("observation-writer-rollback")
    projector = DshCancellationProjector(DshSessionLinkService(database))
    calls: list[str] = []

    def save(session: Session) -> str:
        calls.append("save")
        row = session.get(RunRecord, run_id)
        assert row is not None
        row.error_code = "must_rollback"
        session.flush()
        raise ValueError("isolated_callback_failure")

    try:
        result = await projector.write(save, timeout_seconds=1)
        assert result.state == "invalid" and result.limitation == "isolated_callback_failure"
        assert calls == ["save"]
        with database.session() as session:
            row = session.get(RunRecord, run_id)
            assert row is not None and row.error_code is None
    finally:
        await projector.close()
        database.engine.dispose()

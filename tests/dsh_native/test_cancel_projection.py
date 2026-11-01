"""Real file-SQLite cancellation boundaries; no service, SDK, or Provider."""
# pyright: reportPrivateUsage=false
from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import event

from packages.contracts_py.decision_hub_contracts import (
    DshSessionCompletion,
    DshSessionStatus,
    DshSessionSubmit,
    DshUpstreamIdentity,
)
from packages.kernel.decision_hub_kernel.application.dsh_sessions import DshSessionLinkService
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.persistence.db import Database, RunRecord
from packages.runtime_adapters.dsh_runtime.web_runtime import DshWebResearchRuntime

NOW = datetime(2026, 10, 1, 3, tzinfo=UTC)


def identity() -> DshUpstreamIdentity:
    return DshUpstreamIdentity(
        source_commit="c" * 40, source_version="0.1.2-alpha.2",
        package_versions={"@deepseek-ai/dsh": "0.1.2-alpha.2"}, plugin_build_hash="b" * 64,
    )


def fixture(path: Path) -> tuple[Database, DshSessionLinkService, DshSessionSubmit]:
    db = Database(f"sqlite+pysqlite:///{path}")
    db.create_all()
    run_id, _ = RunService(db, clock=lambda: NOW).create("event-projection")
    session_id, request_id = DshSessionLinkService.deterministic_ids(run_id, "a" * 64)
    submit = DshSessionSubmit(
        schema_version="dsh-session-submit.v1", run_id=run_id, request_hash="a" * 64,
        deterministic_session_id=session_id, deterministic_request_id=request_id,
        workspace_ref="decision-hub://workspace/default", prompt_ref=f"hub://{run_id}/prompt",
        agent_preset="decision-research", permission_ref="decision-hub://research-only",
        deadline_at=NOW + timedelta(minutes=3), model_step_timeout_ms=60000,
        max_tool_calls=12, generation=1,
    )
    links = DshSessionLinkService(db, clock=lambda: NOW)
    links.reserve(submit, identity())
    with db.session() as session:
        run = session.get(RunRecord, run_id)
        assert run is not None
        run.status = "cancelled"
    return db, links, submit


def completion(submit: DshSessionSubmit, state: str = "cancelled") -> DshSessionCompletion:
    return DshSessionCompletion.model_validate({
        "schema_version": "dsh-session-completion.v1", "run_id": submit.run_id,
        "dsh_session_id": submit.deterministic_session_id, "terminal_status": state,
        "generation": submit.generation, "last_seq": 3, "trace_ref": None,
        "result_ref": f"hub://{submit.run_id}/result" if state == "completed" else None,
        "result_hash": "d" * 64 if state == "completed" else None,
        "completed_at": NOW, "error": None,
    })


class CancelHost:
    async def cancel(self, payload: DshSessionSubmit, reason: str) -> DshSessionStatus:
        return DshSessionStatus(
            schema_version="dsh-session-status.v1", run_id=payload.run_id,
            dsh_session_id=payload.deterministic_session_id, state="cancelled",
            generation=payload.generation, last_seq=3, observed_at=NOW, error_code=None,
        )

    async def close(self) -> None:
        pass


@pytest.mark.asyncio
async def test_locked_projection_does_not_block_event_loop(tmp_path: Path) -> None:
    path = tmp_path / "locked.db"
    db, links, submit = fixture(path)
    # Shorten only this legacy fixture connection: the old synchronous path is
    # still demonstrably blocking, without spending five seconds on the red.
    with db.engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA busy_timeout=250")
    runtime = DshWebResearchRuntime(links, CancelHost())  # type: ignore[arg-type]
    locked = sqlite3.connect(path)
    locked.execute("BEGIN IMMEDIATE")
    started = time.monotonic()

    async def observer() -> float:
        await asyncio.sleep(0.02)
        return time.monotonic() - started

    observer_task = asyncio.create_task(observer())
    await asyncio.sleep(0)
    try:
        await runtime._cancel_best_effort(submit, "product_run_cancelled")
        observed_after = await observer_task
    finally:
        locked.rollback()
        locked.close()
        await runtime.close()
        db.engine.dispose()
    assert observed_after < 0.15, f"event loop blocked for {observed_after:.3f}s"


def test_completion_and_generation_switch_are_one_atomic_boundary(tmp_path: Path) -> None:
    db, links, submit = fixture(tmp_path / "race.db")
    old_read = threading.Event()
    release_old = threading.Event()
    advancing_finished = threading.Event()

    def pause_old_read(
        _connection: Any, _cursor: Any, statement: str, _parameters: Any,
        _context: Any, _executemany: bool,
    ) -> None:
        if (
            threading.current_thread().name.startswith("old-completion")
            and statement.startswith("SELECT") and "dsh_session_links" in statement
        ):
            old_read.set()
            assert release_old.wait(3)

    event.listen(db.engine, "after_cursor_execute", pause_old_read)
    next_session, next_request = DshSessionLinkService.deterministic_ids(
        submit.run_id, submit.request_hash, 2,
    )
    next_submit = submit.model_copy(update={
        "generation": 2, "deterministic_session_id": next_session,
        "deterministic_request_id": next_request,
    })

    def advance() -> str:
        try:
            links.complete(completion(submit, "completed"))
            links.reserve(next_submit, identity())
            return "advanced"
        except ValueError as exc:
            assert str(exc) == "dsh_terminal_conflict"
            return "terminal_conflict"
        finally:
            advancing_finished.set()

    try:
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="old-completion") as old_pool:
            with ThreadPoolExecutor(max_workers=1, thread_name_prefix="new-generation") as new_pool:
                old_future = old_pool.submit(links.complete, completion(submit))
                assert old_read.wait(2)
                new_future = new_pool.submit(advance)
                advancing_finished.wait(0.2)
                release_old.set()
                old_future.result(timeout=3)
                outcome = new_future.result(timeout=3)
        final = links.get(submit.run_id)
        assert final is not None
        assert not (final.generation == 2 and final.state == "cancelled"), outcome
        assert (final.generation, final.state, outcome) in {
            (1, "cancelled", "terminal_conflict"), (2, "admitted", "advanced"),
        }
    finally:
        release_old.set()
        event.remove(db.engine, "after_cursor_execute", pause_old_read)
        db.engine.dispose()

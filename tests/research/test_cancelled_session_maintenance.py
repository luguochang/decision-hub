# pyright: reportPrivateUsage=false
"""Production maintenance lifecycle; control ports never execute model research."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from apps.hub_worker import main
from apps.hub_worker.cancelled_sessions import (
    cancelled_session_recovery,
    recover_cancelled_sessions_once,
    run_cancelled_session_recovery,
)
from apps.hub_worker.composition import ResearchWorker, build_research_worker
from packages.contracts_py.decision_hub_contracts import (
    ResearchSessionRequest,
    ResearchSessionResult,
)
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.pilot_runtime import PilotSettings
from packages.source_adapters.registry import SourceRegistry


class NoModelRuntime:
    runtime_id = "controlled-maintenance"
    runtime_version = "controlled.v1"
    profile_ref = "controlled-maintenance.v1"

    def __init__(self) -> None:
        self.closed = False

    async def execute(
        self, request: ResearchSessionRequest, trace_sink: object = None,
    ) -> ResearchSessionResult:
        raise AssertionError("maintenance must not submit model research")

    async def close(self) -> None:
        self.closed = True


class RecoveryRuntime(NoModelRuntime):
    def __init__(self, *, fail_once: bool = False) -> None:
        super().__init__()
        self.calls = 0
        self.fail_once = fail_once
        self.entered = asyncio.Event()

    async def reconcile_cancelled_runs(
        self, *, limit: int = 4, time_budget_seconds: float = 2,
    ) -> dict[str, int]:
        assert limit == 4 and time_budget_seconds > 0
        self.calls += 1
        if self.fail_once and self.calls == 1:
            raise ValueError("controlled-secret-url-that-must-not-be-logged")
        self.entered.set()
        return {"scanned": 0, "attempted": 0, "confirmed": 0, "pending": 0,
                "skipped": 0, "backoff": 0}


def test_optional_recovery_does_not_add_research_to_replay_ports() -> None:
    assert cancelled_session_recovery(NoModelRuntime()) is None
    runtime = RecoveryRuntime()
    assert cancelled_session_recovery(runtime) is runtime


async def test_maintenance_retries_failure_without_exposing_host_message(
    caplog: pytest.LogCaptureFixture,
) -> None:
    runtime = RecoveryRuntime(fail_once=True)
    with caplog.at_level(logging.WARNING):
        task = asyncio.create_task(run_cancelled_session_recovery(runtime, interval_seconds=0.01))
        try:
            await asyncio.wait_for(runtime.entered.wait(), timeout=1)
            assert runtime.calls >= 2
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    assert "error_class=ValueError" in caplog.text
    assert "controlled-secret-url" not in caplog.text
    assert not runtime.closed  # The owning research lifecycle closes the port.


async def test_outer_timeout_does_not_claim_the_host_or_run_was_cancelled() -> None:
    class HeldRecovery(RecoveryRuntime):
        async def reconcile_cancelled_runs(
            self, *, limit: int = 4, time_budget_seconds: float = 2,
        ) -> dict[str, int]:
            await asyncio.Event().wait()
            raise AssertionError("held cooperative await should time out")

    runtime = HeldRecovery()
    result = await recover_cancelled_sessions_once(runtime, time_budget_seconds=0.01)
    assert result is None
    assert not runtime.closed


@pytest.mark.parametrize("priority", [None, 100])
async def test_real_research_once_wires_general_lane_recovery_and_closes_port(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, priority: int | None,
) -> None:
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "runtime"))
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'maintenance.sqlite3'}")
    database.create_all()
    runtime = RecoveryRuntime()
    worker = build_research_worker(
        database, runtime=runtime, allowed_capabilities=("replay.research",)
    )
    worker.worker.min_priority = priority

    def selected_worker(*_args: object, **_kwargs: object) -> ResearchWorker:
        return worker

    monkeypatch.setattr(main, "build_research_worker", selected_worker)
    result = await main._run_loop(
        database=database, registry=SourceRegistry(), settings=PilotSettings(),
        role="research", interval=1, once=True,
    )
    assert result == 0
    assert runtime.calls == (1 if priority is None else 0)
    assert runtime.closed
    assert not any(task.get_name() == "research-cancel-recovery" for task in asyncio.all_tasks())


async def test_production_loop_maintains_while_claim_tick_is_held_and_cleans_tasks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "runtime"))
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'maintenance-held.sqlite3'}")
    database.create_all()
    runtime = RecoveryRuntime()
    worker = build_research_worker(
        database, runtime=runtime, allowed_capabilities=("replay.research",)
    )
    tick_entered = asyncio.Event()
    usage_cancelled = asyncio.Event()

    async def held_tick() -> None:
        tick_entered.set()
        await asyncio.Event().wait()

    async def held_usage() -> None:
        try:
            await asyncio.Event().wait()
        finally:
            usage_cancelled.set()

    monkeypatch.setattr(worker, "tick", held_tick)

    def selected_worker(*_args: object, **_kwargs: object) -> ResearchWorker:
        return worker

    def selected_usage(*_args: object) -> SimpleNamespace:
        return SimpleNamespace(run_forever=held_usage)

    monkeypatch.setattr(main, "build_research_worker", selected_worker)
    monkeypatch.setattr(main, "UsageCollector", selected_usage)
    task = asyncio.create_task(main._run_loop(
        database=database, registry=SourceRegistry(), settings=PilotSettings(),
        role="research", interval=1, once=False,
    ))
    try:
        await asyncio.wait_for(tick_entered.wait(), timeout=1)
        await asyncio.wait_for(runtime.entered.wait(), timeout=1)
        assert not task.done() and runtime.calls == 1
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert usage_cancelled.is_set() and runtime.closed
    assert not any(item.get_name() == "research-cancel-recovery" for item in asyncio.all_tasks())

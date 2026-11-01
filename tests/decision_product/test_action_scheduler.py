"""Async ownership tests plus real normal composition in private databases."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import cast

import pytest

from apps.hub_worker.action_tick import ActionMonitorTick
from apps.hub_worker.composition import build_realtime_worker
from packages.kernel.decision_hub_kernel.application.action_monitor import ActionMonitorService
from packages.kernel.decision_hub_kernel.application.realtime_channel_diagnostics import (
    RealtimeChannelDiagnostics,
)
from packages.kernel.decision_hub_kernel.application.scheduler import RealtimeScheduler
from packages.kernel.decision_hub_kernel.application.source_ingest import SourceIngestionService
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.pilot_runtime import PilotSettings
from packages.runtime_adapters.fake_runtime.runtime import FakeAgentRuntime
from packages.source_adapters.registry import SourceRegistry


class FiniteService:
    def __init__(self):
        self.calls = []

    def process_due(self, *, max_opportunities, max_scopes, checkpoint):
        self.calls.append((max_opportunities, max_scopes))
        checkpoint()
        return 3


@pytest.mark.asyncio
async def test_tick_uses_one_bounded_batch():
    service = FiniteService()
    tick = ActionMonitorTick(cast(ActionMonitorService, service))
    assert await tick.tick() == 3
    assert service.calls == [(4, 2)]


@pytest.mark.asyncio
async def test_inner_budget_is_cooperative_and_surfaces_named_timeout():
    class SlowService(FiniteService):
        def process_due(self, **kwargs):
            threading.Event().wait(0.025)
            return super().process_due(**kwargs)

    service = SlowService()
    tick = ActionMonitorTick(cast(ActionMonitorService, service), budget_seconds=0.005)
    with pytest.raises(TimeoutError, match="^action_monitor_tick_budget_exhausted$"):
        await tick.tick()
    assert service.calls == [(4, 2)]


@pytest.mark.asyncio
async def test_repeated_cancellation_joins_owner_before_concurrent_next_tick():
    entered = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()

    class HeldService(FiniteService):
        active = 0
        maximum_active = 0
        finished = 0

        def process_due(self, **kwargs):
            self.active += 1
            self.maximum_active = max(self.maximum_active, self.active)
            try:
                if not self.calls:
                    self.calls.append((kwargs["max_opportunities"], kwargs["max_scopes"]))
                    loop.call_soon_threadsafe(entered.set)
                    assert release.wait(2)
                    kwargs["checkpoint"]()
                    raise AssertionError("cancelled checkpoint must reject")
                return super().process_due(**kwargs)
            finally:
                self.active -= 1
                self.finished += 1

    service = HeldService()
    tick = ActionMonitorTick(cast(ActionMonitorService, service))
    first = asyncio.create_task(tick.tick())
    await asyncio.wait_for(entered.wait(), 1)
    first.cancel()
    await asyncio.sleep(0.01)
    second = asyncio.create_task(tick.tick())
    first.cancel()
    await asyncio.sleep(0.01)
    assert not first.done() and not second.done()
    assert service.active == 1 and len(service.calls) == 1
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert await asyncio.wait_for(second, 1) == 3
    assert service.maximum_active == 1 and service.finished == 2


@pytest.mark.asyncio
async def test_outer_async_timeout_waits_for_actual_thread_exit():
    entered = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()
    finished = threading.Event()

    class HeldService(FiniteService):
        def process_due(self, **kwargs):
            loop.call_soon_threadsafe(entered.set)
            try:
                assert release.wait(2)
                return super().process_due(**kwargs)
            finally:
                finished.set()

    service = HeldService()
    tick = ActionMonitorTick(cast(ActionMonitorService, service))

    async def bounded():
        async with asyncio.timeout(0.02):
            return await tick.tick()

    owner = asyncio.create_task(bounded())
    await asyncio.wait_for(entered.wait(), 1)
    await asyncio.sleep(0.04)
    assert not owner.done() and not finished.is_set()
    release.set()
    with pytest.raises(TimeoutError):
        await owner
    assert finished.is_set() and service.calls == [(4, 2)]


@pytest.fixture
def private_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("DECISION_HUB_LLM_ENABLED", "0")
    monkeypatch.setenv("DECISION_HUB_MARKET_ENHANCEMENTS_ENABLED", "0")
    monkeypatch.setenv("DECISION_HUB_RISK_ENABLED", "0")
    monkeypatch.setenv("DECISION_HUB_SPOT_RESEARCH_ENABLED", "0")
    monkeypatch.setenv("DECISION_HUB_ACTION_PRODUCT_ENABLED", "0")
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'scheduler.sqlite3'}")
    database.create_all()
    try:
        yield database
    finally:
        database.engine.dispose()


def test_disabled_normal_composition_has_no_action_monitor(private_db: Database):
    worker = build_realtime_worker(
        private_db,
        runtime=FakeAgentRuntime(),
        settings=PilotSettings(sources_enabled=False, market_enabled=False),
    )
    assert worker.scheduler.action_monitor_tick is None


def test_enabled_normal_composition_shares_existing_market_store(
    private_db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    for name in ("RISK", "SPOT_RESEARCH", "ACTION_PRODUCT"):
        monkeypatch.setenv(f"DECISION_HUB_{name}_ENABLED", "1")
    worker = build_realtime_worker(
        private_db,
        runtime=FakeAgentRuntime(),
        settings=PilotSettings(sources_enabled=False, market_enabled=False),
    )
    scheduler = worker.scheduler
    assert scheduler.action_monitor_tick is not None
    adapter = scheduler.action_monitor_tick.__self__
    assert isinstance(adapter, ActionMonitorTick)
    assert scheduler.risk_data is not None
    assert adapter.service.store is scheduler.risk_data.store
    assert adapter.service.store.archive is scheduler.risk_data.raw_archive
    assert adapter.service.feature_root == tmp_path / "data/action-product/features"


@pytest.mark.asyncio
async def test_scheduler_reports_action_work_and_keeps_one_continuous_channel(
    private_db: Database,
):
    reached = asyncio.Event()
    calls = []

    async def monitor():
        calls.append(1)
        reached.set()
        return 3

    scheduler = RealtimeScheduler(
        SourceIngestionService(private_db, SourceRegistry()), action_monitor_tick=monitor
    )
    report = await scheduler.tick()
    assert report.action_plans_evaluated == 3 and len(calls) == 1
    reached.clear()
    task = asyncio.create_task(scheduler.run_forever(0.01))
    try:
        await asyncio.wait_for(reached.wait(), 1)
        assert len([t for t in asyncio.all_tasks() if t.get_name() == "action_monitor"]) == 1
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_disabled_scheduler_has_no_action_channel(private_db: Database):
    scheduler = RealtimeScheduler(SourceIngestionService(private_db, SourceRegistry()))
    assert await scheduler._action_monitor() == 0
    task = asyncio.create_task(scheduler.run_forever(0.01))
    try:
        await asyncio.sleep(0.02)
        assert not [t for t in asyncio.all_tasks() if t.get_name() == "action_monitor"]
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def test_action_channel_failure_and_recovery_are_observable(tmp_path: Path):
    records = []
    diagnostics = RealtimeChannelDiagnostics(root=tmp_path, sink=records.append)
    diagnostics(
        "action_monitor", TimeoutError("action_monitor_tick_budget_exhausted"), 25, 30, False
    )
    diagnostics("action_monitor", None, 5, 30, False)
    assert [row["state"] for row in records] == ["first_failure", "recovered"]
    assert all(row["channel"] == "action_monitor" and row["commit_verified"] is False
               for row in records)

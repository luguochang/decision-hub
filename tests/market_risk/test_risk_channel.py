from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from packages.kernel.decision_hub_kernel.application.outbox import NotificationDispatcher
from packages.kernel.decision_hub_kernel.application.risk_monitor import RiskMonitorService
from packages.kernel.decision_hub_kernel.application.scheduler import RealtimeScheduler
from packages.kernel.decision_hub_kernel.application.source_ingest import SourceIngestionService
from packages.kernel.decision_hub_kernel.persistence.db import Database, OutboxRecord
from packages.kernel.decision_hub_kernel.persistence.risk_records import RiskAlertRecord
from packages.provider_adapters.notifications.local import LocalNotificationAdapter
from packages.source_adapters.registry import SourceRegistry
from tests.market_risk.test_risk_monitor import rule, sample


@pytest.mark.asyncio
async def test_hung_acquisition_and_deep_research_do_not_block_risk_or_notification(tmp_path: Path):
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'channels.sqlite3'}")
    database.create_all()
    entered_data, entered_ai, evaluated = asyncio.Event(), asyncio.Event(), asyncio.Event()
    now = datetime.now(UTC)

    class HangingData:
        async def refresh_due(self, *, deadline_at):
            entered_data.set()
            await asyncio.Event().wait()

    class CommittedSnapshots:
        async def snapshot(self, scope_id, *, as_of):
            assert scope_id == rule().scope_id
            evaluated.set()
            return sample(as_of)

    monitors = RiskMonitorService(database, CommittedSnapshots(), clock=lambda: now)
    monitors.ensure_plan(rule())
    ingestion = SourceIngestionService(database, SourceRegistry())
    scheduler = RealtimeScheduler(
        ingestion,
        risk_data=HangingData(),
        risk_monitor=monitors,
        notifications=NotificationDispatcher(
            database, {"local": LocalNotificationAdapter(tmp_path / "local.jsonl")}
        ),
    )

    async def hanging_runs(*_args):
        entered_ai.set()
        await asyncio.Event().wait()

    scheduler._runs = hanging_runs
    task = asyncio.create_task(scheduler.run_forever(0.02))
    try:
        await asyncio.wait_for(entered_data.wait(), 2)
        await asyncio.wait_for(entered_ai.wait(), 2)
        await asyncio.wait_for(evaluated.wait(), 2)
        async with asyncio.timeout(2):
            while not (tmp_path / "local.jsonl").exists():
                await asyncio.sleep(0.01)
        with database.session() as session:
            assert session.query(RiskAlertRecord).count() == 1
            assert session.query(OutboxRecord).one().sent_at is not None
        assert not task.done()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    database.engine.dispose()


@pytest.mark.asyncio
async def test_hung_enhancements_do_not_block_memory_risk_and_delivery(tmp_path: Path):
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'enhancement-channel.sqlite3'}")
    database.create_all()
    entered, memory_done = asyncio.Event(), asyncio.Event()
    now = datetime.now(UTC)

    class CommittedSnapshots:
        async def snapshot(self, scope_id, *, as_of):
            return sample(as_of)

    async def hanging_enhancements():
        entered.set()
        await asyncio.Event().wait()

    async def memory_tick():
        memory_done.set()
        return 1

    monitors = RiskMonitorService(database, CommittedSnapshots(), clock=lambda: now)
    monitors.ensure_plan(rule())
    scheduler = RealtimeScheduler(
        SourceIngestionService(database, SourceRegistry()),
        risk_monitor=monitors,
        risk_memory_tick=memory_tick,
        risk_enhancement_tick=hanging_enhancements,
        notifications=NotificationDispatcher(
            database,
            {"local": LocalNotificationAdapter(tmp_path / "delivered.jsonl")},
        ),
    )
    task = asyncio.create_task(scheduler.run_forever(0.02))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        await asyncio.wait_for(memory_done.wait(), 2)
        async with asyncio.timeout(2):
            while not (tmp_path / "delivered.jsonl").exists():
                await asyncio.sleep(0.01)
        with database.session() as session:
            assert session.query(RiskAlertRecord).count() == 1
            assert session.query(OutboxRecord).one().sent_at is not None
        assert not task.done()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        database.engine.dispose()

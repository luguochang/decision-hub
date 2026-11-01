import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from apps.hub_worker.composition import RealtimeWorker
from packages.kernel.decision_hub_kernel.application.live_observation import ServiceHeartbeatService
from packages.kernel.decision_hub_kernel.application.scheduler import RealtimeScheduler
from packages.kernel.decision_hub_kernel.application.source_ingest import SourceIngestionService
from packages.kernel.decision_hub_kernel.persistence.db import Database, SourceStateRecord
from packages.kernel.decision_hub_kernel.ports.sources import SourceManifest, SourcePollResult
from packages.source_adapters.registry import SourceRegistry


class HangingSource:
    manifest = SourceManifest(source_id="hanging", source_type="official_feed", version="test")

    def __init__(self):
        self.entered = asyncio.Event()

    async def poll(self, cursor=None):
        self.entered.set()
        await asyncio.Event().wait()


def setup(tmp_path: Path, timeout=45):
    db = Database(f"sqlite+pysqlite:///{tmp_path / 'channels.sqlite3'}")
    db.create_all()
    source = HangingSource()
    registry = SourceRegistry()
    registry.register(source)
    return db, source, SourceIngestionService(db, registry, poll_timeout_seconds=timeout)


@pytest.mark.asyncio
async def test_poll_deadline_and_cancellation_finish_durable_attempt_without_cursor(tmp_path):
    db, source, ingestion = setup(tmp_path, timeout=0.02)
    result = await ingestion.poll_once("hanging")
    assert result.error_code == "source_timeout"
    attempt = ingestion.list_attempts("hanging")[0]
    assert attempt.status == "failed" and attempt.completed_at is not None
    assert ingestion.health("hanging").cursor is None
    ingestion.poll_timeout_seconds = 30
    source.entered.clear()
    task = asyncio.create_task(ingestion.poll_once("hanging"))
    await source.entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert ingestion.list_attempts("hanging")[0].error_code == "source_poll_interrupted"


@pytest.mark.asyncio
async def test_hung_source_does_not_block_window_delivery_or_durable_heartbeat(tmp_path):
    db, source, ingestion = setup(tmp_path)
    windows_progressed = asyncio.Event()
    deliveries_progressed = asyncio.Event()
    window_ticks = []

    class Dispatcher:
        async def dispatch_once(self):
            deliveries_progressed.set()
            return 0

    scheduler = RealtimeScheduler(ingestion, notifications=Dispatcher())

    async def windows():
        window_ticks.append(1)
        if len(window_ticks) >= 2:
            windows_progressed.set()

    scheduler._windows = windows
    heartbeat = ServiceHeartbeatService(db)
    worker = RealtimeWorker(scheduler, heartbeat, "isolated", "fake", 1, datetime.now(UTC))
    task = asyncio.create_task(worker.run_forever(0.02))
    try:
        await asyncio.wait_for(source.entered.wait(), timeout=1)
        await asyncio.wait_for(windows_progressed.wait(), timeout=1)
        await asyncio.wait_for(deliveries_progressed.wait(), timeout=1)
        before = heartbeat.list_views()[0].heartbeat_at
        async with asyncio.timeout(3):
            while heartbeat.list_views()[0].heartbeat_at == before:
                await asyncio.sleep(0.05)
        assert ingestion.list_attempts("hanging")[0].status == "running"
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert ingestion.list_attempts("hanging")[0].status == "failed"


@pytest.mark.asyncio
async def test_api_worker_poll_lease_recovers_without_late_cursor_overwrite(tmp_path):
    db = Database(f"sqlite+pysqlite:///{tmp_path / 'lease.sqlite3'}")
    db.create_all()
    now = [datetime.now(UTC)]
    entered, release = asyncio.Event(), asyncio.Event()

    class Source:
        manifest = HangingSource.manifest

        def __init__(self, delayed):
            self.delayed = delayed

        async def poll(self, cursor=None):
            if self.delayed:
                entered.set()
                await release.wait()
            return SourcePollResult(
                source_id="hanging",
                cursor_before=cursor,
                cursor_after="obsolete" if self.delayed else "recovered",
                envelopes=(),
                fetched_at=now[0],
            )

    def service(delayed):
        registry = SourceRegistry()
        registry.register(Source(delayed))
        return SourceIngestionService(db, registry, clock=lambda: now[0])

    old, current = service(True), service(False)
    pending = asyncio.create_task(old.poll_once("hanging"))
    await entered.wait()
    busy = await current.poll_once("hanging")
    assert busy.skipped and busy.error_code == "source_poll_busy"
    assert len(current.list_attempts("hanging")) == 1
    now[0] += timedelta(minutes=3)
    recovered = await current.poll_once("hanging")
    assert recovered.cursor == "recovered"
    assert any(
        a.error_code == "source_poll_lease_recovered" for a in current.list_attempts("hanging")
    )
    release.set()
    late = await pending
    assert late.error_code == "source_poll_lease_lost"
    assert current.health("hanging").cursor == "recovered"
    assert len(current.list_attempts("hanging")) == 2


@pytest.mark.asyncio
async def test_expired_manual_poll_recovered_before_future_scheduled_poll(tmp_path):
    db, source, ingestion = setup(tmp_path)
    now = [datetime.now(UTC)]
    ingestion.clock = lambda: now[0]
    pending = asyncio.create_task(ingestion.poll_once("hanging"))
    await source.entered.wait()
    with db.session() as session:
        session.get(SourceStateRecord, "hanging").next_poll_at = now[0] + timedelta(hours=6)
    now[0] += timedelta(minutes=3)
    try:
        assert (await ingestion.poll_due("hanging")).skipped
        attempt = ingestion.list_attempts("hanging")[0]
        assert attempt.status == "failed"
        assert attempt.error_code == "source_poll_lease_recovered"
        assert len(ingestion.list_attempts("hanging")) == 1
        with db.session() as session:
            assert session.get(SourceStateRecord, "hanging").poll_owner is None
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)

"""Owned-loop and isolated SQLite controls; no live quotes, HTTP, or model calls."""
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import pytest
from sqlalchemy import event

from packages.kernel.decision_hub_kernel.persistence.db import (
    Database, SourceStateRecord, SourcePollAttemptRecord, as_utc,
)
from packages.kernel.decision_hub_kernel.persistence.risk_records import MarketPathAcquisitionRecord
from packages.kernel.decision_hub_kernel.ports.market_path import spot_scope
from packages.kernel.decision_hub_kernel.ports.sources import SourceManifest
from packages.provider_adapters.research.raw_capture import RawBodyArchive
from packages.source_adapters.registry import SourceRegistry


from packages.kernel.decision_hub_kernel.application import (
    source_ingest as INGEST, scheduler as SCHEDULER, market_risk_data as MARKET,
)
NOW = datetime(2026, 10, 12, 19, 0, 15, tzinfo=timezone.utc)


@pytest.fixture
def database(tmp_path):
    db = Database('sqlite+pysqlite:///' + (tmp_path / 'quote.db').as_posix())
    db.create_all()
    yield db
    db.engine.dispose()


def ingestion(database):
    registry = SourceRegistry()
    registry.register(SimpleNamespace(manifest=SourceManifest(
        source_id='source', source_type='official_feed', version='fixture')))
    return INGEST.SourceIngestionService(database, registry, clock=lambda: NOW)


def test_empty_expiry_precheck_never_requests_sqlite_writer(database):
    service = ingestion(database)
    statements = []
    def observe(_c, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement.split()[0])
    event.listen(database.engine, 'before_cursor_execute', observe)
    try:
        with database.engine.connect() as held:
            held.exec_driver_sql('BEGIN IMMEDIATE')
            service._recover_expired_poll('source')
            held.rollback()
    finally:
        event.remove(database.engine, 'before_cursor_execute', observe)
    assert 'UPDATE' not in statements and 'SELECT' in statements


def test_real_expired_owner_preserves_cas_and_attempt_journal(database):
    service = ingestion(database)
    with database.session() as writer:
        row = writer.get(SourceStateRecord, 'source')
        row.poll_owner, row.poll_lease_expires_at = 'old', NOW - timedelta(seconds=1)
        writer.add(SourcePollAttemptRecord(attempt_id='attempt', source_id='source',
            trigger_type='manual', status='running', started_at=NOW - timedelta(minutes=2),
            received_count=0, accepted_count=0, duplicate_count=0, revision_count=0))
    service._recover_expired_poll('source')
    with database.session() as reader:
        row, attempt = reader.get(SourceStateRecord, 'source'), reader.get(SourcePollAttemptRecord, 'attempt')
        assert row.poll_owner is None and row.poll_lease_expires_at is None
        assert attempt.status == 'failed' and attempt.error_code == 'source_poll_lease_recovered'
        assert as_utc(attempt.completed_at) == NOW


@pytest.mark.asyncio
async def test_batch_keeps_one_closed_minute_with_fresh_per_scope_lease(database, tmp_path):
    now = [NOW]
    scopes = (spot_scope('BTC-USDT'), spot_scope('ETH-USDT'))
    service = MARKET.MarketRiskDataService(database, RawBodyArchive(tmp_path / 'raw'),
        tmp_path / 'features', enabled=True, scopes=scopes, clock=lambda: now[0])
    claims = []
    async def observed_refresh(scope, claim, *, deadline_at):
        claims.append(claim)
        now[0] += timedelta(minutes=1)
        return True  # Test only claim/window basis, no provider success or report grant.
    service._refresh = observed_refresh
    assert await service.refresh_due() == 2
    assert len(claims) == 2
    assert {as_utc(row.slot_at) for row in claims} == {NOW.replace(second=0)}
    assert {as_utc(row.window_end_at) for row in claims} == {NOW.replace(second=0)}
    assert as_utc(claims[1].started_at) == NOW + timedelta(minutes=1)
    assert as_utc(claims[1].lease_expires_at) == NOW + timedelta(minutes=4)
    assert service.stale_after_seconds == 180


def scheduler_for(risk_data, monkeypatch):
    scheduler = SCHEDULER.RealtimeScheduler(SimpleNamespace(), risk_data=risk_data)
    async def noop(*_args, **_kwargs):
        return 0
    for name in ('_poll_sources', '_discovery_recovery', '_windows', '_runs', '_outcomes',
        '_observations', '_assessments', '_effect_tracking_requests', '_calibrations',
        '_notifications', '_risk_evaluations', '_risk_research', '_risk_shadow', '_risk_memory',
        '_risk_enhancements', '_history_hourly', '_history_outcome'):
        setattr(scheduler, name, noop)
    original = SCHEDULER._independent_channel
    async def fast_cadence(channel, name, action, cadence, timeout):
        assert name == 'risk_acquisition' and cadence == 60 and timeout == 90
        # Only compress this test's sleep; execute the real full channel body.
        return await original(channel, name, action, 0.01, timeout)
    monkeypatch.setattr(SCHEDULER, '_independent_channel', fast_cadence)
    return scheduler


@pytest.mark.asyncio
async def test_older_batch_cannot_create_a_slot_behind_completed_native_claim(database, tmp_path):
    now = [NOW]
    scope = spot_scope('BTC-USDT')
    service = MARKET.MarketRiskDataService(database, RawBodyArchive(tmp_path / 'raw'),
        tmp_path / 'features', enabled=True, scopes=(scope,), clock=lambda: now[0])
    claim = await service._claim(scope)
    assert claim is not None
    assert not await service._finish(claim.acquisition_id, error='market_acquisition_fixture_failure')
    now[0] += timedelta(minutes=5)
    assert await service._claim(scope, slot_at=NOW.replace(second=0) - timedelta(minutes=1)) is None
    with database.session() as reader:
        assert reader.query(MarketPathAcquisitionRecord).count() == 1


@pytest.mark.asyncio
async def test_quote_cadence_advances_while_source_blocks_main_loop(monkeypatch):
    entered, progressed = threading.Event(), asyncio.Event()
    calls, active, maximum = [], [0], [0]
    main_thread = threading.get_ident()
    class Quotes:
        async def refresh_due(self, *, deadline_at):
            active[0] += 1
            maximum[0] = max(maximum[0], active[0])
            try:
                calls.append(threading.get_ident())
                entered.set()
                await asyncio.sleep(0)
            finally:
                active[0] -= 1
            return 0
    scheduler = scheduler_for(Quotes(), monkeypatch)
    async def blocking_source():
        assert await asyncio.to_thread(entered.wait, 1)
        count = len(calls)
        time.sleep(0.12)  # Represents the proven synchronous source SQLite wait.
        assert len(calls) >= count + 2
        progressed.set()
    scheduler._poll_sources = blocking_source
    task = asyncio.create_task(scheduler.run_forever(0.02))
    try:
        await asyncio.wait_for(progressed.wait(), 2)
        assert maximum[0] == 1 and main_thread not in calls and len(set(calls)) == 1
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('cleanup_raises', [False, True])
async def test_repeated_cancel_drains_real_owned_loop_even_if_cleanup_raises(monkeypatch, cleanup_raises):
    entered, cleaning, allow_exit, exited = (threading.Event() for _ in range(4))
    class Quotes:
        async def refresh_due(self, *, deadline_at):
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cleaning.set()
                assert await asyncio.to_thread(allow_exit.wait, 2)
                if cleanup_raises:
                    raise RuntimeError('controlled_cleanup_failure')
                raise
            finally:
                exited.set()
    scheduler = scheduler_for(Quotes(), monkeypatch)
    task = asyncio.create_task(scheduler.run_forever(0.02))
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        task.cancel()
        assert await asyncio.to_thread(cleaning.wait, 1)
        task.cancel()
        await asyncio.sleep(0.02)
        assert not task.done() and not exited.is_set()
        allow_exit.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
        assert exited.is_set()
    finally:
        allow_exit.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)

import asyncio
from datetime import UTC, datetime, timedelta
import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from apps.hub_api.event_lifecycle_routes import event_lifecycle_router
from packages.contracts_py.decision_hub_contracts.models import SourceManifest
from packages.kernel.decision_hub_kernel.application.event_coverage import EventCoverageService
from packages.kernel.decision_hub_kernel.application.source_ingest import SourceIngestionService
from packages.kernel.decision_hub_kernel.persistence.db import Database, SourcePollAttemptRecord
from packages.kernel.decision_hub_kernel.persistence.event_scenario_records import EventScenarioJobRecord
from packages.kernel.decision_hub_kernel.ports.sources import SourcePollResult
from packages.source_adapters.registry import SourceRegistry
from .test_event_realization import FixtureArchive, SAVED


def test_real_calendar_allows_scheduled_release_slot_through_both_six_hour_gates(tmp_path):
    path = tmp_path / 'poll.sqlite3'
    db = Database('sqlite:///' + path.as_posix())
    db.create_all()
    with sqlite3.connect(path.as_uri(), uri=True) as connection:
        connection.execute('ATTACH DATABASE ? AS saved', ((SAVED / 'scenario-control-01.sqlite3').as_uri() + '?mode=ro',))
        for table in ('observations', 'source_poll_attempts', 'raw_captures'):
            connection.execute(f'INSERT INTO main.{table} SELECT * FROM saved.{table}')
        connection.commit()
    archive = FixtureArchive(tmp_path / 'raw')
    t0 = datetime(2026, 11, 10, 13, 30, tzinfo=UTC)
    clock = [t0 - timedelta(hours=2)]

    class Source:
        manifest = SourceManifest(source_id='bls-public-api', source_type='official_feed', version='fixture.v1', authority_level='official')
        calls = 0

        async def poll(self, cursor=None):
            self.calls += 1
            return SourcePollResult(source_id=self.manifest.source_id, cursor_before=cursor,
                cursor_after=cursor, envelopes=(), fetched_at=clock[0], next_poll_at=clock[0] + timedelta(hours=6))

    source = Source()
    registry = SourceRegistry()
    registry.register(source)
    ingestion = SourceIngestionService(db, registry, clock=lambda: clock[0], event_archive=archive)
    assert not asyncio.run(ingestion.poll_once('bls-public-api')).skipped
    clock[0] = t0 + timedelta(seconds=60)
    assert not asyncio.run(ingestion.poll_due('bls-public-api')).skipped
    assert source.calls == 2
    assert asyncio.run(ingestion.poll_due('bls-public-api')).skipped
    clock[0] = t0 + timedelta(seconds=300)
    assert asyncio.run(ingestion.poll_once('bls-public-api')).skipped
    assert not asyncio.run(ingestion.poll_due('bls-public-api')).skipped
    with db.session() as session:
        tags = session.scalars(select(SourcePollAttemptRecord.trigger_type).where(
            SourcePollAttemptRecord.source_id == 'bls-public-api',
            SourcePollAttemptRecord.started_at >= t0 - timedelta(hours=2),
            SourcePollAttemptRecord.status != 'skipped',
        ).order_by(SourcePollAttemptRecord.started_at)).all()
    assert source.calls == 3
    assert tags == ['manual', 'scheduled_cpi:2026-11-10:01', 'scheduled_cpi:2026-11-10:05']


def test_missing_event_inputs_create_visible_pre_event_notice_and_idempotent_ack(tmp_path):
    db = Database('sqlite:///' + (tmp_path / 'coverage.sqlite3').as_posix())
    db.create_all()
    at = datetime(2026, 11, 10, 12, tzinfo=UTC)
    t0 = at + timedelta(minutes=90)
    with db.session() as session:
        session.add(EventScenarioJobRecord(job_id='coverage-fixture', event_key='fred-cpi:2026-11-10',
            scheduled_at=t0, reference_period=None, slot_id='2026-11-10', job_kind='forecast', scope_key='global',
            input_hash='0' * 64, model_hash='1' * 64, input_manifest_json='{}', state='waiting_inputs',
            attempts=0, lease_generation=0, created_at=at, updated_at=at))
    service = EventCoverageService(db, clock=lambda: at)
    assert service.tick() == 3 and service.tick() == 3
    rows = service.for_event('fred-cpi:2026-11-10')
    assert [r['state'] for r in rows] == ['missing', 'missing', 'pending']
    assert rows[0]['forecast_id'] is None
    app = FastAPI()
    app.include_router(event_lifecycle_router(db, archive=FixtureArchive(tmp_path / 'raw'),
        require_owner=lambda value: value or 'owner', feature_root=tmp_path / 'features', clock=lambda: at))
    client = TestClient(app)
    view = client.get('/v1/event-lifecycles/fred-cpi:2026-11-10', headers={'X-Owner-Id': 'owner'}).json()
    assert view['realization']['state'] == 'awaiting_release' and len(view['notices']) == 2
    notice_id = view['notices'][0]['notice_id']
    body = {'request_id': 'same-ack', 'command': 'ack'}
    first = client.post(f'/v1/event-notices/{notice_id}/commands', headers={'X-Owner-Id': 'owner', 'Idempotency-Key': 'same-ack'}, json=body)
    assert first.status_code == 200
    assert client.post(f'/v1/event-notices/{notice_id}/commands', headers={'X-Owner-Id': 'owner', 'Idempotency-Key': 'same-ack'}, json=body).json() == first.json()
    view = client.get('/v1/event-lifecycles/fred-cpi:2026-11-10', headers={'X-Owner-Id': 'owner'}).json()
    assert view['notices'][0]['ack_at'] and view['notices'][0]['display_at'] is None

"""Worker-to-PC main path with real saved inputs and an explicit future fixture."""
from datetime import timedelta
import asyncio
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from apps.hub_api.event_lifecycle_routes import event_lifecycle_router
from packages.kernel.decision_hub_kernel.application.event_lifecycle import EventLifecycleProcessor
from packages.kernel.decision_hub_kernel.persistence.db import RunRecord
from packages.kernel.decision_hub_kernel.persistence.event_realization_records import EventRealizationRecord
from .test_event_realization import make_realization_case, admit_bls_fixture, engineering_actual_body


def test_worker_actual_activation_to_pc_status_survives_restart(tmp_path):
    db, archive, forecast = make_realization_case(tmp_path)
    now = [forecast.scheduled_at - timedelta(minutes=10)]

    def worker():
        return EventLifecycleProcessor(db, archive=archive,
            feature_root=tmp_path / 'features', clock=lambda: now[0])

    service = worker()
    initial = service.tick()
    assert not initial.errors
    assert initial.realizations_written == 0
    known = forecast.scheduled_at + timedelta(minutes=5)
    with db.session() as session:
        admit_bls_fixture(session, archive, body=engineering_actual_body(),
            period=forecast.reference_period, known_at=known)
    now[0] = known + timedelta(seconds=2)
    report = service.tick()
    assert not report.errors
    assert report.realizations_written == 1
    assert report.evaluations_written == 4

    app = FastAPI()
    app.include_router(event_lifecycle_router(db, archive=archive,
        feature_root=tmp_path / 'features', require_owner=lambda v: v or 'owner',
        clock=lambda: now[0]))
    client = TestClient(app)
    response = client.get('/v1/event-lifecycles/' + forecast.event_key,
        headers={'X-Owner-Id': 'owner'})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['realization']['state'] == 'realized'
    assert body['realization']['scenario_id']
    assert len(body['realization']['metrics']) == 4
    assert len(body['assets']) == 2
    # No opportunity original was created by this fixture. The PC must show
    # that gap, rather than infer a live action from an actual release alone.
    assert all(p['state'] == 'missing' for a in body['assets'] for p in a['positions'])
    assert all(len(a['positions']) == 2 for a in body['assets'])
    repeated = service.tick()
    assert not repeated.errors and repeated.realizations_written == 0
    restarted = worker().tick()
    assert not restarted.errors and restarted.realizations_written == 0
    with db.session() as session:
        assert session.scalar(select(func.count()).select_from(EventRealizationRecord)) == 1
        assert session.scalar(select(func.count()).select_from(RunRecord)) == 0


def test_future_producer_failure_does_not_starve_existing_event_consumers(monkeypatch):
    from apps.hub_worker import event_scenario_tick as worker
    from packages.kernel.decision_hub_kernel.application.event_lifecycle import EventLifecycleReport
    calls = []
    class FailedProducer:
        def __init__(self, *args, **kwargs): pass
        def tick(self): raise ValueError('calendar_original_invalid')
    class Consumers:
        def __init__(self, *args, **kwargs): pass
        def tick(self):
            calls.append('existing_events')
            return EventLifecycleReport(evaluations_written=3)
    async def original():
        calls.append('original')
        return 2
    monkeypatch.setattr(worker, 'EventScenarioAutoproducer', FailedProducer)
    monkeypatch.setattr(worker, 'EventLifecycleProcessor', Consumers)
    realtime = SimpleNamespace(scheduler=SimpleNamespace(risk_enhancement_tick=original))
    worker.install_event_scenario_on_realtime(realtime, object())
    assert asyncio.run(realtime.scheduler.risk_enhancement_tick()) == 5
    assert calls == ['existing_events', 'original']
    assert realtime.scheduler._event_scenario_last_report.errors == ('forecast:calendar_original_invalid',)

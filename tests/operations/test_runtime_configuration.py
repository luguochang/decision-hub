from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from apps.hub_api.main import create_app
from packages.kernel.decision_hub_kernel.application.live_observation import ServiceHeartbeatService
from packages.kernel.decision_hub_kernel.application.runtime_configuration import (
    list_runtime_configurations,
)
from packages.kernel.decision_hub_kernel.persistence.db import Database


def test_each_restart_records_immutable_allowlisted_configuration_and_new_start(
    tmp_path, monkeypatch
):
    db = Database(f"sqlite+pysqlite:///{tmp_path / 'runtime.sqlite3'}")
    db.create_all()
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-appear")
    monkeypatch.setenv("DECISION_HUB_SOURCE_ACQUISITION_KEY", "another-secret")
    monkeypatch.setenv("DECISION_HUB_EVENT_WINDOW_LIVE_ENABLED", "1")
    monkeypatch.setenv("DECISION_HUB_MARKET_ENABLED", "0")
    start = datetime(2026, 9, 29, tzinfo=UTC)
    service = ServiceHeartbeatService(db, clock=lambda: start + timedelta(seconds=2))
    args = dict(
        service_id="hub-realtime-worker",
        role="realtime_worker",
        instance_id="stable-name",
        version="v1",
        mode="provider",
        interval_seconds=10,
    )
    service.beat(**args, started_at=start)
    first = list_runtime_configurations(db)[0]
    assert first.settings["event_window_capture"] is False
    monkeypatch.setenv("DECISION_HUB_MARKET_ENABLED", "1")
    service.beat(**args, started_at=start)
    assert list_runtime_configurations(db)[0] == first
    later = start + timedelta(minutes=1)
    service.beat(**args, started_at=later)
    records = list_runtime_configurations(db)
    assert len(records) == 2
    assert {r.settings["event_window_capture"] for r in records} == {False, True}
    assert len({r.fingerprint for r in records}) == 2
    assert service.list_views()[0].started_at == later
    serialized = " ".join(r.model_dump_json() for r in records)
    assert "must-not-appear" not in serialized and "another-secret" not in serialized
    with TestClient(create_app(db, source_connectors=[])) as client:
        response = client.get("/v1/operations/runtime-configurations")
        assert response.status_code == 200
        assert all(r["started_at"].endswith("Z") for r in response.json())


@pytest.mark.parametrize("market,capture,expected", [(False, True, False), (True, True, True)])
def test_runtime_reports_constructed_sampler_not_inherited_flag(
    tmp_path, monkeypatch, market, capture, expected
):
    from apps.hub_worker.composition import build_realtime_worker
    from packages.pilot_runtime import PilotSettings
    from packages.source_adapters.registry import SourceRegistry

    db = Database(f"sqlite+pysqlite:///{tmp_path / 'sampler.sqlite3'}")
    db.create_all()
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DECISION_HUB_EVENT_WINDOW_LIVE_ENABLED", "1" if capture else "0")
    worker = build_realtime_worker(
        db, registry=SourceRegistry(), settings=PilotSettings(market_enabled=market)
    )
    worker._beat()
    assert list_runtime_configurations(db)[0].settings["event_window_capture"] == expected

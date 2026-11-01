"""Actual signed schedule API projection, bounded SQL and no forecast reader."""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from apps.hub_api.history_hourly_routes import (
    history_hourly_status_router,
)
from apps.hub_api.history_product_routes import history_product_router
from apps.hub_worker.history_hourly_v2 import (
    SCOPES,
    HistoryHourlyCommitUnconfirmed,
    HistoryHourlyV2,
)
from packages.contracts_py.decision_hub_contracts.generated.history_hourly_status_v2 import (
    HistoryHourlyStatusV2,
)
from packages.kernel.decision_hub_kernel.persistence.history_hourly_v2_records import (
    HistoryHourlyV2SlotRecord as Slot,
)
from tests.market_risk.test_history_hourly_v2 import setup


def test_actual_signed_api_blocked_and_unknown_fence_no_raw(tmp_path, monkeypatch):
    db, commands, _, boot = setup(tmp_path, monkeypatch)
    tick = HistoryHourlyV2(boot)
    tick.process_one()
    writer = commands._write_session

    @contextmanager
    def reservation_not_returned():
        raise OSError("private unknown reserve result, no confirmed row")
        yield

    monkeypatch.setattr(commands, "_write_session", reservation_not_returned)
    with pytest.raises(HistoryHourlyCommitUnconfirmed):
        tick.process_one()
    monkeypatch.setattr(commands, "_write_session", writer)

    def forbidden_reader(*_args, **_kwargs):
        raise AssertionError("fast schedule attempted original reader or archive")

    monkeypatch.setattr(boot.originals, "read_original", forbidden_reader)
    monkeypatch.setattr(boot.originals.archive, "read", forbidden_reader)
    monkeypatch.setattr(boot.originals.raw, "read", forbidden_reader)
    monkeypatch.setattr(commands.ledger.builder.originals, "load", forbidden_reader)
    with commands._read_session() as session:
        before_api_changes = session.connection().connection.driver_connection.total_changes
    app = FastAPI()
    app.include_router(history_product_router(boot))
    app.include_router(history_hourly_status_router(boot))
    client = TestClient(app, base_url=boot.authority.origin)
    assert client.get("/v1/history/schedule", headers={"X-Owner-Id": tick.owner}).status_code == 403
    pair = client.post(
        "/v1/history/owner-session",
        json={"pairing_token": "private-hour-control-token-000001"},
        headers={"Origin": boot.authority.origin},
    )
    assert pair.status_code == 200
    response = client.get("/v1/history/schedule")
    assert response.status_code == 200, response.text
    value = HistoryHourlyStatusV2.model_validate_json(response.text)
    assert value.owner_authenticated and not value.current_forecast_originals_verified
    assert not value.automatic_retry and not value.financial_qualified
    btc, eth = value.scopes
    assert (
        btc.state == "blocked" and btc.reason_code == "history_hourly_trusted_model_not_configured"
    )
    assert eth.state == "unconfirmed" and eth.fence_present and eth.scope_id == SCOPES[1]
    assert eth.cutoff_at is not None and eth.forecast_id is None
    assert "模型" in btc.reason_label and "暂停" in eth.reason_label
    assert (
        client.get("/v1/history/schedule", headers={"Origin": "http://127.0.0.1:1"}).status_code
        == 403
    )
    with commands._read_session() as session:
        assert session.connection().connection.driver_connection.total_changes == before_api_changes
        assert session.scalar(select(func.count()).select_from(Slot)) == 1
    output = tmp_path / "actual-signed-hourly-status.json"
    output.write_text(value.model_dump_json(indent=2), encoding="utf-8")
    absent = FastAPI()
    absent.include_router(history_hourly_status_router(None))
    assert TestClient(absent).get("/v1/history/schedule").status_code == 503
    db.engine.dispose()

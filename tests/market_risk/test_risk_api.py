"""API integration exercises real capture/store/rule/query, using labelled fake HTTP."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from apps.hub_api.risk_routes import risk_router
from packages.contracts_py.decision_hub_contracts.generated.market_risk_view import (
    RiskAlertView,
    RiskSummaryView,
    RiskTimelineView,
)
from packages.kernel.decision_hub_kernel.application.market_risk_data import MarketRiskDataService
from packages.kernel.decision_hub_kernel.application.risk_monitor import RiskMonitorService
from packages.kernel.decision_hub_kernel.persistence.db import Database, OutboxRecord
from packages.kernel.decision_hub_kernel.persistence.risk_records import RiskAlertRecord
from packages.provider_adapters.market.okx_path import OKXMarketPathAdapter
from packages.provider_adapters.research.raw_capture import RawBodyArchive
from packages.query_views.market_risk import MarketRiskQuery
from tests.market_risk.test_market_data import NOW, SCOPE, PublicFixture
from tests.market_risk.test_risk_monitor import rule, sample


@pytest.fixture
def runtime(tmp_path: Path):
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'api.sqlite3'}")
    database.create_all()
    clock = [NOW]
    transport = PublicFixture(clock)
    data = MarketRiskDataService(
        database,
        RawBodyArchive(tmp_path / "raw-captures"),
        tmp_path / "features",
        adapter=OKXMarketPathAdapter(fetcher=transport.fetch),
        scopes=(SCOPE,),
        enabled=True,
        clock=lambda: clock[0],
    )
    monitors = RiskMonitorService(database, clock=lambda: clock[0])
    query = MarketRiskQuery(database, tmp_path, enabled=True, clock=lambda: clock[0])
    app = FastAPI()

    def owner(value):
        if value != "owner":
            raise HTTPException(403, "owner_required")
        return value

    app.include_router(risk_router(database, tmp_path, owner, enabled=True, clock=lambda: clock[0]))
    with TestClient(app) as client:
        yield database, data, monitors, query, client, clock, transport
    database.engine.dispose()


def test_fresh_capture_is_projected_in_strict_contract_without_http_on_reads(runtime):
    _, data, _, _, client, _, transport = runtime
    assert asyncio.run(data.refresh_due()) == 1
    count = len(transport.calls)
    reply = client.get(f"/v1/risk/scopes/{SCOPE.scope_id}/summary")
    assert reply.status_code == 200
    result = RiskSummaryView.model_validate(reply.json())
    assert result.quote and result.quote.price == "100"
    assert result.data_status == "fresh" and result.risk_level == "unknown"
    assert result.window and result.window.samples == result.window.expected_samples == 1440
    assert result.window.max_adverse_move_pct == 1.0
    assert result.research.status == "not_available"
    assert client.get("/v1/risk/scopes").json()["items"][1]["instrument"] == "ETH-USDT"
    assert len(transport.calls) == count


def test_missing_and_other_asset_are_never_normal_or_reused(runtime):
    _, data, _, _, client, _, _ = runtime
    assert asyncio.run(data.refresh_due()) == 1
    result = client.get("/v1/risk/scopes/crypto:spot:okx:ETH-USDT:trade:1m/summary").json()
    assert result["risk_level"] == "unknown" and result["data_status"] == "missing"
    assert result["quote"] is None
    assert (
        client.get(
            "/v1/risk/scopes/crypto:perpetual_swap:okx:BTC-USDT-SWAP:trade:1m/summary"
        ).status_code
        == 404
    )


def test_provider_interruption_is_visible_even_if_last_price_is_recent(runtime):
    _, data, _, _, client, clock, transport = runtime
    assert asyncio.run(data.refresh_due()) == 1
    transport.failure = "market_path_http_429"
    for _ in range(2):
        clock[0] += timedelta(minutes=1)
        assert asyncio.run(data.refresh_due()) == 0
    result = client.get(f"/v1/risk/scopes/{SCOPE.scope_id}/summary").json()
    assert result["data_status"] == "stale" and result["quote"]["price"] == "100"
    assert "连续失败" in result["explanation"]


def test_timeline_rejects_future_and_preserves_missing_ranges(runtime):
    _, data, _, _, client, clock, _ = runtime
    assert asyncio.run(data.refresh_due()) == 1
    end = clock[0].replace(second=0, microsecond=0)
    reply = client.get(
        f"/v1/risk/scopes/{SCOPE.scope_id}/timeline",
        params={"start": (end - timedelta(minutes=30)).isoformat(), "end": end.isoformat()},
    )
    assert reply.status_code == 200
    view = RiskTimelineView.model_validate(reply.json())
    assert len(view.points) == 30 and not view.missing_ranges
    params = {
        "start": (end - timedelta(minutes=30)).isoformat(),
        "end": (end + timedelta(minutes=1)).isoformat(),
    }
    assert (
        client.get(f"/v1/risk/scopes/{SCOPE.scope_id}/timeline", params=params).status_code == 409
    )
    unknown = client.get(
        "/v1/risk/scopes/crypto:spot:okx:ETH-USDT:trade:1m/timeline",
        params={"start": (end - timedelta(minutes=30)).isoformat(), "end": end.isoformat()},
    ).json()
    assert unknown["data_status"] == "missing" and len(unknown["missing_ranges"]) == 1


def test_published_display_and_ack_are_separate_and_original_retry_is_idempotent(runtime):
    database, _, monitors, _, client, clock, _ = runtime
    plan = monitors.ensure_plan(rule())
    claim = monitors.claim(plan)
    assert claim and monitors.complete(claim, sample(clock[0]))
    with database.session() as session:
        alert_id = session.query(RiskAlertRecord).one().alert_id
    path = f"/v1/risk/alerts/{alert_id}"
    assert client.get(path).json()["displayed_at"] is None
    command = {"request_id": "visible01", "client_time": clock[0].isoformat()}
    assert client.post(path + "/display-receipt", json=command).status_code == 403
    first = client.post(path + "/display-receipt", json=command, headers={"X-Owner-Id": "owner"})
    second = client.post(path + "/display-receipt", json=command, headers={"X-Owner-Id": "owner"})
    assert first.status_code == second.status_code == 200 and first.json() == second.json()
    displayed = RiskAlertView.model_validate(client.get(path).json())
    assert displayed.displayed_at and displayed.acknowledged_at is None
    command["request_id"] = "ack01"
    assert (
        client.post(path + "/ack", json=command, headers={"X-Owner-Id": "owner"}).status_code == 200
    )
    assert client.get(path).json()["acknowledged_at"]
    assert client.get(path).json()["status"] == "active"
    with database.session() as session:
        assert session.query(OutboxRecord).one().sent_at is None


def test_pause_resume_persists_and_reused_identity_conflicts(runtime):
    _, _, monitors, _, client, _, _ = runtime
    plan = monitors.ensure_plan(rule())
    path = f"/v1/risk/monitors/{plan}/command"
    payload = {"request_id": "pause01", "action": "pause"}
    headers = {"X-Owner-Id": "owner"}
    assert client.post(path, json=payload).status_code == 403
    first = client.post(path, json=payload, headers=headers)
    assert first.status_code == 200 and first.json()["monitor"]["status"] == "paused"
    assert client.post(path, json=payload, headers=headers).json() == first.json()
    payload["action"] = "resume"
    assert client.post(path, json=payload, headers=headers).status_code == 409
    payload["request_id"] = "resume01"
    reply = client.post(path, json=payload, headers=headers)
    assert reply.status_code == 200 and reply.json()["monitor"]["status"] == "unknown"


def test_halted_monitor_and_switch_off_are_not_reported_running(runtime):
    database, _, monitors, query, _, clock, _ = runtime
    plan = monitors.ensure_plan(rule())
    claim = monitors.claim(plan)
    assert claim and monitors.complete(claim, sample(clock[0], "0.001"))
    assert query.monitor(plan).status == "active"
    clock[0] += timedelta(seconds=181)
    assert query.monitor(plan).status == "degraded"
    disabled = MarketRiskQuery(database, Path("."), enabled=False, clock=lambda: clock[0])
    assert disabled.monitor(plan).status == "unknown"

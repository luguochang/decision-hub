"""Small authentication/strict metadata control, never a provider or model fit."""

import hashlib
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.hub_api.history_owner_authority import HISTORY_COOKIE, HistoryOwnerAuthority
from apps.hub_api.history_product_routes import (
    HistoryProductBootstrap,
    history_product_router,
)
from apps.hub_worker.composition import build_history_observed_worker
from packages.contracts_py.decision_hub_contracts.generated.history_product_status_v2 import (
    HistoryProductStatusV2,
)
from packages.kernel.decision_hub_kernel.application.history_dataset_v2 import (
    HistoryDatasetV2Service,
)
from packages.kernel.decision_hub_kernel.persistence.db import Base, Database
from packages.kernel.decision_hub_kernel.ports.history_dataset_v2 import history_v2_id
from packages.kernel.decision_hub_kernel.ports.history_observed_training import fixed_cutoffs
from packages.kernel.decision_hub_kernel.ports.market_path import spot_scope

ORIGIN = "http://127.0.0.1:8891"
TOKEN = "private-history-pairing-control-token-0001"


def client_for(bootstrap=None):
    app = FastAPI()
    app.include_router(history_product_router(bootstrap))
    return TestClient(app, base_url=ORIGIN)


def test_independent_pair_fast_52_38_pages_and_closed_grants(tmp_path, monkeypatch):
    async def reject(*args, **kwargs):
        raise AssertionError("history API control attempted HTTP")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", reject)
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path))
    closed = client_for()
    assert closed.get("/v1/history/status", headers={"X-Owner-Id": "owner"}).status_code == 503
    db = Database("sqlite+pysqlite:///" + str(tmp_path / "api-private.sqlite3"))
    Base.metadata.create_all(db.engine)
    commands = build_history_observed_worker(db, owner_id="api_private_owner")
    originals = HistoryDatasetV2Service(commands, tmp_path, fixture_mode=True)
    known = datetime.now(UTC)
    cutoffs = fixed_cutoffs(known)
    start = cutoffs[52] + timedelta(minutes=1)
    end = cutoffs[-1] + timedelta(days=1, minutes=1)
    intervals = [
        dict(
            scope_id=spot_scope(asset).scope_id,
            start_at=start.isoformat(),
            end_at=end.isoformat(),
            missing_minutes=int((end - start).total_seconds() / 60),
            max_pages=185,
        )
        for asset in ("BTC-USDT", "ETH-USDT")
    ]
    with commands._read_session() as session:
        prepared = originals.prepare_original(
            session,
            kind="plan",
            owner_claim="api_private_owner",
            request_id="api_plan",
            plan_id=history_v2_id("plan", "api_private_owner", "api_plan"),
            known_asof=known,
            cutoffs=cutoffs,
            provenance=dict(
                fixture_mode=True, missing_intervals=intervals, file_readset=[], row_readset=[]
            ),
        )
    with commands._write_session() as session:
        plan = originals.record_original(session, prepared)
    authority = HistoryOwnerAuthority(
        signing_key=b"h" * 32,
        pairing_sha256=hashlib.sha256(TOKEN.encode()).digest(),
        owner_identity="api_private_owner",
        origin=ORIGIN,
    )
    client = client_for(HistoryProductBootstrap(authority, originals, plan_id=plan.original_id))
    assert (
        client.get("/v1/history/status", headers={"X-Owner-Id": "api_private_owner"}).status_code
        == 403
    )
    assert (
        client.post(
            "/v1/history/owner-session",
            json={"pairing_token": TOKEN},
            headers={"origin": "http://127.0.0.1:8892"},
        ).status_code
        == 403
    )
    paired = client.post(
        "/v1/history/owner-session", json={"pairing_token": TOKEN}, headers={"origin": ORIGIN}
    )
    assert paired.status_code == 200
    assert "Path=/v1/history" in paired.headers["set-cookie"]
    assert (
        "HttpOnly" in paired.headers["set-cookie"]
        and HISTORY_COOKIE in paired.headers["set-cookie"]
    )
    result = client.get("/v1/history/status?job_limit=1&excluded_limit=1&excluded_offset=52")
    assert result.status_code == 200, result.text
    value = HistoryProductStatusV2.model_validate_json(result.text)
    assert [
        (s.raw_complete_days, s.missing_days, s.first_known_complete_days)
        for s in value.data.scopes
    ] == [(52, 38, None), (52, 38, None)]
    assert value.excluded_windows.total == 76 and len(value.excluded_windows.items) == 1
    assert not value.data.current_originals_verified and value.forecast.probabilities is None
    assert (
        not value.actions.can_fit
        and not value.actions.can_forecast
        and not value.actions.can_refresh
    )
    assert client.get("/v1/history/status?excluded_limit=61").status_code == 422
    csrf = paired.json()["csrf_token"]
    assert (
        client.post("/v1/history/assets/BTC/refresh", headers={"origin": ORIGIN}).status_code == 403
    )
    assert (
        client.post(
            "/v1/history/assets/BTC/refresh", headers={"origin": ORIGIN, "X-History-CSRF": csrf}
        ).status_code
        == 403
    )
    snapshot = result.json()
    snapshot["actions"]["can_fit"] = True
    with pytest.raises(ValueError):
        HistoryProductStatusV2.model_validate_json(json.dumps(snapshot))
    snapshot = result.json()
    snapshot["forecast"]["probabilities"] = {
        name: "0.9"
        for name in ("baseline_long", "baseline_short", "conditional_long", "conditional_short")
    }
    with pytest.raises(ValueError):
        HistoryProductStatusV2.model_validate_json(json.dumps(snapshot))
    authority._authority.clock = lambda: 10**12
    assert client.get("/v1/history/status").status_code == 403
    db.engine.dispose()

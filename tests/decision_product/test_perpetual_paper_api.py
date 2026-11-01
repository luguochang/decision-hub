"""Signed PC commands exercise real paper persistence, never order execution."""
import hashlib

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from apps.hub_api.local_owner_session import LocalOwnerSessionAuthority
from packages.kernel.decision_hub_kernel.application.perpetual_paper import PerpetualPaperService
from packages.kernel.decision_hub_kernel.persistence.db import ProductCommandRecord
from tests.decision_product.test_perpetual_paper import NOW, creation
from tests.decision_product.test_perpetual_paper import runtime as runtime
from tests.decision_product.test_okx_perpetual_original import persisted_original as persisted_original

BASE = "/v1/decision-settings/paper-accounts"
ORIGIN = "http://127.0.0.1:8123"
TOKEN = "only-synthetic-local-paper-transport-test"


def client(runtime, pair=True):
    from apps.hub_api.decision_settings_routes import decision_settings_router
    db, _, source, _ = runtime
    authority = LocalOwnerSessionAuthority(
        signing_key=b"p" * 32, pairing_sha256=hashlib.sha256(TOKEN.encode()).digest(),
        owner_identity="owner", domain_pack_ref="crypto_macro.v1", origin=ORIGIN,
    )
    app = FastAPI()
    app.include_router(decision_settings_router(db, authority=authority, source=source, now=lambda: NOW))
    result = TestClient(app, headers={"Origin": ORIGIN})
    if pair:
        reply = result.post("/v1/decision-settings/owner-session", json={"pairing_token": TOKEN})
        assert reply.status_code == 200
        result.headers["X-Settings-CSRF"] = reply.json()["csrf_token"]
    return result


def body(runtime):
    value = creation(runtime)
    return {
        "schema_version": "perpetual-paper-account-create.v1", "request_id": value.request_id,
        "account_id": value.account_id, "instrument_refs": [ref.model_dump(mode="json") for ref in value.instrument_refs],
        "profile": value.profile.model_dump(mode="json"), "initial_cash_usdt": "2500",
        "simulation_acknowledged": True, "execution_mode": "manual_after_visible",
    }


def command_count(runtime):
    with runtime[0].session() as session:
        return session.scalar(select(func.count()).select_from(ProductCommandRecord))


def test_explicit_create_reopen_original_receipt_and_scoped_list(runtime):
    page, payload = client(runtime), body(runtime)
    assert page.get(BASE).json()["accounts"] == []
    response = page.post(BASE, json=payload, headers={"Idempotency-Key": payload["request_id"]})
    assert response.status_code == 200, response.text
    original = response.json()
    assert original["account"]["owner_id"] == "owner"
    assert original["account"]["state_version"] == 1
    assert original["account"]["execution_enabled"] is False
    assert original["account"]["execution_mode"] == "manual_after_visible"
    assert response.headers["cache-control"] == "no-store"
    assert page.post(BASE, json=payload).json() == original
    page.close()
    reopened = client(runtime)
    assert reopened.post(BASE + "/receipts/create", json=payload).json() == original
    listing = reopened.get(BASE).json()
    assert listing["accounts"] == [original["account"]]
    assert listing["owner_id"] == "owner" and listing["next_after_account_id"] is None
    assert command_count(runtime) == 1
    # A different owner's valid internal account cannot enter this signed list.
    db, _, source, _ = runtime
    PerpetualPaperService(db, source, now=lambda: NOW).create_account("other", creation(runtime, account="other-account"))
    assert reopened.get(BASE).json()["accounts"] == [original["account"]]


def test_missing_session_origin_csrf_and_unacknowledged_simulation_write_nothing(runtime):
    payload, before = body(runtime), command_count(runtime)
    unauth = client(runtime, pair=False)
    assert unauth.post(BASE, json=payload, headers={"X-Owner-Id": "owner"}).status_code == 403
    page = client(runtime)
    csrf = page.headers.pop("X-Settings-CSRF")
    assert page.post(BASE, json=payload).status_code == 403
    page.headers["X-Settings-CSRF"] = csrf
    assert page.post(BASE, json=payload, headers={"Origin": "https://foreign.invalid"}).status_code == 403
    assert page.post(BASE, json={**payload, "simulation_acknowledged": False}).status_code == 422
    assert command_count(runtime) == before


def test_conflict_and_recovery_never_create_a_different_account_or_command(runtime):
    page, payload = client(runtime), body(runtime)
    assert page.post(BASE, json=payload).status_code == 200
    before = command_count(runtime)
    changed = {**payload, "initial_cash_usdt": "3500"}
    assert page.post(BASE, json=changed).status_code == 409
    assert page.post(BASE + "/receipts/create", json=changed).status_code == 409
    assert page.post(BASE + "/receipts/missing", json={**payload, "request_id": "missing"}).status_code == 404
    assert page.post(BASE, json=payload, headers={"Idempotency-Key": "other"}).status_code == 409
    assert page.get(BASE).json()["accounts"][0]["account_id"] == payload["account_id"]
    assert command_count(runtime) == before

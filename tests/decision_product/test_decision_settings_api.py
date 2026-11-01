"""Signed owner transport exercised against real settings/contract persistence."""
from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
from datetime import timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.hub_api.local_owner_session import LocalOwnerSessionAuthority
from packages.kernel.decision_hub_kernel.persistence.db import RawCaptureRecord
from tests.decision_product.test_decision_settings_binding import (
    NOW,
    OWNER,
    command,
    counts,
    runtime,
    service,
)

MODULE = "apps.hub_api.decision_settings_routes"
ORIGIN = "http://127.0.0.1:8123"
TOKEN = "settings-private-pairing-token-32-characters"
BASE = "/v1/decision-settings"


def client_for(runtime, *, pair=True, server_errors=True):
    assert importlib.util.find_spec(MODULE), "signed settings API is missing"
    module = importlib.import_module(MODULE)
    authority = LocalOwnerSessionAuthority(
        signing_key=b"s" * 32, pairing_sha256=hashlib.sha256(TOKEN.encode()).digest(),
        owner_identity=OWNER, domain_pack_ref="crypto_macro.v1", origin=ORIGIN,
    )
    app = FastAPI()
    app.include_router(module.decision_settings_router(
        runtime.database, authority=authority, source=runtime.source, now=lambda: NOW,
    ))
    client = TestClient(app, headers={"Origin": ORIGIN}, raise_server_exceptions=server_errors)
    if pair:
        reply = client.post(BASE + "/owner-session", json={"pairing_token": TOKEN})
        assert reply.status_code == 200
        client.headers["X-Settings-CSRF"] = reply.json()["csrf_token"]
    return client


def test_signed_save_reopen_delete_and_original_receipt(runtime):
    value = command(runtime).model_dump(mode="json")
    client = client_for(runtime)
    saved = client.post(BASE + "/commands", json=value)
    assert saved.status_code == 200, saved.text
    original = saved.json()
    # Cross-language protocol fixture comes from the real Python writer.
    fixture = Path(__file__).parents[2] / "apps/decision-desk/src/api/decisionSettingsReceiptFixture.json"
    assert json.loads(fixture.read_text(encoding="utf-8")) == {"intent": {"owner_id": OWNER, "command": value}, "receipt": original}
    assert original["settings_saved"] is True
    assert original["reevaluation_state"] == "pending"
    client.close()
    reopened = client_for(runtime)
    current = reopened.get(BASE + "/current", params=value["partition"])
    assert current.status_code == 200
    assert current.json()["revision"]["values"] == value["values"]
    assert current.json()["state_version"] == 1
    remove = {**value, "request_id": "remove-settings", "operation": "remove", "values": None, "expected_state_version": 1}
    assert reopened.post(BASE + "/commands", json=remove).status_code == 200
    assert reopened.get(BASE + "/current", params=value["partition"]).json()["revision"]["operation"] == "remove"
    assert reopened.get(BASE + "/commands/" + value["request_id"]).json() == original
    assert reopened.post(BASE + "/commands", json=value).json() == original
    assert counts(runtime) == (2, 1, 2, 0)


def test_header_owner_missing_csrf_and_wrong_origin_write_nothing(runtime):
    value = command(runtime).model_dump(mode="json")
    before = counts(runtime)
    unauth = client_for(runtime, pair=False)
    assert unauth.post(BASE + "/commands", json=value, headers={"X-Owner-Id": OWNER}).status_code == 403
    assert unauth.get(BASE + "/current", params=value["partition"], headers={"X-Owner-Id": OWNER}).status_code == 403
    signed = client_for(runtime)
    csrf = signed.headers.pop("X-Settings-CSRF")
    assert signed.post(BASE + "/commands", json=value).status_code == 403
    signed.headers["X-Settings-CSRF"] = csrf
    assert signed.post(BASE + "/commands", json=value, headers={"Origin": "http://localhost:9999"}).status_code == 403
    assert counts(runtime) == before


def test_invalid_current_partition_is_typed_and_writes_nothing(runtime):
    client = client_for(runtime, server_errors=False)
    before = counts(runtime)
    partition = dict(account_kind="user_declared", account_id="", instrument_id="okx:BTC-USDT-SWAP", profile_ref="server-profile")
    response = client.get(BASE + "/current", params=partition)
    assert response.status_code == 422
    assert response.json()["detail"] == "decision_settings_partition_invalid"
    assert counts(runtime) == before


def test_cas_and_request_payload_conflicts_leave_all_rows_unchanged(runtime):
    value = command(runtime).model_dump(mode="json")
    client = client_for(runtime)
    assert client.post(BASE + "/commands", json=value).status_code == 200
    before = counts(runtime)
    stale = {**value, "request_id": "stale-save"}
    response = client.post(BASE + "/commands", json=stale)
    assert (response.status_code, response.json()["detail"]) == (409, "decision_settings_state_conflict")
    changed = {**value, "expected_state_version": 1}
    response = client.post(BASE + "/commands", json=changed)
    assert (response.status_code, response.json()["detail"]) == (409, "decision_settings_request_conflict")
    assert counts(runtime) == before


def test_options_use_authenticated_saved_contract_and_never_supply_balances(runtime):
    client = client_for(runtime)
    with runtime.database.session() as session:
        row = session.get(RawCaptureRecord, runtime.capture_id)
        original_ref = row.body_ref
        row.body_ref = None
    empty = client.get(BASE + "/options")
    assert empty.status_code == 200
    assert empty.json()["availability"] == "unavailable"
    assert empty.json()["reason"] == "decision_settings_instrument_original_unavailable"
    with runtime.database.session() as session:
        session.get(RawCaptureRecord, runtime.capture_id).body_ref = original_ref
    value = command(runtime)
    options = client.get(BASE + "/options").json()
    assert options["owner_id"] == OWNER
    assert options["availability"] == "available"
    assert len(options["instruments"]) == 1
    assert options["instruments"][0]["instrument"] == runtime.source.metadata_contract_reference(runtime.capture_id).model_dump(mode="json")
    assert options["instruments"][0]["instrument_id"] == "okx:BTC-USDT-SWAP"
    assert options["paper_available"] is False
    assert options["paper_reason"] == "decision_settings_paper_account_unavailable"
    assert options["declared_account_id"].startswith("declared_")


def test_foreign_owner_and_claimed_verified_balance_are_rejected(runtime):
    value = command(runtime).model_dump(mode="json")
    client = client_for(runtime)
    before = counts(runtime)
    value["values"]["account_snapshot"]["owner_id"] = "other-owner"
    assert client.post(BASE + "/commands", json=value).status_code == 403
    value["values"]["account_snapshot"].update(owner_id=OWNER, source="verified", original={"ref": "not-real", "hash": "a" * 64})
    assert client.post(BASE + "/commands", json=value).status_code == 409
    assert counts(runtime) == before


def test_prepare_authenticates_real_archived_metadata_before_settings_writes(runtime, monkeypatch):
    async def collected(*, inst_id, kind):
        assert inst_id == "BTC-USDT-SWAP" and kind == "metadata"
        # Network boundary only: the fixture is already a captured original.
        # A competing writer can acquire immediately during source collection.
        from packages.kernel.decision_hub_kernel.persistence.original_preparation import begin_immediate
        with runtime.database.session() as session:
            begin_immediate(session)
        return runtime.capture_id

    monkeypatch.setattr(runtime.source, "collect_one", collected)
    client = client_for(runtime)
    before = counts(runtime)
    response = client.post(BASE + "/instruments/prepare", json={"instrument_id": "okx:BTC-USDT-SWAP"})
    assert response.status_code == 200, response.text
    value = response.json()
    assert value["instrument"]["ref"] == "perp_metadata_contract:" + runtime.capture_id
    assert value["contract"]["ct_val"] == "0.01"
    assert value["contract"]["lot_sz"] == "0.01"
    assert counts(runtime) == before
    assert client.get(BASE + "/options").json()["instruments"][0]["instrument"] == value["instrument"]


def test_newer_metadata_wins_old_run_contract_and_corruption_never_falls_back(runtime):
    old = command(runtime)
    service(runtime).save(OWNER, old)
    with runtime.database.session() as session:
        original = session.get(RawCaptureRecord, runtime.capture_id)
        copied = {column.name: getattr(original, column.name) for column in RawCaptureRecord.__table__.columns}
        copied.update(capture_id="newer-metadata", context_id=original.context_id[:-32] + "2" * 32,
                      started_at=NOW + timedelta(seconds=1), completed_at=NOW + timedelta(seconds=1), recorded_at=NOW + timedelta(seconds=1))
        session.add(RawCaptureRecord(**copied))
    client = client_for(runtime)
    selected = client.get(BASE + "/options").json()["instruments"][0]
    assert selected["instrument"]["ref"] == "perp_metadata_contract:newer-metadata"
    assert selected["instrument"] != old.values.instrument.model_dump(mode="json")
    assert client.get(BASE + "/current", params=old.partition.model_dump()).json()["revision"]["values"]["instrument"] == old.values.instrument.model_dump(mode="json")
    with runtime.database.session() as session:
        session.get(RawCaptureRecord, "newer-metadata").body_hash = "0" * 64
    unavailable = client.get(BASE + "/options").json()
    assert unavailable["availability"] == "unavailable"
    assert unavailable["reason"] == "decision_settings_instrument_original_unavailable"


def test_reads_are_partitioned_by_signed_owner_and_missing_paper_is_unavailable(runtime):
    value = command(runtime).model_dump(mode="json")
    foreign = {**value, "request_id": "foreign-save"}
    foreign["values"]["account_snapshot"]["owner_id"] = "foreign-owner"
    from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import DecisionSettingsCommandV1
    service(runtime).save("foreign-owner", DecisionSettingsCommandV1.model_validate(foreign))
    client = client_for(runtime)
    assert client.get(BASE + "/current", params=value["partition"]).json() is None
    assert client.get(BASE + "/commands/foreign-save").status_code == 404
    value["request_id"] = "paper-forgery"
    value["partition"]["account_kind"] = "paper"
    value["values"]["account_snapshot"].update(owner_id=OWNER, account_kind="paper")
    before = counts(runtime)
    response = client.post(BASE + "/commands", json=value)
    assert response.status_code == 409
    assert response.json()["detail"] == "perp_paper_account_missing"
    assert counts(runtime) == before


def test_existing_real_paper_account_is_available_and_client_finances_cannot_replace_ledger(runtime):
    from decimal import Decimal
    from packages.kernel.decision_hub_kernel.application.perpetual_paper import CreatePaperAccount, PerpetualPaperService
    initial = command(runtime)
    paper = PerpetualPaperService(runtime.database, runtime.source, now=lambda: NOW)
    paper.create_account(OWNER, CreatePaperAccount(request_id="create-paper-settings", account_id="declared-account", instrument_refs=(initial.values.instrument,), profile=initial.values.execution_profile, initial_cash_usdt=Decimal("123"), simulation_acknowledged=True, execution_mode="manual_after_visible"))
    client = client_for(runtime)
    options = client.get(BASE + "/options").json()
    assert options["paper_available"] is True
    assert options["paper_reason"] is None
    selected = options["paper_accounts"][0]
    assert selected["account_snapshot"]["equity"] == "123"
    value = initial.model_dump(mode="json")
    value.update(request_id="paper-risk-save", partition=selected["partition"])
    value["values"].update(instrument=selected["position_context"]["instrument_ref"], account_snapshot=selected["account_snapshot"], position_context=selected["position_context"])
    value["values"]["utility_policy"].update(account_ref=selected["account_snapshot"]["original"], position_scenario="flat")
    saved = client.post(BASE + "/commands", json=value)
    assert saved.status_code == 200, saved.text
    assert client.get(BASE + "/current", params=value["partition"]).json()["revision"]["values"]["account_snapshot"]["equity"] == "123"
    before = counts(runtime)
    value.update(request_id="paper-forged-funds", expected_state_version=1)
    value["values"]["account_snapshot"]["equity"] = "999"
    response = client.post(BASE + "/commands", json=value)
    assert (response.status_code, response.json()["detail"]) == (409, "decision_settings_paper_original_mismatch")
    assert counts(runtime) == before
    value["request_id"] = "paper-foreign-policy-account"
    value["values"]["account_snapshot"]["equity"] = "123"
    value["values"]["utility_policy"]["account_ref"] = {"ref": "foreign-account", "hash": "f" * 64}
    response = client.post(BASE + "/commands", json=value)
    assert (response.status_code, response.json()["detail"]) == (409, "decision_settings_paper_policy_account_mismatch")
    assert counts(runtime) == before

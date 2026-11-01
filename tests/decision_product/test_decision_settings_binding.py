"""Real contract originals and server policy through settings' SQLite writer."""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
    DecisionSettingsCommandV1,
)
from packages.kernel.decision_hub_kernel.application.decision_execution_profile import (
    paper_execution_profile_reference,
)
from packages.kernel.decision_hub_kernel.application.decision_settings import (
    DecisionSettingsService,
)
from packages.kernel.decision_hub_kernel.application.okx_perpetual_source import OKXPerpetualSource
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    OutboxRecord,
    ProductCommandRecord,
    RawCaptureRecord,
    RunRecord,
)
from packages.kernel.decision_hub_kernel.persistence.decision_settings_records import (
    DecisionSettingsHeadRecord,
    DecisionSettingsRevisionRecord,
)
from packages.kernel.decision_hub_kernel.persistence.original_preparation import begin_immediate
from packages.kernel.decision_hub_kernel.ports.action_quote import CaptureOriginal
from packages.kernel.decision_hub_kernel.ports.okx_perpetual_original import VERSION, public_url
from packages.provider_adapters.research.raw_capture import RawBodyArchive

MODULE = "packages.kernel.decision_hub_kernel.application.decision_settings_binding"
OWNER = "binding-owner"
NOW = datetime(2026, 10, 19, tzinfo=UTC)
INSTRUMENT = "okx:BTC-USDT-SWAP"


def api():
    assert importlib.util.find_spec(MODULE) is not None, "real settings binding reader is missing"
    return importlib.import_module(MODULE)


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "data"))
    db = Database(f"sqlite+pysqlite:///{(tmp_path / 'binding.sqlite3').as_posix()}")
    db.create_all()
    archive = RawBodyArchive(tmp_path / "raw")
    raw = json.dumps(
        {
            "code": "0",
            "data": [
                {
                    "instType": "SWAP",
                    "instId": "BTC-USDT-SWAP",
                    "ctType": "linear",
                    "instFamily": "BTC-USDT",
                    "uly": "BTC-USDT",
                    "baseCcy": "",
                    "quoteCcy": "",
                    "settleCcy": "USDT",
                    "ctVal": "0.01",
                    "ctMult": "1",
                    "ctValCcy": "BTC",
                    "lotSz": "0.01",
                    "minSz": "0.01",
                    "tickSz": "0.1",
                    "state": "live",
                }
            ],
        },
        separators=(",", ":"),
    ).encode()
    digest = hashlib.sha256(raw).hexdigest()
    capture = CaptureOriginal(
        "binding-metadata",
        f"{VERSION}:BTC-USDT-SWAP:metadata:" + "1" * 32,
        public_url("BTC-USDT-SWAP", "metadata"),
        200,
        f"raw://sha256/{digest}",
        digest,
        len(raw),
        VERSION,
        None,
        NOW,
        NOW,
        NOW,
    )
    archive.put(raw)
    with db.session() as session:
        session.add(RawCaptureRecord(**asdict(capture), media_type="application/json"))
        session.add(
            RunRecord(
                run_id="binding-run",
                event_id="test",
                status="running",
                created_at=NOW,
                updated_at=NOW,
            )
        )
    yield SimpleNamespace(
        database=db,
        archive=archive,
        source=OKXPerpetualSource(db, archive),
        capture_id=capture.capture_id,
    )
    db.engine.dispose()


def command(runtime):
    prepared = runtime.source.prepare_contract(
        inst_id="BTC-USDT-SWAP",
        capture_ids={"metadata": runtime.capture_id},
        cutoff=NOW,
    )

    def guard(session):
        assert session.get(RunRecord, "binding-run").status == "running"

    with runtime.database.session() as session:
        begin_immediate(session)
        instrument = runtime.source.save_prepared_in_session(
            session,
            run_id="binding-run",
            prepared=prepared,
            writer_guard=guard,
        ).model_dump(mode="json")
    profile = paper_execution_profile_reference(INSTRUMENT).model_dump(mode="json")
    return DecisionSettingsCommandV1.model_validate(
        {
            "schema_version": "decision-settings-command.v1",
            "request_id": "binding-save",
            "expected_state_version": 0,
            "operation": "upsert",
            "partition": {
                "account_kind": "user_declared",
                "account_id": "declared-account",
                "instrument_id": INSTRUMENT,
                "profile_ref": profile["ref"],
            },
            "values": {
                "instrument": instrument,
                "execution_profile": profile,
                "account_snapshot": {
                    "schema_version": "account-snapshot.v2",
                    "owner_id": OWNER,
                    "account_kind": "user_declared",
                    "account_id": "declared-account",
                    "source": "unknown",
                    "as_of": NOW.isoformat().replace("+00:00", "Z"),
                    "account_mode": "unknown",
                    "positions": None,
                    "collateral": None,
                    "valuation_ccy": "USDT",
                    "equity": None,
                    "available_margin": None,
                    "fee_tier": None,
                    "original": None,
                    "limits": {
                        key: None
                        for key in (
                            "max_gross_notional",
                            "max_reserved_margin",
                            "max_fee_buffer",
                            "max_stress_loss",
                        )
                    },
                },
                "position_context": {
                    "schema_version": "position-context.v2",
                    "instrument_id": INSTRUMENT,
                    "instrument_ref": instrument,
                    "state": "unknown",
                    "signed_contracts": None,
                    "entry_price": None,
                    "isolated_margin": None,
                    "margin_mode": "unknown",
                    "position_mode": "unknown",
                    "hedge_leg": None,
                    "leverage": None,
                    "liquidation_estimate": None,
                    "source": "unknown",
                    "ref": None,
                    "hash": None,
                    "account_ref": None,
                },
                "utility_policy": {
                    "schema_version": "utility-policy.v2",
                    "policy_ref": "user-risk.v1",
                    "hash": "1" * 64,
                    "source": "user",
                    "objective": "mean_net",
                    "risk_penalty": "0",
                    "max_gross_notional": None,
                    "max_planned_loss": None,
                    "tail_constraints": None,
                    "position_scenario": "unknown",
                    "margin_buffer_policy": None,
                    "allowed_leverage": None,
                    "allowed_sides": None,
                    "account_ref": None,
                },
            },
        }
    )


def service(runtime, reader=None):
    binding = reader or api().DecisionSettingsBindingReader(runtime.source, now=lambda: NOW)
    return DecisionSettingsService(runtime.database, now=lambda: NOW, binding_reader=binding)


def counts(runtime):
    with runtime.database.session() as session:
        return tuple(
            session.scalar(select(func.count()).select_from(model))
            for model in (
                DecisionSettingsRevisionRecord,
                DecisionSettingsHeadRecord,
                ProductCommandRecord,
                OutboxRecord,
            )
        )


def test_real_contract_policy_and_declared_unknown_survive_settings_restart(runtime):
    api()
    value = command(runtime)
    saved = service(runtime).save(OWNER, value)
    reopened = Database(runtime.database.engine.url.render_as_string(hide_password=False))
    try:
        binding = api().DecisionSettingsBindingReader(
            OKXPerpetualSource(reopened, runtime.archive),
            now=lambda: NOW,
        )
        fresh = DecisionSettingsService(reopened, now=lambda: NOW, binding_reader=binding)
        current = fresh.current(OWNER, value.partition)
        assert fresh.receipt(OWNER, value.request_id) == fresh.save(OWNER, value) == saved
        assert current.revision.values.account_snapshot.equity is None
        assert current.revision.values.position_context.signed_contracts is None
        assert current.revision.values.position_context.leverage is None
        assert current.reevaluation_intent_state == saved.reevaluation_state == "pending"
        assert fresh.pending()[0].revision_id == saved.revision.ref
        assert counts(runtime) == (1, 1, 1, 0)
    finally:
        reopened.engine.dispose()


def forged(value, kind):
    raw = value.model_dump(mode="json")
    account = raw["values"]["account_snapshot"]
    position = raw["values"]["position_context"]
    if kind == "scope":
        raw["partition"]["instrument_id"] = position["instrument_id"] = "okx:ETH-USDT-SWAP"
    elif kind == "instrument_hash":
        raw["values"]["instrument"]["hash"] = position["instrument_ref"]["hash"] = "0" * 64
    elif kind == "profile_hash":
        raw["values"]["execution_profile"]["hash"] = "0" * 64
    elif kind == "owner":
        account["owner_id"] = "other-owner"
    elif kind in {"verified_account", "external_account"}:
        account.update(
            source="verified" if kind == "verified_account" else "user_declared",
            original={"ref": "foreign-account", "hash": "f" * 64},
        )
    elif kind == "verified_position":
        position.update(source="verified", ref="foreign-position", hash="f" * 64)
    elif kind == "nested_verified_position":
        account.update(
            source="user_declared",
            positions=[
                {
                    **position,
                    "source": "verified",
                    "ref": "foreign-position",
                    "hash": "f" * 64,
                }
            ],
        )
    elif kind == "future":
        account["as_of"] = (NOW + timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
    return DecisionSettingsCommandV1.model_validate(raw)


@pytest.mark.parametrize(
    "kind",
    [
        "scope",
        "instrument_hash",
        "profile_hash",
        "owner",
        "verified_account",
        "external_account",
        "verified_position",
        "nested_verified_position",
        "future",
    ],
)
def test_wrong_bindings_and_forged_account_sources_write_nothing(runtime, kind):
    api()
    value = forged(command(runtime), kind)
    with pytest.raises((ValueError, PermissionError)):
        service(runtime).save(OWNER, value)
    assert counts(runtime) == (0, 0, 0, 0)


def test_paper_without_real_perpetual_account_reader_is_explicitly_unavailable(runtime):
    api()
    raw = command(runtime).model_dump(mode="json")
    raw["partition"]["account_kind"] = "paper"
    raw["values"]["account_snapshot"].update(
        account_kind="paper",
        source="paper_policy",
        original={"ref": "client-paper", "hash": "f" * 64},
        equity="10000",
    )
    with pytest.raises(ValueError, match="^decision_settings_paper_account_reader_unavailable$"):
        service(runtime).save(OWNER, DecisionSettingsCommandV1.model_validate(raw))
    assert counts(runtime) == (0, 0, 0, 0)


def test_source_row_change_after_binding_is_rejected_before_settings_write(runtime):
    binding = api().DecisionSettingsBindingReader(runtime.source, now=lambda: NOW)
    value = command(runtime)

    def changed_reader(session, owner, cmd):
        binding(session, owner, cmd)
        with runtime.database.session() as other:
            other.get(RawCaptureRecord, runtime.capture_id).byte_count += 1

    with pytest.raises(ValueError, match="^original_preparation_readset_changed$"):
        service(runtime, changed_reader).save(OWNER, value)
    assert counts(runtime) == (0, 0, 0, 0)


def test_remove_reads_owned_current_original_without_external_sources(runtime, monkeypatch):
    api()
    value = command(runtime)
    saved = service(runtime).save(OWNER, value)

    def unavailable(*_args, **_kwargs):
        raise AssertionError("remove must not reread external metadata")

    monkeypatch.setattr(runtime.source, "read_contract_in_session", unavailable)
    raw = value.model_dump(mode="json")
    raw.update(request_id="remove-owned", expected_state_version=1, operation="remove", values=None)
    remove = DecisionSettingsCommandV1.model_validate(raw)
    with pytest.raises(ValueError, match="^decision_settings_remove_missing$"):
        service(runtime).save("other-owner", remove)
    assert counts(runtime) == (1, 1, 1, 0)
    removed = service(runtime).save(OWNER, remove)
    assert removed.state_version == 2
    assert service(runtime).current(OWNER, value.partition).revision.values is None
    assert service(runtime).receipt(OWNER, value.request_id) == saved
    assert service(runtime).pending()[0].revision_id == removed.revision.ref
    stale = {**raw, "request_id": "stale-remove"}
    with pytest.raises(ValueError, match="^decision_settings_state_conflict$"):
        service(runtime).save(OWNER, DecisionSettingsCommandV1.model_validate(stale))
    assert counts(runtime) == (2, 1, 2, 0)

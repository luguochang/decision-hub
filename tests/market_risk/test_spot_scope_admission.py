"""Isolated synthetic captures verify gates, not live model or product qualification."""

from __future__ import annotations

import asyncio
import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from types import SimpleNamespace

import pytest
from alembic import command
from sqlalchemy import inspect

from packages.kernel.decision_hub_kernel.application.market_risk_data import MarketRiskDataService
from packages.kernel.decision_hub_kernel.application.market_scope import (
    outcome_scope_enabled,
    require_research_scope,
    research_market_catalog,
)
from packages.kernel.decision_hub_kernel.application.spot_scope_admission import (
    EffectiveSpotRecipe,
    HashedResource,
    SpotCapabilityRoute,
    SpotHostReadiness,
    SpotScopeAdmissionService,
)
from packages.kernel.decision_hub_kernel.persistence.db import Database, RunRecord
from packages.kernel.decision_hub_kernel.persistence.risk_records import MarketCandleRecord
from packages.kernel.decision_hub_kernel.persistence.spot_scope_admission import (
    SpotResearchAdmissionRecord,
)
from packages.kernel.decision_hub_kernel.ports.market_path import spot_scope
from packages.provider_adapters.market.okx_path import OKXMarketPathAdapter
from packages.provider_adapters.research.raw_capture import RawBodyArchive
from tests.market_risk.test_market_data import NOW, PublicFixture


def study(instrument="BTC-USDT", *, horizons="24h"):
    return SimpleNamespace(
        market="crypto", instrument=instrument, identity_basis="explicit", horizon_scope=horizons
    )


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "data"))
    db = Database(f"sqlite+pysqlite:///{tmp_path / 'admission.sqlite3'}")
    db.create_all()
    now = [NOW]
    resources = {}

    def resource(name):
        body = f"synthetic installed resource: {name}".encode()
        resources[name] = body
        return HashedResource(ref=name, sha256=hashlib.sha256(body).hexdigest())

    def runtime(instrument="BTC-USDT", *, capture=False):
        scope = spot_scope(instrument)
        data = MarketRiskDataService(
            db,
            RawBodyArchive(tmp_path / "raw"),
            tmp_path / "features",
            adapter=OKXMarketPathAdapter(fetcher=PublicFixture(now).fetch),
            scopes=(scope,),
            clock=lambda: now[0],
            enabled=True,
        )
        if capture:
            assert asyncio.run(data.refresh_due()) == 1
        asset = instrument.split("-")[0].lower()
        routes = (
            SpotCapabilityRoute(
                capability_id="macro",
                asset_mode="global_macro",
                manifest=resource("macro-manifest"),
                route=resource("macro-route"),
            ),
            SpotCapabilityRoute(
                capability_id="market",
                asset_mode="instrument",
                instrument=instrument,
                manifest=resource(f"{asset}-manifest"),
                route=resource(f"{asset}-route"),
            ),
        )
        effective = EffectiveSpotRecipe(
            research_scope_id=f"{asset}-spot-macro.v1",
            instrument=instrument,
            recipe=resource(f"{asset}-recipe"),
            pack=resource("crypto-macro-pack"),
            installed_pointer=resource("installed-pointer"),
            plugin_build_hash="f" * 64,
            required_capability_ids=("macro", "market"),
            routes=routes,
        )
        host = SpotHostReadiness(
            ready=True,
            checked_at=now[0],
            installed_pointer_hash=effective.installed_pointer.sha256,
            plugin_build_hash=effective.plugin_build_hash,
            receipt=resource("host-receipt"),
        )
        service = SpotScopeAdmissionService(
            db,
            data.store,
            read_resource=resources.__getitem__,
            effective_verifier=lambda candidate: candidate == effective,
            route_verifier=lambda route, candidate: (
                route in effective.routes and candidate == effective
            ),
            enabled=True,
            clock=lambda: now[0],
        )
        return service, effective, host

    yield db, now, resources, resource, runtime
    db.engine.dispose()


def test_catalog_is_separate_default_closed_and_old_daily_outcome_survives():
    items = {item.instrument: item for item in research_market_catalog().items}
    assert items["BTC-USDT-SWAP"].research_enabled
    for instrument in ("BTC-USDT", "ETH-USDT"):
        item = items[instrument]
        assert item.instrument_kind == "spot" and item.venue == "okx"
        assert item.domain_pack_ref == "crypto_macro.v1"
        assert item.supported_horizons == ["24h", "30m", "72h"]
        assert not item.research_enabled and not item.outcome_enabled
        with pytest.raises(ValueError, match="study_scope_not_enabled"):
            require_research_scope(study(instrument))
    assert outcome_scope_enabled("crypto", "BTC-USDT")
    assert not outcome_scope_enabled("crypto", "ETH-USDT")


@pytest.mark.parametrize("instrument", ["BTC-USDT", "ETH-USDT"])
def test_first_trial_needs_no_previous_run_but_never_qualifies_release(prepared, instrument):
    db, _, _, _, runtime = prepared
    service, effective, host = runtime(instrument, capture=True)
    token = service.approve_trial(request_id=f"trial-{instrument}", effective=effective, host=host)
    assert not token.product_release_qualified
    assert require_research_scope(study(instrument), spot_admission=token).instrument == instrument
    with pytest.raises(ValueError, match="study_scope_not_enabled"):
        require_research_scope(
            study("ETH-USDT" if instrument == "BTC-USDT" else "BTC-USDT"), spot_admission=token
        )
    with pytest.raises(ValueError, match="research_scope_horizon_not_enabled"):
        require_research_scope(study(instrument, horizons="1y"), spot_admission=token)
    with pytest.raises(ValueError, match="research_scope_pack_mismatch"):
        require_research_scope(
            study(instrument), spot_admission=token, domain_pack_ref="gold_macro.v1"
        )
    repeated = service.approve_trial(
        request_id=f"trial-{instrument}", effective=effective, host=host
    )
    assert repeated.admission_id == token.admission_id
    with db.session() as session:
        assert session.query(RunRecord).count() == 0
        assert session.query(SpotResearchAdmissionRecord).count() == 1


@pytest.mark.parametrize("gate", ["disabled", "recipe", "route", "host", "future", "stale", "hash"])
def test_missing_current_gates_are_closed_without_writing_admission(prepared, gate):
    db, now, resources, _, runtime = prepared
    service, effective, host = runtime()
    expected = {
        "disabled": "spot_research_disabled",
        "recipe": "spot_effective_recipe_not_verified",
        "route": "spot_asset_route_implementation_not_verified",
        "host": "spot_effective_host_not_ready",
        "future": "spot_effective_host_not_ready",
        "stale": "spot_effective_host_not_ready",
        "hash": "spot_effective_resource_hash_mismatch",
    }[gate]
    if gate == "disabled":
        service.enabled = False
    elif gate == "recipe":
        service.effective_verifier = None
    elif gate == "route":
        service.route_verifier = None
    elif gate == "host":
        host = host.model_copy(update={"plugin_build_hash": "e" * 64})
    elif gate == "future":
        host = host.model_copy(update={"checked_at": now[0] + timedelta(seconds=1)})
    elif gate == "stale":
        now[0] += timedelta(seconds=180)
    else:
        resources[effective.recipe.ref] = b"changed source"
    with pytest.raises(ValueError, match=expected):
        service.approve_trial(request_id=gate, effective=effective, host=host)
    with db.session() as session:
        assert session.query(SpotResearchAdmissionRecord).count() == 0


def test_declared_eth_cannot_reuse_btc_asset_sensitive_routes(prepared):
    _, _, _, _, runtime = prepared
    _, effective, _ = runtime()
    raw = effective.model_dump(mode="json")
    raw.update(instrument="ETH-USDT", research_scope_id="eth-spot-macro.v1")
    with pytest.raises(ValueError, match="spot_recipe_asset_routes_incomplete"):
        EffectiveSpotRecipe.model_validate(raw)


def test_readiness_and_recipe_do_not_replace_scope_specific_originals(prepared):
    db, _, _, _, runtime = prepared
    btc_service, btc, host = runtime(capture=True)
    eth_service, eth, eth_host = runtime("ETH-USDT")
    with pytest.raises(ValueError, match="spot_current_original_path_not_fresh"):
        eth_service.approve_trial(request_id="no-eth-source", effective=eth, host=eth_host)
    with db.session() as session:
        row = (
            session.query(MarketCandleRecord)
            .filter_by(scope_id=spot_scope("BTC-USDT").scope_id)
            .first()
        )
        session.delete(row)
    with pytest.raises(ValueError, match="spot_24h_original_path_not_complete"):
        btc_service.approve_trial(request_id="btc-gap", effective=btc, host=host)


def test_saved_admission_expires_changes_with_installed_recipe_and_detects_tampering(prepared):
    db, now, _, resource, runtime = prepared
    service, effective, host = runtime(capture=True)
    token = service.approve_trial(request_id="durable", effective=effective, host=host)
    changed = effective.model_copy(update={"recipe": resource("new-spot-recipe")})
    service.effective_verifier = lambda _: True
    service.route_verifier = lambda *_: True
    with pytest.raises(ValueError, match="spot_admission_effective_binding_changed"):
        service.resolve(token.admission_id, effective=changed, host=host)
    now[0] += timedelta(seconds=180)
    assert not token.matches(study())
    fresh_host = host.model_copy(update={"checked_at": now[0]})
    with pytest.raises(ValueError, match="spot_admission_expired"):
        service.resolve(token.admission_id, effective=effective, host=fresh_host)
    now[0] -= timedelta(seconds=180)
    with db.session() as session:
        session.get(SpotResearchAdmissionRecord, token.admission_id).proof_json = "{}"
    with pytest.raises(ValueError, match="spot_admission_proof_hash_mismatch"):
        service.resolve(token.admission_id, effective=effective, host=host)


def test_one_poll_delay_is_allowed_but_source_clock_bounds_token_expiry(prepared):
    _, now, _, _, runtime = prepared
    service, effective, host = runtime(capture=True)
    latest_close = now[0].replace(second=0)
    now[0] += timedelta(seconds=60)
    host = host.model_copy(update={"checked_at": now[0]})
    token = service.approve_trial(request_id="one-poll-delay", effective=effective, host=host)
    assert token.expires_at == latest_close + timedelta(seconds=180)
    now[0] = token.expires_at
    assert not token.matches(study())
    host = host.model_copy(update={"checked_at": now[0]})
    with pytest.raises(ValueError, match="spot_current_original_path_not_fresh"):
        service.approve_trial(request_id="stale-data", effective=effective, host=host)


def test_eth_declared_route_still_requires_actual_adapter_support(prepared):
    db, _, _, _, runtime = prepared
    service, effective, host = runtime("ETH-USDT")
    service.route_verifier = lambda route, _: route.asset_mode == "global_macro"
    with pytest.raises(ValueError, match="spot_asset_route_implementation_not_verified"):
        service.approve_trial(request_id="eth-btc-only-adapter", effective=effective, host=host)
    with db.session() as session:
        assert session.query(SpotResearchAdmissionRecord).count() == 0


def test_concurrent_request_retries_preserve_one_admission_receipt(prepared):
    db, _, _, _, runtime = prepared
    service, effective, host = runtime(capture=True)

    def approve():
        return service.approve_trial(request_id="concurrent", effective=effective, host=host)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(lambda _: approve(), range(2)))
    assert first.admission_id == second.admission_id
    with db.session() as session:
        assert session.query(SpotResearchAdmissionRecord).count() == 1


def test_real_alembic_0073_to_0074_is_additive_and_matches_orm(tmp_path):
    db = Database(f"sqlite+pysqlite:///{tmp_path / 'migration.sqlite3'}")
    config = db._alembic_config()
    config.attributes["configure_logger"] = False
    command.upgrade(config, "0073_market_risk_ledger")
    previous = set(inspect(db.engine).get_table_names())
    command.upgrade(config, "0074_spot_scope_admissions")
    current = inspect(db.engine)
    assert set(current.get_table_names()) - previous == {"spot_research_scope_admissions"}
    table = SpotResearchAdmissionRecord.__table__
    columns = current.get_columns(table.name)
    assert {item["name"] for item in columns} == set(table.columns.keys())
    assert {item["name"]: item["nullable"] for item in columns} == {
        name: column.nullable for name, column in table.columns.items()
    }
    assert {item["name"] for item in current.get_indexes(table.name)} == {
        item.name for item in table.indexes
    }
    assert current.get_pk_constraint(table.name)["constrained_columns"] == ["admission_id"]
    assert current.get_unique_constraints(table.name)[0]["column_names"] == ["request_id"]
    with pytest.raises(RuntimeError, match="audit_preservation_required"):
        command.downgrade(config, "0073_market_risk_ledger")
    db.engine.dispose()

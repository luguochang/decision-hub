"""Private synthetic captures test the real Store/Gateway, not paid research."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from packages.contracts_py.decision_hub_contracts import (
    ResearchCapabilityManifest,
    ResearchCapabilityQuery,
)
from packages.kernel.decision_hub_kernel.application.market_risk_data import MarketRiskDataService
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchCapabilityError,
    ResearchCapabilityGatewayService,
)
from packages.kernel.decision_hub_kernel.application.spot_scope_admission import (
    HashedResource,
    SpotCapabilityRoute,
)
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.kernel.decision_hub_kernel.persistence.risk_records import MarketCandleRecord
from packages.kernel.decision_hub_kernel.ports.market_path import spot_scope
from packages.provider_adapters.market.okx_path import OKXMarketPathAdapter
from packages.provider_adapters.market.spot_research import (
    CAPABILITY,
    IMPLEMENTATION,
    SpotMarketFactsAdapter,
)
from packages.provider_adapters.research.raw_capture import RawBodyArchive
from tests.market_risk.test_market_data import NOW, PublicFixture


def manifest():
    return ResearchCapabilityManifest(
        schema_version="research-capability-manifest.v1",
        capability_id=CAPABILITY,
        version="1.0.0",
        kind="python_adapter",
        implementation_ref=IMPLEMENTATION,
        input_schema_ref="research-capability-query.v1",
        output_schema_ref="research-capability-result.v1",
        permissions=["storage:read"],
        allowed_domains=["www.okx.com"],
        timeout_seconds=30,
        cost_policy_ref="local-ledger-read-no-provider-fee.v1",
        freshness_policy_ref="closed-1m-180s.v1",
        license_status="approved",
        audit_status="approved",
        replay_policy="archive_required",
        secret_policy="none",
        provider_routes=[],
    )


def query(instrument="BTC-USDT", **updates):
    return ResearchCapabilityQuery.model_validate(
        {
            "schema_version": "research-capability-query.v1",
            "request_id": "spot-call-1",
            "capability_id": CAPABILITY,
            "requirement_id": "SPOT_A",
            "query": "read exact spot path",
            "target_url": None,
            "symbols": [instrument],
            "fields": ["spot_price", "spot_volume", "return_30m", "path"],
            "allowed_domains": ["www.okx.com"],
            "max_results": 1,
            "max_cost_usd": 0,
            "research_session_id": "private-spot-session",
            "round": 1,
            "mode": "live",
            "observed_at": NOW,
            "cutoff_at": NOW + timedelta(minutes=2),
            "deadline_at": NOW + timedelta(minutes=2),
            **updates,
        }
    )


@pytest.fixture
def stored(tmp_path, monkeypatch):
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "private-data"))
    db = Database(f"sqlite+pysqlite:///{tmp_path / 'spot.sqlite3'}")
    db.create_all()
    time = [NOW]
    archive = RawBodyArchive(tmp_path / "raw")
    transports, adapters = {}, {}
    for instrument, price in (("BTC-USDT", "60000"), ("ETH-USDT", "2500")):
        transport = PublicFixture(time)
        transport.bump = Decimal(price) - 100
        data = MarketRiskDataService(
            db,
            archive,
            tmp_path / (instrument + "-features"),
            adapter=OKXMarketPathAdapter(fetcher=transport.fetch),
            scopes=(spot_scope(instrument),),
            enabled=True,
            clock=lambda: time[0],
        )
        assert asyncio.run(data.refresh_due()) == 1
        transports[instrument], adapters[instrument] = (
            transport,
            SpotMarketFactsAdapter(
                data,
                clock=lambda: time[0],
            ),
        )
    yield db, archive, time, transports, adapters
    db.engine.dispose()


def test_exact_btc_eth_originals_real_gateway_path_and_units(stored):
    _db, _archive, time, transports, adapters = stored
    for instrument, price, unit in (("BTC-USDT", "60000", "btc"), ("ETH-USDT", "2500", "eth")):
        adapter = adapters[instrument]
        gateway = ResearchCapabilityGatewayService(
            [manifest()],
            [adapter],
            enabled_capabilities=[CAPABILITY],
            clock=lambda: NOW,
        )
        calls = len(transports[instrument].calls)
        result = asyncio.run(gateway.execute(query(instrument)))
        assert len(transports[instrument].calls) == calls  # Read path makes no transport request.
        facts = {f.field: f for f in result.facts}
        assert facts["price"].value == price
        assert facts["volume"].unit == unit
        assert {f.instrument for f in result.facts} == {instrument}
        assert {f.metric_family for f in result.facts} == {"crypto.spot"}
        path = json.loads(facts["path"].value)
        assert path["window_status"] == "complete" and path["observed_count"] == 1440
        assert path["scope"]["instrument"] == instrument
        assert all(f.published_at is None and f.quality == "candidate" for f in result.facts)
        assert all(f.attributes["derivatives_available"] is False for f in result.facts)
        assert json.loads(result.evidence_candidates[0].excerpt)["crowding"] == "unknown"

    # A real second acquisition produces a nonterminating division ratio.
    # Constant-price inputs would miss the raw candle precision codec mismatch.
    time[0] += timedelta(minutes=1)
    transports["BTC-USDT"].bump += 1
    adapter = adapters["BTC-USDT"]
    assert asyncio.run(adapter.data.refresh_due()) == 1
    gateway = ResearchCapabilityGatewayService(
        [manifest()],
        [adapter],
        enabled_capabilities=[CAPABILITY],
        clock=lambda: time[0],
    )
    updated = asyncio.run(gateway.execute(query(observed_at=time[0])))
    ratio = next(f for f in updated.facts if f.field == "return_30m")
    assert Decimal(ratio.value) == Decimal(60001) / Decimal(60000) - 1
    assert abs(Decimal(ratio.value).as_tuple().exponent) > 18


def test_committed_read_permission_is_independent_of_http_acquisition(stored):
    _db, _archive, time, transports, adapters = stored
    original = adapters["ETH-USDT"]
    original.data.enabled = False
    calls = len(transports["ETH-USDT"].calls)
    denied = SpotMarketFactsAdapter(original.data, clock=lambda: time[0])
    with pytest.raises(ResearchCapabilityError, match="spot_research_scope_not_enabled"):
        asyncio.run(denied.execute(query("ETH-USDT")))
    reader = SpotMarketFactsAdapter(
        original.data, clock=lambda: time[0], read_enabled=True,
    )
    gateway = ResearchCapabilityGatewayService(
        [manifest()], [reader], enabled_capabilities=[CAPABILITY], clock=lambda: time[0],
    )
    result = asyncio.run(gateway.execute(query("ETH-USDT")))
    assert next(f for f in result.facts if f.field == "price").value == "2500"
    assert reader.route_material("ETH-USDT", manifest())["capability_id"] == CAPABILITY
    assert asyncio.run(original.data.refresh_due()) == 0
    assert len(transports["ETH-USDT"].calls) == calls


def test_alias_swaps_cross_asset_and_missing_fields_are_not_promoted(stored):
    _db, _archive, time, _transports, adapters = stored
    adapter = adapters["ETH-USDT"]
    for symbol in ("BTC", "ETH", "ETH/USDT", "ETH-USDT-SWAP", "BTC-USDT"):
        with pytest.raises(ResearchCapabilityError):
            asyncio.run(adapter.execute(query(symbol)))
    with pytest.raises(ResearchCapabilityError, match="spot_research_query_scope_invalid"):
        asyncio.run(adapter.execute(query("ETH-USDT", fields=["funding_rate"])))
    with pytest.raises(ResearchCapabilityError, match="spot_research_original_missing"):
        asyncio.run(
            adapter.execute(query("ETH-USDT", mode="replay", cutoff_at=NOW - timedelta(minutes=1)))
        )
    time[0] = NOW + timedelta(minutes=4)
    with pytest.raises(ResearchCapabilityError, match="spot_research_original_stale"):
        asyncio.run(
            adapter.execute(
                query(
                    "ETH-USDT",
                    cutoff_at=time[0] + timedelta(minutes=1),
                    deadline_at=time[0] + timedelta(minutes=1),
                )
            )
        )


def test_gap_does_not_claim_complete_path_and_original_tamper_is_rejected(stored):
    db, archive, _time, _transports, adapters = stored
    adapter = adapters["BTC-USDT"]
    with db.session() as session:
        rows = (
            session.query(MarketCandleRecord)
            .filter_by(scope_id=spot_scope("BTC-USDT").scope_id)
            .all()
        )
        middle = sorted(rows, key=lambda r: r.open_at)[700]
        session.delete(middle)
    result = asyncio.run(adapter.execute(query()))
    assert "path" not in {f.field for f in result.facts}
    assert json.loads(result.evidence_candidates[0].excerpt)["field_statuses"]["path"] == "unknown"
    with db.session() as session:
        row = (
            session.query(MarketCandleRecord)
            .filter_by(scope_id=spot_scope("BTC-USDT").scope_id)
            .first()
        )
        ref = row.capture_ref
    original = archive.read(ref)
    # Only the private synthetic capture is changed; exact stored capture hash must fail.
    source = adapter.data.store.archive

    class ChangedArchive:
        def read(self, candidate):
            return original + b" " if candidate == ref else source.read(candidate)

    adapter.data.store.archive = ChangedArchive()
    with pytest.raises(ResearchCapabilityError, match="spot_research_original_invalid"):
        asyncio.run(adapter.execute(query()))


def test_mr15_route_signature_binds_actual_manifest_asset_and_implementation(stored):
    _db, _archive, _time, _transports, adapters = stored
    adapter = adapters["ETH-USDT"]
    key = b"isolated-fixture-signing-key-32bytes-only"
    body = adapter.signed_route_receipt("ETH-USDT", manifest(), signing_key=key)
    resources = {
        "manifest": manifest().model_dump_json().encode(),
        "route": json.dumps(body, sort_keys=True).encode(),
    }
    route = SpotCapabilityRoute(
        capability_id=CAPABILITY,
        asset_mode="instrument",
        instrument="ETH-USDT",
        manifest=HashedResource(
            ref="manifest", sha256=hashlib.sha256(resources["manifest"]).hexdigest()
        ),
        route=HashedResource(ref="route", sha256=hashlib.sha256(resources["route"]).hexdigest()),
    )
    effective = SimpleNamespace(instrument="ETH-USDT")
    assert adapter.verify_admission_route(
        route, effective, read_resource=resources.__getitem__, signing_key=key
    )
    assert not adapter.verify_admission_route(
        route, effective, read_resource=resources.__getitem__, signing_key=b"x" * 32
    )
    assert not adapter.verify_admission_route(
        route,
        SimpleNamespace(instrument="BTC-USDT"),
        read_resource=resources.__getitem__,
        signing_key=key,
    )
    resources["route"] = resources["route"].replace(b"ETH-USDT", b"BTC-USDT")
    assert not adapter.verify_admission_route(
        route, effective, read_resource=resources.__getitem__, signing_key=key
    )

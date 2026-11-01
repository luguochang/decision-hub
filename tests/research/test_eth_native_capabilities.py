"""Finite ETH routing and original-computation controls; no external requests."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from apps.hub_worker.historical import HistoricalRefreshWorker
from apps.research_mcp.main import _provider_adapter_registry
from packages.kernel.decision_hub_kernel.application import daily_execution_binding as binding
from packages.kernel.decision_hub_kernel.application.data_jobs import DataJobService
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    CapabilityAdapterReturnCollector,
    ResearchCapabilityError,
)
from packages.kernel.decision_hub_kernel.persistence.db import Database, RawCaptureRecord, utcnow
from packages.provider_adapters.historical import HistoricalDataArchive, HistoricalDataJobRunner
from packages.provider_adapters.historical.gate_daily import GateDailyDataLoader
from packages.provider_adapters.historical.research import (
    HistoricalDataResearchAdapter,
    loaded_daily_implementation,
)
from packages.provider_adapters.market.crowding import CryptoCrowdingResearchAdapter
from packages.provider_adapters.market.gate_htx_research import (
    GateDerivativesResearchAdapter,
    HtxDerivativesResearchAdapter,
)
from packages.provider_adapters.market.spot_fallback import PublicSpotMarketResearchAdapter
from packages.provider_adapters.research.raw_capture import (
    CaptureBatch,
    CapturingAsyncClient,
    RawBodyArchive,
    capture_scope,
)
from tests.research.test_gate_htx_research import _query

RAW = (
    Path(__file__).parents[2]
    / "artifacts/market-risk-implementation-20261007/B2/eth-native05/public-raw01"
)


@pytest.mark.asyncio
@pytest.mark.parametrize("venue", ["gateio", "htx"])
async def test_eth_derivatives_replays_saved_public_body_with_exact_symbol(venue: str) -> None:
    calls: list[str] = []
    route = {
        "gateio": {"tickers": "gate_ticker", "contract_stats": "gate_stats"},
        "htx": {
            "detail/merged": "htx_ticker",
            "swap_funding_rate": "htx_funding",
            "swap_index": "htx_index",
            "swap_open_interest": "htx_oi",
            "swap_his_open_interest": "htx_history",
        },
    }[venue]

    async def fetch(url: str):
        calls.append(url)
        key = "contract" if venue == "gateio" else "contract_code"
        assert parse_qs(urlsplit(url).query)[key] == [
            "ETH_USDT" if venue == "gateio" else "ETH-USDT"
        ]
        name = next(value for key, value in route.items() if urlsplit(url).path.endswith(key))
        return json.loads((RAW / f"{name}.body").read_bytes())

    adapter = (
        GateDerivativesResearchAdapter(fetcher=fetch)
        if venue == "gateio"
        else HtxDerivativesResearchAdapter(fetcher=fetch)
    )
    query = _query().model_copy(update={"symbols": ["ETH-USDT-SWAP"]})
    result = await adapter.execute(query)
    assert len(calls) == (2 if venue == "gateio" else 5)
    assert {fact.instrument for fact in result.facts or []} == {"ETH-USDT-SWAP"}
    assert {fact.independence_group for fact in result.facts or []} == {result.provider}
    assert {fact.field for fact in result.facts or []} == set(query.fields)
    assert (
        2000 < float(next(f.value for f in result.facts or [] if f.field == "index_price")) < 4000
    )
    assert not any("BTC" in url for url in calls)
    if venue == "htx":
        mark = next(f for f in result.facts or [] if f.field == "mark_price")
        assert mark.attributes["proxy_kind"] == "latest_swap_price"

    async def wrong(url: str):
        payload = await fetch(url)
        if venue == "gateio" and urlsplit(url).path.endswith("tickers"):
            payload[0]["contract"] = "BTC_USDT"
        elif venue == "htx" and urlsplit(url).path.endswith("swap_funding_rate"):
            payload["data"]["contract_code"] = "BTC-USDT"
        return payload

    adapter.fetcher = wrong
    with pytest.raises(ResearchCapabilityError, match="contract does not match"):
        await adapter.execute(query)


@pytest.mark.asyncio
@pytest.mark.parametrize("venue", ["gateio", "htx"])
async def test_eth_crowding_replays_native_orderbook_as_explicit_proxy(venue: str) -> None:
    async def fetch(url: str):
        key = "contract" if venue == "gateio" else "contract_code"
        assert parse_qs(urlsplit(url).query)[key] == [
            "ETH_USDT" if venue == "gateio" else "ETH-USDT"
        ]
        assert "BTC" not in url
        name = "gate_book" if venue == "gateio" else "htx_book"
        return json.loads((RAW / f"{name}.body").read_bytes())

    adapter = CryptoCrowdingResearchAdapter(
        provider_id=f"{venue}-orderbook-public",
        exchange=venue,
        base_url="https://api.gateio.ws" if venue == "gateio" else "https://api.hbdm.com",
        fetcher=fetch,
    )
    result = await adapter.execute(
        _query().model_copy(
            update={
                "capability_id": "market.crypto_crowding",
                "symbols": ["ETH-USDT-SWAP"],
                "fields": ["crowding_signal", "bid_depth", "ask_depth"],
            }
        )
    )
    assert {fact.instrument for fact in result.facts or []} == {"ETH-USDT-SWAP"}
    assert all(
        fact.attributes["proxy_kind"] == "orderbook_imbalance" for fact in result.facts or []
    )
    assert (
        -1 <= float(next(f.value for f in result.facts or [] if f.field == "crowding_signal")) <= 1
    )


@pytest.mark.asyncio
async def test_eth_daily_has_its_own_original_and_same_computation_proof(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'private.sqlite3'}")
    database.create_all()
    jobs = DataJobService(database)
    history = HistoricalDataArchive(tmp_path / "historical")
    calls: list[str] = []

    async def fetch(url: str):
        calls.append(url)
        params = parse_qs(urlsplit(url).query)
        start = int(params["from"][0])
        price = 2500 if params["currency_pair"] == ["ETH_USDT"] else 80000
        body = json.dumps(
            [
                [
                    str(start + day * 86400),
                    "10000",
                    str(price + day + 1),
                    str(price + day + 2),
                    str(price + day - 1),
                    str(price + day),
                    "20",
                    "true",
                ]
                for day in range(90)
            ]
        ).encode()
        async with CapturingAsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200, content=body, request=request, headers={"content-type": "application/json"}
                )
            )
        ) as client:
            return (await client.get(url)).json()

    datasets = ("crypto.btc_usdt.ohlcv_1d", "crypto.eth_usdt.ohlcv_1d")
    runner = HistoricalDataJobRunner(
        jobs, history, {item: GateDailyDataLoader(fetcher=fetch) for item in datasets}
    )
    worker = HistoricalRefreshWorker(jobs, runner, daily_dataset_ids=datasets)
    first = await worker.tick_daily()
    second = await worker.tick_daily()
    assert first.status == "completed" and second.status == "already_complete"
    assert first.idempotency_key.startswith("live-gateio-btc-eth-1d-")
    assert len(calls) == 2 and len(jobs.list()) == 1
    assert jobs.list()[0].dataset_ids == list(datasets)
    end = first.window_end_at
    now = utcnow()
    outer = _query().model_copy(
        update={
            "capability_id": "data.historical",
            "requirement_id": "market_regime_context",
            "symbols": [datasets[1]],
            "fields": ["period_close", "sma_20", "sma_60", "volume_ratio_20"],
            "window_start_at": end - timedelta(days=90),
            "window_end_at": end,
            "observed_at": now,
            "cutoff_at": now,
        }
    )
    query = outer.model_copy(update={"requested_observed_at": now})
    archive = RawBodyArchive(tmp_path / "raw-captures")
    batch = CaptureBatch("private-method-test", "research-http.v1", archive, lambda _: None)
    server_received_at = utcnow()
    with binding.daily_computation_scope(
        run_id="run_private_eth",
        query=outer,
        reservation_query_hash="1" * 64,
        expected_lease_owner=None,
    ) as collector:
        with capture_scope(batch):
            result = await HistoricalDataResearchAdapter(jobs, history).execute(query)
    adapter_return = CapabilityAdapterReturnCollector()
    result = adapter_return.capture(query, result, server_received_at)
    assert adapter_return.observation is not None
    with database.session() as session:
        witness = binding.verify_daily_computation_in_session(
            session,
            collector=collector,
            raw_adapter_return=adapter_return.observation,
            result=result,
            archive=archive,
        )
        assert witness is not None
        assert witness.receipt.body_hash == batch.captures[0]["body_hash"]
        assert {fact.instrument for fact in result.facts or []} == {"ETH-USDT"}
        assert "currency_pair=ETH_USDT" in str(result.evidence_candidates[0].source_url)
        original = session.get(RawCaptureRecord, witness.receipt.capture_id)
        assert original is not None
        original.url = original.url.replace("ETH_USDT", "BTC_USDT")
        session.flush()
        with pytest.raises(ValueError, match="daily_original_capture_url_mismatch"):
            binding.read_original_daily_input_in_session(
                session, capture_ids=[witness.receipt.capture_id], query=query, archive=archive
            )
    database.engine.dispose()


def test_daily_asset_mapping_cannot_be_replaced_behind_disk_identity(monkeypatch) -> None:
    def replaced(_dataset: str) -> tuple[str, str, str]:
        return "BTC_USDT", "BTC-USDT", "usdt_and_btc"

    monkeypatch.setattr(binding._daily_asset, "__code__", replaced.__code__)
    with pytest.raises(ValueError, match="daily_loaded_callable_differs_from_source"):
        loaded_daily_implementation().identity()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["gateio", "huobi"])
async def test_eth_spot_uses_actual_mcp_registry_and_base_asset_volume(provider: str) -> None:
    adapter = _provider_adapter_registry()[f"adapter://provider/market/{provider}-public"]
    assert isinstance(adapter, PublicSpotMarketResearchAdapter)
    assert "BTC" not in adapter.base_url and "btcusdt" not in adapter.base_url
    calls: list[str] = []

    async def fetch(url: str):
        calls.append(url)
        params = parse_qs(urlsplit(url).query)
        assert params == (
            {"currency_pair": ["ETH_USDT"]} if provider == "gateio" else {"symbol": ["ethusdt"]}
        )
        return {
            "payload": json.loads(
                (RAW / f"{provider if provider == 'huobi' else 'gate'}_spot.body").read_bytes()
            )
        }

    adapter.fetcher = fetch
    result = await adapter.execute(
        _query().model_copy(
            update={
                "symbols": ["ETH-USDT"],
                "requirement_id": "crypto_spot_confirmation",
                "fields": ["spot_price", "spot_volume"],
            }
        )
    )
    assert len(calls) == 1
    assert {fact.instrument for fact in result.facts or []} == {"ETH-USDT"}
    assert {fact.independence_group for fact in result.facts or []} == {f"{provider}-public"}
    volume = next(fact for fact in result.facts or [] if fact.field == "volume")
    assert volume.unit == "eth"
    raw = json.loads(
        (RAW / f"{provider if provider == 'huobi' else 'gate'}_spot.body").read_bytes()
    )
    assert volume.value == str(
        raw[0]["base_volume"] if provider == "gateio" else raw["tick"]["amount"]
    )

    async def wrong(url: str):
        payload = await fetch(url)
        if provider == "gateio":
            payload["payload"][0]["currency_pair"] = "BTC_USDT"
        else:
            payload["payload"]["ch"] = "market.btcusdt.detail.merged"
        return payload

    adapter.fetcher = wrong
    with pytest.raises(ResearchCapabilityError, match="does not match query"):
        await adapter.execute(
            _query().model_copy(
                update={
                    "symbols": ["ETH-USDT"],
                    "fields": ["spot_price", "spot_volume"],
                }
            )
        )

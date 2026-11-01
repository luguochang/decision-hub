"""Controlled public response/archive inputs through the actual research adapters."""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from packages.contracts_py.decision_hub_contracts import ResearchCapabilityQuery
from packages.provider_adapters.historical.research import HistoricalDataResearchAdapter
from packages.provider_adapters.market.gate_htx_research import (
    GateDerivativesResearchAdapter,
    HtxDerivativesResearchAdapter,
)

AT = datetime(2026, 10, 21, 8, tzinfo=UTC)


def query(**updates):
    data = dict(
        schema_version="research-capability-query.v1", request_id="review-source-closure",
        capability_id="market.crypto_derivatives", requirement_id="derivatives_crowding",
        query="Controlled source projection", target_url=None, symbols=["ETH-USDT-SWAP"],
        fields=["open_interest_delta", "open_interest", "mark_price", "index_price",
                "funding_rate", "basis"], allowed_domains=[], max_results=10, max_cost_usd=0,
        research_session_id="review-evidence-closure", round=1, mode="live",
        observed_at=AT, cutoff_at=AT,
    )
    return ResearchCapabilityQuery.model_validate(data | updates)


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["gate", "htx"])
async def test_review_original_retains_actual_oi_window_units_and_recomputable_inputs(provider):
    seen = []

    async def fetch(url):
        seen.append(url)
        if "contract_stats" in url:
            return [{"time": 1792569900, "open_interest": "1000"},
                    {"time": 1792570200, "open_interest": "1100"}]
        if "futures/usdt/tickers" in url:
            return [{"contract": "ETH_USDT", "funding_rate": "0.0001", "total_size": "1100",
                     "mark_price": "2100", "index_price": "2100", "basis_rate": "0"}]
        if "detail/merged" in url:
            return {"status": "ok", "tick": {"close": "2100"}}
        if "funding_rate" in url:
            return {"status": "ok", "data": {"funding_rate": "0.0001"}}
        if "swap_index" in url:
            return {"status": "ok", "data": [{"index_price": "2100"}]}
        if "his_open_interest" in url:
            return {"status": "ok", "data": {"tick": [
                {"ts": 1792566600000, "volume": "1000"},
                {"ts": 1792570200000, "volume": "1100"}]}}
        return {"status": "ok", "data": [{"volume": "1100"}]}

    cls = GateDerivativesResearchAdapter if provider == "gate" else HtxDerivativesResearchAdapter
    result = await cls(fetcher=fetch).execute(query())
    original = json.loads(result.evidence_candidates[0].excerpt)
    fact = next(f for f in result.facts if f.field == "open_interest_delta")
    window = original["windows"]["open_interest_delta"]
    assert window == {"start_at": fact.window_start_at.isoformat(),
                      "end_at": fact.window_end_at.isoformat()}
    assert original["units"]["open_interest_delta"] == fact.unit == "percent"
    derivation = original["derivations"]["open_interest_delta"]
    assert derivation["base_unit"] == "contracts"
    recomputed = (Decimal(derivation["end_value"]) / Decimal(derivation["start_value"])
                  - 1) * 100
    assert recomputed == Decimal(str(fact.value)) == Decimal("10")
    if provider == "htx":
        url = next(url for url in seen if "his_open_interest" in url)
        assert parse_qs(urlsplit(url).query)["period"] == ["60min"]
        assert (fact.window_end_at - fact.window_start_at).total_seconds() == 3600
    assert len(result.evidence_candidates[0].excerpt) <= 4000


@pytest.mark.asyncio
async def test_replay_short_returns_do_not_use_candles_closing_after_request_cutoff():
    cutoff = AT.replace(hour=12) - timedelta(days=1)
    rows = daily_rows()
    dataset = "crypto.eth_usdt.ohlcv_1d"
    coverage = SimpleNamespace(
        dataset_id=dataset, instrument="ETH-USDT", venue="gateio", source_id="gateio-public",
        status="available", partition_refs=["controlled-partition"], raw_capture_ids=[],
        actual_start_at=AT-timedelta(days=80), actual_end_at=AT.replace(hour=0), gaps=[],
        unit="usdt_and_eth",
    )
    adapter = HistoricalDataResearchAdapter(None, None, clock=lambda: AT)
    adapter._locate = lambda *args, **kwargs: ("controlled-manifest", coverage)
    # Matches the production archive's timestamp < window_end selection: this
    # last candle opens before cutoff but closes afterwards in the full archive.
    adapter._rows = lambda *args: rows
    result = await adapter.execute(query(
        capability_id="data.historical", requirement_id="history", symbols=[dataset],
        fields=["period_close"], window_start_at=AT-timedelta(days=81),
        window_end_at=cutoff, cutoff_at=cutoff, observed_at=cutoff, mode="replay",
    ))
    original = json.loads(result.evidence_candidates[0].excerpt)
    for item in original["daily_close_returns"].values():
        assert datetime.fromisoformat(item["end_at"]) <= cutoff


def daily_rows():
    start = AT.replace(hour=0) - timedelta(days=80)
    return [dict(timestamp=(start + timedelta(days=i)).isoformat(), open=str(100+i),
                 high=str(102+i), low=str(99+i), close=str(101+i), volume_base="1000",
                 confirmed=True) for i in range(80)]


@pytest.mark.asyncio
@pytest.mark.parametrize("asset", ["BTC", "ETH"])
@pytest.mark.parametrize("missing_day", [False, True])
async def test_daily_review_original_has_closed_calendar_day_return_inputs(asset, missing_day):
    rows = daily_rows()
    if missing_day:
        # Still enough rows for the existing summary, but a seven-row lag is
        # no longer seven calendar days and must not be advertised as such.
        rows.pop(-4)
    dataset = f"crypto.{asset.lower()}_usdt.ohlcv_1d"
    coverage = SimpleNamespace(
        dataset_id=dataset, instrument=f"{asset}-USDT", venue="gateio", source_id="gateio-public",
        status="available", partition_refs=["controlled-partition"], raw_capture_ids=[],
        actual_start_at=AT-timedelta(days=80), actual_end_at=AT.replace(hour=0), gaps=[],
        unit="usdt_and_btc",
    )
    adapter = HistoricalDataResearchAdapter(None, None, clock=lambda: AT)
    adapter._locate = lambda *args, **kwargs: ("controlled-manifest", coverage)
    adapter._rows = lambda *args: rows
    result = await adapter.execute(query(
        capability_id="data.historical", requirement_id="history", symbols=[dataset],
        fields=["period_close", "return_20_pct"],
        window_start_at=AT-timedelta(days=81), window_end_at=AT,
    ))
    original = json.loads(result.evidence_candidates[0].excerpt)
    returns = original["daily_close_returns"]
    assert set(returns) == {"return_1d_pct", "return_2d_pct", "return_7d_pct"}
    for days in (1, 2, 7):
        item = returns[f"return_{days}d_pct"]
        if missing_day and days == 7:
            assert item is None
            continue
        assert datetime.fromisoformat(item["end_at"]) <= AT
        assert (datetime.fromisoformat(item["end_at"]) -
                datetime.fromisoformat(item["start_at"])).total_seconds() == days*86400
        actual = (Decimal(item["end_value"]) / Decimal(item["start_value"]) - 1)*100
        assert actual == Decimal(item["value"])
        assert item["unit"] == "percent"
    assert len(result.evidence_candidates[0].excerpt) <= 4000

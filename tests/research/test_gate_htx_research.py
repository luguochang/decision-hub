from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from packages.contracts_py.decision_hub_contracts import ResearchCapabilityQuery
from packages.provider_adapters.market import gate_htx_research
from packages.provider_adapters.market.crowding import CryptoCrowdingResearchAdapter
from packages.provider_adapters.market.gate_htx_research import (
    GateDerivativesResearchAdapter,
    HtxDerivativesResearchAdapter,
)


@pytest.mark.asyncio
async def test_public_fetch_retries_one_transient_connect_failure(monkeypatch) -> None:
    calls = 0

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, str]:
            return {"status": "ok"}

    class _Client:
        def __init__(self, **_kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def get(self, _url: str):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise gate_htx_research.httpx.ConnectError("transient reset")
            return _Response()

    monkeypatch.setattr(gate_htx_research, "CapturingAsyncClient", _Client)
    monkeypatch.setattr(gate_htx_research.asyncio, "sleep", lambda _delay: _async_noop())

    assert await gate_htx_research._fetch_json("https://example.test") == {"status": "ok"}
    assert calls == 2


async def _async_noop() -> None:
    return None


def _query() -> ResearchCapabilityQuery:
    now = datetime(2026, 9, 19, 3, 0, tzinfo=UTC)
    return ResearchCapabilityQuery.model_validate(
        {
            "schema_version": "research-capability-query.v1",
            "request_id": "gate-htx-test",
            "capability_id": "market.crypto_derivatives",
            "requirement_id": "derivatives_crowding",
            "query": "BTC derivatives",
            "target_url": None,
            "symbols": ["BTC-USDT-SWAP"],
            "fields": [
                "funding_rate",
                "open_interest",
                "open_interest_delta",
                "mark_price",
                "index_price",
                "basis",
            ],
            "allowed_domains": [],
            "max_results": 10,
            "max_cost_usd": 0.0,
            "research_session_id": "gate-htx-session",
            "round": 1,
            "mode": "live",
            "observed_at": now,
            "cutoff_at": now,
        }
    )


@pytest.mark.asyncio
async def test_gate_adapter_maps_ticker_and_oi_delta() -> None:
    async def fetch(url: str) -> Any:
        if "contract_stats" in url:
            return [
                {"time": 100, "open_interest": 1000},
                {"time": 400, "open_interest": 1100},
            ]
        return [
            {
                "contract": "BTC_USDT",
                "funding_rate": "0.0001",
                "total_size": "1100",
                "mark_price": "81100",
                "index_price": "81000",
                "basis_rate": "0.001234",
            }
        ]

    result = await GateDerivativesResearchAdapter(fetcher=fetch).execute(_query())
    values = {fact.field: str(fact.value) for fact in result.facts}
    assert result.provider == "gateio-derivatives-public"
    assert values["funding_rate"] == "0.0001"
    assert values["open_interest"] == "1100"
    assert values["open_interest_delta"] == "10.0"
    assert values["basis"] == "0.001234"
    assert next(f for f in result.facts if f.field == "open_interest_delta").window_start_at


@pytest.mark.asyncio
async def test_htx_adapter_maps_public_swap_payloads() -> None:
    async def fetch(url: str) -> Any:
        if "detail/merged" in url:
            return {"status": "ok", "tick": {"close": "81100", "ts": 400000}}
        if "funding_rate" in url:
            return {"status": "ok", "data": {"funding_rate": "0.00005"}}
        if "swap_index" in url:
            return {"status": "ok", "data": [{"index_price": "81000"}]}
        if "his_open_interest" in url:
            return {
                "status": "ok",
                "data": {"tick": [{"ts": 100000, "volume": 1000}, {"ts": 400000, "volume": 1050}]},
            }
        return {"status": "ok", "data": [{"volume": 1050}]}

    result = await HtxDerivativesResearchAdapter(fetcher=fetch).execute(_query())
    values = {fact.field: str(fact.value) for fact in result.facts}
    assert result.provider == "htx-derivatives-public"
    assert values["funding_rate"] == "0.00005"
    assert values["open_interest"] == "1050"
    assert float(values["open_interest_delta"]) == pytest.approx(5.0)
    assert float(values["basis"]) == pytest.approx((81100 - 81000) / 81000)


@pytest.mark.asyncio
async def test_gate_orderbook_crowding_adapter_reads_top_level_depth() -> None:
    async def fetch(_url: str) -> Any:
        return {"bids": [{"s": 3}], "asks": [{"s": 1}]}

    adapter = CryptoCrowdingResearchAdapter(
        provider_id="gateio-orderbook-public",
        base_url="https://api.gateio.ws",
        exchange="gateio",
        fetcher=fetch,
    )
    result = await adapter.execute(
        _query().model_copy(
            update={
                "capability_id": "market.crypto_crowding",
                "fields": ["crowding_signal"],
            }
        )
    )
    assert result.provider == "gateio-orderbook-public"
    assert result.facts[0].value == "0.5"

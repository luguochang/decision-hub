from __future__ import annotations

from datetime import UTC, datetime

import pytest

from packages.contracts_py.decision_hub_contracts import ResearchCapabilityQuery
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchCapabilityError,
)
from packages.provider_adapters.market.crowding import CryptoCrowdingResearchAdapter

NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)


def _query(*fields: str) -> ResearchCapabilityQuery:
    return ResearchCapabilityQuery.model_validate(
        {
            "schema_version": "research-capability-query.v1",
            "request_id": "crowding-test-1",
            "capability_id": "market.crypto_crowding",
            "requirement_id": "derivatives_crowding",
            "query": "BTC order book crowding",
            "target_url": None,
            "symbols": ["BTC-USDT-SWAP"],
            "fields": list(fields),
            "allowed_domains": [],
            "max_results": 10,
            "max_cost_usd": 0.1,
            "research_session_id": "session-crowding",
            "round": 1,
            "mode": "live",
            "observed_at": NOW,
            "cutoff_at": NOW,
        }
    )


@pytest.mark.asyncio
async def test_okx_orderbook_maps_typed_imbalance_proxy() -> None:
    async def fetch(_url: str):
        return {
            "code": "0",
            "data": [
                {
                    "bids": [["100", "3"], ["99", "1"]],
                    "asks": [["101", "1"], ["102", "1"]],
                }
            ],
        }

    adapter = CryptoCrowdingResearchAdapter(
        provider_id="okx-orderbook-public",
        base_url="https://www.okx.com",
        exchange="okx",
        fetcher=fetch,
    )
    result = await adapter.execute(_query("crowding_signal", "bid_depth", "ask_depth"))

    assert result.provider == "okx-orderbook-public"
    assert len(result.evidence_candidates) == 1
    assert result.facts is not None
    assert {item.field for item in result.facts} == {
        "crowding_signal",
        "bid_depth",
        "ask_depth",
    }
    signal = next(item for item in result.facts if item.field == "crowding_signal")
    assert signal.metric_family == "crypto.derivatives"
    assert signal.unit == "ratio"
    assert signal.attributes["proxy_kind"] == "orderbook_imbalance"
    assert float(str(signal.value)) == pytest.approx(0.3333333333)


@pytest.mark.asyncio
async def test_coinex_depth_shape_is_supported() -> None:
    async def fetch(_url: str):
        return {
            "code": 0,
            "data": {"depth": {"buy": [{"price": "1", "amount": "5"}], "sell": [["1", "5"]]}},
        }

    adapter = CryptoCrowdingResearchAdapter(
        provider_id="coinex-orderbook-public",
        base_url="https://api.coinex.com",
        exchange="coinex",
        fetcher=fetch,
    )
    result = await adapter.execute(_query("crowding_signal"))
    assert result.facts is not None
    signal = result.facts[0]
    assert signal.value == "0"
    assert "futures/depth" in str(result.evidence_candidates[0].source_url)
    assert "interval=0.01" in str(result.evidence_candidates[0].source_url)


@pytest.mark.asyncio
async def test_empty_depth_is_non_retryable_and_does_not_emit_facts() -> None:
    async def fetch(_url: str):
        return {"code": "0", "data": [{"bids": [], "asks": []}]}

    adapter = CryptoCrowdingResearchAdapter(
        provider_id="okx-orderbook-public",
        base_url="https://www.okx.com",
        exchange="okx",
        fetcher=fetch,
    )
    with pytest.raises(ResearchCapabilityError) as raised:
        await adapter.execute(_query("crowding_signal"))
    assert raised.value.error_code == "research_market_output_invalid"
    assert raised.value.retryable is False

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from packages.contracts_py.decision_hub_contracts import ResearchCapabilityQuery
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchCapabilityError,
)
from packages.provider_adapters.market import (
    PublicSpotMarketResearchAdapter,
    parse_gate_spot,
    parse_huobi_spot,
)

NOW = datetime(2026, 9, 18, 16, 0, tzinfo=UTC)


def _query(provider: str) -> ResearchCapabilityQuery:
    return ResearchCapabilityQuery(
        schema_version="research-capability-query.v1",
        request_id=f"spot-fallback:{provider}",
        capability_id="market.crypto_derivatives",
        requirement_id="crypto_spot_confirmation",
        query="current BTC spot price and volume",
        target_url=None,
        symbols=["BTC-USDT"],
        fields=["spot_price", "spot_volume"],
        allowed_domains=[],
        max_results=2,
        max_cost_usd=0.0,
        research_session_id="spot-fallback-test",
        round=1,
        mode="live",
        observed_at=NOW,
        cutoff_at=NOW,
    )


@pytest.mark.asyncio
async def test_gate_spot_fallback_emits_exchange_spot_facts() -> None:
    async def fetcher(_url: str):
        return {"payload": [{"last": "80847.7", "base_volume": "123.4"}]}

    adapter = PublicSpotMarketResearchAdapter(
        provider_id="gateio-public",
        base_url="https://api.gateio.ws/api/v4/spot/tickers?currency_pair=BTC_USDT",
        parser=parse_gate_spot,
        fetcher=fetcher,
    )
    result = await adapter.execute(_query("gateio"))

    assert result.provider == "gateio-public"
    assert [fact.field for fact in result.facts] == ["price", "volume"]
    assert {fact.metric_family for fact in result.facts} == {"crypto.spot"}
    assert {fact.independence_group for fact in result.facts} == {"gateio-public"}
    assert result.evidence_candidates[0].authority == "exchange"


def test_huobi_parser_uses_base_volume() -> None:
    assert parse_huobi_spot(
        {"payload": {"status": "ok", "tick": {"close": 80847.6, "amount": 98.1}}}
    ) == ("80847.6", "98.1")


@pytest.mark.parametrize("base_volume", [None, 0])
def test_quote_turnover_cannot_be_mislabeled_as_btc_volume(base_volume: int | None) -> None:
    parsers_and_payloads = [
        (parse_gate_spot, {"payload": [{
            "last": "80000", "base_volume": base_volume, "quote_volume": "8000000",
        }]}),
        (parse_huobi_spot, {"payload": {"status": "ok", "tick": {
            "close": "80000", "amount": base_volume, "vol": "8000000",
        }}}),
    ]
    for parser, payload in parsers_and_payloads:
        if base_volume is None:
            with pytest.raises(ResearchCapabilityError):
                parser(payload)
        else:
            assert parser(payload)[1] == "0"

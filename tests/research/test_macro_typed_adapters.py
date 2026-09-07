from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest

from packages.contracts_py.decision_hub_contracts import ResearchCapabilityQuery
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchCapabilityError,
)
from packages.provider_adapters.macro_market import (
    ExpectationPricingResearchAdapter,
    IntradayMacroResearchAdapter,
)

NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)


def _query(
    capability_id: str,
    requirement_id: str,
    symbols: list[str],
    fields: list[str],
    *,
    offsets: list[str] | None = None,
) -> ResearchCapabilityQuery:
    return ResearchCapabilityQuery.model_validate(
        {
            "schema_version": "research-capability-query.v1",
            "request_id": f"query-{capability_id}",
            "capability_id": capability_id,
            "requirement_id": requirement_id,
            "query": "macro typed fixture",
            "target_url": None,
            "symbols": symbols,
            "fields": fields,
            "allowed_domains": [],
            "max_results": 20,
            "max_cost_usd": 1.0,
            "research_session_id": "session-macro",
            "round": 1,
            "mode": "live",
            "observed_at": NOW,
            "cutoff_at": NOW,
            "requested_event_offsets": offsets or [],
        }
    )


@pytest.mark.asyncio
async def test_intraday_adapter_maps_rates_and_usd_without_claiming_event_delta() -> None:
    async def fetch(url: str):
        symbol = parse_qs(urlsplit(url).query)["symbol"][0]
        return {
            "data": [
                {
                    "timestamp": (NOW - timedelta(minutes=1)).isoformat(),
                    "values": {"level": "4.25" if symbol == "US2Y" else "103.1"},
                }
            ]
        }

    adapter = IntradayMacroResearchAdapter(
        provider_id="macro-intraday-proxy",
        base_url="https://macro.example/series",
        fetcher=fetch,
    )
    result = await adapter.execute(
        _query("macro.cross_asset_intraday", "macro_transmission", ["US2Y", "DXY"], ["level"])
    )

    assert result.facts is not None
    assert len(result.facts) == 2
    assert {fact.metric_family for fact in result.facts} == {"macro.rates", "macro.usd"}
    assert all(fact.delay_class == "delayed" for fact in result.facts)
    assert all(fact.event_offset is None for fact in result.facts)


@pytest.mark.asyncio
async def test_intraday_adapter_preserves_event_offsets_and_source_lineage() -> None:
    async def fetch(url: str):
        symbol = parse_qs(urlsplit(url).query)["symbol"][0]
        source = "rates-feed" if symbol == "US2Y" else "usd-feed"
        source_url = "https://rates.example/us2y" if symbol == "US2Y" else "https://usd.example/dxy"
        level = "4.25" if symbol == "US2Y" else "103.1"
        return {
            "data": [
                {
                    "timestamp": (NOW - timedelta(minutes=5)).isoformat(),
                    "event_offset": "t-5m",
                    "source_id": source,
                    "independence_group": source,
                    "source_url": source_url,
                    "values": {"level": level, "event_return": "0"},
                },
                {
                    "timestamp": NOW.isoformat(),
                    "event_offset": "t+1m",
                    "source_id": source,
                    "independence_group": source,
                    "source_url": source_url,
                    "values": {"level": level, "event_return": "1.5"},
                },
                {
                    "timestamp": NOW.isoformat(),
                    "source_id": source,
                    "source_url": source_url,
                    "values": {"level": "999", "event_return": "999"},
                },
            ]
        }

    adapter = IntradayMacroResearchAdapter(
        provider_id="macro-live",
        base_url="https://gateway.example/series",
        delay_class="realtime",
        authority="exchange",
        fetcher=fetch,
    )
    result = await adapter.execute(
        _query(
            "macro.cross_asset_intraday",
            "macro_transmission",
            ["US2Y", "DXY"],
            ["level", "event_return"],
            offsets=["t-5m", "t+1m"],
        )
    )

    assert result.facts is not None
    assert len(result.evidence_candidates) == 4
    assert {fact.event_offset for fact in result.facts} == {"t-5m", "t+1m"}
    assert {fact.source_id for fact in result.facts} == {"rates-feed", "usd-feed"}
    assert {fact.independence_group for fact in result.facts} == {
        "rates-feed",
        "usd-feed",
    }
    assert {str(item.source_url) for item in result.evidence_candidates} == {
        "https://rates.example/us2y",
        "https://usd.example/dxy",
    }


@pytest.mark.asyncio
async def test_intraday_missing_endpoint_fails_closed_without_network() -> None:
    adapter = IntradayMacroResearchAdapter(
        provider_id="macro-intraday-proxy",
        base_url=None,
    )
    with pytest.raises(ResearchCapabilityError) as raised:
        await adapter.execute(
            _query("macro.cross_asset_intraday", "macro_transmission", ["US2Y"], ["level"])
        )
    assert raised.value.error_code == "provider_unconfigured"
    assert raised.value.retryable is False


@pytest.mark.asyncio
async def test_expectation_adapter_preserves_event_offsets_and_proxy_class() -> None:
    async def fetch(_url: str):
        return {
            "data": [
                {
                    "timestamp": (NOW - timedelta(minutes=5)).isoformat(),
                    "event_offset": "t-5m",
                    "values": {"level": "0.34", "delta": "0.01"},
                },
                {
                    "timestamp": NOW.isoformat(),
                    "event_offset": "t+1m",
                    "values": {"level": "0.41", "delta": "0.07"},
                },
            ]
        }

    adapter = ExpectationPricingResearchAdapter(
        provider_id="expectation-pricing-configured",
        base_url="https://pricing.example/fed",
        delay_class="realtime",
        authority="exchange",
        fetcher=fetch,
    )
    result = await adapter.execute(
        _query(
            "macro.expectation_pricing",
            "expectation_pricing",
            ["SEP26"],
            ["level", "delta"],
            offsets=["t-5m", "t+1m"],
        )
    )

    assert result.facts is not None
    assert {fact.event_offset for fact in result.facts} == {"t-5m", "t+1m"}
    assert {fact.metric_family for fact in result.facts} == {"macro.policy_expectation"}
    assert all(fact.delay_class == "realtime" for fact in result.facts)


@pytest.mark.asyncio
async def test_expectation_adapter_requires_both_requested_offsets() -> None:
    async def fetch(_url: str):
        return {
            "data": [
                {
                    "timestamp": NOW.isoformat(),
                    "event_offset": "t+1m",
                    "values": {"level": "0.41"},
                }
            ]
        }

    adapter = ExpectationPricingResearchAdapter(
        provider_id="expectation-pricing-configured",
        base_url="https://pricing.example/fed",
        delay_class="realtime",
        authority="exchange",
        fetcher=fetch,
    )
    result = await adapter.execute(
        _query(
            "macro.expectation_pricing",
            "expectation_pricing",
            ["SEP26"],
            ["level"],
            offsets=["t-5m", "t+1m"],
        )
    )
    assert result.facts is not None
    assert {fact.event_offset for fact in result.facts} == {"t+1m"}

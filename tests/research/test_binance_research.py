from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from packages.contracts_py.decision_hub_contracts import ResearchCapabilityQuery
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchCapabilityError,
)
from packages.provider_adapters.market.binance_research import BinanceMarketResearchAdapter


def _query(fields: list[str]) -> ResearchCapabilityQuery:
    now = datetime.now(UTC)
    return ResearchCapabilityQuery.model_validate(
        {
            "schema_version": "research-capability-query.v1",
            "request_id": "binance-request-1",
            "capability_id": "market.crypto_derivatives",
            "requirement_id": "derivatives_crowding",
            "query": "BTC/USDT snapshot",
            "target_url": None,
            "symbols": ["BTC-USDT-SWAP"],
            "fields": fields,
            "allowed_domains": ["api.binance.com", "fapi.binance.com"],
            "max_results": 10,
            "max_cost_usd": 0.0,
            "research_session_id": "binance-session-1",
            "round": 1,
            "mode": "live",
            "observed_at": now,
            "cutoff_at": now + timedelta(minutes=1),
        }
    )


@pytest.mark.asyncio
async def test_binance_adapter_emits_two_venue_ready_snapshot_with_auditable_oi_delta() -> None:
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    payloads: dict[str, object] = {
        "/api/v3/ticker/24hr": {
            "lastPrice": "60000.1",
            "volume": "1200.5",
            "closeTime": now_ms,
        },
        "/fapi/v1/premiumIndex": {
            "markPrice": "60005",
            "indexPrice": "60000",
            "lastFundingRate": "0.0001",
            "time": now_ms,
        },
        "/fapi/v1/openInterest": {
            "openInterest": "105",
            "time": now_ms,
        },
        "/futures/data/openInterestHist": [
            {"sumOpenInterest": "100", "timestamp": now_ms - 300_000},
            {"sumOpenInterest": "105", "timestamp": now_ms},
        ],
    }
    requested_urls: list[str] = []

    async def fetcher(url: str) -> object:
        requested_urls.append(url)
        return next(payload for path, payload in payloads.items() if path in url)

    result = await BinanceMarketResearchAdapter(fetcher=fetcher).execute(
        _query(
            [
                "spot_price",
                "spot_volume",
                "funding_rate",
                "open_interest",
                "open_interest_delta",
                "mark_price",
                "index_price",
                "basis",
            ]
        )
    )

    facts = {fact.field: fact for fact in result.facts}
    assert result.provider == "binance-public"
    assert len(requested_urls) == 4
    assert set(facts) == {
        "price",
        "volume",
        "funding_rate",
        "open_interest",
        "open_interest_delta",
        "mark_price",
        "index_price",
        "basis",
    }
    assert facts["open_interest_delta"].value == "5.00"
    assert facts["open_interest_delta"].unit == "percent"
    assert facts["open_interest_delta"].window_start_at is not None
    assert facts["open_interest_delta"].window_end_at is not None
    assert facts["open_interest_delta"].window_end_at > facts["open_interest_delta"].window_start_at
    assert facts["open_interest_delta"].attributes["aggregation_period"] == "5m"
    assert facts["basis"].value == str(
        (Decimal("60005") - Decimal("60000")) / Decimal("60000")
    )
    assert {fact.venue for fact in result.facts} == {"binance"}
    assert {fact.independence_group for fact in result.facts} == {"binance"}
    assert all(
        str(candidate.source_url).startswith("https://")
        for candidate in result.evidence_candidates
    )


@pytest.mark.asyncio
async def test_binance_adapter_fails_closed_when_oi_history_has_only_one_row() -> None:
    async def fetcher(_url: str) -> object:
        return [{"sumOpenInterest": "100", "timestamp": 1_000}]

    with pytest.raises(ResearchCapabilityError, match="requires two rows"):
        await BinanceMarketResearchAdapter(fetcher=fetcher).execute(
            _query(["open_interest_delta"])
        )


@pytest.mark.asyncio
async def test_binance_adapter_does_not_require_unrequested_endpoint_payloads() -> None:
    now_ms = int(datetime.now(UTC).timestamp() * 1000)

    async def fetcher(_url: str) -> object:
        return {
            "markPrice": "60005",
            "indexPrice": "60000",
            "lastFundingRate": "0.0001",
            "time": now_ms,
        }

    result = await BinanceMarketResearchAdapter(fetcher=fetcher).execute(
        _query(["funding_rate", "basis"])
    )

    assert {fact.field for fact in result.facts} == {"funding_rate", "basis"}
    assert len(result.evidence_candidates) == 1

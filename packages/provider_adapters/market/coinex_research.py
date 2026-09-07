from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

import httpx
from pydantic import AnyUrl

from packages.contracts_py.decision_hub_contracts import (
    EvidenceCandidate,
    FactEnvelope,
    ResearchCapabilityQuery,
    ResearchCapabilityResult,
)
from packages.kernel.decision_hub_kernel.application.fact_store import (
    research_fact_instance_id,
    research_fact_payload_hash,
)
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchCapabilityError,
    research_evidence_content_hash,
    research_evidence_instance_id,
)

JsonFetcher = Callable[[str], Awaitable[Mapping[str, object]]]


async def _fetch_json(url: str) -> Mapping[str, object]:
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        response = await client.get(url)
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("CoinEx response must be an object")
    return payload


class CoinExMarketResearchAdapter:
    """Read-only BTC spot and perpetual snapshot from CoinEx public endpoints."""

    capability_id = "market.crypto_derivatives"
    supported_modes = frozenset({"live"})
    supported_fields = frozenset(
        {
            "spot_price",
            "spot_volume",
            "funding_rate",
            "open_interest",
            "mark_price",
            "index_price",
            "basis",
        }
    )
    _spot_fields = frozenset({"spot_price", "spot_volume"})
    _derivatives_fields = frozenset(
        {"funding_rate", "open_interest", "mark_price", "index_price", "basis"}
    )
    _supported_fields = _spot_fields | _derivatives_fields
    _symbol_aliases = {
        "BTC": "BTCUSDT",
        "BTC-USDT": "BTCUSDT",
        "BTC-USDT-SWAP": "BTCUSDT",
        "BTCUSDT": "BTCUSDT",
    }

    def __init__(
        self,
        *,
        fetcher: JsonFetcher | None = None,
        base_url: str = "https://api.coinex.com",
    ) -> None:
        self.fetcher = fetcher or _fetch_json
        self.base_url = base_url.rstrip("/")

    async def execute(self, query: ResearchCapabilityQuery) -> ResearchCapabilityResult:
        market = self._market(query.symbols)
        fields = set(query.fields) or self._default_fields(query.requirement_id)
        if not fields <= self._supported_fields:
            raise ResearchCapabilityError(
                "research_market_field_invalid",
                "unsupported CoinEx market field requested",
            )

        urls: dict[str, str] = {}
        if fields & self._spot_fields:
            urls["spot"] = f"{self.base_url}/v2/spot/ticker?market={market}"
        if fields & (self._derivatives_fields - {"funding_rate"}):
            urls["futures"] = f"{self.base_url}/v2/futures/ticker?market={market}"
        if "funding_rate" in fields:
            urls["funding"] = f"{self.base_url}/v2/futures/funding-rate?market={market}"

        payloads = {name: await self.fetcher(url) for name, url in urls.items()}
        rows = {name: _first_coinex_row(payload) for name, payload in payloads.items()}
        values = self._values(fields, rows)
        received_at = datetime.now(UTC)
        excerpt = json.dumps(
            {
                "market": market,
                "requirement_id": query.requirement_id,
                "values": values,
                "source_urls": urls,
                "observed_at": received_at.isoformat(),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )[:4000]
        primary_url = urls.get("spot") or urls.get("futures") or urls["funding"]
        content_hash = research_evidence_content_hash(
            requirement_id=query.requirement_id,
            kind="market",
            authority="exchange",
            source_id="coinex-public",
            source_url=primary_url,
            published_at=None,
            excerpt=excerpt,
            structured_payload_ref=None,
        )
        candidate = EvidenceCandidate(
            evidence_id=research_evidence_instance_id(
                content_hash=content_hash,
                research_session_id=query.research_session_id,
            ),
            requirement_id=query.requirement_id,
            kind="market",
            authority="exchange",
            source_id="coinex-public",
            source_url=AnyUrl(primary_url),
            published_at=None,
            observed_at=received_at,
            received_at=received_at,
            content_hash=content_hash,
            excerpt=excerpt,
            structured_payload_ref=None,
            tool_call_id=query.request_id,
            research_session_id=query.research_session_id,
            round=query.round,
            quality="candidate",
            freshness_status="unknown",
            conflict_group=None,
        )
        metric_family = (
            "crypto.spot" if fields <= self._spot_fields else "crypto.derivatives"
        )
        field_aliases = {"spot_price": "price", "spot_volume": "volume"}
        field_units = {
            "spot_price": "usdt",
            "spot_volume": "btc",
            "funding_rate": "rate",
            "open_interest": "btc",
            "mark_price": "usdt",
            "index_price": "usdt",
            "basis": "rate",
        }
        facts = [
            _fact(
                query=query,
                evidence_id=candidate.evidence_id,
                source_id=candidate.source_id,
                metric_family=metric_family,
                field=field_aliases.get(field, field),
                value=values[field],
                unit=field_units[field],
                instrument=market,
                received_at=received_at,
                provider_field=field,
            )
            for field in sorted(values)
        ]
        return ResearchCapabilityResult(
            schema_version="research-capability-result.v1",
            request_id=query.request_id,
            capability_id=query.capability_id,
            provider="coinex-public",
            evidence_candidates=[candidate],
            facts=facts,
            cost_usd=0.0,
            completed_at=received_at,
        )

    def _market(self, symbols: list[str]) -> str:
        if len(symbols) != 1 or symbols[0].upper() not in self._symbol_aliases:
            raise ResearchCapabilityError(
                "research_symbols_required",
                "CoinEx market capability requires one audited BTC/USDT symbol",
            )
        return self._symbol_aliases[symbols[0].upper()]

    def _default_fields(self, requirement_id: str) -> set[str]:
        if requirement_id in {"crypto.spot", "crypto_spot_confirmation"}:
            return set(self._spot_fields)
        return set(self._derivatives_fields)

    @staticmethod
    def _values(
        fields: set[str], rows: Mapping[str, Mapping[str, object]]
    ) -> dict[str, object]:
        result: dict[str, object] = {}
        spot = rows.get("spot", {})
        futures = rows.get("futures", {})
        funding = rows.get("funding", {})
        field_map: dict[str, object] = {
            "spot_price": spot.get("last"),
            "spot_volume": spot.get("volume"),
            "funding_rate": funding.get("latest_funding_rate"),
            "open_interest": futures.get("open_interest_volume"),
            "mark_price": futures.get("mark_price"),
            "index_price": futures.get("index_price"),
        }
        for field in sorted(fields - {"basis"}):
            value = field_map.get(field)
            if value in {None, ""}:
                raise ResearchCapabilityError(
                    "research_market_output_invalid",
                    f"CoinEx response is missing {field}",
                )
            result[field] = value
        if "basis" in fields:
            result["basis"] = _basis(futures.get("mark_price"), futures.get("index_price"))
        return result


def _first_coinex_row(payload: Mapping[str, object]) -> Mapping[str, object]:
    if str(payload.get("code", "0")) != "0":
        raise ResearchCapabilityError(
            "research_market_unavailable",
            "CoinEx returned an unsuccessful response",
            retryable=True,
        )
    data = payload.get("data")
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        raise ResearchCapabilityError(
            "research_market_output_invalid",
            "CoinEx response has no data row",
        )
    return data[0]


def _basis(mark_price: object, index_price: object) -> str:
    try:
        mark = Decimal(str(mark_price))
        index = Decimal(str(index_price))
        if index == 0:
            raise InvalidOperation
        return str((mark - index) / index)
    except (InvalidOperation, ValueError) as exc:
        raise ResearchCapabilityError(
            "research_market_output_invalid",
            "CoinEx response cannot produce a valid basis",
        ) from exc


def _fact(
    *,
    query: ResearchCapabilityQuery,
    evidence_id: str,
    source_id: str,
    metric_family: str,
    field: str,
    value: object,
    unit: str,
    instrument: str,
    received_at: datetime,
    provider_field: str,
) -> FactEnvelope:
    normalized_value: str | float | None = None if value is None else str(value)
    attributes: dict[str, float | str | bool | None] = {"provider_field": provider_field}
    payload_schema_ref = "coinex.public-market.v2"
    payload_hash = research_fact_payload_hash(
        requirement_id=query.requirement_id,
        metric_family=metric_family,
        field=field,
        instrument=instrument,
        venue="coinex",
        value=normalized_value,
        unit=unit,
        window_start_at=None,
        window_end_at=None,
        event_offset=None,
        published_at=None,
        source_id=source_id,
        independence_group="coinex",
        delay_class="realtime",
        payload_schema_ref=payload_schema_ref,
        attributes=attributes,
    )
    return FactEnvelope(
        schema_version="fact-envelope.v1",
        fact_id=research_fact_instance_id(
            evidence_id=evidence_id, payload_hash=payload_hash
        ),
        evidence_id=evidence_id,
        requirement_id=query.requirement_id,
        metric_family=metric_family,
        field=field,
        instrument=instrument,
        venue="coinex",
        value=normalized_value,
        unit=unit,
        window_start_at=None,
        window_end_at=None,
        event_offset=None,
        observed_at=received_at,
        received_at=received_at,
        published_at=None,
        source_id=source_id,
        independence_group="coinex",
        quality="candidate",
        delay_class="realtime",
        payload_schema_ref=payload_schema_ref,
        payload_hash=payload_hash,
        attributes=attributes,
    )

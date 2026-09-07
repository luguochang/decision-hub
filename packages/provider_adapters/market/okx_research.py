from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime

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
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(url)
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("OKX response must be an object")
    return payload


class OKXDerivativesResearchAdapter:
    capability_id = "market.crypto_derivatives"
    supported_modes = frozenset({"live"})
    supported_fields = frozenset({"ticker", "funding_rate", "open_interest", "mark_price"})
    _supported_fields = frozenset({"ticker", "funding_rate", "open_interest", "mark_price"})

    def __init__(
        self,
        *,
        fetcher: JsonFetcher | None = None,
        base_url: str = "https://www.okx.com",
    ) -> None:
        self.fetcher = fetcher or _fetch_json
        self.base_url = base_url.rstrip("/")

    async def execute(self, query: ResearchCapabilityQuery) -> ResearchCapabilityResult:
        if not query.symbols:
            raise ResearchCapabilityError(
                "research_symbols_required", "OKX derivatives capability requires symbols"
            )
        fields = set(query.fields) or set(self._supported_fields)
        if not fields <= self._supported_fields:
            raise ResearchCapabilityError(
                "research_market_field_invalid", "unsupported OKX derivatives field requested"
            )
        jobs = [
            (symbol, field, self._url(symbol, field))
            for symbol in query.symbols
            for field in sorted(fields)
        ]
        payloads = await asyncio.gather(*(self.fetcher(url) for _, _, url in jobs))
        received_at = datetime.now(UTC)
        candidates: list[EvidenceCandidate] = []
        facts: list[FactEnvelope] = []
        for (symbol, field, url), payload in zip(jobs, payloads, strict=True):
            row = _first_okx_row(payload)
            published_at = _okx_timestamp(row)
            excerpt = json.dumps(
                {"field": field, "instrument": symbol, "value": row},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )[:4000]
            content_hash = research_evidence_content_hash(
                requirement_id=query.requirement_id,
                kind="market",
                authority="exchange",
                source_id="okx-public",
                source_url=url,
                published_at=published_at,
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
                source_id="okx-public",
                source_url=AnyUrl(url),
                published_at=published_at,
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
            candidates.append(candidate)
            fact = _okx_fact(
                query=query,
                candidate=candidate,
                symbol=symbol,
                provider_field=field,
                row=row,
                received_at=received_at,
            )
            if fact is not None:
                facts.append(fact)
        return ResearchCapabilityResult(
            schema_version="research-capability-result.v1",
            request_id=query.request_id,
            capability_id=query.capability_id,
            provider="okx-public",
            evidence_candidates=candidates,
            facts=facts,
            cost_usd=0.0,
            completed_at=received_at,
        )

    def _url(self, symbol: str, field: str) -> str:
        if field == "ticker":
            return f"{self.base_url}/api/v5/market/ticker?instId={symbol}"
        if field == "funding_rate":
            return f"{self.base_url}/api/v5/public/funding-rate?instId={symbol}"
        if field == "open_interest":
            return f"{self.base_url}/api/v5/public/open-interest?instType=SWAP&instId={symbol}"
        return f"{self.base_url}/api/v5/public/mark-price?instType=SWAP&instId={symbol}"


def _first_okx_row(payload: Mapping[str, object]) -> Mapping[str, object]:
    if str(payload.get("code", "0")) != "0":
        raise ResearchCapabilityError(
            "research_market_unavailable", "OKX returned an unsuccessful response", retryable=True
        )
    data = payload.get("data")
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        raise ResearchCapabilityError(
            "research_market_output_invalid", "OKX response has no data row"
        )
    return data[0]


def _okx_timestamp(row: Mapping[str, object]) -> datetime | None:
    raw = row.get("ts")
    try:
        return datetime.fromtimestamp(int(str(raw)) / 1000, tz=UTC)
    except (TypeError, ValueError, OSError):
        return None


def _okx_fact(
    *,
    query: ResearchCapabilityQuery,
    candidate: EvidenceCandidate,
    symbol: str,
    provider_field: str,
    row: Mapping[str, object],
    received_at: datetime,
) -> FactEnvelope | None:
    mappings = {
        "ticker": ("price", "last", "usdt"),
        "funding_rate": ("funding_rate", "fundingRate", "rate"),
        "open_interest": ("open_interest", "oiCcy", "btc"),
        "mark_price": ("mark_price", "markPx", "usdt"),
    }
    field, row_key, unit = mappings[provider_field]
    value = row.get(row_key)
    if value in {None, ""}:
        return None
    published_at = _okx_timestamp(row)
    attributes: dict[str, float | str | bool | None] = {"provider_field": provider_field}
    payload_schema_ref = "okx.public-api.v5"
    payload_hash = research_fact_payload_hash(
        requirement_id=query.requirement_id,
        metric_family="crypto.derivatives",
        field=field,
        instrument=symbol,
        venue="okx",
        value=str(value),
        unit=unit,
        window_start_at=None,
        window_end_at=None,
        event_offset=None,
        published_at=published_at,
        source_id=candidate.source_id,
        independence_group="okx",
        delay_class="realtime",
        payload_schema_ref=payload_schema_ref,
        attributes=attributes,
    )
    return FactEnvelope(
        schema_version="fact-envelope.v1",
        fact_id=research_fact_instance_id(
            evidence_id=candidate.evidence_id, payload_hash=payload_hash
        ),
        evidence_id=candidate.evidence_id,
        requirement_id=query.requirement_id,
        metric_family="crypto.derivatives",
        field=field,
        instrument=symbol,
        venue="okx",
        value=str(value),
        unit=unit,
        window_start_at=None,
        window_end_at=None,
        event_offset=None,
        observed_at=received_at,
        received_at=received_at,
        published_at=published_at,
        source_id=candidate.source_id,
        independence_group="okx",
        quality="candidate",
        delay_class="realtime",
        payload_schema_ref=payload_schema_ref,
        payload_hash=payload_hash,
        attributes=attributes,
    )

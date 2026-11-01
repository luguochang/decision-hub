from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

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
from packages.provider_adapters.research.raw_capture import CapturingAsyncClient

JsonFetcher = Callable[[str], Awaitable[Any]]


async def _fetch_json(url: str) -> Any:
    try:
        async with CapturingAsyncClient(timeout=20, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.json()
    except httpx.HTTPStatusError as exc:
        raise ResearchCapabilityError(
            "research_market_unavailable",
            f"Binance returned HTTP {exc.response.status_code}",
            retryable=exc.response.status_code in {408, 425, 429, 500, 502, 503, 504},
            origin="provider",
            cause_code=f"http_{exc.response.status_code}",
        ) from exc
    except httpx.RequestError as exc:
        raise ResearchCapabilityError(
            "research_market_unavailable",
            "Binance public market endpoint could not be reached",
            retryable=True,
            origin="provider",
            cause_code=type(exc).__name__,
        ) from exc


class BinanceMarketResearchAdapter:
    """Read-only BTC/USDT spot and perpetual evidence from public Binance APIs."""

    capability_id = "market.crypto_derivatives"
    supported_modes = frozenset({"live"})
    supported_fields = frozenset(
        {
            "spot_price",
            "spot_volume",
            "funding_rate",
            "open_interest",
            "open_interest_delta",
            "mark_price",
            "index_price",
            "basis",
        }
    )
    _spot_fields = frozenset({"spot_price", "spot_volume"})
    _premium_fields = frozenset({"funding_rate", "mark_price", "index_price", "basis"})
    _symbol_aliases = {
        "BTC": "BTCUSDT",
        "BTC-USDT": "BTCUSDT",
        "BTC-USDT-SWAP": "BTCUSDT",
        "BTCUSDT": "BTCUSDT",
    }

    symbols_required = True
    max_symbols = 1

    @property
    def supported_symbols(self):
        return frozenset(self._symbol_aliases)

    def __init__(
        self,
        *,
        fetcher: JsonFetcher | None = None,
        spot_base_url: str = "https://api.binance.com",
        futures_base_url: str = "https://fapi.binance.com",
    ) -> None:
        self.fetcher = fetcher or _fetch_json
        self.spot_base_url = spot_base_url.rstrip("/")
        self.futures_base_url = futures_base_url.rstrip("/")

    async def execute(self, query: ResearchCapabilityQuery) -> ResearchCapabilityResult:
        symbol = self._symbol(query.symbols)
        fields = set(query.fields) or self._default_fields(query.requirement_id)
        if not fields <= self.supported_fields:
            raise ResearchCapabilityError(
                "research_market_field_invalid",
                "unsupported Binance market field requested",
            )

        urls: dict[str, str] = {}
        if fields & self._spot_fields:
            urls["spot"] = f"{self.spot_base_url}/api/v3/ticker/24hr?symbol={symbol}"
        if fields & self._premium_fields:
            urls["premium"] = f"{self.futures_base_url}/fapi/v1/premiumIndex?symbol={symbol}"
        if "open_interest" in fields:
            urls["open_interest"] = f"{self.futures_base_url}/fapi/v1/openInterest?symbol={symbol}"
        if "open_interest_delta" in fields:
            urls["open_interest_history"] = (
                f"{self.futures_base_url}/futures/data/openInterestHist"
                f"?symbol={symbol}&period=5m&limit=2"
            )

        names = list(urls)
        payload_list = await asyncio.gather(*(self.fetcher(urls[name]) for name in names))
        payloads = dict(zip(names, payload_list, strict=True))
        received_at = datetime.now(UTC)
        values, field_sources, windows = _extract_values(fields, payloads, received_at)

        candidates: dict[str, EvidenceCandidate] = {}
        for source_name in sorted({field_sources[field] for field in values}):
            payload = payloads[source_name]
            published_at = _payload_timestamp(source_name, payload, received_at)
            candidates[source_name] = _candidate(
                query=query,
                source_url=urls[source_name],
                source_name=source_name,
                payload=payload,
                published_at=published_at,
                received_at=received_at,
            )

        field_aliases = {"spot_price": "price", "spot_volume": "volume"}
        field_units = {
            "spot_price": "usdt",
            "spot_volume": "btc",
            "funding_rate": "rate",
            "open_interest": "btc",
            "open_interest_delta": "percent",
            "mark_price": "usdt",
            "index_price": "usdt",
            "basis": "rate",
        }
        facts = []
        for provider_field in sorted(values):
            source_name = field_sources[provider_field]
            facts.append(
                _fact(
                    query=query,
                    candidate=candidates[source_name],
                    field=field_aliases.get(provider_field, provider_field),
                    provider_field=provider_field,
                    value=values[provider_field],
                    unit=field_units[provider_field],
                    instrument=symbol,
                    received_at=received_at,
                    window=windows.get(provider_field),
                )
            )

        return ResearchCapabilityResult(
            schema_version="research-capability-result.v1",
            request_id=query.request_id,
            capability_id=query.capability_id,
            provider="binance-public",
            evidence_candidates=list(candidates.values()),
            facts=facts,
            cost_usd=0.0,
            completed_at=received_at,
        )

    def _symbol(self, symbols: list[str]) -> str:
        if len(symbols) != 1 or symbols[0].upper() not in self._symbol_aliases:
            raise ResearchCapabilityError(
                "research_symbols_required",
                "Binance market capability requires one audited BTC/USDT symbol",
            )
        return self._symbol_aliases[symbols[0].upper()]

    def _default_fields(self, requirement_id: str) -> set[str]:
        if requirement_id in {"crypto.spot", "crypto_spot_confirmation"}:
            return set(self._spot_fields)
        return set(self.supported_fields - self._spot_fields)


def _extract_values(
    fields: set[str],
    payloads: Mapping[str, object],
    received_at: datetime,
) -> tuple[
    dict[str, str],
    dict[str, str],
    dict[str, tuple[datetime, datetime]],
]:
    spot = _object(payloads.get("spot"), "spot ticker") if "spot" in payloads else {}
    premium = _object(payloads.get("premium"), "premium index") if "premium" in payloads else {}
    open_interest = (
        _object(payloads.get("open_interest"), "open interest")
        if "open_interest" in payloads
        else {}
    )
    raw: dict[str, object] = {
        "spot_price": spot.get("lastPrice"),
        "spot_volume": spot.get("volume"),
        "funding_rate": premium.get("lastFundingRate"),
        "open_interest": open_interest.get("openInterest"),
        "mark_price": premium.get("markPrice"),
        "index_price": premium.get("indexPrice"),
    }
    sources = {
        "spot_price": "spot",
        "spot_volume": "spot",
        "funding_rate": "premium",
        "open_interest": "open_interest",
        "mark_price": "premium",
        "index_price": "premium",
        "basis": "premium",
        "open_interest_delta": "open_interest_history",
    }
    values: dict[str, str] = {}
    windows: dict[str, tuple[datetime, datetime]] = {}
    for field in sorted(fields - {"basis", "open_interest_delta"}):
        value = raw.get(field)
        if value in {None, ""}:
            raise ResearchCapabilityError(
                "research_market_output_invalid",
                f"Binance response is missing {field}",
            )
        values[field] = str(value)
    if "basis" in fields:
        values["basis"] = _percentage_ratio(
            premium.get("markPrice"), premium.get("indexPrice"), scale=Decimal("1")
        )
    if "open_interest_delta" in fields:
        history = _history_rows(payloads.get("open_interest_history"))
        earlier, later = history[-2], history[-1]
        values["open_interest_delta"] = _percentage_ratio(
            later.get("sumOpenInterest"), earlier.get("sumOpenInterest"), scale=Decimal("100")
        )
        window_start = _timestamp(earlier.get("timestamp"))
        window_end = _timestamp(later.get("timestamp"))
        if window_start is None or window_end is None or window_end <= window_start:
            raise ResearchCapabilityError(
                "research_market_output_invalid",
                "Binance open-interest history has an invalid time window",
            )
        windows["open_interest_delta"] = (
            min(window_start, received_at),
            min(window_end, received_at),
        )
    return values, sources, windows


def _candidate(
    *,
    query: ResearchCapabilityQuery,
    source_url: str,
    source_name: str,
    payload: object,
    published_at: datetime | None,
    received_at: datetime,
) -> EvidenceCandidate:
    excerpt = json.dumps(
        {"endpoint": source_name, "payload": payload},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )[:4000]
    content_hash = research_evidence_content_hash(
        requirement_id=query.requirement_id,
        kind="market",
        authority="exchange",
        source_id="binance-public",
        source_url=source_url,
        published_at=published_at,
        excerpt=excerpt,
        structured_payload_ref=None,
    )
    return EvidenceCandidate(
        evidence_id=research_evidence_instance_id(
            content_hash=content_hash,
            research_session_id=query.research_session_id,
        ),
        requirement_id=query.requirement_id,
        kind="market",
        authority="exchange",
        source_id="binance-public",
        source_url=AnyUrl(source_url),
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


def _fact(
    *,
    query: ResearchCapabilityQuery,
    candidate: EvidenceCandidate,
    field: str,
    provider_field: str,
    value: str,
    unit: str,
    instrument: str,
    received_at: datetime,
    window: tuple[datetime, datetime] | None,
) -> FactEnvelope:
    metric_family = (
        "crypto.spot" if provider_field in {"spot_price", "spot_volume"} else "crypto.derivatives"
    )
    published_at = candidate.published_at
    window_start_at, window_end_at = window or (None, None)
    attributes: dict[str, float | str | bool | None] = {
        "provider_field": provider_field,
        "aggregation_period": "5m" if provider_field == "open_interest_delta" else None,
    }
    payload_schema_ref = "binance.public-market.v3"
    payload_hash = research_fact_payload_hash(
        requirement_id=query.requirement_id,
        metric_family=metric_family,
        field=field,
        instrument=instrument,
        venue="binance",
        value=value,
        unit=unit,
        window_start_at=window_start_at,
        window_end_at=window_end_at,
        event_offset=None,
        published_at=published_at,
        source_id=candidate.source_id,
        independence_group="binance",
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
        metric_family=metric_family,
        field=field,
        instrument=instrument,
        venue="binance",
        value=value,
        unit=unit,
        window_start_at=window_start_at,
        window_end_at=window_end_at,
        event_offset=None,
        observed_at=received_at,
        received_at=received_at,
        published_at=published_at,
        source_id=candidate.source_id,
        independence_group="binance",
        quality="candidate",
        delay_class="realtime",
        payload_schema_ref=payload_schema_ref,
        payload_hash=payload_hash,
        attributes=attributes,
    )


def _object(payload: object, label: str) -> Mapping[str, object]:
    if not isinstance(payload, Mapping):
        raise ResearchCapabilityError(
            "research_market_output_invalid",
            f"Binance {label} response must be an object",
        )
    return payload


def _history_rows(payload: object) -> Sequence[Mapping[str, object]]:
    if (
        not isinstance(payload, list)
        or len(payload) < 2
        or not all(isinstance(item, Mapping) for item in payload)
    ):
        raise ResearchCapabilityError(
            "research_market_output_invalid",
            "Binance open-interest history requires two rows",
        )
    return payload


def _percentage_ratio(current: object, baseline: object, *, scale: Decimal) -> str:
    try:
        current_value = Decimal(str(current))
        baseline_value = Decimal(str(baseline))
        if baseline_value == 0:
            raise InvalidOperation
        return str(((current_value - baseline_value) / baseline_value) * scale)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ResearchCapabilityError(
            "research_market_output_invalid",
            "Binance response cannot produce the requested change",
        ) from exc


def _timestamp(raw: object) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(str(raw)) / 1000, tz=UTC)
    except (TypeError, ValueError, OSError):
        return None


def _payload_timestamp(source_name: str, payload: object, received_at: datetime) -> datetime | None:
    raw: object = None
    if isinstance(payload, Mapping):
        raw = payload.get("closeTime" if source_name == "spot" else "time")
    elif isinstance(payload, list) and payload and isinstance(payload[-1], Mapping):
        raw = payload[-1].get("timestamp")
    timestamp = _timestamp(raw)
    return min(timestamp, received_at) if timestamp is not None else None

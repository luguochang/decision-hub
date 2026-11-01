"""Typed daily macro and cross-asset snapshots from Yahoo Finance charts.

This adapter is a bounded research fallback for environments where the FRED
and Treasury TLS paths are unavailable.  It keeps Yahoo's lower authority
visible as ``audited_aggregator`` and emits completed daily observations only;
it is never treated as an official release or an execution price feed.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from math import isfinite
from typing import Any
from urllib.parse import quote

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
from packages.provider_adapters.http_errors import classify_provider_exception
from packages.provider_adapters.research.raw_capture import CapturingAsyncClient

JsonFetcher = Callable[[str], Awaitable[Any]]


@dataclass(frozen=True)
class _Series:
    yahoo_symbol: str
    instrument: str
    metric_family: str
    level_unit: str


_SERIES: dict[str, _Series] = {
    "US10Y": _Series("^TNX", "US10Y", "macro.rates", "yield_percent"),
    "DGS10": _Series("^TNX", "US10Y", "macro.rates", "yield_percent"),
    "^TNX": _Series("^TNX", "US10Y", "macro.rates", "yield_percent"),
    "DXY": _Series("DX-Y.NYB", "DXY", "macro.usd", "index"),
    "DX-Y.NYB": _Series("DX-Y.NYB", "DXY", "macro.usd", "index"),
    "SPX": _Series("^GSPC", "SPX", "cross_asset.equity", "index"),
    "^GSPC": _Series("^GSPC", "SPX", "cross_asset.equity", "index"),
    "SPY": _Series("SPY", "SPY", "cross_asset.equity", "price"),
    "VIX": _Series("^VIX", "VIX", "cross_asset.volatility", "index"),
    "^VIX": _Series("^VIX", "VIX", "cross_asset.volatility", "index"),
    "GOLD": _Series("GC=F", "GOLD", "cross_asset.commodity", "price"),
    "GC=F": _Series("GC=F", "GOLD", "cross_asset.commodity", "price"),
}

_SUPPORTED_FIELDS = frozenset({"level", "change_5d", "return_5d"})
_COMPLETION_CONTRACT = "yahoo-provider-declared-daily.v2"


@dataclass(frozen=True)
class _DailyClose:
    start_at: datetime
    value: Decimal
    completion_not_after_at: datetime
    completion_basis: str
    snapshot_requested_at: datetime

    def coordinates(self) -> dict[str, str]:
        return {
            "completion_contract": _COMPLETION_CONTRACT,
            "daily_bar_start_at": self.start_at.isoformat(),
            "daily_completion_not_after_at": self.completion_not_after_at.isoformat(),
            "daily_completion_basis": self.completion_basis,
            "daily_snapshot_requested_at": self.snapshot_requested_at.isoformat(),
        }


async def _fetch_json(url: str) -> Any:
    for attempt in range(2):
        try:
            async with CapturingAsyncClient(
                timeout=httpx.Timeout(6.0, connect=2.5),
                follow_redirects=True,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 Chrome/140.0 Safari/537.36"
                    ),
                    "Accept": "application/json",
                },
            ) as client:
                response = await client.get(url)
                response.raise_for_status()
                return response.json()
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            if attempt == 0:
                await asyncio.sleep(0.15)
                continue
            raise classify_provider_exception(exc) from exc
        except Exception as exc:
            raise classify_provider_exception(exc) from exc
    raise AssertionError("bounded provider retry loop is exhaustive")  # pragma: no cover


class YahooCrossAssetResearchAdapter:
    """Completed-daily macro/cross-asset levels and five-session changes."""

    capability_id = "market.cross_asset"
    supported_modes = frozenset({"live"})
    supported_fields = _SUPPORTED_FIELDS
    supported_symbols = frozenset(_SERIES)
    symbols_required = True

    def __init__(
        self,
        *,
        fetcher: JsonFetcher | None = None,
        base_url: str = "https://query1.finance.yahoo.com",
        provider_id: str = "yahoo-finance-public",
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.fetcher = fetcher or _fetch_json
        self._uses_default_fetcher = fetcher is None
        self.base_url = base_url.rstrip("/")
        self.provider_id = provider_id
        self._clock = clock or (lambda: datetime.now(UTC))

    async def execute(self, query: ResearchCapabilityQuery) -> ResearchCapabilityResult:
        series = _resolve_series(query.symbols)
        requested = set(query.fields)
        if not requested:
            requested = {"level"}
        if not requested <= self.supported_fields:
            raise ResearchCapabilityError(
                "research_market_field_invalid",
                "Yahoo cross-asset adapter supports level/change_5d/return_5d only",
            )
        urls = [self._url(item.yahoo_symbol) for item in series]
        # The public chart service rate-limits small concurrent bursts.  Two
        # sequential requests remain well inside the route deadline and are
        # more reliable than opening parallel TLS connections.
        payloads: list[tuple[Any, datetime]] = []
        for index, url in enumerate(urls):
            if index and self._uses_default_fetcher:
                await asyncio.sleep(0.3)
            requested_at = _aware_clock(self._clock())
            payloads.append((await self.fetcher(url), requested_at))
        received_at = _aware_clock(self._clock())
        candidates: list[EvidenceCandidate] = []
        facts: list[FactEnvelope] = []
        for item, url, (payload, requested_at) in zip(series, urls, payloads, strict=True):
            if requested_at > received_at:
                raise ResearchCapabilityError(
                    "research_market_output_invalid", "Yahoo receive clock precedes request"
                )
            rows = _daily_rows(
                payload,
                min(query.cutoff_at, requested_at, received_at),
                expected_symbol=item.yahoo_symbol,
                requested_at=requested_at,
            )
            candidate, item_facts = _build_item(
                query=query,
                item=item,
                url=url,
                rows=rows,
                requested=requested,
                received_at=received_at,
                provider_id=self.provider_id,
            )
            candidates.append(candidate)
            facts.extend(item_facts)
        return ResearchCapabilityResult(
            schema_version="research-capability-result.v1",
            request_id=query.request_id,
            capability_id=query.capability_id,
            provider=self.provider_id,
            evidence_candidates=candidates,
            facts=facts,
            cost_usd=0.0,
            completed_at=received_at,
        )

    def _url(self, symbol: str) -> str:
        encoded = quote(symbol, safe="")
        return (
            f"{self.base_url}/v8/finance/chart/{encoded}"
            "?interval=1d&range=1mo&events=history&includePrePost=false"
        )


def _resolve_series(symbols: Sequence[str]) -> list[_Series]:
    if not symbols:
        raise ResearchCapabilityError(
            "research_symbols_required", "Yahoo cross-asset request requires symbols"
        )
    result: list[_Series] = []
    seen: set[str] = set()
    for raw in symbols:
        item = _SERIES.get(raw.strip().upper())
        if item is None:
            raise ResearchCapabilityError(
                "research_macro_series_denied",
                f"Yahoo series is outside the approved allowlist: {raw}",
            )
        if item.instrument in seen:
            continue
        seen.add(item.instrument)
        result.append(item)
    return result


def _aware_clock(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ResearchCapabilityError(
            "research_market_output_invalid", "Yahoo snapshot clock must be aware"
        )
    return value.astimezone(UTC)


def _epoch(value: object) -> datetime | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        if not isfinite(value) or int(value) != value:
            return None
        return datetime.fromtimestamp(value, tz=UTC)
    except (ValueError, OverflowError, OSError):
        return None


def _daily_rows(
    payload: object,
    cutoff_at: datetime,
    *,
    expected_symbol: str,
    requested_at: datetime,
) -> list[_DailyClose]:
    if not isinstance(payload, Mapping):
        raise ResearchCapabilityError(
            "research_market_output_invalid", "Yahoo response is not an object"
        )
    chart = payload.get("chart")
    if not isinstance(chart, Mapping) or chart.get("error") is not None:
        raise ResearchCapabilityError(
            "research_market_unavailable", "Yahoo chart response is unsuccessful", retryable=True
        )
    results = chart.get("result")
    if not isinstance(results, list) or not results or not isinstance(results[0], Mapping):
        raise ResearchCapabilityError("research_market_output_invalid", "Yahoo chart has no result")
    result = results[0]
    meta = result.get("meta")
    if (
        not isinstance(meta, Mapping)
        or meta.get("symbol") != expected_symbol
        or meta.get("dataGranularity") != "1d"
    ):
        raise ResearchCapabilityError(
            "research_market_output_invalid", "Yahoo daily symbol/granularity is unverified"
        )
    timestamps = result.get("timestamp")
    indicators = result.get("indicators")
    quotes = indicators.get("quote") if isinstance(indicators, Mapping) else None
    closes = quotes[0].get("close") if isinstance(quotes, list) and quotes else None
    if (
        not isinstance(timestamps, list)
        or not isinstance(closes, list)
        or len(timestamps) != len(closes)
    ):
        raise ResearchCapabilityError(
            "research_market_output_invalid", "Yahoo chart has no daily close series"
        )
    starts = [_epoch(value) for value in timestamps]
    if any(value is None for value in starts):
        raise ResearchCapabilityError("research_market_output_invalid", "Yahoo bar time is invalid")
    valid_starts = [value for value in starts if value is not None]
    if any(before >= after for before, after in zip(valid_starts, valid_starts[1:], strict=False)):
        raise ResearchCapabilityError(
            "research_market_output_invalid", "Yahoo daily bars must be strictly ordered"
        )
    trading = meta.get("currentTradingPeriod")
    regular = trading.get("regular") if isinstance(trading, Mapping) else None
    regular_start = _epoch(regular.get("start")) if isinstance(regular, Mapping) else None
    regular_end = _epoch(regular.get("end")) if isinstance(regular, Mapping) else None
    if regular_start is not None and regular_end is not None and regular_end <= regular_start:
        regular_end = None
    cutoff = _aware_clock(cutoff_at)
    rows: list[_DailyClose] = []
    for index, (when, raw_close) in enumerate(zip(valid_starts, closes, strict=True)):
        if raw_close is None:
            continue
        try:
            if isinstance(raw_close, bool):
                raise ValueError("boolean daily close")
            value = Decimal(str(raw_close))
        except (TypeError, ValueError, InvalidOperation) as exc:
            raise ResearchCapabilityError(
                "research_market_output_invalid", "Yahoo daily close is invalid"
            ) from exc
        if not value.is_finite():
            raise ResearchCapabilityError(
                "research_market_output_invalid", "Yahoo daily close must be finite"
            )
        if when == regular_start:
            completion = regular_end
            basis = "provider_current_regular_end"
        else:
            completion = valid_starts[index + 1] if index + 1 < len(valid_starts) else None
            basis = "next_daily_bar_start"
        # A bar start proves neither publication nor completion. Use the
        # matching declared end, or a conservative subsequent-bar boundary.
        if completion is not None and when < completion <= cutoff:
            rows.append(_DailyClose(when, value, completion, basis, requested_at))
    if len(rows) < 6:
        raise ResearchCapabilityError(
            "research_market_output_invalid",
            "Yahoo chart requires six completed daily observations",
        )
    return rows


def _build_item(
    *,
    query: ResearchCapabilityQuery,
    item: _Series,
    url: str,
    rows: list[_DailyClose],
    requested: set[str],
    received_at: datetime,
    provider_id: str,
) -> tuple[EvidenceCandidate, list[FactEnvelope]]:
    baseline_row, latest_row = rows[-6], rows[-1]
    baseline_at, baseline = baseline_row.start_at, baseline_row.value
    latest_at, latest = latest_row.start_at, latest_row.value
    if baseline == 0:
        raise ResearchCapabilityError(
            "research_market_output_invalid", "Yahoo baseline cannot be zero"
        )
    return_5d = (latest - baseline) / baseline * Decimal("100")
    change_5d = (
        (latest - baseline) * Decimal("100") if item.metric_family == "macro.rates" else return_5d
    )
    values = {
        "level": (latest, item.level_unit),
        "change_5d": (change_5d, "bps" if item.metric_family == "macro.rates" else "percent"),
        "return_5d": (return_5d, "percent"),
    }
    excerpt = json.dumps(
        {
            "provider": provider_id,
            "instrument": item.instrument,
            "latest_date": latest_at.date().isoformat(),
            "level": str(latest),
            "baseline_date": baseline_at.date().isoformat(),
            "baseline": str(baseline),
            "change_5d": str(change_5d),
            "return_5d": str(return_5d),
            "completed_sessions": len(rows),
            **latest_row.coordinates(),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    content_hash = research_evidence_content_hash(
        requirement_id=query.requirement_id,
        kind="market",
        authority="audited_aggregator",
        source_id=provider_id,
        source_url=url,
        published_at=None,
        excerpt=excerpt,
        structured_payload_ref=None,
    )
    candidate = EvidenceCandidate(
        evidence_id=research_evidence_instance_id(
            content_hash=content_hash, research_session_id=query.research_session_id
        ),
        requirement_id=query.requirement_id,
        kind="market",
        authority="audited_aggregator",
        source_id=provider_id,
        source_url=AnyUrl(url),
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
    facts = [
        _fact(
            query=query,
            candidate=candidate,
            item=item,
            field=field,
            value=values[field][0],
            unit=values[field][1],
            baseline_at=baseline_at,
            latest_at=latest_at,
            provider_id=provider_id,
            received_at=received_at,
            latest_row=latest_row,
        )
        for field in sorted(requested)
    ]
    return candidate, facts


def _fact(
    *,
    query: ResearchCapabilityQuery,
    candidate: EvidenceCandidate,
    item: _Series,
    field: str,
    value: Decimal,
    unit: str,
    baseline_at: datetime,
    latest_at: datetime,
    provider_id: str,
    received_at: datetime,
    latest_row: _DailyClose,
) -> FactEnvelope:
    is_window = field != "level"
    attributes: dict[str, float | str | bool | None] = {
        "provider_symbol": item.yahoo_symbol,
        # FactEnvelope normalizes numbers in attributes to float. Hash the
        # normalized representation so gateway attestation is stable.
        "window_sessions": 5.0,
        "completed_daily_only": True,
        "authority_limit": "audited_aggregator_not_official_release",
        **latest_row.coordinates(),
    }
    payload_hash = research_fact_payload_hash(
        requirement_id=query.requirement_id,
        metric_family=item.metric_family,
        field=field,
        instrument=item.instrument,
        venue=None,
        value=str(value),
        unit=unit,
        window_start_at=baseline_at if is_window else None,
        window_end_at=latest_at if is_window else None,
        event_offset=None,
        published_at=None,
        source_id=provider_id,
        independence_group=f"yahoo-market:{item.instrument}",
        delay_class="delayed",
        payload_schema_ref="yahoo.chart.daily.v8",
        attributes=attributes,
    )
    return FactEnvelope(
        schema_version="fact-envelope.v1",
        fact_id=research_fact_instance_id(
            evidence_id=candidate.evidence_id, payload_hash=payload_hash
        ),
        evidence_id=candidate.evidence_id,
        requirement_id=query.requirement_id,
        metric_family=item.metric_family,
        field=field,
        instrument=item.instrument,
        venue=None,
        value=str(value),
        unit=unit,
        window_start_at=baseline_at if is_window else None,
        window_end_at=latest_at if is_window else None,
        event_offset=None,
        observed_at=received_at,
        received_at=received_at,
        published_at=None,
        source_id=provider_id,
        independence_group=f"yahoo-market:{item.instrument}",
        quality="candidate",
        delay_class="delayed",
        payload_schema_ref="yahoo.chart.daily.v8",
        payload_hash=payload_hash,
        attributes=attributes,
    )

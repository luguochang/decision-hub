from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import cast
from urllib.parse import urlencode

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


@dataclass(frozen=True)
class _SeriesPoint:
    observed_at: datetime
    event_offset: str | None
    values: Mapping[str, object]
    source_id: str
    independence_group: str
    source_url: str


async def fetch_json(url: str) -> Mapping[str, object]:
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
            payload = response.json()
    except httpx.TimeoutException as exc:
        raise ResearchCapabilityError(
            "provider_timeout",
            "intraday macro provider request timed out",
            retryable=True,
            origin="transport",
            cause_code="timeout",
        ) from exc
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        raise ResearchCapabilityError(
            "provider_rate_limited" if status == 429 else "provider_upstream_unavailable"
            if status >= 500
            else "provider_request_rejected",
            f"intraday macro provider returned HTTP {status}",
            retryable=status == 429 or status >= 500,
            origin="provider",
            cause_code=f"http_{status}",
        ) from exc
    except httpx.TransportError as exc:
        raise ResearchCapabilityError(
            "provider_transport_unavailable",
            "intraday macro provider transport is unavailable",
            retryable=True,
            origin="transport",
            cause_code=type(exc).__name__.lower(),
        ) from exc
    if not isinstance(payload, dict):
        raise ResearchCapabilityError(
            "provider_output_invalid",
            "intraday macro provider returned a non-object payload",
            retryable=False,
            origin="provider",
            cause_code="payload_type",
        )
    return payload


class IntradayMacroResearchAdapter:
    """Map an explicitly configured series endpoint to macro FactEnvelope values.

    The adapter accepts a small provider-neutral fixture shape so providers can
    be replaced without changing the Hub contract. A route may mark the result
    ``delayed``/``free_proxy``; the domain Gate then correctly refuses it for
    realtime requirements.
    """

    capability_id = "macro.cross_asset_intraday"
    supported_modes = frozenset({"live"})
    supported_fields = frozenset({"level", "event_return"})

    def __init__(
        self,
        *,
        provider_id: str,
        base_url: str | None,
        delay_class: str = "delayed",
        authority: str = "audited_aggregator",
        estimated_cost_usd: float | None = None,
        fetcher: JsonFetcher | None = None,
    ) -> None:
        self.provider_id = provider_id
        self.base_url = (base_url or "").rstrip("/")
        self.delay_class = delay_class
        self.authority = authority
        self.estimated_cost_usd = estimated_cost_usd
        self.fetcher = fetcher or fetch_json

    async def execute(self, query: ResearchCapabilityQuery) -> ResearchCapabilityResult:
        if not self.base_url:
            raise ResearchCapabilityError(
                "provider_unconfigured",
                "intraday macro provider endpoint is not configured",
                retryable=False,
                origin="gateway",
                cause_code="endpoint_missing",
            )
        if not query.symbols:
            raise ResearchCapabilityError(
                "research_symbols_required",
                "intraday macro capability requires symbols",
                retryable=False,
                origin="provider",
            )
        fields = set(query.fields) or {"level"}
        if not fields <= self.supported_fields:
            raise ResearchCapabilityError(
                "research_macro_field_invalid",
                "unsupported intraday macro field requested",
                retryable=False,
                origin="provider",
            )
        candidates: list[EvidenceCandidate] = []
        facts: list[FactEnvelope] = []
        for symbol in query.symbols:
            url = series_url(self.base_url, symbol, query)
            payload = await self.fetcher(url)
            points = _points(
                payload,
                default_source_id=self.provider_id,
                default_source_url=url,
            )
            selected_points = _select_points(points, query)
            if not selected_points:
                raise ResearchCapabilityError(
                    "research_macro_data_unavailable",
                    f"intraday macro provider has no PIT-eligible point for {symbol}",
                    retryable=True,
                    origin="provider",
                )
            for point in selected_points:
                selected = {
                    field: point.values[field]
                    for field in sorted(fields)
                    if field in point.values and point.values[field] is not None
                }
                if not selected:
                    continue
                excerpt = json.dumps(
                    {
                        "provider": self.provider_id,
                        "source_id": point.source_id,
                        "symbol": symbol,
                        "observed_at": point.observed_at.isoformat(),
                        "event_offset": point.event_offset,
                        "values": selected,
                        "delay_class": self.delay_class,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )[:4000]
                content_hash = research_evidence_content_hash(
                    requirement_id=query.requirement_id,
                    kind="market",
                    authority=self.authority,  # type: ignore[arg-type]
                    source_id=point.source_id,
                    source_url=point.source_url,
                    published_at=point.observed_at,
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
                    authority=self.authority,  # type: ignore[arg-type]
                    source_id=point.source_id,
                    source_url=AnyUrl(point.source_url),
                    published_at=point.observed_at,
                    observed_at=point.observed_at,
                    received_at=query.observed_at.astimezone(UTC),
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
                for field, value in selected.items():
                    facts.append(
                        _fact(
                            query=query,
                            candidate=candidate,
                            symbol=symbol,
                            field=field,
                            value=value,
                            event_offset=point.event_offset,
                            observed_at=point.observed_at,
                            received_at=query.observed_at.astimezone(UTC),
                            delay_class=self.delay_class,
                            independence_group=point.independence_group,
                        )
                    )
        if not candidates:
            raise ResearchCapabilityError(
                "provider_output_invalid",
                "intraday macro provider returned no requested values",
                retryable=False,
                origin="provider",
            )
        return ResearchCapabilityResult(
            schema_version="research-capability-result.v1",
            request_id=query.request_id,
            capability_id=query.capability_id,
            provider=self.provider_id,
            evidence_candidates=candidates,
            facts=facts,
            cost_usd=self.estimated_cost_usd,
            completed_at=query.observed_at.astimezone(UTC),
        )


def _points(
    payload: Mapping[str, object],
    *,
    default_source_id: str,
    default_source_url: str,
) -> list[_SeriesPoint]:
    raw = payload.get("data") or payload.get("observations")
    if not isinstance(raw, list):
        raise ResearchCapabilityError(
            "provider_output_invalid",
            "intraday macro payload has no data list",
            retryable=False,
            origin="provider",
            cause_code="data_missing",
        )
    output: list[_SeriesPoint] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        typed_item = cast(Mapping[str, object], item)
        timestamp = (
            typed_item.get("timestamp")
            or typed_item.get("observed_at")
            or typed_item.get("time")
        )
        if not isinstance(timestamp, str):
            continue
        try:
            observed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError:
            continue
        if observed.tzinfo is None:
            continue
        raw_values = typed_item.get("values")
        values = (
            cast(Mapping[str, object], raw_values)
            if isinstance(raw_values, Mapping)
            else typed_item
        )
        source_id = _optional_nonempty_string(typed_item.get("source_id")) or default_source_id
        independence_group = (
            _optional_nonempty_string(typed_item.get("independence_group")) or source_id
        )
        source_url = (
            _optional_nonempty_string(typed_item.get("source_url")) or default_source_url
        )
        event_offset = _optional_nonempty_string(typed_item.get("event_offset"))
        output.append(
            _SeriesPoint(
                observed_at=observed.astimezone(UTC),
                event_offset=event_offset,
                values=values,
                source_id=source_id,
                independence_group=independence_group,
                source_url=source_url,
            )
        )
    return output


def _select_points(
    points: Sequence[_SeriesPoint], query: ResearchCapabilityQuery
) -> list[_SeriesPoint]:
    eligible = [item for item in points if item.observed_at <= query.cutoff_at.astimezone(UTC)]
    requested = set(query.requested_event_offsets or [])
    if requested:
        return [item for item in eligible if item.event_offset in requested]
    latest = max(eligible, key=lambda item: item.observed_at) if eligible else None
    return [latest] if latest is not None else []


def series_url(base_url: str, symbol: str, query: ResearchCapabilityQuery) -> str:
    params: list[tuple[str, str]] = [("symbol", symbol)]
    if query.event_id is not None:
        params.append(("event_id", query.event_id))
    if query.event_at is not None:
        params.append(("event_at", query.event_at.astimezone(UTC).isoformat()))
    for offset in query.requested_event_offsets or []:
        params.append(("event_offset", offset))
    params.append(("cutoff_at", query.cutoff_at.astimezone(UTC).isoformat()))
    return f"{base_url}?{urlencode(params)}"


def _optional_nonempty_string(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def _fact(
    *,
    query: ResearchCapabilityQuery,
    candidate: EvidenceCandidate,
    symbol: str,
    field: str,
    value: object,
    event_offset: str | None,
    observed_at: datetime,
    received_at: datetime,
    delay_class: str,
    independence_group: str,
) -> FactEnvelope:
    try:
        normalized = str(Decimal(str(value)))
    except (InvalidOperation, ValueError):
        raise ResearchCapabilityError(
            "provider_output_invalid",
            f"intraday macro value for {symbol}/{field} is not numeric",
            retryable=False,
            origin="provider",
            cause_code="value_not_numeric",
        ) from None
    rates = {"US2Y", "US10Y", "DGS2", "DGS10", "UST2Y", "UST10Y"}
    metric_family = "macro.rates" if symbol.upper() in rates else "macro.usd"
    unit = "yield_percent" if metric_family == "macro.rates" else "index"
    if field == "event_return":
        unit = "bps" if metric_family == "macro.rates" else "percent"
    attributes: dict[str, float | str | bool | None] = {
        "provider_id": candidate.source_id,
        "proxy_kind": "intraday_series",
    }
    payload_schema_ref = "macro.intraday-series.v1"
    payload_hash = research_fact_payload_hash(
        requirement_id=query.requirement_id,
        metric_family=metric_family,
        field=field,
        instrument=symbol,
        venue=None,
        value=normalized,
        unit=unit,
        window_start_at=None,
        window_end_at=None,
        event_offset=event_offset,
        published_at=candidate.published_at,
        source_id=candidate.source_id,
        independence_group=independence_group,
        delay_class=delay_class,
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
        instrument=symbol,
        venue=None,
        value=normalized,
        unit=unit,
        window_start_at=None,
        window_end_at=None,
        event_offset=event_offset,
        observed_at=observed_at,
        received_at=received_at,
        published_at=candidate.published_at,
        source_id=candidate.source_id,
        independence_group=independence_group,
        quality="candidate",
        delay_class=delay_class,  # type: ignore[arg-type]
        payload_schema_ref=payload_schema_ref,
        payload_hash=payload_hash,
        attributes=attributes,
    )

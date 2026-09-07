from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import cast

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

from .intraday import fetch_json, series_url

JsonFetcher = Callable[[str], Awaitable[Mapping[str, object]]]


class ExpectationPricingResearchAdapter:
    """Typed policy-expectation adapter with an explicit licensed/live seam.

    The provider-neutral payload is intentionally small: each observation has a
    timestamp, optional event offset, and ``level``/``delta`` values. A public
    proxy can be configured with ``delay_class=delayed``; it will remain outside
    the realtime semantic Gate. A licensed endpoint can use the same adapter
    with ``delay_class=realtime`` after its license and canary are approved.
    """

    capability_id = "macro.expectation_pricing"
    supported_modes = frozenset({"live"})
    supported_fields = frozenset({"level", "delta"})

    def __init__(
        self,
        *,
        provider_id: str,
        base_url: str | None,
        delay_class: str = "unknown",
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
                "expectation-pricing provider endpoint is not configured",
                retryable=False,
                origin="gateway",
                cause_code="endpoint_missing",
            )
        symbols = query.symbols or ["policy"]
        fields = set(query.fields) or set(self.supported_fields)
        if not fields <= self.supported_fields:
            raise ResearchCapabilityError(
                "research_expectation_field_invalid",
                "unsupported expectation-pricing field requested",
                retryable=False,
                origin="provider",
            )
        candidates: list[EvidenceCandidate] = []
        facts: list[FactEnvelope] = []
        for symbol in symbols:
            url = series_url(self.base_url, symbol, query)
            payload = await self.fetcher(url)
            points = _points(payload)
            selected_points = _select_points(points, query)
            if not selected_points:
                raise ResearchCapabilityError(
                    "research_expectation_data_unavailable",
                    f"expectation-pricing provider has no PIT-eligible point for {symbol}",
                    retryable=True,
                    origin="provider",
                )
            for observed_at, event_offset, values in selected_points:
                selected = {
                    field: values[field]
                    for field in sorted(fields)
                    if field in values and values[field] is not None
                }
                if not selected:
                    continue
                excerpt = json.dumps(
                    {
                        "provider": self.provider_id,
                        "symbol": symbol,
                        "observed_at": observed_at.isoformat(),
                        "event_offset": event_offset,
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
                    source_id=self.provider_id,
                    source_url=url,
                    published_at=observed_at,
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
                    source_id=self.provider_id,
                    source_url=AnyUrl(url),
                    published_at=observed_at,
                    observed_at=observed_at,
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
                            event_offset=event_offset,
                            observed_at=observed_at,
                            received_at=query.observed_at.astimezone(UTC),
                            delay_class=self.delay_class,
                        )
                    )
        if not candidates:
            raise ResearchCapabilityError(
                "provider_output_invalid",
                "expectation-pricing provider returned no requested values",
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
) -> list[tuple[datetime, str | None, Mapping[str, object]]]:
    raw = payload.get("data") or payload.get("observations")
    if not isinstance(raw, list):
        raise ResearchCapabilityError(
            "provider_output_invalid",
            "expectation-pricing payload has no data list",
            retryable=False,
            origin="provider",
            cause_code="data_missing",
        )
    output: list[tuple[datetime, str | None, Mapping[str, object]]] = []
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
        event_offset = typed_item.get("event_offset")
        output.append(
            (
                observed.astimezone(UTC),
                event_offset if isinstance(event_offset, str) else None,
                values,
            )
        )
    return output


def _select_points(
    points: list[tuple[datetime, str | None, Mapping[str, object]]],
    query: ResearchCapabilityQuery,
) -> list[tuple[datetime, str | None, Mapping[str, object]]]:
    eligible = [item for item in points if item[0] <= query.cutoff_at.astimezone(UTC)]
    requested = set(query.requested_event_offsets or [])
    if requested:
        return [item for item in eligible if item[1] in requested]
    latest = max(eligible, key=lambda item: item[0]) if eligible else None
    return [latest] if latest is not None else []


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
) -> FactEnvelope:
    try:
        normalized = str(Decimal(str(value)))
    except (InvalidOperation, ValueError):
        raise ResearchCapabilityError(
            "provider_output_invalid",
            f"expectation value for {symbol}/{field} is not numeric",
            retryable=False,
            origin="provider",
            cause_code="value_not_numeric",
        ) from None
    unit = "probability" if field == "level" else "percentage_point"
    attributes: dict[str, float | str | bool | None] = {
        "provider_id": candidate.source_id,
        "pricing_kind": "policy_expectation",
    }
    payload_schema_ref = "macro.policy-expectation.v1"
    payload_hash = research_fact_payload_hash(
        requirement_id=query.requirement_id,
        metric_family="macro.policy_expectation",
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
        independence_group=candidate.source_id,
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
        metric_family="macro.policy_expectation",
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
        independence_group=candidate.source_id,
        quality="candidate",
        delay_class=delay_class,  # type: ignore[arg-type]
        payload_schema_ref=payload_schema_ref,
        payload_hash=payload_hash,
        attributes=attributes,
    )

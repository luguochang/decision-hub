"""Closed current official series; observation dates are never release timestamps."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Callable, Literal, TypedDict
from urllib.parse import urlsplit

from pydantic import AnyUrl

from packages.contracts_py.decision_hub_contracts import (
    EvidenceCandidate, FactEnvelope, ResearchCapabilityQuery, ResearchCapabilityResult,
)
from packages.kernel.decision_hub_kernel.application.fact_store import (
    research_fact_instance_id, research_fact_payload_hash,
)
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchCapabilityError, research_evidence_content_hash, research_evidence_instance_id,
)
from packages.provider_adapters.research.document_store import StoredDocument
from packages.provider_adapters.research.documents import DocumentFetcher, FetchedDocument
from packages.provider_adapters.research.source_registry import ResearchSourceRegistry

PARSER_REF = "official-fred-current-series.v1"


@dataclass(frozen=True)
class MacroSeries:
    series: str
    metric_family: str
    unit: str
    frequency: str
    publisher: str


SERIES = {
    "usd_reserve_balances": MacroSeries(
        "WRESBAL", "macro.reserve_balances", "million_usd", "weekly_ending_wednesday", "fed-board",
    ),
    "real_yield_10y": MacroSeries(
        "DFII10", "macro.real_yield", "yield_percent", "daily", "fed-board",
    ),
    "inflation_compensation_10y": MacroSeries(
        "T10YIE", "macro.inflation_compensation", "percent", "daily", "st-louis-fed",
    ),
}


@dataclass(frozen=True)
class ParsedCurrentSeries:
    value: str
    observation_date: str
    source_updated_at: datetime


class _MacroFactFields(TypedDict):
    requirement_id: str
    metric_family: str
    field: str
    instrument: str
    venue: None
    value: str
    unit: str
    window_start_at: None
    window_end_at: None
    event_offset: None
    published_at: datetime
    source_id: str
    independence_group: str
    delay_class: Literal["delayed"]
    payload_schema_ref: str
    attributes: dict[str, float | str | bool | None]


def _invalid(code: str, message: str) -> ResearchCapabilityError:
    return ResearchCapabilityError(code, message, retryable=False, origin="provider")


def series_url(spec: MacroSeries) -> str:
    return f"https://fred.stlouisfed.org/series/{spec.series}"


def parse_current_series(document: FetchedDocument, spec: MacroSeries) -> ParsedCurrentSeries:
    url = urlsplit(document.source_url)
    if (url.scheme != "https" or url.netloc != "fred.stlouisfed.org"
            or url.path != f"/series/{spec.series}" or url.query or url.fragment):
        raise _invalid("official_macro_source_denied", "Use the exact fixed official series URL")
    # The existing HTML extractor separates DOM text nodes with newlines.
    # Normalize only parsing whitespace; retain original text for archive/hash.
    text = " ".join(document.text.split())
    if not re.search(r"\(" + re.escape(spec.series) + r"\)", text):
        raise _invalid("official_macro_series_mismatch", "Official page does not identify the series")
    unit_label = "Millions of U.S. Dollars" if spec.series == "WRESBAL" else "Percent"
    frequency_label = r"Weekly,\s*Ending Wednesday" if spec.series == "WRESBAL" else "Daily"
    if (not re.search(r"Units:\s*" + re.escape(unit_label) + r"\s*,\s*Not Seasonally Adjusted", text)
            or not re.search(r"Frequency:\s*" + frequency_label + r"(?:\s|$)", text)):
        raise _invalid("official_macro_metadata_mismatch", "Expected exact official unit and frequency")
    updates = re.findall(
        r"Updated:\s*([A-Z][a-z]{2})\s+(\d{1,2})\s*,\s*(\d{4})\s+(\d{1,2}):(\d{2})\s*(AM|PM)\s+(CDT|CST)",
        text,
    )
    if not updates or len(set(updates)) != 1:
        raise _invalid("official_macro_publication_unknown", "An unambiguous source Updated time is required")
    month, day, year, hour, minute, half, zone = updates[0]
    months = {name: i for i, name in enumerate(
        ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1,
    )}
    try:
        if not 1 <= int(hour) <= 12:
            raise ValueError("hour")
        updated = datetime(
            int(year), months[month], int(day), int(hour) % 12 + (12 if half == "PM" else 0),
            int(minute), tzinfo=timezone(timedelta(hours=-5 if zone == "CDT" else -6)),
        ).astimezone(UTC)
    except (ValueError, KeyError) as exc:
        raise _invalid("official_macro_publication_unknown", "Invalid official Updated timestamp") from exc
    # Include unavailable latest observations so an older numeric point cannot
    # silently become the current reading.
    rows = re.findall(r"(\d{4}-\d{2}-\d{2}):\s*(\S+)", text)
    if not rows:
        raise _invalid("official_macro_value_missing", "No current numeric official observation")
    observation_date, value = rows[0]
    if any(day == max(day for day, _ in rows)
           and re.fullmatch(r"[+-]?[\d,]+(?:\.\d+)?", raw) is None
           for day, raw in rows):
        raise _invalid("official_macro_value_missing", "Latest official observation is unavailable")
    try:
        observation_day = datetime.strptime(observation_date, "%Y-%m-%d").date()
        number = Decimal(value.replace(",", ""))
        if (not number.is_finite() or observation_date != max(day for day, _ in rows)
                or any(day == observation_date and Decimal(v.replace(",", "")) != number
                       for day, v in rows)
                or observation_day > updated.date()
                or (spec.series == "WRESBAL" and (number < 0 or observation_day.weekday() != 2))):
            raise ValueError("observation")
    except (ValueError, InvalidOperation) as exc:
        raise _invalid("official_macro_value_invalid", "Official latest observation is inconsistent") from exc
    if (document.observed_at.tzinfo is None or document.received_at.tzinfo is None
            or not updated <= document.observed_at <= document.received_at):
        raise _invalid("official_macro_time_invalid", "Source update must precede actual observation/receipt")
    lag_days = (document.observed_at.date() - observation_day).days
    if lag_days > (14 if spec.series == "WRESBAL" else 7):
        raise _invalid("official_macro_observation_stale", "Recent metadata update cannot make an old reading current")
    return ParsedCurrentSeries(str(number), observation_date, updated)


async def execute_current_series(
    query: ResearchCapabilityQuery, *, canonical_requirement_id: str,
    fetcher: DocumentFetcher, registry: ResearchSourceRegistry,
    archive: Callable[[FetchedDocument, object], StoredDocument | None],
) -> ResearchCapabilityResult:
    spec = SERIES[canonical_requirement_id]
    if query.mode != "live":
        raise _invalid("official_macro_vintage_unavailable", "Current page cannot prove historical vintage/PIT")
    if (query.target_url is None or str(query.target_url) != series_url(spec)
            or query.symbols not in ([], [spec.series]) or query.fields not in ([], ["level"])
            or query.event_id is not None or query.event_at is not None
            or query.window_start_at is not None or query.window_end_at is not None
            or query.requested_event_offsets):
        raise _invalid("official_macro_query_denied", "Use the declared current series without event windows")
    policy = registry.require(series_url(spec), usage="evidence", requirement_id=query.requirement_id)
    registry.require(series_url(spec), usage="fetch", requirement_id=query.requirement_id)
    try:
        document = await fetcher(series_url(spec))
    except ResearchCapabilityError:
        raise
    except Exception as exc:
        raise ResearchCapabilityError(
            "official_macro_transport_unavailable", "Official current series request failed",
            retryable=True, origin="transport", cause_code=type(exc).__name__.lower(),
        ) from exc
    parsed = parse_current_series(document, spec)
    if document.received_at > query.cutoff_at or parsed.source_updated_at > query.cutoff_at:
        raise _invalid("official_macro_after_cutoff", "Current official data was not available by cutoff")
    # Archive unchanged original text with explicit parsed update availability metadata.
    asset = archive(replace(document, published_at=parsed.source_updated_at), policy)
    raw_hash = hashlib.sha256(document.text.encode()).hexdigest()
    attributes: dict[str, float | str | bool | None] = {
        "series": spec.series, "observation_date": parsed.observation_date,
        "frequency": spec.frequency, "source_updated_at": parsed.source_updated_at.isoformat(),
        "publication_time_kind": "fred_series_updated", "vintage_proven": False,
        "original_publisher": spec.publisher, "source_text_sha256": raw_hash,
        "parser_ref": PARSER_REF,
    }
    excerpt = json.dumps(
        {**attributes, "value": parsed.value, "unit": spec.unit},
        sort_keys=True, ensure_ascii=False, separators=(",", ":"),
    )
    structured_ref = f"source-registry://{policy.source_ref}"
    content_hash = research_evidence_content_hash(
        requirement_id=query.requirement_id, kind="official", authority="official",
        source_id=policy.source_ref, source_url=series_url(spec),
        published_at=parsed.source_updated_at, excerpt=excerpt, structured_payload_ref=structured_ref,
    )
    evidence = EvidenceCandidate(
        evidence_id=research_evidence_instance_id(
            content_hash=content_hash, research_session_id=query.research_session_id,
            content_ref=asset.content_ref if asset else None,
            acquisition_receipt_id=document.acquisition_receipt_id,
        ), requirement_id=query.requirement_id, kind="official", authority="official",
        source_id=policy.source_ref, source_url=AnyUrl(series_url(spec)),
        published_at=parsed.source_updated_at, observed_at=document.observed_at,
        received_at=document.received_at, content_hash=content_hash, excerpt=excerpt,
        content_ref=asset.content_ref if asset else None,
        source_content_hash=asset.source_content_hash if asset else None,
        parser_ref=asset.parser_ref if asset else None, structured_payload_ref=structured_ref,
        acquisition_receipt_id=document.acquisition_receipt_id,
        tool_call_id=query.request_id, research_session_id=query.research_session_id,
        round=query.round, quality="candidate", freshness_status="unknown", conflict_group=None,
    )
    fields: _MacroFactFields = {
        "requirement_id": query.requirement_id, "metric_family": spec.metric_family, "field": "level",
        "instrument": spec.series, "venue": None, "value": parsed.value, "unit": spec.unit,
        "window_start_at": None, "window_end_at": None, "event_offset": None,
        "published_at": parsed.source_updated_at, "source_id": policy.source_ref,
        "independence_group": spec.publisher, "delay_class": "delayed", "payload_schema_ref": PARSER_REF,
        "attributes": attributes,
    }
    payload_hash = research_fact_payload_hash(**fields)
    fact = FactEnvelope(
        schema_version="fact-envelope.v1",
        fact_id=research_fact_instance_id(evidence_id=evidence.evidence_id, payload_hash=payload_hash),
        evidence_id=evidence.evidence_id, observed_at=document.observed_at,
        received_at=document.received_at, quality="candidate", payload_hash=payload_hash, **fields,
    )
    return ResearchCapabilityResult(
        schema_version="research-capability-result.v1", request_id=query.request_id,
        capability_id=query.capability_id, provider=policy.source_ref,
        evidence_candidates=[evidence], facts=[fact], cost_usd=0.0, completed_at=document.received_at,
    )

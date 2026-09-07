from __future__ import annotations

import asyncio
import csv
import io
import json
from collections.abc import Awaitable, Callable
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

TextFetcher = Callable[[str], Awaitable[str]]


async def _fetch_text(url: str) -> str:
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.text


class FredSeriesResearchAdapter:
    capability_id = "market.cross_asset"
    supported_modes = frozenset({"live"})

    def __init__(
        self,
        *,
        fetcher: TextFetcher | None = None,
        series_allowlist: frozenset[str] = frozenset({"DGS2", "DGS10", "DTWEXBGS"}),
        base_url: str = "https://fred.stlouisfed.org",
    ) -> None:
        self.fetcher = fetcher or _fetch_text
        self.series_allowlist = series_allowlist
        self.base_url = base_url.rstrip("/")

    async def execute(self, query: ResearchCapabilityQuery) -> ResearchCapabilityResult:
        if not query.symbols or not set(query.symbols) <= self.series_allowlist:
            raise ResearchCapabilityError(
                "research_macro_series_denied", "FRED series is missing or outside the allowlist"
            )
        urls = [f"{self.base_url}/graph/fredgraph.csv?id={series}" for series in query.symbols]
        payloads = await asyncio.gather(*(self.fetcher(url) for url in urls))
        received_at = datetime.now(UTC)
        candidates = [
            self._candidate(query, series, url, payload, received_at)
            for series, url, payload in zip(query.symbols, urls, payloads, strict=True)
        ]
        facts = [
            _fred_fact(query, candidate, series, received_at)
            for candidate, series in zip(candidates, query.symbols, strict=True)
        ]
        return ResearchCapabilityResult(
            schema_version="research-capability-result.v1",
            request_id=query.request_id,
            capability_id=query.capability_id,
            provider="fred-public",
            evidence_candidates=candidates,
            facts=facts,
            cost_usd=0.0,
            completed_at=received_at,
        )

    @staticmethod
    def _candidate(
        query: ResearchCapabilityQuery,
        series: str,
        url: str,
        payload: str,
        received_at: datetime,
    ) -> EvidenceCandidate:
        rows = list(csv.DictReader(io.StringIO(payload)))
        eligible: list[tuple[datetime, str]] = []
        for row in rows:
            date_text = row.get("DATE") or row.get("observation_date")
            value = row.get(series)
            if not date_text or not isinstance(value, str) or value in {"", "."}:
                continue
            try:
                published_at = datetime.fromisoformat(date_text).replace(tzinfo=UTC)
            except ValueError:
                continue
            if published_at <= query.cutoff_at:
                eligible.append((published_at, value))
        if not eligible:
            raise ResearchCapabilityError(
                "research_macro_data_unavailable", "FRED has no PIT-eligible observation"
            )
        published_at, value = max(eligible, key=lambda item: item[0])
        excerpt = json.dumps(
            {"series": series, "value": value, "observation_date": published_at.date().isoformat()},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        content_hash = research_evidence_content_hash(
            requirement_id=query.requirement_id,
            kind="market",
            authority="official",
            source_id="fred-public",
            source_url=url,
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
            authority="official",
            source_id="fred-public",
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


def _fred_fact(
    query: ResearchCapabilityQuery,
    candidate: EvidenceCandidate,
    series: str,
    received_at: datetime,
) -> FactEnvelope:
    payload = json.loads(candidate.excerpt)
    metric_family = "macro.rates" if series.startswith("DGS") else "macro.usd"
    unit = "yield_percent" if series.startswith("DGS") else "index"
    attributes: dict[str, float | str | bool | None] = {"series": series}
    payload_schema_ref = "fred.series-observation.v1"
    payload_hash = research_fact_payload_hash(
        requirement_id=query.requirement_id,
        metric_family=metric_family,
        field="level",
        instrument=series,
        venue=None,
        value=str(payload["value"]),
        unit=unit,
        window_start_at=None,
        window_end_at=None,
        event_offset=None,
        published_at=candidate.published_at,
        source_id=candidate.source_id,
        independence_group=f"fred:{series}",
        delay_class="delayed",
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
        field="level",
        instrument=series,
        venue=None,
        value=str(payload["value"]),
        unit=unit,
        window_start_at=None,
        window_end_at=None,
        event_offset=None,
        observed_at=received_at,
        received_at=received_at,
        published_at=candidate.published_at,
        source_id=candidate.source_id,
        independence_group=f"fred:{series}",
        quality="candidate",
        delay_class="delayed",
        payload_schema_ref=payload_schema_ref,
        payload_hash=payload_hash,
        attributes=attributes,
    )

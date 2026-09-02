from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

import httpx
from bs4 import BeautifulSoup
from pydantic import AnyUrl

from packages.contracts_py.decision_hub_contracts import (
    EvidenceCandidate,
    ResearchCapabilityQuery,
    ResearchCapabilityResult,
)
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchCapabilityError,
    research_evidence_content_hash,
    research_evidence_instance_id,
)


@dataclass(frozen=True)
class FetchedDocument:
    source_url: str
    text: str
    observed_at: datetime
    received_at: datetime
    published_at: datetime | None = None


DocumentFetcher = Callable[[str], Awaitable[FetchedDocument]]


async def _fetch_document(url: str) -> FetchedDocument:
    observed_at = datetime.now(UTC)
    async with httpx.AsyncClient(
        timeout=20,
        follow_redirects=True,
        headers={"User-Agent": "DecisionHubResearch/0.1"},
    ) as client:
        response = await client.get(url)
        response.raise_for_status()
    content_type = response.headers.get("content-type", "")
    if "html" in content_type:
        soup = BeautifulSoup(response.text, "html.parser")
        for node in soup(["script", "style", "noscript"]):
            node.decompose()
        text = " ".join(soup.get_text(" ", strip=True).split())
    else:
        text = " ".join(response.text.split())
    return FetchedDocument(
        source_url=str(response.url),
        text=text,
        observed_at=observed_at,
        received_at=datetime.now(UTC),
    )


class HttpDocumentResearchAdapter:
    supported_modes = frozenset({"live"})

    def __init__(
        self,
        *,
        capability_id: str,
        kind: Literal["official", "market", "web", "transcript", "document"],
        authority: Literal[
            "official",
            "exchange",
            "audited_aggregator",
            "verified_web",
            "search_derived",
            "unverified",
        ],
        source_id: str,
        fetcher: DocumentFetcher | None = None,
    ) -> None:
        self.capability_id = capability_id
        self.kind: Literal["official", "market", "web", "transcript", "document"] = kind
        self.authority: Literal[
            "official",
            "exchange",
            "audited_aggregator",
            "verified_web",
            "search_derived",
            "unverified",
        ] = authority
        self.source_id = source_id
        self.fetcher = fetcher or _fetch_document

    async def execute(self, query: ResearchCapabilityQuery) -> ResearchCapabilityResult:
        if query.target_url is None:
            raise ResearchCapabilityError(
                "research_target_required", "document capability requires a target URL"
            )
        document = await self.fetcher(str(query.target_url))
        excerpt = document.text[:4000]
        if not excerpt:
            raise ResearchCapabilityError(
                "research_document_empty", "document capability returned no readable text"
            )
        content_hash = research_evidence_content_hash(
            requirement_id=query.requirement_id,
            kind=self.kind,
            authority=self.authority,
            source_id=self.source_id,
            source_url=document.source_url,
            published_at=document.published_at,
            excerpt=excerpt,
            structured_payload_ref=None,
        )
        candidate = EvidenceCandidate(
            evidence_id=research_evidence_instance_id(
                content_hash=content_hash,
                research_session_id=query.research_session_id,
            ),
            requirement_id=query.requirement_id,
            kind=self.kind,
            authority=self.authority,
            source_id=self.source_id,
            source_url=AnyUrl(document.source_url),
            published_at=document.published_at,
            observed_at=document.observed_at,
            received_at=document.received_at,
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
        return ResearchCapabilityResult(
            schema_version="research-capability-result.v1",
            request_id=query.request_id,
            capability_id=query.capability_id,
            provider=self.source_id,
            evidence_candidates=[candidate],
            cost_usd=0.0,
            completed_at=document.received_at,
        )

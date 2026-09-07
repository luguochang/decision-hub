from __future__ import annotations

from urllib.parse import urlsplit

from packages.contracts_py.decision_hub_contracts import (
    EvidenceCandidate,
    ResearchCapabilityQuery,
    ResearchCapabilityResult,
    SearchQuery,
)
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    research_evidence_content_hash,
    research_evidence_instance_id,
)
from packages.kernel.decision_hub_kernel.ports.search import SearchCapabilityPort


class WebSearchResearchAdapter:
    supported_modes = frozenset({"live"})

    def __init__(self, search: SearchCapabilityPort, *, capability_id: str = "web.search") -> None:
        self.search = search
        self.capability_id = capability_id

    async def execute(self, query: ResearchCapabilityQuery) -> ResearchCapabilityResult:
        result = await self.search.search(
            SearchQuery(
                request_id=query.request_id,
                capability_id=query.capability_id,
                query=query.query,
                allowed_domains=query.allowed_domains,
                max_results=query.max_results,
                max_cost_usd=query.max_cost_usd,
                observed_at=query.observed_at,
            )
        )
        candidates: list[EvidenceCandidate] = []
        seen_content_hashes: set[str] = set()
        for item in result.evidence:
            source_url = str(item.source_url)
            source_id = _publisher_source_id(source_url, result.provider)
            content_hash = research_evidence_content_hash(
                requirement_id=query.requirement_id,
                kind="web",
                authority="search_derived",
                source_id=source_id,
                source_url=source_url,
                published_at=item.published_at,
                excerpt=item.snippet,
                structured_payload_ref=None,
            )
            # Search vendors can repeat an identical result in one response
            # (for example, once as a news hit and once as a web hit). The
            # canonical evidence identity is content-based, so forwarding both
            # rows would make the Gateway reject the whole batch as a duplicate
            # and discard otherwise usable locators. Keep the first occurrence
            # deterministic and leave provider-level deduplication to this
            # shared adapter rather than each transport.
            if content_hash in seen_content_hashes:
                continue
            seen_content_hashes.add(content_hash)
            candidates.append(
                EvidenceCandidate(
                    evidence_id=research_evidence_instance_id(
                        content_hash=content_hash,
                        research_session_id=query.research_session_id,
                    ),
                    requirement_id=query.requirement_id,
                    kind="web",
                    authority="search_derived",
                    source_id=source_id,
                    source_url=item.source_url,
                    published_at=item.published_at,
                    observed_at=item.observed_at,
                    received_at=item.received_at,
                    content_hash=content_hash,
                    excerpt=item.snippet,
                    structured_payload_ref=None,
                    tool_call_id=query.request_id,
                    research_session_id=query.research_session_id,
                    round=query.round,
                    quality="candidate",
                    freshness_status="unknown",
                    conflict_group=None,
                )
            )
        return ResearchCapabilityResult(
            schema_version="research-capability-result.v1",
            request_id=query.request_id,
            capability_id=query.capability_id,
            provider=result.provider,
            evidence_candidates=candidates,
            cost_usd=result.cost_usd,
            completed_at=result.completed_at,
        )


def _publisher_source_id(source_url: str, provider: str) -> str:
    hostname = (urlsplit(source_url).hostname or provider).lower().rstrip(".")
    return hostname.removeprefix("www.")

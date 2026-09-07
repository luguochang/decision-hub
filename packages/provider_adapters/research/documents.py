from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import urlsplit

import httpx
from bs4 import BeautifulSoup
from pydantic import AnyUrl

from packages.contracts_py.decision_hub_contracts import (
    EvidenceCandidate,
    FactEnvelope,
    ResearchCapabilityQuery,
    ResearchCapabilityResult,
)
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchCapabilityError,
    research_evidence_content_hash,
    research_evidence_instance_id,
)

from .source_registry import ResearchSourceRegistry, ResearchSourceRegistryError


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
    try:
        async with httpx.AsyncClient(
            timeout=20,
            follow_redirects=True,
            headers={"User-Agent": "DecisionHubResearch/0.1"},
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        raise _classify_fetch_error(exc) from exc
    text = _extract_response_text(response)
    return FetchedDocument(
        source_url=str(response.url),
        text=text,
        observed_at=observed_at,
        received_at=datetime.now(UTC),
    )


def _extract_response_text(response: httpx.Response) -> str:
    """Decode only explicit textual media; binary formats need typed adapters."""

    media_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    textual_application_types = {
        "application/atom+xml",
        "application/json",
        "application/ld+json",
        "application/rss+xml",
        "application/xhtml+xml",
        "application/xml",
    }
    if response.content.startswith(b"%PDF") or media_type == "application/pdf":
        raise ResearchCapabilityError(
            "research_document_media_type_unsupported",
            "PDF requires an explicit audited parser before it can become Evidence",
            retryable=False,
            origin="provider",
            cause_code="application/pdf",
        )
    if not (media_type.startswith("text/") or media_type in textual_application_types):
        raise ResearchCapabilityError(
            "research_document_media_type_unsupported",
            "document response is not an approved textual media type",
            retryable=False,
            origin="provider",
            cause_code=media_type or "missing_content_type",
        )
    if "html" in media_type:
        soup = BeautifulSoup(response.text, "html.parser")
        for node in soup(["script", "style", "noscript"]):
            node.decompose()
        text = " ".join(soup.get_text(" ", strip=True).split())
    else:
        text = " ".join(response.text.split())
    if "\x00" in text or text.count("\ufffd") > max(2, len(text) // 100):
        raise ResearchCapabilityError(
            "research_document_decode_invalid",
            "document response could not be decoded as trustworthy text",
            retryable=False,
            origin="provider",
            cause_code=media_type,
        )
    return text


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
        source_registry: ResearchSourceRegistry | None = None,
        parser: Callable[[ResearchCapabilityQuery, FetchedDocument, str], list[FactEnvelope]]
        | None = None,
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
        self.source_registry = source_registry
        self.parser = parser

    async def execute(self, query: ResearchCapabilityQuery) -> ResearchCapabilityResult:
        if query.target_url is None:
            raise ResearchCapabilityError(
                "research_target_required", "document capability requires a target URL"
            )
        try:
            document = await self.fetcher(str(query.target_url))
        except ResearchCapabilityError:
            raise
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            # Keep injected transports and the default HTTP transport on the
            # same stable error taxonomy for the Gateway and continuation.
            raise _classify_fetch_error(exc) from exc
        source_policy = None
        if self.source_registry is not None:
            try:
                source_policy = self.source_registry.require(
                    str(document.source_url), usage="fetch", requirement_id=query.requirement_id
                )
                self.source_registry.require(
                    str(document.source_url),
                    usage="evidence",
                    requirement_id=query.requirement_id,
                )
            except ResearchSourceRegistryError as exc:
                raise ResearchCapabilityError(
                    exc.error_code, str(exc), retryable=False, origin="source_registry"
                ) from exc
        excerpt = document.text[:4000]
        if not excerpt:
            raise ResearchCapabilityError(
                "research_document_empty", "document capability returned no readable text"
            )
        # A generic web fetch must preserve publisher identity. Using one
        # constant `web-fetch` source id would make two independent domains
        # look like one source and would permanently fail the independence
        # Gate. Domain-specific/typed adapters keep their declared source id.
        source_id = self.source_id
        authority = self.authority
        if self.kind == "web":
            source_id = (
                (urlsplit(document.source_url).hostname or self.source_id).lower().rstrip(".")
            )
            source_id = source_id.removeprefix("www.")
            if source_policy is not None:
                authority = source_policy.authority
        structured_ref = (
            f"source-registry://{source_policy.source_ref}" if source_policy is not None else None
        )
        content_hash = research_evidence_content_hash(
            requirement_id=query.requirement_id,
            kind=self.kind,
            authority=authority,
            source_id=source_id,
            source_url=document.source_url,
            published_at=document.published_at,
            excerpt=excerpt,
            structured_payload_ref=structured_ref,
        )
        candidate = EvidenceCandidate(
            evidence_id=research_evidence_instance_id(
                content_hash=content_hash,
                research_session_id=query.research_session_id,
            ),
            requirement_id=query.requirement_id,
            kind=self.kind,
            authority=authority,
            source_id=source_id,
            source_url=AnyUrl(document.source_url),
            published_at=document.published_at,
            observed_at=document.observed_at,
            received_at=document.received_at,
            content_hash=content_hash,
            excerpt=excerpt,
            structured_payload_ref=structured_ref,
            tool_call_id=query.request_id,
            research_session_id=query.research_session_id,
            round=query.round,
            quality="candidate",
            freshness_status="unknown",
            conflict_group=None,
        )
        facts = self.parser(query, document, candidate.evidence_id) if self.parser else []
        return ResearchCapabilityResult(
            schema_version="research-capability-result.v1",
            request_id=query.request_id,
            capability_id=query.capability_id,
            provider=source_id,
            evidence_candidates=[candidate],
            facts=facts,
            cost_usd=0.0,
            completed_at=document.received_at,
        )


def _classify_fetch_error(exc: httpx.HTTPError) -> ResearchCapabilityError:
    """Map HTTP transport failures to stable, actionable provenance codes."""

    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 404:
            return ResearchCapabilityError(
                "research_document_not_found",
                "document provider returned HTTP 404",
                origin="provider",
                cause_code="http_404",
            )
        if status in {401, 403}:
            return ResearchCapabilityError(
                "research_provider_denied",
                f"document provider returned HTTP {status}",
                origin="provider",
                cause_code=f"http_{status}",
            )
        error_code = (
            "search_provider_failed"
            if status == 429 or status >= 500
            else "research_provider_denied"
        )
        return ResearchCapabilityError(
            error_code,
            f"document provider returned HTTP {status}",
            retryable=status == 429 or status >= 500,
            origin="provider",
            cause_code=f"http_{status}",
        )
    if isinstance(exc, httpx.TimeoutException):
        return ResearchCapabilityError(
            "search_provider_failed",
            "document provider request timed out",
            retryable=True,
            origin="transport",
            cause_code="timeout",
        )
    if isinstance(exc, httpx.ConnectError):
        return ResearchCapabilityError(
            "search_provider_failed",
            "document provider connection failed",
            retryable=True,
            origin="transport",
            cause_code="connection_error",
        )
    return ResearchCapabilityError(
        "search_provider_failed",
        "document provider request failed",
        retryable=True,
        origin="transport",
        cause_code=type(exc).__name__.lower(),
    )

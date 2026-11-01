from __future__ import annotations

import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from ipaddress import ip_address
from typing import TYPE_CHECKING, Literal
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
from packages.provider_adapters.research.raw_capture import CapturingAsyncClient

from .publication_time import html_publication_time
from .source_registry import ResearchSourceRegistry, ResearchSourceRegistryError

if TYPE_CHECKING:
    from .document_store import ContentAddressedDocumentStore, StoredDocument


@dataclass(frozen=True)
class FetchedDocument:
    source_url: str
    text: str
    observed_at: datetime
    received_at: datetime
    published_at: datetime | None = None
    acquisition_receipt_id: str | None = None


DocumentFetcher = Callable[[str], Awaitable[FetchedDocument]]


def _public_https_document_target(url: str) -> bool:
    target = urlsplit(url)
    hostname = (target.hostname or "").lower().rstrip(".")
    if (target.scheme != "https" or not hostname
            or hostname == "localhost" or hostname.endswith((".localhost", ".local"))):
        return False
    try:
        address = ip_address(hostname)
    except ValueError:
        address = None
    if address is not None and (not address.is_global or address.is_multicast):
        return False
    return True


def _public_document_proxy(url: str) -> str | None:
    """Use only explicitly configured proxying for external HTTPS documents."""

    if not _public_https_document_target(url):
        return None
    configured = os.environ.get("DECISION_HUB_PUBLIC_HTTP_PROXY")
    if configured is None or configured == "":
        return None
    try:
        proxy = urlsplit(configured)
        if (proxy.scheme not in {"http", "https"} or not proxy.hostname
                or proxy.port is None or not 1 <= proxy.port <= 65535
                or proxy.username is not None or proxy.password is not None
                or proxy.path not in {"", "/"} or proxy.query or proxy.fragment
                or any(char.isspace() for char in configured)
                or "?" in configured or "#" in configured):
            raise ValueError("invalid_proxy_shape")
        checked = httpx.URL(configured)
        if not checked.host:
            raise ValueError("invalid_proxy_host")
    except (ValueError, httpx.InvalidURL):
        raise ResearchCapabilityError(
            "research_document_proxy_config_invalid",
            "Explicit public document proxy configuration is invalid",
            retryable=False,
            origin="provider",
            cause_code="invalid_proxy_url",
        ) from None
    return str(checked)


async def _require_public_proxy_target(request: httpx.Request) -> None:
    if not _public_https_document_target(str(request.url)):
        raise ResearchCapabilityError(
            "research_document_proxy_scope_denied",
            "Configured document proxy requires a public HTTPS target at every hop",
            retryable=False,
            origin="source_registry",
            cause_code="non_public_https_target",
        )


async def _fetch_document(url: str) -> FetchedDocument:
    observed_at = datetime.now(UTC)
    proxy = _public_document_proxy(url)
    try:
        async with CapturingAsyncClient(
            timeout=20,
            follow_redirects=True,
            trust_env=False,
            proxy=proxy,
            headers={"User-Agent": "DecisionHubResearch/0.1"},
        ) as client:
            if proxy is not None:
                client.max_redirects = 5
                client.event_hooks["request"].append(_require_public_proxy_target)
            response = await client.get(url)
            response.raise_for_status()
    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        if proxy is not None and isinstance(exc, httpx.TooManyRedirects):
            raise ResearchCapabilityError(
                "research_document_proxy_redirect_limit",
                "Configured document proxy exceeded its redirect limit",
                retryable=False,
                origin="source_registry",
                cause_code="redirect_limit",
            ) from None
        raise _classify_fetch_error(exc) from exc
    text = _extract_response_text(response)
    published = (html_publication_time(response.text, str(response.url))
                 if "html" in response.headers.get("content-type", "").lower() else None)
    return FetchedDocument(
        source_url=str(response.url),
        text=text,
        observed_at=observed_at,
        received_at=datetime.now(UTC),
        published_at=published,
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
        text = extract_html_text(response.text)
    else:
        text = response.text.strip()
    if "\x00" in text or text.count("\ufffd") > max(2, len(text) // 100):
        raise ResearchCapabilityError(
            "research_document_decode_invalid",
            "document response could not be decoded as trustworthy text",
            retryable=False,
            origin="provider",
            cause_code=media_type,
        )
    return text


def extract_html_text(html: str) -> str:
    """Extract readable HTML text with the same deterministic parser as HTTP fetch."""

    soup = BeautifulSoup(html, "html.parser")
    for node in soup(["script", "style", "noscript", "nav", "footer"]):
        node.decompose()
    body = soup.select_one("article, main, #article") or soup
    return "\n".join(
        line
        for value in body.get_text("\n", strip=True).splitlines()
        if (line := " ".join(value.split()))
    )


class HttpDocumentResearchAdapter:
    supported_modes = frozenset({"live"})
    target_url_required = True

    @property
    def source_query_catalog(self):
        return self.source_registry.query_sources("fetch") if self.source_registry else None

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
        document_store: ContentAddressedDocumentStore | None = None,
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
        self.document_store = document_store
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
        excerpt = _relevant_excerpt(document.text, query.query)
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
        asset = self.archive_document(document, source_policy)
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
                content_ref=asset.content_ref if asset is not None else None,
                acquisition_receipt_id=None,
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
            content_ref=asset.content_ref if asset is not None else None,
            source_content_hash=(asset.source_content_hash if asset is not None else None),
            parser_ref=asset.parser_ref if asset is not None else None,
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

    def archive_document(
        self,
        document: FetchedDocument,
        source_policy: object | None,
    ) -> StoredDocument | None:
        """Persist an original only when the audited source policy permits it."""

        if (
            self.document_store is None
            or source_policy is None
            or getattr(source_policy, "retention_policy", None) != "full_text"
        ):
            return None
        policy_parser = getattr(source_policy, "parser_ref", None)
        parser_ref = f"http-document-main-text.v2+publication-time.v1+{policy_parser or 'untyped'}"
        return self.document_store.write(document, parser_ref=parser_ref)


def relevant_excerpt(text: str, query: str, *, limit: int = 4000) -> str:
    """Select a contiguous verbatim window, keeping qualifiers beside matches.

    This is lexical selection, not proof that the passage supports a claim.
    A new query can select a different window from the same source. Original
    paragraph order and wording remain intact for citation verification.
    """
    if len(text) <= limit:
        return text
    tokens = set(re.findall(r"[a-zA-Z]{3,}|[\u4e00-\u9fff]{2,}", query.casefold()))
    tokens -= {"the", "and", "for", "with", "from", "this", "that", "https", "www", "com"}
    lowered = text.casefold()
    starts = {0}
    for token in tokens:
        for match in re.finditer(re.escape(token), lowered):
            starts.add(max(0, min(match.start() - limit // 4, len(text) - limit)))
    start = max(
        sorted(starts),
        key=lambda index: sum(token in lowered[index : index + limit] for token in tokens),
    )
    # Start at a nearby paragraph boundary when possible; never splice unrelated
    # passages into what appears to be one continuous source quotation.
    boundary = text.rfind("\n", max(0, start - 300), start)
    if boundary >= 0 and start + limit < len(text):
        start = boundary + 1
    return text[start : start + limit]


# Backward-compatible local alias; sibling adapters use the public helper.
_relevant_excerpt = relevant_excerpt


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

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from datetime import UTC
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from pydantic import AnyUrl

from packages.contracts_py.decision_hub_contracts import (
    EvidenceCandidate,
    ResearchCapabilityQuery,
    ResearchCapabilityResult,
)
from packages.contracts_py.decision_hub_contracts.models import SourceAcquisition
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchCapabilityError,
    research_evidence_content_hash,
    research_evidence_instance_id,
)

from .document_store import ContentAddressedDocumentStore
from .documents import (
    FetchedDocument,
    HttpDocumentResearchAdapter,
    extract_html_text,
    relevant_excerpt,
)
from .source_registry import ResearchSourceRegistry, ResearchSourceRegistryError

ReceiptFetcher = Callable[[ResearchCapabilityQuery, str], Awaitable[SourceAcquisition]]

_BROWSER_ROUTES = {
    "federalreserve.gov": ("/monetarypolicy/", "fed-calendar"),
    "ecb.europa.eu": ("/press/calendars/", "ecb-calendar"),
    "boj.or.jp": ("/en/mopo/", "boj-calendar"),
}


async def _http_receipt_fetcher(
    query: ResearchCapabilityQuery,
    source_id: str,
    *,
    base_url: str,
    shared_key: str,
) -> SourceAcquisition:
    if query.target_url is None:
        raise ResearchCapabilityError(
            "research_target_required", "browser readback requires a target URL"
        )
    headers = {"x-decision-hub-source-key": shared_key} if shared_key else {}
    payload = {
        "request_id": query.request_id,
        "source_id": source_id,
        "url": str(query.target_url),
        "modes": ["dom", "network", "screenshot"],
    }
    try:
        # The worker is intentionally bound to loopback.  Do not inherit a
        # process-wide HTTP(S)_PROXY here: in desktop environments that can
        # route 127.0.0.1 through a corporate proxy and return a misleading
        # 502 before the request reaches the local Playwright service.
        async with httpx.AsyncClient(timeout=90, trust_env=False) as client:
            response = await client.post(
                f"{base_url.rstrip('/')}/v1/acquire", json=payload, headers=headers
            )
            # A failed crawl still returns a durable receipt with its specific
            # cause. Preserve it instead of collapsing every 502 to transport.
            if response.status_code == 502:
                try:
                    failed_receipt = SourceAcquisition.model_validate(response.json())
                    if failed_receipt.status != "succeeded":
                        return failed_receipt
                except ValueError:
                    pass
            response.raise_for_status()
            return SourceAcquisition.model_validate(response.json())
    except (httpx.HTTPError, ValueError) as exc:
        raise ResearchCapabilityError(
            "browser_acquisition_failed",
            "source acquisition worker did not return a valid receipt",
            retryable=True,
            origin="provider",
            cause_code=type(exc).__name__.lower(),
            capability_id=query.capability_id,
            tool_call_id=query.request_id,
        ) from exc


class BrowserReadbackResearchAdapter:
    """Use the standalone Playwright worker as a verified research capability.

    The worker owns browser lifecycle and submits an immutable receipt to Hub.
    This adapter only reads the declared DOM artifact, verifies its hash, applies
    the existing deterministic HTML parser and emits a normal EvidenceCandidate
    carrying the receipt id.  It never treats a screenshot or an error page as
    accepted Evidence by itself.
    """

    capability_id = "browser.readback"
    supported_modes = frozenset({"live"})
    target_url_required = True

    @property
    def source_query_catalog(self):
        return self.source_registry.query_sources(
            "evidence", path_scope={host: route[0] for host, route in _BROWSER_ROUTES.items()}
        )

    def __init__(
        self,
        *,
        source_registry: ResearchSourceRegistry,
        document_store: ContentAddressedDocumentStore | None,
        artifact_root: Path,
        base_url: str = "http://127.0.0.1:8038",
        shared_key: str = "",
        fetcher: ReceiptFetcher | None = None,
    ) -> None:
        self.source_registry = source_registry
        self.document_store = document_store
        self.artifact_root = artifact_root.resolve()
        self.base_url = base_url
        self.shared_key = shared_key
        self.fetcher = fetcher or (
            lambda query, source_id: _http_receipt_fetcher(
                query,
                source_id,
                base_url=base_url,
                shared_key=shared_key,
            )
        )

    async def execute(self, query: ResearchCapabilityQuery) -> ResearchCapabilityResult:
        if query.target_url is None:
            raise ResearchCapabilityError(
                "research_target_required", "browser readback requires a target URL"
            )
        source_policy, source_id = self._source_for_url(str(query.target_url), query.requirement_id)
        receipt = await self.fetcher(query, source_id)
        if receipt.status != "succeeded":
            raise ResearchCapabilityError(
                "browser_acquisition_incomplete",
                "browser acquisition did not produce a succeeded receipt",
                retryable=True,
                origin="provider",
                cause_code=receipt.error_code or "incomplete",
                capability_id=query.capability_id,
                tool_call_id=query.request_id,
            )
        observed_at = receipt.observed_at
        if observed_at is None:
            raise ResearchCapabilityError(
                "browser_observation_time_missing",
                "successful browser receipt has no observation time",
                origin="pit",
                capability_id=query.capability_id,
                tool_call_id=query.request_id,
            )
        dom_artifact = next(
            (item for item in receipt.artifacts if item.kind in {"dom", "html"}), None
        )
        if dom_artifact is None:
            raise ResearchCapabilityError(
                "browser_dom_artifact_missing",
                "browser receipt has no DOM artifact",
                origin="provider",
                capability_id=query.capability_id,
                tool_call_id=query.request_id,
            )
        dom_path = self._artifact_path(dom_artifact.path)
        raw = dom_path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != dom_artifact.content_hash:
            raise ResearchCapabilityError(
                "browser_artifact_hash_mismatch",
                "browser DOM artifact hash does not match its receipt",
                origin="storage",
                capability_id=query.capability_id,
                tool_call_id=query.request_id,
            )
        try:
            text = extract_html_text(raw.decode("utf-8"))
        except UnicodeDecodeError as exc:
            raise ResearchCapabilityError(
                "browser_dom_decode_invalid",
                "browser DOM artifact is not valid UTF-8",
                origin="provider",
                capability_id=query.capability_id,
                tool_call_id=query.request_id,
            ) from exc
        excerpt = relevant_excerpt(text, query.query)
        if not excerpt:
            raise ResearchCapabilityError(
                "browser_document_empty",
                "browser DOM contains no readable text",
                origin="provider",
                capability_id=query.capability_id,
                tool_call_id=query.request_id,
            )
        # The Hub receive clock is authoritative when available. Legacy
        # receipts use completion or observation time for replay compatibility.
        received_at = (
            receipt.received_at or receipt.request_completed_at or observed_at
        ).astimezone(UTC)
        if received_at > query.cutoff_at:
            raise ResearchCapabilityError(
                "browser_cutoff_expired",
                "browser receipt arrived after the research cutoff",
                retryable=True,
                origin="pit",
                capability_id=query.capability_id,
                tool_call_id=query.request_id,
            )
        document = FetchedDocument(
            source_url=str(query.target_url),
            text=text,
            observed_at=observed_at,
            received_at=received_at,
        )
        asset = None
        if self.document_store is not None and source_policy.retention_policy == "full_text":
            asset = self.document_store.write(
                document,
                parser_ref=f"playwright-dom-main-text.v1+{source_policy.parser_ref or 'untyped'}",
            )
        structured_ref = f"source-registry://{source_policy.source_ref}"
        content_hash = research_evidence_content_hash(
            requirement_id=query.requirement_id,
            kind="official",
            authority=source_policy.authority,
            source_id=source_id,
            source_url=str(query.target_url),
            published_at=None,
            excerpt=excerpt,
            structured_payload_ref=structured_ref,
        )
        candidate = EvidenceCandidate(
            evidence_id=research_evidence_instance_id(
                content_hash=content_hash,
                research_session_id=query.research_session_id,
                content_ref=asset.content_ref if asset is not None else None,
                acquisition_receipt_id=receipt.receipt_id,
            ),
            requirement_id=query.requirement_id,
            kind="official",
            authority=source_policy.authority,
            source_id=source_id,
            source_url=AnyUrl(str(query.target_url)),
            published_at=None,
            observed_at=observed_at,
            received_at=received_at,
            content_hash=content_hash,
            excerpt=excerpt,
            content_ref=asset.content_ref if asset is not None else None,
            acquisition_receipt_id=receipt.receipt_id,
            source_content_hash=asset.source_content_hash if asset is not None else None,
            parser_ref=asset.parser_ref if asset is not None else "playwright-dom-main-text.v1",
            structured_payload_ref=structured_ref,
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
            provider=source_id,
            evidence_candidates=[candidate],
            facts=[],
            cost_usd=0.0,
            completed_at=received_at,
        )

    def _source_for_url(self, url: str, requirement_id: str):
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower().removeprefix("www.")
        path = parsed.path or "/"
        route = _BROWSER_ROUTES.get(host)
        if route is None or not path.startswith(route[0]):
            raise ResearchCapabilityError(
                "browser_source_not_supported",
                "browser readback is restricted to registered official calendars",
                origin="source_registry",
                capability_id=self.capability_id,
            )
        try:
            policy = self.source_registry.require(
                url, usage="evidence", requirement_id=requirement_id
            )
        except ResearchSourceRegistryError as exc:
            raise ResearchCapabilityError(
                exc.error_code,
                str(exc),
                origin="source_registry",
                capability_id=self.capability_id,
            ) from exc
        return policy, route[1]

    def _artifact_path(self, artifact_path: str) -> Path:
        resolved = Path(artifact_path).resolve()
        try:
            resolved.relative_to(self.artifact_root)
        except ValueError as exc:
            raise ResearchCapabilityError(
                "browser_artifact_outside_root",
                "browser artifact is outside the configured acquisition root",
                origin="storage",
                capability_id=self.capability_id,
            ) from exc
        if not resolved.is_file():
            raise ResearchCapabilityError(
                "browser_artifact_missing",
                "browser artifact path does not exist",
                origin="storage",
                capability_id=self.capability_id,
            )
        return resolved


class BrowserFallbackWebFetchAdapter:
    """Keep ``web.fetch`` stable while falling back to browser for calendars.

    The HTTP document route remains the primary path.  Only an audited
    official Fed/ECB/BOJ calendar URL can enter the fallback, and only after a
    transport/upstream/parser failure.  The returned capability id is kept as
    ``web.fetch`` so the DSH tool contract and downstream evidence ledger do
    not fork; the candidate still carries the browser acquisition receipt.
    """

    capability_id = "web.fetch"
    supported_modes = frozenset({"live"})
    target_url_required = True

    @property
    def source_query_catalog(self):
        return self.primary.source_query_catalog

    def __init__(
        self,
        *,
        primary: HttpDocumentResearchAdapter,
        browser: BrowserReadbackResearchAdapter,
    ) -> None:
        self.primary = primary
        self.browser = browser

    async def execute(self, query: ResearchCapabilityQuery) -> ResearchCapabilityResult:
        try:
            return await self.primary.execute(query)
        except ResearchCapabilityError as primary_error:
            if not _browser_calendar_url(query.target_url) or not _fallback_allowed(primary_error):
                raise
            try:
                browser_result = await self.browser.execute(query)
            except ResearchCapabilityError as browser_error:
                raise ResearchCapabilityError(
                    "browser_fallback_failed",
                    "HTTP fetch failed and the audited browser fallback also failed",
                    retryable=browser_error.retryable,
                    origin=browser_error.origin,
                    cause_code=f"{primary_error.error_code}:{browser_error.error_code}",
                    capability_id=self.capability_id,
                    tool_call_id=query.request_id,
                ) from browser_error
            return browser_result.model_copy(
                update={
                    "capability_id": self.capability_id,
                    "provider": f"browser-readback:{browser_result.provider}",
                }
            )


def _browser_calendar_url(url: AnyUrl | None) -> bool:
    if url is None:
        return False
    parsed = urlsplit(str(url))
    host = (parsed.hostname or "").lower().removeprefix("www.")
    path = parsed.path or "/"
    route = _BROWSER_ROUTES.get(host)
    return route is not None and path.startswith(route[0])


def _fallback_allowed(error: ResearchCapabilityError) -> bool:
    return error.retryable or error.error_code in {
        "research_document_empty",
        "research_document_media_type_unsupported",
        "research_provider_denied",
        "search_provider_failed",
    }

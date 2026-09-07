from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from pydantic import AnyUrl

from packages.contracts_py.decision_hub_contracts import ResearchCapabilityQuery
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchCapabilityError,
)
from packages.provider_adapters.research.documents import (
    FetchedDocument,
    HttpDocumentResearchAdapter,
    _extract_response_text,  # pyright: ignore[reportPrivateUsage]
)
from packages.provider_adapters.research.source_registry import ResearchSourceRegistry

OBSERVED = datetime(2026, 9, 4, 1, 0, tzinfo=UTC)
RECEIVED = datetime(2026, 9, 4, 1, 0, 1, tzinfo=UTC)
PACK_ROOT = Path(__file__).resolve().parents[2] / "packs" / "crypto_macro"


def _query(url: str, *, requirement_id: str = "counter.thesis") -> ResearchCapabilityQuery:
    return ResearchCapabilityQuery(
        schema_version="research-capability-query.v1",
        request_id="fetch-request-1",
        capability_id="web.fetch",
        requirement_id=requirement_id,
        query="read the cited page",
        target_url=AnyUrl(url),
        symbols=[],
        fields=[],
        allowed_domains=[],
        max_results=1,
        max_cost_usd=0.05,
        research_session_id="dsh-session-1",
        round=1,
        mode="live",
        observed_at=OBSERVED,
        cutoff_at=datetime(2026, 9, 4, 1, 0, 10, tzinfo=UTC),
    )


def test_web_fetch_uses_publisher_domain_as_source_identity() -> None:
    async def fetcher(url: str) -> FetchedDocument:
        return FetchedDocument(
            source_url=url,
            text="A verified public page excerpt.",
            observed_at=OBSERVED,
            received_at=RECEIVED,
        )

    adapter = HttpDocumentResearchAdapter(
        capability_id="web.fetch",
        kind="web",
        authority="verified_web",
        source_id="web-fetch",
        fetcher=fetcher,
    )

    result = asyncio.run(adapter.execute(_query("https://www.reuters.com/markets/macro/example")))

    candidate = result.evidence_candidates[0]
    assert candidate.source_id == "reuters.com"
    assert result.provider == "reuters.com"
    assert candidate.authority == "verified_web"
    assert candidate.received_at == RECEIVED


def test_typed_official_fetch_keeps_declared_source_identity() -> None:
    async def fetcher(url: str) -> FetchedDocument:
        return FetchedDocument(
            source_url=url,
            text="Official statement excerpt.",
            observed_at=OBSERVED,
            received_at=RECEIVED,
        )

    adapter = HttpDocumentResearchAdapter(
        capability_id="official.macro",
        kind="official",
        authority="official",
        source_id="official-macro-document",
        fetcher=fetcher,
    )

    result = asyncio.run(
        adapter.execute(_query("https://www.federalreserve.gov/newsevents/speech/x.htm"))
    )

    assert result.evidence_candidates[0].source_id == "official-macro-document"
    assert result.provider == "official-macro-document"


@pytest.mark.parametrize(
    ("status", "error_code", "retryable", "cause_code"),
    [
        (404, "research_document_not_found", False, "http_404"),
        (403, "research_provider_denied", False, "http_403"),
        (429, "search_provider_failed", True, "http_429"),
        (503, "search_provider_failed", True, "http_503"),
    ],
)
def test_document_fetch_preserves_http_failure_classification(
    status: int, error_code: str, retryable: bool, cause_code: str
) -> None:
    request = httpx.Request("GET", "https://www.federalreserve.gov/speech.htm")
    response = httpx.Response(status, request=request)

    async def fetcher(url: str) -> FetchedDocument:
        del url
        raise httpx.HTTPStatusError("upstream failure", request=request, response=response)

    adapter = HttpDocumentResearchAdapter(
        capability_id="web.fetch",
        kind="web",
        authority="verified_web",
        source_id="web-fetch",
        fetcher=fetcher,
    )

    with pytest.raises(ResearchCapabilityError) as raised:
        asyncio.run(adapter.execute(_query("https://www.federalreserve.gov/speech.htm")))

    error = raised.value
    assert error.error_code == error_code
    assert error.retryable is retryable
    assert error.origin == "provider"
    assert error.cause_code == cause_code


def test_document_fetch_preserves_transport_timeout_classification() -> None:
    request = httpx.Request("GET", "https://www.federalreserve.gov/speech.htm")

    async def fetcher(url: str) -> FetchedDocument:
        del url
        raise httpx.ReadTimeout("upstream timeout", request=request)

    adapter = HttpDocumentResearchAdapter(
        capability_id="web.fetch",
        kind="web",
        authority="verified_web",
        source_id="web-fetch",
        fetcher=fetcher,
    )

    with pytest.raises(ResearchCapabilityError) as raised:
        asyncio.run(adapter.execute(_query("https://www.federalreserve.gov/speech.htm")))

    error = raised.value
    assert error.error_code == "search_provider_failed"
    assert error.retryable is True
    assert error.origin == "transport"
    assert error.cause_code == "timeout"


def test_document_fetch_rejects_unparsed_pdf_binary() -> None:
    response = httpx.Response(
        200,
        headers={"content-type": "application/pdf"},
        content=b"%PDF-1.7\x00binary-object-stream",
        request=httpx.Request("GET", "https://example.com/report.pdf"),
    )

    with pytest.raises(ResearchCapabilityError) as raised:
        _extract_response_text(response)

    assert raised.value.error_code == "research_document_media_type_unsupported"
    assert raised.value.origin == "provider"
    assert raised.value.cause_code == "application/pdf"


def test_registry_backed_web_fetch_uses_approved_authority_and_evidence_policy() -> None:
    async def fetcher(url: str) -> FetchedDocument:
        return FetchedDocument(
            source_url=url,
            text="Official Federal Reserve speech text.",
            observed_at=OBSERVED,
            received_at=RECEIVED,
        )

    adapter = HttpDocumentResearchAdapter(
        capability_id="web.fetch",
        kind="web",
        authority="verified_web",
        source_id="web-fetch",
        fetcher=fetcher,
        source_registry=ResearchSourceRegistry.from_pack(PACK_ROOT),
    )

    result = asyncio.run(
        adapter.execute(
            _query(
                "https://www.federalreserve.gov/newsevents/speech/example.htm",
                requirement_id="event_identity",
            )
        )
    )

    candidate = result.evidence_candidates[0]
    assert candidate.authority == "official"
    assert candidate.source_id == "federalreserve.gov"
    assert candidate.structured_payload_ref == "source-registry://fed.monetary_policy"

    with pytest.raises(ResearchCapabilityError) as raised:
        asyncio.run(
            adapter.execute(
                _query(
                    "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS2",
                    requirement_id="macro_transmission",
                )
            )
        )
    assert raised.value.error_code == "research_source_not_approved"

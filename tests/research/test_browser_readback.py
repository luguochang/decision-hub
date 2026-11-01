from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from packages.contracts_py.decision_hub_contracts import ResearchCapabilityQuery
from packages.contracts_py.decision_hub_contracts.models import SourceAcquisition
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchCapabilityError,
)
from packages.provider_adapters.research import (
    BrowserFallbackWebFetchAdapter,
    BrowserReadbackResearchAdapter,
    ContentAddressedDocumentStore,
    ResearchSourceRegistry,
)

NOW = datetime(2026, 9, 22, 1, 0, tzinfo=UTC)
PACK_ROOT = Path(__file__).resolve().parents[2] / "packs" / "crypto_macro"


def _query(*, target_url: str = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"):
    return ResearchCapabilityQuery(
        schema_version="research-capability-query.v1",
        request_id="browser-request-1",
        capability_id="browser.readback",
        requirement_id="event_identity",
        query="FOMC calendar meeting date",
        target_url=target_url,
        symbols=[],
        fields=[],
        allowed_domains=["federalreserve.gov"],
        max_results=1,
        max_cost_usd=0.0,
        research_session_id="session-browser",
        round=1,
        mode="live",
        observed_at=NOW,
        cutoff_at=NOW + timedelta(minutes=5),
    )


@pytest.mark.asyncio
async def test_browser_receipt_is_verified_and_becomes_receipt_linked_evidence(
    tmp_path: Path,
) -> None:
    root = tmp_path / "acquisition"
    dom_path = root / "fed-calendar" / ("a" * 32) / "dom.html"
    dom_path.parent.mkdir(parents=True)
    dom = (
        "<html><body><main><h1>FOMC Calendar</h1>"
        "<p>September 16-17, 2026 meeting.</p></main></body></html>"
    )
    dom_path.write_text(dom, encoding="utf-8")
    receipt = SourceAcquisition.model_validate(
        {
            "schema_version": "acquisition-receipt.v1",
            "receipt_id": "a" * 32,
            "source_id": "fed-calendar",
            "url": "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
            "access_mode": "playwright",
            "status": "succeeded",
            "request_started_at": NOW.isoformat(),
            "request_completed_at": (NOW + timedelta(seconds=2)).isoformat(),
            "observed_at": (NOW + timedelta(seconds=1)).isoformat(),
            "received_at": (NOW + timedelta(seconds=3)).isoformat(),
            "status_code": 200,
            "artifacts": [
                {
                    "kind": "dom",
                    "path": str(dom_path),
                    "content_hash": hashlib.sha256(dom.encode()).hexdigest(),
                }
            ],
            "error_code": None,
        }
    )

    async def fetcher(_query: ResearchCapabilityQuery, _source_id: str) -> SourceAcquisition:
        return receipt

    adapter = BrowserReadbackResearchAdapter(
        source_registry=ResearchSourceRegistry.from_pack(PACK_ROOT),
        document_store=ContentAddressedDocumentStore(tmp_path / "documents"),
        artifact_root=root,
        fetcher=fetcher,
    )
    result = await adapter.execute(_query())
    candidate = result.evidence_candidates[0]
    assert candidate.acquisition_receipt_id == receipt.receipt_id
    assert candidate.observed_at == NOW + timedelta(seconds=1)
    assert candidate.received_at == NOW + timedelta(seconds=3)
    assert candidate.content_ref is not None
    assert "September 16-17" in candidate.excerpt
    assert candidate.parser_ref.startswith("playwright-dom-main-text.v1+")


@pytest.mark.asyncio
async def test_browser_receipt_rejects_artifact_hash_tampering(tmp_path: Path) -> None:
    root = tmp_path / "acquisition"
    dom_path = root / "fed-calendar" / ("b" * 32) / "dom.html"
    dom_path.parent.mkdir(parents=True)
    dom_path.write_text("<main>trusted text</main>", encoding="utf-8")
    receipt = SourceAcquisition.model_validate(
        {
            "schema_version": "acquisition-receipt.v1",
            "receipt_id": "b" * 32,
            "source_id": "fed-calendar",
            "url": "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
            "access_mode": "playwright",
            "status": "succeeded",
            "observed_at": NOW.isoformat(),
            "status_code": 200,
            "artifacts": [{"kind": "dom", "path": str(dom_path), "content_hash": "0" * 64}],
            "error_code": None,
        }
    )

    async def fetcher(_query: ResearchCapabilityQuery, _source_id: str) -> SourceAcquisition:
        return receipt

    adapter = BrowserReadbackResearchAdapter(
        source_registry=ResearchSourceRegistry.from_pack(PACK_ROOT),
        document_store=None,
        artifact_root=root,
        fetcher=fetcher,
    )
    with pytest.raises(ResearchCapabilityError) as error:
        await adapter.execute(_query())
    assert error.value.error_code == "browser_artifact_hash_mismatch"


@pytest.mark.asyncio
async def test_web_fetch_falls_back_to_browser_only_after_calendar_http_failure(
    tmp_path: Path,
) -> None:
    root = tmp_path / "acquisition"
    dom_path = root / "fed-calendar" / ("c" * 32) / "dom.html"
    dom_path.parent.mkdir(parents=True)
    dom = "<main><h1>FOMC Calendar</h1><p>December meeting date.</p></main>"
    dom_path.write_text(dom, encoding="utf-8")
    receipt = SourceAcquisition.model_validate(
        {
            "schema_version": "acquisition-receipt.v1",
            "receipt_id": "c" * 32,
            "source_id": "fed-calendar",
            "url": "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
            "access_mode": "playwright",
            "status": "succeeded",
            "observed_at": NOW.isoformat(),
            "status_code": 200,
            "artifacts": [
                {
                    "kind": "dom",
                    "path": str(dom_path),
                    "content_hash": hashlib.sha256(dom.encode()).hexdigest(),
                }
            ],
            "error_code": None,
        }
    )

    class FailingHttp:
        async def execute(self, _query: ResearchCapabilityQuery):
            raise ResearchCapabilityError(
                "provider_upstream_unavailable",
                "http route unavailable",
                retryable=True,
                origin="provider",
            )

    async def fetcher(_query: ResearchCapabilityQuery, _source_id: str) -> SourceAcquisition:
        return receipt

    browser = BrowserReadbackResearchAdapter(
        source_registry=ResearchSourceRegistry.from_pack(PACK_ROOT),
        document_store=None,
        artifact_root=root,
        fetcher=fetcher,
    )
    adapter = BrowserFallbackWebFetchAdapter(primary=FailingHttp(), browser=browser)  # type: ignore[arg-type]
    result = await adapter.execute(_query())
    assert result.capability_id == "web.fetch"
    assert result.provider == "browser-readback:fed-calendar"
    assert result.evidence_candidates[0].acquisition_receipt_id == receipt.receipt_id


@pytest.mark.asyncio
async def test_browser_worker_502_preserves_failed_receipt_cause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    failure = SourceAcquisition.model_validate(
        {
            "schema_version": "acquisition-receipt.v1",
            "receipt_id": "failed-browser-receipt",
            "source_id": "fed-calendar",
            "url": "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
            "access_mode": "playwright",
            "status": "failed",
            "request_started_at": NOW.isoformat(),
            "request_completed_at": (NOW + timedelta(seconds=2)).isoformat(),
            "observed_at": None,
            "received_at": (NOW + timedelta(seconds=3)).isoformat(),
            "status_code": None,
            "artifacts": [],
            "error_code": "crawler_failed:TimeoutError",
        }
    )

    class FailedWorkerClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def __aenter__(self) -> FailedWorkerClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(self, url: str, **kwargs: object) -> httpx.Response:
            return httpx.Response(
                502,
                json=failure.model_dump(mode="json"),
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr(
        "packages.provider_adapters.research.browser_readback.httpx.AsyncClient",
        FailedWorkerClient,
    )
    browser = BrowserReadbackResearchAdapter(
        source_registry=ResearchSourceRegistry.from_pack(PACK_ROOT),
        document_store=None,
        artifact_root=tmp_path,
    )
    with pytest.raises(ResearchCapabilityError) as error:
        await browser.execute(_query())
    assert error.value.error_code == "browser_acquisition_incomplete"
    assert error.value.cause_code == "crawler_failed:TimeoutError"

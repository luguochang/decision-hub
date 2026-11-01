"""Closed browser originals for current FRED series; receipts are not Evidence authority."""
from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from packages.contracts_py.decision_hub_contracts import ResearchCapabilityQuery
from packages.contracts_py.decision_hub_contracts.models import SourceAcquisition
from packages.kernel.decision_hub_kernel.application.research_evidence import ResearchCapabilityError
from packages.provider_adapters.research.documents import FetchedDocument, extract_html_text
from packages.provider_adapters.research.source_registry import ResearchSourceRegistry

from .macro_series import SERIES, series_url

SOURCE_ID = "fred.current_macro"
_URLS = frozenset(series_url(spec) for spec in SERIES.values())
_MAX_DOM_BYTES = 2 * 1024 * 1024
BrowserReceiptFetcher = Callable[
    [ResearchCapabilityQuery, datetime], Awaitable[SourceAcquisition]
]


def _failure(code: str, *, origin: str = "provider") -> ResearchCapabilityError:
    return ResearchCapabilityError(code, "Official browser original could not be verified", origin=origin)


class OfficialMacroBrowserFetcher:
    """Browser-first only for the three permitted pages, inside the original twenty seconds."""

    def __init__(
        self, *, source_registry: ResearchSourceRegistry, artifact_root: Path,
        base_url: str = "http://127.0.0.1:8038", shared_key: str = "",
        receipt_fetcher: BrowserReceiptFetcher | None = None,
    ) -> None:
        endpoint = urlsplit(base_url)
        if (endpoint.scheme != "http" or endpoint.hostname not in {"127.0.0.1", "localhost", "::1"}
                or endpoint.username is not None or endpoint.password is not None
                or endpoint.path not in {"", "/"} or endpoint.query or endpoint.fragment):
            raise _failure("official_macro_browser_endpoint_invalid")
        self.source_registry = source_registry
        self.artifact_root = artifact_root.resolve()
        self.base_url = base_url.rstrip("/")
        self.shared_key = shared_key
        self.receipt_fetcher = receipt_fetcher or self._fetch_receipt

    async def _fetch_receipt(
        self, query: ResearchCapabilityQuery, deadline: datetime
    ) -> SourceAcquisition:
        remaining = (deadline - datetime.now(UTC)).total_seconds()
        if remaining <= 0:
            raise _failure("official_macro_browser_deadline_elapsed", origin="transport")
        headers = {"x-decision-hub-source-key": self.shared_key} if self.shared_key else {}
        payload = {
            "request_id": query.request_id, "source_id": SOURCE_ID,
            "url": str(query.target_url), "modes": ["dom"],
            "deadline_at": deadline.isoformat().replace("+00:00", "Z"),
        }
        try:
            # The trusted worker is loopback; external proxy configuration never applies here.
            async with httpx.AsyncClient(timeout=remaining, trust_env=False) as client:
                response = await client.post(
                    f"{self.base_url}/v1/acquire", json=payload, headers=headers
                )
                if response.status_code == 502:
                    failed = SourceAcquisition.model_validate(response.json())
                    if failed.status != "succeeded":
                        return failed
                response.raise_for_status()
                return SourceAcquisition.model_validate(response.json())
        except (httpx.HTTPError, ValueError) as exc:
            raise ResearchCapabilityError(
                "official_macro_browser_transport_failed", "Browser worker returned no verified original",
                retryable=True, origin="transport", cause_code=type(exc).__name__.lower(),
            ) from exc

    async def __call__(self, query: ResearchCapabilityQuery, url: str) -> FetchedDocument:
        if url not in _URLS or str(query.target_url) != url or query.mode != "live":
            raise _failure("official_macro_browser_source_denied", origin="source_registry")
        for usage in ("fetch", "evidence"):
            policy = self.source_registry.require(url, usage=usage, requirement_id=query.requirement_id)
            if policy.source_ref != SOURCE_ID:
                raise _failure("official_macro_browser_policy_mismatch", origin="source_registry")
        deadline = min(datetime.now(UTC) + timedelta(seconds=20), query.cutoff_at)
        remaining = (deadline - datetime.now(UTC)).total_seconds()
        if remaining <= 0:
            raise _failure("official_macro_browser_cutoff_expired", origin="pit")
        async with asyncio.timeout(remaining):
            receipt = await self.receipt_fetcher(query, deadline)
            document = self._document(query, url, receipt)
            if datetime.now(UTC) >= deadline:
                raise _failure("official_macro_browser_deadline_elapsed", origin="transport")
            return document

    def _document(
        self, query: ResearchCapabilityQuery, url: str, receipt: SourceAcquisition
    ) -> FetchedDocument:
        expected_id = hashlib.sha256(f"{SOURCE_ID}\n{url}\n{query.request_id}".encode()).hexdigest()[:32]
        if (receipt.receipt_id != expected_id or receipt.source_id != SOURCE_ID
                or str(receipt.url) != url or receipt.access_mode != "playwright"):
            raise _failure("official_macro_browser_receipt_identity_mismatch", origin="storage")
        if receipt.status != "succeeded" or receipt.status_code != 200 or receipt.error_code is not None:
            raise ResearchCapabilityError(
                "official_macro_browser_acquisition_incomplete", "Browser acquisition did not succeed",
                retryable=True, origin="transport", cause_code=receipt.error_code or "incomplete",
            )
        started, completed, observed, received = (
            receipt.request_started_at, receipt.request_completed_at,
            receipt.observed_at, receipt.received_at,
        )
        if (started is None or completed is None or observed is None or received is None
                or not started <= observed <= completed <= received <= query.cutoff_at
                or not 0 <= (received - started).total_seconds() <= 20):
            raise _failure("official_macro_browser_receipt_time_invalid", origin="pit")
        dom = [item for item in receipt.artifacts if item.kind == "dom"]
        if len(dom) != 1:
            raise _failure("official_macro_browser_dom_missing", origin="storage")
        expected_path = self.artifact_root / SOURCE_ID / expected_id / "dom.html"
        supplied = Path(dom[0].path)
        if supplied.resolve() != expected_path or supplied.is_symlink():
            raise _failure("official_macro_browser_dom_path_invalid", origin="storage")
        try:
            if expected_path.stat().st_size > _MAX_DOM_BYTES:
                raise _failure("official_macro_browser_dom_size_invalid", origin="storage")
            raw = expected_path.read_bytes()
        except OSError as exc:
            raise _failure("official_macro_browser_dom_unavailable", origin="storage") from exc
        if len(raw) > _MAX_DOM_BYTES or hashlib.sha256(raw).hexdigest() != dom[0].content_hash:
            raise _failure("official_macro_browser_dom_hash_mismatch", origin="storage")
        try:
            text = extract_html_text(raw.decode("utf-8"))
        except UnicodeDecodeError as exc:
            raise _failure("official_macro_browser_dom_decode_invalid", origin="storage") from exc
        return FetchedDocument(
            source_url=url, text=text, observed_at=observed, received_at=received,
            acquisition_receipt_id=receipt.receipt_id,
        )

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import AnyUrl

from packages.contracts_py.decision_hub_contracts import (
    ObservationCreate,
    ResearchCapabilityQuery,
    SearchEvidence,
    SearchQuery,
    SearchResult,
)
from packages.contracts_py.decision_hub_contracts.models import SourceType
from packages.kernel.decision_hub_kernel.application.admission import AdmissionService
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchEvidenceService,
)
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.application.search import (
    search_evidence_content_hash,
)
from packages.kernel.decision_hub_kernel.decision.sufficiency import (
    assess_evidence_sufficiency,
)
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.provider_adapters.research import (
    CryptoMacroFactPack,
    FetchedDocument,
    HttpDocumentResearchAdapter,
    WebSearchResearchAdapter,
)
from packages.provider_adapters.research.source_registry import ResearchSourceRegistry

ROOT = Path(__file__).resolve().parents[2]
PACK_ROOT = ROOT / "packs" / "crypto_macro"
NOW = datetime(2026, 9, 17, 2, 0, tzinfo=UTC)
CUTOFF = NOW + timedelta(minutes=5)
SOURCE_URL = "https://www.federalreserve.gov/newsevents/speech/example.htm"


class _OfficialLocatorSearch:
    async def search(self, query: SearchQuery) -> SearchResult:
        published_at = NOW - timedelta(hours=1)
        source_url = AnyUrl(SOURCE_URL)
        title = "Federal Reserve policy outlook"
        snippet = "The policy outlook remains conditional on inflation and employment data."
        return SearchResult(
            request_id=query.request_id,
            capability_id=query.capability_id,
            provider="fixture-search",
            evidence=[
                SearchEvidence(
                    evidence_id="provider-locator-1",
                    title=title,
                    snippet=snippet,
                    source_url=source_url,
                    published_at=published_at,
                    observed_at=NOW,
                    received_at=NOW + timedelta(seconds=1),
                    content_hash=search_evidence_content_hash(
                        title=title,
                        snippet=snippet,
                        source_url=str(source_url),
                        published_at=published_at,
                    ),
                )
            ],
            cost_usd=0.01,
            completed_at=NOW + timedelta(seconds=1),
        )


def _query(
    capability_id: str,
    request_id: str,
    *,
    target_url: str | None,
) -> ResearchCapabilityQuery:
    return ResearchCapabilityQuery(
        schema_version="research-capability-query.v1",
        request_id=request_id,
        capability_id=capability_id,
        requirement_id="market_narrative",
        query="current official macro release that could explain or falsify the BTC move",
        target_url=AnyUrl(target_url) if target_url is not None else None,
        symbols=["BTC"],
        fields=[],
        allowed_domains=["federalreserve.gov"],
        max_results=3,
        max_cost_usd=0.05,
        research_session_id="research-session-narrative",
        round=1 if capability_id == "web.search" else 2,
        mode="live",
        observed_at=NOW,
        cutoff_at=CUTOFF,
    )


@pytest.mark.asyncio
async def test_search_locator_must_be_fetched_before_it_satisfies_narrative_evidence(
    tmp_path: Path,
) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'narrative.sqlite3'}")
    database.create_all()
    event_id, _envelope, _created = AdmissionService(database, clock=lambda: NOW).admit(
        ObservationCreate(
            text="获取最新 BTC 操作建议",
            source_id="decision-desk-latest-advice",
            source_type=SourceType.manual,
            event_hint="crypto_market_snapshot",
            language="zh",
        )
    )
    run_id, _created = RunService(database).create(event_id, strategy_version="research.v1")
    requirement = next(
        item
        for item in CryptoMacroFactPack.from_pack(PACK_ROOT).contract_requirements_for_event(
            "crypto_market_snapshot"
        )
        if item.requirement_id == "market_narrative"
    )
    requirements = {requirement.requirement_id: requirement}
    evidence_service = ResearchEvidenceService(database)

    locator_result = await WebSearchResearchAdapter(_OfficialLocatorSearch()).execute(
        _query("web.search", "search-market-narrative", target_url=None)
    )
    locators = evidence_service.accept_candidates(
        run_id=run_id,
        capability_id="web.search",
        candidates=locator_result.evidence_candidates,
        requirements=requirements,
        cutoff_at=CUTOFF,
    )
    locator_coverage = assess_evidence_sufficiency(
        [requirement], locators, cutoff_at=CUTOFF
    )

    assert locators[0].authority == "search_derived"
    assert locators[0].quality == "candidate"
    # This is a soft requirement, so the overall hard gate remains sufficient;
    # the locator itself still leaves an explicit uncovered gap.
    assert locator_coverage.covered_requirement_ids == []
    assert [item.requirement_id for item in locator_coverage.gaps] == ["market_narrative"]
    assert locator_coverage.soft_coverage_ratio == 0.0

    async def fetcher(url: str) -> FetchedDocument:
        assert url == SOURCE_URL
        return FetchedDocument(
            source_url=url,
            text=(
                "The Federal Reserve stated that future policy remains conditional on "
                "incoming inflation and employment data."
            ),
            published_at=NOW - timedelta(hours=1),
            observed_at=NOW + timedelta(seconds=2),
            received_at=NOW + timedelta(seconds=3),
        )

    fetch_result = await HttpDocumentResearchAdapter(
        capability_id="web.fetch",
        kind="web",
        authority="verified_web",
        source_id="web-fetch",
        fetcher=fetcher,
        source_registry=ResearchSourceRegistry.from_pack(PACK_ROOT),
    ).execute(_query("web.fetch", "fetch-market-narrative", target_url=SOURCE_URL))
    fetched = evidence_service.accept_candidates(
        run_id=run_id,
        capability_id="web.fetch",
        candidates=fetch_result.evidence_candidates,
        requirements=requirements,
        cutoff_at=CUTOFF,
    )
    fetched_coverage = assess_evidence_sufficiency(
        [requirement], [*locators, *fetched], cutoff_at=CUTOFF
    )

    assert fetched[0].authority == "official"
    assert fetched[0].quality == "accepted"
    assert str(fetched[0].source_url) == SOURCE_URL
    assert fetched_coverage.status == "sufficient"
    assert fetched_coverage.covered_requirement_ids == ["market_narrative"]

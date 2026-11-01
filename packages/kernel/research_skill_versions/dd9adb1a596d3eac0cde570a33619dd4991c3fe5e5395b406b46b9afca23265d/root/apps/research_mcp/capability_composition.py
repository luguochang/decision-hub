"""Construct the actual MCP capability set without executing any provider.

The MCP and Skill bootstrap share this exact constructor, including disabled
capabilities, event windows, document readback and configured search transports.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .main import (
    ContentAddressedDocumentStore,
    CryptoEventWindowArchive,
    CryptoEventWindowResearchAdapter,
    DataJobService,
    EventWatchService,
    HistoricalDataArchive,
    RawBodyArchive,
    ResearchCapabilityAdapter,
    ResearchEvidenceService,
    SpotMarketFactsAdapter,
    TavilySearchTransport,
    WebSearchResearchAdapter,
    _configured_search_transport,
    compose_capability_adapters,
    research_query_catalog,
)


@dataclass(frozen=True)
class CapabilityComposition:
    manifests: list
    adapters: list
    catalog: dict
    evidence_service: object
    event_watches: object


def build_research_capability_composition(
    database, data_root: Path, enabled: set[str], *, manifests, archive
):
    evidence_service = ResearchEvidenceService(database) if database is not None else None
    document_store = ContentAddressedDocumentStore(data_root / "documents")
    data_jobs = DataJobService(database) if database is not None else None
    historical_archive = HistoricalDataArchive(data_root / "historical")
    event_watches: EventWatchService | None = None
    event_window_adapter: ResearchCapabilityAdapter | None = None
    spot_market_adapter: ResearchCapabilityAdapter | None = None
    if "spot_market_facts" in enabled:
        from packages.kernel.decision_hub_kernel.application.market_risk_data import (
            MarketRiskDataService,
        )

        assert database is not None
        # The worker owns acquisition; research reads the same committed raw path.
        spot_market_adapter = SpotMarketFactsAdapter(
            MarketRiskDataService(
                database,
                RawBodyArchive(data_root / "raw-captures"),
                data_root / "market-risk" / "features",
                enabled=False,
            ),
            read_enabled=True,
        )
    if "market.crypto_derivatives" in enabled:
        assert database is not None
        event_watches = EventWatchService(database)
        event_window_adapter = CryptoEventWindowResearchAdapter(
            event_watches,
            CryptoEventWindowArchive(data_root / "event-windows"),
        )
    adapters = compose_capability_adapters(
        manifests,
        archive,
        event_window_adapter=event_window_adapter,
        document_store=document_store,
        evidence_service=evidence_service,
        data_jobs=data_jobs,
        historical_archive=historical_archive,
        spot_market_adapter=spot_market_adapter,
    )
    if "web.search" in enabled:
        adapters.append(
            WebSearchResearchAdapter(_configured_search_transport(), capability_id="web.search")
        )
    if "web.search.tavily" in enabled:
        adapters.append(
            WebSearchResearchAdapter(
                TavilySearchTransport.from_env(), capability_id="web.search.tavily"
            )
        )
    query_catalog = research_query_catalog(manifests, adapters, enabled)
    return CapabilityComposition(
        manifests, adapters, query_catalog, evidence_service, event_watches
    )

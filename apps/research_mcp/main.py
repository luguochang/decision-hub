from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import yaml
from mcp.server.mcpserver import MCPServer

from packages.contracts_py.decision_hub_contracts import (
    ResearchCapabilityManifest,
    ResearchCapabilityQuery,
)
from packages.kernel.decision_hub_kernel.application.data_jobs import DataJobService
from packages.kernel.decision_hub_kernel.application.dsh_sessions import DshSessionLinkService
from packages.kernel.decision_hub_kernel.application.event_watch import EventWatchService
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchCapabilityError,
    ResearchCapabilityGatewayService,
    ResearchEvidenceService,
)
from packages.kernel.decision_hub_kernel.application.research_observability import (
    ResearchObservabilityService,
)
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.kernel.decision_hub_kernel.ports.research import ResearchCapabilityAdapter
from packages.kernel.decision_hub_kernel.ports.search import SearchCapabilityPort
from packages.provider_adapters.historical import (
    HistoricalDataArchive,
    HistoricalDataResearchAdapter,
)
from packages.provider_adapters.macro_market import (
    ExpectationPricingResearchAdapter,
    FredSeriesResearchAdapter,
    IntradayMacroResearchAdapter,
    YahooCrossAssetResearchAdapter,
)
from packages.provider_adapters.market import (
    BinanceMarketResearchAdapter,
    CoinExMarketResearchAdapter,
    CryptoCrowdingResearchAdapter,
    CryptoEventWindowArchive,
    CryptoEventWindowResearchAdapter,
    GateDerivativesResearchAdapter,
    HtxDerivativesResearchAdapter,
    OKXDerivativesResearchAdapter,
    PublicSpotMarketResearchAdapter,
    parse_gate_spot,
    parse_huobi_spot,
)
from packages.provider_adapters.market.spot_research import SpotMarketFactsAdapter
from packages.provider_adapters.official_sources import OfficialDocumentResearchAdapter
from packages.provider_adapters.official_sources.browser_documents import (
    OfficialMacroBrowserFetcher,
)
from packages.provider_adapters.research import (
    BrowserFallbackWebFetchAdapter,
    BrowserReadbackResearchAdapter,
    ContentAddressedDocumentStore,
    CryptoMacroFactPack,
    DocumentReadbackResearchAdapter,
    HttpDocumentResearchAdapter,
    ReplayResearchArchive,
    ReplayResearchCapabilityAdapter,
    ResearchSourceRegistry,
    WebSearchResearchAdapter,
    load_replay_archive,
)
from packages.provider_adapters.research.raw_capture import RawBodyArchive, reused_observation_times
from packages.provider_adapters.routing import ProviderCapabilityRouter
from packages.provider_adapters.search import (
    DshNativeWebSearchTransport,
    OpenAIResponsesWebSearchTransport,
    TavilySearchTransport,
)
from packages.workbench_adapters.durable_research_gateway import (
    DurableResearchCapabilityGateway,
)
from packages.workbench_adapters.research_catalog import research_query_catalog
from packages.workbench_adapters.research_mcp import build_research_mcp_server
from packages.workbench_adapters.research_reuse import ArchivedResearchAdapter

ROOT = Path(__file__).resolve().parents[2]


def load_capability_manifests() -> list[ResearchCapabilityManifest]:
    payload = yaml.safe_load(
        (ROOT / "packs/crypto_macro/tools/bindings.yaml").read_text(encoding="utf-8")
    )
    if not isinstance(payload, dict) or not isinstance(payload.get("capabilities"), list):
        raise RuntimeError("research_capability_bindings_invalid")
    return [ResearchCapabilityManifest.model_validate(item) for item in payload["capabilities"]]


class _UnavailableEventWindowAdapter:
    """Keep the Pack route composable while requiring an explicit runtime store."""

    capability_id = "market.crypto_derivatives"
    supported_modes = frozenset({"live", "replay"})
    supported_fields = CryptoEventWindowResearchAdapter.supported_fields

    async def execute(self, query: ResearchCapabilityQuery):
        raise ResearchCapabilityError(
            "provider_window_archive_unconfigured",
            "event-window archive was not injected by the runtime",
            retryable=False,
            origin="gateway",
        )


def _provider_adapter_registry(
    *, event_window_adapter: ResearchCapabilityAdapter | None = None
) -> dict[str, ResearchCapabilityAdapter]:
    """Map Pack adapter refs to concrete adapters at the composition boundary."""

    return {
        "adapter://provider/market/binance-public": BinanceMarketResearchAdapter(),
        "adapter://provider/market/okx-public": OKXDerivativesResearchAdapter(),
        "adapter://provider/market/coinex-public": CoinExMarketResearchAdapter(),
        "adapter://provider/market/gateio-derivatives-public": GateDerivativesResearchAdapter(),
        "adapter://provider/market/htx-derivatives-public": HtxDerivativesResearchAdapter(),
        "adapter://provider/market/gateio-public": PublicSpotMarketResearchAdapter(
            provider_id="gateio-public",
            base_url="https://api.gateio.ws/api/v4/spot/tickers",
            parser=parse_gate_spot,
        ),
        "adapter://provider/market/huobi-public": PublicSpotMarketResearchAdapter(
            provider_id="huobi-public",
            base_url="https://api.huobi.pro/market/detail/merged",
            parser=parse_huobi_spot,
        ),
        "adapter://provider/market/event-window-archive": (
            event_window_adapter or _UnavailableEventWindowAdapter()
        ),
        "adapter://provider/market/okx-orderbook": CryptoCrowdingResearchAdapter(
            provider_id="okx-orderbook-public",
            base_url="https://www.okx.com",
            exchange="okx",
        ),
        "adapter://provider/market/coinex-orderbook": CryptoCrowdingResearchAdapter(
            provider_id="coinex-orderbook-public",
            base_url="https://api.coinex.com",
            exchange="coinex",
        ),
        "adapter://provider/market/gateio-orderbook": CryptoCrowdingResearchAdapter(
            provider_id="gateio-orderbook-public",
            base_url="https://api.gateio.ws",
            exchange="gateio",
        ),
        "adapter://provider/market/htx-orderbook": CryptoCrowdingResearchAdapter(
            provider_id="htx-orderbook-public",
            base_url="https://api.hbdm.com",
            exchange="htx",
        ),
        "adapter://provider/macro/intraday-primary": IntradayMacroResearchAdapter(
            provider_id="macro-intraday-proxy",
            base_url=os.getenv("DECISION_HUB_INTRADAY_MACRO_URL"),
            delay_class=os.getenv("DECISION_HUB_INTRADAY_MACRO_DELAY_CLASS", "delayed"),
            authority=os.getenv("DECISION_HUB_INTRADAY_MACRO_AUTHORITY", "exchange"),
            estimated_cost_usd=_optional_nonnegative_float(
                "DECISION_HUB_INTRADAY_MACRO_ESTIMATED_COST_USD"
            ),
        ),
        "adapter://provider/macro/intraday-fallback": IntradayMacroResearchAdapter(
            provider_id="macro-intraday-fallback",
            base_url=os.getenv("DECISION_HUB_INTRADAY_MACRO_FALLBACK_URL"),
            delay_class=os.getenv("DECISION_HUB_INTRADAY_MACRO_FALLBACK_DELAY_CLASS", "delayed"),
            authority=os.getenv("DECISION_HUB_INTRADAY_MACRO_FALLBACK_AUTHORITY", "exchange"),
            estimated_cost_usd=_optional_nonnegative_float(
                "DECISION_HUB_INTRADAY_MACRO_FALLBACK_ESTIMATED_COST_USD"
            ),
        ),
        "adapter://provider/macro_market/fred": FredSeriesResearchAdapter(),
        "adapter://provider/macro_market/yahoo": YahooCrossAssetResearchAdapter(),
        "adapter://provider/macro/expectation-primary": ExpectationPricingResearchAdapter(
            provider_id="expectation-pricing-configured",
            base_url=os.getenv("DECISION_HUB_EXPECTATION_PRICING_URL"),
            delay_class=os.getenv("DECISION_HUB_EXPECTATION_PRICING_DELAY_CLASS", "unknown"),
            authority=os.getenv("DECISION_HUB_EXPECTATION_PRICING_AUTHORITY", "exchange"),
            estimated_cost_usd=_optional_nonnegative_float(
                "DECISION_HUB_EXPECTATION_PRICING_ESTIMATED_COST_USD"
            ),
        ),
    }


def compose_capability_adapters(
    manifests: list[ResearchCapabilityManifest],
    archive: ReplayResearchArchive,
    *,
    provider_registry: dict[str, ResearchCapabilityAdapter] | None = None,
    event_window_adapter: ResearchCapabilityAdapter | None = None,
    document_store: ContentAddressedDocumentStore | None = None,
    evidence_service: ResearchEvidenceService | None = None,
    data_jobs: DataJobService | None = None,
    historical_archive: HistoricalDataArchive | None = None,
    spot_market_adapter: ResearchCapabilityAdapter | None = None,
) -> list[ResearchCapabilityAdapter]:
    """Build one adapter per stable capability, using Pack routes when declared."""

    registry = _provider_adapter_registry(event_window_adapter=event_window_adapter)
    if provider_registry is not None:
        registry.update(provider_registry)
    source_registry = ResearchSourceRegistry.from_pack(ROOT / "packs/crypto_macro")
    http_document = HttpDocumentResearchAdapter(
        capability_id="web.fetch",
        kind="web",
        authority="verified_web",
        source_id="web-fetch",
        source_registry=source_registry,
        document_store=document_store,
    )
    browser_readback = BrowserReadbackResearchAdapter(
        source_registry=source_registry,
        document_store=document_store,
        artifact_root=Path(
            os.getenv("DECISION_HUB_SOURCE_ACQUISITION_ROOT", "data/source-acquisition")
        ),
        base_url=os.getenv("DECISION_HUB_SOURCE_ACQUISITION_URL", "http://127.0.0.1:8038"),
        shared_key=os.getenv("DECISION_HUB_SOURCE_ACQUISITION_KEY", ""),
    )
    adapters: list[ResearchCapabilityAdapter] = [
        ReplayResearchCapabilityAdapter(
            capability_id="replay.research",
            fixtures=archive.queries,
            requirement_fixtures=archive.requirements,
        ),
        BrowserFallbackWebFetchAdapter(primary=http_document, browser=browser_readback),
        browser_readback,
        OfficialDocumentResearchAdapter(
            source_registry=source_registry,
            document_store=document_store,
            current_series_fetcher=OfficialMacroBrowserFetcher(
                source_registry=source_registry,
                artifact_root=Path(os.getenv(
                    "DECISION_HUB_SOURCE_ACQUISITION_ROOT", "data/source-acquisition"
                )),
                base_url=os.getenv("DECISION_HUB_SOURCE_ACQUISITION_URL", "http://127.0.0.1:8038"),
                shared_key=os.getenv("DECISION_HUB_SOURCE_ACQUISITION_KEY", ""),
            ),
        ),
    ]
    if document_store is not None and evidence_service is not None:
        adapters.append(DocumentReadbackResearchAdapter(evidence_service, document_store))
    if data_jobs is not None and historical_archive is not None:
        adapters.append(HistoricalDataResearchAdapter(data_jobs, historical_archive))
    if spot_market_adapter is not None:
        adapters.append(spot_market_adapter)
    direct = {item.capability_id: item for item in adapters}
    for manifest in manifests:
        routes = tuple(manifest.provider_routes or ())
        if event_window_adapter is None:
            # Unit/replay composition does not own the Hub EventWatch store;
            # keep the route declaration in the Pack but do not expose an
            # executable archive route until the runtime injects its adapter.
            routes = tuple(
                route
                for route in routes
                if not route.requires_event_window
                or route.adapter_ref != "adapter://provider/market/event-window-archive"
            )
        if not routes:
            continue
        route_adapters: dict[str, ResearchCapabilityAdapter] = {}
        for route in routes:
            adapter = registry.get(route.adapter_ref)
            if adapter is None:
                raise RuntimeError(
                    f"research_provider_adapter_ref_unregistered:{route.adapter_ref}"
                )
            if adapter.capability_id != manifest.capability_id:
                raise RuntimeError(
                    "research_provider_adapter_capability_mismatch:"
                    f"{route.provider_id}:{manifest.capability_id}"
                )
            if route.provider_id in route_adapters:
                raise RuntimeError(f"research_provider_provider_id_duplicate:{route.provider_id}")
            route_adapters[route.provider_id] = adapter
        if manifest.capability_id in direct:
            raise RuntimeError(
                f"research_capability_has_direct_and_routed_adapters:{manifest.capability_id}"
            )
        adapters.append(
            ProviderCapabilityRouter(
                capability_id=manifest.capability_id,
                routes=routes,
                adapters=route_adapters,
                minimum_successes_by_requirement=(
                    {
                        "crypto_spot_confirmation": 2,
                        "derivatives_crowding": 2,
                    }
                    if manifest.capability_id == "market.crypto_derivatives"
                    else None
                ),
                max_attempts_per_provider=(
                    2 if manifest.capability_id == "market.crypto_derivatives" else 1
                ),
            )
        )
    return adapters


def _configure_retained_recipe_registry_from_normal_launch() -> None:
    """Consume the normal launcher's guarded release identity before DB startup.

    Consume the same validated release as the API/Worker before catalog reads.
    V3 is explicitly enabled by the launcher; no pointer or Run is created here.
    """
    receipt_name = os.getenv("DECISION_HUB_RETAINED_RECIPE_RECEIPT", "")
    receipt_sha256 = os.getenv("DECISION_HUB_RETAINED_RECIPE_RECEIPT_SHA256", "")
    spot_enabled = os.getenv("DECISION_HUB_SPOT_RESEARCH_ENABLED", "0") == "1"
    if not receipt_name and not receipt_sha256:
        if spot_enabled:
            raise ValueError("research_graph_recipe_v3_startup_receipt_required")
        return
    if not receipt_name or not receipt_sha256:
        raise ValueError("research_history_startup_receipt_pair_required")
    import hashlib

    from packages.kernel.decision_hub_kernel.application.product_release_provenance import (
        resolve_product_receipt,
    )
    from packages.kernel.decision_hub_kernel.application.research_graph_history_versions import (
        _managed_read,
        configure_retained_recipe_registry_from_validated_release,
    )

    try:
        if (len(receipt_sha256) != 64
                or any(character not in "0123456789abcdef" for character in receipt_sha256)):
            raise ValueError("invalid receipt identity")
        receipt_path = Path(receipt_name)
        if not receipt_path.is_absolute():
            raise ValueError("invalid receipt path")
        root = Path(__file__).resolve().parents[2]
        relative = receipt_path.absolute().relative_to(root).as_posix()
        raw = _managed_read(relative)
        if hashlib.sha256(raw).hexdigest() != receipt_sha256:
            raise ValueError("changed receipt")
        receipt = dict(resolve_product_receipt(
            raw, expected_root=root, read_resource=_managed_read,
            expected_sha256=receipt_sha256,
        ).value)
    except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError) as error:
        raise ValueError("research_history_startup_receipt_invalid") from error
    # A valid release made before automatic retention keeps the three manually
    # registered readers. Missing archive evidence is never synthesized.
    if "retained_recipe_archives_v1" in receipt["evidence"]:
        configure_retained_recipe_registry_from_validated_release(
            receipt_path=receipt_path, receipt_sha256=receipt_sha256,
        )
    if spot_enabled:
        from packages.kernel.decision_hub_kernel.application.research_graph_recipe_v3 import (
            configure_graph_recipe_v3_from_validated_release,
        )
        configure_graph_recipe_v3_from_validated_release(
            receipt_path=receipt_path, receipt_sha256=receipt_sha256,
        )


def create_server() -> MCPServer:
    _configure_retained_recipe_registry_from_normal_launch()
    enabled = {
        item.strip()
        for item in os.getenv("DECISION_HUB_RESEARCH_CAPABILITIES", "").split(",")
        if item.strip()
    }
    archive = load_replay_archive(
        _optional_path(os.getenv("DECISION_HUB_RESEARCH_REPLAY_FIXTURES"))
    )
    manifests = load_capability_manifests()
    durable_progress = _durable_progress_enabled(enabled)
    database: Database | None = None
    if durable_progress or enabled.intersection({"market.crypto_derivatives", "spot_market_facts"}):
        database = Database()
        database.create_all()
    from .capability_composition import build_research_capability_composition
    data_root = Path(os.getenv("DECISION_HUB_DATA_DIR", "data/decision-hub"))
    composition = build_research_capability_composition(
        database, data_root, enabled, manifests=manifests, archive=archive,
    )
    adapters, query_catalog = composition.adapters, composition.catalog
    evidence_service, event_watches = composition.evidence_service, composition.event_watches
    # Cache identity changes with audited manifests and executing parser code.
    fingerprint_hash = hashlib.sha256()
    fingerprint_hash.update("\n".join(item.model_dump_json() for item in manifests).encode())
    for folder in ("packages/provider_adapters", "packages/workbench_adapters"):
        for path in sorted((ROOT / folder).rglob("*.py")):
            fingerprint_hash.update(path.relative_to(ROOT).as_posix().encode())
            fingerprint_hash.update(path.read_bytes())
    parser_fingerprint = fingerprint_hash.hexdigest()
    raw_archive = RawBodyArchive(data_root / "raw-captures")
    if database is not None and durable_progress:
        adapters = [
            ArchivedResearchAdapter(item, database, raw_archive, parser_fingerprint)
            for item in adapters
        ]
    gateway = ResearchCapabilityGatewayService(
        manifests,
        adapters,
        enabled_capabilities=enabled,
        archived_observation_resolver=reused_observation_times,
    )
    # Replay-only subprocess smoke tests intentionally exercise the transport
    # without a product Run. Live product calls always use the durable
    # recorder, sharing the Hub SQLite data directory with the worker/API.
    if durable_progress:
        assert database is not None
        assert evidence_service is not None
        fact_pack = CryptoMacroFactPack.from_pack(ROOT / "packs/crypto_macro")
        from apps.hub_api.frozen_harness_bootstrap import configured_frozen_run_harness
        from packages.kernel.decision_hub_kernel.persistence.db import utcnow
        from packages.workbench_adapters.frozen_run_research_source import FrozenRunCapabilitySource
        configured_frozen = configured_frozen_run_harness(database)
        frozen_source = (
            FrozenRunCapabilitySource(
                configured_frozen.service, clock=utcnow,
                manifests=manifests, enabled_capabilities=enabled,
            ) if configured_frozen is not None else None
        )
        durable = DurableResearchCapabilityGateway(
            gateway,
            DshSessionLinkService(database),
            evidence_service,
            ResearchObservabilityService(database),
            requirements={
                item.requirement_id: item for item in fact_pack.all_contract_requirements()
            },
            requirement_resolver=lambda event_family: {
                item.requirement_id: item
                for item in fact_pack.contract_requirements_for_event(event_family)
            },
            event_watches=event_watches,
            raw_archive=raw_archive,
            parser_fingerprint=parser_fingerprint,
            frozen_source=frozen_source,
        )
        from packages.kernel.decision_hub_kernel.application.research_baseline import (
            ResearchBaselineService,
        )
        return build_research_mcp_server(
            durable,
            bridge_key=os.getenv("DECISION_HUB_DSH_RESEARCH_TOOL_KEY"),
            query_catalog=query_catalog,
            baseline_service=ResearchBaselineService(database, gateway=durable,
                                                     query_catalog=query_catalog),
        )
    return build_research_mcp_server(
        gateway,
        bridge_key=os.getenv("DECISION_HUB_DSH_RESEARCH_TOOL_KEY"),
        query_catalog=query_catalog,
    )


def _optional_path(value: str | None) -> Path | None:
    return Path(value) if value else None


def _configured_search_transport() -> SearchCapabilityPort:
    """Select exactly one provider behind the stable ``web.search`` capability."""

    provider = os.getenv("DECISION_HUB_SEARCH_PROVIDER", "dsh_native").strip().lower()
    factories = {
        "dsh_native": DshNativeWebSearchTransport.from_env,
        "openai_responses": OpenAIResponsesWebSearchTransport.from_env,
        "tavily": TavilySearchTransport.from_env,
    }
    try:
        factory = factories[provider]
    except KeyError as exc:
        raise RuntimeError(f"research_search_provider_invalid:{provider}") from exc
    return factory()


def _durable_progress_enabled(enabled: set[str]) -> bool:
    explicit = os.getenv("DECISION_HUB_RESEARCH_DURABLE_PROGRESS")
    if explicit is None:
        # Standalone replay smoke tests stay isolated unless explicitly
        # attached to a durable product or evaluation Run.
        return enabled != {"replay.research"}
    return explicit.strip().lower() not in {"0", "false", "no", "off"}


def _optional_nonnegative_float(name: str) -> float | None:
    value = os.getenv(name)
    if value is None or not value.strip():
        return None
    try:
        parsed = float(value)
    except ValueError as exc:
        raise RuntimeError(f"research_provider_cost_invalid:{name}") from exc
    if parsed < 0:
        raise RuntimeError(f"research_provider_cost_invalid:{name}")
    return parsed


def run() -> None:
    create_server().run(
        "streamable-http",
        host=os.getenv("DECISION_HUB_RESEARCH_MCP_HOST", "127.0.0.1"),
        port=int(os.getenv("DECISION_HUB_RESEARCH_MCP_PORT", "8002")),
        stateless_http=True,
    )


if __name__ == "__main__":
    run()

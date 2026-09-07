from __future__ import annotations

import os
from pathlib import Path

import yaml
from mcp.server.mcpserver import MCPServer

from packages.contracts_py.decision_hub_contracts import (
    ResearchCapabilityManifest,
    ResearchCapabilityQuery,
)
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
from packages.provider_adapters.macro_market import (
    ExpectationPricingResearchAdapter,
    FredSeriesResearchAdapter,
    IntradayMacroResearchAdapter,
)
from packages.provider_adapters.market import (
    CoinExMarketResearchAdapter,
    CryptoCrowdingResearchAdapter,
    CryptoEventWindowArchive,
    CryptoEventWindowResearchAdapter,
    OKXDerivativesResearchAdapter,
)
from packages.provider_adapters.official_sources import OfficialDocumentResearchAdapter
from packages.provider_adapters.research import (
    CryptoMacroFactPack,
    HttpDocumentResearchAdapter,
    ReplayResearchArchive,
    ReplayResearchCapabilityAdapter,
    ResearchSourceRegistry,
    WebSearchResearchAdapter,
    load_replay_archive,
)
from packages.provider_adapters.routing import ProviderCapabilityRouter
from packages.provider_adapters.search import DshNativeWebSearchTransport, TavilySearchTransport
from packages.workbench_adapters.durable_research_gateway import (
    DurableResearchCapabilityGateway,
)
from packages.workbench_adapters.research_mcp import build_research_mcp_server

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
        "adapter://provider/market/okx-public": OKXDerivativesResearchAdapter(),
        "adapter://provider/market/coinex-public": CoinExMarketResearchAdapter(),
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
) -> list[ResearchCapabilityAdapter]:
    """Build one adapter per stable capability, using Pack routes when declared."""

    registry = _provider_adapter_registry(event_window_adapter=event_window_adapter)
    if provider_registry is not None:
        registry.update(provider_registry)
    source_registry = ResearchSourceRegistry.from_pack(ROOT / "packs/crypto_macro")
    adapters: list[ResearchCapabilityAdapter] = [
        ReplayResearchCapabilityAdapter(
            capability_id="replay.research",
            fixtures=archive.queries,
            requirement_fixtures=archive.requirements,
        ),
        HttpDocumentResearchAdapter(
            capability_id="web.fetch",
            kind="web",
            authority="verified_web",
            source_id="web-fetch",
            source_registry=source_registry,
        ),
        OfficialDocumentResearchAdapter(source_registry=source_registry),
        FredSeriesResearchAdapter(),
    ]
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
            )
        )
    return adapters


def create_server() -> MCPServer:
    enabled = {
        item.strip()
        for item in os.getenv("DECISION_HUB_RESEARCH_CAPABILITIES", "").split(",")
        if item.strip()
    }
    archive = load_replay_archive(
        _optional_path(os.getenv("DECISION_HUB_RESEARCH_REPLAY_FIXTURES"))
    )
    manifests = load_capability_manifests()
    database: Database | None = None
    event_watches: EventWatchService | None = None
    event_window_adapter: ResearchCapabilityAdapter | None = None
    if "market.crypto_derivatives" in enabled:
        database = Database()
        database.create_all()
        event_watches = EventWatchService(database)
        event_window_adapter = CryptoEventWindowResearchAdapter(
            event_watches,
            CryptoEventWindowArchive(
                Path(os.getenv("DECISION_HUB_DATA_DIR", "data/decision-hub")) / "event-windows"
            ),
        )
    adapters = compose_capability_adapters(
        manifests,
        archive,
        event_window_adapter=event_window_adapter,
    )
    if "web.search" in enabled:
        adapters.append(
            WebSearchResearchAdapter(
                DshNativeWebSearchTransport.from_env(), capability_id="web.search"
            )
        )
    if "web.search.tavily" in enabled:
        adapters.append(
            WebSearchResearchAdapter(
                TavilySearchTransport.from_env(), capability_id="web.search.tavily"
            )
        )
    gateway = ResearchCapabilityGatewayService(
        manifests,
        adapters,
        enabled_capabilities=enabled,
    )
    # Replay-only subprocess smoke tests intentionally exercise the transport
    # without a product Run. Live product calls always use the durable
    # recorder, sharing the Hub SQLite data directory with the worker/API.
    if enabled != {"replay.research"} and os.getenv(
        "DECISION_HUB_RESEARCH_DURABLE_PROGRESS", "1"
    ).strip().lower() not in {"0", "false", "no", "off"}:
        database = database or Database()
        database.create_all()
        durable = DurableResearchCapabilityGateway(
            gateway,
            DshSessionLinkService(database),
            ResearchEvidenceService(database),
            ResearchObservabilityService(database),
            requirements={
                item.requirement_id: item
                for item in CryptoMacroFactPack.from_pack(
                    ROOT / "packs/crypto_macro"
                ).all_contract_requirements()
            },
            event_watches=event_watches,
        )
        return build_research_mcp_server(
            durable,
            bridge_key=os.getenv("DECISION_HUB_DSH_RESEARCH_TOOL_KEY"),
        )
    return build_research_mcp_server(
        gateway,
        bridge_key=os.getenv("DECISION_HUB_DSH_RESEARCH_TOOL_KEY"),
    )


def _optional_path(value: str | None) -> Path | None:
    return Path(value) if value else None


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

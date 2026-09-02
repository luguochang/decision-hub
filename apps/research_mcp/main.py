from __future__ import annotations

import os
from pathlib import Path

import yaml
from mcp.server.mcpserver import MCPServer

from packages.contracts_py.decision_hub_contracts import ResearchCapabilityManifest
from packages.kernel.decision_hub_kernel.application.dsh_sessions import DshSessionLinkService
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchCapabilityGatewayService,
    ResearchEvidenceService,
)
from packages.kernel.decision_hub_kernel.application.research_observability import (
    ResearchObservabilityService,
)
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.kernel.decision_hub_kernel.ports.research import ResearchCapabilityAdapter
from packages.provider_adapters.macro_market import FredSeriesResearchAdapter
from packages.provider_adapters.market import (
    CoinExMarketResearchAdapter,
    OKXDerivativesResearchAdapter,
)
from packages.provider_adapters.official_sources import OfficialDocumentResearchAdapter
from packages.provider_adapters.research import (
    CryptoMacroFactPack,
    HttpDocumentResearchAdapter,
    ReplayResearchCapabilityAdapter,
    WebSearchResearchAdapter,
    load_replay_archive,
)
from packages.provider_adapters.search import OpenAIResponsesWebSearchTransport
from packages.workbench_adapters.durable_research_gateway import (
    DurableResearchCapabilityGateway,
)
from packages.workbench_adapters.research_mcp import build_research_mcp_server

ROOT = Path(__file__).resolve().parents[2]


def _manifests() -> list[ResearchCapabilityManifest]:
    payload = yaml.safe_load(
        (ROOT / "packs/crypto_macro/tools/bindings.yaml").read_text(encoding="utf-8")
    )
    if not isinstance(payload, dict) or not isinstance(payload.get("capabilities"), list):
        raise RuntimeError("research_capability_bindings_invalid")
    return [ResearchCapabilityManifest.model_validate(item) for item in payload["capabilities"]]


def create_server() -> MCPServer:
    enabled = {
        item.strip()
        for item in os.getenv("DECISION_HUB_RESEARCH_CAPABILITIES", "").split(",")
        if item.strip()
    }
    archive = load_replay_archive(
        _optional_path(os.getenv("DECISION_HUB_RESEARCH_REPLAY_FIXTURES"))
    )
    market_provider = os.getenv(
        "DECISION_HUB_CRYPTO_DERIVATIVES_PROVIDER", "coinex"
    ).strip().lower()
    if market_provider == "coinex":
        market_adapter: ResearchCapabilityAdapter = CoinExMarketResearchAdapter()
    elif market_provider == "okx":
        market_adapter = OKXDerivativesResearchAdapter()
    else:
        raise RuntimeError("research_crypto_derivatives_provider_invalid")
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
        ),
        OfficialDocumentResearchAdapter(),
        market_adapter,
        FredSeriesResearchAdapter(),
    ]
    if "web.search" in enabled:
        adapters.append(
            WebSearchResearchAdapter(OpenAIResponsesWebSearchTransport.from_env())
        )
    gateway = ResearchCapabilityGatewayService(
        _manifests(),
        adapters,
        enabled_capabilities=enabled,
    )
    # Replay-only subprocess smoke tests intentionally exercise the transport
    # without a product Run. Live product calls always use the durable
    # recorder, sharing the Hub SQLite data directory with the worker/API.
    if enabled != {"replay.research"} and os.getenv(
        "DECISION_HUB_RESEARCH_DURABLE_PROGRESS", "1"
    ).strip().lower() not in {"0", "false", "no", "off"}:
        database = Database()
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


def run() -> None:
    create_server().run(
        "streamable-http",
        host=os.getenv("DECISION_HUB_RESEARCH_MCP_HOST", "127.0.0.1"),
        port=int(os.getenv("DECISION_HUB_RESEARCH_MCP_PORT", "8002")),
        stateless_http=True,
    )


if __name__ == "__main__":
    run()

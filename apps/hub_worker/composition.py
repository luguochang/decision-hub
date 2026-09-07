from __future__ import annotations

import asyncio
import os
import socket
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

from apps.hub_worker.research import DurableResearchWorker
from packages.contracts_py.decision_hub_contracts.models import CandidateProposal, EvolutionJobView
from packages.evals.runner import EvaluationRunner
from packages.kernel.decision_hub_kernel.application.admission import AdmissionService
from packages.kernel.decision_hub_kernel.application.dsh_sessions import DshSessionLinkService
from packages.kernel.decision_hub_kernel.application.event_watch import EventWatchService
from packages.kernel.decision_hub_kernel.application.evolution import EvolutionAssetService
from packages.kernel.decision_hub_kernel.application.live_observation import (
    EvolutionContextService,
    EvolutionExperienceService,
    EvolutionJobPolicy,
    EvolutionJobService,
    EvolutionTriggerScanner,
    ServiceHeartbeatService,
)
from packages.kernel.decision_hub_kernel.application.outbox import NotificationDispatcher
from packages.kernel.decision_hub_kernel.application.outcome_due import DueOutcomeService
from packages.kernel.decision_hub_kernel.application.scheduler import (
    RealtimeScheduler,
    SchedulerReport,
)
from packages.kernel.decision_hub_kernel.application.source_ingest import (
    RunTarget,
    SourceIngestionService,
)
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.kernel.decision_hub_kernel.ports.research import ResearchHarnessRuntime
from packages.kernel.decision_hub_kernel.ports.runtime import AgentRuntime
from packages.orchestration.langgraph import build_analyze_text_service
from packages.orchestration.langgraph.evolution_executor import (
    CandidateArtifactStore,
    EvolutionJobExecutor,
    FixtureEvaluationPlanFactory,
    SupervisorEvolutionPlanner,
)
from packages.pilot_runtime import PilotSettings, build_notification_adapters
from packages.provider_adapters.market import (
    CapabilitySnapshotWindowProvider,
    CoinExMarketResearchAdapter,
    CryptoCrowdingResearchAdapter,
    CryptoEventWindowArchive,
    CryptoEventWindowProvider,
    CryptoEventWindowSampler,
    OKXDerivativesResearchAdapter,
)
from packages.provider_adapters.market.okx import OKXPublicMarketAdapter
from packages.provider_adapters.research.discovery import CryptoMacroDiscoveryPolicy
from packages.runtime_adapters.dsh_runtime import (
    DshResearchRuntime,
    DshWebHostClient,
    DshWebHostConfig,
    DshWebResearchRuntime,
)
from packages.runtime_adapters.fake_runtime.runtime import FakeEvolutionAgentRuntime
from packages.runtime_adapters.langgraph_agent.runtime import LangGraphAgentRuntime
from packages.runtime_adapters.replay_runtime import ReplayResearchRuntime
from packages.source_adapters.official_feeds import official_source_presets
from packages.source_adapters.registry import SourceRegistry

APP_VERSION = "0.1.0"
DOMAIN_PACK = "crypto_macro.v1"


def _instance_id(role: str) -> str:
    configured = os.getenv("DECISION_HUB_INSTANCE_ID")
    return configured or f"{socket.gethostname()}:{os.getpid()}:{role}"


def _runtime_mode(runtime: AgentRuntime) -> Literal["fake", "replay", "provider"]:
    if getattr(runtime, "enabled", False):
        return "provider"
    return "replay" if "replay" in runtime.runtime_id else "fake"


@dataclass
class RealtimeWorker:
    scheduler: RealtimeScheduler
    heartbeats: ServiceHeartbeatService
    instance_id: str
    mode: str
    heartbeat_interval_seconds: float
    started_at: datetime

    async def tick(self) -> SchedulerReport:
        self._beat()
        try:
            result = await self.scheduler.tick()
        except Exception:
            self._beat("realtime_tick_failed")
            raise
        self._beat("realtime_run_failed" if result.runs_failed else None)
        return result

    def _beat(self, error_code: str | None = None) -> None:
        self.heartbeats.beat(
            service_id="hub-realtime-worker",
            role="realtime_worker",
            instance_id=self.instance_id,
            version=APP_VERSION,
            mode=self.mode,
            interval_seconds=self.heartbeat_interval_seconds,
            started_at=self.started_at,
            last_error_code=error_code,
        )


@dataclass
class EvolutionWorker:
    executor: EvolutionJobExecutor
    heartbeats: ServiceHeartbeatService
    instance_id: str
    mode: str
    heartbeat_interval_seconds: float
    started_at: datetime

    async def tick(self) -> EvolutionJobView | None:
        self._beat()
        try:
            result = await self.executor.tick(self.instance_id)
        except Exception:
            self._beat("evolution_tick_failed")
            raise
        error_code = (
            result.last_error_code
            if result is not None and result.status in {"retry_wait", "failed"}
            else None
        )
        self._beat(error_code)
        return result

    def _beat(self, error_code: str | None = None) -> None:
        self.heartbeats.beat(
            service_id="hub-evolution-worker",
            role="evolution_worker",
            instance_id=self.instance_id,
            version=APP_VERSION,
            mode=self.mode,
            interval_seconds=self.heartbeat_interval_seconds,
            started_at=self.started_at,
            last_error_code=error_code,
        )


@dataclass
class ResearchWorker:
    worker: DurableResearchWorker
    heartbeats: ServiceHeartbeatService
    instance_id: str
    mode: str
    heartbeat_interval_seconds: float
    started_at: datetime

    async def tick(self):
        self._beat()
        heartbeat_task = asyncio.create_task(self._heartbeat_while_running())
        try:
            result = await self.worker.tick()
        except Exception:
            self._beat("research_tick_failed")
            raise
        finally:
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)
        self._beat("research_worker_failed" if result and result.status == "failed" else None)
        return result

    async def close(self) -> None:
        await self.worker.runtime.close()

    def _beat(self, error_code: str | None = None) -> None:
        self.heartbeats.beat(
            service_id="hub-research-worker",
            role="research_worker",
            instance_id=self.instance_id,
            version=APP_VERSION,
            mode=self.mode,
            interval_seconds=self.heartbeat_interval_seconds,
            started_at=self.started_at,
            last_error_code=error_code,
        )

    async def _heartbeat_while_running(self) -> None:
        """Keep long DSH runs visible without changing the durable Run lease."""

        interval = max(1.0, self.heartbeat_interval_seconds)
        while True:
            await asyncio.sleep(interval)
            try:
                self._beat()
            except Exception:
                # The next regular beat/readiness check remains authoritative;
                # a transient heartbeat write must not cancel research work.
                continue


def build_realtime_worker(
    database: Database,
    *,
    registry: SourceRegistry | None = None,
    runtime: AgentRuntime | None = None,
    settings: PilotSettings | None = None,
    heartbeat_interval_seconds: float = 10,
    window_providers: Iterable[CryptoEventWindowProvider] | None = None,
) -> RealtimeWorker:
    selected_settings = settings or PilotSettings.from_env()
    selected_registry = registry or SourceRegistry()
    if registry is None and selected_settings.sources_enabled:
        include_calendar = os.getenv(
            "DECISION_HUB_CALENDAR_DISCOVERY_ENABLED", "0"
        ).strip().lower() in {"1", "true", "yes", "on"}
        for source in official_source_presets(include_calendar=include_calendar):
            selected_registry.register(source)
    selected_runtime = runtime or LangGraphAgentRuntime()
    data_dir = Path(os.getenv("DECISION_HUB_DATA_DIR", "data/decision-hub"))
    analyzer = build_analyze_text_service(
        database,
        selected_runtime,
        checkpoint_path=data_dir / "checkpoints" / "decision_graph.sqlite3",
    )
    event_watches = EventWatchService(database)
    ingestion = SourceIngestionService(
        database,
        selected_registry,
        admission=AdmissionService(database),
        strategy_selector=CryptoMacroDiscoveryPolicy.from_pack(
            Path(__file__).resolve().parents[2] / "packs" / "crypto_macro"
        ),
        event_watches=event_watches,
    )

    async def execute_target(target: RunTarget) -> None:
        await analyzer.run_admitted(target.event_id, target.run_id)

    window_sampler = _build_event_window_sampler(
        selected_settings,
        data_dir,
        window_providers,
    )
    scheduler = RealtimeScheduler(
        ingestion,
        run_executor=execute_target,
        outcomes=(
            DueOutcomeService(database, OKXPublicMarketAdapter())
            if selected_settings.market_enabled
            else None
        ),
        notifications=NotificationDispatcher(
            database,
            build_notification_adapters(selected_settings, data_dir),
        ),
        window_sampler=window_sampler,
    )
    return RealtimeWorker(
        scheduler=scheduler,
        heartbeats=ServiceHeartbeatService(database),
        instance_id=_instance_id("realtime"),
        mode=_runtime_mode(selected_runtime),
        heartbeat_interval_seconds=heartbeat_interval_seconds,
        started_at=datetime.now(UTC),
    )


def _build_event_window_sampler(
    settings: PilotSettings,
    data_dir: Path,
    providers: Iterable[CryptoEventWindowProvider] | None,
) -> CryptoEventWindowSampler | None:
    """Enable event captures only when market data and the explicit flag are on."""

    enabled = os.getenv("DECISION_HUB_EVENT_WINDOW_LIVE_ENABLED", "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if not settings.market_enabled or (providers is None and not enabled):
        return None
    selected = (
        tuple(providers)
        if providers is not None
        else (
            CapabilitySnapshotWindowProvider(
                "okx-public",
                OKXDerivativesResearchAdapter(),
                symbols=("BTC-USDT-SWAP",),
                fields=("ticker", "funding_rate", "open_interest", "mark_price"),
            ),
            CapabilitySnapshotWindowProvider(
                "coinex-public",
                CoinExMarketResearchAdapter(),
                symbols=("BTC",),
                fields=(
                    "spot_price",
                    "spot_volume",
                    "funding_rate",
                    "open_interest",
                    "mark_price",
                    "index_price",
                    "basis",
                ),
            ),
            CapabilitySnapshotWindowProvider(
                "okx-orderbook-public",
                CryptoCrowdingResearchAdapter(
                    provider_id="okx-orderbook-public",
                    base_url="https://www.okx.com",
                    exchange="okx",
                ),
                symbols=("BTC-USDT-SWAP",),
                fields=("crowding_signal", "book_imbalance", "bid_depth", "ask_depth"),
            ),
        )
    )
    if not selected:
        raise ValueError("event_window_provider_required")
    # Constructing the sampler validates provider identity before the worker
    # starts; EventWatch state remains owned by SourceIngestionService.
    return CryptoEventWindowSampler(
        selected,
        CryptoEventWindowArchive(data_dir / "event-windows"),
    )


def build_research_worker(
    database: Database,
    *,
    runtime: ResearchHarnessRuntime | None = None,
    heartbeat_interval_seconds: float = 10,
    allowed_capabilities: tuple[str, ...] | None = None,
    lease_seconds: int = 240,
) -> ResearchWorker:
    """Build the durable agentic research composition root.

    The DSH adapter is lazy about provider credentials, so an offline tick with
    no admitted research run remains safe and deterministic. Tests and replay
    tooling can inject another ``ResearchHarnessRuntime`` without changing the
    worker or Product Kernel.
    """
    selected_runtime = runtime or _research_runtime_from_env(database)
    selected_capabilities = allowed_capabilities or _research_capabilities_from_env()
    data_dir = Path(os.getenv("DECISION_HUB_DATA_DIR", "data/decision-hub"))
    root = Path(__file__).resolve().parents[2]
    worker = DurableResearchWorker(
        database,
        selected_runtime,
        pack_root=root / "packs" / "crypto_macro",
        checkpoint_path=data_dir / "checkpoints" / "research_graph.sqlite3",
        worker_id=_instance_id("research"),
        allowed_capabilities=selected_capabilities,
        execution_mode=_research_execution_mode_from_env(selected_runtime),
        lease_seconds=lease_seconds,
    )
    return ResearchWorker(
        worker=worker,
        heartbeats=ServiceHeartbeatService(database),
        instance_id=_instance_id("research"),
        mode=(
            "provider"
            if isinstance(selected_runtime, (DshResearchRuntime, DshWebResearchRuntime))
            else "replay"
        ),
        heartbeat_interval_seconds=heartbeat_interval_seconds,
        started_at=datetime.now(UTC),
    )


def _research_capabilities_from_env() -> tuple[str, ...]:
    """Read an explicit Pack capability allowlist without opening live tools by default."""

    configured = os.getenv("DECISION_HUB_RESEARCH_CAPABILITIES", "replay.research")
    capabilities = tuple(
        dict.fromkeys(item.strip() for item in configured.split(",") if item.strip())
    )
    return capabilities or ("replay.research",)


def _research_execution_mode_from_env(
    runtime: ResearchHarnessRuntime,
) -> Literal["live", "replay"]:
    """Keep the product PIT mode independent from the Harness transport.

    A DSH Web process can use either a real model provider or the pinned
    upstream replay adapter. The durable request must state which Evidence/PIT
    semantics apply instead of inferring them from the Python adapter class.
    """

    configured = os.getenv("DECISION_HUB_RESEARCH_EXECUTION_MODE")
    if configured is None or not configured.strip():
        return "replay" if isinstance(runtime, ReplayResearchRuntime) else "live"
    normalized = configured.strip().lower()
    if normalized not in {"live", "replay"}:
        raise ValueError("research_execution_mode_invalid")
    return cast(Literal["live", "replay"], normalized)


def _research_runtime_from_env(database: Database) -> ResearchHarnessRuntime:
    mode = os.getenv("DECISION_HUB_RESEARCH_RUNTIME", "dsh").strip().lower()
    if mode == "dsh":
        return DshResearchRuntime()
    if mode == "dsh-web":
        config = DshWebHostConfig.from_env()
        return DshWebResearchRuntime(
            DshSessionLinkService(database),
            DshWebHostClient(config),
            poll_interval_seconds=float(
                os.getenv("DECISION_HUB_DSH_POLL_INTERVAL_SECONDS", "0.25")
            ),
            readiness_grace_seconds=float(
                os.getenv("DECISION_HUB_DSH_READINESS_GRACE_SECONDS", "10")
            ),
        )
    if mode == "replay":
        fixture_path = os.getenv("DECISION_HUB_RESEARCH_RUNTIME_FIXTURE")
        if not fixture_path:
            raise ValueError("research_runtime_replay_fixture_required")
        return ReplayResearchRuntime.from_path(Path(fixture_path))
    raise ValueError("research_runtime_mode_invalid")


def build_evolution_worker(
    database: Database,
    *,
    runtime: AgentRuntime | None = None,
    planning_runtime: AgentRuntime | None = None,
    heartbeat_interval_seconds: float = 10,
) -> EvolutionWorker:
    selected_runtime = runtime or LangGraphAgentRuntime()
    planner_runtime = planning_runtime or LangGraphAgentRuntime(
        response_model=CandidateProposal,
        fallback_runtime=FakeEvolutionAgentRuntime(),
        system_prompt=(
            "Propose only a bounded Decision Hub strategy/runtime/profile/doctrine/provider-policy "
            "candidate. Never propose code, SQL, secrets, Gate changes, trading, or auto-promotion."
        ),
    )
    data_dir = Path(os.getenv("DECISION_HUB_DATA_DIR", "data/decision-hub"))
    root = Path(__file__).resolve().parents[2]
    assets = EvolutionAssetService(database)
    pointer = assets.ensure_release_baseline(DOMAIN_PACK)
    baseline = assets.get_candidate(pointer.candidate_id)
    if baseline is None:
        raise RuntimeError("release_baseline_missing")
    jobs = EvolutionJobService(database)
    scanner = EvolutionTriggerScanner(
        database,
        jobs,
        assets,
        policy=EvolutionJobPolicy(domain_pack_ref=DOMAIN_PACK),
    )
    fixture_root = root / "fixtures" / "evolution"
    artifact_store = CandidateArtifactStore(data_dir / "evolution")
    plans = FixtureEvaluationPlanFactory(
        {
            "replay": (fixture_root / "replay" / "powell-higher-for-longer.json",),
            "holdout": (fixture_root / "holdout" / "powell-higher-for-longer.json",),
            "shadow": (fixture_root / "shadow" / "powell-higher-for-longer.json",),
        },
        baseline_runtime=selected_runtime,
        candidate_base_runtime=selected_runtime,
        baseline_version=baseline.version,
        assets=assets,
        artifacts=artifact_store,
    )
    executor = EvolutionJobExecutor(
        jobs=jobs,
        scanner=scanner,
        context=EvolutionContextService(database),
        planner=SupervisorEvolutionPlanner(
            planner_runtime,
            available_capabilities={"counter_thesis", "data_quality", "market_transmission"},
            required_capabilities=("counter_thesis", "data_quality"),
        ),
        artifacts=artifact_store,
        assets=assets,
        evaluation_plans=plans,
        evaluation_runner=EvaluationRunner(data_dir / "evaluations"),
        experiences=EvolutionExperienceService(database, assets),
    )
    return EvolutionWorker(
        executor=executor,
        heartbeats=ServiceHeartbeatService(database),
        instance_id=_instance_id("evolution"),
        mode=_runtime_mode(selected_runtime),
        heartbeat_interval_seconds=heartbeat_interval_seconds,
        started_at=datetime.now(UTC),
    )

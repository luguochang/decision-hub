from __future__ import annotations

import asyncio
import json
import os
import socket
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

from apps.hub_worker.documents import DocumentPipelineWorker
from apps.hub_worker.harness_baseline import (
    HarnessBaselineWorkerConfig,
    configured_baseline_first_freeze,
)
from apps.hub_worker.historical import HistoricalRefreshWorker
from apps.hub_worker.installed_harness_bootstrap import (
    InstalledHarnessBootstrapRequest,
    produce_installed_harness_baseline,
)
from apps.hub_worker.research import (
    DurableResearchWorker,
    ResearchWorkerReport,
)
from apps.hub_worker.work_admission import HeavyWorkAdmission
from packages.contracts_py.decision_hub_contracts.models import EvolutionJobView
from packages.evals.fixture_audit import default_evolution_fixtures
from packages.evals.runner import EvaluationRunner
from packages.kernel.decision_hub_kernel.application.admission import AdmissionService
from packages.kernel.decision_hub_kernel.application.analytical_assessment import (
    AnalyticalAssessmentService,
)
from packages.kernel.decision_hub_kernel.application.analytical_observation import (
    AnalyticalObservationService,
)
from packages.kernel.decision_hub_kernel.application.calibration import (
    KernelCalibrationProducer,
    configure_probability_archive,
)
from packages.kernel.decision_hub_kernel.application.data_jobs import DataJobService
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
from packages.kernel.decision_hub_kernel.application.research_harness_artifact import (
    InstalledHarnessBaseline,
)
from packages.kernel.decision_hub_kernel.application.research_harness_consumption import (
    HarnessFirstFreezeService,
)
from packages.kernel.decision_hub_kernel.application.research_library import ResearchLibraryService
from packages.kernel.decision_hub_kernel.application.scheduler import (
    RealtimeScheduler,
    SchedulerReport,
)
from packages.kernel.decision_hub_kernel.application.source_ingest import (
    RunTarget,
    SourceIngestionService,
)
from packages.kernel.decision_hub_kernel.application.study_effect_tracking import (
    StudyEffectTrackingService,
)
from packages.kernel.decision_hub_kernel.persistence.db import Database, utcnow
from packages.kernel.decision_hub_kernel.persistence.sqlite_busy import is_sqlite_storage_busy
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
from packages.provider_adapters.documents import (
    DocumentParserRouter,
    FinancialSemanticCorrectionAdapter,
    HeuristicAnalystClaimExtractor,
    ImagePlaceholderDocumentParser,
    MineruCliDocumentParser,
    ModelFinancialSemanticCorrectionAdapter,
    PypdfTextDocumentParser,
)
from packages.provider_adapters.historical import (
    GateDailyDataLoader,
    HistoricalDataArchive,
    HistoricalDataJobRunner,
    OKXHistoricalDataLoader,
)
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
from packages.provider_adapters.research.document_bundle_store import DocumentBundleStore
from packages.provider_adapters.research.raw_capture import RawBodyArchive
from packages.runtime_adapters.dsh_runtime import (
    DshResearchRuntime,
    DshWebHostClient,
    DshWebHostConfig,
    DshWebResearchRuntime,
)
from packages.runtime_adapters.fake_runtime.runtime import FakeEvolutionAgentRuntime
from packages.runtime_adapters.langgraph_agent.evolution import EvolutionPlanningRuntime
from packages.runtime_adapters.langgraph_agent.runtime import LangGraphAgentRuntime
from packages.runtime_adapters.replay_runtime import ReplayResearchRuntime
from packages.source_adapters.official_feeds import official_source_presets
from packages.source_adapters.registry import SourceRegistry
from packages.workbench_adapters.source_capture import (
    observation_capture_context,
    source_capture_context,
)

APP_VERSION = "0.1.0"
DOMAIN_PACK = "crypto_macro.v1"


if TYPE_CHECKING:
    from packages.kernel.decision_hub_kernel.application.history_observed_commands import (
        HistoryObservedCommandService,
    )


def build_history_observed_worker(
    database: Database,
    *,
    owner_id: str,
    clock: Callable[[], datetime] = utcnow,
) -> HistoryObservedCommandService:
    """Compose opt-in history commands without registering or executing work."""
    from packages.kernel.decision_hub_kernel.application.history_observed_acquisition import (
        HistoryObservedAcquisition,
    )
    from packages.kernel.decision_hub_kernel.application.history_observed_commands import (
        HistoryObservedCommandService,
    )
    from packages.kernel.decision_hub_kernel.application.history_observed_dataset import (
        HistoryObservedArchive,
        HistoryObservedDatasetBuilder,
        HistoryObservedOriginals,
    )
    from packages.kernel.decision_hub_kernel.application.history_observed_ledger import (
        HistoryObservedLedger,
    )
    from packages.kernel.decision_hub_kernel.application.history_observed_model import (
        HistoryObservedModelService,
    )

    data_dir = Path(os.getenv("DECISION_HUB_DATA_DIR", "data/decision-hub"))
    raw = RawBodyArchive(data_dir / "raw-captures")
    builder = HistoryObservedDatasetBuilder(
        HistoryObservedOriginals(raw.read), HistoryObservedArchive(data_dir / "history-observed"),
        clock=clock,
    )
    ledger = HistoryObservedLedger(Path(__file__).resolve().parents[2], builder, clock=clock)
    acquisition = HistoryObservedAcquisition(database, ledger, raw, owner_id=owner_id)
    return HistoryObservedCommandService(
        database, ledger, acquisition, HistoryObservedModelService(ledger),
    )


def _instance_id(role: str) -> str:
    configured = os.getenv("DECISION_HUB_INSTANCE_ID")
    return configured or f"{socket.gethostname()}:{os.getpid()}:{role}"


def build_research_admission_tick(*, perpetual_forecasts, periodic_admission, previous_research_tick):
    """The same normal scheduler callback, injectable without booting services."""
    async def tick():
        operations = [
            asyncio.create_task(periodic_admission.process_due(limit=1)),
            asyncio.create_task(perpetual_forecasts.tick()),
        ]
        if previous_research_tick is not None:
            operations.append(asyncio.create_task(previous_research_tick()))
        try:
            results = await asyncio.gather(*operations, return_exceptions=True)
        except BaseException:
            for operation in operations:
                if not operation.done():
                    operation.cancel()
            await asyncio.gather(*operations, return_exceptions=True)
            raise
        for result in results:
            if isinstance(result, BaseException):
                raise result
        return sum(results)

    return tick


def build_perpetual_paper_execution(database, archive, *, clock):
    """One funding-original reader shared by execution and durable experience retry."""
    from packages.kernel.decision_hub_kernel.application.okx_perpetual_source import (
        OKXPerpetualSource,
    )
    from packages.kernel.decision_hub_kernel.application.perpetual_execution_protocol import (
        PerpetualPaperProtocolService,
    )
    from packages.kernel.decision_hub_kernel.application.perpetual_funding_history_source import (
        PerpetualFundingHistorySource,
    )
    from packages.kernel.decision_hub_kernel.application.perpetual_manual_execution import (
        PerpetualManualPaperService,
    )
    from packages.kernel.decision_hub_kernel.application.perpetual_paper import (
        PerpetualPaperService,
    )
    from packages.kernel.decision_hub_kernel.application.perpetual_paper_experience import (
        PerpetualPaperExperienceService,
    )

    source = OKXPerpetualSource(database, archive)
    funding = PerpetualFundingHistorySource(database, archive)
    manual = PerpetualManualPaperService(
        database,
        PerpetualPaperService(database, source, now=clock),
        PerpetualPaperProtocolService(database, source, clock=clock),
        clock=clock,
        funding_history=funding,
    )
    return manual, PerpetualPaperExperienceService(database, funding, clock=clock)


def build_paper_experience_execution_tick(execution, *, bootstrap, paper_experience):
    """Retry after execution in its existing bounded thread and cancellation owner."""
    from apps.hub_worker.action_execution_tick import ActionExecutionTick

    class ExecutionWithExperience:
        def process_due(self, *, max_intents, checkpoint):
            result = execution.process_due(max_intents=max_intents, checkpoint=checkpoint)
            checkpoint()
            paper_experience.process_due(limit=4)
            checkpoint()
            return result

    return ActionExecutionTick(ExecutionWithExperience(), bootstrap=bootstrap).tick


def _action_product_enabled(*, spot_enabled: bool) -> bool:
    configured = os.getenv("DECISION_HUB_ACTION_PRODUCT_ENABLED", "0")
    if configured not in {"0", "1"}:
        raise ValueError("action_product_flag_invalid")
    enabled = configured == "1"
    if enabled and not spot_enabled:
        raise ValueError("action_product_spot_research_required")
    return enabled


def _action_execution_enabled(*, action_enabled: bool) -> bool:
    configured = os.getenv("DECISION_HUB_ACTION_EXECUTION_ENABLED", "0")
    if configured not in {"0", "1"}:
        raise ValueError("action_execution_flag_invalid")
    if configured == "1" and not action_enabled:
        raise ValueError("action_execution_product_required")
    return configured == "1"


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
        heartbeat_task = asyncio.create_task(self._heartbeat_while_running())
        try:
            result = await self.scheduler.tick()
        except Exception:
            self._beat("realtime_tick_failed")
            raise
        finally:
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)
        self._beat("realtime_run_failed" if result.runs_failed else None)
        return result

    async def run_forever(self, interval_seconds: float) -> None:
        self._beat()
        heartbeat_task = asyncio.create_task(self._heartbeat_while_running())
        try:
            await self.scheduler.run_forever(interval_seconds)
        finally:
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)

    async def _heartbeat_while_running(self) -> None:
        while True:
            await asyncio.sleep(max(1.0, self.heartbeat_interval_seconds))
            try:
                self._beat("realtime_channel_failed" if self.scheduler.channel_errors else None)
            except Exception:
                continue

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
    _pending_heartbeat_error: str | None = field(default=None, init=False, repr=False)

    async def tick(self) -> EvolutionJobView | ResearchWorkerReport | None:
        if not self._try_beat(self._pending_heartbeat_error, phase="heartbeat_start"):
            return ResearchWorkerReport(
                status="maintenance_pending", error_code="evolution_heartbeat_storage_busy",
                retryable=True, recorded_in_database=False,
            )
        heartbeat_task = asyncio.create_task(self._heartbeat_while_running())
        try:
            result = await self.executor.tick(self.instance_id)
        except Exception:
            self._try_beat("evolution_tick_failed", phase="heartbeat_after_error")
            raise
        finally:
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)
        error_code = (
            result.last_error_code
            if result is not None and result.status in {"retry_wait", "failed"}
            else None
        )
        self._try_beat(error_code, phase="heartbeat_finish")
        return result

    def _try_beat(self, error_code: str | None, *, phase: str) -> bool:
        try:
            self._beat(error_code)
        except Exception as error:
            if not is_sqlite_storage_busy(error):
                raise
            self._pending_heartbeat_error = error_code or "evolution_heartbeat_storage_busy"
            print(json.dumps({
                "role": "evolution", "status": "maintenance_pending", "phase": phase,
                "instance_id": self.instance_id, "error_code": self._pending_heartbeat_error,
                "retryable": True, "recorded_in_database": False,
                "observed_at": datetime.now(UTC).isoformat(),
            }), flush=True)
            return False
        self._pending_heartbeat_error = None
        return True

    async def _heartbeat_while_running(self) -> None:
        while True:
            await asyncio.sleep(max(1.0, self.heartbeat_interval_seconds))
            try:
                self._beat()
            except Exception:
                continue

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
    service_id: str = "hub-research-worker"
    _pending_heartbeat_error: str | None = field(default=None, init=False, repr=False)
    # Independent of research/publication admission: a long admitted scheduler
    # must not starve health. This gate also serializes outer/inner beat loops.
    _heartbeat_admission: HeavyWorkAdmission = field(
        default_factory=HeavyWorkAdmission, init=False, repr=False,
    )

    async def _heartbeat_attempt(self, error_code: str | None = None, *, phase: str) -> bool:
        return await self._heartbeat_admission.run_thread(
            self._try_beat, error_code, phase=phase,
        )

    @staticmethod
    async def _stop_heartbeat(task: asyncio.Task[None]) -> None:
        task.cancel()
        interrupted = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                parent = asyncio.current_task()
                interrupted = interrupted or bool(parent and parent.cancelling())
            except Exception:
                break
        # Retrieve the terminal exception without leaking a pending native beat.
        if not task.cancelled():
            task.result()
        if interrupted:
            raise asyncio.CancelledError

    async def tick(self):
        if not await self._heartbeat_attempt(phase="heartbeat_start"):
            return ResearchWorkerReport(
                status="maintenance_pending",
                error_code="research_heartbeat_storage_busy",
                retryable=True,
            )
        heartbeat_task = asyncio.create_task(self._heartbeat_while_running())
        try:
            result = await self.worker.tick()
        except Exception:
            # A diagnostic write must not replace the original business error.
            try:
                await self._heartbeat_attempt("research_tick_failed", phase="heartbeat_after_error")
            except Exception:
                self._record_maintenance(
                    "research_heartbeat_failed", "heartbeat_after_error", False,
                )
            raise
        finally:
            await self._stop_heartbeat(heartbeat_task)
        error_code = (
            "research_worker_failed" if result and result.status == "failed"
            else result.error_code if result and result.status in {
                "maintenance_pending", "terminal_storage_pending",
            }
            else None
        )
        saved = await self._heartbeat_attempt(error_code, phase="heartbeat_finish")
        if result and result.status == "maintenance_pending":
            phase = "claim" if result.error_code == "research_claim_storage_busy" else "scheduler"
            self._record_maintenance(result.error_code, phase, saved)
        # If the final heartbeat was busy, retain the original outcome. Research
        # may already have committed; its operation must never be repeated here.
        return result

    def _record_maintenance(self, error_code: str | None, phase: str, saved: bool) -> None:
        # Managed stdout is the durable fallback while SQLite cannot accept a
        # heartbeat. Do not claim that this diagnostic was committed to the DB.
        print(json.dumps({
            "role": "research", "status": "maintenance_pending", "phase": phase,
            "instance_id": self.instance_id, "error_code": error_code,
            "retryable": True, "recorded_in_database": saved,
            "observed_at": datetime.now(UTC).isoformat(),
        }), flush=True)

    def _try_beat(self, error_code: str | None, *, phase: str) -> bool:
        # Normal async callers serialize this whole callable, including the
        # pending-code read, rather than capturing an obsolete value in a queue.
        if phase == "heartbeat_start" and error_code is None:
            error_code = self._pending_heartbeat_error
        try:
            self._beat(error_code)
        except Exception as error:
            if not is_sqlite_storage_busy(error):
                raise
            # This fixed heartbeat transaction rolls back and closes its owned
            # Session on failure; it cannot publish research or create commands.
            self._pending_heartbeat_error = error_code or "research_heartbeat_storage_busy"
            self._record_maintenance(self._pending_heartbeat_error, phase, False)
            return False
        self._pending_heartbeat_error = None
        return True

    async def close(self) -> None:
        await self.worker.runtime.close()

    def _beat(self, error_code: str | None = None) -> None:
        self.heartbeats.beat(
            service_id=self.service_id,
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
                await self._heartbeat_attempt(phase="heartbeat_background")
            except Exception:
                # Fixed safe diagnostics: never print provider/native contents.
                # Failure remains visible without cancelling legitimate research.
                self._record_maintenance(
                    "research_heartbeat_failed", "heartbeat_background", False,
                )


def build_realtime_worker(
    database: Database,
    *,
    registry: SourceRegistry | None = None,
    runtime: AgentRuntime | None = None,
    settings: PilotSettings | None = None,
    heartbeat_interval_seconds: float = 10,
    window_providers: Iterable[CryptoEventWindowProvider] | None = None,
) -> RealtimeWorker:
    spot_enabled = os.getenv("DECISION_HUB_SPOT_RESEARCH_ENABLED", "0") == "1"
    action_enabled = _action_product_enabled(spot_enabled=spot_enabled)
    selected_settings = settings or PilotSettings.from_env()
    action_review_enabled = selected_settings.action_review_enabled
    if action_review_enabled and not action_enabled:
        raise ValueError("action_review_requires_action_product")
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
        poll_context=source_capture_context(database, data_dir),
        strategy_selector=CryptoMacroDiscoveryPolicy.from_pack(
            Path(__file__).resolve().parents[2] / "packs" / "crypto_macro"
        ),
        event_watches=event_watches,
    )

    async def execute_target(target: RunTarget) -> None:
        await analyzer.run_admitted(
            target.event_id, target.run_id, worker_id=f"{_instance_id('realtime')}:{os.getpid()}"
        )

    window_sampler = _build_event_window_sampler(
        selected_settings,
        data_dir,
        window_providers,
    )
    risk_data = risk_monitor = None
    market_components = None
    if spot_enabled:
        from apps.hub_worker.market_component_bootstrap import build_market_components

        market_components = build_market_components(database)
    risk_enabled = os.getenv("DECISION_HUB_RISK_ENABLED", "0").strip().lower() in {
        "1", "true", "yes", "on",
    }
    if risk_enabled:
        from decimal import Decimal

        from packages.kernel.decision_hub_kernel.application.market_risk_data import (
            MarketRiskDataService,
        )
        from packages.kernel.decision_hub_kernel.application.risk_monitor import RiskMonitorService
        from packages.kernel.decision_hub_kernel.ports.risk_monitor import (
            RiskComparisonV1,
            RiskConditionV1,
            RiskMetricV1,
        )

        risk_data = MarketRiskDataService(
            database, RawBodyArchive(data_dir / "raw-captures"),
            data_dir / "market-risk" / "features", enabled=True,
            component_consumer=market_components,
        )
        risk_monitor = RiskMonitorService(
            database, risk_data, component_consumer=market_components,
            channel=selected_settings.notification_channel,
        )
        for scope in risk_data.scopes.values():
            threshold = Decimal("0.015") if scope.instrument == "BTC-USDT" else Decimal("0.02")
            risk_monitor.ensure_plan(RiskConditionV1(
                condition_id=f"{scope.instrument.lower()}-spot-move30.v1", scope_id=scope.scope_id,
                expression=RiskComparisonV1(left=RiskMetricV1(), right=threshold),
            ))
    research_tick = shadow_tick = memory_tick = enhancement_tick = action_tick = None
    quote_tick = execution_tick = None
    execution_enabled = _action_execution_enabled(action_enabled=action_enabled)
    if spot_enabled:
        if risk_data is None:
            raise ValueError("spot_research_requires_risk_data")
        from apps.hub_worker.market_enhancement_bootstrap import MarketEnhancementTick
        from apps.hub_worker.market_memory import MarketMemoryTick
        from apps.hub_worker.market_risk_loops import PathShadowTick, SpotResearchTick
        from apps.hub_worker.spot_research_bootstrap import InstalledSpotResearch
        from packages.kernel.decision_hub_kernel.application.forecast_alarm import (
            ForecastAlarmService,
        )
        from packages.kernel.decision_hub_kernel.application.risk_path_ledger import RiskPathLedger

        installed_spot = InstalledSpotResearch(
            database, data_dir, enabled=True,
            host_url=os.getenv("DECISION_HUB_DSH_WEB_URL", "http://127.0.0.1:3097")
            .rstrip("/") + "/decision-hub/v1/readiness",
        )
        research_tick = SpotResearchTick(
            database, installed_spot,
            action_review_enabled=action_review_enabled,
        ).tick
        forecast_alarms = ForecastAlarmService(database, risk_data.store)
        shadow_tick = PathShadowTick(
            RiskPathLedger(
                database, risk_data.store, component_consumer=market_components,
                forecast_alarm_publisher=forecast_alarms.publish_pair_in_session,
                forecast_alarm_score_reader=forecast_alarms.score_in_session,
                forecast_alarm_original_reader=forecast_alarms.read_score_in_session,
            ),
            owner_id=_instance_id("path-shadow"),
        ).tick
        memory_tick = MarketMemoryTick(database, RawBodyArchive(data_dir / "raw-captures")).tick
        enhancements = MarketEnhancementTick(
            database, data_dir,
            enabled=os.getenv("DECISION_HUB_MARKET_ENHANCEMENTS_ENABLED", "1") == "1",
        )
        enhancements.start()
        enhancement_tick = enhancements.tick
        if action_enabled:
            from apps.hub_worker.action_tick import ActionMonitorTick
            from packages.kernel.decision_hub_kernel.application.action_monitor import (
                ActionMonitorService,
            )

            action_tick = ActionMonitorTick(
                ActionMonitorService(
                    database,
                    risk_data.store,
                    feature_root=data_dir / "action-product" / "features",
                    clock=risk_data.clock,
                )
            ).tick
            from apps.hub_worker.action_quote_tick import ActionQuoteTick
            from packages.kernel.decision_hub_kernel.application.action_execution import (
                ActionExecutionService,
            )
            from packages.kernel.decision_hub_kernel.application.action_execution_demand import (
                ExecutionQuoteDemandReader,
            )
            from packages.kernel.decision_hub_kernel.application.action_quote_demand import (
                ActionQuoteDemandReader,
            )
            from packages.kernel.decision_hub_kernel.application.action_quote_sampling import (
                QuoteSamplingService,
            )

            demand = ActionQuoteDemandReader(database.factory).scopes
            if execution_enabled:
                perpetual_archive = RawBodyArchive(data_dir / "raw-captures")
                perpetual_manual, paper_experience = build_perpetual_paper_execution(
                    database, perpetual_archive, clock=risk_data.clock
                )
                execution = ActionExecutionService(
                    database,
                    market_store=risk_data.store,
                    feature_root=data_dir / "action-product" / "features",
                    clock=risk_data.clock,
                    enabled=True,
                    perpetual_execution=perpetual_manual,
                )
                from packages.kernel.decision_hub_kernel.application.action_paper_product import (
                    ActionPaperProductService,
                )

                execution_tick = build_paper_experience_execution_tick(
                    execution, bootstrap=ActionPaperProductService(execution),
                    paper_experience=paper_experience,
                )
                demand = ExecutionQuoteDemandReader(database.factory, action_demand=demand).scopes
            quote_tick = ActionQuoteTick(
                QuoteSamplingService(
                    database.factory,
                    RawBodyArchive(data_dir / "raw-captures"),
                    owner_id=_instance_id("action-quotes"),
                    demand_reader=demand,
                    clock=risk_data.clock,
                )
            ).tick
    # Same admission channel and durable request/Run queues; only explicit saved
    # owner schedules create periodic work. Empty configuration performs no I/O.
    from packages.runtime_adapters.perpetual_research_entry import configured_perpetual_research_services
    from packages.kernel.decision_hub_kernel.application.perpetual_research_drivers import PerpetualResearchAdmissionDrivers
    from packages.kernel.decision_hub_kernel.application.risk_research_admission import RiskResearchAdmissionService
    perpetual_services = configured_perpetual_research_services(database)
    perpetual_schedule = PerpetualResearchAdmissionDrivers(
        database, perpetual_services.entry, now=perpetual_services.entry.now,
        channel=selected_settings.notification_channel,
    )
    periodic_admission = RiskResearchAdmissionService(database, enabled=True, perpetual_driver=perpetual_schedule)
    previous_research_tick = research_tick
    from packages.kernel.decision_hub_kernel.application.perpetual_forecast_runtime import (
        PerpetualForecastRuntime,
    )
    perpetual_forecasts = PerpetualForecastRuntime(
        database, perpetual_services.source, clock=perpetual_services.entry.now,
        collection_enabled=selected_settings.market_enabled,
        scoring_version="v2",
    )

    research_tick = build_research_admission_tick(
        perpetual_forecasts=perpetual_forecasts, periodic_admission=periodic_admission,
        previous_research_tick=previous_research_tick,
    )
    from packages.kernel.decision_hub_kernel.application.realtime_channel_diagnostics import (
        RealtimeChannelDiagnostics,
    )

    scheduler = RealtimeScheduler(
        ingestion,
        run_executor=execute_target,
        analytical_assessments=AnalyticalAssessmentService(
            database,
            archive=RawBodyArchive(data_dir / "raw-captures"),
        ),
        calibrations=KernelCalibrationProducer(
            database,
            archive=RawBodyArchive(data_dir / "raw-captures"),
        ),
        effect_tracking=StudyEffectTrackingService(
            database,
            archive=RawBodyArchive(data_dir / "raw-captures"),
        ),
        outcomes=(
            DueOutcomeService(database, OKXPublicMarketAdapter())
            if selected_settings.market_enabled
            else None
        ),
        analytical_observations=(
            AnalyticalObservationService(
                database,
                OKXPublicMarketAdapter(),
                capture_context=observation_capture_context(database, data_dir),
            )
            if selected_settings.market_enabled
            else None
        ),
        notifications=NotificationDispatcher(
            database,
            build_notification_adapters(selected_settings, data_dir),
        ),
        window_sampler=window_sampler,
        risk_data=risk_data,
        risk_monitor=risk_monitor,
        risk_research_tick=research_tick,
        risk_shadow_tick=shadow_tick,
        risk_memory_tick=memory_tick,
        risk_enhancement_tick=enhancement_tick,
        action_monitor_tick=action_tick,
        action_quote_tick=quote_tick,
        action_execution_tick=execution_tick,
        channel_observer=RealtimeChannelDiagnostics.from_environment(),
    )
    return RealtimeWorker(
        scheduler=scheduler,
        heartbeats=ServiceHeartbeatService(
            database,
            config_overrides={
                "sources_enabled": selected_settings.sources_enabled,
                "market_enabled": selected_settings.market_enabled,
                "risk_enabled": risk_enabled,
                "spot_research_enabled": spot_enabled,
                "action_product_enabled": action_enabled,
                "action_review_enabled": action_review_enabled,
                "event_window_capture": window_sampler is not None,
                "role_mode": "source_and_observation_scheduler",
                "legacy_analysis_enabled": bool(
                    getattr(ingestion.strategy_selector, "include_baseline", True)
                ),
            },
        ),
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
    harness_first_freeze: HarnessFirstFreezeService | None = None,
    harness_baseline_config: HarnessBaselineWorkerConfig | None = None,
    harness_installed_baseline: InstalledHarnessBaseline | None = None,
    harness_install_request: InstalledHarnessBootstrapRequest | None = None,
    heavy_work_admission: HeavyWorkAdmission | None = None,
) -> ResearchWorker:
    """Build the durable agentic research composition root.

    The DSH adapter is lazy about provider credentials, so an offline tick with
    no admitted research run remains safe and deterministic. Tests and replay
    tooling can inject another ``ResearchHarnessRuntime`` without changing the
    worker or Product Kernel.
    """
    spot_enabled = os.getenv("DECISION_HUB_SPOT_RESEARCH_ENABLED", "0") == "1"
    action_enabled = _action_product_enabled(spot_enabled=spot_enabled)
    selected_runtime = runtime or _research_runtime_from_env(database)
    selected_capabilities = allowed_capabilities or _research_capabilities_from_env()
    spot_capabilities = selected_capabilities if spot_enabled else None
    if spot_enabled:
        # This capability belongs to prepared Pack 1.2 only. Old Pack 1.1
        # remains separately validated and retains its original allowlist.
        selected_capabilities = tuple(
            item for item in selected_capabilities if item != "spot_market_facts"
        )
    service_id = os.getenv("DECISION_HUB_RESEARCH_SERVICE_ID", "hub-research-worker").strip()
    if not service_id:
        service_id = "hub-research-worker"
    raw_min_priority = os.getenv("DECISION_HUB_RESEARCH_MIN_PRIORITY", "").strip()
    try:
        min_priority = int(raw_min_priority) if raw_min_priority else None
    except ValueError as exc:
        raise ValueError("research_min_priority_invalid") from exc
    if min_priority is not None and not 0 <= min_priority <= 100:
        raise ValueError("research_min_priority_invalid")
    data_dir = Path(os.getenv("DECISION_HUB_DATA_DIR", "data/decision-hub"))
    root = Path(__file__).resolve().parents[2]
    configure_probability_archive(database, RawBodyArchive(data_dir / "raw-captures"))
    from packages.kernel.decision_hub_kernel.application.research_binding import (
        PreparedResearchPolicyRefs,
    )
    prepared_root = root / "packs" / "crypto_macro_prepared" / "1.1.0"
    prepared_refs = PreparedResearchPolicyRefs(
        gate_policy_ref="gates/prepared.yaml",
        rubric_policy_ref="evaluations/prepared.yaml",
        role_profile_refs=(
            "profiles/manager.yaml", "profiles/counter-thesis.yaml", "profiles/data-quality.yaml",
        ),
    )
    execution_mode = _research_execution_mode_from_env(selected_runtime)
    if harness_install_request is not None:
        if any(value is not None for value in (
            harness_first_freeze, harness_baseline_config, harness_installed_baseline
        )):
            raise ValueError("harness_worker_configuration_conflict")
        if not isinstance(selected_runtime, DshWebResearchRuntime):
            raise ValueError("harness_installed_web_runtime_required")
        installed = produce_installed_harness_baseline(
            harness_install_request, repository_root=root, data_root=data_dir,
            refs=prepared_refs, allowed_capabilities=selected_capabilities,
            execution_mode=execution_mode, runtime_id=selected_runtime.runtime_id,
        )
        harness_baseline_config = installed.config
        harness_installed_baseline = installed.baseline
    if harness_first_freeze is not None and (
        harness_baseline_config is not None or harness_installed_baseline is not None
    ):
        raise ValueError("harness_worker_configuration_conflict")
    selected_harness = harness_first_freeze or configured_baseline_first_freeze(
        database, config=harness_baseline_config,
        installed_baseline=harness_installed_baseline, data_root=data_dir,
        prepared_pack_root=prepared_root, refs=prepared_refs,
        allowed_capabilities=selected_capabilities, execution_mode=execution_mode,
        runtime_id=selected_runtime.runtime_id,
    )
    from apps.hub_api.frozen_harness_bootstrap import configured_frozen_run_harness
    configured_frozen = configured_frozen_run_harness(
        database,
        installed_baseline=selected_harness.deployment.baseline
        if selected_harness is not None else None,
    )
    if configured_frozen is not None and selected_harness is None:
        raise ValueError("harness_frozen_current_installed_baseline_required")
    case_context = None
    market_components = None
    action_integration = None
    opinion_context = None
    if spot_enabled:
        from apps.hub_worker.market_component_bootstrap import build_market_components

        market_components = build_market_components(database)
    from apps.hub_worker.research_skill_bootstrap import configured_research_skills
    from apps.hub_worker.research_learning_bootstrap import build_learning_execution
    learning_execution = build_learning_execution(database, data_dir)
    skills = configured_research_skills(
        database, data_dir, components=market_components, execution=learning_execution,
    )
    if skills is not None:
        if not isinstance(selected_runtime, DshWebResearchRuntime):
            raise ValueError("skill_native_web_runtime_required")
        selected_runtime.skill_preparation = skills.preparation
    if action_enabled:
        from packages.kernel.decision_hub_kernel.application.research_skill_publication import ResearchSkillPublicationReader
        from packages.kernel.decision_hub_kernel.application.action_publication import (
            ActionPublicationService,
        )
        from packages.kernel.decision_hub_kernel.application.action_research_integration import (
            ActionResearchIntegration,
        )
        from packages.kernel.decision_hub_kernel.application.public_opinion import (
            PublicOpinionResearchContext,
        )

        document_library = build_document_library_service(database)
        opinion_context = PublicOpinionResearchContext(document_library)
        action_integration = ActionResearchIntegration(
            database,
            publisher=ActionPublicationService(
                database, document_store=document_library.store,
                skill_reader=(
                    ResearchSkillPublicationReader(skills.consumption) if skills is not None else None
                ),
            ),
        )
    if spot_enabled and isinstance(selected_runtime, DshWebResearchRuntime):
        from apps.hub_worker.market_case_context import MarketCaseContextProvider

        case_context = MarketCaseContextProvider(
            database, RawBodyArchive(data_dir / "raw-captures"),
            prepared_pack_root=root / "packs" / "crypto_macro_prepared" / "1.2.0",
            refs=prepared_refs, allowed_capabilities=spot_capabilities or (),
            execution_mode=execution_mode, runtime_id=selected_runtime.runtime_id,
            profile_ref=selected_runtime.profile_ref,
            memory_enabled=os.getenv("DECISION_HUB_MARKET_CASE_MEMORY_ENABLED", "1") == "1",
        )
    from packages.runtime_adapters.perpetual_research_entry import configured_perpetual_research_services
    perpetual_services = configured_perpetual_research_services(database)
    worker = DurableResearchWorker(
        database,
        selected_runtime,
        pack_root=root / "packs" / "crypto_macro",
        prepared_pack_root=prepared_root,
        perpetual_binding_service=perpetual_services.binding,
        spot_prepared_pack_root=(
            root / "packs" / "crypto_macro_prepared" / "1.2.0"
            if os.getenv("DECISION_HUB_SPOT_RESEARCH_ENABLED", "0") == "1" else None
        ),
        prepared_policy_refs=prepared_refs,
        harness_first_freeze=selected_harness,
        frozen_run_harness=configured_frozen.service if configured_frozen is not None else None,
        checkpoint_path=data_dir / "checkpoints" / "research_graph.sqlite3",
        worker_id=_instance_id("research"),
        allowed_capabilities=selected_capabilities,
        spot_allowed_capabilities=spot_capabilities,
        execution_mode=execution_mode,
        decision_output_enabled=action_enabled,
        action_integration=action_integration,
        opinion_context=opinion_context,
        lease_seconds=lease_seconds,
        min_priority=min_priority,
        heavy_work_admission=heavy_work_admission if min_priority is None else None,
        context_inputs_provider=case_context,
        current_method_observer=case_context.observe if case_context is not None else None,
        component_consumer=market_components,
    )
    from apps.hub_worker.research_skill_callback_bootstrap import (
        configured_research_skill_callbacks,
    )
    worker.skill_callback_service = configured_research_skill_callbacks(
        database, skills.service if skills is not None else None,
    )
    if os.getenv("DECISION_HUB_SPOT_RESEARCH_ENABLED", "0") == "1":
        from apps.hub_worker.spot_research_bootstrap import InstalledSpotResearch
        from packages.kernel.decision_hub_kernel.application.review_schedule import (
            ReviewScheduleService,
        )
        from packages.kernel.decision_hub_kernel.application.run import RunService

        installed_spot = InstalledSpotResearch(
            database, data_dir, enabled=True,
            host_url=os.getenv("DECISION_HUB_DSH_WEB_URL", "http://127.0.0.1:3097")
            .rstrip("/") + "/decision-hub/v1/readiness",
        )
        worker.review_schedule = ReviewScheduleService(
            database, spot_admission_resolver=installed_spot.resolve_in_session,
        )
        worker.runs = RunService(
            database, spot_admission_resolver=installed_spot.resolve_in_session,
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
        service_id=service_id,
    )


def build_document_library_service(database: Database) -> ResearchLibraryService:
    """Build the document library boundary without creating a second queue."""
    data_dir = Path(os.getenv("DECISION_HUB_DATA_DIR", "data/decision-hub"))
    root = Path(os.getenv("DECISION_HUB_DOCUMENT_BUNDLE_ROOT", str(data_dir / "document-bundles")))
    store = DocumentBundleStore(root)
    parser = DocumentParserRouter(
        (
            MineruCliDocumentParser(store),
            PypdfTextDocumentParser(store),
            ImagePlaceholderDocumentParser(store),
        )
    )
    correction = FinancialSemanticCorrectionAdapter(store)
    if (
        os.getenv("DECISION_HUB_DOCUMENT_CORRECTION_LLM_ENABLED", "0") == "1"
        and os.getenv("DECISION_HUB_LLM_ENABLED", "0") == "1"
    ):
        from packages.provider_adapters.documents.correction import (
            FinancialSemanticCorrectionPayload,
        )
        from packages.runtime_adapters.langgraph_agent.runtime import LangGraphAgentRuntime

        correction = ModelFinancialSemanticCorrectionAdapter(
            store,
            LangGraphAgentRuntime(
                response_model=FinancialSemanticCorrectionPayload,
                system_prompt=(
                    "You are a financial document correction specialist. Return only the "
                    "strict correction schema. Preserve source meaning and numbers; never "
                    "fill missing data. Every mutation is a review proposal."
                ),
            ),
        )
    acquisition_root = os.getenv("DECISION_HUB_SOURCE_ACQUISITION_ROOT")
    library = ResearchLibraryService(
        database,
        store,
        parser,
        correction,
        HeuristicAnalystClaimExtractor(),
        acquisition_root=(
            Path(acquisition_root)
            if acquisition_root and acquisition_root.strip()
            else None
        ),
    )
    if os.getenv("DECISION_HUB_ACTION_PRODUCT_ENABLED", "0") == "1":
        from packages.kernel.decision_hub_kernel.application.public_opinion import (
            PublicOpinionImportService,
        )
        from packages.provider_adapters.research.source_registry import ResearchSourceRegistry
        from packages.provider_adapters.research.opinion_capture import OpinionCaptureRegistry

        library.public_opinion_importer = PublicOpinionImportService(
            library,
            ResearchSourceRegistry.from_pack(
                Path(__file__).resolve().parents[2] / "packs" / "crypto_macro"
            ),
            capture_registry=OpinionCaptureRegistry.from_pack(
                Path(__file__).resolve().parents[2] / "packs" / "crypto_macro"
            ),
        )
    return library


def build_document_worker(database: Database) -> DocumentPipelineWorker:
    """Build the document pipeline on the same durable database boundary."""
    service = build_document_library_service(database)
    return DocumentPipelineWorker(service, database)


def build_historical_worker(
    database: Database,
    *,
    lookback_days: int = 90,
) -> HistoricalRefreshWorker:
    """Build the minimal rolling hourly history trigger.

    The trigger owns only DataJobs and immutable history partitions.  It does
    not admit research, run a model, or start a second scheduling system.
    """

    data_dir = Path(os.getenv("DECISION_HUB_DATA_DIR", "data/decision-hub"))
    jobs = DataJobService(database)
    runner = HistoricalDataJobRunner(
        jobs,
        HistoricalDataArchive(data_dir / "historical"),
        {
            "crypto.btc_usdt.ohlcv_1h": OKXHistoricalDataLoader(),
            "crypto.btc_usdt.ohlcv_1d": GateDailyDataLoader(),
            "crypto.eth_usdt.ohlcv_1d": GateDailyDataLoader(),
        },
    )
    return HistoricalRefreshWorker(
        jobs, runner, lookback_days=lookback_days,
        daily_dataset_ids=("crypto.btc_usdt.ohlcv_1d", "crypto.eth_usdt.ohlcv_1d"),
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
    policy: EvolutionJobPolicy | None = None,
    heartbeat_interval_seconds: float = 10,
) -> EvolutionWorker:
    selected_runtime: AgentRuntime
    if runtime is None:
        default_runtime = LangGraphAgentRuntime()
        if not default_runtime.enabled:
            raise ValueError("evolution_live_provider_required_no_implicit_fake")
        selected_runtime = default_runtime
    else:
        selected_runtime = runtime or LangGraphAgentRuntime()
    planner_runtime = planning_runtime or (
        EvolutionPlanningRuntime()
        if getattr(selected_runtime, "enabled", False)
        else FakeEvolutionAgentRuntime()
    )
    data_dir = Path(os.getenv("DECISION_HUB_DATA_DIR", "data/decision-hub"))
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
        policy=policy or EvolutionJobPolicy(domain_pack_ref=DOMAIN_PACK),
    )
    artifact_store = CandidateArtifactStore(data_dir / "evolution")
    plans = FixtureEvaluationPlanFactory(
        default_evolution_fixtures(),
        baseline_runtime=selected_runtime,
        candidate_base_runtime=selected_runtime,
        baseline_version=baseline.version,
        assets=assets,
        artifacts=artifact_store,
    )
    from apps.hub_worker.research_learning_bootstrap import (
        build_learning_worker_services, research_learning_enabled,
    )
    from packages.kernel.decision_hub_kernel.persistence.db import utcnow
    learning = None
    if research_learning_enabled():
        from packages.runtime_adapters.langgraph_agent.provider_config import ProviderConfig
        learning = build_learning_worker_services(
            database, data_dir, jobs=jobs, legacy_scanner=scanner,
            provider_config=ProviderConfig.from_env(), clock=utcnow,
        )
    from packages.orchestration.langgraph.research_learning_executor import LearningEvolutionExecutor
    executor_type = LearningEvolutionExecutor if learning is not None else EvolutionJobExecutor
    learning_arguments = {"learning_handler": learning.handler} if learning is not None else {}
    executor = executor_type(
        **learning_arguments,
        jobs=jobs,
        scanner=learning.scanner if learning is not None else scanner,
        context=EvolutionContextService(database),
        planner=SupervisorEvolutionPlanner(
            planner_runtime,
            database=database,
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

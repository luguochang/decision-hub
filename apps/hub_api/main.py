from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import socket
from collections.abc import Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal, cast

from fastapi import (
    BackgroundTasks,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from apps.hub_api.asset_routes import asset_router
from apps.hub_api.document_upload_limit import DocumentUploadRequestLimitMiddleware
from apps.hub_api.document_uploads import read_document_upload
from apps.hub_api.evidence_original_routes import evidence_original_router
from apps.hub_api.fact_routes import fact_router
from apps.hub_api.outlook_routes import outlook_router
from apps.hub_api.request_timing import DatabaseRequestDiagnostics, SlowRequestLoggingMiddleware
from apps.hub_api.research_bootstrap import prewarm_research_metadata
from apps.hub_api.study_routes import study_router
from apps.hub_worker.composition import build_document_library_service
from packages.contracts_py.decision_hub_contracts.generated.dsh_host_bridge import (
    DshModelDispatchBinding,
)
from packages.contracts_py.decision_hub_contracts.models import (
    AnalystClaim,
    CandidateVersion,
    CapabilityManifest,
    DataJobCreate,
    DataJobView,
    DecisionGraph,
    DocumentBlock,
    DocumentCorrection,
    DocumentUpload,
    DshBusinessStatus,
    DshRunSessionLinkView,
    DshSessionAccepted,
    DshSessionCompletion,
    DshSessionPrompt,
    DshSessionStatus,
    EvaluationDatasetManifest,
    EvolutionJobView,
    EvolutionOverviewView,
    ExperienceCreate,
    ExperienceView,
    ExperimentManifest,
    ExperimentResultView,
    FeedbackCreate,
    FeedbackView,
    ForecastAuditView,
    MacroEventView,
    MacroEventVintageView,
    ObservationCreate,
    OperationsOverviewView,
    OutcomeCreate,
    PilotReadinessReport,
    PromotionDecisionCreate,
    PromotionDecisionResult,
    PromotionReviewRequest,
    PromotionReviewView,
    ResearchDocument,
    ResearchDocumentVersion,
    ResearchInboxView,
    ResearchMemoCreate,
    ResearchMemoView,
    ResearchObservabilityView,
    ResearchRequest,
    ResearchRunCommand,
    ResearchRunCommandReceipt,
    ResearchRunCommandResult,
    ResearchRunComparisonView,
    ResearchRunDetailView,
    ResearchRunQueued,
    ResearchRunScope,
    ResearchRunView,
    ResearchValueEvaluation,
    ReviewScheduleView,
    RunInspectorView,
    RunStatus,
    RunTimelineView,
    RuntimeModeView,
    SourceAcquisition,
    SourceType,
    StrategyCalibrationView,
    WorkbenchOverviewView,
)
from packages.kernel.decision_hub_kernel.application.admission import AdmissionService
from packages.kernel.decision_hub_kernel.application.calibration import (
    StrategyCalibrationService,
    configure_probability_archive,
)
from packages.kernel.decision_hub_kernel.application.data_jobs import DataJobService
from packages.kernel.decision_hub_kernel.application.dsh_activity import (
    DshHostActivityScopeView,
    DshHostActivitySnapshot,
    freeze_activity_snapshot_in_session,
    invalidate_activity_scope_in_session,
    register_verified_activity_scope_in_session,
)
from packages.kernel.decision_hub_kernel.application.dsh_sessions import DshSessionLinkService
from packages.kernel.decision_hub_kernel.application.evolution import EvolutionAssetService
from packages.kernel.decision_hub_kernel.application.forecast_audit import forecast_audit_for_run
from packages.kernel.decision_hub_kernel.application.health import HealthService
from packages.kernel.decision_hub_kernel.application.live_observation import (
    ServiceHeartbeatService,
)
from packages.kernel.decision_hub_kernel.application.macro_event import (
    get_macro_event,
    list_macro_event_families,
    list_macro_event_vintages,
    list_macro_events_page,
)
from packages.kernel.decision_hub_kernel.application.model_dispatch import ModelDispatchService
from packages.kernel.decision_hub_kernel.application.outcome import OutcomeService
from packages.kernel.decision_hub_kernel.application.research_intake import (
    normalize_latest_advice_request,
)
from packages.kernel.decision_hub_kernel.application.research_library import ResearchLibraryService
from packages.kernel.decision_hub_kernel.application.research_observability import (
    ResearchCommandService,
)
from packages.kernel.decision_hub_kernel.application.review_schedule import ReviewScheduleService
from packages.kernel.decision_hub_kernel.application.runtime_configuration import (
    RuntimeConfigurationView,
    list_runtime_configurations,
)
from packages.kernel.decision_hub_kernel.application.source_acquisition import (
    SourceAcquisitionIngestResult,
    SourceAcquisitionService,
)
from packages.kernel.decision_hub_kernel.application.source_ingest import (
    SourceIngestionService,
    SourcePollAttemptView,
)
from packages.kernel.decision_hub_kernel.application.workbench import WorkbenchAssetService
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.kernel.decision_hub_kernel.persistence.sqlite_busy import is_sqlite_initial_begin_busy
from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError
from packages.kernel.decision_hub_kernel.ports.sources import SourceConnector
from packages.orchestration.langgraph import build_analyze_text_service
from packages.pilot_runtime import build_readiness_service
from packages.provider_adapters.research.discovery import CryptoMacroDiscoveryPolicy
from packages.provider_adapters.research.raw_capture import RawBodyArchive
from packages.query_views.decision_desk.service import DecisionDeskQueryService
from packages.query_views.operations import OperationsQueryService
from packages.query_views.research import ResearchQueryService
from packages.query_views.research.recent import recent_research_runs
from packages.query_views.research.service import ResearchHomeView
from packages.runtime_adapters.development_runtime import development_runtime_from_environment
from packages.runtime_adapters.dsh_runtime.activity_transport import (
    DshActivityTransportConfig,
    validate_activity_scope,
)
from packages.runtime_adapters.dsh_runtime.web_host_client import (
    DshActivityHostClient,
    DshWebHostClient,
)
from packages.runtime_adapters.langgraph_agent.runtime import LangGraphAgentRuntime
from packages.source_adapters.official_feeds import official_source_presets
from packages.source_adapters.registry import SourceRegistry
from packages.workbench_adapters.source_capture import source_capture_context


def _configure_retained_recipe_registry_from_normal_launch() -> None:
    """Consume the normal launcher's guarded release identity before DB startup.

    This process-local input grants historical reads only. It is independent
    of optional method comparison configuration and never changes a Run.
    """
    receipt_name = os.getenv("DECISION_HUB_RETAINED_RECIPE_RECEIPT", "")
    receipt_sha256 = os.getenv("DECISION_HUB_RETAINED_RECIPE_RECEIPT_SHA256", "")
    if not receipt_name and not receipt_sha256:
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
    if "retained_recipe_archives_v1" not in receipt["evidence"]:
        return
    configure_retained_recipe_registry_from_validated_release(
        receipt_path=receipt_path, receipt_sha256=receipt_sha256,
    )
    if os.getenv("DECISION_HUB_SPOT_RESEARCH_ENABLED", "0") == "1":
        from packages.kernel.decision_hub_kernel.application.research_graph_recipe_v3 import (
            configure_graph_recipe_v3_from_validated_release,
        )
        configure_graph_recipe_v3_from_validated_release(
            receipt_path=receipt_path, receipt_sha256=receipt_sha256,
        )


def create_app(
    database: Database | None = None,
    *,
    source_connectors: Sequence[SourceConnector] | None = None,
    sources_enabled: bool | None = None,
    dsh_callback_secret: str | None = None,
    frontend_dir: Path | None = None,
    dsh_activity_config: DshActivityTransportConfig | None = None,
    dsh_activity_host_client: DshActivityHostClient | None = None,
    probability_archive: RawBodyArchive | None = None,
) -> FastAPI:
    development = development_runtime_from_environment(Path(__file__).resolve().parents[2])
    _configure_retained_recipe_registry_from_normal_launch()
    db = database or Database()
    if database is None:
        db.initialize()
    else:
        db.create_all()
    historical_reader = None
    if development is not None:
        from packages.kernel.decision_hub_kernel.application.development_history_reader import (
            prepare_development_history_reader,
        )

        historical_reader = prepare_development_history_reader(
            Path(__file__).resolve().parents[2], development, db,
        )
    from packages.kernel.decision_hub_kernel.application.research_publication_runtime48 import (
        create_managed_publication_bundle_service,
    )

    publication_data_dir = os.getenv("DECISION_HUB_PUBLICATION_DATA_DIR")
    if development is not None:
        publication_service = None
    elif publication_data_dir is None:
        publication_service = create_managed_publication_bundle_service(
            database=db, repository_root=Path(__file__).resolve().parents[2],
        )
    else:
        publication_service = create_managed_publication_bundle_service(
            database=db, repository_root=Path(__file__).resolve().parents[2],
            publication_data_root=Path(publication_data_dir),
        )
    def confirmed_report_snapshot_in_session(session: Session, run_id: str):
        if publication_service is None:
            raise ValueError("development_formal_publication_unavailable")
        # Same caller snapshot; this read scope never verifies external material.
        snapshot = publication_service.lifecycle._confirmed_report_snapshot(session, run_id)
        if publication_service.lifecycle.archive.fenced(run_id):
            raise ValueError("report_snapshot_commit_unconfirmed")
        return snapshot

    effect_archive = probability_archive
    if probability_archive is not None:
        configure_probability_archive(db, probability_archive)
    elif database is None:
        probability_data_dir = Path(os.getenv("DECISION_HUB_DATA_DIR", "data/decision-hub"))
        effect_archive = RawBodyArchive(probability_data_dir / "raw-captures")
        configure_probability_archive(db, effect_archive)
    runtime = LangGraphAgentRuntime()
    checkpoint_path = None
    if database is None:
        data_dir = Path(os.getenv("DECISION_HUB_DATA_DIR", "data/decision-hub"))
        checkpoint_path = Path(
            os.getenv(
                "DECISION_HUB_CHECKPOINT_PATH",
                str(data_dir / "checkpoints" / "decision_graph.sqlite3"),
            )
        )
    analyzer = build_analyze_text_service(db, runtime, checkpoint_path=checkpoint_path)
    desk = DecisionDeskQueryService(db)
    health = HealthService(db)
    outcomes = OutcomeService(db)
    workbench = WorkbenchAssetService(db)
    evolution = EvolutionAssetService(db)
    calibrations = StrategyCalibrationService(db)
    review_schedules = ReviewScheduleService(
        db, confirmed_report_reader=confirmed_report_snapshot_in_session,
    )
    heartbeats = ServiceHeartbeatService(db)
    source_registry = SourceRegistry()
    default_calendar_enabled = os.getenv(
        "DECISION_HUB_CALENDAR_DISCOVERY_ENABLED", "0"
    ).strip().lower() in {"1", "true", "yes", "on"}
    for source in (
        list(source_connectors)
        if source_connectors is not None
        else official_source_presets(include_calendar=default_calendar_enabled)
    ):
        source_registry.register(source)
    source_ingestion = SourceIngestionService(
        db, source_registry, admission=AdmissionService(db),
        poll_context=source_capture_context(
            db, Path(os.getenv("DECISION_HUB_DATA_DIR", "data/decision-hub"))
        ),
        strategy_selector=CryptoMacroDiscoveryPolicy.from_pack(
            Path(__file__).resolve().parents[2] / "packs" / "crypto_macro"
        ),
    )
    acquisition_root_value = os.getenv("DECISION_HUB_SOURCE_ACQUISITION_ROOT", "").strip()
    opinion_captures = None
    if os.getenv("DECISION_HUB_ACTION_PRODUCT_ENABLED", "0") == "1":
        from packages.provider_adapters.research.opinion_capture import OpinionCaptureRegistry

        opinion_captures = OpinionCaptureRegistry.from_pack(
            Path(__file__).resolve().parents[2] / "packs" / "crypto_macro"
        )
    source_acquisitions = SourceAcquisitionService(
        db,
        source_registry,
        artifact_root=Path(acquisition_root_value) if acquisition_root_value else None,
        opinion_captures=opinion_captures,
        receipt_only_sources={"fred.current_macro": tuple(
            f"https://fred.stlouisfed.org/series/{series}"
            for series in ("WRESBAL", "DFII10", "T10YIE")
        )},
    )
    heartbeat_interval = float(os.getenv("DECISION_HUB_HEARTBEAT_INTERVAL", "10"))
    if heartbeat_interval <= 0:
        raise ValueError("heartbeat_interval_invalid")
    api_started_at = datetime.now(UTC)
    api_instance_id = os.getenv("DECISION_HUB_INSTANCE_ID") or (
        f"{socket.gethostname()}:{os.getpid()}:api"
    )
    research_runtime = os.getenv("DECISION_HUB_OPERATIONS_RESEARCH_RUNTIME", "").strip().lower()
    runtime_mode = "provider" if runtime.enabled or research_runtime == "dsh-web" else "fake"
    canary_status = os.getenv("DECISION_HUB_LLM_CANARY_STATUS", "not_run")
    if canary_status not in {"not_run", "passed", "failed", "unknown"}:
        raise ValueError("llm_canary_status_invalid")
    runtime_view = RuntimeModeView(
        runtime_id=runtime.effective_runtime_id,
        runtime_version=runtime.effective_runtime_version,
        mode=runtime_mode,
        provider_configured=runtime.enabled,
        live_canary_status=cast(Literal["not_run", "passed", "failed", "unknown"], canary_status),
        model=runtime.provider_config.model if runtime.enabled else None,
        api_mode=runtime.api_mode if runtime.enabled else None,
    )
    if research_runtime == "dsh-web":
        # The native analyzer above is intentionally separate from the live
        # DSH research worker. Operations must describe the runtime that
        # actually handles research Runs, rather than showing its unused
        # native fallback as ``fake``.
        runtime_view = RuntimeModeView(
            runtime_id="dsh-web",
            runtime_version=os.getenv("DECISION_HUB_DSH_SOURCE_VERSION", "").strip()
            or "dsh-web-live",
            mode="provider",
            provider_configured=True,
            live_canary_status=cast(
                Literal["not_run", "passed", "failed", "unknown"], canary_status
            ),
            model=os.getenv("DECISION_HUB_OPERATIONS_MODEL", "gpt-6.1-sol"),
            api_mode=os.getenv("DECISION_HUB_LLM_API_MODE", "responses"),
        )
    effective_sources_enabled = (
        sources_enabled
        if sources_enabled is not None
        else os.getenv("DECISION_HUB_SOURCES_ENABLED", "0") == "1"
    )
    heartbeats.config_overrides["sources_enabled"] = effective_sources_enabled
    operations = OperationsQueryService(
        db,
        runtime=runtime_view,
        source_manifests=source_registry.manifests(),
        source_execution_enabled=effective_sources_enabled,
    )
    pack_root = Path(__file__).resolve().parents[2] / "packs" / "crypto_macro"
    research_query = ResearchQueryService(db, pack_root=pack_root)
    from apps.hub_api.frozen_harness_bootstrap import configured_frozen_run_harness
    frozen_review_bootstrap = configured_frozen_run_harness(db)
    spot_enabled = os.getenv("DECISION_HUB_SPOT_RESEARCH_ENABLED", "0") == "1"
    installed_spot = None
    if spot_enabled:
        from apps.hub_worker.spot_research_bootstrap import InstalledSpotResearch

        installed_spot = InstalledSpotResearch(
            db, Path(os.getenv("DECISION_HUB_DATA_DIR", "data/decision-hub")),
            enabled=True,
            host_url=os.getenv("DECISION_HUB_DSH_WEB_URL", "http://127.0.0.1:3097")
            .rstrip("/") + "/decision-hub/v1/readiness",
        )
        review_schedules = ReviewScheduleService(
            db, spot_admission_resolver=installed_spot.resolve_in_session,
            confirmed_report_reader=confirmed_report_snapshot_in_session,
        )
    research_commands = ResearchCommandService(
        db, spot_admission_resolver=installed_spot.resolve_in_session if installed_spot else None,
    )
    research_library: ResearchLibraryService = build_document_library_service(db)
    dsh_sessions = DshSessionLinkService(db)
    model_dispatch = ModelDispatchService(db)
    data_jobs = DataJobService(db)
    effective_dsh_callback_secret = (
        dsh_callback_secret
        if dsh_callback_secret is not None
        else os.getenv("DECISION_HUB_DSH_CALLBACK_SECRET")
    )

    def beat_api(error_code: str | None = None) -> None:
        heartbeats.beat(
            service_id="hub-api",
            role="api",
            instance_id=api_instance_id,
            version="0.1.0",
            mode=runtime_mode,
            interval_seconds=heartbeat_interval,
            started_at=api_started_at,
            last_error_code=error_code,
        )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if research_runtime == "dsh-web":
            await asyncio.to_thread(prewarm_research_metadata)

        async def heartbeat_loop() -> None:
            while True:
                try:
                    await asyncio.to_thread(beat_api)
                except Exception:
                    # Readiness remains authoritative when the durable heartbeat write fails.
                    pass
                await asyncio.sleep(heartbeat_interval)

        beat_api()
        task = asyncio.create_task(heartbeat_loop())
        try:
            yield
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            if publication_service is not None:
                publication_service.close()

    app = FastAPI(title="Decision Hub API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(DocumentUploadRequestLimitMiddleware)
    app.add_middleware(SlowRequestLoggingMiddleware, database=DatabaseRequestDiagnostics(db.engine))
    app.include_router(study_router(db))
    app.include_router(evidence_original_router(
        db, Path(os.getenv("DECISION_HUB_DATA_DIR", "data/decision-hub")) / "documents"
    ))
    app.include_router(outlook_router(
        db, Path(os.getenv("DECISION_HUB_DATA_DIR", "data/decision-hub"))
    ))
    app.include_router(fact_router(
        db, Path(os.getenv("DECISION_HUB_DATA_DIR", "data/decision-hub"))
    ))
    app.include_router(asset_router(
        db, Path(os.getenv("DECISION_HUB_DATA_DIR", "data/decision-hub"))
    ))
    app.state.database = db
    app.state.analyzer = analyzer
    app.state.source_registry = source_registry
    app.state.source_ingestion = source_ingestion
    app.state.source_acquisitions = source_acquisitions
    app.state.sources_enabled = effective_sources_enabled
    app.state.readiness_service = build_readiness_service(
        db,
        source_registry.manifests(),
    )
    app.state.workbench = workbench
    app.state.evolution = evolution
    app.state.heartbeats = heartbeats
    app.state.operations = operations
    app.state.research_query = research_query
    app.state.research_commands = research_commands
    app.state.dsh_sessions = dsh_sessions
    app.state.data_jobs = data_jobs
    app.state.research_library = research_library

    def require_owner(owner_id: str | None, declared_owner: str | None = None) -> str:
        if not owner_id:
            raise HTTPException(status_code=403, detail="owner_identity_required")
        if declared_owner is not None and owner_id != declared_owner:
            raise HTTPException(status_code=403, detail="owner_identity_mismatch")
        return owner_id

    from apps.hub_api.delivery_routes import delivery_router

    app.include_router(delivery_router(db, require_owner))
    from apps.hub_api.action_routes import action_router

    action_flag = os.getenv("DECISION_HUB_ACTION_PRODUCT_ENABLED", "0")
    if action_flag not in {"0", "1"}:
        raise ValueError("action_product_flag_invalid")
    if action_flag == "1" and not spot_enabled:
        raise ValueError("action_product_spot_research_required")
    app.include_router(action_router(db, require_owner, enabled=action_flag == "1"))
    from apps.hub_api.action_paper_routes import action_paper_router
    from packages.kernel.decision_hub_kernel.application.action_execution import ActionExecutionService
    from packages.kernel.decision_hub_kernel.application.action_paper_product import ActionPaperProductService
    from packages.kernel.decision_hub_kernel.application.market_path_store import MarketPathStore

    paper_flag = os.getenv("DECISION_HUB_ACTION_EXECUTION_ENABLED", "0")
    if paper_flag not in {"0", "1"}:
        raise ValueError("action_execution_flag_invalid")
    if paper_flag == "1" and action_flag != "1":
        raise ValueError("action_execution_product_required")
    paper_data = Path(os.getenv("DECISION_HUB_DATA_DIR", "data/decision-hub"))
    paper_execution = ActionExecutionService(
        db, market_store=MarketPathStore(db, RawBodyArchive(paper_data / "raw-captures")),
        feature_root=paper_data / "action-product" / "features", enabled=paper_flag == "1",
    )
    app.include_router(action_paper_router(ActionPaperProductService(paper_execution), require_owner))
    from apps.hub_api.risk_routes import risk_router

    app.include_router(risk_router(
        db, Path(os.getenv("DECISION_HUB_DATA_DIR", "data/decision-hub")), require_owner,
        enabled=os.getenv("DECISION_HUB_RISK_ENABLED", "0").strip().lower()
        in {"1", "true", "yes", "on"},
    ))
    from apps.hub_api.risk_research_routes import risk_research_router
    from packages.kernel.decision_hub_kernel.application.risk_research_admission import (
        RiskResearchAdmissionService,
    )

    app.include_router(risk_research_router(RiskResearchAdmissionService(
        db, enabled=spot_enabled,
        prepare_scope=installed_spot.prepare if installed_spot else None,
        spot_admission_resolver=installed_spot.resolve_in_session if installed_spot else None,
    ), require_owner))
    from apps.hub_api.history_product_bootstrap39 import configured_history_product39
    from apps.hub_api.history_product_bootstrap41 import configured_history_product41
    from apps.hub_api.history_product_routes import history_product_router
    from apps.hub_api.history_product_routes41 import history_product_router41
    from apps.hub_api.history_hourly_routes import history_hourly_status_router
    from apps.hub_api.history_forecast_index_routes import history_forecast_index_router
    from apps.hub_api.history_outcome_bootstrap41 import configured_history_outcome41
    from apps.hub_api.history_outcome_routes import history_outcome_router
    from apps.hub_api.history_outcome_index_routes import history_outcome_index_router

    history_bootstrap41 = configured_history_product41(db)
    if history_bootstrap41 is None:
        history_bootstrap = configured_history_product39(db)
        history_outcome_bootstrap = None
        app.include_router(history_product_router(history_bootstrap))
    else:
        history_bootstrap = history_bootstrap41.legacy
        history_outcome_bootstrap = configured_history_outcome41(db, history41=history_bootstrap41)
        app.include_router(history_product_router41(history_bootstrap41))
    if history_outcome_bootstrap is None and os.getenv("DECISION_HUB_HISTORY_OUTCOME_PRIVATE_CONFIG"):
        raise ValueError("history_outcome41_explicit_qualification_required")
    app.include_router(history_hourly_status_router(history_bootstrap))
    app.include_router(history_forecast_index_router(history_bootstrap))
    app.include_router(history_outcome_router(history_outcome_bootstrap))
    app.include_router(history_outcome_index_router(history_outcome_bootstrap))
    from apps.hub_api.risk_paths import risk_paths_router
    from packages.kernel.decision_hub_kernel.application.market_path_store import MarketPathStore

    app.include_router(risk_paths_router(db, MarketPathStore(
        db, RawBodyArchive(Path(os.getenv("DECISION_HUB_DATA_DIR", "data/decision-hub"))
                           / "raw-captures"),
    )))
    from apps.hub_api.market_memory_routes import market_memory_router

    app.include_router(market_memory_router(
        db, Path(os.getenv("DECISION_HUB_DATA_DIR", "data/decision-hub")),
    ))
    from apps.hub_api.research_quality_routes import research_quality_router

    if publication_service is not None:
        app.include_router(research_quality_router(
            db, publication_lifecycle=publication_service.lifecycle,
        ))
    from apps.hub_api.market_enhancement_routes import market_enhancement_router

    app.include_router(market_enhancement_router(db))
    from apps.hub_api.event_prevision_routes import event_prevision_router

    app.include_router(event_prevision_router(db, archive=effect_archive))
    from apps.hub_api.event_lifecycle_routes import event_lifecycle_router
    app.include_router(event_lifecycle_router(db, require_owner=require_owner, archive=effect_archive,
        feature_root=Path(os.getenv('DECISION_HUB_DATA_DIR', 'data/decision-hub')) / 'action-product' / 'features'))
    from apps.hub_api.forecast_alarm_routes import forecast_alarm_router

    app.include_router(forecast_alarm_router(
        db, MarketPathStore(db, RawBodyArchive(
            Path(os.getenv("DECISION_HUB_DATA_DIR", "data/decision-hub")) / "raw-captures",
        )), require_owner=require_owner,
    ))
    from apps.hub_api.study_effect_tracking_routes import study_effect_tracking_router
    app.include_router(study_effect_tracking_router(db, require_owner, effect_archive))
    from apps.hub_api.harness_comparison_routes import harness_comparison_router
    app.include_router(harness_comparison_router(db))
    from apps.hub_api.decision_settings_routes import (
        configured_settings_authority,
        decision_settings_router,
    )
    app.include_router(decision_settings_router(
        db, authority=configured_settings_authority(), historical_reader=historical_reader,
    ))
    from apps.hub_api.research_library_routes import research_library_router
    app.include_router(research_library_router(research_library, require_owner))

    def require_dsh_callback_secret(provided: str | None) -> None:
        if not effective_dsh_callback_secret:
            raise HTTPException(status_code=503, detail="dsh_callback_not_configured")
        if not provided or not hmac.compare_digest(provided, effective_dsh_callback_secret):
            raise HTTPException(status_code=401, detail="dsh_callback_unauthorized")

    from apps.hub_api.terminal_quiescence import terminal_quiescence_router
    app.include_router(terminal_quiescence_router(db, require_dsh_callback_secret))
    from apps.hub_api.provider_input_routes import provider_input_router
    app.include_router(provider_input_router(db, require_dsh_callback_secret))
    from apps.hub_api.native_conversation_census_routes import native_conversation_census_router
    app.include_router(native_conversation_census_router(db, require_dsh_callback_secret))
    from apps.hub_api.perpetual_research_routes import perpetual_research_router
    app.include_router(perpetual_research_router(db, require_dsh_callback_secret))

    async def read_current_activity_scope() -> tuple[
        DshActivityTransportConfig, DshHostActivityScopeView,
    ]:
        created_host: DshWebHostClient | None = None
        try:
            config = dsh_activity_config or DshActivityTransportConfig.from_env()
            host = dsh_activity_host_client
            if host is None:
                created_host = DshWebHostClient(config.host)
                host = created_host
            # Authenticate the current independently configured channel before
            # entering a DB writer. A callback body cannot register itself as current.
            async with asyncio.timeout(3):
                current = validate_activity_scope(
                    await host.activity_scope(), config, now=datetime.now(UTC),
                )
            return config, current
        except asyncio.CancelledError:
            raise
        except HTTPException:
            raise
        except Exception as exc:
            def invalidate_current() -> None:
                with db.session() as session:
                    session.connection().exec_driver_sql("BEGIN IMMEDIATE")
                    invalidate_activity_scope_in_session(
                        session, recorded_at=datetime.now(UTC),
                        reason_code="dsh_activity_scope_unavailable",
                    )
            await run_in_threadpool(invalidate_current)
            raise HTTPException(status_code=503, detail="dsh_activity_scope_unavailable") from exc
        finally:
            if created_host is not None:
                await created_host.close()

    async def register_activity_scope(
        config: DshActivityTransportConfig, current: DshHostActivityScopeView,
    ) -> None:
        def register_current() -> None:
            with db.session() as session:
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
                register_verified_activity_scope_in_session(
                    session, expected_scope=current.scope,
                    transport_fingerprint=config.fingerprint(current.scope),
                    recorded_at=datetime.now(UTC),
                )
        try:
            await run_in_threadpool(register_current)
        except ValueError as exc:
            def invalidate_conflicting_scope() -> None:
                with db.session() as session:
                    session.connection().exec_driver_sql("BEGIN IMMEDIATE")
                    invalidate_activity_scope_in_session(
                        session, recorded_at=datetime.now(UTC),
                        reason_code="dsh_activity_scope_conflict",
                    )
            await run_in_threadpool(invalidate_conflicting_scope)
            raise HTTPException(status_code=409, detail="dsh_activity_scope_conflict") from exc

    @app.post("/v1/dsh/activity-scope-probe")
    async def dsh_activity_scope_probe(
        bridge_key: str | None = Header(default=None, alias="X-Decision-Hub-Bridge-Key"),
    ) -> dict[str, bool]:
        require_dsh_callback_secret(bridge_key)
        config, current = await read_current_activity_scope()
        await register_activity_scope(config, current)
        return {"scope_verified": True}

    @app.post("/v1/dsh/activity-snapshots")
    async def dsh_activity_snapshot(
        payload: DshHostActivitySnapshot,
        request: Request,
        bridge_key: str | None = Header(default=None, alias="X-Decision-Hub-Bridge-Key"),
    ) -> dict[str, int]:
        require_dsh_callback_secret(bridge_key)
        config, current = await read_current_activity_scope()
        # Register the genuine GET first: a newly observed boot must invalidate
        # old proof even when the caller's snapshot later fails its Link checks.
        await register_activity_scope(config, current)
        if current.scope != payload.scope:
            raise HTTPException(status_code=409, detail="dsh_activity_epoch_changed")
        fingerprint = config.fingerprint(current.scope)
        raw_snapshot = (await request.body()).decode("utf-8")

        def freeze() -> int:
            with db.session() as session:
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
                return freeze_activity_snapshot_in_session(
                    session, payload, expected_scope=current.scope,
                    transport_fingerprint=fingerprint, recorded_at=datetime.now(UTC),
                    raw_snapshot_json=raw_snapshot,
                )

        try:
            count = await run_in_threadpool(freeze)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"recorded_count": count}

    async def execute(event_id: str, run_id: str) -> None:
        try:
            await analyzer.run_admitted(event_id, run_id)
        except AgentExecutionError as exc:
            analyzer.runs.set_status(run_id, RunStatus.failed, error_code=exc.error_code)
            raise
        except Exception:
            analyzer.runs.set_status(run_id, RunStatus.failed, error_code="run_execution_failed")
            raise

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    if development is not None:
        @app.get("/v1/development/status")
        def development_status() -> dict[str, str | bool]:
            return {
                "mode": "development", "formal_publication_enabled": False,
                "financial_qualified": False, "execution_authorized": False,
            }

    # Synchronous database/file services run in FastAPI's bounded thread pool.
    # Calling them directly from async endpoints stalls unrelated requests.
    @app.get("/health/ready")
    def ready() -> dict[str, str]:
        try:
            health.status()
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {"status": "ready"}

    @app.get("/v1/health")
    def product_health():
        return health.status()

    @app.get("/v1/pilot/readiness", response_model=PilotReadinessReport)
    def pilot_readiness() -> PilotReadinessReport:
        # Recompute database state on every request while keeping checks in pilot_runtime.
        return app.state.readiness_service.report()

    @app.get("/v1/sources")
    def sources() -> list[dict[str, object]]:
        return [
            {
                "manifest": manifest.model_dump(mode="json"),
                "health": source_ingestion.health(manifest.source_id).model_dump(mode="json"),
            }
            for manifest in source_registry.manifests()
        ]

    @app.get(
        "/v1/sources/{source_id}/poll-attempts",
        response_model=list[SourcePollAttemptView],
    )
    def source_poll_attempts(
        source_id: str,
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> list[SourcePollAttemptView]:
        try:
            return source_ingestion.list_attempts(source_id, limit=limit, offset=offset)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="source_not_found") from exc

    @app.post("/v1/sources/{source_id}/poll")
    async def poll_source(source_id: str, background_tasks: BackgroundTasks,
                          mode: Literal["latest", "catchup"] = "latest",
                          lookback_hours: int = Query(168, ge=1, le=720)) -> dict[str, object]:
        if not app.state.sources_enabled:
            raise HTTPException(status_code=409, detail="sources_disabled")
        try:
            result = await source_ingestion.poll_once(source_id,
                trigger_type="catchup" if mode == "catchup" else "manual",
                lookback_hours=lookback_hours)
            for target in result.run_targets:
                background_tasks.add_task(execute, target.event_id, target.run_id)
            return result.model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="source_not_found") from exc

    @app.post(
        "/v1/source-acquisitions",
        status_code=status.HTTP_202_ACCEPTED,
        response_model=SourceAcquisitionIngestResult,
    )
    def ingest_source_acquisition(
        payload: SourceAcquisition,
    ) -> SourceAcquisitionIngestResult:
        """Admit an acquisition receipt; raw artifacts remain outside the Hub DB."""

        try:
            return source_acquisitions.ingest(payload)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="source_not_found") from exc
        except ValueError as exc:
            detail = str(exc)
            status_code = 409 if detail == "acquisition_receipt_conflict" else 400
            raise HTTPException(status_code=status_code, detail=detail) from exc

    @app.get("/v1/source-acquisitions", response_model=list[SourceAcquisition])
    def list_source_acquisitions(
        limit: int = Query(default=100, ge=1, le=500),
    ) -> list[SourceAcquisition]:
        return source_acquisitions.list(limit=limit)

    @app.post("/v1/observations", status_code=status.HTTP_202_ACCEPTED)
    def observations(
        payload: ObservationCreate,
        background_tasks: BackgroundTasks,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, str]:
        if not idempotency_key:
            raise HTTPException(status_code=400, detail="Idempotency-Key header is required")
        from packages.kernel.decision_hub_kernel.application.observation_submission import (
            submit_observation,
        )

        try:
            receipt, should_execute = submit_observation(analyzer, payload, idempotency_key)
        except ValueError as exc:
            code = str(exc)
            raise HTTPException(
                status_code=409 if code == "observation_idempotency_conflict" else 400,
                detail=code,
            ) from exc
        if should_execute:
            background_tasks.add_task(execute, receipt["event_id"], receipt["run_id"])
        return receipt

    @app.post(
        "/v1/research/observations",
        status_code=status.HTTP_202_ACCEPTED,
        response_model=ResearchRunQueued,
    )
    def research_observations(
        payload: ObservationCreate,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        x_owner_id: str | None = Header(None, max_length=128),
    ) -> ResearchRunQueued:
        """Queue an agentic research candidate without running it in the API process."""
        if not idempotency_key:
            raise HTTPException(status_code=400, detail="Idempotency-Key header is required")
        from packages.kernel.decision_hub_kernel.application.research_submission import (
            INTENT_CONFLICT,
            LEGACY_BINDING_UNAVAILABLE,
            RECEIPT_CORRUPT,
            submit_research_observation,
        )

        try:
            owner = require_owner(x_owner_id) if x_owner_id is not None else None
            return submit_research_observation(
                analyzer, payload, idempotency_key, owner_id=owner
            )
        except ValueError as exc:
            code = str(exc)
            raise HTTPException(
                status_code=409 if code in {
                    INTENT_CONFLICT, LEGACY_BINDING_UNAVAILABLE, RECEIPT_CORRUPT,
                } else 400,
                detail=code,
            ) from exc

    @app.post("/v1/research-library/documents", status_code=status.HTTP_202_ACCEPTED)
    async def upload_research_document(
        background_tasks: BackgroundTasks,
        file: Annotated[UploadFile, File()],
        metadata: str = Form(...),
        x_owner_id: str | None = Header(None, max_length=128),
    ) -> ResearchDocumentVersion:
        """Store an immutable source and enqueue its first parse step."""
        owner = require_owner(x_owner_id)
        try:
            payload = DocumentUpload.model_validate_json(metadata)
            data = await read_document_upload(file)
            receipt, created = await run_in_threadpool(
                research_library.accept_legacy_upload, payload,
                filename=file.filename or "upload.bin", data=data, owner=owner,
            )
            if created:
                background_tasks.add_task(
                    research_library.process_next, worker_id="api-document-upload",
                )
            return receipt.version
        except ValueError as exc:
            error_status = 413 if str(exc) == "document_file_too_large" else 400
            raise HTTPException(status_code=error_status, detail=str(exc)) from exc

    @app.post("/v1/research-library/web-import", status_code=status.HTTP_202_ACCEPTED)
    def import_research_acquisition(
        background_tasks: BackgroundTasks,
        receipt_id: str = Form(...),
        metadata: str = Form(...),
        x_owner_id: str | None = Header(None, max_length=128),
    ) -> ResearchDocumentVersion:
        """Import an existing Playwright/Crawlee receipt, never fetch a URL here."""
        require_owner(x_owner_id)
        try:
            payload = DocumentUpload.model_validate_json(metadata)
            view = research_library.import_acquisition(receipt_id, payload)
            background_tasks.add_task(
                research_library.process_next, worker_id="api-web-import"
            )
            return view
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/v1/research-library/documents", response_model=list[ResearchDocument])
    def list_research_documents(
        limit: int = Query(default=100, ge=1, le=500),
    ) -> list[ResearchDocument]:
        return research_library.list_documents(limit=limit)

    @app.get("/v1/research-library/documents/{document_id}", response_model=ResearchDocument)
    def get_research_document(document_id: str) -> ResearchDocument:
        view = research_library.get_document(document_id)
        if view is None:
            raise HTTPException(status_code=404, detail="research_document_not_found")
        return view

    @app.get("/v1/research-library/versions/{version_id}", response_model=ResearchDocumentVersion)
    def get_research_document_version(version_id: str) -> ResearchDocumentVersion:
        view = research_library.get_version(version_id)
        if view is None:
            raise HTTPException(status_code=404, detail="research_document_version_not_found")
        return view

    @app.get("/v1/research-library/versions/{version_id}/original")
    def get_research_document_original(version_id: str):
        path = research_library.original_path(version_id)
        if path is None:
            raise HTTPException(status_code=404, detail="research_document_version_not_found")
        import mimetypes

        return FileResponse(path, media_type=mimetypes.guess_type(path.name)[0])

    @app.get(
        "/v1/research-library/versions/{version_id}/blocks",
        response_model=list[DocumentBlock],
    )
    def get_research_document_blocks(version_id: str) -> list[DocumentBlock]:
        if research_library.get_version(version_id) is None:
            raise HTTPException(status_code=404, detail="research_document_version_not_found")
        return research_library.get_blocks(version_id)

    @app.get(
        "/v1/research-library/versions/{version_id}/correction",
        response_model=DocumentCorrection | None,
    )
    def get_research_document_correction(version_id: str) -> DocumentCorrection | None:
        if research_library.get_version(version_id) is None:
            raise HTTPException(status_code=404, detail="research_document_version_not_found")
        return research_library.get_correction(version_id)

    @app.post(
        "/v1/research-library/versions/{version_id}/correction",
        status_code=status.HTTP_202_ACCEPTED,
        response_model=ResearchDocumentVersion,
    )
    def request_research_document_correction(
        version_id: str,
        background_tasks: BackgroundTasks,
        x_owner_id: str | None = Header(None, max_length=128),
    ) -> ResearchDocumentVersion:
        require_owner(x_owner_id)
        try:
            view = research_library.request_correction(version_id)
            background_tasks.add_task(
                research_library.process_next, worker_id="api-document-correction"
            )
            return view
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post(
        "/v1/research-library/versions/{version_id}/correction/accept",
        response_model=ResearchDocumentVersion,
    )
    async def accept_research_document_correction(
        version_id: str,
        x_owner_id: str | None = Header(None, max_length=128),
    ) -> ResearchDocumentVersion:
        require_owner(x_owner_id)
        try:
            return await research_library.accept_correction(version_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/v1/research-library/versions/{version_id}/claims", response_model=list[AnalystClaim])
    def get_research_claims(version_id: str) -> list[AnalystClaim]:
        if research_library.get_version(version_id) is None:
            raise HTTPException(status_code=404, detail="research_document_version_not_found")
        return research_library.get_claims(version_id)

    @app.post("/v1/research-library/context/{run_id}")
    def select_research_context(
        run_id: str,
        payload: ResearchRequest,
        x_owner_id: str | None = Header(None, max_length=128),
    ) -> dict[str, object]:
        require_owner(x_owner_id)
        if research_library.get_context(run_id) is None:
            raise HTTPException(
                status_code=409, detail="research_context_requires_atomic_admission"
            )
        try:
            return research_library.select_context(payload, run_id=run_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/v1/research-library/context/{run_id}", response_model=ResearchRunScope | None)
    def get_research_context(run_id: str) -> ResearchRunScope | None:
        return research_library.get_context(run_id)

    @app.post(
        "/v1/research-library/research",
        status_code=status.HTTP_202_ACCEPTED,
        response_model=ResearchRunQueued,
    )
    def research_with_library_context(
        payload: ResearchRequest,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        x_owner_id: str | None = Header(None, max_length=128),
    ) -> ResearchRunQueued:
        require_owner(x_owner_id)
        if not idempotency_key:
            raise HTTPException(status_code=400, detail="Idempotency-Key header is required")
        if payload.research_family != "crypto_macro":
            raise HTTPException(status_code=409, detail="research_family_not_executable")
        observation = ObservationCreate(
            text=payload.focus,
            source_id="decision-desk-research-library",
            source_type=SourceType.manual,
            language="zh",
            event_hint="crypto_market_snapshot",
        )
        normalized = normalize_latest_advice_request(observation)
        try:
            with db.session() as session:
                event_id, run_id, _created = analyzer.queue_research(
                    normalized, idempotency_key, session=session
                )
                research_library.select_context(payload, run_id=run_id, session=session)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return ResearchRunQueued(
            schema_version="research-run-queued.v1",
            event_id=event_id,
            run_id=run_id,
            status="queued",
            status_url=f"/v1/runs/{run_id}",
        )

    @app.post(
        "/v1/data-jobs",
        status_code=status.HTTP_202_ACCEPTED,
        response_model=DataJobView,
    )
    def create_data_job(
        payload: DataJobCreate,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        owner_id: str | None = Header(default=None, alias="X-Owner-Id"),
    ) -> DataJobView:
        require_owner(owner_id)
        if idempotency_key != payload.idempotency_key:
            raise HTTPException(status_code=400, detail="idempotency_key_mismatch")
        try:
            view, _created = data_jobs.create(payload)
            return view
        except ValueError as exc:
            code = str(exc)
            status_code = 409 if code == "data_job_idempotency_conflict" else 400
            raise HTTPException(status_code=status_code, detail=code) from exc

    @app.get("/v1/data-jobs", response_model=list[DataJobView])
    def list_data_jobs(
        response: Response,
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        status: Literal["queued", "running", "partial", "failed", "completed"] | None = None,
        dataset: str | None = Query(default=None, max_length=200),
        created_before: datetime | None = None,
    ) -> list[DataJobView]:
        try:
            items, total = data_jobs.list_page(
                limit=limit, offset=offset, status=status, dataset=dataset,
                created_before=created_before,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        response.headers["X-Total-Count"] = str(total)
        return items

    @app.get("/v1/data-jobs/{job_id}", response_model=DataJobView)
    def get_data_job(job_id: str) -> DataJobView:
        view = data_jobs.get(job_id)
        if view is None:
            raise HTTPException(status_code=404, detail="data_job_not_found")
        return view

    @app.get("/v1/macro-events", response_model=list[MacroEventView])
    def macro_events(
        response: Response,
        limit: int = Query(default=50, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        scope: Literal["all", "upcoming", "history"] = Query(default="all"),
        q: str | None = Query(default=None, max_length=200),
        family: str | None = Query(default=None, max_length=128),
    ) -> list[MacroEventView]:
        page = list_macro_events_page(
            db, limit=limit, offset=offset, scope=scope, q=q, family=family
        )
        response.headers["X-Total-Count"] = str(page.total_count)
        return page.items

    @app.get("/v1/macro-events/families", response_model=list[str])
    def macro_event_families() -> list[str]:
        return list_macro_event_families(db)

    @app.get("/v1/macro-events/{macro_event_id}", response_model=MacroEventView)
    def macro_event(macro_event_id: str) -> MacroEventView:
        view = get_macro_event(db, macro_event_id)
        if view is None:
            raise HTTPException(status_code=404, detail="macro_event_not_found")
        return view

    @app.get(
        "/v1/macro-events/{macro_event_id}/vintages",
        response_model=list[MacroEventVintageView],
    )
    def macro_event_vintages(
        macro_event_id: str,
        limit: int = Query(default=100, ge=1, le=500),
    ) -> list[MacroEventVintageView]:
        return list_macro_event_vintages(db, macro_event_id, limit=limit)

    @app.get("/v1/strategy-calibrations", response_model=list[StrategyCalibrationView])
    def strategy_calibrations(
        limit: int = Query(default=50, ge=1, le=500),
    ) -> list[StrategyCalibrationView]:
        return calibrations.list(limit=limit)

    @app.get("/v1/research/home", response_model=ResearchHomeView)
    def research_home() -> ResearchHomeView:
        return research_query.home()

    @app.get("/v1/research/recent-runs", response_model=list[ResearchRunView])
    def research_recent_runs(limit: int = Query(default=8, ge=1, le=8)) -> list[ResearchRunView]:
        return recent_research_runs(research_query, limit=limit)

    @app.get("/v1/research/runs", response_model=list[ResearchRunView])
    def research_runs(
        response: Response,
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        cursor: str | None = Query(default=None, max_length=1024),
        status: Literal[
            "queued", "researching", "retry_wait", "completed", "degraded",
            "research_only", "rejected", "failed", "cancelled"
        ] | None = Query(default=None),
        source_id: str | None = Query(default=None, min_length=1, max_length=128),
        origin: str | None = Query(default=None, min_length=1, max_length=32),
        q: str | None = Query(default=None, max_length=200),
    ) -> list[ResearchRunView]:
        try:
            page = research_query.list_page(
                limit=limit, offset=offset, cursor=cursor,
                status=status, source_id=source_id, origin=origin, q=q,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        response.headers["X-Total-Count"] = str(page.total_count)
        response.headers["X-Has-More"] = "true" if page.has_more else "false"
        if page.next_cursor is not None:
            response.headers["X-Next-Cursor"] = page.next_cursor
        return page.items

    @app.get("/v1/research/inbox", response_model=ResearchInboxView)
    def research_inbox(
        limit: int = Query(default=100, ge=1, le=500),
    ) -> ResearchInboxView:
        return research_query.inbox(limit=limit)

    @app.get(
        "/v1/research/runs/{run_id}/observability",
        response_model=ResearchObservabilityView,
    )
    def research_run_observability(run_id: str) -> ResearchObservabilityView:
        view = research_query.observability_view(run_id)
        if view is None:
            raise HTTPException(status_code=404, detail="research_run_not_found")
        return view

    @app.get(
        "/v1/research/runs/{run_id}/value-evaluation",
        response_model=ResearchValueEvaluation,
    )
    def research_run_value_evaluation(run_id: str) -> ResearchValueEvaluation:
        view = research_query.value_evaluation(run_id)
        if view is None:
            raise HTTPException(
                status_code=404,
                detail="research_value_evaluation_not_found",
            )
        return view

    @app.get(
        "/v1/research/runs/{run_id}/forecast-audit",
        response_model=list[ForecastAuditView],
    )
    def research_run_forecast_audit(run_id: str) -> list[ForecastAuditView]:
        try:
            return forecast_audit_for_run(db, run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="research_run_not_found") from exc

    @app.get(
        "/v1/research/runs/{run_id}/review-plans",
        response_model=list[ReviewScheduleView],
    )
    def research_run_review_plans(run_id: str) -> list[ReviewScheduleView]:
        return review_schedules.list_for_run(run_id)

    @app.get("/v1/research/runs/{run_id}", response_model=ResearchRunDetailView)
    async def research_run_detail(
        request: Request,
        run_id: str,
        bridge_key: str | None = Header(default=None, alias="X-Decision-Hub-Bridge-Key"),
    ) -> ResearchRunDetailView:
        from starlette.concurrency import run_in_threadpool

        def read_original(check):
            check()
            view = research_query.get(run_id)
            if view is None and bridge_key is not None:
                require_dsh_callback_secret(bridge_key)
                if frozen_review_bootstrap is not None:
                    try:
                        view = research_query.get_for_signed_harness_review(
                            run_id, frozen_review_bootstrap.service,
                        )
                    except (ValueError, KeyError, PermissionError, OSError):
                        raise HTTPException(
                            status_code=409, detail="harness_frozen_review_scope_invalid",
                        ) from None
            check()
            return view

        if any(name in request.headers for name in (
            "x-decision-hub-research-proof-deadline-at",
            "x-decision-hub-research-proof-budget-ms",
        )):
            # A native original deadline is a ceiling only. Authenticate this
            # selected branch before work; the complete reader remains intact.
            from apps.hub_api.research_proof_request import await_original_research_read

            require_dsh_callback_secret(bridge_key)
            try:
                view = await await_original_research_read(
                    request, db, run_id, read_original, kind="research_detail",
                )
            except ValueError as error:
                if str(error) not in {
                    "dsh_host_read_cancelled", "dsh_host_read_deadline_elapsed",
                    "dsh_model_dispatch_identity_mismatch",
                }:
                    raise
                raise HTTPException(status_code=409, detail=str(error)) from error
        else:
            # The default UI and signed legacy fallback keep the same reader
            # and authorization contract, with one owned Starlette RO callback.
            view = await run_in_threadpool(read_original, lambda: None)
        if view is None:
            raise HTTPException(status_code=404, detail="research_run_not_found")
        return view


    @app.get(
        "/v1/research/runs/{run_id}/decision-graph",
        response_model=DecisionGraph,
    )
    def research_run_decision_graph(run_id: str) -> DecisionGraph:
        view = research_query.get(run_id)
        if view is None or view.decision_graph is None:
            raise HTTPException(status_code=404, detail="research_decision_graph_not_found")
        return view.decision_graph

    @app.get(
        "/v1/research/runs/{run_id}/comparison",
        response_model=ResearchRunComparisonView,
    )
    def research_run_comparison(run_id: str) -> ResearchRunComparisonView:
        view = research_query.comparison(run_id)
        if view is None:
            raise HTTPException(status_code=404, detail="research_run_not_found")
        return view

    @app.get("/v1/research/runs/{run_id}/events")
    async def research_run_events(
        run_id: str,
        after: int = Query(default=0, ge=0),
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ) -> StreamingResponse:
        if await run_in_threadpool(research_query.trace_status, run_id) is None:
            raise HTTPException(status_code=404, detail="research_run_not_found")
        cursor = after
        if last_event_id:
            try:
                cursor = max(cursor, int(last_event_id))
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="last_event_id_invalid") from exc

        async def stream():
            nonlocal cursor
            while True:
                events = await run_in_threadpool(research_query.trace, run_id, after=cursor)
                for event in events:
                    cursor = event.sequence_no
                    payload = json.dumps(event.model_dump(mode="json"), ensure_ascii=False)
                    yield f"id: {cursor}\nevent: {event.event_type}\ndata: {payload}\n\n"
                status = await run_in_threadpool(research_query.trace_status, run_id)
                if status is None or status in {
                    "completed",
                    "degraded",
                    "research_only",
                    "rejected",
                    "failed",
                    "cancelled",
                }:
                    return
                await asyncio.sleep(0.25)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post(
        "/v1/research/runs/{run_id}/commands",
        response_model=ResearchRunCommandResult,
    )
    async def research_run_command(
        run_id: str,
        payload: ResearchRunCommand,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        owner_id: str | None = Header(default=None, alias="X-Owner-Id"),
    ) -> ResearchRunCommandResult:
        owner = require_owner(owner_id)
        if not idempotency_key or idempotency_key != payload.request_id:
            raise HTTPException(status_code=400, detail="idempotency_key_mismatch")
        try:
            if installed_spot is not None and payload.command in {"retry", "recheck"}:
                from packages.kernel.decision_hub_kernel.persistence.db import (
                    ProductCommandRecord,
                    RunRecord,
                    StudyRecord,
                )

                with db.session() as session:
                    prior = session.get(ProductCommandRecord, payload.request_id)
                    parent = session.get(RunRecord, run_id)
                    study = session.get(StudyRecord, parent.study_id) if parent else None
                    instrument = (
                        study.instrument if study is not None and study.market == "crypto"
                        and study.instrument in {"BTC-USDT", "ETH-USDT"} else None
                    )
                if prior is None and instrument is not None:
                    await installed_spot.prepare(instrument)
            return await asyncio.to_thread(research_commands.apply, run_id, payload, owner_id=owner)
        except OperationalError as exc:
            if not is_sqlite_initial_begin_busy(exc):
                raise
            raise HTTPException(
                status_code=503, detail="research_command_storage_busy",
                headers={"Retry-After": "1"},
            ) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="research_run_not_found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get(
        "/v1/research/runs/{run_id}/commands/{request_id}",
        response_model=ResearchRunCommandReceipt,
    )
    def research_run_command_receipt(
        run_id: str,
        request_id: str,
        owner_id: str | None = Header(default=None, alias="X-Owner-Id"),
    ) -> ResearchRunCommandReceipt:
        owner = require_owner(owner_id)
        try:
            return research_commands.receipt(run_id, request_id, owner_id=owner)
        except KeyError as exc:
            raise HTTPException(
                status_code=404, detail="research_command_receipt_not_found"
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail="research_command_receipt_invalid") from exc

    @app.get("/v1/runs/{run_id}")
    def run_view(run_id: str):
        view = db.get_run_view(run_id)
        if not view:
            raise HTTPException(status_code=404, detail="run_not_found")
        return view

    @app.get("/v1/runs/{run_id}/view")
    def run_detail(run_id: str):
        view = db.get_run_view(run_id)
        if not view:
            raise HTTPException(status_code=404, detail="run_not_found")
        artifact = db.get_artifact_view(view.artifact_id) if view.artifact_id else None
        return {"run": view, "artifact": artifact}

    @app.get("/v1/runs/{run_id}/timeline", response_model=RunTimelineView)
    def run_timeline(run_id: str) -> RunTimelineView:
        if not db.get_run_view(run_id):
            raise HTTPException(status_code=404, detail="run_not_found")
        return RunTimelineView(run_id=run_id, items=db.get_timeline(run_id))

    @app.get("/v1/runs/{run_id}/inspector", response_model=RunInspectorView)
    def run_inspector(run_id: str) -> RunInspectorView:
        view = db.get_run_inspector(run_id)
        if not view:
            raise HTTPException(status_code=404, detail="run_not_found")
        return view

    @app.get("/v1/artifacts/{artifact_id}")
    def artifact_view(artifact_id: str):
        view = db.get_artifact_view(artifact_id)
        if not view:
            raise HTTPException(status_code=404, detail="artifact_not_found")
        return view

    @app.post("/v1/outcomes")
    def record_outcome(payload: OutcomeCreate):
        try:
            return outcomes.record(payload)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="forecast_not_found") from exc

    @app.get("/v1/evaluations/{evaluation_id}")
    def evaluation_view(evaluation_id: str):
        view = db.get_evaluation(evaluation_id)
        if not view:
            raise HTTPException(status_code=404, detail="evaluation_not_found")
        return view

    @app.get("/v1/workbench/memos", response_model=list[ResearchMemoView])
    def research_memos(limit: int = 100) -> list[ResearchMemoView]:
        return workbench.list_memos(limit=max(1, min(limit, 100)))

    @app.get("/v1/workbench/overview", response_model=WorkbenchOverviewView)
    def workbench_overview(limit: int = 100) -> WorkbenchOverviewView:
        return workbench.overview(limit=limit)

    @app.post("/v1/workbench/memos", response_model=ResearchMemoView)
    def create_research_memo(
        payload: ResearchMemoCreate,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        owner_id: str | None = Header(default=None, alias="X-Owner-Id"),
    ) -> ResearchMemoView:
        if not idempotency_key or idempotency_key != payload.request_id:
            raise HTTPException(status_code=400, detail="idempotency_key_mismatch")
        if not owner_id or owner_id != payload.created_by:
            raise HTTPException(status_code=403, detail="owner_identity_mismatch")
        try:
            return workbench.create_memo(payload)
        except ValueError as exc:
            code = str(exc)
            status_code = (
                409
                if code == "workbench_request_reused"
                else (
                    422
                    if code
                    in {
                        "workbench_evidence_not_found",
                        "workbench_snapshot_mismatch",
                        "workbench_reference_required",
                    }
                    else 404
                )
            )
            raise HTTPException(status_code=status_code, detail=code) from exc

    @app.get("/v1/workbench/feedback", response_model=list[FeedbackView])
    def workbench_feedback(limit: int = 100) -> list[FeedbackView]:
        return workbench.list_feedback(limit=max(1, min(limit, 500)))

    @app.get("/v1/workbench/capabilities", response_model=list[CapabilityManifest])
    def workbench_capabilities() -> list[CapabilityManifest]:
        return workbench.list_capabilities()

    @app.post("/v1/workbench/feedback", response_model=FeedbackView)
    def create_workbench_feedback(
        payload: FeedbackCreate,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        owner_id: str | None = Header(default=None, alias="X-Owner-Id"),
    ):
        if not idempotency_key or idempotency_key != payload.request_id:
            raise HTTPException(status_code=400, detail="idempotency_key_mismatch")
        require_owner(owner_id, payload.created_by)
        from packages.kernel.decision_hub_kernel.application.revision_feedback_evaluation import (
            RevisionFeedbackEvaluationError,
        )

        try:
            return workbench.create_feedback(payload)
        except RevisionFeedbackEvaluationError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            code = str(exc)
            if code not in {"workbench_request_reused", "workbench_target_not_found"}:
                raise
            status_code = 409 if code == "workbench_request_reused" else 404
            raise HTTPException(status_code=status_code, detail=code) from exc

    @app.post(
        "/v1/workbench/capabilities/{capability_id}/status",
        response_model=CapabilityManifest,
    )
    def set_capability_status(
        capability_id: str,
        status_value: str,
        owner_id: str | None = Header(default=None, alias="X-Owner-Id"),
    ) -> CapabilityManifest:
        require_owner(owner_id)
        try:
            return workbench.set_capability_status(capability_id, status_value)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/v1/evolution/experiences", response_model=ExperienceView)
    def create_experience(
        payload: ExperienceCreate,
        owner_id: str | None = Header(default=None, alias="X-Owner-Id"),
    ) -> ExperienceView:
        require_owner(owner_id, payload.created_by)
        try:
            return evolution.create_experience(payload)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/v1/evolution/overview", response_model=EvolutionOverviewView)
    def evolution_overview() -> EvolutionOverviewView:
        return evolution.overview()

    @app.get("/v1/evolution/candidates/{candidate_id}/execution")
    def candidate_execution(candidate_id: str):
        from packages.kernel.decision_hub_kernel.application.candidate_execution import (
            RULES,
            candidate_execution_error,
            checked_proposal,
        )
        candidate = evolution.get_candidate(candidate_id)
        if candidate is None:
            raise HTTPException(status_code=404, detail="candidate_not_found")
        error = candidate_execution_error(candidate)
        proposal = None
        try:
            proposal = checked_proposal(candidate)
        except (ValueError, OSError):
            pass
        return {"executable": error is None, "reason_code": error,
                "rules": [RULES[rule][0] for rule in (proposal.execution_rules or [])]
                if proposal and error is None else [],
                "summary": proposal.summary if proposal else None,
                "changes": proposal.changes if proposal else [],
                "evidence_refs": proposal.evidence_refs if proposal else [],
                "candidate_type": candidate.candidate_type,
                "content_hash": candidate.content_hash}

    @app.get("/v1/evolution/jobs/{job_id}/planning-decision")
    def evolution_planning_decision(job_id: str):
        from packages.kernel.decision_hub_kernel.application.planning_decisions import (
            planning_decision,
        )
        try:
            return planning_decision(db, job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="evolution_job_not_found") from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=409, detail="planning_decision_integrity_invalid"
            ) from exc

    @app.get("/v1/evolution/jobs", response_model=list[EvolutionJobView])
    def evolution_jobs(limit: int = 100) -> list[EvolutionJobView]:
        return operations.jobs.list_jobs(limit=max(1, min(limit, 500)))

    @app.get("/v1/evolution/jobs/{job_id}/planning-calls")
    def evolution_planning_calls(job_id: str, limit: int = 100):
        from packages.orchestration.langgraph.evolution_diagnostics import planning_calls
        try:
            return planning_calls(db, job_id, limit=limit)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="evolution_job_not_found") from exc

    @app.get("/v1/evolution/jobs/{job_id}/planning-context")
    def evolution_planning_context(job_id: str, include_inputs: bool = False):
        from packages.kernel.decision_hub_kernel.application.evolution_context import (
            EvolutionContextService,
        )
        try:
            body = EvolutionContextService(db).read(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="evolution_job_not_found") from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=409, detail="evolution_context_snapshot_corrupt"
            ) from exc
        if body is None or include_inputs:
            return body
        summary = {key: body[key] for key in (
            "schema_version", "frozen_at", "admission", "counts", "summary_count",
            "prompt_characters", "recovery_of")}
        summary["input_count"] = len(body["binding"]["input_refs"])
        summary["baseline_version"] = body["context"]["active_candidate_version"]
        return summary

    @app.get("/v1/operations", response_model=OperationsOverviewView)
    def operations_overview() -> OperationsOverviewView:
        return operations.overview()

    @app.get("/v1/operations/runtime-configurations", response_model=list[RuntimeConfigurationView])
    def runtime_configurations(
        limit: int = 50, current_only: bool = False
    ) -> list[RuntimeConfigurationView]:
        return list_runtime_configurations(db, limit, current_only=current_only)

    @app.get("/v1/evolution/failures")
    def evolution_failures():
        return evolution.list_failures()

    @app.get("/v1/evolution/experiences")
    def evolution_experiences(limit: int = 100):
        return evolution.list_experiences(limit=max(1, min(limit, 500)))

    @app.get("/v1/evolution/decisions")
    def evolution_decisions(limit: int = 100):
        return evolution.list_decisions(limit=max(1, min(limit, 500)))

    @app.post("/v1/evolution/datasets")
    def register_dataset(
        payload: EvaluationDatasetManifest,
        owner_id: str | None = Header(default=None, alias="X-Owner-Id"),
    ):
        require_owner(owner_id)
        try:
            return evolution.register_dataset(payload)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/v1/evolution/candidates")
    def register_candidate(
        payload: CandidateVersion,
        owner_id: str | None = Header(default=None, alias="X-Owner-Id"),
    ):
        require_owner(owner_id)
        try:
            return evolution.register_candidate(payload)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/v1/evolution/experiments")
    def register_experiment(
        payload: ExperimentManifest,
        owner_id: str | None = Header(default=None, alias="X-Owner-Id"),
    ):
        require_owner(owner_id)
        try:
            return evolution.register_experiment(payload)
        except ValueError as exc:
            code = str(exc)
            status = 404 if code.endswith("_not_found") else 422
            raise HTTPException(status_code=status, detail=code) from exc

    @app.post("/v1/evolution/results")
    def record_experiment_result(
        payload: ExperimentResultView,
        owner_id: str | None = Header(default=None, alias="X-Owner-Id"),
    ):
        require_owner(owner_id)
        try:
            return evolution.record_result(payload)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get(
        "/v1/evolution/{domain_pack_ref}/decisions/{request_id}",
        response_model=PromotionDecisionResult,
    )
    def promotion_decision_receipt(
        domain_pack_ref: str,
        request_id: str,
        owner_id: str | None = Header(default=None, alias="X-Owner-Id"),
    ) -> PromotionDecisionResult:
        owner = require_owner(owner_id)
        try:
            return evolution.decision_receipt(domain_pack_ref, request_id, owner=owner)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/v1/evolution/{domain_pack_ref}/decisions")
    def promotion_decision(
        domain_pack_ref: str,
        payload: PromotionDecisionCreate,
        owner_id: str | None = Header(default=None, alias="X-Owner-Id"),
    ) -> PromotionDecisionResult:
        require_owner(owner_id, payload.owner)
        try:
            return evolution.decide(domain_pack_ref, payload)
        except PermissionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post(
        "/v1/evolution/{domain_pack_ref}/promotion-reviews",
        response_model=PromotionReviewView,
    )
    def promotion_review(
        domain_pack_ref: str, payload: PromotionReviewRequest
    ) -> PromotionReviewView:
        try:
            return evolution.review_promotion(domain_pack_ref, payload)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/v1/decision-desk/summary")
    def summary():
        return desk.summary()

    @app.get("/v1/decision-desk/inbox")
    def inbox():
        return desk.summary().inbox

    @app.put("/v1/dsh/sessions/{run_id}/model-binding", response_model=DshModelDispatchBinding)
    async def dsh_model_binding(
        run_id: str,
        request: Request,
        payload: DshModelDispatchBinding,
        bridge_key: str | None = Header(default=None, alias="X-Decision-Hub-Bridge-Key"),
    ) -> DshModelDispatchBinding:
        require_dsh_callback_secret(bridge_key)
        if payload.run_id != run_id:
            raise HTTPException(status_code=400, detail="dsh_run_id_mismatch")
        try:
            from apps.hub_api.model_binding_request import bind_model_request

            return await bind_model_request(request, model_dispatch, payload)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="dsh_session_link_not_found") from exc
        except ValueError as exc:
            # Preserve a safe reason for native callback failures without
            # logging request bodies, credentials, SQL or query parameters.
            reason = str(exc)
            safe_reason = reason if re.fullmatch(r"[a-z][a-z0-9_]{0,127}", reason) else (
                "rejection_sha256:" + hashlib.sha256(reason.encode()).hexdigest()
            )
            safe_run = run_id if re.fullmatch(r"run_[a-f0-9]{32}", run_id) else (
                "sha256:" + hashlib.sha256(run_id.encode()).hexdigest()
            )
            query_fingerprint = getattr(exc, "query_fingerprint", None)
            if not isinstance(query_fingerprint, str) or not re.fullmatch(
                r"[a-f0-9]{64}", query_fingerprint
            ):
                query_fingerprint = None
            logging.getLogger("uvicorn.error").warning(
                "dsh_model_binding_rejected code=%s run=%s request_hash=%s query_fingerprint=%s",
                safe_reason, safe_run, payload.request_hash, query_fingerprint,
            )
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/v1/dsh/sessions/{run_id}/model-binding", response_model=DshModelDispatchBinding)
    def dsh_model_binding_read(
        run_id: str,
        bridge_key: str | None = Header(default=None, alias="X-Decision-Hub-Bridge-Key"),
        execution_session_id: str | None = Query(default=None, min_length=1, max_length=256),
    ) -> DshModelDispatchBinding:
        require_dsh_callback_secret(bridge_key)
        try:
            value = model_dispatch.get(run_id, execution_session_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail="dsh_model_binding_corrupt") from exc
        if value is None:
            raise HTTPException(status_code=404, detail="dsh_model_binding_not_recorded")
        return value

    @app.put(
        "/v1/dsh/sessions/{run_id}/accepted",
        response_model=DshRunSessionLinkView,
    )
    def dsh_session_accepted(
        run_id: str,
        payload: DshSessionAccepted,
        bridge_key: str | None = Header(default=None, alias="X-Decision-Hub-Bridge-Key"),
    ) -> DshRunSessionLinkView:
        require_dsh_callback_secret(bridge_key)
        if payload.run_id != run_id:
            raise HTTPException(status_code=400, detail="dsh_run_id_mismatch")
        try:
            return dsh_sessions.accepted(payload)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="dsh_session_link_not_found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.put(
        "/v1/dsh/sessions/{run_id}/status",
        response_model=DshRunSessionLinkView,
    )
    def dsh_session_status(
        run_id: str,
        payload: DshSessionStatus,
        bridge_key: str | None = Header(default=None, alias="X-Decision-Hub-Bridge-Key"),
    ) -> DshRunSessionLinkView:
        require_dsh_callback_secret(bridge_key)
        if payload.run_id != run_id:
            raise HTTPException(status_code=400, detail="dsh_run_id_mismatch")
        try:
            return dsh_sessions.observe(payload)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="dsh_session_link_not_found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.put(
        "/v1/dsh/sessions/{run_id}/terminal",
        response_model=DshRunSessionLinkView,
    )
    def dsh_session_terminal(
        run_id: str,
        payload: DshSessionCompletion,
        bridge_key: str | None = Header(default=None, alias="X-Decision-Hub-Bridge-Key"),
    ) -> DshRunSessionLinkView:
        require_dsh_callback_secret(bridge_key)
        if payload.run_id != run_id:
            raise HTTPException(status_code=400, detail="dsh_run_id_mismatch")
        try:
            return dsh_sessions.complete(payload, authenticated_host=True)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="dsh_session_link_not_found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get(
        "/v1/dsh/sessions/{run_id}/prompt",
        response_model=DshSessionPrompt,
    )
    async def dsh_session_prompt(
        request: Request,
        run_id: str,
        generation: int = Query(default=1, ge=1),
        bridge_key: str | None = Header(default=None, alias="X-Decision-Hub-Bridge-Key"),
    ) -> DshSessionPrompt:
        from starlette.concurrency import run_in_threadpool

        require_dsh_callback_secret(bridge_key)

        def read_original(check):
            check()
            prompt = dsh_sessions.get_prompt(run_id, generation)
            check()
            return prompt

        if any(name in request.headers for name in (
            "x-decision-hub-research-proof-deadline-at",
            "x-decision-hub-research-proof-budget-ms",
        )):
            from apps.hub_api.research_proof_request import await_original_research_read

            def scope_check(link):
                if link.run_id != run_id or link.generation != generation:
                    raise ValueError("dsh_model_dispatch_identity_mismatch")

            try:
                prompt = await await_original_research_read(
                    request, db, run_id, read_original,
                    scope_check=scope_check, kind="session_prompt",
                )
            except ValueError as error:
                if str(error) not in {
                    "dsh_host_read_cancelled", "dsh_host_read_deadline_elapsed",
                    "dsh_model_dispatch_identity_mismatch",
                }:
                    raise
                raise HTTPException(status_code=409, detail=str(error)) from error
        else:
            prompt = await run_in_threadpool(read_original, lambda: None)
        if prompt is None:
            raise HTTPException(status_code=404, detail="dsh_session_prompt_not_found")
        return prompt

    @app.get(
        "/v1/dsh/sessions/{run_id}/business-status",
        response_model=DshBusinessStatus,
    )
    async def dsh_business_status(
        request: Request,
        run_id: str,
        bridge_key: str | None = Header(default=None, alias="X-Decision-Hub-Bridge-Key"),
        projection: Literal["full", "lifecycle"] = "full",
    ) -> DshBusinessStatus:
        from starlette.concurrency import run_in_threadpool

        if projection == "lifecycle":
            require_dsh_callback_secret(bridge_key)

        def read_original(check):
            check()
            view = research_query.business_status(run_id, lifecycle_only=projection == "lifecycle")
            if view is None and bridge_key is not None:
                require_dsh_callback_secret(bridge_key)
                if frozen_review_bootstrap is not None:
                    try:
                        view = research_query.business_status_for_signed_harness(
                            run_id, frozen_review_bootstrap.service,
                        )
                    except (ValueError, KeyError, PermissionError, OSError):
                        raise HTTPException(
                            status_code=409, detail="harness_frozen_business_scope_invalid",
                        ) from None
            check()
            return view

        if projection == "full" and any(name in request.headers for name in (
            "x-decision-hub-research-proof-deadline-at",
            "x-decision-hub-research-proof-budget-ms",
        )):
            from apps.hub_api.research_proof_request import await_original_research_read

            require_dsh_callback_secret(bridge_key)
            try:
                view = await await_original_research_read(
                    request, db, run_id, read_original, kind="business_status",
                )
            except ValueError as error:
                if str(error) not in {
                    "dsh_host_read_cancelled", "dsh_host_read_deadline_elapsed",
                    "dsh_model_dispatch_identity_mismatch",
                }:
                    raise
                raise HTTPException(status_code=409, detail=str(error)) from error
        else:
            view = await run_in_threadpool(read_original, lambda: None)
        if view is None:
            raise HTTPException(status_code=404, detail="dsh_business_status_not_found")
        return view

    @app.get(
        "/v1/dsh/sessions/{run_id}",
        response_model=DshRunSessionLinkView,
    )
    def dsh_session_link(run_id: str) -> DshRunSessionLinkView:
        view = dsh_sessions.get(run_id)
        if view is None:
            raise HTTPException(status_code=404, detail="dsh_session_link_not_found")
        return view

    from apps.hub_api.harness_execution import HarnessLinkView, harness_execution_router, link_hint
    app.include_router(harness_execution_router(db, require_dsh_callback_secret))
    from apps.hub_api.research_skill_routes import research_skill_router
    from apps.hub_worker.research_skill_bootstrap import configured_research_skills
    research_skills = configured_research_skills(
        db, Path(os.getenv("DECISION_HUB_DATA_DIR", "data/decision-hub")),
        execution=paper_execution,
    )
    from apps.hub_worker.research_skill_callback_bootstrap import (
        configured_research_skill_callbacks,
    )
    skill_callbacks = configured_research_skill_callbacks(
        db, research_skills.service if research_skills is not None else None,
    )
    app.include_router(research_skill_router(
        research_skills.service if research_skills is not None else None,
        require_dsh_callback_secret, callbacks=skill_callbacks,
    ))
    from apps.hub_api.skill_method_routes import skill_method_router
    from packages.kernel.decision_hub_kernel.application.skill_method_view import SkillMethodQuery
    from packages.kernel.decision_hub_kernel.application.research_skill_generation_adapters import read_generation_method_in_session
    from packages.kernel.decision_hub_kernel.application.research_skill_trial_view import TrialMethodProjection
    trial_projection = (
        TrialMethodProjection(db, research_skills.trial_admission, research_skills.selections)
        if research_skills is not None and research_skills.trial_admission is not None
        else None
    )
    app.include_router(skill_method_router(
        SkillMethodQuery(
            db, research_skills.service, generation_reader=read_generation_method_in_session,
            trial_projection=trial_projection,
        ) if research_skills is not None else None,
        require_owner,
    ))

    @app.get(
        "/v1/dsh/sessions/by-session/{dsh_session_id}",
        response_model=HarnessLinkView, response_model_exclude_unset=True,
    )
    def dsh_session_link_by_session(dsh_session_id: str) -> DshRunSessionLinkView:
        view = dsh_sessions.get_by_session(dsh_session_id)
        if view is None:
            raise HTTPException(status_code=404, detail="dsh_session_link_not_found")
        return link_hint(db, view)

    @app.api_route(
        "/v1/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    )
    async def unknown_api_route(path: str):
        # Keep the SPA fallback from turning an API typo or stale process into
        # an HTML response that the client misreads as an empty API payload.
        del path
        raise HTTPException(status_code=404, detail="api_route_not_found")

    static_dir = (
        frontend_dir
        or Path(__file__).resolve().parents[2] / "apps" / "decision-desk" / "dist"
    )
    if (
        static_dir.is_dir()
        and (static_dir / "assets").is_dir()
        and (static_dir / "index.html").is_file()
    ):
        app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")

        @app.get("/{path:path}")
        def spa(path: str):
            candidate = static_dir / path
            return FileResponse(candidate if candidate.is_file() else static_dir / "index.html")

    return app


app = create_app()


def run() -> None:
    import uvicorn

    from apps.hub_api.server_config import graceful_shutdown_timeout_seconds

    uvicorn.run(
        app,
        access_log=False,  # Safe route-template timing replaces raw URL access logging.
        host=os.getenv("HUB_API_HOST", "127.0.0.1"),
        port=int(os.getenv("HUB_API_PORT", "8000")),
        timeout_graceful_shutdown=graceful_shutdown_timeout_seconds(),
    )


if __name__ == "__main__":
    run()

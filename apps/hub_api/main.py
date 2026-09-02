from __future__ import annotations

import asyncio
import hmac
import json
import os
import socket
from collections.abc import Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Query, status
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from packages.contracts_py.decision_hub_contracts.models import (
    CandidateVersion,
    CapabilityManifest,
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
    ObservationCreate,
    OperationsOverviewView,
    OutcomeCreate,
    PilotReadinessReport,
    PromotionDecisionCreate,
    PromotionDecisionResult,
    PromotionReviewRequest,
    PromotionReviewView,
    ResearchMemoCreate,
    ResearchMemoView,
    ResearchRunCommand,
    ResearchRunCommandResult,
    ResearchRunDetailView,
    ResearchRunQueued,
    ResearchRunView,
    RunInspectorView,
    RunStatus,
    RunTimelineView,
    RuntimeModeView,
    WorkbenchOverviewView,
)
from packages.kernel.decision_hub_kernel.application.admission import AdmissionService
from packages.kernel.decision_hub_kernel.application.dsh_sessions import DshSessionLinkService
from packages.kernel.decision_hub_kernel.application.evolution import EvolutionAssetService
from packages.kernel.decision_hub_kernel.application.health import HealthService
from packages.kernel.decision_hub_kernel.application.live_observation import (
    ServiceHeartbeatService,
)
from packages.kernel.decision_hub_kernel.application.outcome import OutcomeService
from packages.kernel.decision_hub_kernel.application.research_observability import (
    ResearchCommandService,
)
from packages.kernel.decision_hub_kernel.application.source_ingest import SourceIngestionService
from packages.kernel.decision_hub_kernel.application.workbench import WorkbenchAssetService
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError
from packages.kernel.decision_hub_kernel.ports.sources import SourceConnector
from packages.orchestration.langgraph import build_analyze_text_service
from packages.pilot_runtime import build_readiness_service
from packages.query_views.decision_desk.service import DecisionDeskQueryService
from packages.query_views.operations import OperationsQueryService
from packages.query_views.research import ResearchQueryService
from packages.runtime_adapters.langgraph_agent.runtime import LangGraphAgentRuntime
from packages.source_adapters.official_feeds import official_source_presets
from packages.source_adapters.registry import SourceRegistry


def create_app(
    database: Database | None = None,
    *,
    source_connectors: Sequence[SourceConnector] | None = None,
    sources_enabled: bool | None = None,
    dsh_callback_secret: str | None = None,
) -> FastAPI:
    db = database or Database()
    if database is None:
        db.initialize()
    else:
        db.create_all()
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
    source_ingestion = SourceIngestionService(db, source_registry, admission=AdmissionService(db))
    heartbeat_interval = float(os.getenv("DECISION_HUB_HEARTBEAT_INTERVAL", "10"))
    if heartbeat_interval <= 0:
        raise ValueError("heartbeat_interval_invalid")
    api_started_at = datetime.now(UTC)
    api_instance_id = os.getenv("DECISION_HUB_INSTANCE_ID") or (
        f"{socket.gethostname()}:{os.getpid()}:api"
    )
    runtime_mode = "provider" if runtime.enabled else "fake"
    canary_status = os.getenv("DECISION_HUB_LLM_CANARY_STATUS", "not_run")
    if canary_status not in {"not_run", "passed", "failed", "unknown"}:
        raise ValueError("llm_canary_status_invalid")
    runtime_view = RuntimeModeView(
        runtime_id=runtime.effective_runtime_id,
        runtime_version=runtime.effective_runtime_version,
        mode=runtime_mode,
        provider_configured=runtime.enabled,
        live_canary_status=cast(
            Literal["not_run", "passed", "failed", "unknown"], canary_status
        ),
        model=runtime.provider_config.model if runtime.enabled else None,
        api_mode=runtime.api_mode if runtime.enabled else None,
    )
    effective_sources_enabled = (
        sources_enabled
        if sources_enabled is not None
        else os.getenv("DECISION_HUB_SOURCES_ENABLED", "0") == "1"
    )
    operations = OperationsQueryService(
        db,
        runtime=runtime_view,
        source_manifests=source_registry.manifests(),
        source_execution_enabled=effective_sources_enabled,
    )
    pack_root = Path(__file__).resolve().parents[2] / "packs" / "crypto_macro"
    research_query = ResearchQueryService(db, pack_root=pack_root)
    research_commands = ResearchCommandService(db)
    dsh_sessions = DshSessionLinkService(db)
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

    app = FastAPI(title="Decision Hub API", version="0.1.0", lifespan=lifespan)
    app.state.database = db
    app.state.analyzer = analyzer
    app.state.source_registry = source_registry
    app.state.source_ingestion = source_ingestion
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

    def require_owner(owner_id: str | None, declared_owner: str | None = None) -> str:
        if not owner_id:
            raise HTTPException(status_code=403, detail="owner_identity_required")
        if declared_owner is not None and owner_id != declared_owner:
            raise HTTPException(status_code=403, detail="owner_identity_mismatch")
        return owner_id

    def require_dsh_callback_secret(provided: str | None) -> None:
        if not effective_dsh_callback_secret:
            raise HTTPException(status_code=503, detail="dsh_callback_not_configured")
        if not provided or not hmac.compare_digest(provided, effective_dsh_callback_secret):
            raise HTTPException(status_code=401, detail="dsh_callback_unauthorized")

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

    @app.get("/health/ready")
    async def ready() -> dict[str, str]:
        try:
            health.status()
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {"status": "ready"}

    @app.get("/v1/health")
    async def product_health():
        return health.status()

    @app.get("/v1/pilot/readiness", response_model=PilotReadinessReport)
    async def pilot_readiness() -> PilotReadinessReport:
        # Recompute database state on every request while keeping checks in pilot_runtime.
        return app.state.readiness_service.report()

    @app.get("/v1/sources")
    async def sources() -> list[dict[str, object]]:
        return [
            {
                "manifest": manifest.model_dump(mode="json"),
                "health": source_ingestion.health(manifest.source_id).model_dump(mode="json"),
            }
            for manifest in source_registry.manifests()
        ]

    @app.post("/v1/sources/{source_id}/poll")
    async def poll_source(source_id: str, background_tasks: BackgroundTasks) -> dict[str, object]:
        if not app.state.sources_enabled:
            raise HTTPException(status_code=409, detail="sources_disabled")
        try:
            result = await source_ingestion.poll_once(source_id)
            for target in result.run_targets:
                background_tasks.add_task(execute, target.event_id, target.run_id)
            return result.model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="source_not_found") from exc

    @app.post("/v1/observations", status_code=status.HTTP_202_ACCEPTED)
    async def observations(
        payload: ObservationCreate,
        background_tasks: BackgroundTasks,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, str]:
        if not idempotency_key:
            raise HTTPException(status_code=400, detail="Idempotency-Key header is required")
        event_id, envelope, admitted = analyzer.admission.admit(payload)
        if not admitted:
            existing_run = analyzer.runs.for_event(event_id)
            if existing_run:
                return {
                    "event_id": event_id,
                    "run_id": existing_run,
                    "status_url": f"/v1/runs/{existing_run}",
                }
        run_id, run_created = analyzer.runs.create(event_id, idempotency_key)
        if admitted and run_created:
            background_tasks.add_task(execute, event_id, run_id)
        elif not run_created:
            return {
                "event_id": event_id,
                "run_id": run_id,
                "status_url": f"/v1/runs/{run_id}",
            }
        else:
            analyzer.runs.set_status(run_id, RunStatus.degraded, error_code="duplicate_observation")
        return {"event_id": event_id, "run_id": run_id, "status_url": f"/v1/runs/{run_id}"}

    @app.post(
        "/v1/research/observations",
        status_code=status.HTTP_202_ACCEPTED,
        response_model=ResearchRunQueued,
    )
    async def research_observations(
        payload: ObservationCreate,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> ResearchRunQueued:
        """Queue an agentic research candidate without running it in the API process."""
        if not idempotency_key:
            raise HTTPException(status_code=400, detail="Idempotency-Key header is required")
        try:
            event_id, run_id, _created = analyzer.queue_research(payload, idempotency_key)
        except ValueError as exc:
            if str(exc) == "idempotency_key_required":
                raise HTTPException(
                    status_code=400, detail="Idempotency-Key header is required"
                ) from exc
            raise
        return ResearchRunQueued(
            schema_version="research-run-queued.v1",
            event_id=event_id,
            run_id=run_id,
            status="queued",
            status_url=f"/v1/runs/{run_id}",
        )

    @app.get("/v1/research/runs", response_model=list[ResearchRunView])
    async def research_runs(limit: int = Query(default=100, ge=1, le=500)):
        return research_query.list(limit=limit)

    @app.get("/v1/research/runs/{run_id}", response_model=ResearchRunDetailView)
    async def research_run_detail(run_id: str) -> ResearchRunDetailView:
        view = research_query.get(run_id)
        if view is None:
            raise HTTPException(status_code=404, detail="research_run_not_found")
        return view

    @app.get("/v1/research/runs/{run_id}/events")
    async def research_run_events(
        run_id: str,
        after: int = Query(default=0, ge=0),
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ) -> StreamingResponse:
        if research_query.get(run_id) is None:
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
                events = research_query.trace(run_id, after=cursor)
                for event in events:
                    cursor = event.sequence_no
                    payload = json.dumps(event.model_dump(mode="json"), ensure_ascii=False)
                    yield f"id: {cursor}\nevent: {event.event_type}\ndata: {payload}\n\n"
                view = research_query.get(run_id)
                if view is None or view.run.status in {
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
        require_owner(owner_id)
        if not idempotency_key or idempotency_key != payload.request_id:
            raise HTTPException(status_code=400, detail="idempotency_key_mismatch")
        try:
            return research_commands.apply(run_id, payload)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="research_run_not_found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/v1/runs/{run_id}")
    async def run_view(run_id: str):
        view = db.get_run_view(run_id)
        if not view:
            raise HTTPException(status_code=404, detail="run_not_found")
        return view

    @app.get("/v1/runs/{run_id}/view")
    async def run_detail(run_id: str):
        view = db.get_run_view(run_id)
        if not view:
            raise HTTPException(status_code=404, detail="run_not_found")
        artifact = db.get_artifact_view(view.artifact_id) if view.artifact_id else None
        return {"run": view, "artifact": artifact}

    @app.get("/v1/runs/{run_id}/timeline", response_model=RunTimelineView)
    async def run_timeline(run_id: str) -> RunTimelineView:
        if not db.get_run_view(run_id):
            raise HTTPException(status_code=404, detail="run_not_found")
        return RunTimelineView(run_id=run_id, items=db.get_timeline(run_id))

    @app.get("/v1/runs/{run_id}/inspector", response_model=RunInspectorView)
    async def run_inspector(run_id: str) -> RunInspectorView:
        view = db.get_run_inspector(run_id)
        if not view:
            raise HTTPException(status_code=404, detail="run_not_found")
        return view

    @app.get("/v1/artifacts/{artifact_id}")
    async def artifact_view(artifact_id: str):
        view = db.get_artifact_view(artifact_id)
        if not view:
            raise HTTPException(status_code=404, detail="artifact_not_found")
        return view

    @app.post("/v1/outcomes")
    async def record_outcome(payload: OutcomeCreate):
        try:
            return outcomes.record(payload)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="forecast_not_found") from exc

    @app.get("/v1/evaluations/{evaluation_id}")
    async def evaluation_view(evaluation_id: str):
        view = db.get_evaluation(evaluation_id)
        if not view:
            raise HTTPException(status_code=404, detail="evaluation_not_found")
        return view

    @app.get("/v1/workbench/memos", response_model=list[ResearchMemoView])
    async def research_memos(limit: int = 100) -> list[ResearchMemoView]:
        return workbench.list_memos(limit=max(1, min(limit, 100)))

    @app.get("/v1/workbench/overview", response_model=WorkbenchOverviewView)
    async def workbench_overview(limit: int = 100) -> WorkbenchOverviewView:
        return workbench.overview(limit=limit)

    @app.post("/v1/workbench/memos", response_model=ResearchMemoView)
    async def create_research_memo(
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
    async def workbench_feedback(limit: int = 100) -> list[FeedbackView]:
        return workbench.list_feedback(limit=max(1, min(limit, 500)))

    @app.get("/v1/workbench/capabilities", response_model=list[CapabilityManifest])
    async def workbench_capabilities() -> list[CapabilityManifest]:
        return workbench.list_capabilities()

    @app.post("/v1/workbench/feedback", response_model=FeedbackView)
    async def create_workbench_feedback(
        payload: FeedbackCreate,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        owner_id: str | None = Header(default=None, alias="X-Owner-Id"),
    ):
        if not idempotency_key or idempotency_key != payload.request_id:
            raise HTTPException(status_code=400, detail="idempotency_key_mismatch")
        require_owner(owner_id, payload.created_by)
        try:
            return workbench.create_feedback(payload)
        except ValueError as exc:
            code = str(exc)
            status_code = 409 if code == "workbench_request_reused" else 404
            raise HTTPException(status_code=status_code, detail=code) from exc

    @app.post(
        "/v1/workbench/capabilities/{capability_id}/status",
        response_model=CapabilityManifest,
    )
    async def set_capability_status(
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
    async def create_experience(
        payload: ExperienceCreate,
        owner_id: str | None = Header(default=None, alias="X-Owner-Id"),
    ) -> ExperienceView:
        require_owner(owner_id, payload.created_by)
        try:
            return evolution.create_experience(payload)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/v1/evolution/overview", response_model=EvolutionOverviewView)
    async def evolution_overview() -> EvolutionOverviewView:
        return evolution.overview()

    @app.get("/v1/evolution/jobs", response_model=list[EvolutionJobView])
    async def evolution_jobs(limit: int = 100) -> list[EvolutionJobView]:
        return operations.jobs.list_jobs(limit=max(1, min(limit, 500)))

    @app.get("/v1/operations", response_model=OperationsOverviewView)
    async def operations_overview() -> OperationsOverviewView:
        return operations.overview()

    @app.get("/v1/evolution/failures")
    async def evolution_failures():
        return evolution.list_failures()

    @app.get("/v1/evolution/experiences")
    async def evolution_experiences(limit: int = 100):
        return evolution.list_experiences(limit=max(1, min(limit, 500)))

    @app.get("/v1/evolution/decisions")
    async def evolution_decisions(limit: int = 100):
        return evolution.list_decisions(limit=max(1, min(limit, 500)))

    @app.post("/v1/evolution/datasets")
    async def register_dataset(
        payload: EvaluationDatasetManifest,
        owner_id: str | None = Header(default=None, alias="X-Owner-Id"),
    ):
        require_owner(owner_id)
        try:
            return evolution.register_dataset(payload)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/v1/evolution/candidates")
    async def register_candidate(
        payload: CandidateVersion,
        owner_id: str | None = Header(default=None, alias="X-Owner-Id"),
    ):
        require_owner(owner_id)
        try:
            return evolution.register_candidate(payload)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @app.post("/v1/evolution/experiments")
    async def register_experiment(
        payload: ExperimentManifest,
        owner_id: str | None = Header(default=None, alias="X-Owner-Id"),
    ):
        require_owner(owner_id)
        try:
            return evolution.register_experiment(payload)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/v1/evolution/results")
    async def record_experiment_result(
        payload: ExperimentResultView,
        owner_id: str | None = Header(default=None, alias="X-Owner-Id"),
    ):
        require_owner(owner_id)
        try:
            return evolution.record_result(payload)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/v1/evolution/{domain_pack_ref}/decisions")
    async def promotion_decision(
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
    async def promotion_review(
        domain_pack_ref: str, payload: PromotionReviewRequest
    ) -> PromotionReviewView:
        try:
            return evolution.review_promotion(domain_pack_ref, payload)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/v1/decision-desk/summary")
    async def summary():
        return desk.summary()

    @app.get("/v1/decision-desk/inbox")
    async def inbox():
        return desk.summary().inbox

    @app.put(
        "/v1/dsh/sessions/{run_id}/accepted",
        response_model=DshRunSessionLinkView,
    )
    async def dsh_session_accepted(
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
    async def dsh_session_status(
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
    async def dsh_session_terminal(
        run_id: str,
        payload: DshSessionCompletion,
        bridge_key: str | None = Header(default=None, alias="X-Decision-Hub-Bridge-Key"),
    ) -> DshRunSessionLinkView:
        require_dsh_callback_secret(bridge_key)
        if payload.run_id != run_id:
            raise HTTPException(status_code=400, detail="dsh_run_id_mismatch")
        try:
            return dsh_sessions.complete(payload)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="dsh_session_link_not_found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get(
        "/v1/dsh/sessions/{run_id}/prompt",
        response_model=DshSessionPrompt,
    )
    async def dsh_session_prompt(
        run_id: str,
        generation: int = Query(default=1, ge=1),
        bridge_key: str | None = Header(default=None, alias="X-Decision-Hub-Bridge-Key"),
    ) -> DshSessionPrompt:
        require_dsh_callback_secret(bridge_key)
        prompt = dsh_sessions.get_prompt(run_id, generation)
        if prompt is None:
            raise HTTPException(status_code=404, detail="dsh_session_prompt_not_found")
        return prompt

    @app.get(
        "/v1/dsh/sessions/{run_id}/business-status",
        response_model=DshBusinessStatus,
    )
    async def dsh_business_status(run_id: str) -> DshBusinessStatus:
        view = research_query.business_status(run_id)
        if view is None:
            raise HTTPException(status_code=404, detail="dsh_business_status_not_found")
        return view

    @app.get(
        "/v1/dsh/sessions/{run_id}",
        response_model=DshRunSessionLinkView,
    )
    async def dsh_session_link(run_id: str) -> DshRunSessionLinkView:
        view = dsh_sessions.get(run_id)
        if view is None:
            raise HTTPException(status_code=404, detail="dsh_session_link_not_found")
        return view

    @app.get(
        "/v1/dsh/sessions/by-session/{dsh_session_id}",
        response_model=DshRunSessionLinkView,
    )
    async def dsh_session_link_by_session(dsh_session_id: str) -> DshRunSessionLinkView:
        view = dsh_sessions.get_by_session(dsh_session_id)
        if view is None:
            raise HTTPException(status_code=404, detail="dsh_session_link_not_found")
        return view

    @app.api_route(
        "/v1/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    )
    async def unknown_api_route(path: str):
        # Keep the SPA fallback from turning an API typo or stale process into
        # an HTML response that the client misreads as an empty API payload.
        del path
        raise HTTPException(status_code=404, detail="api_route_not_found")

    static_dir = Path(__file__).resolve().parents[2] / "apps" / "decision-desk" / "dist"
    if (
        static_dir.is_dir()
        and (static_dir / "assets").is_dir()
        and (static_dir / "index.html").is_file()
    ):
        app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")

        @app.get("/{path:path}")
        async def spa(path: str):
            candidate = static_dir / path
            return FileResponse(candidate if candidate.is_file() else static_dir / "index.html")

    return app


app = create_app()


def run() -> None:
    import uvicorn

    uvicorn.run(
        "apps.hub_api.main:app",
        host=os.getenv("HUB_API_HOST", "127.0.0.1"),
        port=int(os.getenv("HUB_API_PORT", "8000")),
    )


if __name__ == "__main__":
    run()

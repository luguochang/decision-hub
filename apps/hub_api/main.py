from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from packages.contracts_py.decision_hub_contracts.models import (
    CandidateVersion,
    CapabilityManifest,
    EvaluationDatasetManifest,
    EvolutionOverviewView,
    ExperienceCreate,
    ExperienceView,
    ExperimentManifest,
    ExperimentResultView,
    FeedbackCreate,
    FeedbackView,
    ObservationCreate,
    OutcomeCreate,
    PilotReadinessReport,
    PromotionDecisionCreate,
    PromotionDecisionResult,
    PromotionReviewRequest,
    PromotionReviewView,
    ResearchMemoCreate,
    ResearchMemoView,
    RunInspectorView,
    RunStatus,
    RunTimelineView,
    WorkbenchOverviewView,
)
from packages.kernel.decision_hub_kernel.application.admission import AdmissionService
from packages.kernel.decision_hub_kernel.application.evolution import EvolutionAssetService
from packages.kernel.decision_hub_kernel.application.health import HealthService
from packages.kernel.decision_hub_kernel.application.outcome import OutcomeService
from packages.kernel.decision_hub_kernel.application.source_ingest import SourceIngestionService
from packages.kernel.decision_hub_kernel.application.workbench import WorkbenchAssetService
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError
from packages.kernel.decision_hub_kernel.ports.sources import SourceConnector
from packages.orchestration.langgraph import build_analyze_text_service
from packages.pilot_runtime import build_readiness_service
from packages.query_views.decision_desk.service import DecisionDeskQueryService
from packages.runtime_adapters.langgraph_agent.runtime import LangGraphAgentRuntime
from packages.source_adapters.official_feeds import official_source_presets
from packages.source_adapters.registry import SourceRegistry


def create_app(
    database: Database | None = None,
    *,
    source_connectors: Sequence[SourceConnector] | None = None,
    sources_enabled: bool | None = None,
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
    source_registry = SourceRegistry()
    for source in (
        list(source_connectors) if source_connectors is not None else official_source_presets()
    ):
        source_registry.register(source)
    source_ingestion = SourceIngestionService(db, source_registry, admission=AdmissionService(db))
    app = FastAPI(title="Decision Hub API", version="0.1.0")
    app.state.database = db
    app.state.analyzer = analyzer
    app.state.source_registry = source_registry
    app.state.source_ingestion = source_ingestion
    app.state.sources_enabled = (
        sources_enabled
        if sources_enabled is not None
        else os.getenv("DECISION_HUB_SOURCES_ENABLED", "0") == "1"
    )
    app.state.readiness_service = build_readiness_service(
        db,
        source_registry.manifests(),
    )
    app.state.workbench = workbench
    app.state.evolution = evolution

    def require_owner(owner_id: str | None, declared_owner: str | None = None) -> str:
        if not owner_id:
            raise HTTPException(status_code=403, detail="owner_identity_required")
        if declared_owner is not None and owner_id != declared_owner:
            raise HTTPException(status_code=403, detail="owner_identity_mismatch")
        return owner_id

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

    static_dir = Path(__file__).resolve().parents[2] / "apps" / "decision-desk" / "dist"
    if static_dir.exists():
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

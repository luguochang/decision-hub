from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from packages.contracts_py.decision_hub_contracts.models import (
    ObservationCreate,
    OutcomeCreate,
    RunStatus,
)
from packages.kernel.decision_hub_kernel.application.admission import AdmissionService
from packages.kernel.decision_hub_kernel.application.analyze import AnalyzeTextService
from packages.kernel.decision_hub_kernel.application.health import HealthService
from packages.kernel.decision_hub_kernel.application.outcome import OutcomeService
from packages.kernel.decision_hub_kernel.application.source_ingest import SourceIngestionService
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError
from packages.kernel.decision_hub_kernel.ports.sources import SourceConnector
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
    analyzer = AnalyzeTextService(db, runtime, checkpoint_path=checkpoint_path)
    desk = DecisionDeskQueryService(db)
    health = HealthService(db)
    outcomes = OutcomeService(db)
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

    @app.get("/v1/runs/{run_id}/timeline")
    async def run_timeline(run_id: str):
        if not db.get_run_view(run_id):
            raise HTTPException(status_code=404, detail="run_not_found")
        return {"run_id": run_id, "items": db.get_timeline(run_id)}

    @app.get("/v1/runs/{run_id}/inspector")
    async def run_inspector(run_id: str):
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

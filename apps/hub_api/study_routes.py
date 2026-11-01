from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.query_views.studies import (
    AdmissionListView,
    StudyListView,
    StudyQueryService,
    StudyReportComparisonBindingError,
    StudyReportComparisonView,
    StudyTimelineView,
    StudyView,
)


def study_router(database: Database) -> APIRouter:
    router = APIRouter(tags=["studies"])
    queries = StudyQueryService(database)

    @router.get("/v1/studies", response_model=StudyListView)
    def list_studies(
        market: str | None = None,
        q: str | None = Query(None, max_length=300),
        view: Literal["research", "sources", "all"] = "all",
        limit: int = Query(30, ge=1, le=100),
        offset: int = Query(0, ge=0),
    ):
        return queries.list(market=market, q=q, view=view, limit=limit, offset=offset)

    @router.get("/v1/studies/{study_id}", response_model=StudyTimelineView)
    def timeline(study_id: str, limit: int = Query(30, ge=1, le=100), offset: int = Query(0, ge=0)):
        try:
            return queries.timeline(study_id, limit=limit, offset=offset)
        except KeyError as exc:
            raise HTTPException(404, "study_not_found") from exc

    @router.get(
        "/v1/studies/{study_id}/revisions/{revision_id}/comparison",
        response_model=StudyReportComparisonView,
    )
    def report_comparison(study_id: str, revision_id: str):
        try:
            return queries.comparison(study_id, revision_id)
        except KeyError as exc:
            raise HTTPException(404, "study_report_revision_not_found") from exc
        except StudyReportComparisonBindingError as exc:
            raise HTTPException(409, "study_report_comparison_binding_invalid") from exc

    @router.get("/v1/runs/{run_id}/study", response_model=StudyView | None)
    def for_run(run_id: str):
        try:
            return queries.for_run(run_id)
        except KeyError as exc:
            raise HTTPException(404, "run_not_found") from exc

    @router.get("/v1/discovery/decisions", response_model=AdmissionListView)
    def decisions(
        source_id: str | None = None,
        study_id: str | None = None,
        decision: Literal["admitted", "merged", "blocked", "ignored"] | None = None,
        limit: int = Query(30, ge=1, le=100),
        offset: int = Query(0, ge=0),
    ):
        return queries.decisions(
            source_id=source_id, study_id=study_id, decision=decision, limit=limit, offset=offset
        )

    return router

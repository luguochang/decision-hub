from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import AwareDatetime

from packages.contracts_py.decision_hub_contracts.generated.analytical_outlook import (
    AnalyticalCohortListView,
    AnalyticalOutlookDetailView,
    AnalyticalOutlookListView,
    PublicationSeparationView,
)
from packages.evals.analytical_assessment import AnalyticalAssessment
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.provider_adapters.research.raw_capture import RawBodyArchive
from packages.query_views.analytical_outlooks import AnalyticalOutlookQuery


def outlook_router(
    database: Database, data_root: Path | None = None, *, background_assessments: bool = True,
) -> APIRouter:
    router = APIRouter(tags=["analytical-outlooks"])
    service = AnalyticalOutlookQuery(
        database, archive=RawBodyArchive(data_root / "raw-captures") if data_root else None,
        background_assessments=background_assessments,
    )

    @router.get("/v1/analytical-outlooks", response_model=AnalyticalOutlookListView)
    def outlooks(
        run_id: str | None = None,
        study_id: str | None = None,
        market: str | None = None,
        instrument: str | None = None,
        horizon: str | None = None,
        status: str | None = None,
        method_family: str | None = None,
        method_version: str | None = None,
        stable_recipe_hash: str | None = Query(
            None, min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$",
        ),
        recipe_status: Literal["unchecked"] | None = None,
        action: str | None = None,
        research_quality: str | None = None,
        quality_status: Literal["observed", "estimated", "unlabelled"] | None = None,
        cohort: str | None = None,
        since: AwareDatetime | None = None,
        until: AwareDatetime | None = None,
        limit: int = Query(30, ge=1, le=100),
        offset: int = Query(0, ge=0),
    ):
        if stable_recipe_hash is not None and recipe_status is not None:
            raise HTTPException(400, "analytical_recipe_filters_conflict")
        if ((stable_recipe_hash is not None or recipe_status is not None)
            and not background_assessments):
            raise HTTPException(400, "analytical_recipe_filter_requires_background")
        return service.list(
            run_id=run_id,
            study_id=study_id,
            market=market,
            instrument=instrument,
            horizon=horizon,
            status=status,
            method_family=method_family,
            method_version=method_version,
            stable_recipe_hash=stable_recipe_hash,
            recipe_status=recipe_status,
            action=action,
            research_quality=research_quality,
            quality_status=quality_status,
            cohort=cohort,
            since=since,
            until=until,
            limit=limit,
            offset=offset,
        )

    @router.get(
        "/v1/analytical-outlooks/cohorts", response_model=AnalyticalCohortListView,
        response_model_exclude_unset=True,
    )
    def cohorts(
        run_id: str | None = None,
        study_id: str | None = None,
        horizon: str | None = None,
        status: str | None = None,
        market: str | None = None,
        instrument: str | None = None,
        method_family: str | None = None,
        method_version: str | None = None,
        stable_recipe_hash: str | None = Query(
            None, min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$",
        ),
        recipe_status: Literal["unchecked"] | None = None,
        action: str | None = None,
        research_quality: str | None = None,
        quality_status: Literal["observed", "estimated", "unlabelled"] | None = None,
        cohort: str | None = None,
        since: AwareDatetime | None = None,
        until: AwareDatetime | None = None,
        limit: int = Query(30, ge=1, le=100),
        offset: int = Query(0, ge=0),
    ):
        if stable_recipe_hash is not None and recipe_status is not None:
            raise HTTPException(400, "analytical_recipe_filters_conflict")
        if ((stable_recipe_hash is not None or recipe_status is not None)
            and not background_assessments):
            raise HTTPException(400, "analytical_recipe_filter_requires_background")
        return service.cohorts(
            run_id=run_id,
            study_id=study_id,
            horizon=horizon,
            status=status,
            market=market,
            instrument=instrument,
            method_family=method_family,
            method_version=method_version,
            stable_recipe_hash=stable_recipe_hash,
            recipe_status=recipe_status,
            action=action,
            research_quality=research_quality,
            quality_status=quality_status,
            cohort=cohort,
            since=since,
            until=until,
            limit=limit,
            offset=offset,
        )

    @router.get(
        "/v1/analytical-outlooks/{outlook_id}/assessment", response_model=AnalyticalAssessment
    )
    def assessment(outlook_id: str):
        try:
            return service.assessment(outlook_id)
        except KeyError as exc:
            raise HTTPException(404, "analytical_outlook_not_found") from exc

    @router.get("/v1/analytical-outlooks/{outlook_id}", response_model=AnalyticalOutlookDetailView)
    def detail(outlook_id: str, limit: int = Query(30, ge=1, le=100), offset: int = Query(0, ge=0)):
        try:
            return service.get(outlook_id, limit=limit, offset=offset)
        except KeyError as exc:
            raise HTTPException(404, "analytical_outlook_not_found") from exc

    @router.get("/v1/artifacts/{artifact_id}/publication", response_model=PublicationSeparationView)
    def publication(artifact_id: str):
        try:
            return service.publication(artifact_id)
        except KeyError as exc:
            raise HTTPException(404, "publication_projection_not_recorded") from exc

    return router

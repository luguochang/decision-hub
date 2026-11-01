"""Actual saved research memory. Diagnostic records grant no model qualification."""

from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import AwareDatetime, BaseModel, ConfigDict
from sqlalchemy import select

from packages.kernel.decision_hub_kernel.application.market_case import MarketCaseService
from packages.kernel.decision_hub_kernel.persistence.db import Database, utcnow
from packages.kernel.decision_hub_kernel.persistence.market_case_records import MarketCaseRecord
from packages.kernel.decision_hub_kernel.ports.market_case import MarketCase
from packages.kernel.decision_hub_kernel.ports.market_path import spot_scope
from packages.provider_adapters.research.raw_capture import RawBodyArchive


class CaseSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str
    run_id: str
    revision_id: str
    instrument: str
    horizon: str
    classification: Literal[
        "verified_market_case", "product_feedback", "operational_failure", "model_error",
        "research_diagnostic",
    ]
    available_at: AwareDatetime
    original_cutoff_at: AwareDatetime | None
    label_known_at: AwareDatetime | None
    mechanism: str | None
    counter_evidence: tuple[str, ...]
    unresolved_questions: tuple[str, ...]
    exclusion_reasons: tuple[str, ...]
    stored_eligible: bool
    current_qualification: Literal["not_reassessed"] = "not_reassessed"


class CasesView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["market-cases-view.v1"] = "market-cases-view.v1"
    scope_id: str
    as_of: AwareDatetime
    integrity: Literal["stored_payload_hash"] = "stored_payload_hash"
    items: list[CaseSummary]
    has_more: bool


def market_memory_router(database: Database, data_root: Path, *, clock=utcnow) -> APIRouter:
    router = APIRouter(tags=["market-memory"])
    service = MarketCaseService(database, RawBodyArchive(data_root / "raw-captures"), clock=clock)
    scopes = {spot_scope(x).scope_id: x for x in ("BTC-USDT", "ETH-USDT")}

    @router.get("/v1/risk/scopes/{scope_id}/cases", response_model=CasesView)
    def cases(
        scope_id: str, limit: int = Query(10, ge=1, le=50), offset: int = Query(0, ge=0, le=10000)
    ):
        if scope_id not in scopes:
            raise HTTPException(404, "risk_scope_not_found")
        now: datetime = clock()
        with database.session() as session:
            rows = session.scalars(
                select(MarketCaseRecord)
                .where(
                    MarketCaseRecord.market == "crypto",
                    MarketCaseRecord.instrument == scopes[scope_id],
                    MarketCaseRecord.available_at <= now,
                )
                .order_by(MarketCaseRecord.available_at.desc(), MarketCaseRecord.case_id)
                .offset(offset)
                .limit(limit + 1)
            ).all()
            try:
                originals = [service._decode(row) for row in rows[:limit]]
            except ValueError as error:
                raise HTTPException(409, "market_case_original_invalid") from error
        return CasesView(
            scope_id=scope_id,
            as_of=now,
            has_more=len(rows) > limit,
            items=[
                CaseSummary(
                    case_id=x.case_id,
                    run_id=x.run_id,
                    revision_id=x.revision_id,
                    instrument=x.scope.instrument,
                    horizon=x.scope.horizon,
                    classification=x.classification,
                    available_at=x.available_at,
                    original_cutoff_at=x.original_cutoff_at,
                    label_known_at=x.label_known_at,
                    mechanism=x.mechanism,
                    counter_evidence=x.counter_evidence,
                    unresolved_questions=x.unresolved_questions,
                    exclusion_reasons=x.exclusion_reasons,
                    stored_eligible=x.eligible,
                )
                for x in originals
            ],
        )

    @router.get("/v1/risk/cases/{case_id}", response_model=MarketCase)
    def original(case_id: str):
        try:
            result = service.read(case_id)
            if result.available_at > clock():
                raise HTTPException(409, "market_case_not_yet_available")
            return result
        except ValueError as error:
            status = 404 if str(error) == "market_case_not_found" else 409
            raise HTTPException(status, str(error)) from error

    return router

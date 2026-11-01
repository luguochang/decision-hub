"""Read saved enhancement candidates/results; GET never registers, evaluates or fetches."""

from datetime import date
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import AwareDatetime, Field
from sqlalchemy import select

from apps.hub_worker.market_enhancement_bootstrap import (
    ROUTES,
    SCHEMA,
    EnhancementJobReceipt,
    read_enhancement_receipt,
    receipt_run_id,
)
from packages.kernel.decision_hub_kernel.application.market_enhancements import FeatureValue
from packages.kernel.decision_hub_kernel.application.market_extension_registry import (
    MarketExtensionRegistry,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    RunRoleOutputRecord,
    as_utc,
    utcnow,
)
from packages.kernel.decision_hub_kernel.persistence.market_case_records import (
    MarketExtensionRegistrationRecord,
)
from packages.kernel.decision_hub_kernel.ports.market_extension import InputRouteManifest
from packages.kernel.decision_hub_kernel.ports.market_path import (
    FrozenContract,
    aware_utc,
    spot_scope,
)


class EnhancementRouteView(FrozenContract):
    route: Literal["MACRO_STATE_V1", "CROWDING_V1", "EVENT_SURPRISE_V1"]
    registration_id: str | None
    registered_at: AwareDatetime | None
    data_status: Literal["not_registered", "awaiting_data", "available", "partial", "unavailable"]
    admission_state: Literal[
        "not_assessed", "not_admitted", "descriptive_only", "research_qualified"
    ]
    receipt_id: str | None
    saved_at: AwareDatetime | None
    cutoff_at: AwareDatetime | None
    source_known_at: AwareDatetime | None
    observation_date: date | None
    features: tuple[FeatureValue, ...]
    missing_reasons: tuple[str, ...]
    original_count: int = Field(ge=0)
    excluded_source_count: int = Field(ge=0)
    scan_truncated: bool
    data_pit_verified: bool | None
    probability: None = None
    probability_qualified: Literal[False] = False
    statistical_status: Literal["insufficient_evidence"] = "insufficient_evidence"
    promotion: Literal[False] = False


class MarketEnhancementsView(FrozenContract):
    schema_version: Literal["market-enhancements-view.v1"] = "market-enhancements-view.v1"
    scope_id: str
    instrument: Literal["BTC-USDT", "ETH-USDT"]
    as_of: AwareDatetime
    integrity: Literal["stored_payload_hash"] = "stored_payload_hash"
    routes: tuple[EnhancementRouteView, ...] = Field(min_length=3, max_length=3)


def market_enhancement_router(database: Database, *, clock=utcnow) -> APIRouter:
    router = APIRouter(tags=["market-enhancements"])
    scopes = {spot_scope(x).scope_id: x for x in ("BTC-USDT", "ETH-USDT")}

    @router.get("/v1/risk/scopes/{scope_id}/enhancements", response_model=MarketEnhancementsView)
    def summary(scope_id: str):
        if scope_id not in scopes:
            raise HTTPException(404, "risk_scope_not_found")
        now = aware_utc(clock())
        views = []
        with database.session() as session:
            registrations = session.scalars(
                select(MarketExtensionRegistrationRecord)
                .where(
                    MarketExtensionRegistrationRecord.scope_id == scope_id,
                    MarketExtensionRegistrationRecord.registered_at <= now,
                )
                .order_by(
                    MarketExtensionRegistrationRecord.registered_at.desc(),
                    MarketExtensionRegistrationRecord.registration_id,
                )
                .limit(65)
            ).all()
            if len(registrations) > 64:
                raise HTTPException(409, "enhancement_registration_scan_limit")
            by_route = {}
            for candidate in registrations:
                try:
                    manifest = MarketExtensionRegistry._manifest(candidate)
                except ValueError as error:
                    raise HTTPException(409, "enhancement_registration_original_invalid") from error
                if isinstance(manifest, InputRouteManifest) and manifest.route in ROUTES:
                    by_route.setdefault(manifest.route, candidate)
            for route in ROUTES:
                registration = by_route.get(route)
                row = session.scalar(
                    select(RunRoleOutputRecord)
                    .where(
                        RunRoleOutputRecord.run_id == receipt_run_id(scope_id),
                        RunRoleOutputRecord.schema_version == SCHEMA,
                        RunRoleOutputRecord.created_at <= now,
                        RunRoleOutputRecord.role.like("enhance_" + route.lower() + "_%"),
                    )
                    .order_by(RunRoleOutputRecord.created_at.desc(), RunRoleOutputRecord.output_id)
                    .limit(1)
                )
                value = None
                if row is not None:
                    try:
                        value = read_enhancement_receipt(row)
                        if value.result.route != route:
                            raise ValueError("enhancement_saved_route_changed")
                        if (
                            not registration
                            or value.registration_id != registration.registration_id
                        ):
                            value = None
                        elif value.manifest_hash != registration.manifest_hash:
                            raise ValueError("enhancement_registration_binding_changed")
                    except ValueError as error:
                        raise HTTPException(409, "enhancement_saved_original_invalid") from error
                reasons = (
                    value.result.missing_reasons
                    if value
                    else (
                        "enhancement_inputs_not_collected"
                        if registration
                        else "enhancement_not_registered",
                    )
                )
                if value and value.excluded_fred_versions:
                    reasons = (*reasons, "enhancement_fred_originals_excluded")
                views.append(
                    EnhancementRouteView(
                        route=route,
                        registration_id=registration.registration_id if registration else None,
                        registered_at=as_utc(registration.registered_at) if registration else None,
                        data_status=value.result.status
                        if value
                        else ("awaiting_data" if registration else "not_registered"),
                        admission_state=value.admission.state if value else "not_assessed",
                        receipt_id=value.receipt_id if value else None,
                        saved_at=value.saved_at if value else None,
                        cutoff_at=value.cutoff_at if value else None,
                        source_known_at=max((b.known_at for b in value.fred_bindings), default=None)
                        if value
                        else None,
                        observation_date=max(
                            (b.observation_date for b in value.fred_bindings), default=None
                        )
                        if value
                        else None,
                        features=value.result.features if value else (),
                        missing_reasons=reasons,
                        original_count=len(value.result.original_refs) if value else 0,
                        excluded_source_count=value.excluded_fred_versions if value else 0,
                        scan_truncated=value.scan_truncated if value else False,
                        data_pit_verified=value.result.data_pit_verified if value else None,
                    )
                )
        return MarketEnhancementsView(
            scope_id=scope_id,
            instrument=scopes[scope_id],
            as_of=now,
            routes=tuple(views),
        )

    @router.get("/v1/risk/enhancements/receipts/{receipt_id}", response_model=EnhancementJobReceipt)
    def original(receipt_id: str):
        with database.session() as session:
            row = session.get(RunRoleOutputRecord, receipt_id)
            if row is None or row.schema_version != SCHEMA:
                raise HTTPException(404, "enhancement_receipt_not_found")
            try:
                value = read_enhancement_receipt(row)
                if value.saved_at > aware_utc(clock()):
                    raise HTTPException(409, "enhancement_receipt_not_yet_available")
                return value
            except ValueError as error:
                raise HTTPException(409, "enhancement_saved_original_invalid") from error

    return router

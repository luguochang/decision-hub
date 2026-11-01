"""PC forecast warnings: reading never records delivery or visibility."""

from collections.abc import Callable
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from packages.kernel.decision_hub_kernel.application.forecast_alarm import ForecastAlarmService
from packages.kernel.decision_hub_kernel.application.market_path_store import MarketPathStore
from packages.kernel.decision_hub_kernel.persistence.db import Database, utcnow
from packages.kernel.decision_hub_kernel.ports.forecast_alarm import (
    ForecastAlarmListV1,
    ForecastAlarmReceiptV1,
)


class DisplayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    owner_id: str = Field(min_length=1, max_length=128)
    request_id: str = Field(min_length=1, max_length=128)


class ExposureRequest(DisplayRequest):
    display_receipt_id: str
    visible: Literal[True]
    surface: Literal["pc"]
    visible_duration_ms: int = Field(ge=1000, le=86_400_000)


class AcknowledgeRequest(DisplayRequest):
    exposure_receipt_id: str


def forecast_alarm_router(
    database: Database,
    path_store: MarketPathStore | None = None,
    *,
    clock: Callable[[], datetime] = utcnow,
    require_owner: Callable[[str | None, str | None], str] | None = None,
) -> APIRouter:
    router = APIRouter(tags=["forecast-alarms"])
    service = ForecastAlarmService(database, path_store, clock=clock)

    def owner(
        header: str | None,
        declared: str | None,
        key: str | None = None,
        request_id: str | None = None,
    ) -> str:
        if require_owner is not None:
            verified = require_owner(header, declared)
        else:
            if not header or declared is not None and header != declared:
                raise HTTPException(403, "owner_identity_required_or_mismatch")
            verified = header
        if not verified or declared is not None and verified != declared:
            raise HTTPException(403, "owner_identity_mismatch")
        if key is not None and key != request_id:
            raise HTTPException(409, "forecast_alarm_idempotency_key_mismatch")
        return verified

    def failure(error: Exception) -> HTTPException:
        return HTTPException(
            404 if isinstance(error, KeyError) else 409,
            "forecast_alarm_not_found" if isinstance(error, KeyError) else str(error),
        )

    @router.get("/v1/risk/scopes/{scope_id}/forecast-alarms", response_model=ForecastAlarmListV1)
    def alarms(
        scope_id: str,
        owner_id: str | None = Query(None, max_length=128),
        limit: int = Query(50, ge=1, le=100),
        x_owner_id: str | None = Header(None, alias="X-Owner-Id"),
    ) -> ForecastAlarmListV1:
        verified = owner(x_owner_id, owner_id)
        try:
            return service.list(scope_id, as_of=clock(), owner_id=verified, limit=limit)
        except (ValueError, KeyError) as error:
            raise failure(error) from error

    @router.post(
        "/v1/risk/forecast-alarms/{alarm_id}/display", response_model=ForecastAlarmReceiptV1
    )
    def display(
        alarm_id: str,
        body: DisplayRequest,
        x_owner_id: str | None = Header(None, alias="X-Owner-Id"),
        idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    ) -> ForecastAlarmReceiptV1:
        owner(x_owner_id, body.owner_id, idempotency_key, body.request_id)
        try:
            return service.receipt(
                alarm_id,
                owner_id=body.owner_id,
                request_id=body.request_id,
                receipt_kind="displayed",
            )
        except (ValueError, KeyError) as error:
            raise failure(error) from error

    @router.post(
        "/v1/risk/forecast-alarms/{alarm_id}/exposure", response_model=ForecastAlarmReceiptV1
    )
    def exposure(
        alarm_id: str,
        body: ExposureRequest,
        x_owner_id: str | None = Header(None, alias="X-Owner-Id"),
        idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    ) -> ForecastAlarmReceiptV1:
        owner(x_owner_id, body.owner_id, idempotency_key, body.request_id)
        try:
            return service.receipt(
                alarm_id,
                owner_id=body.owner_id,
                request_id=body.request_id,
                receipt_kind="visible",
                parent_receipt_id=body.display_receipt_id,
                visible_duration_ms=body.visible_duration_ms,
            )
        except (ValueError, KeyError) as error:
            raise failure(error) from error

    @router.post(
        "/v1/risk/forecast-alarms/{alarm_id}/acknowledge", response_model=ForecastAlarmReceiptV1
    )
    def acknowledge(
        alarm_id: str,
        body: AcknowledgeRequest,
        x_owner_id: str | None = Header(None, alias="X-Owner-Id"),
        idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    ) -> ForecastAlarmReceiptV1:
        owner(x_owner_id, body.owner_id, idempotency_key, body.request_id)
        try:
            return service.receipt(
                alarm_id,
                owner_id=body.owner_id,
                request_id=body.request_id,
                receipt_kind="acknowledged",
                parent_receipt_id=body.exposure_receipt_id,
            )
        except (ValueError, KeyError) as error:
            raise failure(error) from error

    return router

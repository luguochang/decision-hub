from collections.abc import Callable

from fastapi import APIRouter, Header, HTTPException, Response

from packages.contracts_py.decision_hub_contracts.generated.study_effect_tracking import (
    StudyEffectTrackingCommand,
    StudyEffectTrackingReceipt,
    StudyEffectTrackingRequestStatus,
    StudyEffectTrackingStatus,
)
from packages.kernel.decision_hub_kernel.application.study_effect_tracking import StudyEffectTrackingService
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.provider_adapters.research.raw_capture import RawBodyArchive


def study_effect_tracking_router(
    database: Database,
    require_owner: Callable[[str | None], str],
    archive: RawBodyArchive | None,
) -> APIRouter:
    router = APIRouter(tags=["study-effect-tracking"])
    service = StudyEffectTrackingService(database, archive=archive) if archive is not None else None

    def available() -> StudyEffectTrackingService:
        if service is None:
            raise HTTPException(503, "effect_tracking_archive_unavailable")
        return service

    def failure(error: Exception) -> HTTPException:
        if isinstance(error, PermissionError):
            return HTTPException(403, str(error))
        if isinstance(error, KeyError):
            return HTTPException(404, error.args[0])
        return HTTPException(409, str(error))

    @router.post("/v1/studies/{study_id}/effect-tracking", response_model=StudyEffectTrackingReceipt)
    def start(
        study_id: str,
        payload: StudyEffectTrackingCommand,
        x_owner_id: str | None = Header(None, max_length=128),
        idempotency_key: str | None = Header(None, max_length=128),
    ) -> StudyEffectTrackingReceipt:
        owner = require_owner(x_owner_id)
        if idempotency_key != payload.request_id:
            raise HTTPException(400, "idempotency_key_mismatch")
        try:
            return available().start(study_id, owner, payload)
        except (PermissionError, KeyError, ValueError, TypeError) as error:
            raise failure(error) from error

    @router.get("/v1/studies/{study_id}/effect-tracking/commands/{request_id}", response_model=StudyEffectTrackingReceipt)
    def receipt(
        study_id: str,
        request_id: str,
        x_owner_id: str | None = Header(None, max_length=128),
    ) -> StudyEffectTrackingReceipt:
        owner = require_owner(x_owner_id)
        try:
            result = available().get_receipt(study_id, owner, request_id)
        except (PermissionError, KeyError, ValueError, TypeError) as error:
            raise failure(error) from error
        if result is None:
            raise HTTPException(404, "effect_tracking_command_not_found")
        return result

    @router.get("/v1/studies/{study_id}/effect-tracking", response_model=StudyEffectTrackingStatus)
    def status(
        study_id: str,
        x_owner_id: str | None = Header(None, max_length=128),
    ) -> StudyEffectTrackingStatus:
        owner = require_owner(x_owner_id)
        try:
            return available().status(study_id, owner)
        except (PermissionError, KeyError, ValueError, TypeError) as error:
            raise failure(error) from error

    @router.post("/v1/studies/{study_id}/effect-tracking/requests",
                 response_model=StudyEffectTrackingRequestStatus, status_code=202,
                 responses={200: {"model": StudyEffectTrackingRequestStatus}})
    def admit_request(
        study_id: str, payload: StudyEffectTrackingCommand, response: Response,
        x_owner_id: str | None = Header(None, max_length=128),
        idempotency_key: str | None = Header(None, max_length=128),
    ) -> StudyEffectTrackingRequestStatus:
        owner = require_owner(x_owner_id)
        if idempotency_key != payload.request_id:
            raise HTTPException(400, "idempotency_key_mismatch")
        try:
            result = available().admit_request(study_id, owner, payload)
        except (PermissionError, KeyError, ValueError, TypeError) as error:
            raise failure(error) from error
        if result.state == "completed":
            response.status_code = 200
        return result

    @router.get("/v1/studies/{study_id}/effect-tracking/requests/{request_id}",
                response_model=StudyEffectTrackingRequestStatus)
    def request_status(
        study_id: str, request_id: str,
        x_owner_id: str | None = Header(None, max_length=128),
    ) -> StudyEffectTrackingRequestStatus:
        owner = require_owner(x_owner_id)
        try:
            result = available().get_request(study_id, owner, request_id)
        except (PermissionError, KeyError, ValueError, TypeError) as error:
            raise failure(error) from error
        if result is None:
            raise HTTPException(404, "effect_tracking_request_not_found")
        return result

    return router

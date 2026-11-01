"""Existing local owner boundary and identity-only paper commands."""

from collections.abc import Callable

from fastapi import APIRouter, Header, HTTPException, Query
from packages.kernel.decision_hub_kernel.application.action_paper_product import (
    ActionPaperProductService,
)
from packages.kernel.decision_hub_kernel.ports.action_paper_product import (
    Horizon,
    PaperOptions,
    PaperRegistrationCommand,
    PaperRegistrationReceipt,
    PaperStatus,
)
from sqlalchemy.exc import SQLAlchemyError


def action_paper_router(
    service: ActionPaperProductService, require_owner: Callable[[str | None], str]
):
    router = APIRouter(prefix="/v1/actions/paper", tags=["paper-diagnostic"])

    def owner(value: str | None) -> str:
        identity = require_owner(value)
        if not identity or identity.strip() != identity or identity.startswith("system:"):
            raise HTTPException(403, "paper_owner_invalid")
        return identity

    def call[T](operation: Callable[[], T]) -> T:
        try:
            return operation()
        except ValueError as error:
            code = str(error)
            if len(code) > 160 or not code.replace("_", "").isalnum():
                code = "paper_original_or_contract_invalid"
            raise HTTPException(
                404 if code in {"paper_registration_missing", "paper_outcome_missing"} else 409,
                code,
            ) from error
        except SQLAlchemyError as error:
            raise HTTPException(503, "paper_storage_unavailable") from error

    @router.get("/options", response_model=PaperOptions)
    def options(scope_id: str, horizon: Horizon, x_owner_id: str | None = Header(None)):
        return call(lambda: service.options(owner(x_owner_id), scope_id, horizon))

    @router.post("/registrations", response_model=PaperRegistrationReceipt)
    def register(command: PaperRegistrationCommand, x_owner_id: str | None = Header(None)):
        return call(lambda: service.register(owner(x_owner_id), command))

    @router.get("/registrations/{request_id}", response_model=PaperRegistrationReceipt)
    def receipt(request_id: str, x_owner_id: str | None = Header(None)):
        return call(lambda: service.receipt(owner(x_owner_id), request_id))

    @router.get("/registrations/{request_id}/status", response_model=PaperStatus)
    def status(request_id: str, x_owner_id: str | None = Header(None)):
        return call(lambda: service.status(owner(x_owner_id), request_id))

    @router.get("/registrations/{request_id}/outcomes/{outcome_id}")
    def outcome(request_id: str, outcome_id: str, x_owner_id: str | None = Header(None)):
        return call(lambda: service.outcome(owner(x_owner_id), request_id, outcome_id))

    @router.get("/registrations/{request_id}/outcomes")
    def outcomes(
        request_id: str, after: str | None = Query(None), x_owner_id: str | None = Header(None)
    ):
        return call(lambda: service.outcomes(owner(x_owner_id), request_id, after=after))

    @router.get("/registrations/{request_id}/opportunities")
    def opportunities(
        request_id: str, after: str | None = Query(None), x_owner_id: str | None = Header(None)
    ):
        return call(lambda: service.opportunities(owner(x_owner_id), request_id, after=after))

    return router

"""Child routes sharing the settings signed owner/Origin/CSRF authority."""

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool

from packages.kernel.decision_hub_kernel.application.perpetual_research_horizon import (
    parse_perpetual_research_command,
)


def perpetual_research_entry_router(service, *, owner):
    router = APIRouter()

    @router.post("/research", response_model=None)
    async def create(request: Request, response: Response):
        identity = owner(request, response, True)
        try:
            command = parse_perpetual_research_command(await request.json())
        except (ValidationError, ValueError) as exc:
            raise HTTPException(422, "perpetual_research_command_invalid") from exc
        if request.headers.get("idempotency-key") != command.request_id:
            raise HTTPException(400, "idempotency_key_mismatch")
        try:
            return await service.create(identity, command)
        except PermissionError as exc:
            raise HTTPException(403, "perpetual_research_owner_or_account_mismatch") from exc
        except (httpx.HTTPError, OSError) as exc:
            raise HTTPException(409, "perpetual_research_source_unavailable") from exc
        except ValueError as exc:
            value = str(exc)
            code = (
                value
                if value.replace("_", "").isalnum() and len(value) <= 160
                else "perpetual_research_original_unavailable"
            )
            raise HTTPException(409, code) from exc
        except SQLAlchemyError as exc:
            raise HTTPException(503, "perpetual_research_commit_outcome_unknown") from exc

    @router.post("/research/commands/{request_id}/close", response_model=None)
    async def close_unaccepted(request_id: str, request: Request, response: Response):
        identity = owner(request, response, True)
        try:
            command = parse_perpetual_research_command(await request.json())
        except (ValidationError, ValueError) as exc:
            raise HTTPException(422, "perpetual_research_command_invalid") from exc
        if request_id != command.request_id or request.headers.get("idempotency-key") != request_id:
            raise HTTPException(400, "idempotency_key_mismatch")
        try:
            return await run_in_threadpool(service.close_unaccepted, identity, command)
        except ValueError as exc:
            code = str(exc)
            raise HTTPException(
                409,
                code
                if code.replace("_", "").isalnum() and len(code) <= 160
                else "perpetual_research_original_unavailable",
            ) from exc
        except SQLAlchemyError as exc:
            raise HTTPException(503, "perpetual_research_closure_outcome_unknown") from exc

    @router.get("/research/commands/{request_id}", response_model=None)
    async def receipt(request_id: str, request: Request, response: Response):
        identity = owner(request, response, False)
        try:
            result = await run_in_threadpool(service.receipt, identity, request_id)
            if result is None:
                raise HTTPException(404, "perpetual_research_receipt_missing")
            return result
        except ValueError as exc:
            raise HTTPException(409, "perpetual_research_command_original_invalid") from exc

    return router

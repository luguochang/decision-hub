"""Signed owner consent for periodic research; no models or collection in commands."""

from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
    DecisionSettingsPartitionV1,
)
from packages.kernel.decision_hub_kernel.ports.perpetual_research_schedule import (
    parse_perpetual_research_schedule_command,
)


def perpetual_research_schedule_router(service, *, owner):
    router = APIRouter()

    @router.post("/research/schedules/commands", response_model=None)
    async def save(request: Request, response: Response):
        identity = owner(request, response, True)
        try:
            command = parse_perpetual_research_schedule_command(await request.json())
        except (ValueError, ValidationError) as exc:
            raise HTTPException(422, "perpetual_schedule_command_invalid") from exc
        if request.headers.get("idempotency-key") != command.request_id:
            raise HTTPException(400, "idempotency_key_mismatch")
        try:
            return await run_in_threadpool(service.save, identity, command)
        except ValueError as exc:
            code = str(exc)
            raise HTTPException(
                409,
                code
                if code.replace("_", "").isalnum()
                else "perpetual_schedule_original_unavailable",
            ) from exc

    @router.get("/research/schedules/commands/{request_id}", response_model=None)
    async def receipt(request_id: str, request: Request, response: Response):
        identity = owner(request, response, False)
        try:
            original = await run_in_threadpool(service.receipt, identity, request_id)
            if original is None:
                raise HTTPException(404, "perpetual_schedule_receipt_missing")
            return original
        except ValueError as exc:
            raise HTTPException(409, "perpetual_schedule_original_unavailable") from exc

    @router.get("/research/schedules/current", response_model=None)
    async def current(
        request: Request,
        response: Response,
        account_kind: Literal["paper", "user_declared"],
        account_id: str,
        instrument_id: str,
        profile_ref: str,
        horizon: Literal["4h", "24h", "72h"],
    ):
        identity = owner(request, response, False)
        try:
            partition = DecisionSettingsPartitionV1(
                account_kind=account_kind,
                account_id=account_id,
                instrument_id=instrument_id,
                profile_ref=profile_ref,
            )
            return await run_in_threadpool(service.current, identity, partition, horizon)
        except ValidationError as exc:
            raise HTTPException(422, "perpetual_schedule_partition_invalid") from exc
        except ValueError as exc:
            raise HTTPException(409, "perpetual_schedule_original_unavailable") from exc

    return router

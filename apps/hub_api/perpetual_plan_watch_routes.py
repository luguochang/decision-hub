"""Signed exact-revision watch consent. Consent is never execution permission."""

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
    DecisionSettingsPartitionV1,
    DecisionSourceRefV1,
    PerpetualPlanWatchCommandV1,
)
from packages.kernel.decision_hub_kernel.ports.perpetual_plan_watch import (
    parse_continuous_watch_command,
)


def perpetual_plan_watch_router(service, *, owner):
    router = APIRouter()

    @router.post("/plans/watches/continuous/commands", response_model=None)
    async def continuous_save(request: Request, response: Response):
        identity = owner(request, response, True)
        try:
            command = parse_continuous_watch_command(await request.json())
        except (ValueError, ValidationError) as exc:
            raise HTTPException(422, "perpetual_continuous_command_invalid") from exc
        if request.headers.get("idempotency-key") != command.request_id:
            raise HTTPException(400, "idempotency_key_mismatch")
        try:
            return await run_in_threadpool(service.save_continuous, identity, command)
        except ValueError as exc:
            raise HTTPException(409, "perpetual_continuous_original_or_state_changed") from exc

    @router.get("/plans/watches/continuous/commands/{request_id}", response_model=None)
    async def continuous_receipt(request_id: str, request: Request, response: Response):
        identity = owner(request, response, False)
        try:
            value = await run_in_threadpool(service.receipt_continuous, identity, request_id)
            if value is None:
                raise HTTPException(404, "perpetual_continuous_receipt_missing")
            return value
        except ValueError as exc:
            raise HTTPException(409, "perpetual_continuous_original_unavailable") from exc

    @router.get("/plans/watches/continuous/current", response_model=None)
    async def continuous_current(
        request: Request,
        response: Response,
        account_kind: str,
        account_id: str,
        instrument_id: str,
        profile_ref: str,
        horizon: str,
        settings_ref: str,
        settings_hash: str,
    ):
        identity = owner(request, response, False)
        try:
            partition = DecisionSettingsPartitionV1(
                account_kind=account_kind,
                account_id=account_id,
                instrument_id=instrument_id,
                profile_ref=profile_ref,
            )
            settings = DecisionSourceRefV1(ref=settings_ref, hash=settings_hash)
            return await run_in_threadpool(
                service.current_continuous, identity, partition, horizon, settings
            )
        except ValidationError as exc:
            raise HTTPException(422, "perpetual_continuous_query_invalid") from exc
        except ValueError as exc:
            raise HTTPException(409, "perpetual_continuous_original_unavailable") from exc

    @router.post("/plans/watches/commands", response_model=None)
    async def save(request: Request, response: Response):
        identity = owner(request, response, True)
        try:
            command = PerpetualPlanWatchCommandV1.model_validate(await request.json())
        except (ValueError, ValidationError) as exc:
            raise HTTPException(422, "perpetual_watch_command_invalid") from exc
        if request.headers.get("idempotency-key") != command.request_id:
            raise HTTPException(400, "idempotency_key_mismatch")
        try:
            return await run_in_threadpool(service.save, identity, command)
        except ValueError as exc:
            raise HTTPException(409, "perpetual_watch_original_or_state_changed") from exc

    @router.get("/plans/watches/commands/{request_id}", response_model=None)
    async def receipt(request_id: str, request: Request, response: Response):
        identity = owner(request, response, False)
        try:
            value = await run_in_threadpool(service.receipt, identity, request_id)
            if value is None:
                raise HTTPException(404, "perpetual_watch_receipt_missing")
            return value
        except ValueError as exc:
            raise HTTPException(409, "perpetual_watch_original_unavailable") from exc

    @router.get("/plans/watches/current", response_model=None)
    async def current(
        request: Request, response: Response, plan_id: str, revision_ref: str, revision_hash: str
    ):
        identity = owner(request, response, False)
        try:
            reference = DecisionSourceRefV1(ref=revision_ref, hash=revision_hash)
            if not 1 <= len(plan_id) <= 128:
                raise ValueError("plan_id_invalid")
            return await run_in_threadpool(
                service.current, identity, plan_id, reference.model_dump(mode="json")
            )
        except ValidationError as exc:
            raise HTTPException(422, "perpetual_watch_query_invalid") from exc
        except ValueError as exc:
            raise HTTPException(409, "perpetual_watch_original_unavailable") from exc

    return router

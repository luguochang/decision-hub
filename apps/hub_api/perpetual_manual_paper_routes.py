"""Signed manual simulation; no price/fill injection or real-order transport."""

import os

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from packages.kernel.decision_hub_kernel.ports.action_quote import decode_json
from packages.kernel.decision_hub_kernel.ports.perpetual_manual_execution import (
    MANUAL_HORIZONS,
    MANUAL_INSTRUMENT_IDS,
    parse_manual_display,
    parse_manual_execution,
    parse_manual_preparation,
)


def perpetual_manual_paper_router(service, guide, *, owner):
    router = APIRouter(prefix="/paper-execution", tags=["perpetual-manual-paper"])

    def configured():
        return (
            os.environ.get("DECISION_HUB_ACTION_PRODUCT_ENABLED", "0") == "1"
            and os.environ.get("DECISION_HUB_ACTION_EXECUTION_ENABLED", "0") == "1"
        )

    @router.get("/capabilities", response_model=None)
    async def capabilities(request: Request, response: Response):
        identity = owner(request, response, False)
        return dict(
            schema_version="perpetual-manual-capabilities.v3",
            owner_id=identity,
            configured=configured(),
            worker_liveness="unknown",
            horizon="4h",
            horizons=list(MANUAL_HORIZONS),
            instrument_ids=list(MANUAL_INSTRUMENT_IDS),
            execution="paper_only",
        )

    @router.get("/input-guide/{prepare_request_id}", response_model=None)
    async def input_guide(prepare_request_id: str, request: Request, response: Response):
        identity = owner(request, response, False)
        return await run_in_threadpool(business, lambda: guide.read(identity, prepare_request_id))

    def business(operation):
        try:
            return operation()
        except PermissionError:
            raise HTTPException(403, "perp_manual_owner_rejected") from None
        except (ValueError, OSError) as error:
            code = str(error)
            if not code.startswith(("perp_", "decision_", "execution_")):
                code = "perp_manual_original_unavailable"
            raise HTTPException(
                404
                if code in {"perp_manual_command_missing", "perp_manual_intent_missing"}
                else 409,
                code,
            ) from None

    async def parse(request, model):
        try:
            command = model(decode_json(await request.body()))
        except (ValidationError, ValueError, TypeError):
            raise HTTPException(422, "perp_manual_command_invalid") from None
        if request.headers.get("idempotency-key") != command.request_id:
            raise HTTPException(400, "perp_manual_idempotency_key_mismatch")
        return command

    async def execute(request, response, model, operation):
        identity = owner(request, response, True)
        if not configured():
            raise HTTPException(409, "perp_manual_worker_not_configured")
        command = await parse(request, model)
        receipt = await run_in_threadpool(business, lambda: operation(identity, command))
        return receipt.wire()

    @router.post("/prepare", response_model=None)
    async def prepare(request: Request, response: Response):
        return await execute(request, response, parse_manual_preparation, service.prepare_context)

    @router.post("/display", response_model=None)
    async def display(request: Request, response: Response):
        return await execute(request, response, parse_manual_display, service.record_display)

    @router.post("/intents", response_model=None)
    async def intent(request: Request, response: Response):
        return await execute(request, response, parse_manual_execution, service.submit_intent)

    @router.get("/commands/{request_id}", response_model=None)
    async def command(request_id: str, request: Request, response: Response):
        identity = owner(request, response, False)

        def read():
            original = service.read_command(identity, request_id)
            if original is None:
                raise ValueError("perp_manual_command_missing")
            return original.wire()

        return await run_in_threadpool(business, read)

    @router.get("/intents/{intent_id}", response_model=None)
    async def current(intent_id: str, request: Request, response: Response):
        identity = owner(request, response, False)
        return await run_in_threadpool(
            business, lambda: service.read_execution(identity, intent_id)
        )

    return router

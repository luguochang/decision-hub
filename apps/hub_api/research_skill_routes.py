"""Authenticated native skill callbacks; no arbitrary model-supplied identity authority."""

from __future__ import annotations

import asyncio
import re
import time
from threading import Event

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from packages.kernel.decision_hub_kernel.application.research_skill_package import decoded


def research_skill_router(service, authenticate, callbacks=None):
    router = APIRouter(tags=["research-skills"])

    async def invoke(operation, run_id, request, bridge_key):
        authenticate(bridge_key)
        if service is None:
            raise HTTPException(503, "skill_service_not_installed")
        raw = await request.body()
        if len(raw) > 1_000_000:
            raise HTTPException(413, "skill_request_too_large")
        try:
            body = decoded(raw)
            if not isinstance(body, dict) or body.get("identity", {}).get("run_id") != run_id:
                raise ValueError("skill_route_identity_mismatch")
        except (ValueError, TypeError, AttributeError) as error:
            raise HTTPException(400, "skill_request_invalid") from error
        cancelled = Event()
        deadline = time.monotonic() + 60

        def check():
            if cancelled.is_set() or time.monotonic() >= deadline:
                raise ValueError("skill_callback_lifetime_elapsed")

        task = asyncio.create_task(asyncio.to_thread(operation, body, request_check=check))
        try:
            while not task.done():
                if await request.is_disconnected() or time.monotonic() >= deadline:
                    cancelled.set()
                    task.add_done_callback(
                        lambda done: done.exception() if not done.cancelled() else None
                    )
                    raise HTTPException(503, "skill_callback_commit_unconfirmed")
                await asyncio.wait({task}, timeout=0.05)
            return task.result()
        except asyncio.CancelledError:
            cancelled.set()
            # A writer may already have committed. Never invent failure state;
            # the original call/stage key is safe to retry and read back.
            task.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
            raise
        except (ValueError, TypeError, PermissionError, KeyError) as error:
            code = str(error)
            if code != "legacy_skill_unversioned" and not re.fullmatch(r"skill_[a-z_]{1,100}(?::[a-zA-Z0-9_.:]*)?", code):
                code = "skill_original_verification_failed"
            raise HTTPException(409, code) from error

    @router.post("/v1/dsh/sessions/{run_id}/skills/tool")
    async def tool(
        run_id: str,
        request: Request,
        bridge_key: str | None = Header(None, alias="X-Decision-Hub-Bridge-Key"),
    ):
        return await invoke(service.tool if service else None, run_id, request, bridge_key)

    @router.post("/v1/dsh/sessions/{run_id}/skills/records")
    async def record(
        run_id: str,
        request: Request,
        bridge_key: str | None = Header(None, alias="X-Decision-Hub-Bridge-Key"),
    ):
        return await invoke(service.record if service else None, run_id, request, bridge_key)

    def callback_error(error):
        code = str(error)
        if code != "legacy_skill_unversioned" and not re.fullmatch(r"skill_[a-z_]{1,100}(?::[a-zA-Z0-9_.:]*)?", code):
            code = "skill_original_verification_failed"
        return HTTPException(409, code)

    def callback_response(value):
        # A queue receipt never masquerades as a native successful tool result.
        return JSONResponse(value, status_code=200 if value["status"] == "completed" else 202)

    @router.get("/v1/dsh/sessions/{run_id}/skills/selected-delivery")
    async def selected_delivery(
        run_id: str, request: Request, generation: int,
        execution_session_id: str, request_hash: str,
        bridge_key: str | None = Header(None, alias="X-Decision-Hub-Bridge-Key"),
    ):
        from apps.hub_api.research_proof_request import await_original_research_read
        from packages.kernel.decision_hub_kernel.application import (
            research_skill_selected_delivery,
        )

        authenticate(bridge_key)
        if service is None:
            raise HTTPException(503, "skill_service_not_installed")
        if request.headers.get("x-decision-hub-research-proof-deadline-at") is None:
            raise HTTPException(400, "selected_skill_delivery_budget_invalid")

        def scope(link):
            if (link.generation != generation or link.dsh_session_id != execution_session_id
                    or link.request_hash != request_hash):
                raise ValueError("skill_selected_request_changed")

        try:
            return await await_original_research_read(
                request, service.database, run_id,
                lambda check: research_skill_selected_delivery.read_owned_selected_delivery(
                    service, run_id=run_id, generation=generation,
                    root_session_id=execution_session_id,
                    request_hash=request_hash, checkpoint=check,
                ), scope_check=scope, kind="selected_skill_delivery",
            )
        except (ValueError, TypeError, PermissionError, KeyError) as error:
            raise callback_error(error) from error

    @router.post("/v1/dsh/sessions/{run_id}/skills/callbacks")
    async def accept_callback(
        run_id: str,
        request: Request,
        bridge_key: str | None = Header(None, alias="X-Decision-Hub-Bridge-Key"),
    ):
        authenticate(bridge_key)
        if callbacks is None:
            raise HTTPException(503, "skill_callback_service_not_installed")
        raw = await request.body()
        if len(raw) > 1_002_048:
            raise HTTPException(413, "skill_request_too_large")
        try:
            envelope = decoded(raw)
            if envelope["body"]["identity"]["run_id"] != run_id:
                raise ValueError("skill_route_identity_mismatch")
            value = await asyncio.to_thread(callbacks.accept, raw)
            return callback_response(value)
        except (ValueError, TypeError, PermissionError, KeyError, AttributeError) as error:
            raise callback_error(error) from error

    @router.get("/v1/dsh/sessions/{run_id}/skills/callbacks/{job_id}")
    async def read_callback(
        run_id: str,
        job_id: str,
        generation: int,
        execution_session_id: str,
        request_hash: str,
        bridge_key: str | None = Header(None, alias="X-Decision-Hub-Bridge-Key"),
    ):
        authenticate(bridge_key)
        if callbacks is None:
            raise HTTPException(503, "skill_callback_service_not_installed")
        try:
            value = await asyncio.to_thread(
                callbacks.get,
                job_id,
                run_id=run_id,
                generation=generation,
                execution_session_id=execution_session_id,
                request_hash=request_hash,
            )
            return callback_response(value)
        except (ValueError, TypeError, PermissionError, KeyError) as error:
            raise callback_error(error) from error

    return router

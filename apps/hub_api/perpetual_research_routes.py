"""Existing Host service-secret boundary for the frozen perpetual binding."""

from collections.abc import Callable
from datetime import datetime

from fastapi import APIRouter, Header, HTTPException, Query
from starlette.concurrency import run_in_threadpool

from packages.kernel.decision_hub_kernel.application.perpetual_research_binding import (
    read_native_perpetual_binding,
)
from packages.kernel.decision_hub_kernel.persistence.db import Database, utcnow


def perpetual_research_router(
    database: Database,
    require_service: Callable[[str | None], None],
    *,
    now: Callable[[], datetime] = utcnow,
):
    router = APIRouter()

    @router.get("/v1/research/runs/{run_id}/decision-binding", response_model=None)
    async def decision_binding(
        run_id: str,
        request_hash: str = Query(pattern=r"^[a-f0-9]{64}$"),
        generation: int = Query(ge=1, le=2147483647),
        source_call_id: str | None = Query(default=None, min_length=1, max_length=256),
        bridge_key: str | None = Header(default=None, alias="X-Decision-Hub-Bridge-Key"),
    ):
        require_service(bridge_key)
        try:
            return await run_in_threadpool(
                read_native_perpetual_binding,
                database,
                run_id,
                request_hash,
                generation,
                source_call_id=source_call_id,
                now=now,
            )
        except KeyError as exc:
            raise HTTPException(404, "perpetual_research_run_missing") from exc
        except ValueError as exc:
            reason = str(exc)
            code = (
                reason
                if reason.replace("_", "").isalnum() and len(reason) <= 160
                else "perpetual_research_binding_unavailable"
            )
            raise HTTPException(409, code) from exc

    return router

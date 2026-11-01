"""Identity-only authenticated diagnostics; no caller candidate or write path."""

import re
from collections.abc import Callable

from fastapi import APIRouter, Header, HTTPException, Path, Query, Request
from sqlalchemy.exc import OperationalError

from apps.hub_api.research_proof_request import await_original_research_read
from packages.kernel.decision_hub_kernel.application.action_synthesis_preflight import (
    ActionSynthesisPreflight,
)
from packages.kernel.decision_hub_kernel.persistence.db import Database, DshSessionLinkRecord


async def _owned_preflight_read(
    request: Request, service: ActionSynthesisPreflight, run_id: str, call_id: str,
    *, generation: int, execution_session_id: str,
):
    def scope_check(link: DshSessionLinkRecord) -> None:
        if link.generation != generation or link.dsh_session_id != execution_session_id:
            raise ValueError("action_preflight_identity_invalid")

    return await await_original_research_read(
        request, service.database, run_id,
        lambda check: service.read(
            run_id, call_id, generation=generation,
            execution_session_id=execution_session_id, request_check=check,
        ),
        scope_check=scope_check, kind="action_preflight",
    )


def action_preflight_router(
    database: Database, authenticate: Callable[[str | None], None]
) -> APIRouter:
    router = APIRouter(tags=["action-synthesis-preflight"])
    service = ActionSynthesisPreflight(database)

    @router.get("/v1/dsh/sessions/{run_id}/action-preflight/{call_id}")
    async def read(
        request: Request,
        run_id: str = Path(min_length=1, max_length=128),
        call_id: str = Path(min_length=1, max_length=256),
        generation: int = Query(ge=1),
        execution_session_id: str = Query(min_length=1, max_length=256),
        bridge_key: str | None = Header(None, alias="X-Decision-Hub-Bridge-Key"),
    ):
        authenticate(bridge_key)
        if (
            request.headers.get("content-length", "0") != "0"
            or "transfer-encoding" in request.headers
        ):
            raise HTTPException(400, "action_preflight_request_body_forbidden")
        if (
            set(request.query_params) != {"generation", "execution_session_id"}
            or len(request.query_params.multi_items()) != 2
        ):
            raise HTTPException(400, "action_preflight_identity_query_invalid")
        try:
            value = await _owned_preflight_read(
                request, service, run_id, call_id, generation=generation,
                execution_session_id=execution_session_id,
            )
        except KeyError as exc:
            raise HTTPException(404, "action_preflight_run_not_found") from exc
        except (ValueError, TypeError, PermissionError) as exc:
            code = str(exc)
            safe = (
                code
                if re.fullmatch(r"action_preflight_[a-z_]{1,100}", code)
                else ("action_preflight_original_verification_failed")
            )
            raise HTTPException(409, safe) from exc
        except OperationalError as exc:
            raise HTTPException(503, "action_preflight_storage_unavailable") from exc
        if value is None:
            raise HTTPException(404, "action_preflight_emission_not_found")
        return value

    return router

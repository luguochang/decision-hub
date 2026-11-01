import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime
from time import monotonic
from uuid import uuid4

from fastapi import APIRouter, Header, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.runtime_adapters.dsh_runtime.terminal_queue_recovery import (
    TerminalQueueAuthority,
    read_failed_terminal_authority,
    terminal_queue_authority_in_session,
)


def terminal_quiescence_router(
    database: Database,
    authenticate: Callable[[str | None], None],
) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/v1/dsh/sessions/{run_id}/terminal-quiescence-authority",
        response_model=TerminalQueueAuthority,
    )
    async def authority(
        request: Request,
        run_id: str,
        bridge_key: str | None = Header(None, alias="X-Decision-Hub-Bridge-Key"),
    ) -> TerminalQueueAuthority:
        authenticate(bridge_key)
        native = any(name in request.headers for name in (
            "x-decision-hub-research-proof-deadline-at",
            "x-decision-hub-research-proof-budget-ms",
        ))
        operation_id, started = uuid4().hex, monotonic()

        def stage(name: str) -> None:
            logging.getLogger("uvicorn.error").warning(
                "dsh_terminal_authority_phase operation_id=%s run_id=%s "
                "phase=%s elapsed_ms=%d observed_at=%s",
                operation_id, run_id if re.fullmatch(r"run_[a-f0-9]{32}", run_id) else "other",
                name, round((monotonic() - started) * 1000), datetime.now(UTC).isoformat(),
            )

        def legacy():
            with database.session() as session:
                if session.get_bind().dialect.name == "sqlite":
                    session.connection().exec_driver_sql("BEGIN")
                return terminal_queue_authority_in_session(session, run_id)

        try:
            if not native:
                # Historical acknowledged-terminal cleanup keeps its original
                # bounded lifetime, without making an expired Task current.
                return await run_in_threadpool(legacy)
            from apps.hub_api.research_proof_request import await_original_research_read
            stage("callback_started")
            result = await await_original_research_read(request, database, run_id,
                lambda check: read_failed_terminal_authority(database, run_id,
                    checkpoint=check, stage=stage), kind="terminal_quiescence")
            stage("callback_confirmed")
            return result
        except (ValueError, TypeError) as exc:
            stage("callback_rejected")
            code = str(exc)
            if code not in {"dsh_host_read_cancelled", "dsh_host_read_deadline_elapsed",
                            "dsh_model_dispatch_identity_mismatch"}:
                code = "terminal_queue_authority_unavailable"
            raise HTTPException(409, code) from exc

    return router

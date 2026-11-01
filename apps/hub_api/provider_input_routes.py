"""Authenticated short provider callbacks and safe original delivery summaries."""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from threading import Event
from typing import Any, Literal

from fastapi import APIRouter, Header, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool

from packages.contracts_py.decision_hub_contracts.generated.provider_input import (
    ResearchProviderFinishRecorded,
    ResearchProviderFinishSubmit,
    ResearchProviderInputRecorded,
    ResearchProviderInputSubmit,
    ResearchProviderToolEmissionRecorded,
    ResearchProviderToolEmissionSubmit,
)
from packages.kernel.decision_hub_kernel.application.provider_callback_diagnostics import (
    ProviderCallbackDiagnostics,
)
from packages.kernel.decision_hub_kernel.application.provider_input_receipts import (
    ProviderInputReceiptService,
)
from packages.kernel.decision_hub_kernel.application.research_context_delivery import (
    ResearchContextDeliveryQuery,
)
from packages.kernel.decision_hub_kernel.persistence.db import Database, DshSessionLinkRecord, as_utc
from packages.kernel.decision_hub_kernel.ports.provider_input import ResearchContextDelivery


async def _bounded_callback(
    request: Request, operation: Callable[..., Any], payload: Any,
    callback_kind: Literal["input", "emission", "finish"] = "input",
    *, database: Database | None = None,
) -> Any:
    original_header = request.headers.get("x-decision-hub-provider-input-deadline-at")
    original_deadline: datetime | None = None
    if original_header is not None:
        try:
            original_deadline = datetime.fromisoformat(original_header.replace("Z", "+00:00"))
            if original_deadline.tzinfo is None or original_deadline.utcoffset() is None or database is None:
                raise ValueError
            original_deadline = original_deadline.astimezone(UTC)
        except (ValueError, TypeError, OverflowError) as exc:
            raise HTTPException(400, "research_provider_callback_budget_invalid") from exc
    raw = request.headers.get("x-decision-hub-provider-input-budget-ms")
    maximum_ms = 2_147_483_647 if original_deadline is not None else 60_000
    if raw is not None and (
        not raw.isascii() or not raw.isdigit() or len(raw) > (10 if original_deadline is not None else 5)
        or not 1 <= int(raw) <= maximum_ms
    ):
        raise HTTPException(400, "research_provider_callback_budget_invalid")
    budget_ms = 30_000 if raw is None else int(raw)
    deadline = time.monotonic() + budget_ms / 1000
    if original_deadline is not None:
        deadline = min(deadline, time.monotonic() + (original_deadline - datetime.now(UTC)).total_seconds())
    diagnostics = ProviderCallbackDiagnostics(payload, callback_kind, budget_ms)
    request.scope["decision_hub_provider_callback_response_observer"] = diagnostics.asgi_observation
    diagnostics.event("route", "started")
    cancelled = Event()
    stop_watcher = asyncio.Event()

    def check() -> None:
        if (cancelled.is_set() or time.monotonic() >= deadline
                or original_deadline is not None and datetime.now(UTC) >= original_deadline):
            diagnostics.event("route", "cancelled" if cancelled.is_set() else "error",
                              error_code="research_provider_callback_lifetime_elapsed")
            raise ValueError("research_provider_callback_lifetime_elapsed")

    async def watch() -> None:
        try:
            while not stop_watcher.is_set():
                if await request.is_disconnected():
                    cancelled.set()
                    diagnostics.event("route", "disconnected")
                    return
                if stop_watcher.is_set():
                    return
                await asyncio.sleep(0.025)
        except asyncio.CancelledError:
            raise
        except Exception:
            cancelled.set()
            diagnostics.event("route", "error")

    # Starlette's receive CancelScope may consume task cancellation. Capture
    # the caller's cancellation before the first probe and preserve it at exit.
    callback_task = asyncio.current_task()
    callback_cancelling = callback_task.cancelling() if callback_task is not None else 0
    watcher: asyncio.Task[None] | None = None
    callback_succeeded = False

    def invoke_owned():
        nonlocal deadline, original_deadline
        if original_deadline is not None:
            assert database is not None
            # A caller-supplied later timestamp never extends the durable Task.
            # This is only a ceiling read; the unchanged full original/auth/fence
            # below remains the sole permission to record anything.
            with database.session() as session, session.no_autoflush:
                link = session.get(DshSessionLinkRecord, payload.run_id, populate_existing=True)
                if link is None or link.deadline_at is None or (
                    link.dsh_session_id, link.generation, link.request_hash, link.plugin_build_hash
                ) != (payload.root_session_id, payload.generation, payload.request_hash, payload.plugin_build_hash):
                    raise ValueError("research_provider_execution_unavailable")
                original_deadline = min(original_deadline, as_utc(link.deadline_at))
                deadline = min(deadline, time.monotonic() + (original_deadline - datetime.now(UTC)).total_seconds())
        check()
        return operation(payload, request_check=check, diagnostics=diagnostics)

    async def await_owned():
        # Submit once. Repeated caller cancellation sets the original checkpoint
        # but cannot release the request while a native Session/lease is alive.
        work = asyncio.create_task(run_in_threadpool(invoke_owned))
        was_cancelled = False
        while not work.done():
            try:
                await asyncio.shield(work)
            except asyncio.CancelledError:
                was_cancelled = True
                cancelled.set()
            except BaseException:
                if not work.done():
                    raise
                break
        if was_cancelled:
            try:
                work.result()
            except BaseException:
                pass
            raise asyncio.CancelledError
        return work.result()
    try:
        if await request.is_disconnected():
            cancelled.set()
            diagnostics.event("route", "disconnected")
        if callback_task is not None and callback_task.cancelling() > callback_cancelling:
            cancelled.set()
            raise asyncio.CancelledError
        check()
        watcher = asyncio.create_task(watch())
        result = await await_owned()
        check()
        callback_succeeded = True
        diagnostics.event("return", "finished")
        return result
    except asyncio.CancelledError:
        cancelled.set()
        diagnostics.event("return", "cancelled")
        raise
    except KeyError as exc:
        diagnostics.event("return", "error", status_code=404,
                          error_code="research_provider_run_not_found")
        raise HTTPException(404, "research_provider_run_not_found") from exc
    except (ValueError, PermissionError, TypeError) as exc:
        code = str(exc)
        safe = (
            code
            if re.fullmatch(r"research_provider_[a-z_]{1,100}", code)
            else "research_provider_original_verification_failed"
        )
        diagnostics.event("return", "error", status_code=409, error_code=safe)
        raise HTTPException(409, safe) from exc
    finally:
        stop_watcher.set()
        if watcher is not None:
            watcher.cancel()
            try:
                await watcher
            except asyncio.CancelledError:
                if callback_task is not None and callback_task.cancelling() > callback_cancelling:
                    cancelled.set()
                    diagnostics.event("return", "cancelled")
                    raise
        if callback_task is not None and callback_task.cancelling() > callback_cancelling:
            cancelled.set()
            diagnostics.event("return", "cancelled")
            raise asyncio.CancelledError
        if callback_succeeded:
            try:
                check()
            except ValueError as exc:
                diagnostics.event("return", "error", status_code=409,
                                  error_code="research_provider_callback_lifetime_elapsed")
                raise HTTPException(409, "research_provider_callback_lifetime_elapsed") from exc


def provider_input_router(
    database: Database, authenticate: Callable[[str | None], None]
) -> APIRouter:
    router = APIRouter(tags=["provider-input-originals"])
    from .action_preflight_routes import action_preflight_router

    router.include_router(action_preflight_router(database, authenticate))
    service = ProviderInputReceiptService(database)

    def identity(run_id: str, dispatch_id: str, payload: Any, key: str | None) -> None:
        authenticate(key)
        if (payload.run_id, payload.dispatch_id) != (run_id, dispatch_id):
            raise HTTPException(409, "research_provider_callback_path_mismatch")

    @router.put(
        "/v1/dsh/sessions/{run_id}/provider-inputs/{dispatch_id}",
        response_model=ResearchProviderInputRecorded,
    )
    async def input_receipt(
        run_id: str,
        dispatch_id: str,
        payload: ResearchProviderInputSubmit,
        request: Request,
        bridge_key: str | None = Header(None, alias="X-Decision-Hub-Bridge-Key"),
    ):
        identity(run_id, dispatch_id, payload, bridge_key)
        return await _bounded_callback(request, service.record_input, payload, database=database)

    @router.put(
        "/v1/dsh/sessions/{run_id}/provider-inputs/{dispatch_id}/tool-emissions/{call_id}",
        response_model=ResearchProviderToolEmissionRecorded,
    )
    async def emission(
        run_id: str,
        dispatch_id: str,
        call_id: str,
        payload: ResearchProviderToolEmissionSubmit,
        request: Request,
        bridge_key: str | None = Header(None, alias="X-Decision-Hub-Bridge-Key"),
    ):
        identity(run_id, dispatch_id, payload, bridge_key)
        if payload.call_id != call_id:
            raise HTTPException(409, "research_provider_callback_path_mismatch")
        return await _bounded_callback(request, service.record_emission, payload, "emission", database=database)

    @router.put(
        "/v1/dsh/sessions/{run_id}/provider-inputs/{dispatch_id}/finish",
        response_model=ResearchProviderFinishRecorded,
    )
    async def finish(
        run_id: str,
        dispatch_id: str,
        payload: ResearchProviderFinishSubmit,
        request: Request,
        bridge_key: str | None = Header(None, alias="X-Decision-Hub-Bridge-Key"),
    ):
        identity(run_id, dispatch_id, payload, bridge_key)
        return await _bounded_callback(request, service.record_finish, payload, "finish", database=database)

    @router.get(
        "/v1/dsh/sessions/{run_id}/provider-tool-emissions/{call_id}",
        response_model=ResearchProviderToolEmissionRecorded,
    )
    def read_emission(
        run_id: str,
        call_id: str,
        generation: int = Query(ge=1),
        execution_session_id: str = Query(min_length=1, max_length=256),
        bridge_key: str | None = Header(None, alias="X-Decision-Hub-Bridge-Key"),
    ):
        authenticate(bridge_key)
        try:
            value = service.get_emission(
                run_id, call_id, generation=generation, execution_session_id=execution_session_id
            )
        except KeyError as exc:
            raise HTTPException(404, "research_provider_run_not_found") from exc
        except (ValueError, PermissionError, TypeError) as exc:
            raise HTTPException(409, "research_provider_original_verification_failed") from exc
        if value is None:
            raise HTTPException(404, "research_provider_tool_emission_not_found")
        return value

    @router.get(
        "/v1/research/runs/{run_id}/context-delivery", response_model=ResearchContextDelivery
    )
    def delivery(run_id: str):
        try:
            return ResearchContextDeliveryQuery(database).read(run_id)
        except KeyError as exc:
            raise HTTPException(404, "research_context_delivery_publication_not_found") from exc
        except (ValueError, PermissionError, TypeError) as exc:
            raise HTTPException(
                409, "research_context_delivery_original_verification_failed"
            ) from exc

    return router

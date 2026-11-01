"""One owned research RO callback; an original ceiling grants no permission."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from datetime import UTC, datetime
from threading import Event
from typing import Literal

from fastapi import HTTPException, Request
from starlette.concurrency import run_in_threadpool

from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    DshSessionLinkRecord,
    as_utc,
)


async def await_original_research_read[T](
    request: Request,
    database: Database,
    run_id: str,
    operation: Callable[[Callable[[], None]], T],
    *,
    scope_check: Callable[[DshSessionLinkRecord], None] | None = None,
    kind: Literal[
        "research_detail", "action_preflight", "session_prompt", "business_status",
        "terminal_quiescence", "selected_skill_delivery",
    ],
) -> T:
    """Never detach a submitted Session when ASGI/asyncio cancellation arrives.

    Callers retain endpoint authentication and original validation. Only an
    explicit native header selects an absolute lifetime; old readers retain
    their existing deadline contract. All work stays on one native callback.
    """
    original_header = request.headers.get("x-decision-hub-research-proof-deadline-at")
    raw = request.headers.get("x-decision-hub-research-proof-budget-ms")
    original_deadline: datetime | None = None
    deadline: float | None = None
    if original_header is not None:
        try:
            original_deadline = datetime.fromisoformat(original_header.replace("Z", "+00:00"))
            if original_deadline.tzinfo is None or original_deadline.utcoffset() is None:
                raise ValueError
            original_deadline = original_deadline.astimezone(UTC)
            if (
                raw is None
                or not raw.isascii()
                or not raw.isdigit()
                or len(raw) > 10
                or not 1 <= int(raw) <= 2_147_483_647
            ):
                raise ValueError
            deadline = min(
                time.monotonic() + int(raw) / 1000,
                time.monotonic() + (original_deadline - datetime.now(UTC)).total_seconds(),
            )
        except (ValueError, TypeError, OverflowError) as error:
            raise HTTPException(400, f"{kind}_budget_invalid") from error
    elif raw is not None:
        raise HTTPException(400, f"{kind}_budget_invalid")
    cancelled = Event()

    def check() -> None:
        if cancelled.is_set():
            raise ValueError(
                "dsh_host_read_cancelled"
                if kind != "action_preflight"
                else "action_preflight_cancelled"
            )
        if (
            deadline is not None
            and time.monotonic() >= deadline
            or original_deadline is not None
            and datetime.now(UTC) >= original_deadline
        ):
            raise ValueError(
                "dsh_host_read_deadline_elapsed"
                if kind != "action_preflight"
                else "action_preflight_deadline_elapsed"
            )

    def invoke_owned() -> T:
        nonlocal deadline, original_deadline
        if original_deadline is not None:
            # This fresh read merely narrows the original ceiling. It does not
            # replace the operation's complete Task/source/PIT/permission proof.
            with database.session() as session, session.no_autoflush:
                link = session.get(DshSessionLinkRecord, run_id, populate_existing=True)
                if link is None or link.deadline_at is None:
                    raise ValueError(
                        "dsh_model_dispatch_identity_mismatch"
                        if kind != "action_preflight"
                        else "action_preflight_identity_invalid"
                    )
                if scope_check is not None:
                    scope_check(link)
                original_deadline = min(original_deadline, as_utc(link.deadline_at))
                assert deadline is not None
                deadline = min(
                    deadline,
                    time.monotonic() + (original_deadline - datetime.now(UTC)).total_seconds(),
                )
        check()
        result = operation(check)
        check()
        return result

    stop_watcher = asyncio.Event()

    async def watch() -> None:
        while not stop_watcher.is_set():
            try:
                if await request.is_disconnected():
                    cancelled.set()
                    return
                if stop_watcher.is_set():
                    return
                await asyncio.sleep(0.025)
            except asyncio.CancelledError:
                raise
            except Exception:
                cancelled.set()
                return

    callback = asyncio.current_task()
    cancelling = callback.cancelling() if callback is not None else 0
    if await request.is_disconnected():
        cancelled.set()
    if callback is not None and callback.cancelling() > cancelling:
        cancelled.set()
        raise asyncio.CancelledError
    check()
    watcher = asyncio.create_task(watch())

    async def await_owned() -> T:
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
        result = await await_owned()
    finally:
        stop_watcher.set()
        watcher.cancel()
        try:
            await watcher
        except asyncio.CancelledError:
            if callback is not None and callback.cancelling() > cancelling:
                cancelled.set()
                raise
        # An AnyIO receive CancelScope may swallow the watcher's cancellation.
        # The parent's original cancellation must still prevent a late 200.
        if callback is not None and callback.cancelling() > cancelling:
            cancelled.set()
            raise asyncio.CancelledError
    check()
    return result

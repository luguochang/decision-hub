"""Lifetime fence for one native model binding callback; no authorization cache."""
import asyncio
import logging
import re
import time
from datetime import UTC, datetime
from hashlib import sha256
from threading import Event
from uuid import uuid4

from fastapi import HTTPException, Request
from starlette.concurrency import run_in_threadpool

from packages.contracts_py.decision_hub_contracts.generated.dsh_host_bridge import (
    DshModelDispatchBinding,
)
from packages.kernel.decision_hub_kernel.application.model_dispatch import ModelDispatchService
from packages.kernel.decision_hub_kernel.persistence.callback_source_lease import (
    callback_source_lease_scope,
)
from packages.kernel.decision_hub_kernel.persistence.db import DshSessionLinkRecord, as_utc
from packages.kernel.decision_hub_kernel.persistence.operation_phases import (
    OperationPhases,
    PhaseObservation,
)
from packages.kernel.decision_hub_kernel.persistence.sqlite_busy import (
    is_sqlite_initial_begin_busy,
)

MAX_MODEL_BINDING_BUDGET_MS = 30_000


def _diagnostic_identity(value: str | None) -> str:
    try:
        if value is None:
            return "None"
        if re.fullmatch(
            r"run_[a-f0-9]{32}|dsh_[a-f0-9]{64}|[a-f0-9]{32}|"
            r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}", value,
        ):
            return value
        return "sha256_" + sha256(value.encode("utf-8", errors="surrogatepass")).hexdigest()
    except Exception:
        return "None"


async def bind_model_request(
    request: Request, service: ModelDispatchService, payload: DshModelDispatchBinding,
) -> DshModelDispatchBinding:
    original_header = request.headers.get("x-decision-hub-model-binding-deadline-at")
    original_deadline: datetime | None = None
    if original_header is not None:
        try:
            original_deadline = datetime.fromisoformat(original_header.replace("Z", "+00:00"))
            if original_deadline.tzinfo is None or original_deadline.utcoffset() is None:
                raise ValueError
            original_deadline = original_deadline.astimezone(UTC)
        except (ValueError, TypeError, OverflowError) as error:
            raise HTTPException(400, "dsh_model_binding_budget_invalid") from error
    raw = request.headers.get("x-decision-hub-model-binding-budget-ms")
    native = original_deadline is not None
    if (native and raw is None) or raw is not None and (
        len(raw) > (10 if native else 5) or not raw.isascii() or not raw.isdigit()
        or not (1 if native else 1_000) <= int(raw) <= (
            2_147_483_647 if native else MAX_MODEL_BINDING_BUDGET_MS
        )
    ):
        raise HTTPException(status_code=400, detail="dsh_model_binding_budget_invalid")
    budget_ms = MAX_MODEL_BINDING_BUDGET_MS if raw is None else int(raw)
    # One existing absolute ceiling, never a fresh model-step allowance.
    deadline = time.monotonic() + budget_ms / 1000
    if original_deadline is not None:
        deadline = min(deadline, time.monotonic() + (
            original_deadline - datetime.now(UTC)
        ).total_seconds())
    writer_deadline: float | None = None
    writer_complete = False
    operation_id = uuid4().hex
    candidate_request_id = request.scope.get("decision_hub_request_id")
    request_id = (
        candidate_request_id if isinstance(candidate_request_id, str)
        and re.fullmatch(r"[a-f0-9]{32}", candidate_request_id) else "None"
    )
    began = time.monotonic()
    identities = (
        request_id, _diagnostic_identity(payload.run_id),
        _diagnostic_identity(payload.dsh_session_id),
        _diagnostic_identity(payload.execution_session_id),
        _diagnostic_identity(payload.parent_session_id), payload.generation,
        payload.request_hash,
    )
    observation_count = 0

    def observe(
        phase: str, state: str = "finished", error: BaseException | None = None,
    ) -> None:
        nonlocal observation_count
        if observation_count >= 32:
            return
        observation_count += 1
        try:
            logging.getLogger("uvicorn.error").warning(
                "dsh_model_binding_request_phase operation_id=%s request_id=%s "
                "run=%s session=%s execution_session=%s parent_session=%s generation=%s "
                "request_hash=%s phase=%s state=%s observed_at=%s "
                "elapsed_ms=%.3f error_type=%s allocated_budget_ms=%s "
                "original_deadline_at=%s writer_budget_started=%s",
                operation_id, *identities, phase, state, datetime.now(UTC).isoformat(),
                (time.monotonic() - began) * 1000,
                type(error).__name__ if error is not None else None, budget_ms,
                original_deadline.isoformat() if original_deadline is not None else None,
                writer_deadline is not None,
            )
        except Exception:
            # Observation never grants permission or changes the original outcome.
            pass

    # The middleware reads this server-owned callback only for ASGI response
    # handoff. It never reads or logs headers, response body or request payload.
    request.scope["decision_hub_model_binding_response_observer"] = observe
    observe("callback_entered", "started")

    def phase_observer(observation: PhaseObservation) -> None:
        logging.getLogger("uvicorn.error").warning(
            "dsh_model_binding_phase operation_id=%s process_id=%s phase=%s "
            "state=%s observed_at=%s elapsed_ms=%.3f error_type=%s sqlite_code=%s "
            "request_id=%s run=%s session=%s execution_session=%s "
            "parent_session=%s generation=%s request_hash=%s",
            observation.operation_id, observation.process_id, observation.phase,
            observation.state, observation.observed_at, observation.elapsed_ms,
            observation.error_type, observation.sqlite_error_code, *identities,
        )

    phases = OperationPhases(phase_observer, operation_id=operation_id)
    disconnected = Event()

    def check() -> None:
        if disconnected.is_set():
            observe("request_cancelled", "error")
            raise ValueError("dsh_model_binding_cancelled")
        if original_deadline is not None and datetime.now(UTC) >= original_deadline:
            observe("original_deadline_elapsed", "error")
            raise ValueError("dsh_model_binding_original_deadline_elapsed")
        if time.monotonic() >= deadline:
            observe("request_budget_elapsed", "error")
            raise ValueError("dsh_model_binding_budget_elapsed")
        if (writer_deadline is not None and not writer_complete
                and time.monotonic() >= writer_deadline):
            observe("writer_budget_elapsed", "error")
            raise ValueError("dsh_model_binding_writer_budget_elapsed")

    stop_watcher = asyncio.Event()

    async def watch() -> None:
        try:
            while not stop_watcher.is_set():
                is_disconnected = await request.is_disconnected()
                if is_disconnected:
                    observe("disconnect_observed")
                    disconnected.set()
                    return
                # Starlette's AnyIO CancelScope can consume the task cancellation.
                # Preserve a disconnect just observed, then end the receive loop.
                if stop_watcher.is_set():
                    return
                await asyncio.sleep(0.025)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            observe("disconnect_observer_error", "error", error)
            # A broken request observer cannot grant this callback a writer.
            disconnected.set()

    # Capture before the first receive probe: its AnyIO scope can also consume
    # an external cancellation before the watcher has been created.
    callback_task = asyncio.current_task()
    callback_cancelling = callback_task.cancelling() if callback_task is not None else 0
    if await request.is_disconnected():
        disconnected.set()
    if callback_task is not None and callback_task.cancelling() > callback_cancelling:
        disconnected.set()
        raise asyncio.CancelledError
    check()
    watcher = asyncio.create_task(watch())

    def start_writer() -> None:
        nonlocal writer_deadline
        check()
        if writer_deadline is None:
            # This is an independent cap on time spent holding the SQLite
            # writer. The original request/step ceilings stay in ``deadline``.
            writer_deadline = time.monotonic() + MAX_MODEL_BINDING_BUDGET_MS / 1000
        observe("writer_budget_started", "started")

    def bind_in_thread() -> DshModelDispatchBinding:
        nonlocal deadline, original_deadline, writer_complete
        observe("threadpool_entered", "started")
        try:
            if original_deadline is not None:
                # This fresh ceiling is not authorization. The service still
                # authenticates every original and the final owner/lease fence.
                with service.database.session() as session, session.no_autoflush:
                    link = session.get(DshSessionLinkRecord, payload.run_id, populate_existing=True)
                    if link is None or link.deadline_at is None or (
                        link.dsh_session_id,
                        link.generation,
                        link.request_hash,
                        link.plugin_build_hash,
                    ) != (
                        payload.dsh_session_id,
                        payload.generation,
                        payload.request_hash,
                        payload.plugin_build_hash,
                    ):
                        raise ValueError("dsh_model_dispatch_identity_mismatch")
                    original_deadline = min(original_deadline, as_utc(link.deadline_at))
                    deadline = min(deadline, time.monotonic() + (
                        original_deadline - datetime.now(UTC)
                    ).total_seconds())
                check()
            witness_hash = request.headers.get("x-decision-hub-harness-witness")
            if witness_hash is not None and not re.fullmatch(r"[a-f0-9]{64}", witness_hash):
                raise ValueError("harness_root_witness_header_invalid")
            # Deadline ownership and original witness authentication are
            # complementary. The service still proves the saved witness;
            # a header alone grants no authority and cannot refill the Task.
            witness_options = (
                {} if witness_hash is None else {"harness_witness_hash": witness_hash}
            )
            if native:
                # Keep source handles and the SQLite Session on one worker,
                # including the Harness path, through commit or rollback.
                with callback_source_lease_scope(
                    check, defer_unselected_recipe_archives=True,
                ) as source_lease:
                    result = service.bind(
                        payload, request_check=check, diagnostics=phases,
                        writer_start=start_writer, source_lease=source_lease,
                        **witness_options,
                    )
            else:
                result = service.bind(
                    payload, request_check=check, diagnostics=phases, **witness_options,
                )
        except BaseException as error:
            observe("service_error", "error", error)
            # The real SQLite error and exact initial BEGIN statement prove no
            # writer was acquired. Post-write/COMMIT errors cannot use this code.
            # No replay is performed here; the Host preserves this bounded cause.
            if is_sqlite_initial_begin_busy(error):
                raise HTTPException(
                    status_code=409, detail="dsh_model_binding_writer_busy",
                ) from error
            raise
        # bind returned only after its original Session COMMIT/close. The local
        # writer slice cannot become a response-handoff timeout after that fact.
        writer_complete = True
        observe("service_returned")
        return result

    async def await_owned() -> DshModelDispatchBinding:
        # Submit once and drain the same Session through repeated cancellation.
        work = asyncio.create_task(run_in_threadpool(bind_in_thread))
        cancelled = False
        while not work.done():
            try:
                await asyncio.shield(work)
            except asyncio.CancelledError:
                cancelled = True
                disconnected.set()
            except BaseException:
                if not work.done():
                    raise
                break
        if cancelled:
            try:
                work.result()
            except BaseException:
                pass
            raise asyncio.CancelledError
        return work.result()

    callback_succeeded = False
    try:
        observe("threadpool_queued", "started")
        result = await await_owned()
        observe("threadpool_returned")
        check()
        observe("callback_return_ready")
        callback_succeeded = True
        return result
    except asyncio.CancelledError as error:
        observe("request_task_cancelled", "error", error)
        disconnected.set()
        raise
    finally:
        observe("watcher_shutdown", "started")
        stop_watcher.set()
        watcher.cancel()
        try:
            await watcher
        except asyncio.CancelledError:
            # Only the watcher's own cancellation is consumed here. A caller
            # abort during shutdown must never turn an aborted request into 200.
            if callback_task is not None and callback_task.cancelling() > callback_cancelling:
                disconnected.set()
                raise
        except BaseException as error:
            observe("watcher_shutdown", "error", error)
            raise
        finally:
            observe("watcher_shutdown")
        # Cancellation propagated to the watcher can also be consumed by its
        # AnyIO scope. Retain the callback's cancellation even in that case.
        if callback_task is not None and callback_task.cancelling() > callback_cancelling:
            disconnected.set()
            raise asyncio.CancelledError
        if callback_succeeded:
            # A true disconnect can arrive in the final receive/join after the
            # pre-return fence. Never send a successful receipt in that race.
            check()

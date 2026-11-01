"""Signed-owner, separately granted outcome reads; no production or mutation."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from datetime import datetime
from threading import Event
from time import monotonic
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool

from packages.contracts_py.decision_hub_contracts.generated.history_outcome_v2 import (
    HistoryOutcomeObservationReplyV2,
    HistoryOutcomeScoreReplyV2,
    HistoryOutcomeStatusV2,
)

from .history_owner_authority import HISTORY_COOKIE
from .history_product_routes import HistoryProductBootstrap
from .local_owner_session import LocalOwnerAuthenticationError

if TYPE_CHECKING:
    from packages.kernel.decision_hub_kernel.application.history_outcome_v2 import (
        HistoryOutcomeV2Service,
    )

PROTOCOL_ID = re.compile(r"^heval2_[a-f0-9]{48}$")
FULL_READ_SECONDS = 120.0


@dataclass(frozen=True)
class OutcomeBootstrapV2:
    history: HistoryProductBootstrap
    service: HistoryOutcomeV2Service
    protocol_ids: tuple[str, ...]

    def __post_init__(self):
        from packages.kernel.decision_hub_kernel.application.history_outcome_v2 import (
            HistoryOutcomeV2Service,
        )

        if (
            type(self.history) is not HistoryProductBootstrap
            or (type(self.service) is not HistoryOutcomeV2Service)
            or self.service.owner_claim != self.history.authority.owner_identity
            or (self.service.commands is not self.history.originals.commands)
        ):
            raise ValueError("history_outcome_trusted_bootstrap_invalid")
        if (
            type(self.protocol_ids) is not tuple
            or len(set(self.protocol_ids)) != len(self.protocol_ids)
            or any(
                not isinstance(identifier, str) or PROTOCOL_ID.fullmatch(identifier) is None
                for identifier in self.protocol_ids
            )
        ):
            raise ValueError("history_outcome_exact_protocol_grants_required")


def _translate(error):
    if isinstance(error, KeyError):
        return HTTPException(404, "history_outcome_original_not_found")
    if isinstance(error, TimeoutError):
        return HTTPException(409, "history_outcome_read_deadline_elapsed")
    return HTTPException(409, "history_outcome_original_unavailable_or_invalid")


def _json_value(value):
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else value


def _json_clock(value):
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError("history_outcome_unexpected_json_value")


async def _read_full(request, bootstrap, protocol_id, identifier, kind):
    deadline = monotonic() + FULL_READ_SECONDS
    cancelled = Event()
    stop = asyncio.Event()
    task = asyncio.current_task()
    original_cancelling = task.cancelling() if task is not None else 0

    def checkpoint():
        if cancelled.is_set():
            raise ValueError("history_outcome_read_cancelled")
        if monotonic() >= deadline:
            raise TimeoutError("history_outcome_read_deadline_elapsed")

    async def watch():
        try:
            while not stop.is_set():
                if await request.is_disconnected():
                    cancelled.set()
                    return
                if not stop.is_set():
                    await asyncio.sleep(0.05)
        except Exception:
            cancelled.set()
            raise

    def read_in_thread():
        checkpoint()
        method = (
            bootstrap.service.read_score if kind == "score" else bootstrap.service.read_observation
        )
        with bootstrap.service.commands._read_session() as session:
            value = method(
                session, identifier, budget_seconds=deadline - monotonic(), checkpoint=checkpoint
            )
            checkpoint()
            subject = _json_value(value)
            if (
                subject["owner_claim"] != bootstrap.history.authority.owner_identity
                or (subject["protocol"]["identifier"] != protocol_id)
                or subject[kind + "_id"] != identifier
            ):
                raise ValueError("history_outcome_original_identity_mismatch")
            wrapper = (
                HistoryOutcomeScoreReplyV2 if kind == "score" else HistoryOutcomeObservationReplyV2
            )
            reply = wrapper.model_validate_json(
                json.dumps(
                    dict(
                        schema_version=f"history-outcome-{kind}-reply.v2",
                        owner_authenticated=True,
                        current_originals_verified=True,
                        protocol_id=protocol_id,
                        **{kind: subject},
                    ),
                    default=_json_clock,
                )
            )
            if len(reply.model_dump_json().encode()) > 4 * 1024 * 1024:
                raise ValueError("history_outcome_original_reply_oversize")
            checkpoint()
            return reply

    if await request.is_disconnected():
        cancelled.set()
    if task is not None and task.cancelling() > original_cancelling:
        cancelled.set()
        raise asyncio.CancelledError
    checkpoint()
    watcher = asyncio.create_task(watch())
    work = asyncio.create_task(run_in_threadpool(read_in_thread))
    try:
        value = await asyncio.shield(work)
        checkpoint()
        return value
    except asyncio.CancelledError:
        cancelled.set()
        # Join the finite reader. Cancellation does not leave a new writer,
        # background producer or an unbounded raw-reader task behind.
        try:
            await asyncio.shield(work)
        except (Exception, asyncio.CancelledError):
            pass
        raise
    finally:
        stop.set()
        watcher.cancel()
        try:
            await watcher
        except asyncio.CancelledError:
            if task is not None and task.cancelling() > original_cancelling:
                cancelled.set()
                raise
        except Exception:
            cancelled.set()
            raise ValueError("history_outcome_disconnect_observer_failed") from None
        if task is not None and task.cancelling() > original_cancelling:
            cancelled.set()
            raise asyncio.CancelledError
        checkpoint()


def history_outcome_router(bootstrap: OutcomeBootstrapV2 | None = None):
    if bootstrap is not None and type(bootstrap) is not OutcomeBootstrapV2:
        raise ValueError("history_outcome_actual_bootstrap_required")
    router = APIRouter()

    def authorize(request, protocol_id):
        if bootstrap is None:
            raise HTTPException(503, "history_outcome_not_configured")
        try:
            bootstrap.__post_init__()
            principal = bootstrap.history.authority.resolve(
                request.cookies.get(HISTORY_COOKIE, ""),
                origin=request.headers.get("Origin"),
                mutation=False,
            )
        except LocalOwnerAuthenticationError:
            raise HTTPException(403, "history_owner_session_rejected") from None
        except (ValueError, TypeError):
            raise HTTPException(409, "history_outcome_trusted_binding_changed") from None
        if (
            principal.owner_identity != bootstrap.service.owner_claim
            or protocol_id not in bootstrap.protocol_ids
        ):
            raise HTTPException(403, "history_outcome_protocol_not_granted")

    @router.get(
        "/v1/history/evaluations/{protocol_id}/status", response_model=HistoryOutcomeStatusV2
    )
    def status(
        request: Request,
        protocol_id: str,
        offset: int = Query(0, ge=0, le=47),
        limit: int = Query(30, ge=1, le=30),
    ):
        authorize(request, protocol_id)
        try:
            with bootstrap.service.commands._read_session() as session:
                value = HistoryOutcomeStatusV2.model_validate_json(
                    json.dumps(
                        _json_value(
                            bootstrap.service.fast_status(
                                session, protocol_id, offset=offset, limit=limit
                            )
                        ),
                        default=_json_clock,
                    )
                )
                if value.protocol_id != protocol_id or (
                    value.pagination.offset != offset or value.pagination.limit != limit
                ):
                    raise ValueError("history_outcome_status_response_identity_mismatch")
                if len(value.model_dump_json().encode()) > 1024 * 1024:
                    raise ValueError("history_outcome_status_reply_oversize")
                return value
        except (KeyError, ValueError, TypeError, OSError, SQLAlchemyError) as error:
            raise _translate(error) from None

    @router.get(
        "/v1/history/evaluations/{protocol_id}/scores/{score_id}",
        response_model=HistoryOutcomeScoreReplyV2,
    )
    async def score(request: Request, protocol_id: str, score_id: str):
        authorize(request, protocol_id)
        if re.fullmatch(r"hscore2_[a-f0-9]{48}", score_id) is None:
            raise HTTPException(404, "history_outcome_original_not_found")
        try:
            return await _read_full(request, bootstrap, protocol_id, score_id, "score")
        except (KeyError, ValueError, TypeError, OSError, TimeoutError, SQLAlchemyError) as error:
            raise _translate(error) from None

    @router.get(
        "/v1/history/evaluations/{protocol_id}/observations/{observation_id}",
        response_model=HistoryOutcomeObservationReplyV2,
    )
    async def observation(request: Request, protocol_id: str, observation_id: str):
        authorize(request, protocol_id)
        if re.fullmatch(r"hobservation2_[a-f0-9]{48}", observation_id) is None:
            raise HTTPException(404, "history_outcome_original_not_found")
        try:
            return await _read_full(request, bootstrap, protocol_id, observation_id, "observation")
        except (KeyError, ValueError, TypeError, OSError, TimeoutError, SQLAlchemyError) as error:
            raise _translate(error) from None

    return router

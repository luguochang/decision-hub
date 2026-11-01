"""Bounded demand-driven sampler draft; no independent process, trades or fills."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from packages.kernel.decision_hub_kernel.application.action_quote_capture import (
    QuoteCaptureService,
    QuoteStore,
    now_utc,
    writer_lock,
)
from packages.kernel.decision_hub_kernel.persistence.action_quote_sampling_records import (
    QuoteSamplingHead,
    QuoteSamplingReceipt,
)
from packages.kernel.decision_hub_kernel.persistence.db import as_utc
from packages.kernel.decision_hub_kernel.ports.action_quote import require_scope, text_at
from packages.kernel.decision_hub_kernel.ports.market_path import (
    MarketPathScope,
    aware_utc,
    canonical_hash,
)
from packages.provider_adapters.research.raw_capture import RawBodyArchive


@dataclass(frozen=True)
class QuoteSamplingClaim:
    scope_id: str
    slot_at: datetime
    owner_id: str
    generation: int
    lease_expires_at: datetime


def _receipt(
    session: Session,
    claim: QuoteSamplingClaim,
    event: str,
    at: datetime,
    *,
    capture_ids: tuple[str, ...] = (),
    quote_id: str | None = None,
    error: str | None = None,
) -> None:
    value = {
        "schema_version": (
            "action-quote-sampling-receipt.v2"
            if len(capture_ids) > 1
            else "action-quote-sampling-receipt.v1"
        ),
        "scope_id": claim.scope_id,
        "slot_at": text_at(claim.slot_at),
        "owner_id": claim.owner_id,
        "generation": claim.generation,
        "lease_expires_at": text_at(claim.lease_expires_at),
        "event": event,
        "recorded_at": text_at(at),
        "capture_ids": list(capture_ids),
        "quote_id": quote_id,
        "error": error,
    }
    identity = "aqsr_" + canonical_hash(
        [claim.scope_id, text_at(claim.slot_at), claim.generation, event]
    )
    session.add(
        QuoteSamplingReceipt(
            receipt_id=identity,
            scope_id=claim.scope_id,
            slot_at=claim.slot_at,
            generation=claim.generation,
            event=event,
            payload_json=json.dumps(value, sort_keys=True, separators=(",", ":")),
            payload_hash=canonical_hash(value),
        )
    )


def claim_scope(
    session: Session,
    *,
    scope: MarketPathScope,
    owner_id: str,
    now: datetime,
) -> QuoteSamplingClaim | None:
    from ..ports.perpetual_manual_execution import PerpetualSamplingScope
    if isinstance(scope, PerpetualSamplingScope):
        scope.validate()
    else:
        require_scope(scope)
    now = aware_utc(now)
    slot = now.replace(second=(now.second // 5) * 5, microsecond=0)
    head = session.get(QuoteSamplingHead, scope.scope_id, populate_existing=True)
    if head is not None:
        if (
            head.status == "blocked"
            or as_utc(head.next_due_at) > now
            or as_utc(head.slot_at) >= slot
            or head.lease_expires_at is not None
            and as_utc(head.lease_expires_at) > now
        ):
            return None
        generation = head.generation + 1
    else:
        generation = 1
        head = QuoteSamplingHead(scope_id=scope.scope_id)
        session.add(head)
    head.status, head.blocked_reason = "ready", None
    head.slot_at, head.next_due_at = slot, slot + timedelta(seconds=5)
    head.owner_id, head.generation = owner_id, generation
    lease_expires_at = now + timedelta(seconds=90)
    head.lease_expires_at = lease_expires_at
    claim = QuoteSamplingClaim(scope.scope_id, slot, owner_id, generation, lease_expires_at)
    _receipt(session, claim, "claimed", now)
    session.flush()
    return claim


def require_claim(session: Session, claim: QuoteSamplingClaim, now: datetime) -> QuoteSamplingHead:
    head = session.get(QuoteSamplingHead, claim.scope_id, populate_existing=True)
    if (
        head is None
        or head.status != "ready"
        or head.owner_id != claim.owner_id
        or head.generation != claim.generation
        or as_utc(head.slot_at) != claim.slot_at
        or as_utc(head.lease_expires_at) != claim.lease_expires_at
        or claim.lease_expires_at <= aware_utc(now)
    ):
        raise ValueError("action_quote_sampling_fence_lost")
    return head


def finish_scope(
    session: Session,
    claim: QuoteSamplingClaim,
    *,
    now: datetime,
    event: str,
    capture_ids: tuple[str, ...] = (),
    quote_id: str | None = None,
    error: str | None = None,
    retry_seconds: int = 5,
) -> None:
    head = require_claim(session, claim, now)
    _receipt(session, claim, event, now, capture_ids=capture_ids, quote_id=quote_id, error=error)
    head.owner_id, head.lease_expires_at = None, None
    head.next_due_at = max(as_utc(head.next_due_at), now + timedelta(seconds=retry_seconds))
    if event == "rejected":
        head.status, head.blocked_reason = "blocked", error
    session.flush()


def _sqlite_lock_contention(error: Exception) -> bool:
    original = error.orig if isinstance(error, OperationalError) else error
    if not isinstance(original, sqlite3.OperationalError):
        return False
    code = getattr(original, "sqlite_errorcode", None)
    return type(code) is int and (code & 0xFF) in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}


class QuoteSamplingService:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        archive: RawBodyArchive,
        *,
        owner_id: str,
        demand_reader: Callable[[datetime], tuple[MarketPathScope, ...]],
        clock: Callable[[], datetime] = now_utc,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.sessions, self.archive, self.owner_id = session_factory, archive, owner_id
        self.demand_reader, self.clock, self.transport = demand_reader, clock, transport

    def process_due(
        self,
        *,
        max_scopes: int = 2,
        checkpoint: Callable[[], None] | None = None,
    ) -> int:
        if max_scopes != 2:
            raise ValueError("action_quote_scope_budget_fixed")
        scopes = tuple(
            {scope.scope_id: scope for scope in self.demand_reader(self.clock())}.values()
        )
        if len(scopes) > max_scopes:
            raise ValueError("action_quote_demand_scope_budget_exceeded")
        captured = 0
        for scope in scopes:
            if checkpoint is not None:
                checkpoint()
            with self.sessions() as session, session.begin():
                writer_lock(session)
                claim = claim_scope(session, scope=scope, owner_id=self.owner_id, now=self.clock())
            if claim is None:
                continue

            def guard(session: Session, owned: QuoteSamplingClaim = claim) -> None:
                if checkpoint is not None:
                    checkpoint()
                require_claim(session, owned, self.clock())

            store = QuoteStore(self.sessions, self.archive, clock=self.clock, writer_guard=guard)
            collector = QuoteCaptureService(store, transport=self.transport)
            quote, failure, status, retry = None, None, "captured", 5
            try:
                quote = asyncio.run(collector.collect_once(scope=scope))
            except Exception as exc:
                failure, status, retry = (str(exc) or type(exc).__name__)[:160], "failed", 60
                if _sqlite_lock_contention(exc) or isinstance(
                    exc, (httpx.TransportError, TimeoutError)
                ) or str(exc) in {
                    "action_quote_future_or_stale",
                    "action_quote_clock_sample_stale",
                    "action_quote_clock_uncertain",
                }:
                    # One new request on the next normal demand slot; no inner retry.
                    # A sixty-second backoff consumes the entire T0 opening window.
                    retry = 5
                if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code >= 500:
                    retry = 5
                if (
                    isinstance(exc, httpx.HTTPStatusError)
                    and exc.response.status_code in {401, 403}
                ) or str(exc) == "action_quote_provider_rejected":
                    status = "rejected"
            with self.sessions() as session, session.begin():
                writer_lock(session)
                finish_scope(
                    session,
                    claim,
                    now=self.clock(),
                    event=status,
                    capture_ids=collector.capture_ids,
                    quote_id=quote.quote_id if quote else None,
                    error=failure,
                    retry_seconds=retry,
                )
            captured += int(quote is not None)
            if checkpoint is not None:
                checkpoint()
        return captured

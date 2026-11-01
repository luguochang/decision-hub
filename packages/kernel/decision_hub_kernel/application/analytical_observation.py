"""Durable, fenced observations of frozen research hypotheses; no trading authority."""

from __future__ import annotations

import asyncio
import math
import uuid
from contextlib import nullcontext
from datetime import timedelta

from sqlalchemy import or_

from packages.kernel.decision_hub_kernel.application.market_scope import outcome_scope_enabled
from packages.kernel.decision_hub_kernel.persistence.db import (
    AnalyticalObservationAttemptRecord as Attempt,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    AnalyticalObservationRecord as Observation,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    AnalyticalOutlookRecord as Outlook,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    as_utc,
    utcnow,
)


class ObservationInputError(ValueError):
    pass


def measure_window(outlook, window, *, now):
    """A common price benchmark, independent of the model's direction/probability."""
    if window is None:
        raise ObservationInputError("market_window_missing")
    if window.quality_status not in {"observed", "estimated"}:
        raise ObservationInputError("market_window_quality_invalid")
    entry, exit_quote = window.entry, window.exit
    if entry.benchmark != exit_quote.benchmark or entry.source_id != exit_quote.source_id:
        raise ObservationInputError("market_window_inconsistent")
    if entry.benchmark not in {"close_1m", "first_executable", "last"}:
        raise ObservationInputError("market_benchmark_not_audited")
    prices = []
    for quote, boundary in ((entry, outlook.window_start_at), (exit_quote, outlook.window_end_at)):
        if quote.instrument != outlook.instrument:
            raise ObservationInputError("market_instrument_mismatch")
        observed = as_utc(quote.observed_at)
        received = as_utc(quote.received_at)
        if not as_utc(boundary) <= observed < as_utc(boundary) + timedelta(minutes=1):
            raise ObservationInputError("market_timestamp_misaligned")
        if observed > received or received > now:
            raise ObservationInputError("market_timestamp_future")
        if quote.quality_status not in {"observed", "estimated"}:
            raise ObservationInputError("market_quote_quality_invalid")
        # This is a directional benchmark, not an executable P&L. With a
        # contemporaneous spread use its midpoint for both long and short.
        if quote.benchmark == "first_executable":
            if quote.bid is None or quote.ask is None or quote.ask < quote.bid:
                raise ObservationInputError("market_spread_invalid")
            price = (quote.bid + quote.ask) / 2
        else:
            price = quote.last
        if price is None or not math.isfinite(price) or price <= 0:
            raise ObservationInputError("market_price_invalid")
        prices.append(price)
    market_return = (prices[1] / prices[0] - 1) * 100
    directional = (
        market_return
        if outlook.action == "long"
        else -market_return
        if outlook.action == "short"
        else None
    )
    quality = (
        "estimated"
        if "estimated"
        in {
            window.quality_status,
            entry.quality_status,
            exit_quote.quality_status,
        }
        or entry.benchmark == "close_1m"
        else "observed"
    )
    return {
        "market_return_pct": market_return,
        "directional_return_pct": directional,
        "direction_correct": int(directional > 0) if directional is not None else None,
        "quality_status": quality,
        "benchmark": "midpoint" if entry.benchmark == "first_executable" else entry.benchmark,
        "window_json": window.model_dump_json(),
    }


class AnalyticalObservationService:
    def __init__(self, database, market, *, clock=utcnow, capture_context=None):
        self.database = database
        self.market = market
        self.clock = clock
        self.capture_context = capture_context or (lambda _id: nullcontext())

    def recover_expired(self, now):
        with self.database.session() as session:
            rows = (
                session.query(Observation)
                .filter(
                    Observation.status == "running",
                    Observation.lease_expires_at <= now,
                )
                .all()
            )
            for row in rows:
                old_owner = row.lease_owner
                changed = (
                    session.query(Observation)
                    .filter_by(
                        outlook_id=row.outlook_id,
                        status="running",
                        lease_owner=old_owner,
                        attempt=row.attempt,
                    )
                    .filter(Observation.lease_expires_at <= now)
                    .update(
                        {
                            "status": "retry",
                            "lease_owner": None,
                            "lease_expires_at": None,
                            "next_attempt_at": now,
                            "error_code": "observation_interrupted",
                        },
                        synchronize_session=False,
                    )
                )
                if changed:
                    attempt = session.get(Attempt, (row.outlook_id, row.attempt))
                    if attempt:
                        attempt.status = "interrupted"
                        attempt.error_code = "observation_interrupted"
                        attempt.finished_at = now

    def claim(self, outlook_id, now):
        owner = uuid.uuid4().hex
        with self.database.session() as session:
            row = session.get(Observation, outlook_id)
            if not row:
                return None
            attempt = row.attempt + 1
            changed = (
                session.query(Observation)
                .filter(
                    Observation.outlook_id == outlook_id,
                    Observation.attempt == row.attempt,
                    Observation.status.in_(("pending", "retry")),
                    Observation.next_attempt_at <= now,
                    or_(
                        Observation.lease_expires_at.is_(None), Observation.lease_expires_at <= now
                    ),
                )
                .update(
                    {
                        "status": "running",
                        "attempt": attempt,
                        "lease_owner": owner,
                        "lease_expires_at": now + timedelta(minutes=2),
                        "error_code": None,
                    },
                    synchronize_session=False,
                )
            )
            if not changed:
                return None
            session.add(
                Attempt(outlook_id=outlook_id, attempt=attempt, status="running", started_at=now)
            )
        return owner, attempt

    def finish(self, outlook_id, owner, attempt, *, values=None, error=None):
        now = self.clock()
        with self.database.session() as session:
            changed = (
                session.query(Observation)
                .filter_by(
                    outlook_id=outlook_id,
                    lease_owner=owner,
                    attempt=attempt,
                    status="running",
                )
                .filter(Observation.lease_expires_at > now)
                .update(
                    {
                        **(values or {}),
                        "status": "completed" if values else "retry",
                        "lease_owner": None,
                        "lease_expires_at": None,
                        "error_code": error,
                        "evaluated_at": now if values else None,
                        "next_attempt_at": now
                        + timedelta(seconds=min(21600, 60 * 2 ** min(attempt - 1, 9))),
                    },
                    synchronize_session=False,
                )
            )
            if not changed:
                return False
            record = session.get(Attempt, (outlook_id, attempt))
            record.status = "completed" if values else "failed"
            record.finished_at, record.error_code = now, error
            return True

    async def process_due(self, *, now=None, limit=10):
        cutoff = now or self.clock()
        self.recover_expired(cutoff)
        with self.database.session() as session:
            targets = (
                session.query(Outlook)
                .join(
                    Observation,
                    Observation.outlook_id == Outlook.outlook_id,
                )
                .filter(
                    Outlook.eligibility == "eligible",
                    Outlook.cohort == "prospective",
                    Outlook.window_end_at <= cutoff,
                    Observation.status.in_(("pending", "retry")),
                    Observation.next_attempt_at <= cutoff,
                )
                .order_by(Observation.next_attempt_at, Outlook.outlook_id)
                .limit(limit)
                .all()
            )
        processed = 0
        for target in targets:
            claim = self.claim(target.outlook_id, self.clock())
            if not claim:
                continue
            owner, attempt = claim
            try:
                if not outcome_scope_enabled(target.market, target.instrument):
                    raise ObservationInputError("market_scope_not_enabled")
                with self.capture_context(f"{target.outlook_id}:{attempt}"):
                    async with asyncio.timeout(25):
                        window = await self.market.window(
                            target.instrument,
                            as_utc(target.window_start_at),
                            as_utc(target.window_end_at),
                        )
                values = measure_window(target, window, now=self.clock())
            except asyncio.CancelledError:
                self.finish(target.outlook_id, owner, attempt, error="observation_cancelled")
                raise
            except Exception as exc:
                error = (
                    str(exc)
                    if isinstance(exc, ObservationInputError)
                    else "market_provider_" + type(exc).__name__
                )
                self.finish(target.outlook_id, owner, attempt, error=error)
            else:
                processed += int(self.finish(target.outlook_id, owner, attempt, values=values))
        return processed

"""One finite outcome action in the existing managed realtime risk lane."""

from __future__ import annotations

import asyncio
import hashlib
import os
from datetime import timedelta
from pathlib import Path
from threading import Event
from time import monotonic

from sqlalchemy import select

from apps.hub_api.history_outcome_routes import OutcomeBootstrapV2
from packages.kernel.decision_hub_kernel.application.history_observed_dataset import encoded
from packages.kernel.decision_hub_kernel.application.history_outcome_jobs_v2 import (
    HistoryOutcomeJobsV2,
    HistoryOutcomeJobUnconfirmed,
)
from packages.kernel.decision_hub_kernel.application.history_outcome_recovery_v2 import (
    HistoryOutcomeRecoveryV2,
)
from packages.kernel.decision_hub_kernel.persistence.db import as_utc
from packages.kernel.decision_hub_kernel.persistence.history_outcome_v2_records import (
    HistoryOutcomeProtocolV2Record as Protocol,
)
from packages.kernel.decision_hub_kernel.persistence.history_outcome_v2_records import (
    HistoryOutcomeScoreV2Record as Score,
)
from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash

LOADED_SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


class HistoryOutcomeRuntime40:
    def __init__(self, bootstrap):
        if type(bootstrap) is not OutcomeBootstrapV2:
            raise ValueError("history_outcome_runtime_actual_bootstrap_required")
        bootstrap.__post_init__()
        self.bootstrap = bootstrap
        self.recovery = HistoryOutcomeRecoveryV2(HistoryOutcomeJobsV2(bootstrap.service))
        self.cursor = 0
        self.last_receipt = None
        self._busy = False
        self._identity = bootstrap, bootstrap.service, self.recovery

    def _final_score_if_due(self, protocol, deadline, check):
        service = self.bootstrap.service
        request = "outcome40-final:" + protocol
        key = canonical_hash([service.owner_claim, protocol, request])
        fence = service.archive.root / "runtime-score-fences" / (key + ".json")
        # A saved row alone never resolves a prior unknown COMMIT.
        if fence.exists():
            raise HistoryOutcomeJobUnconfirmed(
                "history_outcome_runtime_score_reconciliation_required"
            )
        with service.commands._read_session() as session:
            row = session.get(Protocol, protocol)
            if row is None or row.owner_claim != service.owner_claim or row.confirmed_at is None:
                raise ValueError("history_outcome_runtime_confirmed_protocol_required")
            if service.now() < as_utc(row.last_hour) + timedelta(days=2, minutes=6):
                return None
            existing = session.scalar(
                select(Score.score_id)
                .where(
                    Score.owner_claim == service.owner_claim,
                    Score.protocol_id == protocol,
                    Score.request_id == request,
                )
                .limit(1)
            )
            if existing is not None:
                return dict(
                    outcome="final_score_already_saved",
                    committed=False,
                    score_id=existing,
                    automatic_retry=False,
                    current_originals_verified=False,
                )
        check()
        body = encoded(dict(protocol_id=protocol, request_id=request, automatic_retry=False))
        fence.parent.mkdir(parents=True, exist_ok=True)
        try:
            with fence.open("xb") as handle:
                handle.write(body)
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError:
            raise HistoryOutcomeJobUnconfirmed(
                "history_outcome_runtime_score_reconciliation_required"
            ) from None
        try:
            with service.commands._read_session() as session:
                packet = service.prepare_score(
                    session,
                    protocol,
                    request,
                    budget_seconds=deadline - monotonic(),
                    checkpoint=check,
                )
            check()
            value = service.save(packet, checkpoint=check)[0]
            if fence.read_bytes() != body:
                raise ValueError("history_outcome_runtime_score_fence_changed")
            check()
            fence.unlink()
            return dict(
                outcome="final_score_saved",
                committed=True,
                score_id=value.score_id,
                expected_total=48,
                paired_count=value.paired_count,
                automatic_retry=False,
                current_originals_verified=False,
                financial_qualified=False,
            )
        except BaseException as error:
            raise HistoryOutcomeJobUnconfirmed(
                "history_outcome_runtime_score_reconciliation_required"
            ) from error

    async def tick(self, budget_seconds):
        if (
            self._identity != (self.bootstrap, self.bootstrap.service, self.recovery)
            or hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != LOADED_SOURCE_SHA256
            or any(
                getattr(HistoryOutcomeRuntime40, name) is not fn or fn.__code__ is not code
                for name, fn, code in LOADED_METHODS
            )
        ):
            raise ValueError("history_outcome_runtime_loaded_source_or_binding_changed")
        if self._busy:
            return 0
        self._busy = True
        try:
            return await self._tick(budget_seconds)
        finally:
            self._busy = False

    async def _tick(self, budget_seconds):
        if type(budget_seconds) not in (int, float) or not 0 < budget_seconds <= 120:
            raise ValueError("history_outcome_runtime_finite_budget_required")
        self.bootstrap.__post_init__()
        grants = self.bootstrap.protocol_ids
        if not grants:
            return 0
        protocol = grants[self.cursor % len(grants)]
        self.cursor = (self.cursor + 1) % len(grants)
        deadline, cancelled = monotonic() + budget_seconds, Event()

        def check():
            if cancelled.is_set():
                raise ValueError("history_outcome_runtime_cancelled")
            if monotonic() >= deadline:
                raise TimeoutError("history_outcome_runtime_deadline_elapsed")

        def process():
            check()
            final = self._final_score_if_due(protocol, deadline, check)
            if final is not None:
                return final
            return self.recovery.tick(
                protocol, budget_seconds=deadline - monotonic(), checkpoint=check
            )

        work = asyncio.create_task(asyncio.to_thread(process))
        try:
            value = await asyncio.shield(work)
        except asyncio.CancelledError:
            cancelled.set()
            while not work.done():
                try:
                    await asyncio.shield(work)
                except asyncio.CancelledError:
                    cancelled.set()
                except Exception:
                    break
            if work.done() and not work.cancelled():
                work.exception()
            raise
        except HistoryOutcomeJobUnconfirmed:
            # Durable per-opportunity fences remain in place. The next tick may
            # inspect another configured protocol, never replay this claim.
            self.last_receipt = dict(
                protocol_id=protocol,
                outcome="unconfirmed",
                reason_code="history_outcome_action_requires_readonly_reconciliation",
                automatic_retry=False,
            )
            return 0
        except (ValueError, TimeoutError):
            self.last_receipt = dict(
                protocol_id=protocol,
                outcome="blocked",
                reason_code="history_outcome_runtime_preparation_unavailable",
                automatic_retry=False,
            )
            return 0
        self.last_receipt = dict(protocol_id=protocol, **value)
        return int(value.get("committed") is True)


def install_history_outcome40_on_risk_lane(realtime, *, bootstrap=None):
    """No process/config/protocol creation. Invoke after the existing hour hook."""
    if bootstrap is None:
        return None
    scheduler = realtime.scheduler
    if getattr(scheduler, "_history_outcome_v2", None) is not None:
        raise ValueError("history_outcome_risk_lane_already_bound")
    runtime, predecessor = HistoryOutcomeRuntime40(bootstrap), scheduler.risk_enhancement_tick

    async def combined():
        started = monotonic()
        count = 0 if predecessor is None else await predecessor()
        remaining = min(120.0, 115.0 - (monotonic() - started))
        # Full original readers must have a finite useful budget. A slow
        # predecessor defers the action, preserving the original due times.
        return count if remaining < 45 else count + await runtime.tick(remaining)

    scheduler.risk_enhancement_tick = combined
    scheduler._history_outcome_v2 = runtime
    return runtime


LOADED_METHODS = tuple(
    (name, fn, fn.__code__)
    for name, fn in HistoryOutcomeRuntime40.__dict__.items()
    if callable(fn) and hasattr(fn, "__code__")
)

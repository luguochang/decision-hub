"""Cancelled DSH session maintenance, separate from research claims and usage collection."""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
from collections.abc import Mapping
from typing import Final, Protocol, cast

from packages.kernel.decision_hub_kernel.ports.research import ResearchHarnessRuntime

log = logging.getLogger(__name__)

DEFAULT_RECOVERY_TIME_BUDGET_SECONDS: Final[float] = 8.0


def cancelled_session_recovery_time_budget() -> float:
    """Maintenance wait budget; never extends the original model deadline."""
    configured = os.getenv(
        "DECISION_HUB_CANCELLED_SESSION_RECOVERY_TIME_BUDGET_SECONDS", "",
    ).strip()
    if not configured:
        return DEFAULT_RECOVERY_TIME_BUDGET_SECONDS
    try:
        value = float(configured)
    except ValueError as exc:
        raise ValueError("cancelled_session_recovery_budget_invalid") from exc
    if not math.isfinite(value) or not 0 < value <= 30:
        raise ValueError("cancelled_session_recovery_budget_invalid")
    return value


class CancelledSessionRecovery(Protocol):
    async def reconcile_cancelled_runs(
        self, *, limit: int = 4,
        time_budget_seconds: float = DEFAULT_RECOVERY_TIME_BUDGET_SECONDS,
    ) -> object: ...


def cancelled_session_recovery(runtime: ResearchHarnessRuntime) -> CancelledSessionRecovery | None:
    """Optional maintenance capability; replay and SDK ports keep their existing lifecycle."""
    if not callable(getattr(runtime, "reconcile_cancelled_runs", None)):
        return None
    return cast(CancelledSessionRecovery, runtime)


async def recover_cancelled_sessions_once(
    recovery: CancelledSessionRecovery, *, limit: int = 4,
    time_budget_seconds: float = DEFAULT_RECOVERY_TIME_BUDGET_SECONDS,
) -> object | None:
    if limit <= 0 or not math.isfinite(time_budget_seconds) or time_budget_seconds <= 0:
        raise ValueError("cancelled_session_recovery_budget_invalid")
    try:
        # Bounds cooperative awaits. It does not kill an already-running DB
        # thread or prove the remote Host has stopped; its durable state can
        # still be reconciled on a later maintenance tick.
        async with asyncio.timeout(time_budget_seconds + 0.5):
            return await recovery.reconcile_cancelled_runs(
                limit=limit, time_budget_seconds=time_budget_seconds,
            )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        # Host error messages may contain URLs or sensitive configuration.
        # Keep maintenance failures independent from the business Run status.
        log.warning("cancelled_session_recovery_pending error_class=%s", type(exc).__name__)
        return None


async def run_cancelled_session_recovery(
    recovery: CancelledSessionRecovery, *, interval_seconds: float = 15,
    limit: int = 4, time_budget_seconds: float = DEFAULT_RECOVERY_TIME_BUDGET_SECONDS,
) -> None:
    if not math.isfinite(interval_seconds) or interval_seconds <= 0:
        raise ValueError("cancelled_session_recovery_interval_invalid")
    if limit <= 0 or not math.isfinite(time_budget_seconds) or time_budget_seconds <= 0:
        raise ValueError("cancelled_session_recovery_budget_invalid")
    previous: dict[str, int] | None = None
    while True:
        result = await recover_cancelled_sessions_once(
            recovery, limit=limit, time_budget_seconds=time_budget_seconds,
        )
        if isinstance(result, Mapping):
            counts = {
                key: value
                for key in ("scanned", "attempted", "confirmed", "pending", "skipped", "backoff")
                if isinstance((value := result.get(key)), int) and not isinstance(value, bool)
            }
            if counts and any(counts.values()) and counts != previous:
                log.info("cancelled_session_recovery %s", json.dumps(counts, sort_keys=True))
            previous = counts
        await asyncio.sleep(interval_seconds)

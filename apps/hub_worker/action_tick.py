"""One bounded synchronous Action monitor owner in the existing scheduler."""

from __future__ import annotations

import asyncio
import math
import threading
from time import monotonic

from packages.kernel.decision_hub_kernel.application.action_monitor import ActionMonitorService


class ActionMonitorTick:
    def __init__(self, service: ActionMonitorService, *, budget_seconds: float = 25) -> None:
        if (
            isinstance(budget_seconds, bool)
            or not math.isfinite(budget_seconds)
            or not 0 < budget_seconds <= 25
        ):
            raise ValueError("action_monitor_tick_budget_invalid")
        self.service = service
        self.budget_seconds = budget_seconds
        self._lock = asyncio.Lock()

    async def tick(self) -> int:
        # The lock also protects against an accidental concurrent one-shot call.
        # It stays owned through cancellation until the actual thread exits.
        async with self._lock:
            stopped = threading.Event()
            deadline = monotonic() + self.budget_seconds

            def checkpoint() -> None:
                if stopped.is_set():
                    raise TimeoutError("action_monitor_tick_cancelled")
                if monotonic() >= deadline:
                    raise TimeoutError("action_monitor_tick_budget_exhausted")

            task = asyncio.create_task(
                asyncio.to_thread(
                    self.service.process_due,
                    max_opportunities=4,
                    max_scopes=2,
                    checkpoint=checkpoint,
                )
            )
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                stopped.set()
                # Cancelling an await cannot stop SQLite/file work. Keep the
                # owner until its cooperative checkpoint/finite batch returns.
                while not task.done():
                    try:
                        await asyncio.shield(task)
                    except asyncio.CancelledError:
                        continue
                    except Exception:
                        break
                if not task.cancelled():
                    task.exception()
                raise

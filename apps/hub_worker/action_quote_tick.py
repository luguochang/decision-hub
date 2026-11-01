"""Existing scheduler channel draft; hold owner until blocking work really ends."""

from __future__ import annotations

import asyncio
import threading
from time import monotonic

from packages.kernel.decision_hub_kernel.application.action_quote_sampling import (
    QuoteSamplingService,
)


class ActionQuoteTick:
    def __init__(self, service: QuoteSamplingService):
        self.service = service
        self._lock = asyncio.Lock()

    async def tick(self) -> int:
        async with self._lock:
            stopped = threading.Event()
            deadline = monotonic() + 70

            def checkpoint() -> None:
                if stopped.is_set() or monotonic() >= deadline:
                    raise TimeoutError("action_quote_tick_stopped")

            task = asyncio.create_task(
                asyncio.to_thread(
                    self.service.process_due,
                    max_scopes=2,
                    checkpoint=checkpoint,
                )
            )
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                stopped.set()
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

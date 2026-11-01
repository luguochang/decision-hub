"""Existing normal scheduler channel, cooperative cancellation joins its owner."""

from __future__ import annotations

import asyncio
import logging
import threading
from time import monotonic


class ActionExecutionTick:
    def __init__(self, service, *, bootstrap=None):
        self.service = service
        self.bootstrap = bootstrap
        self._lock = asyncio.Lock()

    async def tick(self) -> int:
        async with self._lock:
            stopped = threading.Event()
            deadline = monotonic() + 25

            def checkpoint():
                if stopped.is_set() or monotonic() >= deadline:
                    raise TimeoutError("action_execution_tick_stopped")

            def process():
                if self.bootstrap is not None:
                    self.bootstrap.process_due(max_registrations=4, checkpoint=checkpoint)
                return self.service.process_due(max_intents=8, checkpoint=checkpoint)

            task = asyncio.create_task(asyncio.to_thread(process))
            try:
                result = await asyncio.shield(task)
                if result["errors"]:
                    logging.getLogger(__name__).error(
                        "Action execution tick failed: %s", result["errors"]
                    )
                    raise ValueError("action_execution_tick_original_or_state_failure")
                return result["processed"]
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

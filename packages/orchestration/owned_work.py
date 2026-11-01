"""One owned native callback; grants no business rights or executor of its own."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Any


class HeavyWorkAdmission:
    """Keep admission until a submitted callback and its Sessions really exit."""

    def __init__(self):
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def admitted(self):
        # FIFO admission carries no Task, source, lease or permission verdict.
        async with self._lock:
            yield

    async def run_thread[T](self, function: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        async with self.admitted():
            task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
            cancelled = False
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    cancelled = True
                except BaseException:
                    if not task.done():
                        raise
                    break
            # Cancellation stops the caller after the original native work,
            # including its finally/Session close, not after an async timeout.
            if cancelled:
                try:
                    task.result()
                except BaseException:
                    pass
                raise asyncio.CancelledError
            return task.result()

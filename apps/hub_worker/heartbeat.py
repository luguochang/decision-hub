"""Keep process health independent of a job or a long idle cadence."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import asynccontextmanager


@asynccontextmanager
async def keep_heartbeat(beat: Callable[[], None], interval_seconds: float = 10):
    beat()

    async def loop():
        while True:
            await asyncio.sleep(interval_seconds)
            try:
                await asyncio.to_thread(beat)
            except Exception:
                continue

    task = asyncio.create_task(loop())
    try:
        yield
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

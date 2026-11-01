"""Join finite synchronous learning work before relinquishing its lease owner."""

from __future__ import annotations

import asyncio
from collections.abc import Callable


async def settle_learning_task(task: asyncio.Future) -> bool:
    """Drain actual completion, including repeated outer cancellation.

    Return whether cleanup itself received cancellation. The caller preserves
    that signal; retrieving an exception here only prevents orphan warnings.
    """
    interrupted = False
    owner = asyncio.current_task()
    cancellations = owner.cancelling() if owner else 0
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            current = owner.cancelling() if owner else 0
            interrupted |= current > cancellations
            cancellations = current
        except Exception:
            break
    if not task.cancelled():
        task.exception()
    return interrupted


async def learning_sync[T](function: Callable[..., T], *args, **kwargs) -> T:
    """Keep model I/O on its original loop; offload only the supplied sync call."""
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await settle_learning_task(task)
        raise

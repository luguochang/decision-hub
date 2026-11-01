"""Bounded same-hour acquisition receipts, isolated to one Entry's fixed source.

This caches only original capture IDs and their original clock. Original readers
still read and authenticate the archive. A threading lock protects short memory
updates; concurrent futures distribute completion across worker event loops.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from concurrent.futures import Future
from datetime import datetime
from threading import Lock

from ..ports.market_path import aware_utc
from . import perpetual_base_rate_source as history_source
from .perpetual_base_rate_source import CollectedHourlyHistory

type _Key = tuple[str, str, datetime]
type _Result = CollectedHourlyHistory | None


class PerpetualHistoryCache:
    """Coalesce bounded optional acquisitions; never combine pages or renew clocks."""

    MAX_ENTRIES = 8

    def __init__(self, source: object) -> None:
        self._source = source
        self._lock = Lock()
        self._success: OrderedDict[_Key, CollectedHourlyHistory] = OrderedDict()
        self._pending: dict[_Key, Future[_Result]] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    @staticmethod
    def _hour() -> datetime:
        return aware_utc(history_source.utcnow()).replace(minute=0, second=0, microsecond=0)

    async def collect(self, *, inst_id: str) -> _Result:
        key = (history_source.VERSION, inst_id, self._hour())
        with self._lock:
            for old in tuple(self._success):
                if old[0] != key[0] or old[2] != key[2]:
                    del self._success[old]
            value = self._success.get(key)
            if value is not None:
                self._success.move_to_end(key)
                return value
            shared = self._pending.get(key)
            start = shared is None
            if shared is None:
                if len(self._pending) >= self.MAX_ENTRIES:
                    return None
                shared = Future()
                self._pending[key] = shared
        if start:
            task = asyncio.create_task(self._produce(key, shared))
            with self._lock:
                self._tasks.add(task)
            task.add_done_callback(lambda done: self._producer_done(key, shared, done))
        # Cancellation of this caller must not cancel either the shared receipt
        # or its producer. Each event loop owns only its own wrapper future.
        return await asyncio.shield(asyncio.wrap_future(shared))

    async def _produce(self, key: _Key, shared: Future[_Result]) -> None:
        try:
            value = await history_source.collect_hourly_history_or_none(
                self._source, inst_id=key[1]
            )
            self._finish(key, shared, value=value)
        except BaseException as error:
            self._finish(key, shared, error=error)

    def _finish(
        self,
        key: _Key,
        shared: Future[_Result],
        *,
        value: _Result = None,
        error: BaseException | None = None,
    ) -> None:
        reusable = error is None and value is not None and self._hour() == key[2]
        with self._lock:
            if self._pending.get(key) is not shared:
                return
            del self._pending[key]
            if reusable and value is not None:
                self._success[key] = value
                self._success.move_to_end(key)
                while len(self._success) > self.MAX_ENTRIES:
                    self._success.popitem(last=False)
        # Completion invokes foreign-loop callbacks, so publish outside the lock.
        if isinstance(error, asyncio.CancelledError):
            shared.cancel()
        elif error is not None:
            shared.set_exception(error)
        else:
            shared.set_result(value)

    def _producer_done(
        self, key: _Key, shared: Future[_Result], task: asyncio.Task[None]
    ) -> None:
        with self._lock:
            self._tasks.discard(task)
        # A task cancelled before its first coroutine step cannot enter _produce's
        # try block. Loop shutdown must still release every cross-loop waiter.
        error = asyncio.CancelledError() if task.cancelled() else task.exception()
        if error is not None:
            self._finish(key, shared, error=error)

"""Bounded thread lane for short SQLite cancellation reads and projections."""
from __future__ import annotations

import asyncio
import re
import sqlite3
import threading
import weakref
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import TypeVar
from uuid import uuid4

from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts import DshRunSessionLinkView, DshSessionCompletion
from packages.kernel.decision_hub_kernel.application.dsh_sessions import DshSessionLinkService
from packages.kernel.decision_hub_kernel.persistence.db import DshSessionLinkRecord, RunRecord
from packages.kernel.decision_hub_kernel.persistence.dsh_projection import DshProjectionSessions

T = TypeVar("T")
type ProjectionStageObserver = Callable[[str, str, str | None], None]


def _sqlite_read_contention(error: OperationalError) -> bool:
    """Only native SQLite BUSY/LOCKED is eligible for another complete RO read."""
    original = error.orig
    code = getattr(original, "sqlite_errorcode", None)
    return bool(
        isinstance(original, sqlite3.OperationalError)
        and type(code) is int
        and code >= 0
        and (code & 0xFF) in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
    )


@dataclass
class _ProjectionProgress:
    # One safe string is diagnostic only; it never decides transaction state.
    latest_stage: str | None = None


def _report_stage(
    observer: ProjectionStageObserver | None, operation_id: str, stage: str,
    code: str | None = None, *, progress: _ProjectionProgress | None = None,
) -> None:
    if progress is not None:
        progress.latest_stage = stage
    if observer is not None:
        try:
            observer(operation_id, stage, code)
        except Exception:
            # Diagnostics never decide transaction outcome or authority.
            pass


@dataclass(frozen=True)
class ProjectionRead[T]:
    state: str
    value: T | None = None
    limitation: str | None = None
    pending: ProjectionPending[T] | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class ProjectionPending[T]:
    """Observe the one real operation; this handle cannot submit or replay it."""

    operation_id: str
    _future: Future[ProjectionRead[T]] = field(repr=False, compare=False)
    _progress: _ProjectionProgress = field(repr=False, compare=False)
    _cancel_operation: Callable[[], None] | None = field(default=None, repr=False, compare=False)

    def request_cancel(self) -> None:
        """Notify the owned callback checkpoint; never cancel/replay its Future."""
        if self._cancel_operation is not None:
            self._cancel_operation()

    @property
    def latest_stage(self) -> str | None:
        return self._progress.latest_stage

    def poll(self) -> ProjectionRead[T]:
        """No additional wait, SQLite call, transaction, or authority check."""
        if not self._future.done():
            return ProjectionRead(
                "pending", limitation="cancellation_projection_wait_elapsed", pending=self,
            )
        if self._future.cancelled():
            return ProjectionRead("pending", limitation="cancellation_projection_closed")
        try:
            return self._future.result()
        except BaseException:
            # A late thread exception is consumed, never mistaken for commit.
            self._progress.latest_stage = "failed"
            return ProjectionRead("pending", limitation="cancellation_projection_failed")

    def observe(self, callback: Callable[[ProjectionRead[T]], None]) -> None:
        """A completion observer runs after the original Session has finished."""
        self._future.add_done_callback(lambda _future: callback(self.poll()))


class DshCancellationProjector:
    """At most two real outstanding threads, including timed-out operations.

    Async timeout stops waiting; the slot is retained until the thread really
    finishes. It does not kill a thread, filesystem operation, or SQLite call.
    """

    def __init__(self, links: DshSessionLinkService) -> None:
        self._slots = threading.BoundedSemaphore(2)
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="dsh-cancel-db")
        self._closed = False
        self._observations = {}
        self._observation_lock = threading.Lock()
        self.unsupported: str | None = None
        self.sessions: DshProjectionSessions | None = None
        try:
            self.sessions = DshProjectionSessions(links.database)
        except ValueError as exc:
            self.unsupported = str(exc)
        self._links = DshSessionLinkService(
            links.database, clock=links.clock,
            completion_sessions=self.sessions.session if self.sessions else None,
        )

    async def _call(
        self, operation: Callable[[], T], timeout_seconds: float, *,
        operation_id: str | None = None,
        stage_observer: ProjectionStageObserver | None = None,
        progress: _ProjectionProgress | None = None,
        read_only: bool = False, observation_scope: object | None = None,
        drain_on_cancel: bool = False,
        cancel_operation: Callable[[], None] | None = None,
    ) -> ProjectionRead[T]:
        if self._closed:
            return ProjectionRead("pending", limitation="cancellation_projection_closed")
        if self.unsupported:
            return ProjectionRead("pending", limitation=self.unsupported)
        if timeout_seconds <= 0 or not self._slots.acquire(blocking=False):
            return ProjectionRead("pending", limitation="cancellation_projection_busy")
        operation_id = operation_id or uuid4().hex
        progress = progress or _ProjectionProgress()

        def execute() -> ProjectionRead[T]:
            try:
                return ProjectionRead("ready", operation())
            except OperationalError as error:
                code = (
                    "cancellation_projection_read_busy"
                    if read_only and _sqlite_read_contention(error)
                    else "cancellation_projection_db_busy"
                )
                _report_stage(
                    stage_observer, operation_id, "failed", code,
                    progress=progress,
                )
                return ProjectionRead("pending", limitation=code)
            except ValueError as exc:
                code = str(exc)
                code = (
                    code if re.fullmatch(r"[a-z][a-z0-9_]{0,100}", code)
                    else "cancellation_projection_invalid"
                )
                _report_stage(stage_observer, operation_id, "failed", code, progress=progress)
                return ProjectionRead("invalid", limitation=code)
            except Exception:
                _report_stage(
                    stage_observer, operation_id, "failed", "cancellation_projection_failed",
                    progress=progress,
                )
                return ProjectionRead("pending", limitation="cancellation_projection_failed")

        # Announce the submission attempt before any worker can emit BEGIN.
        # This stage is not evidence of an accepted future or a committed row.
        _report_stage(stage_observer, operation_id, "queued", progress=progress)
        try:
            future = self._executor.submit(execute)
        except RuntimeError:
            self._slots.release()
            _report_stage(
                stage_observer, operation_id, "failed", "cancellation_projection_closed",
                progress=progress,
            )
            return ProjectionRead("pending", limitation="cancellation_projection_closed")
        future.add_done_callback(lambda _future: self._slots.release())
        pending = ProjectionPending(operation_id, future, progress, cancel_operation)
        def discard(reference):
            with self._observation_lock:
                saved = self._observations.get(operation_id)
                if saved is not None and saved[0] is reference:
                    self._observations.pop(operation_id, None)
        reference = weakref.ref(pending, discard)
        with self._observation_lock:
            self._observations[operation_id] = (reference, read_only, observation_scope)

        wrapped = asyncio.wrap_future(future)
        # The shielded wrapper can outlive its waiter. Retrieve a late exception
        # even when no caller polls the handle; the real result remains available.
        wrapped.add_done_callback(lambda completed: (
            None if completed.cancelled() else completed.exception()
        ))
        try:
            async with asyncio.timeout(timeout_seconds):
                return await asyncio.shield(wrapped)
        except TimeoutError:
            return ProjectionRead(
                "pending", limitation="cancellation_projection_wait_elapsed", pending=pending,
            )
        except asyncio.CancelledError:
            if cancel_operation is not None:
                cancel_operation()
            if drain_on_cancel:
                # The owned callback closes its Sessions and source handles in
                # this native thread. Cancellation cannot release its caller's
                # execution ownership while that work is still in flight.
                while not wrapped.done():
                    try:
                        await asyncio.shield(wrapped)
                    except asyncio.CancelledError:
                        continue
                if not wrapped.cancelled():
                    wrapped.result()
            raise

    def owns_pending(self, pending, *, read_only: bool, observation_scope: object) -> bool:
        """Origin of this one native submitted operation, never execution permission.

        Public ProjectionPending constructors/UUIDs cannot mint an issuer record.
        Weak handles die with their caller; no Task/value/verified state is cached.
        """
        if self._closed or type(pending) is not ProjectionPending or observation_scope is None:
            return False
        with self._observation_lock:
            saved = self._observations.get(pending.operation_id)
            return (saved is not None and saved[0]() is pending
                    and saved[1] is read_only and saved[2] is observation_scope)

    async def await_pending(
        self, pending, *, timeout_seconds: float, read_only: bool, observation_scope: object
    ) -> ProjectionRead:
        """Observe this same submitted Future; never re-run its callback.

        Only the caller's original deadline supplies the wait budget. Ordinary
        read/write confirmation windows and all other consumers are unchanged.
        """
        if not self.owns_pending(
            pending, read_only=read_only, observation_scope=observation_scope
        ):
            return ProjectionRead("invalid", limitation="cancellation_projection_observation_invalid")
        if timeout_seconds <= 0:
            return pending.poll()
        wrapped = asyncio.wrap_future(pending._future)
        wrapped.add_done_callback(lambda completed: (
            None if completed.cancelled() else completed.exception()
        ))
        try:
            async with asyncio.timeout(timeout_seconds):
                observed = await asyncio.shield(wrapped)
                if self._closed:
                    return ProjectionRead("invalid", limitation="cancellation_projection_closed")
                return observed
        except TimeoutError:
            return pending.poll()

    async def read(
        self, operation: Callable[[Session], T], *, timeout_seconds: float = 0.25,
        observation_scope: object | None = None,
    ) -> ProjectionRead[T]:
        def read_in_thread() -> T:
            assert self.sessions is not None
            with self.sessions.session() as session:
                # SQLite's legacy SELECT handling otherwise leaves related
                # receipts in separate snapshots during a generation advance.
                session.connection().exec_driver_sql("BEGIN")
                return operation(session)
        return await self._call(
            read_in_thread, timeout_seconds, read_only=True, observation_scope=observation_scope
        )

    async def read_owned(
        self, operation: Callable[[], T], *, timeout_seconds: float = 0.25,
    ) -> ProjectionRead[T]:
        """One RO callback owning all its physical snapshots in this thread."""
        return await self._call(
            operation, timeout_seconds, read_only=True, drain_on_cancel=True,
        )

    async def write(
        self, operation: Callable[[Session], T], *, timeout_seconds: float = 0.25,
        stage_observer: ProjectionStageObserver | None = None,
        observation_scope: object | None = None,
        drain_on_cancel: bool = False,
    ) -> ProjectionRead[T]:
        """One caller callback, physical writer transaction and confirmed commit.

        Timeout only stops the async wait. A running callback may still commit;
        its real thread retains its slot and Session until it has finished.
        A caller must not treat pending as either rollback or permission to RPC.
        Opt-in cancellation drain retains ownership until this callback exits;
        ordinary callers retain their existing cancellation behavior.
        """
        operation_id = uuid4().hex
        progress = _ProjectionProgress()

        def write_in_thread() -> T:
            assert self.sessions is not None
            with self.sessions.session() as session:
                connection = session.connection()
                # Raw research receipts have a five-second confirmation budget.
                # A transient writer must not fail them after the connection's
                # 50 ms read/maintenance default. Wait once, within this call's
                # budget, before the callback starts; never replay a callback or
                # an uncertain commit. NullPool closes this private connection.
                writer_wait_ms = max(1, min(5000, int(timeout_seconds * 1000)))
                connection.exec_driver_sql(f"PRAGMA busy_timeout={writer_wait_ms}")
                connection.exec_driver_sql("BEGIN IMMEDIATE")
                _report_stage(stage_observer, operation_id, "begin_acquired", progress=progress)
                result = operation(session)
                _report_stage(
                    stage_observer, operation_id, "operation_completed", progress=progress,
                )
            # The context commits before this value can become ready.
            _report_stage(stage_observer, operation_id, "commit_done", progress=progress)
            return result
        return await self._call(
            write_in_thread, timeout_seconds, operation_id=operation_id,
            stage_observer=stage_observer, progress=progress, observation_scope=observation_scope,
            drain_on_cancel=drain_on_cancel,
        )

    async def write_owned(
        self, operation: Callable[[Callable[[], None], Callable[[str], None]], T], *,
        timeout_seconds: float = 0.25,
        stage_observer: ProjectionStageObserver | None = None,
    ) -> ProjectionRead[T]:
        """One owned RO→writer operation; the callback owns each physical Session."""
        operation_id = uuid4().hex
        progress = _ProjectionProgress()
        cancelled = threading.Event()

        def check() -> None:
            if cancelled.is_set():
                raise ValueError("research_runtime_preparation_cancelled")

        def stage(name: str) -> None:
            _report_stage(stage_observer, operation_id, name, progress=progress)

        return await self._call(
            lambda: operation(check, stage), timeout_seconds,
            operation_id=operation_id, stage_observer=stage_observer, progress=progress,
            drain_on_cancel=True, cancel_operation=cancelled.set,
        )

    async def authorizes_cancel(
        self, run_id: str, session_id: str, generation: int, request_hash: str,
        *, timeout_seconds: float = 0.25,
    ) -> bool:
        def authorized(session: Session) -> bool:
            run = session.get(RunRecord, run_id)
            link = session.get(DshSessionLinkRecord, run_id)
            return bool(
                run is not None and run.status == "cancelled" and link is not None
                and link.dsh_session_id == session_id and link.generation == generation
                and link.request_hash == request_hash
            )
        result = await self.read(authorized, timeout_seconds=timeout_seconds)
        return result.state == "ready" and result.value is True

    async def project(
        self, completion: DshSessionCompletion, request_hash: str,
        *, timeout_seconds: float = 0.25,
        binding_validator: Callable[[Session], None] | None = None,
    ) -> ProjectionRead[DshRunSessionLinkView]:
        result = await self._call(lambda: self._links.complete(
            completion, require_cancelled=True, expected_request_hash=request_hash,
            binding_validator=binding_validator,
        ), timeout_seconds)
        if result.state != "ready" or result.value is None:
            return result
        view = result.value
        if (
            view.run_id != completion.run_id
            or view.dsh_session_id != completion.dsh_session_id
            or view.generation != completion.generation
            or view.state != completion.terminal_status
        ):
            return ProjectionRead("invalid", view, "cancellation_projection_stale_binding")
        return ProjectionRead("confirmed", view)

    async def close(self) -> None:
        self._closed = True
        # No new work; active threads retain their connection and slot until
        # their own finally closes it. NullPool never stores pooled sessions.
        self._executor.shutdown(wait=False, cancel_futures=True)
        if self.sessions:
            self.sessions.close()

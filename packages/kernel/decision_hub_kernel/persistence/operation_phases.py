"""STAGE ONLY: bounded phase observations, never permission or retry policy."""
from __future__ import annotations

import os
import re
import sqlite3
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter
from typing import Literal
from uuid import uuid4

from sqlalchemy import event
from sqlalchemy.orm import Session

Phase = Literal[
    "auth_prepared_execution", "auth_current_permission", "auth_model_context", "auth_recipe_exit",
    "source_capture", "original_auth", "ro_readset", "ro_source_recheck",
    "writer_wait", "writer_verify", "writer_after_writes", "writer_fence",
    "writer_lifetime", "writer_source", "writer_readset", "writer_after_source",
    "writer_after_rows", "ro_rows",
    "risk_summary_research", "risk_summary_plan_acquisition", "risk_summary_monitors_alerts",
    "risk_summary_bar_load_verify", "risk_summary_metrics", "risk_summary_view_projection",
]
State = Literal["started", "finished", "error", "acquired", "released"]


@dataclass(frozen=True)
class PhaseObservation:
    operation_id: str
    process_id: int
    phase: Phase
    state: State
    observed_at: str
    elapsed_ms: float
    error_type: str | None = None
    sqlite_error_code: int | None = None


class OperationPhases:
    """One explicit call-local sink. Sink failure cannot change business work."""

    def __init__(
        self, sink: Callable[[PhaseObservation], None] | None = None,
        *, operation_id: str | None = None, clock: Callable[[], float] = perf_counter,
    ) -> None:
        self.operation_id = (
            operation_id if isinstance(operation_id, str)
            and re.fullmatch(r"[a-f0-9]{32}", operation_id) else uuid4().hex
        )
        self._sink, self._clock = sink, clock
        self._count = 0

    def _emit(
        self, phase: Phase, state: State, started: float,
        error: BaseException | None = None,
    ) -> None:
        if self._sink is None or self._count >= 64:
            return
        self._count += 1
        try:
            underlying = getattr(error, "orig", error)
            sqlite_code = (
                getattr(underlying, "sqlite_errorcode", None)
                if isinstance(underlying, sqlite3.Error) else None
            )
            self._sink(PhaseObservation(
                self.operation_id, os.getpid(), phase, state, datetime.now(UTC).isoformat(),
                round((self._clock() - started) * 1000, 3),
                type(error).__name__ if error is not None else None,
                sqlite_code if type(sqlite_code) is int else None,
            ))
        except Exception:
            # Deliberately do not expose exception messages, SQL, parameters,
            # request identities or any credential-bearing logging fallback.
            pass

    @contextmanager
    def phase(self, phase: Phase) -> Generator[None]:
        if self._sink is None:
            yield
            return
        try:
            started = self._clock()
        except Exception:
            yield
            return
        self._emit(phase, "started", started)
        try:
            yield
        except BaseException as error:
            self._emit(phase, "error", started, error)
            raise
        else:
            self._emit(phase, "finished", started)

    def writer_acquired(self, session: Session) -> None:
        """Call immediately after successful physical BEGIN IMMEDIATE only.

        Never infer release from service return. SQLAlchemy's commit/rollback
        hook must additionally observe that the actual SQLite transaction ended.
        Missing release remains unconfirmed, including process termination.
        """
        if self._sink is None:
            return
        try:
            native = session.connection().connection.driver_connection
            if not isinstance(native, sqlite3.Connection) or not native.in_transaction:
                return
            started = self._clock()
            self._emit("writer_lifetime", "acquired", started)
            released = False

            def release(_session: Session) -> None:
                nonlocal released
                try:
                    if released or native.in_transaction:
                        return
                except Exception:
                    # A closed/invalidated original connection cannot prove
                    # release. Diagnostic uncertainty must not mask rollback.
                    return
                released = True
                self._emit("writer_lifetime", "released", started)

            event.listen(session, "after_commit", release)
            event.listen(session, "after_rollback", release)
        except Exception:
            # Diagnostics are not a transaction participant or authority gate.
            pass

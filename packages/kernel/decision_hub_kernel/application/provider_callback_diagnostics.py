"""Lossy local timing observations, never callback permission or commit proof.

Only a trusted launch environment selects the JSONL path. The request thread
never waits for disk; a missing line remains unknown, including process exit.
ASGI body_sent means the server send returned, not that the client received it.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
from collections.abc import Callable, Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from queue import Empty, Full, Queue
from threading import Lock, Thread
from time import perf_counter
from typing import Any
from uuid import uuid4

from sqlalchemy import event
from sqlalchemy.orm import Session

from ..persistence.operation_phases import OperationPhases, PhaseObservation

ENV_PATH = "DECISION_HUB_PROVIDER_CALLBACK_DIAGNOSTICS_PATH"
MAX_EVENTS = 64
MAX_LINE_BYTES = 4096
MAX_FILE_BYTES = 16 * 1024 * 1024
QUEUE_CAPACITY = 256
_PHASES = frozenset(
    {
        "auth_prepared_execution",
        "auth_current_permission",
        "auth_model_context",
        "auth_recipe_exit",
        "source_capture",
        "original_auth",
        "ro_readset",
        "ro_source_recheck",
        "writer_wait",
        "writer_verify",
        "writer_after_writes",
        "writer_fence",
        "writer_lifetime",
        "writer_source",
        "writer_readset",
        "writer_after_source",
        "writer_after_rows",
        "ro_rows",
        "input_verify",
        "postwrite",
        "commit",
        "return",
        "route",
        "response_ready",
        "response_headers_sent",
        "response_body_sent",
        "response_send",
    }
)
_STATES = frozenset(
    {
        "started",
        "finished",
        "error",
        "acquired",
        "released",
        "cancelled",
        "disconnected",
        "response_started",
        "response_sent",
    }
)
_ERROR_TYPES = frozenset(
    {
        "ValueError",
        "TypeError",
        "KeyError",
        "PermissionError",
        "TimeoutError",
        "CancelledError",
        "HTTPException",
        "OperationalError",
        "IntegrityError",
        "OriginalPreparationReadsetChanged",
    }
)
_ERROR_CODES = frozenset(
    {
        "research_provider_callback_lifetime_elapsed",
        "research_provider_callback_budget_invalid",
        "research_provider_run_not_found",
        "research_provider_original_verification_failed",
        "research_provider_callback_conflict",
        "research_provider_callback_path_mismatch",
        "research_provider_generation_invalid",
        "research_provider_input_original_invalid",
        "research_provider_emission_original_invalid",
        "research_provider_finish_original_invalid",
        "research_provider_callback_clock_invalid",
        "research_provider_permission_denied",
        "original_preparation_source_changed",
        "original_preparation_original_changed",
        "original_preparation_readset_changed",
        "research_provider_execution_unavailable",
        "research_provider_model_binding_unavailable",
    }
)


class _AsyncJsonlSink:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.queue: Queue[bytes] = Queue(QUEUE_CAPACITY)
        self.failed = False
        self.dropped = 0
        Thread(target=self._write, name="provider-phase-jsonl", daemon=True).start()

    def __call__(self, record: dict[str, Any]) -> bool:
        try:
            raw = (json.dumps(record, separators=(",", ":"), allow_nan=False) + "\n").encode()
            if self.failed or len(raw) > MAX_LINE_BYTES:
                self.dropped += 1
                return False
            self.queue.put_nowait(raw)
            return True
        except (Full, TypeError, ValueError):
            self.dropped += 1
            return False

    def _write(self) -> None:
        while True:
            try:
                raw = self.queue.get(timeout=1)
            except Empty:
                continue
            try:
                if self.failed:
                    continue
                descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                try:
                    if os.fstat(descriptor).st_size + len(raw) > MAX_FILE_BYTES:
                        self.failed = True
                    elif os.write(descriptor, raw) != len(raw):
                        self.failed = True
                finally:
                    os.close(descriptor)
            except Exception:
                self.failed = True
            finally:
                self.queue.task_done()


_sink_lock = Lock()
_process_sink: _AsyncJsonlSink | None = None


def _configured_sink() -> _AsyncJsonlSink | None:
    global _process_sink
    try:
        text = os.environ.get(ENV_PATH)
        if not text:
            return None
        path = Path(text)
        if not path.is_absolute() or path.suffix != ".jsonl" or not path.parent.is_dir():
            return None
        path = path.resolve()
        with _sink_lock:
            if _process_sink is None:
                _process_sink = _AsyncJsonlSink(path)
            return _process_sink if _process_sink.path == path else None
    except Exception:
        return None


class ProviderCallbackDiagnostics:
    """One server-generated operation, with fixed safe fields and bounded loss."""

    def __init__(
        self,
        payload: Any,
        callback_kind: str,
        budget_ms: int | None = None,
        *,
        sink: Callable[[dict[str, Any]], Any] | None = None,
    ) -> None:
        self.operation_id = uuid4().hex
        self._started = perf_counter()
        self._lock = Lock()
        self._count = self._dropped = 0
        self._sink = sink if sink is not None else _configured_sink()
        run_id = getattr(payload, "run_id", "")
        run_id = run_id if isinstance(run_id, str) else ""
        self.run_id = run_id if re.fullmatch(r"run_[a-f0-9]{32}", run_id) else None
        self.run_hash = hashlib.sha256(run_id.encode(errors="surrogatepass")).hexdigest()
        self.callback_kind = (
            callback_kind if callback_kind in {"input", "emission", "finish"} else "unknown"
        )
        keys = [run_id, getattr(payload, "dispatch_id", ""), self.callback_kind]
        keys.append(getattr(payload, "call_id", "") if self.callback_kind == "emission" else "")
        self.callback_key_hash = hashlib.sha256(
            "\n".join(item if isinstance(item, str) else "" for item in keys).encode(
                errors="surrogatepass"
            )
        ).hexdigest()
        # Observation only. The authenticated route clamps extended receipts to
        # their original Task/step absolute ceiling before the full native proof.
        maximum_ms = 2_147_483_647 if self.callback_kind in {"input", "emission", "finish"} else 30_000
        self.budget_ms = (
            budget_ms if type(budget_ms) is int and 1 <= budget_ms <= maximum_ms else None
        )
        self.operation_phases = OperationPhases(
            self._original_phase if self._sink is not None else None,
            operation_id=self.operation_id,
        )

    def _emit(
        self,
        phase: str,
        state: str,
        elapsed_ms: float,
        *,
        error: BaseException | None = None,
        error_code: str | None = None,
        status_code: int | None = None,
        sqlite_error_code: int | None = None,
    ) -> None:
        if self._sink is None or phase not in _PHASES or state not in _STATES:
            return
        try:
            with self._lock:
                if self._count >= MAX_EVENTS:
                    self._dropped += 1
                    return
                self._count += 1
                dropped = self._dropped
            error_type = type(error).__name__ if error is not None else None
            record = {
                "schema_version": "provider-callback-diagnostics.v1",
                "operation_id": self.operation_id,
                "process_id": os.getpid(),
                "run_id": self.run_id,
                "run_hash": self.run_hash,
                "callback_kind": self.callback_kind,
                "callback_key_hash": self.callback_key_hash,
                "allocated_budget_ms": self.budget_ms,
                "phase": phase,
                "state": state,
                "result_status": (
                    "committed"
                    if (phase, state) == ("commit", "finished")
                    else "returned"
                    if (phase, state) == ("return", "finished")
                    else "error"
                    if state == "error"
                    else "cancelled"
                    if state in {"cancelled", "disconnected"}
                    else "observed"
                ),
                "observed_at": datetime.now(UTC).isoformat(),
                "elapsed_ms": round(max(0, elapsed_ms), 3) if math.isfinite(elapsed_ms) else None,
                "error_type": error_type if error_type in _ERROR_TYPES else None,
                "error_code": error_code if error_code in _ERROR_CODES else None,
                "status_code": status_code
                if type(status_code) is int and 100 <= status_code <= 599
                else None,
                "sqlite_error_code": sqlite_error_code if type(sqlite_error_code) is int else None,
                "dropped_events": dropped,
            }
            if self._sink(record) is False:
                self._dropped += 1
        except Exception:
            self._dropped += 1

    def _original_phase(self, value: PhaseObservation) -> None:
        # Original phases supply only fixed numeric/type projections; no SQL or message.
        self._emit(
            value.phase, value.state, value.elapsed_ms, sqlite_error_code=value.sqlite_error_code
        )

    def event(
        self,
        phase: str,
        state: str = "finished",
        *,
        error: BaseException | None = None,
        error_code: str | None = None,
        status_code: int | None = None,
    ) -> None:
        self._emit(
            phase,
            state,
            (perf_counter() - self._started) * 1000,
            error=error,
            error_code=error_code,
            status_code=status_code,
        )

    @contextmanager
    def phase(self, phase: str) -> Generator[None]:
        started = perf_counter()
        self._emit(phase, "started", 0)
        try:
            yield
        except BaseException as error:
            self._emit(phase, "error", (perf_counter() - started) * 1000, error=error)
            raise
        else:
            self._emit(phase, "finished", (perf_counter() - started) * 1000)

    def asgi_observation(
        self,
        phase: str,
        state: str = "finished",
        error: BaseException | None = None,
        *,
        status_code: int | None = None,
    ) -> None:
        if phase in {
            "response_ready",
            "response_headers_sent",
            "response_body_sent",
            "response_send",
        }:
            self.event(phase, state, error=error, status_code=status_code)

    def observe_commit(self, session: Session) -> None:
        """Observe the existing transaction; never commit, rollback or check permission."""
        if self._sink is None:
            return
        try:
            native = session.connection().connection.driver_connection
            if not isinstance(native, sqlite3.Connection):
                return
            started: float | None = None

            def begin(_session: Session) -> None:
                nonlocal started
                started = perf_counter()
                self._emit("commit", "started", 0)

            def end(_session: Session, state: str) -> None:
                try:
                    if started is not None and not native.in_transaction:
                        self._emit("commit", state, (perf_counter() - started) * 1000)
                except Exception:
                    pass

            def committed(value: Session) -> None:
                end(value, "finished")

            def rolled_back(value: Session) -> None:
                end(value, "error")

            event.listen(session, "before_commit", begin)
            event.listen(session, "after_commit", committed)
            event.listen(session, "after_rollback", rolled_back)
        except Exception:
            pass

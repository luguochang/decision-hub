"""Safe, lossy local channel observations. Never work/commit/permission proof."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from queue import Empty, Full, Queue
from threading import Lock, Thread
from typing import Any
from uuid import uuid4

ENV_PATH = "DECISION_HUB_REALTIME_DIAGNOSTICS_PATH"
MAX_LINE_BYTES = 4096
MAX_FILE_BYTES = 16 * 1024 * 1024
QUEUE_CAPACITY = 256
_CHANNELS = frozenset(
    {
        "sources",
        "discovery_recovery",
        "windows",
        "runs",
        "outcomes",
        "analytical_observations",
        "analytical_assessments",
        "effect_tracking_requests",
        "calibrations",
        "notifications",
        "risk_acquisition",
        "risk_monitor",
        "action_monitor",
        "action_quotes",
        "action_execution",
        "risk_research_admission",
        "risk_shadow",
        "market_memory",
        "market_enhancements",
    }
)
_SQL_WORDS = frozenset(
    {
        "SELECT",
        "INSERT",
        "UPDATE",
        "DELETE",
        "BEGIN",
        "COMMIT",
        "ROLLBACK",
        "SAVEPOINT",
        "RELEASE",
        "PRAGMA",
        "WITH",
        "CREATE",
        "ALTER",
        "DROP",
    }
)
_SAFE_CODES = frozenset(
    {
        "research_policy_trajectory_audit_limit",
        "research_policy_trajectory_bytes_exceeded",
        "research_policy_snapshot_changed",
        "research_policy_checker_current_implementation_unavailable",
    }
)


def _json(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode("utf-8")


def _name(value: str) -> str | None:
    return value if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]{0,127}", value) else None


def _error_fields(error: BaseException, root: Path) -> dict[str, Any]:
    original = getattr(error, "orig", None)
    if not isinstance(original, BaseException):
        original = error
    statement = getattr(error, "statement", None)
    word = re.match(r"\s*([A-Za-z]+)", statement) if isinstance(statement, str) else None
    keyword = word[1].upper() if word is not None else None
    sqlite_code = (
        getattr(original, "sqlite_errorcode", None) if isinstance(original, sqlite3.Error) else None
    )
    sqlite_name = (
        getattr(original, "sqlite_errorname", None) if isinstance(original, sqlite3.Error) else None
    )
    frames = []
    cursor = error.__traceback__
    while cursor is not None:
        code = cursor.tb_frame.f_code
        try:
            filename = Path(code.co_filename).relative_to(root).as_posix()
            if ".." in Path(filename).parts or not re.fullmatch(r"[A-Za-z0-9_./-]+", filename):
                filename = None
        except ValueError:
            filename = None
        frames.append({"file": filename, "function": _name(code.co_name), "line": cursor.tb_lineno})
        cursor = cursor.tb_next
    # Never format an outer statement exception with SQL/parameters. Missing
    # underlying message stays unknown; other messages are digested, not saved.
    message = None if original is error and isinstance(statement, str) else str(original)
    return {
        "error_type": _name(type(error).__name__),
        "error_module": _name(type(error).__module__),
        "orig_type": _name(type(original).__name__),
        "orig_module": _name(type(original).__module__),
        "sqlite_errorcode": sqlite_code if type(sqlite_code) is int else None,
        "sqlite_errorname": sqlite_name
        if isinstance(sqlite_name, str) and re.fullmatch(r"SQLITE_[A-Z0-9_]+", sqlite_name)
        else None,
        "statement_first_keyword": keyword if keyword in _SQL_WORDS else None,
        "error_code": message if message in _SAFE_CODES else None,
        "message_sha256": hashlib.sha256(
            message.encode("utf-8", errors="surrogatepass")
        ).hexdigest()
        if message is not None
        else None,
        "traceback": frames,
        "traceback_complete": True,
        "traceback_dropped": 0,
        "traceback_sha256": hashlib.sha256(_json(frames)).hexdigest(),
    }


class _FileSink:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.queue: Queue[bytes] = Queue(QUEUE_CAPACITY)
        self.lock = Lock()
        self.dropped = 0
        self.failed = False
        Thread(target=self._write, daemon=True, name="realtime-diagnostic-jsonl").start()

    def __call__(self, record: dict[str, Any]) -> bool:
        try:
            with self.lock:
                record = {**record, "diagnostic_gap_events": self.dropped}
                while len(_json(record)) > MAX_LINE_BYTES and record.get("traceback"):
                    record["traceback"] = record["traceback"][1:]
                    record["traceback_complete"] = False
                    record["traceback_dropped"] += 1
                raw = _json(record)
                if self.failed or len(raw) > MAX_LINE_BYTES:
                    self.dropped += 1
                    return False
                try:
                    self.queue.put_nowait(raw)
                except Full:
                    self.dropped += 1
                    return False
            return True  # queued only, never a durable-write acknowledgement
        except BaseException:
            with self.lock:
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
                    size = os.fstat(descriptor).st_size
                    if size + len(raw) > MAX_FILE_BYTES - MAX_LINE_BYTES:
                        gap = _json(
                            {
                                "schema_version": "realtime-channel-diagnostics.v1",
                                "state": "diagnostic_gap",
                                "reason": "file_budget_exhausted",
                                "observed_at": datetime.now(UTC).isoformat(),
                                "process_id": os.getpid(),
                            }
                        )
                        if size + len(gap) <= MAX_FILE_BYTES:
                            os.write(descriptor, gap)
                        self.failed = True
                    elif os.write(descriptor, raw) != len(raw):
                        self.failed = True
                finally:
                    os.close(descriptor)
            except BaseException:
                self.failed = True
            finally:
                self.queue.task_done()


class RealtimeChannelDiagnostics:
    def __init__(self, *, root: Path, sink: Any = None) -> None:
        self.root, self.sink = root, sink
        self.episodes: dict[str, dict[str, Any]] = {}

    @classmethod
    def from_environment(cls) -> RealtimeChannelDiagnostics | None:
        try:
            value = os.environ.get(ENV_PATH)
            if not value:
                return None
            path = Path(value)
            if not path.is_absolute() or path.suffix != ".jsonl" or not path.parent.is_dir():
                return None
            return cls(root=Path(__file__).resolve().parents[4], sink=_FileSink(path.resolve()))
        except BaseException:
            return None

    def __call__(
        self,
        channel: str,
        error: BaseException | None,
        elapsed_ms: float,
        timeout_seconds: float | None,
        timeout_expired: bool,
    ) -> None:
        try:
            if self.sink is None or channel not in _CHANNELS:
                return
            now = datetime.now(UTC).isoformat()
            common = {
                "schema_version": "realtime-channel-diagnostics.v1",
                "process_id": os.getpid(),
                "channel": channel,
                "observed_at": now,
                "elapsed_ms": max(0, round(elapsed_ms, 3)) if math.isfinite(elapsed_ms) else None,
                "channel_timeout_seconds": timeout_seconds,
                "channel_timeout_expired": timeout_expired,
                "proof_scope": "local_channel_action_observation",
                "commit_verified": False,
            }
            prior = self.episodes.get(channel)
            if error is None:
                if prior is not None:
                    self.sink({**common, "state": "recovered", **prior})
                    self.episodes.pop(channel, None)
                return
            if prior is not None:
                prior["failure_count"] += 1
                prior["last_failure_at"] = now
                return
            episode = {
                "episode_id": uuid4().hex,
                "first_failure_at": now,
                "last_failure_at": now,
                "failure_count": 1,
            }
            try:
                fields = _error_fields(error, self.root)
            except BaseException:
                self.sink(
                    {**common, "state": "diagnostic_gap", "reason": "error_projection_unavailable"}
                )
                return
            episode["first_message_sha256"] = fields["message_sha256"]
            self.episodes[channel] = episode
            self.sink({**common, **episode, "state": "first_failure", **fields})
        except BaseException:
            # Diagnostic failures cannot turn a success into a failure or
            # intercept action cancellation. No raw logging fallback.
            return

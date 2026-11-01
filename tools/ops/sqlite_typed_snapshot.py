"""Stream complete physical SQLite cells and retain full binary online backups."""
from __future__ import annotations

import base64
import hashlib
import json
import math
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

type SqlCell = int | float | bytes | None


def canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("ascii")


def quoted(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def read(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=15)
    connection.text_factory = bytes
    connection.execute("PRAGMA query_only=ON")
    return connection


def scalar(value: SqlCell) -> dict[str, str]:
    if value is None:
        return {"type": "null"}
    if type(value) is int:
        return {"type": "integer", "value": str(value)}
    if type(value) is float:
        return {"type": "real", "value": value.hex()}
    if not isinstance(value, bytes):
        raise TypeError("unexpected_sqlite_scalar")
    return {"type": "bytes", "base64": base64.b64encode(value).decode("ascii")}


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def snapshot(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {
        "encoding": "sqlite-physical-typed-cells.v1", "tables": {}, "schema_objects": []}
    connection = read(path)
    try:
        connection.execute("BEGIN")
        objects = connection.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name").fetchall()
        result["schema_objects"] = [[scalar(value) for value in row] for row in objects]
        for kind, name_raw, _, ddl in objects:
            if kind != b"table":
                continue
            name = name_raw.decode("utf-8")
            metadata = connection.execute(f"PRAGMA table_xinfo({quoted(name)})").fetchall()
            columns = [row[1].decode("utf-8") for row in metadata]
            if not columns:
                raise ValueError("empty_physical_table_metadata")
            selectors = ",".join(
                f"typeof({quoted(column)}),{quoted(column)}" for column in columns)
            primary = sorted((row[5], row[1].decode("utf-8")) for row in metadata if row[5])
            without_rowid = ddl is not None and b"WITHOUT ROWID" in ddl.upper()
            order = ",".join(quoted(column) for _, column in primary) if without_rowid else "rowid"
            if not order:
                raise ValueError("without_rowid_primary_key_missing")
            cursor = connection.execute(
                f"SELECT {selectors} FROM {quoted(name)} ORDER BY {order}")
            digest = hashlib.sha256(b"sqlite-physical-typed-cells.v1\n")
            count = 0
            for row in cursor:
                cells = [{"sqlite_type": row[index].decode("ascii"),
                          "cell": scalar(row[index + 1])} for index in range(0, len(row), 2)]
                digest.update(canonical(cells) + b"\n")
                count += 1
            result["tables"][name] = {
                "columns": columns,
                "metadata": [[scalar(value) for value in row] for row in metadata],
                "row_count": count, "rows_sha256": digest.hexdigest(),
            }
        result["integrity"] = [row[0].decode("utf-8") for row in
                               connection.execute("PRAGMA integrity_check")]
        connection.rollback()
    finally:
        connection.close()
    return result


def backup(
    source_path: Path, target_path: Path, *, timeout_seconds: float = 60,
    pages: int = 1024, progress: Callable[[int, int, int], None] | None = None,
) -> dict[str, str | int]:
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0 or pages <= 0:
        raise ValueError("finite_backup_budget_and_positive_pages_required")
    if not source_path.is_file() or target_path.exists():
        raise ValueError("existing_source_and_fresh_backup_required")
    target_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    deadline = started + timeout_seconds
    source: sqlite3.Connection | None = None
    destination: sqlite3.Connection | None = None
    status: dict[str, str | int] = {
        "phase": "source_snapshot_acquire", "progress_calls": 0,
        "last_status": -1, "remaining_pages": -1, "total_pages": -1,
        "source_snapshot_fixed": False, "source_closed": False, "destination_closed": False,
    }
    failure: BaseException | None = None

    def remaining_wait() -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("sqlite_backup_deadline_elapsed")
        return min(5.0, remaining)

    def on_progress(code: int, remaining: int, total: int) -> None:
        status.update(progress_calls=int(status["progress_calls"]) + 1, last_status=code,
                      remaining_pages=remaining, total_pages=total)
        remaining_wait()
        if progress is not None:
            progress(code, remaining, total)
        remaining_wait()

    try:
        source = sqlite3.connect(source_path.resolve().as_uri() + "?mode=ro", uri=True,
                                 timeout=remaining_wait())
        source.text_factory = bytes
        source.execute("PRAGMA query_only=ON")
        source.execute("BEGIN")
        # BEGIN alone is deferred. This actual read fixes the WAL end mark;
        # later commits cannot restart backup against a newer source image.
        source.execute("SELECT rootpage FROM sqlite_master ORDER BY rowid LIMIT 1").fetchall()
        status["source_snapshot_fixed"] = source.in_transaction
        remaining_wait()
        status["phase"] = "destination_acquire"
        destination = sqlite3.connect(target_path, timeout=remaining_wait())
        status["phase"] = "copying"
        source.backup(destination, pages=pages, progress=on_progress,
                      sleep=min(0.05, timeout_seconds))
        remaining_wait()
        status["phase"] = "copy_complete"
    except BaseException as error:
        failure = error
        status["error_type"] = type(error).__name__
        if isinstance(error, sqlite3.Error):
            status["sqlite_errorcode"] = getattr(error, "sqlite_errorcode", -1)
        raise
    finally:
        try:
            if destination is not None:
                destination.close()
                status["destination_closed"] = True
        finally:
            if source is not None:
                source.close()
                status["source_closed"] = True
        status["elapsed_ms"] = int((time.monotonic() - started) * 1000)
        if failure is not None:
            status["partial_retained"] = target_path.exists()
            saved_status = target_path.with_name(target_path.name + ".backup-status.json")
            try:
                with saved_status.open("x", encoding="utf-8") as output:
                    json.dump(status, output, sort_keys=True, indent=2)
                    output.write("\n")
            except OSError as status_error:
                failure.add_note(f"backup_status_not_saved:{type(status_error).__name__}")
    return {"path": str(target_path), "bytes": target_path.stat().st_size,
            "sha256": hash_file(target_path), **status}

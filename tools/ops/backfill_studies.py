"""Offline additive migration and identity projection. Requires a new verified backup."""

from __future__ import annotations

import argparse
import hashlib
import json
import socket
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from packages.kernel.decision_hub_kernel.application.study import backfill_studies
from packages.kernel.decision_hub_kernel.persistence.db import Database
from tools.ops.database import backup, integrity


def immutable_digests(path: Path) -> dict:
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        queries = {
            "artifacts": "SELECT * FROM artifacts ORDER BY artifact_id",
            "observations": "SELECT * FROM observations ORDER BY observation_id",
            "macro_event_vintages": "SELECT * FROM macro_event_vintages ORDER BY vintage_id",
            "forecasts": "SELECT * FROM forecasts ORDER BY forecast_id",
            "research_evidence": "SELECT * FROM research_evidence ORDER BY evidence_id",
            "research_facts": "SELECT * FROM research_facts ORDER BY fact_id",
            "research_results": "SELECT * FROM research_results ORDER BY run_id",
            "event_watches": "SELECT * FROM event_watches ORDER BY watch_id",
            "event_window_samples": "SELECT * FROM event_window_samples ORDER BY sample_id",
        }
        result = {}
        for name, query in queries.items():
            rows = db.execute(query).fetchall()
            result[name] = {
                "count": len(rows),
                "sha256": hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode()).hexdigest(),
            }
        return result


def apply(database: Path, backup_path: Path, *, rehearsal: bool) -> dict:
    if (
        not database.is_file()
        or backup_path.exists()
        or database.resolve() == backup_path.resolve()
    ):
        raise ValueError("database_or_new_backup_path_invalid")
    if not rehearsal:
        for port in (8047, 3097, 8037, 8038):
            with socket.socket() as sock:
                sock.settimeout(0.3)
                if sock.connect_ex(("127.0.0.1", port)) == 0:
                    raise ValueError(f"stop_managed_product_before_migration:{port}")
    backup(database, backup_path)
    if not integrity(backup_path):
        raise ValueError("backup_integrity_failed")
    before = immutable_digests(database)
    db = Database(f"sqlite+pysqlite:///{database.resolve().as_posix()}")
    db.initialize()
    first = backfill_studies(db)
    second = backfill_studies(db)
    db.engine.dispose()
    after = immutable_digests(database)
    if before != after or first != second or not integrity(database):
        raise ValueError("study_projection_verification_failed")
    return {
        "status": "passed",
        "scope": "rehearsal" if rehearsal else "formal_offline_migration",
        "at": datetime.now(UTC).isoformat(),
        "database": str(database.resolve()),
        "backup": str(backup_path.resolve()),
        "old_records": before,
        "projection_counts": first,
        "repeated_projection_unchanged": True,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rehearsal", action="store_true")
    args = parser.parse_args()
    # A rehearsal must use an explicitly separate database, never the formal file.
    if (
        args.rehearsal
        and args.database.resolve()
        == Path("data/core-live-20260913/db/decision_hub.sqlite3").resolve()
    ):
        raise ValueError("rehearsal_must_not_use_formal_database")
    result = apply(args.database, args.backup, rehearsal=args.rehearsal)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps({"status": result["status"], "projection_counts": result["projection_counts"]})
    )


if __name__ == "__main__":
    main()

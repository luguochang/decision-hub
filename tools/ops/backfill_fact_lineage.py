"""Offline additive migration and fact lineage projection. Requires a new verified backup."""

from __future__ import annotations

import argparse
import json
import socket
from datetime import UTC, datetime
from pathlib import Path

from packages.kernel.decision_hub_kernel.application.fact_lineage import FactLineageStore
from packages.kernel.decision_hub_kernel.persistence.db import Database
from tools.ops.backfill_studies import immutable_digests
from tools.ops.database import backup, integrity


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
    first = FactLineageStore(db).backfill()
    second = FactLineageStore(db).backfill()
    db.engine.dispose()
    after = immutable_digests(database)
    if before != after or first != second or not integrity(database):
        raise ValueError("fact_projection_verification_failed")
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

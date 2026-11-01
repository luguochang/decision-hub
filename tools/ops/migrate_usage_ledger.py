"""Back up and rehearse the additive usage-ledger migration before applying it."""

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

from alembic import command

from packages.kernel.decision_hub_kernel.persistence.db import Database
from tools.ops.database import backup, integrity

HEAD = "0055_usage_collection"


def frozen_reports(path):
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        return {
            row[0]: [row[1], hashlib.sha256(row[2].encode()).hexdigest()]
            for row in db.execute("SELECT run_id,payload_hash,payload_json FROM research_results")
        }


def frozen_receipts(path):
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        exists = db.execute(
            "SELECT name FROM sqlite_master WHERE name='research_usage_snapshots'"
        ).fetchone()
        if not exists:
            return {}
        return {
            row[0]: [row[1], hashlib.sha256(row[2].encode()).hexdigest()]
            for row in db.execute(
                "SELECT snapshot_id,payload_sha256,payload_json FROM research_usage_snapshots"
            )
        }


def upgrade(path):
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        head = db.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    if head not in {"0053_library_context_consent", "0054_research_usage_snapshots", HEAD}:
        raise ValueError(f"unexpected_head:{head}")
    previous = frozen_reports(path)
    prior_receipts = frozen_receipts(path)
    database = Database(f"sqlite+pysqlite:///{path.resolve().as_posix()}")
    try:
        command.upgrade(database._alembic_config(), HEAD)
        current = frozen_reports(path)
        if not all(current.get(key) == value for key, value in previous.items()):
            raise ValueError("frozen_report_changed")
        current_receipts = frozen_receipts(path)
        if not all(current_receipts.get(key) == value for key, value in prior_receipts.items()):
            raise ValueError("frozen_usage_receipt_changed")
        if not integrity(path):
            raise ValueError("sqlite_integrity_failed")
        with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
            receipts = db.execute("SELECT COUNT(*) FROM research_usage_snapshots").fetchone()[0]
        return {
            "head": HEAD, "preserved_reports": len(previous), "receipts": receipts,
            "preserved_receipts": len(prior_receipts),
        }
    finally:
        database.engine.dispose()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not args.database.is_file() or args.output_dir.exists():
        raise ValueError("existing_database_and_new_output_directory_required")
    args.output_dir.mkdir(parents=True)
    saved, rehearsal = args.output_dir / "before.sqlite3", args.output_dir / "rehearsal.sqlite3"
    backup(args.database, saved)
    backup(saved, rehearsal)
    result = {"backup": str(saved.resolve()), "rehearsal": upgrade(rehearsal)}
    result["idempotent_rehearsal"] = upgrade(rehearsal)
    if args.apply:
        result["formal"] = upgrade(args.database)
    (args.output_dir / "receipt.json").write_bytes(json.dumps(result, indent=2).encode())
    print(json.dumps(result))


if __name__ == "__main__":
    main()

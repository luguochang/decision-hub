"""0052 guarded offline upgrade: preserve every existing table and infer no historical reads."""

import argparse
import hashlib
import json
import socket
import sqlite3
from pathlib import Path

from alembic import command

from packages.kernel.decision_hub_kernel.persistence.db import Database
from tools.ops.database import backup, integrity

NEW_TABLES = {"product_commands", "product_read_receipts"}


def digest(path):
    result = {}
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        names = [r[0] for r in db.execute("select name from sqlite_master where type='table'")]
        for name in names:
            if name.startswith("sqlite_") or name in NEW_TABLES | {"alembic_version"}:
                continue
            quoted = '"' + name.replace('"', '""') + '"'
            rows = db.execute(f"SELECT * FROM {quoted} ORDER BY rowid").fetchall()
            result[name] = {
                "count": len(rows),
                "sha256": hashlib.sha256(json.dumps(rows, default=str).encode()).hexdigest(),
            }
    return result


def apply(path, backup_path, rehearsal=False):
    formal = Path("data/core-live-20260913/db/decision_hub.sqlite3").resolve()
    if not path.is_file() or backup_path.exists() or path.resolve() == backup_path.resolve():
        raise ValueError("database_or_new_backup_path_invalid")
    if rehearsal and path.resolve() == formal:
        raise ValueError("rehearsal_must_use_separate_copy")
    if not rehearsal:
        for port in (8047, 3097, 8037, 8038):
            with socket.socket() as sock:
                sock.settimeout(0.3)
                if sock.connect_ex(("127.0.0.1", port)) == 0:
                    raise ValueError(f"stop_managed_product_before_migration:{port}")
    with sqlite3.connect(path) as db:
        head = db.execute("select version_num from alembic_version").fetchone()[0]
        if head not in {"0051_analytical_outlooks", "0052_product_receipts"}:
            raise ValueError(f"unexpected_migration_head:{head}")
    backup(path, backup_path)
    assert integrity(backup_path)
    before = digest(path)
    database = Database(f"sqlite+pysqlite:///{path.resolve().as_posix()}")
    command.upgrade(database._alembic_config(), "0052_product_receipts")
    assert before == digest(path), "existing_table_changed"
    command.upgrade(database._alembic_config(), "0052_product_receipts")
    assert before == digest(path), "second_upgrade_changed_existing_table"
    with sqlite3.connect(path) as db:
        counts = {
            table: db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in NEW_TABLES
        }
        if head == "0051_analytical_outlooks":
            assert counts == dict.fromkeys(NEW_TABLES, 0), "historical_read_was_inferred"
    assert integrity(path)
    return {
        "head": "0052_product_receipts",
        "existing_tables_unchanged": before,
        "new_table_counts": counts,
        "idempotent": True,
        "integrity": True,
        "backup": str(backup_path),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--backup", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--rehearsal", action="store_true")
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("output_must_be_new")
    result = apply(args.database, args.backup, args.rehearsal)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "head": result["head"],
                "unchanged_tables": len(result["existing_tables_unchanged"]),
                "idempotent": True,
                "integrity": True,
                "output": str(args.output),
            }
        )
    )


if __name__ == "__main__":
    main()

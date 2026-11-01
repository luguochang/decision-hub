"""Preview additive planning-decision migration; apply only after managed services stop."""

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

from packages.kernel.decision_hub_kernel.persistence.db import Database


def read(path):
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)


def rows(path):
    with read(path) as db:
        tables = [row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ) if row[0] != "alembic_version"]
        return {table: hashlib.sha256(json.dumps(sorted(
            [json.dumps(row, default=str) for row in db.execute(
                'SELECT * FROM "' + table.replace('"', '""') + '"')]
        )).encode()).hexdigest() for table in tables}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not args.database.is_file():
        raise ValueError("existing_database_required")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    mode = "live" if args.apply else "preview"
    backup = args.output_dir / f"pre-0064-{mode}.sqlite3"
    if backup.exists():
        raise ValueError("backup_already_exists")
    with read(args.database) as source, sqlite3.connect(backup) as destination:
        if source.execute("SELECT version_num FROM alembic_version").fetchone()[0] != (
            "0063_evolution_context_snapshots"
        ):
            raise ValueError("expected_schema_0063")
        if args.apply:
            for table in ("runs", "evolution_jobs"):
                if source.execute(
                    f"SELECT count(*) FROM {table} WHERE status='running'"
                ).fetchone()[0]:
                    raise ValueError("inflight_work_requires_wait")
        source.backup(destination)
    before = rows(backup)
    target = args.database if args.apply else args.output_dir / "migration-preview.sqlite3"
    if not args.apply:
        with read(backup) as source, sqlite3.connect(target) as destination:
            source.backup(destination)
    Database(f"sqlite+pysqlite:///{target.resolve().as_posix()}").initialize()
    after = rows(target)
    preserved = all(after.get(table) == digest for table, digest in before.items())
    with read(target) as db:
        integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
        schema = db.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        count = db.execute("SELECT count(*) FROM evolution_planning_decisions").fetchone()[0]
    proof = {"mode": mode, "original_tables": len(before), "preserved": preserved,
             "integrity": integrity, "schema": schema, "new_decisions": count,
             "before": before, "after": after}
    (args.output_dir / f"migration-{mode}.json").write_text(
        json.dumps(proof, indent=2), encoding="utf-8")
    assert preserved and integrity == "ok" and count == 0
    summary = {key: value for key, value in proof.items() if key not in ("before", "after")}
    print(json.dumps(summary))


if __name__ == "__main__":
    main()

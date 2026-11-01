"""Rehearse the additive 0053 upgrade, preserve legacy scopes, then optionally apply."""

import argparse
import json
import sqlite3
from pathlib import Path

from alembic import command

from packages.kernel.decision_hub_kernel.persistence.db import Database
from tools.ops.database import backup, integrity

HEAD = "0053_library_context_consent"
OLD_COLUMNS = (
    "run_id,research_family,domain_pack_ref,instrument_scope_json,"
    "library_context_mode,selected_version_ids_json,created_at"
)


def scopes(path):
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        return dict(
            (row[0], list(row[1:]))
            for row in db.execute(f"SELECT {OLD_COLUMNS} FROM research_run_scopes")
        )


def upgrade(path):
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        head = db.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    if head not in {"0052_product_receipts", HEAD}:
        raise ValueError(f"unexpected_head:{head}")
    previous = scopes(path)
    database = Database(f"sqlite+pysqlite:///{path.resolve().as_posix()}")
    command.upgrade(database._alembic_config(), HEAD)
    current = scopes(path)
    assert all(current.get(key) == value for key, value in previous.items())
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        if head != HEAD:
            for run_id in previous:
                consent, request_hash, inputs = db.execute(
                    "SELECT allow_remote_library_content,request_hash,input_receipts_json "
                    "FROM research_run_scopes WHERE run_id=?",
                    (run_id,),
                ).fetchone()
                assert not consent and request_hash is None and inputs == "[]"
    assert integrity(path)
    database.engine.dispose()
    return {"head": HEAD, "preserved_legacy_scopes": len(previous), "integrity": True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not args.database.is_file() or args.output_dir.exists():
        raise ValueError("existing_database_and_new_output_directory_required")
    args.output_dir.mkdir(parents=True)
    saved = args.output_dir / "before.sqlite3"
    rehearsal = args.output_dir / "rehearsal.sqlite3"
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

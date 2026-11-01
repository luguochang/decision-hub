"""Back up and rehearse the additive Host watchdog migration before optional apply."""

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

from alembic import command

from packages.kernel.decision_hub_kernel.persistence.db import Database
from tools.ops.database import backup, integrity

HEAD = "0059_dsh_model_step_budget"


def upgrade(path):
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as conn:
        head = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        if head not in {"0058_prospective_labels", HEAD}:
            raise ValueError(f"unexpected_head:{head}")
        columns = [row[1] for row in conn.execute("PRAGMA table_info(dsh_session_links)")]
        old_columns = [name for name in columns if name != "model_step_timeout_ms"]
        projection = ",".join(old_columns)
        before = list(conn.execute(f"SELECT {projection} FROM dsh_session_links ORDER BY run_id"))
    db = Database(f"sqlite+pysqlite:///{path.resolve().as_posix()}")
    try:
        command.upgrade(db._alembic_config(), HEAD)
    finally:
        db.engine.dispose()
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as conn:
        after = list(conn.execute(f"SELECT {projection} FROM dsh_session_links ORDER BY run_id"))
        assert before == after, "historical_session_data_changed"
        if head != HEAD:
            assert conn.execute(
                "SELECT COUNT(*) FROM dsh_session_links WHERE model_step_timeout_ms IS NOT NULL"
            ).fetchone()[0] == 0, "historical_budget_invented"
    assert integrity(path)
    return {"head": HEAD, "preserved_sessions": len(before), "historical_budgets_backfilled": False,
            "old_columns_sha256": hashlib.sha256(json.dumps(before).encode()).hexdigest(),
            "integrity": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not args.database.is_file() or args.output_dir.exists():
        raise ValueError("existing_database_and_new_output_directory_required")
    if args.apply:
        with sqlite3.connect(args.database.resolve().as_uri() + "?mode=ro", uri=True) as conn:
            if conn.execute(
                "SELECT COUNT(*) FROM runs WHERE strategy_version='research.v1' "
                "AND status IN ('admitted','running')"
            ).fetchone()[0]:
                raise ValueError("active_research_must_finish_before_migration")
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

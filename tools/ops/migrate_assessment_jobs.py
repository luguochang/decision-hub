"""Preview 0066 on a backup; apply under one physical writer without old-row writes."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, create_engine

from tools.ops.migrate_revision_feedback import (
    ROOT,
    _retained_terminal_history,
    _terminal_history_guard,
    idle,
)
from tools.ops.sqlite_typed_snapshot import backup, canonical, quoted, scalar, snapshot

BEFORE = "0065_revision_feedback_evaluations"
AFTER = "0066_analytical_assessment_jobs"
TABLE = "analytical_assessment_jobs"
INDEXES = {"ix_assessment_jobs_status_due", "ix_assessment_jobs_lease_expires_at"}


def new_index(name: str | None) -> bool:
    return name is not None and (
        name in INDEXES or name.startswith(f"sqlite_autoindex_{TABLE}_")
    )


def _cell(value: Any) -> dict[str, str]:
    return scalar(value.encode("utf-8") if isinstance(value, str) else value)


def _writer_snapshot(connection: Connection) -> dict[str, Any]:
    """Read uncommitted schema and all physical cells on the actual writer connection."""
    objects = connection.exec_driver_sql(
        "SELECT type,name,tbl_name,CAST(sql AS BLOB) FROM sqlite_master ORDER BY type,name"
    ).all()
    tables: dict[str, Any] = {}
    for kind, name, _table, ddl in objects:
        if kind != "table":
            continue
        metadata = connection.exec_driver_sql(f"PRAGMA table_xinfo({quoted(name)})").all()
        columns = [row[1] for row in metadata]
        if not columns:
            raise ValueError("empty_physical_table_metadata")
        selectors = ",".join(
            f"typeof({quoted(column)}),CASE WHEN typeof({quoted(column)}) IN ('text','blob') "
            f"THEN CAST({quoted(column)} AS BLOB) ELSE {quoted(column)} END"
            for column in columns
        )
        primary = sorted((row[5], row[1]) for row in metadata if row[5])
        without_rowid = ddl is not None and b"WITHOUT ROWID" in ddl.upper()
        order = ",".join(quoted(column) for _, column in primary) if without_rowid else "rowid"
        if not order:
            raise ValueError("without_rowid_primary_key_missing")
        digest = hashlib.sha256(b"sqlite-physical-typed-cells.v1\n")
        count = 0
        for row in connection.exec_driver_sql(
            f"SELECT {selectors} FROM {quoted(name)} ORDER BY {order}"
        ):
            cells = [{"sqlite_type": row[index], "cell": _cell(row[index + 1])}
                     for index in range(0, len(row), 2)]
            digest.update(canonical(cells) + b"\n")
            count += 1
        tables[name] = {
            "columns": columns, "metadata": [[_cell(value) for value in row] for row in metadata],
            "row_count": count, "rows_sha256": digest.hexdigest(),
        }
    return {
        "encoding": "sqlite-physical-typed-cells.v1", "tables": tables,
        "schema_objects": [[_cell(value) for value in row] for row in objects],
        "integrity": [row[0] for row in connection.exec_driver_sql("PRAGMA integrity_check")],
    }


def _verify_original(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    preserved = {name: value == after["tables"].get(name)
                 for name, value in before["tables"].items() if name != "alembic_version"}
    old_schema = all(item in after["schema_objects"] for item in before["schema_objects"])
    allowed_objects = {TABLE, *INDEXES}
    added_objects = [
        item for item in after["schema_objects"] if item not in before["schema_objects"]
    ]
    allowed_schema = all(
        item[0] in (_cell("table"), _cell("index")) and item[2] == _cell(TABLE)
        and (item[1] in [_cell(name) for name in allowed_objects]
             or (item[0] == _cell("index") and item[1] == _cell(f"sqlite_autoindex_{TABLE}_1")))
        for item in added_objects
    )
    result = {"original_tables_unchanged": preserved,
              "original_schema_objects_preserved": old_schema,
              "only_assessment_schema_added": allowed_schema}
    if (not all(preserved.values()) or not old_schema or not allowed_schema
            or after["integrity"] != ["ok"]
            or after["tables"].get(TABLE, {}).get("row_count") != 0):
        raise RuntimeError("assessment_migration_original_snapshot_changed")
    return result


def upgrade_in_writer(
    path: Path, *, receipt: dict[str, Any] | None = None,
    before_upgrade: Callable[[Connection], None] | None = None,
    require_idle: bool = True, allow_terminal_history: bool = False,
) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError("existing_database_required")
    report = receipt if receipt is not None else {}
    report.update(started_at=datetime.now(UTC).isoformat(), committed=False, rolled_back=False,
                  sql=[], denied_mutations=[], phase="connecting")
    engine = create_engine(f"sqlite+pysqlite:///{path.resolve().as_posix()}",
                           connect_args={"timeout": 5})
    try:
        with engine.connect() as connection:
            native: sqlite3.Connection | None = None
            allow_end = False
            denied: list[dict[str, Any]] = []

            def authorize(action: int, first: str | None, second: str | None,
                          database: str | None, source: str | None) -> int:
                allowed = True
                if action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE):
                    allowed = first in {"alembic_version", "sqlite_master"}
                elif action == sqlite3.SQLITE_DELETE:
                    allowed = False
                elif action == sqlite3.SQLITE_CREATE_TABLE:
                    allowed = first == TABLE and database == "main"
                elif action == sqlite3.SQLITE_CREATE_INDEX:
                    allowed = second == TABLE and new_index(first) and database == "main"
                elif action == sqlite3.SQLITE_REINDEX:
                    allowed = new_index(first)
                elif action in (
                    sqlite3.SQLITE_DROP_TABLE, sqlite3.SQLITE_DROP_INDEX,
                    sqlite3.SQLITE_ALTER_TABLE,
                    sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH, sqlite3.SQLITE_CREATE_VIEW,
                    sqlite3.SQLITE_DROP_VIEW, sqlite3.SQLITE_CREATE_TRIGGER,
                    sqlite3.SQLITE_DROP_TRIGGER,
                    sqlite3.SQLITE_CREATE_VTABLE, sqlite3.SQLITE_DROP_VTABLE,
                    sqlite3.SQLITE_CREATE_TEMP_TABLE, sqlite3.SQLITE_DROP_TEMP_TABLE,
                    sqlite3.SQLITE_CREATE_TEMP_INDEX, sqlite3.SQLITE_DROP_TEMP_INDEX,
                    sqlite3.SQLITE_CREATE_TEMP_VIEW, sqlite3.SQLITE_DROP_TEMP_VIEW,
                    sqlite3.SQLITE_CREATE_TEMP_TRIGGER, sqlite3.SQLITE_DROP_TEMP_TRIGGER,
                    sqlite3.SQLITE_SAVEPOINT, sqlite3.SQLITE_ANALYZE,
                ):
                    allowed = False
                elif action == sqlite3.SQLITE_TRANSACTION:
                    allowed = allow_end and first in {"COMMIT", "ROLLBACK"}
                elif action == sqlite3.SQLITE_PRAGMA:
                    allowed = second is None or first in {
                        "table_info", "table_xinfo", "foreign_key_list", "index_list", "index_info",
                        "index_xinfo", "integrity_check", "quick_check",
                    }
                if not allowed:
                    denied.append({"action": action, "first": first, "second": second,
                                   "database": database, "source": source})
                return sqlite3.SQLITE_OK if allowed else sqlite3.SQLITE_DENY

            try:
                report["phase"] = "acquiring_writer"
                connection.exec_driver_sql("BEGIN IMMEDIATE")
                raw = connection.connection.driver_connection
                if not isinstance(raw, sqlite3.Connection) or not raw.in_transaction:
                    raise RuntimeError("physical_sqlite_writer_required")
                native = raw
                report.update(physical_writer_confirmed=True, phase="checking_schema_and_idle")
                if connection.exec_driver_sql("SELECT version_num FROM alembic_version").all() != [
                    (BEFORE,),
                ]:
                    raise ValueError("expected_schema_0065")
                if connection.exec_driver_sql(
                    "SELECT count(*) FROM sqlite_master WHERE name=?", (TABLE,),
                ).scalar_one() != 0:
                    raise ValueError("assessment_job_table_already_exists")
                report.update(idle=idle(connection), idle_required=require_idle,
                              allow_terminal_history=allow_terminal_history)
                if allow_terminal_history:
                    report["terminal_history_guard"] = guard = _terminal_history_guard(connection)
                    if any(guard["blockers"].values()):
                        raise ValueError("migration_inflight_work")
                    report["retained_terminal_history"] = _retained_terminal_history(connection)
                elif require_idle and any(report["idle"].values()):
                    raise ValueError("migration_inflight_work")
                report["before"] = before = _writer_snapshot(connection)
                native.set_authorizer(authorize)
                native.set_trace_callback(report["sql"].append)
                report["phase"] = "migrating"
                if before_upgrade is not None:
                    before_upgrade(connection)
                config = Config(str(ROOT / "alembic.ini"))
                config.set_main_option("sqlalchemy.url", str(engine.url))
                config.attributes.update(connection=connection, configure_logger=False)
                command.upgrade(config, AFTER)
                if not native.in_transaction:
                    raise RuntimeError("migration_lost_original_writer")
                if connection.exec_driver_sql("SELECT version_num FROM alembic_version").all() != [
                    (AFTER,),
                ] or denied:
                    raise RuntimeError("assessment_migration_postcondition_failed")
                report["phase"] = "verifying_original_cells_under_writer"
                report["after"] = after = _writer_snapshot(connection)
                report.update(_verify_original(before, after))
                report["phase"] = "committing"
                allow_end = True
                connection.commit()
                report.update(committed=True, head=AFTER, new_job_rows=0,
                              same_physical_transaction=True, phase="complete")
            except BaseException as error:
                report.update(error_type=type(error).__name__, failure_phase=report["phase"])
                held = native is not None and native.in_transaction
                allow_end = True
                if connection.in_transaction():
                    connection.rollback()
                report["rolled_back"] = native is not None and held and not native.in_transaction
                raise
            finally:
                report["denied_mutations"] = denied
                if native is not None:
                    native.set_authorizer(None)
                    native.set_trace_callback(None)
    finally:
        engine.dispose()
        report["finished_at"] = datetime.now(UTC).isoformat()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--allow-terminal-history", action="store_true")
    args = parser.parse_args()
    if not args.database.is_file():
        raise ValueError("existing_database_required")
    args.output_dir.mkdir(parents=True, exist_ok=False)
    report: dict[str, Any] = {"mode": "apply" if args.apply else "preview", "committed": False,
                             "database": str(args.database.resolve()), "phase": "backup"}
    try:
        saved = args.output_dir / "pre-0066.sqlite3"
        report["backup"] = backup(args.database, saved)
        preview = args.output_dir / "preview.sqlite3"
        report["preview_copy"] = backup(saved, preview)
        report["phase"] = "preview_migration"
        report["preview"] = upgrade_in_writer(preview, require_idle=False)
        before, after = snapshot(saved), snapshot(preview)
        report["preview_verification"] = _verify_original(before, after)
        if args.apply:
            def final_backup(_connection: Connection) -> None:
                report["final_writer_backup"] = backup(
                    args.database, args.output_dir / "final-pre-0066.sqlite3",
                )
                report["backup_under_original_writer"] = True

            upgrade_in_writer(args.database, receipt=report, before_upgrade=final_backup,
                              allow_terminal_history=args.allow_terminal_history)
        else:
            report.update(head=AFTER, phase="complete", new_job_rows=0,
                          source_database_modified=False)
    except BaseException as error:
        report.setdefault("error_type", type(error).__name__)
        report.setdefault("failure_phase", report["phase"])
        raise
    finally:
        with (args.output_dir / "migration.json").open("x", encoding="utf-8") as output:
            json.dump(report, output, ensure_ascii=False, indent=2)
            output.write("\n")
    print(json.dumps({"mode": report["mode"], "committed": report["committed"],
                      "head": report["head"]}))


if __name__ == "__main__":
    main()

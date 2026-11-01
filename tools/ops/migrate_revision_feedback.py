"""Preview 0065; apply only under one physical writer after actual idle checks.

Example: python -m tools.ops.migrate_revision_feedback --database PATH --output-dir FRESH_DIR
Add --apply to migrate that database after preview/gates. All receipts and backups are retained.
Use --allow-terminal-history only to preserve unresolved children of terminal Runs.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, create_engine

from tools.ops.sqlite_typed_snapshot import backup, quoted, scalar, snapshot

ROOT = Path(__file__).resolve().parents[2]
BEFORE = "0064_evolution_planning_decisions"
AFTER = "0065_revision_feedback_evaluations"
TABLE = "revision_feedback_evaluations"


def new_index(name: str | None) -> bool:
    return name is not None and (
        name in {"ix_revision_feedback_owner_revision", "ix_revision_feedback_evaluations_run_id"}
        or name.startswith(f"sqlite_autoindex_{TABLE}_")
    )


def idle(connection: Connection) -> dict[str, list[dict[str, Any]]]:
    queries = {
        "active_or_live_leased_runs": "SELECT run_id,status,lease_owner,lease_expires_at FROM runs "
        "WHERE status IN ('admitted','dispatched','running') OR (lease_owner IS NOT NULL AND "
        "(lease_expires_at IS NULL OR julianday(lease_expires_at) IS NULL OR "
        "julianday(lease_expires_at)>julianday('now')))",
        "nonterminal_session_links": "SELECT run_id,state,generation,terminal_at FROM "
        "dsh_session_links WHERE state IS NULL OR state NOT IN ('completed','failed','cancelled')",
        "running_tool_reservations": "SELECT run_id,request_id,status FROM "
        "research_tool_call_reservations WHERE status IS NULL OR "
        "status NOT IN ('completed','failed','cancelled')",
    }
    return {name: [dict(row) for row in connection.exec_driver_sql(sql).mappings().all()]
            for name, sql in queries.items()}


# The actual RunStatus contract: admitted/running are nonterminal; unknown is denied.
TERMINAL_RUN_STATES = ("completed", "degraded", "failed", "cancelled")
_LEASE_TIME = re.compile(
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?"
    r"(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)?"
)


def _run_lease_issue(owner: object, expires_at: object, checked_at: datetime) -> str | None:
    if owner is None and expires_at is None:
        return None
    if (
        not isinstance(owner, str) or not owner.strip()
        or not isinstance(expires_at, str) or _LEASE_TIME.fullmatch(expires_at) is None
    ):
        return "invalid_run_lease"
    try:
        expiry = datetime.fromisoformat(expires_at)
    except ValueError:
        return "invalid_run_lease"
    # SQLAlchemy's existing SQLite DateTime storage uses naive UTC strings.
    expiry = expiry.replace(tzinfo=UTC) if expiry.tzinfo is None else expiry.astimezone(UTC)
    return "live_run_lease" if expiry > checked_at else None


def _physical_history_rows(
    connection: Connection, table: str, predicate: str,
) -> dict[str, Any]:
    metadata = connection.exec_driver_sql(f"PRAGMA table_xinfo({quoted(table)})").all()
    columns = [row[1] for row in metadata]
    if not columns or any(not isinstance(column, str) for column in columns):
        raise ValueError("physical_history_columns_invalid")
    # Casting TEXT to BLOB prevents UTF-8/newline normalization in the receipt.
    selectors = ",".join(
        f"typeof({quoted(column)}),CASE WHEN typeof({quoted(column)}) IN ('text','blob') "
        f"THEN CAST({quoted(column)} AS BLOB) ELSE {quoted(column)} END"
        for column in columns
    )
    rows = connection.exec_driver_sql(
        f"SELECT {selectors} FROM {quoted(table)} WHERE {predicate} ORDER BY rowid"
    ).all()
    return {
        "encoding": "sqlite-physical-typed-cells.v1",
        "columns": columns,
        "rows": [
            [{"sqlite_type": row[index], "cell": scalar(row[index + 1])}
             for index in range(0, len(row), 2)]
            for row in rows
        ],
    }


def _terminal_history_guard(connection: Connection) -> dict[str, Any]:
    checked_at = datetime.now(UTC)
    states = ",".join("?" for _state in TERMINAL_RUN_STATES)
    blockers: dict[str, list[dict[str, Any]]] = {
        "nonterminal_or_unknown_runs": [dict(row) for row in connection.exec_driver_sql(
            f"SELECT run_id,status FROM runs WHERE status IS NULL OR status NOT IN ({states})",
            TERMINAL_RUN_STATES,
        ).mappings().all()],
        "live_or_invalid_run_leases": [],
        "orphan_session_links": [dict(row) for row in connection.exec_driver_sql(
            "SELECT l.run_id,l.state,l.generation FROM dsh_session_links l "
            "LEFT JOIN runs r ON r.run_id=l.run_id WHERE r.run_id IS NULL"
        ).mappings().all()],
        "orphan_tool_reservations": [dict(row) for row in connection.exec_driver_sql(
            "SELECT t.run_id,t.request_id,t.status FROM research_tool_call_reservations t "
            "LEFT JOIN runs r ON r.run_id=t.run_id WHERE r.run_id IS NULL"
        ).mappings().all()],
    }
    for row in connection.exec_driver_sql(
        "SELECT run_id,status,lease_owner,lease_expires_at FROM runs "
        "WHERE lease_owner IS NOT NULL OR lease_expires_at IS NOT NULL"
    ).mappings().all():
        issue = _run_lease_issue(row["lease_owner"], row["lease_expires_at"], checked_at)
        if issue is not None:
            blockers["live_or_invalid_run_leases"].append({**dict(row), "reason": issue})
    return {
        "checked_at": checked_at.isoformat(),
        "terminal_run_states": list(TERMINAL_RUN_STATES),
        "blockers": blockers,
    }


def _retained_terminal_history(connection: Connection) -> dict[str, Any]:
    link_predicate = "state IS NULL OR state NOT IN ('completed','failed','cancelled')"
    reservation_predicate = "status IS NULL OR status NOT IN ('completed','failed','cancelled')"
    parent_predicate = (
        f"run_id IN (SELECT run_id FROM dsh_session_links WHERE {link_predicate} "
        "UNION SELECT run_id FROM research_tool_call_reservations WHERE "
        f"{reservation_predicate})"
    )
    return {
        "historical_state_repaired": False,
        "unknown_cost_assumed_zero": False,
        "runs": _physical_history_rows(connection, "runs", parent_predicate),
        "dsh_session_links": _physical_history_rows(
            connection, "dsh_session_links", link_predicate,
        ),
        "research_tool_call_reservations": _physical_history_rows(
            connection, "research_tool_call_reservations", reservation_predicate,
        ),
    }


def upgrade_in_writer(
    path: Path, *, receipt: dict[str, Any] | None = None,
    before_upgrade: Callable[[Connection], None] | None = None,
    require_idle: bool = True,
    allow_terminal_history: bool = False,
) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError("existing_database_required")
    report: dict[str, Any] = receipt if receipt is not None else {}
    report.update(started_at=datetime.now(UTC).isoformat(), committed=False, sql=[],
                  denied_mutations=[], rolled_back=False, phase="connecting")
    engine = create_engine(f"sqlite+pysqlite:///{path.resolve().as_posix()}",
                           connect_args={"timeout": 5})
    try:
        with engine.connect() as connection:
            native: sqlite3.Connection | None = None
            denied: list[dict[str, Any]] = []
            allow_transaction_end = False

            def authorize(action: int, first: str | None, second: str | None,
                          database: str | None, source: str | None) -> int:
                allowed = True
                if action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE):
                    allowed = first in {
                        TABLE, "alembic_version", "sqlite_master",
                    }
                elif action == sqlite3.SQLITE_CREATE_TABLE:
                    allowed = first in {TABLE, "sqlite_sequence"}
                elif action == sqlite3.SQLITE_CREATE_INDEX:
                    allowed = second == TABLE and new_index(first)
                elif action == sqlite3.SQLITE_REINDEX:
                    # SQLite emits REINDEX while creating each new index. Old
                    # indexes and arbitrary new index names remain denied.
                    allowed = new_index(first)
                elif action in (
                    sqlite3.SQLITE_DROP_TABLE, sqlite3.SQLITE_DROP_INDEX,
                    sqlite3.SQLITE_ALTER_TABLE, sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH,
                    sqlite3.SQLITE_CREATE_VIEW, sqlite3.SQLITE_DROP_VIEW,
                    sqlite3.SQLITE_CREATE_TRIGGER, sqlite3.SQLITE_DROP_TRIGGER,
                    sqlite3.SQLITE_CREATE_VTABLE, sqlite3.SQLITE_DROP_VTABLE,
                    sqlite3.SQLITE_CREATE_TEMP_TABLE, sqlite3.SQLITE_DROP_TEMP_TABLE,
                    sqlite3.SQLITE_CREATE_TEMP_INDEX, sqlite3.SQLITE_DROP_TEMP_INDEX,
                    sqlite3.SQLITE_CREATE_TEMP_VIEW, sqlite3.SQLITE_DROP_TEMP_VIEW,
                    sqlite3.SQLITE_CREATE_TEMP_TRIGGER, sqlite3.SQLITE_DROP_TEMP_TRIGGER,
                    sqlite3.SQLITE_SAVEPOINT, sqlite3.SQLITE_ANALYZE,
                ):
                    allowed = False
                elif action == sqlite3.SQLITE_TRANSACTION:
                    allowed = allow_transaction_end and first in {"COMMIT", "ROLLBACK"}
                elif action == sqlite3.SQLITE_PRAGMA:
                    allowed = second is None or first in {
                        "table_info", "table_xinfo", "foreign_key_list", "index_list",
                        "index_info", "index_xinfo", "integrity_check", "quick_check",
                    }
                if not allowed:
                    denied.append({"action": action, "first": first, "second": second,
                                   "database": database, "source": source})
                return sqlite3.SQLITE_OK if allowed else sqlite3.SQLITE_DENY

            try:
                report["phase"] = "acquiring_writer"
                connection.exec_driver_sql("BEGIN IMMEDIATE")
                raw_connection = connection.connection.driver_connection
                if not isinstance(raw_connection, sqlite3.Connection):
                    raise RuntimeError("physical_sqlite_writer_required")
                native = raw_connection
                if not native.in_transaction:
                    raise RuntimeError("physical_sqlite_writer_required")
                report["physical_writer_confirmed"] = True
                report["phase"] = "checking_schema_and_idle"
                head = connection.exec_driver_sql("SELECT version_num FROM alembic_version").all()
                if head != [(BEFORE,)]:
                    raise ValueError("expected_schema_0064")
                if connection.exec_driver_sql(
                    "SELECT count(*) FROM sqlite_master WHERE name=?", (TABLE,),
                ).scalar_one() != 0:
                    raise ValueError("feedback_binding_table_already_exists")
                report["idle"] = idle(connection)
                report["idle_required"] = require_idle
                report["allow_terminal_history"] = allow_terminal_history
                if allow_terminal_history:
                    report["terminal_history_guard"] = guard = _terminal_history_guard(connection)
                    if any(guard["blockers"].values()):
                        raise ValueError("migration_inflight_work")
                    report["retained_terminal_history"] = _retained_terminal_history(connection)
                elif require_idle and any(report["idle"].values()):
                    raise ValueError("migration_inflight_work")
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
                head = connection.exec_driver_sql("SELECT version_num FROM alembic_version").all()
                count = connection.exec_driver_sql(f"SELECT count(*) FROM {TABLE}").scalar_one()
                if head != [(AFTER,)] or count != 0 or denied:
                    raise RuntimeError("migration_postcondition_failed")
                report["phase"] = "committing"
                allow_transaction_end = True
                connection.commit()
                report.update(committed=True, head=AFTER, new_binding_rows=count,
                              denied_mutations=denied, same_physical_transaction=True,
                              phase="complete")
            except BaseException as error:
                report.update(error_type=type(error).__name__, failure_phase=report["phase"],
                              denied_mutations=denied)
                held_writer = native is not None and native.in_transaction
                allow_transaction_end = True
                if connection.in_transaction():
                    connection.rollback()
                report["rolled_back"] = (
                    native is not None and held_writer and not native.in_transaction
                )
                raise
            finally:
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
    parser.add_argument(
        "--allow-terminal-history", action="store_true",
        help="Preserve unresolved children of terminal Runs; active/invalid/orphan work is denied.",
    )
    args = parser.parse_args()
    if not args.database.is_file():
        raise ValueError("existing_database_required")
    args.output_dir.mkdir(parents=True, exist_ok=False)
    os.environ.update(DECISION_HUB_DATA_DIR=str(args.output_dir / "imports"),
                      DECISION_HUB_LLM_ENABLED="0",
                      DECISION_HUB_DOCUMENT_CORRECTION_LLM_ENABLED="0",
                      DECISION_HUB_SOURCES_ENABLED="0")
    mode = "apply" if args.apply else "preview"
    target = args.database if args.apply else args.output_dir / "preview.sqlite3"
    report: dict[str, Any] = {"mode": mode, "committed": False, "phase": "backup",
                             "database": str(args.database.resolve()), "target": str(target)}
    try:
        saved = args.output_dir / "pre-0065.sqlite3"
        report["backup"] = binary = backup(args.database, saved)
        print(json.dumps({"phase": "backup_complete", "bytes": binary["bytes"]}), flush=True)
        if not args.apply:
            report["phase"] = "preview_copy"
            backup(saved, target)
        def final_backup(_connection: Connection) -> None:
            # The independent reader sees the last committed state while the
            # original physical writer excludes all intervening writers.
            report["phase"] = "final_writer_backup"
            report["preflight_backup"] = report.pop("backup")
            report["backup"] = backup(args.database, args.output_dir / "final-pre-0065.sqlite3")
            report["backup_under_original_writer"] = True
            report["phase"] = "migrating"

        upgrade_in_writer(target, receipt=report, require_idle=args.apply,
                          allow_terminal_history=args.allow_terminal_history,
                          before_upgrade=final_backup if args.apply else None)
        if not args.apply:
            report["phase"] = "preview_verification"
            before, after = snapshot(saved), snapshot(target)
            preserved = {name: value == after["tables"].get(name)
                         for name, value in before["tables"].items() if name != "alembic_version"}
            old_objects_preserved = all(
                item in after["schema_objects"] for item in before["schema_objects"]
            )
            report.update(before=before, after=after, original_tables_unchanged=preserved,
                          original_schema_objects_preserved=old_objects_preserved)
            if (not all(preserved.values()) or not old_objects_preserved
                    or after["integrity"] != ["ok"]):
                raise RuntimeError("preview_physical_snapshot_changed")
        report["phase"] = "complete"
    except BaseException as error:
        report.setdefault("error_type", type(error).__name__)
        report.setdefault("failure_phase", report["phase"])
        raise
    finally:
        with (args.output_dir / "migration.json").open("x", encoding="utf-8") as output:
            json.dump(report, output, ensure_ascii=False, indent=2)
            output.write("\n")
    print(json.dumps({"mode": mode, "committed": report["committed"], "head": report["head"]}))


if __name__ == "__main__":
    main()

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, event
from sqlalchemy.exc import DatabaseError, OperationalError

from tools.ops import migrate_assessment_jobs as migration
from tools.ops.sqlite_typed_snapshot import backup, snapshot


def original(path: Path, *, history: bool = False) -> dict[str, Any]:
    config = Config(str(migration.ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{path.as_posix()}")
    config.attributes["configure_logger"] = False
    command.upgrade(config, migration.BEFORE)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO workbench_feedback "
            "(feedback_id,request_id,target_type,target_id,created_by,verdict,notes,created_at) "
            "VALUES ('old','old/key','report_revision','old-revision','owner','needs_review',"
            "?,'2026-10-04 06:00:00.000000')", (b"\x00\xff original bytes",),
        )
        if history:
            connection.execute(
                "INSERT INTO runs (run_id,event_id,status,strategy_version,runtime_version,"
                "created_at,updated_at,finished_at,cost_usd) VALUES ('history','event','degraded',"
                "'strategy','runtime','2026-10-04 05:00:00.000000',"
                "'2026-10-04 05:10:00.000000','2026-10-04 05:10:00.000000',NULL)"
            )
            connection.execute(
                "INSERT INTO dsh_session_links "
                "(run_id,dsh_session_id,request_hash,state,generation,"
                "upstream_identity_json,plugin_build_hash,last_seq,created_at,updated_at,"
                "tool_calls_started) VALUES ('history','old-session',?,'admitted',2,?,?,-1,"
                "'2026-10-04 05:00:00.000000','2026-10-04 05:01:00.000000',1)",
                ("a" * 64, '{\r\n"original":"历史"\r\n}', "b" * 64),
            )
    return snapshot(path)


def assert_original(before: dict[str, Any], after: dict[str, Any]) -> None:
    assert all(value == after["tables"].get(name) for name, value in before["tables"].items()
               if name != "alembic_version")
    assert all(item in after["schema_objects"] for item in before["schema_objects"])
    assert after["integrity"] == ["ok"]
    assert after["tables"][migration.TABLE]["row_count"] == 0


@pytest.mark.parametrize("apply", [False, True])
def test_cli_previews_copy_then_applies_only_under_writer_with_exclusive_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, apply: bool,
) -> None:
    path = tmp_path / "original.sqlite3"
    before = original(path, history=True)
    output = tmp_path / "receipt"
    argv = ["migrate_assessment_jobs", "--database", str(path), "--output-dir", str(output)]
    if apply:
        argv += ["--apply", "--allow-terminal-history"]
    monkeypatch.setattr("sys.argv", argv)
    migration.main()
    receipt = json.loads((output / "migration.json").read_text("utf-8"))
    assert snapshot(output / "pre-0066.sqlite3") == before
    assert_original(before, snapshot(output / "preview.sqlite3"))
    assert receipt["preview"]["committed"] is True
    assert all(receipt["preview_verification"]["original_tables_unchanged"].values())
    if apply:
        assert receipt["committed"] and receipt["same_physical_transaction"]
        assert receipt["backup_under_original_writer"]
        assert snapshot(output / "final-pre-0066.sqlite3") == before
        assert_original(before, snapshot(path))
        retained = receipt["retained_terminal_history"]
        assert retained["historical_state_repaired"] is False
        assert retained["unknown_cost_assumed_zero"] is False
        assert len(retained["dsh_session_links"]["rows"]) == 1
        assert receipt["original_schema_objects_preserved"]
        assert all(receipt["original_tables_unchanged"].values())
    else:
        assert receipt["committed"] is False and receipt["source_database_modified"] is False
        assert snapshot(path) == before
    # A repeated output directory cannot overwrite a backup or receipt.
    frozen_receipt = (output / "migration.json").read_bytes()
    with pytest.raises(FileExistsError):
        migration.main()
    assert (output / "migration.json").read_bytes() == frozen_receipt


@pytest.mark.parametrize("fault", [False, True])
def test_real_physical_writer_blocks_second_writer_and_rolls_back_ddl_fault(
    tmp_path: Path, fault: bool,
) -> None:
    path = tmp_path / "writer.sqlite3"
    before = original(path)
    report: dict[str, Any] = {}
    observed: list[bool] = []

    def install(connection: Connection) -> None:
        def after_table(_connection: Connection, _cursor: object, statement: str,
                        _parameters: object, _context: object, _many: bool) -> None:
            if not statement.lstrip().upper().startswith("CREATE TABLE ANALYTICAL_ASSESSMENT_JOBS"):
                return
            native = connection.connection.driver_connection
            assert isinstance(native, sqlite3.Connection) and native.in_transaction
            with sqlite3.connect(path, timeout=0.05) as other:
                assert other.execute("SELECT version_num FROM alembic_version").fetchone() == (
                    migration.BEFORE,
                )
                assert other.execute(
                    "SELECT count(*) FROM sqlite_master WHERE name=?", (migration.TABLE,),
                ).fetchone() == (0,)
                with pytest.raises(sqlite3.OperationalError) as busy:
                    other.execute("BEGIN IMMEDIATE")
                assert busy.value.sqlite_errorcode & 0xff == sqlite3.SQLITE_BUSY
            observed.append(True)
            if fault:
                raise RuntimeError("actual_after_create_fault")
        event.listen(connection, "after_cursor_execute", after_table)

    if fault:
        with pytest.raises(RuntimeError, match="actual_after_create_fault"):
            migration.upgrade_in_writer(path, receipt=report, before_upgrade=install)
        assert report["rolled_back"] and report["committed"] is False
        assert snapshot(path) == before
    else:
        migration.upgrade_in_writer(path, receipt=report, before_upgrade=install)
        assert report["committed"] and report["same_physical_transaction"]
        assert_original(before, snapshot(path))
    assert observed == [True]


@pytest.mark.parametrize("statement", [
    "UPDATE workbench_feedback SET notes='replacement'",
    "CREATE INDEX forbidden_index ON workbench_feedback(notes)",
    "PRAGMA user_version=7",
    "COMMIT",
])
def test_authorizer_rejects_old_write_schema_and_early_commit(
    tmp_path: Path, statement: str,
) -> None:
    path = tmp_path / "forbidden.sqlite3"
    before = original(path)
    report: dict[str, Any] = {}

    def mutate(connection: Connection) -> None:
        connection.exec_driver_sql(statement)

    with pytest.raises(DatabaseError, match="not authorized"):
        migration.upgrade_in_writer(path, receipt=report, before_upgrade=mutate)
    assert report["denied_mutations"] and report["rolled_back"] and not report["committed"]
    assert snapshot(path) == before


@pytest.mark.parametrize("condition", ["wrong_head", "active_run", "strict_history"])
def test_wrong_head_or_inflight_work_refused_without_old_cell_changes(
    tmp_path: Path, condition: str,
) -> None:
    path = tmp_path / "blocked.sqlite3"
    original(path, history=True)
    with sqlite3.connect(path) as connection:
        if condition == "wrong_head":
            connection.execute("UPDATE alembic_version SET version_num='unexpected-head'")
        elif condition == "active_run":
            connection.execute("UPDATE runs SET status='running' WHERE run_id='history'")
    before = snapshot(path)
    report: dict[str, Any] = {}
    expected = "expected_schema_0065" if condition == "wrong_head" else "migration_inflight_work"
    with pytest.raises(ValueError, match=expected):
        migration.upgrade_in_writer(path, receipt=report,
                                    allow_terminal_history=condition == "active_run")
    assert report["rolled_back"] and not report["committed"]
    assert snapshot(path) == before


def test_authorizer_refuses_backfill_into_new_job_table(tmp_path: Path) -> None:
    path = tmp_path / "backfill.sqlite3"
    before = original(path)
    report: dict[str, Any] = {}

    def install(connection: Connection) -> None:
        def after_table(_connection: Connection, _cursor: object, statement: str,
                        _parameters: object, _context: object, _many: bool) -> None:
            if statement.lstrip().upper().startswith("CREATE TABLE ANALYTICAL_ASSESSMENT_JOBS"):
                connection.exec_driver_sql(
                    "INSERT INTO analytical_assessment_jobs "
                    "(outlook_id,status,attempt,next_attempt_at,verifier_version) "
                    "VALUES ('fake','pending',0,'2026-10-04 06:00:00.000000','fake')"
                )
        event.listen(connection, "after_cursor_execute", after_table)

    with pytest.raises(DatabaseError, match="not authorized"):
        migration.upgrade_in_writer(path, receipt=report, before_upgrade=install)
    assert report["denied_mutations"][0]["first"] == migration.TABLE
    assert report["rolled_back"] and not report["committed"]
    assert snapshot(path) == before


def test_busy_writer_refused_and_preserves_failure_receipt(tmp_path: Path) -> None:
    path = tmp_path / "busy.sqlite3"
    before = original(path)
    report: dict[str, Any] = {}
    with sqlite3.connect(path) as writer:
        writer.execute("BEGIN IMMEDIATE")
        with pytest.raises(OperationalError, match="database is locked"):
            migration.upgrade_in_writer(path, receipt=report)
        assert writer.in_transaction
        writer.rollback()
    assert report["failure_phase"] == "acquiring_writer" and report["committed"] is False
    assert snapshot(path) == before


def test_final_backup_captures_real_commit_between_preview_and_original_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "gap.sqlite3"
    before = original(path)
    output = tmp_path / "receipt"
    gap: dict[str, Any] = {}

    def concurrent_backup(source: Path, target: Path) -> dict[str, str | int]:
        result = backup(source, target)
        if target.name == "pre-0066.sqlite3":
            with sqlite3.connect(path) as other:
                other.execute("UPDATE workbench_feedback SET notes=? WHERE feedback_id='old'",
                              (b"\x00\xfe legitimately committed gap",))
            gap.update(snapshot(path))
        return result

    monkeypatch.setattr(migration, "backup", concurrent_backup)
    monkeypatch.setattr("sys.argv", ["migrate_assessment_jobs", "--database", str(path),
                                       "--output-dir", str(output), "--apply"])
    migration.main()
    assert snapshot(output / "pre-0066.sqlite3") == before
    assert snapshot(output / "final-pre-0066.sqlite3") == gap
    assert_original(gap, snapshot(path))

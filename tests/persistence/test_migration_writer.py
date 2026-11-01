from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, event
from sqlalchemy.exc import DatabaseError

from tools.ops import migrate_revision_feedback as migration
from tools.ops.migrate_revision_feedback import BEFORE, ROOT, upgrade_in_writer
from tools.ops.sqlite_typed_snapshot import backup, snapshot


def original(path: Path) -> dict[str, Any]:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{path.as_posix()}")
    config.attributes["configure_logger"] = False
    command.upgrade(config, BEFORE)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO workbench_feedback "
            "(feedback_id,request_id,target_type,target_id,created_by,verdict,notes,created_at) "
            "VALUES ('old','old/key','report_revision','old-revision','owner','needs_review',"
            "?,'2026-10-03 06:00:00.000000')", (b"\x00\xff original bytes",))
    return snapshot(path)


@pytest.mark.parametrize("fault", [False, True])
def test_external_writer_preserves_old_cells_and_rolls_back_real_ddl(
    tmp_path: Path, fault: bool,
) -> None:
    path = tmp_path / "writer.sqlite3"
    before = original(path)
    observed: list[dict[str, Any]] = []
    receipt: dict[str, Any] = {}

    def install(connection: Connection) -> None:
        def after_table(_connection: Connection, _cursor: object, statement: str,
                        _parameters: object, _context: object, _many: bool) -> None:
            if not statement.lstrip().upper().startswith(
                "CREATE TABLE REVISION_FEEDBACK_EVALUATIONS",
            ):
                return
            native = connection.connection.driver_connection
            assert isinstance(native, sqlite3.Connection) and native.in_transaction
            with sqlite3.connect(path, timeout=0.05) as second:
                assert second.execute(
                    "SELECT version_num FROM alembic_version",
                ).fetchone() == (BEFORE,)
                assert second.execute(
                    "SELECT count(*) FROM sqlite_master WHERE name='revision_feedback_evaluations'"
                ).fetchone() == (0,)
                with pytest.raises(sqlite3.OperationalError) as busy:
                    second.execute("BEGIN IMMEDIATE")
                assert busy.value.sqlite_errorcode & 0xff == sqlite3.SQLITE_BUSY
            observed.append({"physical_writer": True, "second_writer_busy": True})
            if fault:
                raise RuntimeError("actual_after_table_fault")
        event.listen(connection, "after_cursor_execute", after_table)

    if fault:
        with pytest.raises(RuntimeError, match="actual_after_table_fault"):
            upgrade_in_writer(path, receipt=receipt, before_upgrade=install)
        assert receipt["committed"] is False and receipt["rolled_back"] is True
        assert snapshot(path) == before
    else:
        upgrade_in_writer(path, receipt=receipt, before_upgrade=install)
        after = snapshot(path)
        assert receipt["committed"] and receipt["same_physical_transaction"]
        assert all(value == after["tables"][name] for name, value in before["tables"].items()
                   if name != "alembic_version")
        assert after["tables"]["revision_feedback_evaluations"]["row_count"] == 0
        assert after["integrity"] == ["ok"]
    assert observed == [{"physical_writer": True, "second_writer_busy": True}]


def test_migration_authorizer_denies_old_opinion_mutation(tmp_path: Path) -> None:
    path = tmp_path / "forbidden.sqlite3"
    before = original(path)
    receipt: dict[str, Any] = {}

    def mutate(connection: Connection) -> None:
        connection.exec_driver_sql("UPDATE workbench_feedback SET notes='replacement'")

    with pytest.raises(DatabaseError, match="not authorized"):
        upgrade_in_writer(path, receipt=receipt, before_upgrade=mutate)
    assert receipt["committed"] is False and receipt["rolled_back"]
    assert receipt["denied_mutations"][0]["first"] == "workbench_feedback"
    assert snapshot(path) == before


@pytest.mark.parametrize("statement", [
    "CREATE VIEW bypass_view AS SELECT feedback_id FROM workbench_feedback",
    "CREATE TRIGGER bypass_trigger AFTER UPDATE ON workbench_feedback BEGIN SELECT 1; END",
    "PRAGMA user_version = 7",
    "COMMIT",
    "ROLLBACK",
    "SAVEPOINT bypass_savepoint",
])
def test_authorizer_preserves_schema_and_original_transaction(
    tmp_path: Path, statement: str,
) -> None:
    path = tmp_path / "bypass.sqlite3"
    before = original(path)
    receipt: dict[str, Any] = {}

    def bypass(connection: Connection) -> None:
        connection.exec_driver_sql(statement)

    with pytest.raises(DatabaseError, match="not authorized"):
        upgrade_in_writer(path, receipt=receipt, before_upgrade=bypass)
    assert receipt["committed"] is False and receipt["rolled_back"] is True
    assert receipt["failure_phase"] == "migrating"
    assert receipt["denied_mutations"]
    assert snapshot(path) == before
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (0,)


@pytest.mark.parametrize("unfinished", [False, True])
def test_idle_uses_persisted_reservation_states_and_preserves_preflight_failure(
    tmp_path: Path, unfinished: bool,
) -> None:
    path = tmp_path / "reservation.sqlite3"
    original(path)
    statuses = ["completed", "failed", "cancelled"]
    if unfinished:
        statuses.extend(["running", "unrecognized"])
    with sqlite3.connect(path) as connection:
        connection.executemany(
            "INSERT INTO research_tool_call_reservations "
            "(run_id,request_id,research_session_id,generation,capability_id,status,reserved_at) "
            "VALUES ('old',?,'old-session',1,'web.fetch',?,'2026-10-03 06:00:00.000000')",
            [(f"request-{status}", status) for status in statuses],
        )
    before = snapshot(path)
    receipt: dict[str, Any] = {}
    if unfinished:
        with pytest.raises(ValueError, match="migration_inflight_work"):
            upgrade_in_writer(path, receipt=receipt)
        assert receipt["committed"] is False and receipt["rolled_back"] is True
        assert receipt["error_type"] == "ValueError"
        assert receipt["failure_phase"] == "checking_schema_and_idle"
        assert {row["status"] for row in receipt["idle"]["running_tool_reservations"]} == {
            "running", "unrecognized",
        }
        assert snapshot(path) == before
    else:
        upgrade_in_writer(path, receipt=receipt)
        after = snapshot(path)
        assert receipt["committed"] and not any(receipt["idle"].values())
        assert all(value == after["tables"][name] for name, value in before["tables"].items()
                   if name != "alembic_version")


def test_cli_final_backup_keeps_real_commit_between_early_backup_and_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "gap.sqlite3"
    original(path)
    output_dir = tmp_path / "receipt"
    committed_gap: dict[str, Any] = {}

    def concurrent_backup(source: Path, target: Path) -> dict[str, str | int]:
        result = backup(source, target)
        if target.name == "pre-0065.sqlite3":
            with sqlite3.connect(path) as other_writer:
                other_writer.execute(
                    "UPDATE workbench_feedback SET notes='legitimate committed gap' "
                    "WHERE feedback_id='old'",
                )
            committed_gap.update(snapshot(path))
        return result

    monkeypatch.setattr(migration, "backup", concurrent_backup)
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "imports"))
    monkeypatch.setattr("sys.argv", [
        "migrate_revision_feedback", "--database", str(path),
        "--output-dir", str(output_dir), "--apply",
    ])
    migration.main()
    receipt = json.loads((output_dir / "migration.json").read_text("utf-8"))
    early = snapshot(output_dir / "pre-0065.sqlite3")
    final = snapshot(output_dir / "final-pre-0065.sqlite3")
    after = snapshot(path)
    assert early["tables"]["workbench_feedback"] != final["tables"]["workbench_feedback"]
    assert final == committed_gap
    assert receipt["backup_under_original_writer"] and receipt["same_physical_transaction"]
    assert receipt["committed"] and receipt["phase"] == "complete"
    assert all(value == after["tables"][name] for name, value in final["tables"].items()
               if name != "alembic_version")
    assert all(item in after["schema_objects"] for item in final["schema_objects"])



def _terminal_history_original(path: Path, status: str = "cancelled") -> dict[str, Any]:
    original(path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO runs (run_id,event_id,status,strategy_version,runtime_version,"
            "created_at,updated_at,finished_at,cost_usd) "
            "VALUES ('history','original-event',?,'original-strategy','original-runtime',"
            "'2026-09-21 12:00:00.000000','2026-09-21 12:21:00.000000',"
            "'2026-09-21 12:21:00.000000',NULL)", (status,),
        )
        connection.execute(
            "INSERT INTO dsh_session_links (run_id,dsh_session_id,request_hash,state,generation,"
            "upstream_identity_json,plugin_build_hash,last_seq,created_at,updated_at,"
            "tool_calls_started) VALUES ('history','original-session',?,'admitted',2,?,?,-1,"
            "'2026-09-21 12:00:00.000000','2026-09-21 12:01:00.000000',1)",
            ("a" * 64, '{\r\n  "original": "历史"\r\n}', "b" * 64),
        )
        connection.execute(
            "ALTER TABLE research_tool_call_reservations ADD COLUMN extra_original BLOB"
        )
        connection.execute(
            "INSERT INTO research_tool_call_reservations "
            "(run_id,request_id,research_session_id,generation,capability_id,status,reserved_at,"
            "result_json,extra_original) VALUES ('history','original/call','original-session',2,"
            "'web.fetch','running','2026-09-21 12:04:31.395764',?,?)",
            (b'\x00\xff original result', b'\x00\xfe extra physical column'),
        )
    return snapshot(path)


@pytest.mark.parametrize("status", ["completed", "degraded", "failed", "cancelled"])
def test_explicit_terminal_history_preserves_complete_original_rows_under_one_writer(
    tmp_path: Path, status: str,
) -> None:
    import base64

    path = tmp_path / "terminal-history.sqlite3"
    _terminal_history_original(path, status)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE runs SET lease_owner='expired-original-owner',"
            "lease_expires_at='2000-01-01 00:00:00.000000' WHERE run_id='history'"
        )
    before = snapshot(path)
    receipt: dict[str, Any] = {}
    held: list[bool] = []

    def inspect_writer(connection: Connection) -> None:
        native = connection.connection.driver_connection
        assert isinstance(native, sqlite3.Connection) and native.in_transaction
        with sqlite3.connect(path, timeout=0.05) as other:
            with pytest.raises(sqlite3.OperationalError) as busy:
                other.execute("BEGIN IMMEDIATE")
            assert busy.value.sqlite_errorcode & 0xff == sqlite3.SQLITE_BUSY
        retained = receipt["retained_terminal_history"]
        assert retained["historical_state_repaired"] is False
        assert retained["unknown_cost_assumed_zero"] is False
        for table in ("runs", "dsh_session_links", "research_tool_call_reservations"):
            assert retained[table]["columns"] == before["tables"][table]["columns"]
            assert len(retained[table]["rows"]) == 1
            assert len(retained[table]["rows"][0]) == len(retained[table]["columns"])
        run_cells = dict(zip(retained["runs"]["columns"], retained["runs"]["rows"][0], strict=True))
        assert run_cells["cost_usd"] == {"sqlite_type": "null", "cell": {"type": "null"}}
        link = retained["dsh_session_links"]
        link_cells = dict(zip(link["columns"], link["rows"][0], strict=True))
        assert link_cells["upstream_identity_json"]["sqlite_type"] == "text"
        assert base64.b64decode(link_cells["upstream_identity_json"]["cell"]["base64"]) == (
            '{\r\n  "original": "历史"\r\n}'.encode()
        )
        assert link_cells["model_step_timeout_ms"]["sqlite_type"] == "null"
        reservation = retained["research_tool_call_reservations"]
        cells = dict(zip(reservation["columns"], reservation["rows"][0], strict=True))
        assert cells["result_json"]["sqlite_type"] == "blob"
        assert base64.b64decode(cells["result_json"]["cell"]["base64"]) == (
            b'\x00\xff original result'
        )
        assert base64.b64decode(cells["extra_original"]["cell"]["base64"]) == (
            b'\x00\xfe extra physical column'
        )
        held.append(True)

    upgrade_in_writer(
        path, receipt=receipt, allow_terminal_history=True, before_upgrade=inspect_writer,
    )
    after = snapshot(path)
    assert receipt["committed"] and receipt["same_physical_transaction"] and held == [True]
    assert receipt["allow_terminal_history"] is True and receipt["idle_required"] is True
    assert not any(receipt["terminal_history_guard"]["blockers"].values())
    assert receipt["idle"]["nonterminal_session_links"]
    assert receipt["idle"]["running_tool_reservations"]
    assert all(value == after["tables"][name] for name, value in before["tables"].items()
               if name != "alembic_version")
    assert all(item in after["schema_objects"] for item in before["schema_objects"])
    assert after["tables"]["revision_feedback_evaluations"]["row_count"] == 0
    assert after["integrity"] == ["ok"]


def test_default_strict_idle_still_refuses_terminal_history_without_mutation(
    tmp_path: Path,
) -> None:
    path = tmp_path / "strict.sqlite3"
    before = _terminal_history_original(path)
    receipt: dict[str, Any] = {}
    with pytest.raises(ValueError, match="migration_inflight_work"):
        upgrade_in_writer(path, receipt=receipt)
    assert receipt["allow_terminal_history"] is False
    assert receipt["committed"] is False and receipt["rolled_back"] is True
    assert "retained_terminal_history" not in receipt
    assert snapshot(path) == before


@pytest.mark.parametrize("blocked,expected", [
    ("admitted", "nonterminal_or_unknown_runs"),
    ("running", "nonterminal_or_unknown_runs"),
    ("unknown_run_state", "nonterminal_or_unknown_runs"),
    ("live_lease", "live_or_invalid_run_leases"),
    ("missing_lease_expiry", "live_or_invalid_run_leases"),
    ("invalid_lease_expiry", "live_or_invalid_run_leases"),
    ("orphan_lease_expiry", "live_or_invalid_run_leases"),
    ("orphan_link", "orphan_session_links"),
    ("orphan_reservation", "orphan_tool_reservations"),
])
def test_terminal_history_mode_rejects_real_active_lease_unknown_and_orphan_rows(
    tmp_path: Path, blocked: str, expected: str,
) -> None:
    path = tmp_path / "blocked.sqlite3"
    _terminal_history_original(path)
    with sqlite3.connect(path) as connection:
        if expected == "nonterminal_or_unknown_runs":
            connection.execute("UPDATE runs SET status=? WHERE run_id='history'", (blocked,))
        elif expected == "live_or_invalid_run_leases":
            leases = {
                "live_lease": ("owner", "2999-01-01T00:00:00Z"),
                "missing_lease_expiry": ("owner", None),
                "invalid_lease_expiry": ("owner", "2026-02-30T00:00:00Z"),
                "orphan_lease_expiry": (None, "2000-01-01 00:00:00.000000"),
            }
            connection.execute(
                "UPDATE runs SET lease_owner=?,lease_expires_at=? WHERE run_id='history'",
                leases[blocked],
            )
        elif blocked == "orphan_link":
            connection.execute(
                "UPDATE dsh_session_links SET run_id='missing-parent',state='failed'"
            )
        else:
            connection.execute(
                "UPDATE research_tool_call_reservations "
                "SET run_id='missing-parent',status='completed'"
            )
    before = snapshot(path)
    receipt: dict[str, Any] = {}
    with pytest.raises(ValueError, match="migration_inflight_work"):
        upgrade_in_writer(path, receipt=receipt, allow_terminal_history=True)
    assert receipt["terminal_history_guard"]["blockers"][expected]
    assert receipt["failure_phase"] == "checking_schema_and_idle"
    assert receipt["physical_writer_confirmed"] is True and receipt["rolled_back"] is True
    assert receipt["committed"] is False and "retained_terminal_history" not in receipt
    assert snapshot(path) == before


def test_cli_explicit_terminal_history_keeps_backup_and_original_physical_cells(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "explicit-cli.sqlite3"
    before = _terminal_history_original(path)
    output_dir = tmp_path / "explicit-receipt"
    monkeypatch.setattr("sys.argv", [
        "migrate_revision_feedback", "--database", str(path), "--output-dir", str(output_dir),
        "--apply", "--allow-terminal-history",
    ])
    migration.main()
    receipt = json.loads((output_dir / "migration.json").read_text("utf-8"))
    after = snapshot(path)
    assert receipt["allow_terminal_history"] is True and receipt["committed"] is True
    assert receipt["backup_under_original_writer"] is True
    assert receipt["same_physical_transaction"] is True
    assert snapshot(output_dir / "final-pre-0065.sqlite3") == before
    assert receipt["retained_terminal_history"]["historical_state_repaired"] is False
    assert len(receipt["retained_terminal_history"]["dsh_session_links"]["rows"]) == 1
    assert len(receipt["retained_terminal_history"]["research_tool_call_reservations"]["rows"]) == 1
    assert all(value == after["tables"][name] for name, value in before["tables"].items()
               if name != "alembic_version")



def _wal_backup_source(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
        connection.execute("CREATE TABLE original_cells (id INTEGER PRIMARY KEY, payload BLOB)")
        connection.executemany(
            "INSERT INTO original_cells VALUES (?,?)", [(index, bytes([index]) * 8192)
                                                       for index in range(64)],
        )


def test_fixed_backup_snapshot_survives_real_wal_commits_between_steps(tmp_path: Path) -> None:
    from tools.ops import sqlite_typed_snapshot as snapshots

    source = tmp_path / "wal-source.sqlite3"
    target = tmp_path / "fixed-backup.sqlite3"
    _wal_backup_source(source)
    before = snapshot(source)
    commits: list[int] = []
    with sqlite3.connect(source) as writer:
        def write_after_step(status: int, remaining: int, _total: int) -> None:
            if status == sqlite3.SQLITE_OK and remaining > 0 and len(commits) < 3:
                writer.execute("UPDATE original_cells SET payload=? WHERE id=0",
                               (b"later committed source " + bytes([len(commits)]),))
                writer.commit()
                commits.append(remaining)

        receipt = snapshots.backup(source, target, pages=1, progress=write_after_step)
    calls = receipt["progress_calls"]
    assert len(commits) == 3 and isinstance(calls, int) and calls > 3
    assert receipt["source_snapshot_fixed"] is True
    assert receipt["source_closed"] is True and receipt["destination_closed"] is True
    assert receipt["last_status"] == sqlite3.SQLITE_DONE and receipt["remaining_pages"] == 0
    assert snapshot(target) == before
    assert snapshot(source)["tables"]["original_cells"] != before["tables"]["original_cells"]
    assert not target.with_name(target.name + ".backup-status.json").exists()


def test_backup_snapshot_acquisition_busy_is_finite_and_closes_source(tmp_path: Path) -> None:
    import time

    from tools.ops import sqlite_typed_snapshot as snapshots

    source = tmp_path / "exclusive-source.sqlite3"
    target = tmp_path / "blocked-backup.sqlite3"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE original_cells (id INTEGER PRIMARY KEY, payload BLOB)")
        connection.execute("INSERT INTO original_cells VALUES (1,?)", (b"unchanged",))
    before = snapshot(source)
    with sqlite3.connect(source) as writer:
        writer.execute("BEGIN EXCLUSIVE")
        started = time.monotonic()
        with pytest.raises(sqlite3.OperationalError) as busy:
            snapshots.backup(source, target, timeout_seconds=0.05)
        elapsed = time.monotonic() - started
        assert busy.value.sqlite_errorcode & 0xff == sqlite3.SQLITE_BUSY
        assert elapsed < 2
        writer.rollback()
    failure = json.loads(target.with_name(target.name + ".backup-status.json").read_text("utf-8"))
    assert failure["phase"] == "source_snapshot_acquire"
    assert failure["source_closed"] is True and failure["source_snapshot_fixed"] is False
    assert failure["progress_calls"] == 0 and failure["partial_retained"] is False
    assert failure["sqlite_errorcode"] & 0xff == sqlite3.SQLITE_BUSY
    assert snapshot(source) == before
    with sqlite3.connect(source, timeout=0.05) as probe:
        probe.execute("BEGIN EXCLUSIVE")
        probe.rollback()


@pytest.mark.parametrize("fault", ["deadline", "callback_error"])
def test_backup_step_timeout_or_error_retains_partial_and_releases_real_connections(
    tmp_path: Path, fault: str,
) -> None:
    import time

    from tools.ops import sqlite_typed_snapshot as snapshots

    source = tmp_path / "step-source.sqlite3"
    target = tmp_path / "partial-backup.sqlite3"
    _wal_backup_source(source)
    before = snapshot(source)

    def fail_after_real_step(status: int, remaining: int, total: int) -> None:
        assert status == sqlite3.SQLITE_OK and 0 < remaining < total
        if fault == "deadline":
            time.sleep(0.08)
        else:
            raise RuntimeError("actual_backup_progress_failure")

    exception = TimeoutError if fault == "deadline" else RuntimeError
    message = (
        "sqlite_backup_deadline_elapsed"
        if fault == "deadline" else "actual_backup_progress_failure"
    )
    started = time.monotonic()
    with pytest.raises(exception, match=message):
        snapshots.backup(source, target, pages=1, timeout_seconds=0.05,
                         progress=fail_after_real_step)
    assert time.monotonic() - started < 2
    failure = json.loads(target.with_name(target.name + ".backup-status.json").read_text("utf-8"))
    assert target.exists() and failure["partial_retained"] is True
    assert failure["phase"] == "copying" and failure["progress_calls"] == 1
    assert failure["last_status"] == sqlite3.SQLITE_OK
    assert 0 < failure["remaining_pages"] < failure["total_pages"]
    assert failure["source_snapshot_fixed"] is True
    assert failure["source_closed"] is True and failure["destination_closed"] is True
    assert failure["error_type"] == exception.__name__
    assert snapshot(source) == before
    with sqlite3.connect(source, timeout=0.05) as probe:
        assert probe.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0] == 0
    with sqlite3.connect(target, timeout=0.05) as probe:
        probe.execute("BEGIN EXCLUSIVE")
        probe.rollback()

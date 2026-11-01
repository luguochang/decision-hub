from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

ROOT = Path(__file__).resolve().parents[2]
BEFORE = "0064_evolution_planning_decisions"
AFTER = "0065_revision_feedback_evaluations"


def _config(path: Path) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{path}")
    config.attributes["configure_logger"] = False
    return config


def _rows(path: Path) -> dict[str, list[tuple[object, ...]]]:
    with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as connection:
        connection.execute("PRAGMA query_only=ON")
        tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "AND name NOT IN ('alembic_version','revision_feedback_evaluations')"
        ).fetchall()
        return {name: sorted(connection.execute('SELECT * FROM "' + name + '"').fetchall(),
                             key=lambda row: json.dumps(row, ensure_ascii=False))
                for (name,) in tables}


def test_upgrade_and_downgrade_preserve_every_original_row_without_backfill(
    tmp_path: Path,
) -> None:
    path = tmp_path / "migration.sqlite3"
    config = _config(path)
    command.upgrade(config, BEFORE)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO workbench_feedback "
            "(feedback_id,request_id,target_type,target_id,created_by,verdict,notes,created_at) "
            "VALUES ('legacy','legacy/key','report_revision','historical','owner',"
            "'needs_review','原始意见未升级','2026-10-03 06:00:00.000000')"
        )
        connection.execute(
            "INSERT INTO events (event_id,event_type,occurred_at,received_at,generation,status) "
            "VALUES ('original','macro_event','2026-10-03 05:00:00.000000',"
            "'2026-10-03 05:00:01.000000',1,'active')"
        )
    before = _rows(path)
    command.upgrade(config, AFTER)
    assert _rows(path) == before
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (AFTER,)
        assert connection.execute(
            "SELECT count(*) FROM revision_feedback_evaluations"
        ).fetchone() == (0,)
        ddl = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='revision_feedback_evaluations'"
        ).fetchone()[0]
        assert "AUTOINCREMENT" in ddl
        columns = connection.execute("PRAGMA table_info(revision_feedback_evaluations)").fetchall()
        assert next(column for column in columns if column[1] == "binding_seq")[5] == 1
        unique = [
            row for row in connection.execute("PRAGMA index_list(revision_feedback_evaluations)")
            if row[2] == 1
        ]
        unique_columns = {
            tuple(item[2] for item in connection.execute('PRAGMA index_info("' + row[1] + '")'))
            for row in unique
        }
        assert {
            ("binding_id",), ("feedback_id",), ("request_id",), ("evaluation_id",),
        } <= unique_columns
    # An idempotent head upgrade cannot retrospectively create a binding.
    command.upgrade(config, AFTER)
    assert _rows(path) == before
    command.downgrade(config, BEFORE)
    assert _rows(path) == before
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (BEFORE,)
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name='revision_feedback_evaluations'"
        ).fetchone() is None

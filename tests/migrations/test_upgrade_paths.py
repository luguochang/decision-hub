from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from packages.kernel.decision_hub_kernel.persistence.db import Database

ROOT = Path(__file__).resolve().parents[2]
R1_HEAD = "0010_source_poll_schedule"


def upgrade(path: Path, revision: str) -> None:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{path}")
    command.upgrade(config, revision)


def revision(path: Path) -> str:
    engine = create_engine(f"sqlite+pysqlite:///{path}")
    with engine.connect() as connection:
        return connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()


def columns(path: Path, table_name: str) -> set[str]:
    engine = create_engine(f"sqlite+pysqlite:///{path}")
    return {column["name"] for column in inspect(engine).get_columns(table_name)}


def test_r0_schema_snapshot_does_not_leak_r1_tables(tmp_path: Path) -> None:
    path = tmp_path / "r0.sqlite3"

    upgrade(path, "0007_run_cost_nullable")

    assert revision(path) == "0007_run_cost_nullable"
    tables = inspect(create_engine(f"sqlite+pysqlite:///{path}")).get_table_names()
    assert "source_states" not in tables
    assert {"next_attempt_at", "failed_at", "last_error_code"}.isdisjoint(columns(path, "outbox"))


def test_r0_0007_upgrades_to_r1_head(tmp_path: Path) -> None:
    path = tmp_path / "r0-to-r1.sqlite3"
    upgrade(path, "0007_run_cost_nullable")

    Database(f"sqlite+pysqlite:///{path}").initialize()

    assert revision(path) == R1_HEAD
    assert "next_poll_at" in columns(path, "source_states")
    assert {"next_attempt_at", "failed_at", "last_error_code"}.issubset(columns(path, "outbox"))


def test_r1_0009_upgrades_to_r1_head(tmp_path: Path) -> None:
    path = tmp_path / "r1-0009.sqlite3"
    upgrade(path, "0009_outbox_delivery_state")

    Database(f"sqlite+pysqlite:///{path}").initialize()

    assert revision(path) == R1_HEAD
    assert "next_poll_at" in columns(path, "source_states")

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from packages.kernel.decision_hub_kernel.persistence.db import Database

ROOT = Path(__file__).resolve().parents[2]
CURRENT_HEAD = "0030_research_value_evaluations"


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


def test_r0_0007_upgrades_to_current_head(tmp_path: Path) -> None:
    path = tmp_path / "r0-to-r1.sqlite3"
    upgrade(path, "0007_run_cost_nullable")

    Database(f"sqlite+pysqlite:///{path}").initialize()

    assert revision(path) == CURRENT_HEAD
    assert "next_poll_at" in columns(path, "source_states")
    assert {"next_attempt_at", "failed_at", "last_error_code"}.issubset(columns(path, "outbox"))
    assert "request_id" in columns(path, "research_memos")
    assert "generation" in columns(path, "active_pointers")
    assert {"randomness_policy", "deadline_seconds", "max_cost_usd"}.issubset(
        columns(path, "experiments")
    )


def test_r1_0009_upgrades_to_current_head(tmp_path: Path) -> None:
    path = tmp_path / "r1-0009.sqlite3"
    upgrade(path, "0009_outbox_delivery_state")

    Database(f"sqlite+pysqlite:///{path}").initialize()

    assert revision(path) == CURRENT_HEAD
    assert "next_poll_at" in columns(path, "source_states")
    assert "status" in columns(path, "capability_manifests")
    assert "sample_count" in columns(path, "experiment_results")
    assert "event_family_counts_json" in columns(path, "experiment_results")
    assert "request_id" in columns(path, "promotion_decisions")
    assert "experiences" in inspect(create_engine(f"sqlite+pysqlite:///{path}")).get_table_names()
    assert {"raw_artifact_refs_json", "scorer_version"}.issubset(
        columns(path, "experiment_results")
    )
    assert {"evaluation_refs_json", "available_at"}.issubset(columns(path, "experiences"))
    tables = inspect(create_engine(f"sqlite+pysqlite:///{path}")).get_table_names()
    assert {"evolution_jobs", "service_heartbeats"}.issubset(tables)
    assert "research_evidence" in tables
    assert "research_facts" in tables
    assert "event_watches" in tables
    assert "event_window_samples" in tables
    assert {
        "evidence_id",
        "requirement_id",
        "metric_family",
        "field",
        "event_offset",
        "payload_hash",
    }.issubset(columns(path, "research_facts"))
    assert {"snapshot_type", "run_id", "generation", "created_at"}.issubset(
        columns(path, "snapshots")
    )
    assert "decision_snapshot_id" in columns(path, "runs")
    assert {"lease_owner", "lease_expires_at"}.issubset(columns(path, "runs"))
    assert "error_provenance_json" in columns(path, "research_trace_events")
    assert "dsh_session_links" in tables
    assert {
        "dsh_session_id",
        "request_hash",
        "upstream_identity_json",
        "last_seq",
        "deadline_at",
    }.issubset(
        columns(path, "dsh_session_links")
    )
    assert "dsh_session_prompts" in tables
    assert {"generation", "dsh_session_id", "request_id", "request_hash", "prompt"}.issubset(
        columns(path, "dsh_session_prompts")
    )
    assert "scheduled_at" in columns(path, "observations")
    assert {"admission_origin", "priority"}.issubset(columns(path, "runs"))
    assert "research_value_evaluations" in tables
    assert {
        "run_id",
        "artifact_id",
        "evaluation_version",
        "payload_hash",
    }.issubset(columns(path, "research_value_evaluations"))


def test_0025_preserves_historical_run_and_marks_it_legacy(tmp_path: Path) -> None:
    path = tmp_path / "run-admission-priority.sqlite3"
    upgrade(path, "0024_dsh_prompt_generations")
    engine = create_engine(f"sqlite+pysqlite:///{path}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO events "
                "(event_id, event_type, occurred_at, received_at, generation, status) "
                "VALUES ('event-legacy', 'macro_event', CURRENT_TIMESTAMP, "
                "CURRENT_TIMESTAMP, 1, 'active')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO runs "
                "(run_id, event_id, status, strategy_version, runtime_version, "
                "created_at, updated_at, cost_usd) "
                "VALUES ('run-legacy', 'event-legacy', 'completed', 'research.v1', 'fixed.v1', "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 0)"
            )
        )

    upgrade(path, CURRENT_HEAD)

    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT run_id, status, strategy_version, runtime_version, "
                "admission_origin, priority "
                "FROM runs WHERE run_id = 'run-legacy'"
            )
        ).mappings().one()
    assert dict(row) == {
        "run_id": "run-legacy",
        "status": "completed",
        "strategy_version": "research.v1",
        "runtime_version": "fixed.v1",
        "admission_origin": "legacy",
        "priority": 0,
    }

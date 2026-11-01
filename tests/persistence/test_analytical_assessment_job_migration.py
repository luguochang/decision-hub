from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from packages.kernel.decision_hub_kernel.persistence.db import AnalyticalAssessmentJobRecord
from tools.ops.sqlite_typed_snapshot import snapshot

ROOT = Path(__file__).resolve().parents[2]
BEFORE = "0065_revision_feedback_evaluations"
AFTER = "0066_analytical_assessment_jobs"
TABLE = "analytical_assessment_jobs"
WHEN = "2026-10-04 06:00:00.000000"


def _config(path: Path) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{path.as_posix()}")
    config.attributes["configure_logger"] = False
    return config


def _original_state(path: Path) -> dict[str, Any]:
    state = snapshot(path)
    state["tables"].pop("alembic_version")
    state["tables"].pop(TABLE, None)
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as connection:
        connection.execute("PRAGMA query_only=ON")
        connection.text_factory = bytes
        state["schema_objects"] = connection.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master "
            "WHERE tbl_name != ? ORDER BY type,name", (TABLE,),
        ).fetchall()
    return state


def test_upgrade_downgrade_preserve_original_cells_and_never_backfill(tmp_path: Path) -> None:
    path = tmp_path / "original.sqlite3"
    config = _config(path)
    command.upgrade(config, BEFORE)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO workbench_feedback "
            "(feedback_id,request_id,target_type,target_id,created_by,verdict,notes,created_at) "
            "VALUES ('legacy','legacy/key','report_revision','historical','owner',"
            "'needs_review',?,?)", (b"\x00\xff original opinion", WHEN),
        )
        connection.execute(
            "INSERT INTO analytical_outlooks "
            "(outlook_id,run_id,artifact_id,market,instrument,method_family,method_version,"
            "horizon,action,probability_status,window_start_at,window_end_at,result_hash,"
            "evidence_refs_json,fact_version_refs_json,raw_payload_json,trigger,invalidation,"
            "cohort,eligibility,exclusion_reasons_json,research_quality,recorded_at) "
            "VALUES ('old-outlook','old-run','old-artifact','crypto','BTC-USDT','research',"
            "'old-method','30m','long','not_calibrated',?,?,?,"
            "'[]','[]','{}','original trigger','original invalidation',"
            "'historical','unknown','[]','degraded',?)", (WHEN, WHEN, "a" * 64, WHEN),
        )
        connection.execute(
            "INSERT INTO analytical_observations "
            "(outlook_id,status,attempt,next_attempt_at,error_code) "
            "VALUES ('old-outlook','retry',2,?,'original_error')", (WHEN,),
        )
    before = _original_state(path)
    command.upgrade(config, AFTER)
    assert _original_state(path) == before
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (AFTER,)
        assert connection.execute(f"SELECT count(*) FROM {TABLE}").fetchone() == (0,)
    command.upgrade(config, AFTER)
    assert _original_state(path) == before
    command.downgrade(config, BEFORE)
    assert _original_state(path) == before
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (BEFORE,)
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE tbl_name=?", (TABLE,),
        ).fetchall() == []


def test_migrated_job_constraints_fence_incomplete_leases_and_projections(tmp_path: Path) -> None:
    path = tmp_path / "constraints.sqlite3"
    command.upgrade(_config(path), AFTER)
    pending: dict[str, object] = {
        "outlook_id": "job", "status": "pending", "attempt": 0,
        "next_attempt_at": WHEN, "verifier_version": "verifier/v1",
    }
    projection = {
        "evaluated_at": WHEN, "basis_json": "{}", "basis_hash": "b" * 64,
        "assessment_json": '{"source_status":"unverifiable"}', "assessment_hash": "c" * 64,
    }
    invalid = [
        {**pending, "status": "verified"},
        {**pending, "attempt": -1},
        {**pending, "status": "running"},
        {**pending, "status": "running", "lease_owner": "worker"},
        {**pending, "status": "running", "lease_expires_at": WHEN},
        {**pending, "status": "completed"},
        *[{**pending, "status": "completed", **projection, field: None}
          for field in projection],
    ]
    with sqlite3.connect(path) as connection:
        for row in invalid:
            columns = ",".join(row)
            placeholders = ",".join("?" for _ in row)
            with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
                connection.execute(
                    f"INSERT INTO {TABLE} ({columns}) VALUES ({placeholders})", tuple(row.values()),
                )
            connection.rollback()
        for index, row in enumerate([
            pending,
            {**pending, "status": "running", "lease_owner": "worker", "lease_expires_at": WHEN},
            {**pending, "status": "retry", **projection, "error_code": "refresh_required"},
            {**pending, "status": "completed", **projection},
        ]):
            row = {**row, "outlook_id": f"job-{index}"}
            columns = ",".join(row)
            placeholders = ",".join("?" for _ in row)
            connection.execute(
                f"INSERT INTO {TABLE} ({columns}) VALUES ({placeholders})", tuple(row.values()),
            )
        assert connection.execute(f"SELECT count(*) FROM {TABLE}").fetchone() == (4,)


def test_migrated_columns_constraints_and_indexes_match_orm(tmp_path: Path) -> None:
    path = tmp_path / "metadata.sqlite3"
    command.upgrade(_config(path), AFTER)
    engine = create_engine(f"sqlite+pysqlite:///{path.as_posix()}")
    try:
        actual = inspect(engine)
        model = AnalyticalAssessmentJobRecord.__table__
        assert [(column["name"], str(column["type"]), column["nullable"])
                for column in actual.get_columns(TABLE)] == [
            (column.name, str(column.type), column.nullable) for column in model.columns
        ]
        assert actual.get_pk_constraint(TABLE)["constrained_columns"] == ["outlook_id"]
        assert {index["name"]: tuple(index["column_names"])
                for index in actual.get_indexes(TABLE)} == {
            index.name: tuple(column.name for column in index.columns) for index in model.indexes
        }
        assert {constraint["name"]: constraint["sqltext"]
                for constraint in actual.get_check_constraints(TABLE)} == {
            constraint.name: str(constraint.sqltext) for constraint in model.constraints
            if hasattr(constraint, "sqltext")
        }
    finally:
        engine.dispose()

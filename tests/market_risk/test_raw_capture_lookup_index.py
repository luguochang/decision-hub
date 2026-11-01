"""Actual isolated Alembic 0080→0081 and rollback; no model/provider or formal DB."""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Column, MetaData, String, Table, create_engine, inspect, text

from packages.kernel.decision_hub_kernel.persistence.db import OutboxRecord, RawCaptureRecord

INDEX = "ix_raw_capture_body_identity"
BASE = "0080_risk_research_requests"
HEAD = "0081_raw_capture_lookup_index"


def test_incremental_index_search_nonunique_data_preservation_and_rollback(tmp_path):
    database_path = tmp_path / "index.sqlite3"
    url = f"sqlite+pysqlite:///{database_path}"
    engine = create_engine(url)
    metadata = MetaData()
    captures = RawCaptureRecord.__table__.to_metadata(metadata)
    captures.indexes = {index for index in captures.indexes if index.name != INDEX}
    outbox = OutboxRecord.__table__.to_metadata(metadata)
    version = Table(
        "alembic_version", metadata, Column("version_num", String(128), primary_key=True)
    )
    metadata.create_all(engine)
    stamp = datetime(2026, 10, 8, tzinfo=UTC)
    with engine.begin() as connection:
        connection.execute(version.insert().values(version_num=BASE))
        connection.execute(
            captures.insert(),
            [
                dict(
                    capture_id="legacy-" + str(i),
                    context_id="context-" + str(i),
                    url="https://www.okx.com/api/v5/market/history-candles",
                    status_code=200,
                    body_hash="a" * 64,
                    body_ref="raw://sha256/" + "a" * 64,
                    byte_count=32,
                    media_type="application/json",
                    adapter_version="legacy.v1",
                    error_code=None,
                    started_at=stamp,
                    completed_at=stamp,
                    recorded_at=stamp,
                )
                for i in range(2)
            ],
        )
        connection.execute(
            outbox.insert().values(
                artifact_id="old-report",
                subject_kind="report",
                subject_id="old-report",
                channel="local",
                dedupe_key="old-report-local",
                attempts=1,
                sent_at=stamp,
                dispatch_status="sent",
                claim_generation=0,
                attempt_history_json="[]",
            )
        )

    def originals():
        with engine.connect() as connection:
            return (
                connection.execute(text("SELECT * FROM raw_captures ORDER BY capture_id")).all(),
                connection.execute(text("SELECT * FROM outbox ORDER BY id")).all(),
            )

    def plan():
        # DDL is applied by Alembic's separate connection. A new connection
        # avoids SQLite's cached EXPLAIN statement retaining its old plan.
        with sqlite3.connect(database_path) as connection:
            return " ".join(
                row[3]
                for row in connection.execute(
                    "EXPLAIN QUERY PLAN SELECT * FROM raw_captures "
                    "WHERE body_ref=? AND body_hash=?",
                    ("raw://sha256/" + "a" * 64, "a" * 64),
                )
            )

    before = originals()
    assert "SCAN raw_captures" in plan()
    config = Config()
    config.set_main_option(
        "script_location", str(Path(__file__).resolve().parents[2] / "migrations")
    )
    config.set_main_option("sqlalchemy.url", url)
    config.attributes["configure_logger"] = False
    command.upgrade(config, HEAD)
    assert "SEARCH raw_captures USING INDEX " + INDEX in plan()
    indexes = {index["name"]: index for index in inspect(engine).get_indexes("raw_captures")}
    assert indexes[INDEX]["column_names"] == ["body_ref", "body_hash"]
    assert not indexes[INDEX]["unique"] and "ix_raw_captures_context_id" in indexes
    assert originals() == before
    assert (
        next(index for index in RawCaptureRecord.__table__.indexes if index.name == INDEX).unique
        is False
    )
    command.downgrade(config, BASE)
    assert "SCAN raw_captures" in plan() and originals() == before
    with engine.connect() as connection:
        assert (
            connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == BASE
        )
    engine.dispose()

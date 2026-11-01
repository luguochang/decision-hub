from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

ROOT = Path(__file__).resolve().parents[2]


def _config(path: Path) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{path}")
    return config


def test_receipt_timeline_upgrade_preserves_legacy_receipts(tmp_path: Path) -> None:
    path = tmp_path / "source-acquisition-upgrade.sqlite3"
    config = _config(path)
    command.upgrade(config, "0042_source_poll_attempts")
    engine = create_engine(f"sqlite+pysqlite:///{path}")
    legacy_time = datetime(2026, 9, 22, tzinfo=UTC)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO source_acquisition_receipts "
                "(receipt_id, source_id, url, access_mode, status, observed_at, "
                "status_code, artifacts_json, error_code, receipt_hash, created_at) "
                "VALUES (:id, :source, :url, :mode, :status, :observed, "
                ":code, :artifacts, :error, :hash, :created)"
            ),
            {
                "id": "legacy-receipt",
                "source": "official-calendar",
                "url": "https://example.test/calendar",
                "mode": "playwright",
                "status": "succeeded",
                "observed": legacy_time,
                "code": 200,
                "artifacts": "[]",
                "error": None,
                "hash": "a" * 64,
                "created": legacy_time,
            },
        )

    command.upgrade(config, "0043_source_acquisition_receipt_timeline")

    columns = {
        column["name"]: column
        for column in inspect(engine).get_columns("source_acquisition_receipts")
    }
    assert columns["observed_at"]["nullable"] is True
    assert {"request_started_at", "request_completed_at", "received_at"} <= columns.keys()
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT observed_at, request_started_at, request_completed_at, "
                "received_at, created_at FROM source_acquisition_receipts "
                "WHERE receipt_id = 'legacy-receipt'"
            )
        ).one()
    assert row.observed_at is not None
    assert row.request_started_at is None
    assert row.request_completed_at is None
    assert row.received_at == row.created_at

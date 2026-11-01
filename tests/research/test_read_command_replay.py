"""Real SQLite control for old read-command ACK versus current unread state.

The fixture contains controlled Study/Revision rows. It exercises the actual
service and transactions, without Commit, Worker, Provider or network execution.
"""

import hashlib
import json
import socket
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from packages.contracts_py.decision_hub_contracts.generated.product_delivery import (
    ReadReceiptCommand,
)
from packages.kernel.decision_hub_kernel.application.product_delivery import ProductDeliveryService
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    ReportRevisionRecord,
    StudyRecord,
)

STAMP = datetime(2026, 10, 3, 4, tzinfo=UTC)


def _cell(value):
    if value is None:
        return ["null", None]
    if isinstance(value, bytes):
        return ["blob", value.hex()]
    if isinstance(value, int):
        return ["integer", value]
    if isinstance(value, float):
        return ["real", repr(value)]
    return ["text", value]


def _all_physical_rows(path: Path):
    tables = {}
    with sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True) as connection:
        connection.execute("PRAGMA query_only=ON")
        names = connection.execute(
            "SELECT name FROM sqlite_schema WHERE type='table' ORDER BY name"
        ).fetchall()
        for (name,) in names:
            quoted = name.replace('"', '""')
            cursor = connection.execute(f'SELECT * FROM "{quoted}"')
            columns = [field[0] for field in cursor.description]
            rows = [[_cell(value) for value in row] for row in cursor.fetchall()]
            rows.sort(key=lambda row: json.dumps(row, ensure_ascii=False, separators=(",", ":")))
            tables[name] = {"columns": columns, "rows": rows}
    canonical = json.dumps(tables, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return tables, hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def test_old_read_replay_keeps_later_unread_state_and_all_physical_rows(tmp_path, monkeypatch):
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "isolated-data"))
    monkeypatch.setenv("LLM_ENABLED", "0")
    monkeypatch.setenv("DOCUMENT_CORRECTION_LLM_ENABLED", "0")

    def no_network(*_args, **_kwargs):
        pytest.fail("network is forbidden in the read-command SQLite control")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    database_path = tmp_path / "read-replay.sqlite3"
    db = Database(f"sqlite+pysqlite:///{database_path}")
    db.create_all()
    try:
        with db.session() as session:
            session.add(StudyRecord(
                study_id="study-read-1", scope_key="a" * 64,
                title="Read replay control", market="crypto", instrument="BTC-USDT-SWAP",
                horizon_scope="30m,24h,72h", question_key="read-replay",
                identity_basis="fixture", status="active", created_at=STAMP, updated_at=STAMP,
            ))
            session.add(ReportRevisionRecord(
                revision_id="revision-read-1", study_id="study-read-1",
                source_run_id="run-read-1", artifact_id="artifact-read-1",
                previous_revision_id=None, strategy_version="research.v1", input_fingerprint=None,
                publication_status="research_only", change_json="{}", identity_basis="fixture",
                created_at=STAMP, recorded_at=STAMP,
            ))
        calls = []

        def clock():
            value = STAMP + timedelta(minutes=len(calls) + 1)
            calls.append(value)
            return value

        service = ProductDeliveryService(db, clock=clock)
        original = ReadReceiptCommand(request_id="read-original-key", read=True)
        first = service.mark_read("revision-read-1", "owner", original)
        undo = service.mark_read(
            "revision-read-1", "owner", ReadReceiptCommand(request_id="read-undo-key", read=False)
        )
        assert first.read_at == STAMP + timedelta(minutes=1)
        assert undo.read_at is None
        before, before_hash = _all_physical_rows(database_path)

        replay = service.mark_read("revision-read-1", "owner", original)
        assert replay == first
        assert len(calls) == 2
        current = service.list_reports("owner").items[0]
        assert current.read_at is None
        assert service.list_reports("owner", unread_only=True).total == 1
        after, after_hash = _all_physical_rows(database_path)
        assert after == before
        assert after_hash == before_hash
        assert len(after["product_commands"]["rows"]) == 2
        assert len(after["product_read_receipts"]["rows"]) == 1
        print("read_command_replay_observation=" + json.dumps({
            "database_path": str(database_path), "table_count": len(after),
            "before_all_rows_sha256": before_hash, "after_all_rows_sha256": after_hash,
            "original_receipt": first.model_dump(mode="json"),
            "later_unread_receipt": undo.model_dump(mode="json"),
            "replayed_receipt": replay.model_dump(mode="json"),
            "current_read_at": current.read_at, "clock_calls": len(calls),
            "command_count": len(after["product_commands"]["rows"]),
        }, ensure_ascii=False))
    finally:
        db.engine.dispose()

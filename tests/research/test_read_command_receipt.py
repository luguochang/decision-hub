"""Original read-command lookup through real SQLite and the delivery router."""

import hashlib
import json
import socket
import sqlite3
import sys
from collections.abc import Generator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from apps.hub_api.delivery_routes import delivery_router
from packages.contracts_py.decision_hub_contracts.generated.product_delivery import (
    ReadReceiptCommand,
)
from packages.kernel.decision_hub_kernel.application.product_delivery import (
    ProductDeliveryService,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    ProductCommandRecord,
    ProductReadReceiptRecord,
    ReportRevisionRecord,
    StudyRecord,
)

STAMP = datetime(2026, 10, 3, 4, tzinfo=UTC)
REVISION = "revision-command-1"
OWNER = "owner-command-1"
READ_ID = "original-read-command"
UNDO_ID = "later-undo-command"


@dataclass(frozen=True)
class ReadInputs:
    database: Database
    path: Path
    service: ProductDeliveryService
    clock_calls: list[datetime]


def _cell(value: Any) -> list[Any]:
    if value is None:
        return ["null", None]
    if isinstance(value, bytes):
        return ["blob", value.hex()]
    if isinstance(value, int):
        return ["integer", value]
    if isinstance(value, float):
        return ["real", repr(value)]
    return ["text", value]


def _all_rows(path: Path) -> dict[str, Any]:
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
    return tables


def _observe(tmp_path: Path, before: dict[str, Any], after: dict[str, Any], **facts: Any):
    def digest(rows: dict[str, Any]) -> str:
        text = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    observation = {
        "before": before,
        "after": after,
        "before_all_rows_sha256": digest(before),
        "after_all_rows_sha256": digest(after),
        "table_count": len(before),
        **facts,
    }
    (tmp_path / "read-command-receipt-observation.json").write_text(
        json.dumps(observation, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8"
    )
    assert after == before


@pytest.fixture
def inputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[ReadInputs]:
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "isolated-data"))
    monkeypatch.setenv("LLM_ENABLED", "0")
    monkeypatch.setenv("DOCUMENT_CORRECTION_LLM_ENABLED", "0")

    def no_network(*_args: Any, **_kwargs: Any):
        pytest.fail("network is forbidden in the read-command lookup tests")

    original_connect = socket.socket.connect
    fallback = getattr(socket, "_fallback_socketpair", None)
    fallback_code = getattr(fallback, "__code__", None)

    def guarded_connect(connection: socket.socket, address: Any):
        caller = sys._getframe(1)
        listener = caller.f_locals.get("lsock")
        # Windows Proactor needs the stdlib's authenticated self-pipe. Permit
        # only that exact function's own sockets and bound numeric loopback.
        if (
            fallback_code is not None
            and caller.f_code is fallback_code
            and caller.f_locals.get("csock") is connection
            and isinstance(listener, socket.socket)
            and isinstance(address, tuple)
            and len(address) == 2
            and address[0] in {"127.0.0.1", "::1"}
            and isinstance(address[1], int)
            and 0 < address[1] <= 65535
            and connection.family in {socket.AF_INET, socket.AF_INET6}
            and connection.type == socket.SOCK_STREAM
            and listener.getsockname()[:2] == address
        ):
            return original_connect(connection, address)
        no_network()

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket, "create_connection", no_network)
    path = tmp_path / "read-command.sqlite3"
    database = Database(f"sqlite+pysqlite:///{path}")
    database.create_all()
    clock_calls: list[datetime] = []

    def clock() -> datetime:
        value = STAMP + timedelta(minutes=len(clock_calls) + 1)
        clock_calls.append(value)
        return value

    try:
        with database.session() as session:
            session.add(
                StudyRecord(
                    study_id="study-command-1",
                    scope_key="a" * 64,
                    title="Original read-command control",
                    market="crypto",
                    instrument="BTC-USDT-SWAP",
                    horizon_scope="30m,24h,72h",
                    question_key="read-command-control",
                    identity_basis="fixture",
                    status="active",
                    created_at=STAMP,
                    updated_at=STAMP,
                )
            )
            session.add(
                ReportRevisionRecord(
                    revision_id=REVISION,
                    study_id="study-command-1",
                    source_run_id="run-command-1",
                    artifact_id="artifact-command-1",
                    previous_revision_id=None,
                    strategy_version="research.v1",
                    input_fingerprint=None,
                    publication_status="research_only",
                    change_json="{}",
                    identity_basis="fixture",
                    created_at=STAMP,
                    recorded_at=STAMP,
                )
            )
        yield ReadInputs(database, path, ProductDeliveryService(database, clock), clock_calls)
    finally:
        database.engine.dispose()


def _client(inputs: ReadInputs) -> TestClient:
    def require_owner(value: str | None) -> str:
        if not value:
            raise HTTPException(403, "owner_identity_required")
        return value

    app = FastAPI()
    app.include_router(delivery_router(inputs.database, require_owner))
    return TestClient(app)


def _url(request_id: str = READ_ID, revision_id: str = REVISION) -> str:
    return f"/v1/product/reports/{revision_id}/read/commands/{request_id}"


def _read(inputs: ReadInputs):
    return inputs.service.mark_read(
        REVISION, OWNER, ReadReceiptCommand(request_id=READ_ID, read=True)
    )


def test_original_ack_after_undo_is_original_and_get_preserves_all_rows(
    inputs: ReadInputs, tmp_path: Path
):
    first = _read(inputs)
    undo = inputs.service.mark_read(
        REVISION, OWNER, ReadReceiptCommand(request_id=UNDO_ID, read=False)
    )
    before = _all_rows(inputs.path)
    restored = inputs.service.get_read_command_receipt(
        REVISION, OWNER, ReadReceiptCommand(request_id=READ_ID, read=True)
    )
    assert restored is not None
    assert restored.model_dump(mode="json") == {
        "schema_version": "read-command-receipt.v1",
        "request_id": READ_ID,
        "revision_id": REVISION,
        "owner_id": OWNER,
        "command": {"request_id": READ_ID, "read": True},
        "result": first.model_dump(mode="json"),
        "payload_hash": inputs.service._fingerprint(  # pyright: ignore[reportPrivateUsage]
            OWNER, REVISION, "read_receipt", ReadReceiptCommand(request_id=READ_ID, read=True)
        ),
        "confirmation_basis": "stored_command_hash",
    }
    assert restored.result.read_at is not None
    with _client(inputs) as client:
        original_response = client.get(
            _url(), params={"read": "true"}, headers={"X-Owner-Id": OWNER}
        )
        undo_response = client.get(
            _url(UNDO_ID), params={"read": "false"}, headers={"X-Owner-Id": OWNER}
        )
    assert original_response.status_code == undo_response.status_code == 200
    assert original_response.json() == restored.model_dump(mode="json")
    assert undo_response.json()["result"] == undo.model_dump(mode="json")
    assert undo_response.json()["command"] == {"request_id": UNDO_ID, "read": False}
    assert inputs.service.list_reports(OWNER).items[0].read_at is None
    assert len(inputs.clock_calls) == 2
    _observe(
        tmp_path,
        before,
        _all_rows(inputs.path),
        original=first.model_dump(mode="json"),
        undo=undo.model_dump(mode="json"),
        looked_up=original_response.json(),
        current_read_at=None,
        clock_calls=len(inputs.clock_calls),
    )


def test_missing_command_is_200_null_without_revision_or_receipt_write(
    inputs: ReadInputs, tmp_path: Path
):
    before = _all_rows(inputs.path)
    assert inputs.service.get_read_command_receipt(
        "missing-revision", OWNER, ReadReceiptCommand(request_id="missing-command", read=True)
    ) is None
    with _client(inputs) as client:
        response = client.get(
            _url("missing-command", "missing-revision"),
            params={"read": "true"},
            headers={"X-Owner-Id": OWNER},
        )
    assert response.status_code == 200 and response.json() is None
    assert inputs.clock_calls == []
    _observe(tmp_path, before, _all_rows(inputs.path), response=response.json())


def test_lookup_does_not_require_todays_revision_or_read_receipt(
    inputs: ReadInputs, tmp_path: Path
):
    original = _read(inputs)
    with inputs.database.session() as session:
        revision = session.get(ReportRevisionRecord, REVISION)
        current = session.get(ProductReadReceiptRecord, (REVISION, OWNER))
        assert revision is not None and current is not None
        session.delete(revision)
        session.delete(current)
    before = _all_rows(inputs.path)
    with _client(inputs) as client:
        response = client.get(_url(), params={"read": "true"}, headers={"X-Owner-Id": OWNER})
    assert response.status_code == 200
    assert response.json()["result"] == original.model_dump(mode="json")
    assert len(inputs.clock_calls) == 1
    _observe(tmp_path, before, _all_rows(inputs.path), response=response.json())


@pytest.mark.parametrize("wrong", ["owner", "revision", "intent"])
def test_lookup_rejects_different_frozen_intent_without_writes(
    inputs: ReadInputs, tmp_path: Path, wrong: str
):
    _read(inputs)
    owner = "foreign-owner" if wrong == "owner" else OWNER
    revision = "foreign-revision" if wrong == "revision" else REVISION
    command = ReadReceiptCommand(request_id=READ_ID, read=wrong != "intent")
    before = _all_rows(inputs.path)
    with pytest.raises(ValueError, match="^product_request_reused$"):
        inputs.service.get_read_command_receipt(revision, owner, command)
    with _client(inputs) as client:
        response = client.get(
            _url(revision_id=revision),
            params={"read": str(command.read).lower()},
            headers={"X-Owner-Id": owner},
        )
    assert response.status_code == 409 and response.json()["detail"] == "product_request_reused"
    _observe(tmp_path, before, _all_rows(inputs.path), wrong=wrong, response=response.json())


@pytest.mark.parametrize("field", ["owner_id", "target_id", "kind", "payload_hash", "request_id"])
def test_corrupt_command_identity_or_hash_is_denied_and_not_repaired(
    inputs: ReadInputs, tmp_path: Path, field: str
):
    _read(inputs)
    changed_id = "moved-original-command" if field == "request_id" else READ_ID
    with inputs.database.session() as session:
        stored = session.get(ProductCommandRecord, READ_ID)
        assert stored is not None
        values = {
            "owner_id": "foreign-owner",
            "target_id": "foreign-revision",
            "kind": "study_tracking",
            "payload_hash": "f" * 64,
            "request_id": changed_id,
        }
        setattr(stored, field, values[field])
    before = _all_rows(inputs.path)
    command = ReadReceiptCommand(request_id=changed_id, read=True)
    with pytest.raises(ValueError, match="^product_request_reused$"):
        inputs.service.get_read_command_receipt(REVISION, OWNER, command)
    with _client(inputs) as client:
        response = client.get(
            _url(changed_id), params={"read": "true"}, headers={"X-Owner-Id": OWNER}
        )
        if field == "request_id":
            assert client.get(
                _url(), params={"read": "true"}, headers={"X-Owner-Id": OWNER}
            ).json() is None
    assert response.status_code == 409 and response.json()["detail"] == "product_request_reused"
    _observe(tmp_path, before, _all_rows(inputs.path), field=field, response=response.json())


@pytest.mark.parametrize(
    "damage",
    [
        "json", "owner", "revision", "read_state", "updated_at",
        "read_at_number", "updated_at_number", "extra_field",
    ],
)
def test_corrupt_original_result_is_denied_without_current_state_fallback(
    inputs: ReadInputs, tmp_path: Path, damage: str
):
    _read(inputs)
    with inputs.database.session() as session:
        stored = session.get(ProductCommandRecord, READ_ID)
        assert stored is not None
        result = json.loads(stored.result_json)
        if damage == "json":
            stored.result_json = "not-json"
        elif damage in {"read_at_number", "updated_at_number"}:
            result[damage.removesuffix("_number")] = 1760000000
            stored.result_json = json.dumps(result)
        elif damage == "extra_field":
            result["foreign_field"] = "not-in-the-original-receipt"
            stored.result_json = json.dumps(result)
        else:
            field = {
                "owner": "owner_id", "revision": "revision_id",
                "read_state": "read_at", "updated_at": "updated_at",
            }[damage]
            result[field] = None if damage in {"read_state", "updated_at"} else "foreign"
            stored.result_json = json.dumps(result)
    before = _all_rows(inputs.path)
    with pytest.raises(ValueError, match="^product_read_command_receipt_invalid$"):
        inputs.service.get_read_command_receipt(
            REVISION, OWNER, ReadReceiptCommand(request_id=READ_ID, read=True)
        )
    with _client(inputs) as client:
        response = client.get(_url(), params={"read": "true"}, headers={"X-Owner-Id": OWNER})
    assert response.status_code == 409
    assert response.json()["detail"] == "product_read_command_receipt_invalid"
    _observe(tmp_path, before, _all_rows(inputs.path), damage=damage, response=response.json())


def test_route_requires_owner_and_frozen_boolean_without_writes(inputs: ReadInputs, tmp_path: Path):
    _read(inputs)
    before = _all_rows(inputs.path)
    with _client(inputs) as client:
        missing_owner = client.get(_url(), params={"read": "true"})
        missing_read = client.get(_url(), headers={"X-Owner-Id": OWNER})
        malformed_read = client.get(
            _url(), params={"read": "not-a-bool"}, headers={"X-Owner-Id": OWNER}
        )
    assert missing_owner.status_code == 403
    assert missing_read.status_code == malformed_read.status_code == 422
    assert len(inputs.clock_calls) == 1
    _observe(
        tmp_path, before, _all_rows(inputs.path),
        statuses=[missing_owner.status_code, missing_read.status_code, malformed_read.status_code],
    )

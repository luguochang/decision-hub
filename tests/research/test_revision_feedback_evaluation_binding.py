from __future__ import annotations

import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from test_revision_feedback_evaluation import (
    FEEDBACK_AT,
    Scenario,
    _canonical,  # pyright: ignore[reportPrivateUsage] -- exact original isolated fixture
    _client,  # pyright: ignore[reportPrivateUsage] -- no lifespan/services
    _observe,  # pyright: ignore[reportPrivateUsage] -- fresh write-once observations
    _post,  # pyright: ignore[reportPrivateUsage] -- real POST route
    _rows,  # pyright: ignore[reportPrivateUsage] -- full physical SQLite rows
    _scenario,  # pyright: ignore[reportPrivateUsage] -- original Commit/SQLite source
)
from test_revision_feedback_evaluation import (
    offline as offline,  # Re-export unchanged original fixture for module usefixtures.
)

from packages.contracts_py.decision_hub_contracts import (
    RevisionFeedbackEvaluation,
    RevisionFeedbackEvaluationBasis,
)
from packages.kernel.decision_hub_kernel.persistence.db import Database

pytestmark = pytest.mark.usefixtures("offline")


def _get(client: TestClient, scenario: Scenario, request_id: str,
         owner: str = "owner-a", suffix: str = "/requests") -> dict[str, Any]:
    response = client.get(
        f"/v1/product/reports/{scenario.revision_id}/feedback-evaluation{suffix}",
        params={"request_id": request_id}, headers={"X-Owner-Id": owner},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _latest(client: TestClient, scenario: Scenario,
            owner: str = "owner-a") -> dict[str, Any] | None:
    response = client.get(
        f"/v1/product/reports/{scenario.revision_id}/feedback-evaluation",
        headers={"X-Owner-Id": owner},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_binding_exact_owner_basis_and_equal_time_insertion_order(
    tmp_path: Path,
) -> None:
    scenario = _scenario(tmp_path / "owner-basis")
    try:
        client = _client(scenario)
        assert _latest(client, scenario) is None
        first_key, second_key = "opinion/first", "opinion/second"
        first_feedback = _post(client, scenario, request_id=first_key)
        first = _get(client, scenario, first_key)
        # Validate the real serialized route DTO, not an invented dict mirror.
        RevisionFeedbackEvaluation.model_validate_json(_canonical(first))
        assert first["status"] == "available"
        assert first["feedback"] == first_feedback
        assert first["value"]["usefulness"] == "not_useful"
        basis = _get(client, scenario, first_key, suffix="/basis")
        RevisionFeedbackEvaluationBasis.model_validate_json(_canonical(basis))
        assert (
            hashlib.sha256(basis["basis_json"].encode("utf-8")).hexdigest()
            == basis["basis_hash"]
        )
        raw = json.loads(basis["basis_json"])
        assert len(raw["tables"]) == 18
        assert {item["source_table"] for item in raw["tables"]} >= {
            "studies", "research_run_scopes", "outcomes", "workbench_feedback",
        }
        for table in raw["tables"]:
            assert table["columns"] == [item["name"] for item in table["column_metadata"]]
            assert all(len(cells) == len(table["columns"]) for cells in table["rows"])
        _post(client, scenario, request_id=second_key, verdict="useful", notes="Second opinion.")
        second = _get(client, scenario, second_key)
        assert first["binding"]["recorded_at"] == second["binding"]["recorded_at"]
        assert second["binding"]["recorded_order"] > first["binding"]["recorded_order"]
        assert _latest(client, scenario) == second
        before_replay = _rows(scenario.path)
        assert _post(client, scenario, request_id=first_key) == first_feedback
        assert _get(client, scenario, first_key) == first
        assert _get(client, scenario, first_key, suffix="/basis") == basis
        assert _latest(client, scenario) == second
        assert _rows(scenario.path) == before_replay
        foreign = client.get(
            f"/v1/product/reports/{scenario.revision_id}/feedback-evaluation/requests",
            params={"request_id": first_key}, headers={"X-Owner-Id": "owner-b"},
        )
        assert foreign.status_code == 409
        assert foreign.json()["detail"] == "revision_feedback_evaluation_request_conflict"
        assert _latest(client, scenario, "owner-b") is None
        wrong_revision = client.get(
            "/v1/product/reports/wrong-revision/feedback-evaluation/requests",
            params={"request_id": first_key}, headers={"X-Owner-Id": "owner-a"},
        )
        assert wrong_revision.status_code == 409
        assert _rows(scenario.path) == before_replay
        _observe("binding-owner-order", first=first, second=second, basis=basis,
                 physical_rows=before_replay)
    finally:
        scenario.database.engine.dispose()


@pytest.mark.parametrize("damage", ["hash", "missing", "blob-schema", "blob-pk"])
def test_unavailable_source_retains_opinion_full_raw_and_owner_latest(
    tmp_path: Path, damage: str,
) -> None:
    scenario = _scenario(tmp_path / damage)
    try:
        client = _client(scenario)
        _post(client, scenario, request_id="previous", verdict="useful", notes="Prior opinion.")
        previous = _get(client, scenario, "previous")
        with sqlite3.connect(scenario.path) as connection:
            if damage == "hash":
                connection.execute("UPDATE research_results SET payload_hash=? WHERE run_id=?",
                                   ("0" * 64, scenario.run_id))
            elif damage == "missing":
                connection.execute(
                    "DELETE FROM research_results WHERE run_id=?", (scenario.run_id,)
                )
            elif damage == "blob-schema":
                connection.execute("UPDATE research_results SET schema_version=? WHERE run_id=?",
                                   (b"invalid-schema", scenario.run_id))
            else:
                body = _canonical({"run_id": scenario.run_id})
                connection.execute(
                    "INSERT INTO run_role_outputs "
                    "(output_id,run_id,role,schema_version,payload_json,payload_hash,created_at) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (b"invalid-pk", scenario.run_id, "research_task_request", b"invalid-schema",
                     body, hashlib.sha256(body.encode()).hexdigest(), FEEDBACK_AT.isoformat()),
                )
        # _rows uses JSON sorting; BLOB rows need the explicit typed whole-row
        # capture below instead of coercing their physical scalar to a string.
        before = _physical_rows(scenario.path)
        _post(client, scenario, request_id="unavailable", notes="Keep this opinion.")
        actual = _get(client, scenario, "unavailable")
        assert actual["status"] == "unavailable" and actual["value"] is None
        assert actual["reason"] == {
            "hash": "source_hash_mismatch", "missing": "source_missing",
            "blob-schema": "source_invalid", "blob-pk": "source_invalid",
        }[damage]
        assert actual["feedback"]["notes"] == "Keep this opinion."
        assert _latest(client, scenario) == actual
        basis = _get(client, scenario, "unavailable", suffix="/basis")
        tables = {item["source_table"]: item for item in json.loads(basis["basis_json"])["tables"]}
        if damage == "blob-schema":
            result_witness = next(item for item in actual["binding"]["source_witnesses"]
                                  if item["source_table"] == "research_results")
            assert result_witness["source_schema"] is None
            assert any(cell["type"] == "blob" for cell in tables["research_results"]["rows"][0])
        if damage == "blob-pk":
            assert not any(item["source_table"] == "run_role_outputs"
                           for item in actual["binding"]["source_witnesses"])
            assert any(cell["type"] == "blob" for cell in tables["run_role_outputs"]["rows"][0])
        after = _physical_rows(scenario.path)
        for name in before.keys() - {"workbench_feedback", "revision_feedback_evaluations"}:
            assert after[name] == before[name]
        assert len(after["research_value_evaluations"]) == 2  # original and prior valid opinion
        assert after["research_value_evaluations"] == before["research_value_evaluations"]
        read_only = _physical_rows(scenario.path)
        assert _get(client, scenario, "unavailable") == actual
        assert _physical_rows(scenario.path) == read_only
        _observe("unavailable-" + damage, previous=previous, actual=actual, basis=basis,
                 before=before, after=after)
    finally:
        scenario.database.engine.dispose()


def _physical_rows(path: Path) -> dict[str, list[list[dict[str, str]]]]:
    def cell(value: object) -> dict[str, str]:
        if value is None:
            return {"type": "null"}
        if isinstance(value, bytes):
            return {"type": "blob", "value": value.hex()}
        if type(value) is float:
            return {"type": "real", "value": value.hex()}
        return {"type": type(value).__name__, "value": str(value)}

    with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as connection:
        connection.execute("PRAGMA query_only=ON")
        tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        return {name: sorted([[cell(value) for value in row] for row in connection.execute(
            'SELECT * FROM "' + name.replace('"', '""') + '"'
        )], key=_canonical) for (name,) in tables}


@pytest.mark.parametrize("fault", ["value-flush", "binding-flush", "sql-fault"])
def test_actual_flushed_additions_roll_back_on_programming_or_sql_fault(
    tmp_path: Path, fault: str,
) -> None:
    scenario = _scenario(tmp_path / fault)
    phases: list[dict[str, object]] = []
    try:
        client = _client(scenario)
        before = _rows(scenario.path)

        def fail_after_flush(session: Session, _context: object) -> None:
            if session.bind is not scenario.database.engine:
                return
            connection = session.connection()
            values = connection.exec_driver_sql(
                "SELECT count(*) FROM research_value_evaluations"
            ).scalar_one()
            bindings = connection.exec_driver_sql(
                "SELECT count(*) FROM revision_feedback_evaluations"
            ).scalar_one()
            if values <= 1 or (fault == "binding-flush" and bindings == 0):
                return
            native = connection.connection.driver_connection
            assert isinstance(native, sqlite3.Connection) and native.in_transaction
            with sqlite3.connect(scenario.path, timeout=0.05) as second:
                assert second.execute("SELECT count(*) FROM workbench_feedback").fetchone()[0] == 0
                with pytest.raises(sqlite3.OperationalError) as locked:
                    second.execute("BEGIN IMMEDIATE")
                assert locked.value.sqlite_errorcode & 0xff == sqlite3.SQLITE_BUSY
            phases.append({"values_in_writer": values, "bindings_in_writer": bindings,
                           "physical_writer": True, "independent_connection_feedback": 0})
            if fault == "sql-fault":
                connection.exec_driver_sql("INSERT INTO nonexistent_feedback_fault VALUES (1)")
            raise RuntimeError("actual_after_flush_fault")

        event.listen(Session, "after_flush", fail_after_flush)
        try:
            expected = OperationalError if fault == "sql-fault" else RuntimeError
            with pytest.raises(expected):
                _post(client, scenario, request_id="rollback")
        finally:
            event.remove(Session, "after_flush", fail_after_flush)
        assert phases and phases[0]["physical_writer"] is True
        after = _rows(scenario.path)
        assert after == before
        _observe(fault, before=before, after=after, actual_flush_phases=phases)
    finally:
        scenario.database.engine.dispose()


def test_actual_extra_sqlite_columns_and_values_are_frozen_not_ignored(
    tmp_path: Path,
) -> None:
    first = _scenario(tmp_path / "extra-one")
    second = _scenario(tmp_path / "extra-two")
    try:
        # The same actual source rows/clock/feedback, differing only in an
        # unmapped physical column, must not collide in the basis identity.
        with sqlite3.connect(first.path) as connection:
            connection.execute("ALTER TABLE outcomes ADD COLUMN provider_raw_extra TEXT")
            connection.execute("ALTER TABLE research_run_scopes ADD COLUMN empty_extra TEXT")
            connection.execute("UPDATE outcomes SET provider_raw_extra='original-extra'")
        # Clone exact original DB with SQLite's real backup API; no new product
        # source generation or time/owner difference can explain the hash.
        second.database.engine.dispose()
        with sqlite3.connect(first.path) as source, sqlite3.connect(second.path) as destination:
            source.backup(destination)
        second = Scenario(Database(f"sqlite+pysqlite:///{second.path}"), second.path,
                          first.run_id, first.revision_id, first.original_value)
        with sqlite3.connect(second.path) as connection:
            connection.execute("UPDATE outcomes SET provider_raw_extra='different-extra'")
        one_client, two_client = _client(first), _client(second)
        _post(one_client, first, request_id="same-extra-feedback")
        _post(two_client, second, request_id="same-extra-feedback")
        one = _get(one_client, first, "same-extra-feedback")
        two = _get(two_client, second, "same-extra-feedback")
        assert one["status"] == two["status"] == "available"
        assert one["binding"]["basis_hash"] != two["binding"]["basis_hash"]
        assert one["value"]["evaluation_version"] != two["value"]["evaluation_version"]
        basis = _get(one_client, first, "same-extra-feedback", suffix="/basis")
        tables = {item["source_table"]: item for item in json.loads(basis["basis_json"])["tables"]}
        assert "provider_raw_extra" in tables["outcomes"]["columns"]
        assert "empty_extra" in tables["research_run_scopes"]["columns"]
        assert tables["research_run_scopes"]["rows"] == []
        with sqlite3.connect(first.path) as connection:
            connection.execute("UPDATE outcomes SET provider_raw_extra='later-extra'")
        before = _rows(first.path)
        assert _get(one_client, first, "same-extra-feedback", suffix="/basis") == basis
        assert _get(one_client, first, "same-extra-feedback") == one
        assert _rows(first.path) == before  # no current Outcome recompute
        _observe("physical-extra-column", one=one, two=two, basis=basis, after=before)
    finally:
        first.database.engine.dispose()
        second.database.engine.dispose()


def test_legacy_same_key_never_backfills_and_conflicting_owner_is_not_disclosed(
    tmp_path: Path,
) -> None:
    scenario = _scenario(tmp_path / "legacy")
    try:
        with sqlite3.connect(scenario.path) as connection:
            connection.execute(
                "INSERT INTO workbench_feedback "
                "(feedback_id,request_id,target_type,target_id,created_by,"
                "verdict,notes,created_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                ("legacy-feedback", "legacy/key", "report_revision", scenario.revision_id,
                 "owner-a", "not_useful", "Missing event context.", FEEDBACK_AT.isoformat()),
            )
        before = _rows(scenario.path)
        client = _client(scenario)
        original = _post(client, scenario, request_id="legacy/key")
        legacy = _get(client, scenario, "legacy/key")
        assert legacy["feedback"] == original
        assert legacy["reason"] == "legacy_feedback_not_bound"
        assert legacy["binding"] is None and legacy["value"] is None
        assert _latest(client, scenario) == legacy
        basis = client.get(
            f"/v1/product/reports/{scenario.revision_id}/feedback-evaluation/basis",
            params={"request_id": "legacy/key"}, headers={"X-Owner-Id": "owner-a"},
        )
        assert basis.status_code == 404
        assert basis.json()["detail"] == "revision_feedback_evaluation_not_recorded"
        missing = client.get(
            f"/v1/product/reports/{scenario.revision_id}/feedback-evaluation/requests",
            params={"request_id": "missing"}, headers={"X-Owner-Id": "owner-a"},
        )
        assert missing.status_code == 404
        assert _rows(scenario.path) == before
        _observe("legacy-no-backfill", legacy=legacy, before=before, after=_rows(scenario.path))
    finally:
        scenario.database.engine.dispose()


def test_two_actual_connections_same_feedback_key_append_only_one_binding(
    tmp_path: Path,
) -> None:
    scenario = _scenario(tmp_path / "same-key")
    try:
        clients = [_client(scenario), _client(scenario)]
        with ThreadPoolExecutor(max_workers=2) as pool:
            calls = [pool.submit(_post, client, scenario, request_id="concurrent/key")
                     for client in clients]
            receipts = [call.result(timeout=15) for call in calls]
        assert receipts[0] == receipts[1]
        rows = _rows(scenario.path)
        assert len(rows["workbench_feedback"]) == 1
        assert len(rows["revision_feedback_evaluations"]) == 1
        assert len(rows["research_value_evaluations"]) == 2
        receipt = _get(clients[0], scenario, "concurrent/key")
        assert _get(clients[1], scenario, "concurrent/key") == receipt
        assert _rows(scenario.path) == rows
        _observe("concurrent-same-key", receipts=receipts, evaluation=receipt, rows=rows)
    finally:
        scenario.database.engine.dispose()


def test_corrupt_saved_attachment_is_not_recomputed_or_backfilled(
    tmp_path: Path,
) -> None:
    scenario = _scenario(tmp_path / "corrupt-attachment")
    try:
        client = _client(scenario)
        _post(client, scenario, request_id="immutable")
        original = _get(client, scenario, "immutable")
        with sqlite3.connect(scenario.path) as connection:
            connection.execute(
                "UPDATE revision_feedback_evaluations SET basis_json='{}' "
                "WHERE request_id='immutable'"
            )
        before = _rows(scenario.path)
        for suffix in ("/requests", "/basis", ""):
            response = client.get(
                f"/v1/product/reports/{scenario.revision_id}/feedback-evaluation{suffix}",
                params={"request_id": "immutable"}, headers={"X-Owner-Id": "owner-a"},
            )
            assert response.status_code == 409
            assert response.json()["detail"] == "revision_feedback_evaluation_invalid"
        replay = client.post(
            "/v1/workbench/feedback",
            json={"request_id": "immutable", "target_type": "report_revision",
                  "target_id": scenario.revision_id, "created_by": "owner-a",
                  "verdict": "not_useful", "notes": "Missing event context."},
            headers={"Idempotency-Key": "immutable", "X-Owner-Id": "owner-a"},
        )
        assert replay.status_code == 409
        assert _rows(scenario.path) == before
        _observe("corrupt-attachment", original=original, before=before, after=_rows(scenario.path))
    finally:
        scenario.database.engine.dispose()

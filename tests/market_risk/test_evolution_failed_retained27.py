"""Readiness error projection controls; no archived recipe replay or live service.

The reader seam runs the real, unchanged retained terminal gate against an
isolated original Run mapping. The accepted receipt is a controlled projection,
not evidence of a complete archived dependency closure or a formal Run result.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, NoReturn, cast

import pytest
from sqlalchemy.orm import Session

import packages.query_views.evolution_readiness as readiness
from packages.kernel.decision_hub_kernel.application import (
    research_graph_history_versions as history,
)
from packages.kernel.decision_hub_kernel.application.research_method import ROLE as METHOD_ROLE
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    RunRecord,
    RunRoleOutputRecord,
)

GAP = "research_history_recovered_reader_not_terminal"
NOW = datetime(2026, 10, 9, tzinfo=UTC)
RUN_ID = "controlled-retained-readiness-27"
Ledger = tuple[Database, dict[str, Any]]


class OriginalRunMapping:
    """Use the original Run table without loading an archived dependency closure."""

    @staticmethod
    def load(name: str) -> SimpleNamespace:
        assert name == "packages.kernel.decision_hub_kernel.persistence.db"
        return SimpleNamespace(RunRecord=RunRecord)


@pytest.fixture
def ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[Ledger]:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'readiness.sqlite3'}")
    database.create_all()
    receipt = {
        "output_id": "controlled-method-27",
        "run_id": RUN_ID,
        "schema_version": "research-execution-method.v3",
        "request_hash": "a" * 64,
        "definition": {"request_pack_hash": "b" * 64},
    }
    raw = json.dumps(receipt, sort_keys=True, separators=(",", ":"))
    with database.session() as session:
        session.add(RunRecord(
            run_id=RUN_ID, event_id="controlled", status="failed",
            artifact_id=None, created_at=NOW, updated_at=NOW, finished_at=NOW,
        ))
        session.add(RunRoleOutputRecord(
            output_id=receipt["output_id"], run_id=RUN_ID, role=METHOD_ROLE,
            schema_version=receipt["schema_version"], payload_json=raw,
            payload_hash=hashlib.sha256(raw.encode()).hexdigest(), created_at=NOW,
        ))
    prepared = SimpleNamespace(method=SimpleNamespace(
        definition=SimpleNamespace(policy_binding=SimpleNamespace(checker_activation="prepared")),
    ))

    def read_prepared(session: Session, run_id: str) -> SimpleNamespace:
        assert run_id == RUN_ID
        # The cast only supplies the gate's original Run table loader seam;
        # it does not claim this is a verified archived dependency closure.
        history._terminal_read_original_in_session(  # pyright: ignore[reportPrivateUsage]
            session, cast(Any, OriginalRunMapping()), run_id,
        )
        return prepared

    def original_projection(method: SimpleNamespace) -> dict[str, Any]:
        assert method is prepared.method
        return receipt

    monkeypatch.setattr(readiness, "read_prepared_execution_in_session", read_prepared)
    monkeypatch.setattr(readiness, "method_v2_view", original_projection)
    try:
        yield database, receipt
    finally:
        database.engine.dispose()


def snapshot(database: Database) -> dict[str, list[dict[str, str]]]:
    with database.session() as session:
        return {
            table.__tablename__: [
                {column.name: str(getattr(row, column.name)) for column in table.__table__.columns}
                for row in session.query(table).all()
            ]
            for table in (RunRecord, RunRoleOutputRecord)
        }


def set_terminal_state(
    database: Database, status: str, artifact_id: str | None, finished_at: datetime | None,
) -> None:
    with database.session() as session:
        run = session.get(RunRecord, RUN_ID)
        assert run is not None
        run.status, run.artifact_id, run.finished_at = status, artifact_id, finished_at


@pytest.mark.parametrize("status,artifact_id,finished_at", [
    ("failed", None, NOW),
    ("cancelled", None, NOW),
    ("failed", "controlled-artifact", NOW),
    ("cancelled", "controlled-artifact", NOW),
    ("running", "controlled-artifact", NOW),
    ("completed", None, NOW),
    ("degraded", None, NOW),
    ("completed", "controlled-artifact", None),
])
def test_retained_read_gap_is_readable_and_does_not_widen_terminal_gate(
    ledger: Ledger, status: str, artifact_id: str | None, finished_at: datetime | None,
) -> None:
    database, _receipt = ledger
    set_terminal_state(database, status, artifact_id, finished_at)
    before = snapshot(database)
    view = readiness.evolution_readiness(database, NOW).research_method
    assert view["recorded_receipts"] == 1
    assert view["latest_receipt"] is None
    assert view["latest_receipt_status"] == "invalid"
    assert view["request_pack_status"] == "invalid"
    assert view["research_policy_status"] == "invalid"
    assert view["latest_receipt_unavailable_reason"] == GAP
    assert view["incomplete_execution_run_id"] == RUN_ID
    assert view["deployment_connected"] is False
    assert view["model_dispatch"] == {"status": "not_recorded", "binding": None}
    assert view["child_model_dispatch"] == {"status": "not_recorded", "bindings": []}
    assert "research_policy_activation" not in view
    with database.session() as session:
        with pytest.raises(PermissionError, match=GAP):
            history._terminal_read_original_in_session(  # pyright: ignore[reportPrivateUsage]
                session, cast(Any, OriginalRunMapping()), RUN_ID,
            )
    assert snapshot(database) == before


@pytest.mark.parametrize("status", ["completed", "degraded"])
def test_available_receipt_projection_is_unchanged(ledger: Ledger, status: str) -> None:
    database, receipt = ledger
    set_terminal_state(database, status, "controlled-artifact", NOW)
    before = snapshot(database)
    view = readiness.evolution_readiness(database, NOW).research_method
    assert view["latest_receipt"] == receipt
    assert view["latest_receipt_status"] == "verified"
    assert view["request_pack_status"] == "verified"
    assert view["research_policy_status"] == "verified"
    assert view["research_policy_execution_status"] == "not_available"
    assert view["research_policy_activation"] == "prepared"
    assert view["deployment_connected"] is False
    assert "latest_receipt_unavailable_reason" not in view
    assert "incomplete_execution_run_id" not in view
    assert snapshot(database) == before


def test_unknown_authority_rejection_still_propagates(
    ledger: Ledger, monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, _receipt = ledger

    def reject(session: Session, run_id: str) -> NoReturn:
        raise PermissionError("research_history_read_scope_required")

    monkeypatch.setattr(readiness, "read_prepared_execution_in_session", reject)
    before = snapshot(database)
    with pytest.raises(PermissionError, match="research_history_read_scope_required"):
        readiness.evolution_readiness(database, NOW)
    assert snapshot(database) == before


def test_integrity_rejection_remains_invalid(
    ledger: Ledger, monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, _receipt = ledger

    def reject(session: Session, run_id: str) -> NoReturn:
        raise ValueError("research_history_v3_original_changed")

    monkeypatch.setattr(readiness, "read_prepared_execution_in_session", reject)
    before = snapshot(database)
    view = readiness.evolution_readiness(database, NOW).research_method
    assert view["latest_receipt_status"] == "invalid"
    assert view["latest_receipt"] is None
    assert view["request_pack_status"] != "verified"
    assert view["research_policy_status"] != "verified"
    assert view["model_dispatch"]["status"] != "verified"
    assert view["deployment_connected"] is False
    assert "latest_receipt_unavailable_reason" not in view
    assert snapshot(database) == before

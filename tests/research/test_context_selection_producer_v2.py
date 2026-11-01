"""Standalone T1 actual Factory/SQLite originals; no Graph/Runtime rewiring."""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session

from apps.hub_worker.research import ResearchRequestFactory
from packages.contracts_py.decision_hub_contracts import ResearchSessionRequest
from packages.contracts_py.decision_hub_contracts.generated.research_context_selection_v3 import (
    ResearchContextSelectionReceiptV3,
)
from packages.kernel.decision_hub_kernel.application import research_context_selection as selection
from packages.kernel.decision_hub_kernel.application.research_binding import (
    PreparedResearchPolicyRefs,
)
from packages.kernel.decision_hub_kernel.application.research_context_selection import (
    ContextSelectionService,
)
from packages.kernel.decision_hub_kernel.application.research_graph_binding import (
    admit_observed_round_in_session,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    RunRecord,
    RunRoleOutputRecord,
)
from tests.dsh_native.test_execution_preparation_v2 import PreparedInputs
from tests.evolution.test_research_worker import (
    _admitted_run,  # pyright: ignore[reportPrivateUsage]
)
from tests.orchestration.test_graph_observed_result_v2 import (
    OWNER,
    MaterialHost,
    continuation_inputs,
)
from tests.research.test_full_method_v2_bundle import (
    _prepared_pack,  # pyright: ignore[reportPrivateUsage]
    forbid_external_connections,
)
from tests.research.test_research_graph_binding_v2 import (
    _observed,  # pyright: ignore[reportPrivateUsage]
)

pytestmark = pytest.mark.usefixtures("forbid_external_connections")
assert forbid_external_connections is not None and continuation_inputs is not None


@dataclass(frozen=True)
class T1Inputs:
    database: Database
    request: ResearchSessionRequest


@pytest.fixture(params=["live", "replay"])
def t1_inputs(request: pytest.FixtureRequest, tmp_path: Path) -> Generator[T1Inputs]:
    database, event_id, run_id = _admitted_run(tmp_path)
    try:
        root, manifest = _prepared_pack(tmp_path)
        factory = ResearchRequestFactory(
            database,
            pack_root=root,
            execution_mode=request.param,
            allowed_capabilities=("document.readback", "web.search"),
            prepared_policy_refs=PreparedResearchPolicyRefs(
                gate_policy_ref=manifest["gate_policy_ref"],
                rubric_policy_ref=manifest["evaluation_policy_ref"],
                role_profile_refs=tuple(manifest["role_profile_refs"]),
            ),
        )
        original = factory.build(event_id, run_id)
        # Only the real frozen request/database fields are used by this fixture;
        # standalone T1 does not need a fabricated Method or submit.
        with database.session() as session:
            run = session.get(RunRecord, run_id)
            assert run is not None
            run.status = "running"
            run.lease_owner = OWNER
            run.lease_expires_at = datetime.now(UTC) + timedelta(minutes=5)
        yield T1Inputs(database, original)
    finally:
        database.engine.dispose()


def _snapshot(fixture: T1Inputs | PreparedInputs) -> dict[str, list[dict[str, Any]]]:
    path = fixture.database.engine.url.database
    assert path is not None and path != ":memory:"
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        return {
            table: [
                dict(row)
                for row in connection.execute(
                    'SELECT * FROM "' + table.replace('"', '""') + '" ORDER BY rowid'
                )
            ]
            for table in tables
        }


def _obs(path: Path, name: str, **body: Any) -> None:
    target = path / name
    target.write_text(
        json.dumps(body, ensure_ascii=False, sort_keys=True, indent=2) + "\n", "utf-8"
    )


def _select(service: ContextSelectionService, fixture: T1Inputs | PreparedInputs, **extra: Any):
    return service.select(
        run_id=fixture.request.run_id, graph_round=1, expected_lease_owner=OWNER, **extra
    )


def test_real_first_selection_replay_and_pure_terminal_read(
    t1_inputs: T1Inputs, tmp_path: Path
) -> None:
    service = ContextSelectionService(t1_inputs.database)
    before = _snapshot(t1_inputs)
    first = _select(service, t1_inputs)
    again = _select(service, t1_inputs)
    after = _snapshot(t1_inputs)
    assert first.reference == again.reference and first.payload_json == again.payload_json
    assert first.receipt.prepared_bundle_identity is not None
    assert first.request.current_round == 1
    assert (
        first.receipt.live_selection_clock_at is not None
        if t1_inputs.request.execution_mode == "live"
        else first.receipt.live_selection_clock_at is None
    )
    assert len(after["run_role_outputs"]) == len(before["run_role_outputs"]) + 1
    assert before["runs"] == after["runs"]
    for table in before:
        if table != "run_role_outputs":
            assert before[table] == after[table], table
    with t1_inputs.database.session() as session:
        run = session.get(RunRecord, t1_inputs.request.run_id)
        assert run is not None
        run.status = "cancelled"
    with t1_inputs.database.session() as session:
        historical = service.read_in_session(
            session,
            run_id=t1_inputs.request.run_id,
            reference=first.reference,
            require_current_permission=False,
        )
    assert historical.payload_json == first.payload_json
    terminal = _snapshot(t1_inputs)
    with pytest.raises(PermissionError):
        _select(service, t1_inputs)
    assert terminal == _snapshot(t1_inputs)
    _obs(
        tmp_path,
        "t1-first-original.json",
        before=before,
        after=after,
        terminal=terminal,
        receipt=json.loads(first.payload_json),
        original_ref=first.reference.model_dump(mode="json"),
    )


def test_fault_after_actual_selection_flush_rolls_back_every_row(
    t1_inputs: T1Inputs,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    before = _snapshot(t1_inputs)
    original = selection._insert_selection_in_session  # pyright: ignore[reportPrivateUsage]
    reached = []

    def fault(session: Session, receipt: ResearchContextSelectionReceiptV3, payload: str):
        row = original(session, receipt, payload)
        reached.append(row.output_id)
        raise RuntimeError("after real selection flush")

    monkeypatch.setattr(selection, "_insert_selection_in_session", fault)
    with pytest.raises(RuntimeError, match="real selection flush"):
        _select(ContextSelectionService(t1_inputs.database), t1_inputs)
    after = _snapshot(t1_inputs)
    assert len(reached) == 1 and before == after
    _obs(tmp_path, "t1-flush-rollback.json", before=before, after=after, actual_flushed=reached)


def test_final_clock_crossing_lease_expiry_rolls_back_after_flush(
    t1_inputs: T1Inputs,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    now = datetime.now(UTC)
    with t1_inputs.database.session() as session:
        run = session.get(RunRecord, t1_inputs.request.run_id)
        assert run is not None
        run.lease_expires_at = now + timedelta(seconds=30)
    clock = [now]
    original = selection._insert_selection_in_session  # pyright: ignore[reportPrivateUsage]

    def inserted(session: Session, receipt: ResearchContextSelectionReceiptV3, payload: str):
        row = original(session, receipt, payload)
        clock[0] = now + timedelta(seconds=31)
        return row

    monkeypatch.setattr(selection, "_insert_selection_in_session", inserted)
    before = _snapshot(t1_inputs)
    with pytest.raises(PermissionError):
        _select(ContextSelectionService(t1_inputs.database, clock=lambda: clock[0]), t1_inputs)
    after = _snapshot(t1_inputs)
    assert before == after
    _obs(
        tmp_path,
        "t1-final-fence.json",
        before=before,
        after=after,
        final_clock=clock[0].isoformat(),
    )


def test_two_real_connections_keep_the_first_id_clock_and_time(
    t1_inputs: T1Inputs, tmp_path: Path
) -> None:
    path = t1_inputs.database.engine.url.database
    assert path is not None
    other = Database(f"sqlite+pysqlite:///{Path(path).as_posix()}")
    start = threading.Barrier(2)
    try:

        def writer(database: Database):
            start.wait(timeout=10)
            return _select(ContextSelectionService(database), t1_inputs)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(writer, database) for database in (t1_inputs.database, other)]
            left, right = [future.result(timeout=90) for future in futures]
        assert left.reference == right.reference and left.payload_json == right.payload_json
        after = _snapshot(t1_inputs)
        rows = [
            row for row in after["run_role_outputs"] if row["schema_version"] == selection.SCHEMA
        ]
        assert len(rows) == 1
        _obs(
            tmp_path,
            "t1-two-connections.json",
            after=after,
            left=json.loads(left.payload_json),
            right=json.loads(right.payload_json),
        )
    finally:
        other.engine.dispose()


def test_actual_final_text_byte_cap_is_not_a_before_input_cap(
    t1_inputs: T1Inputs,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    first = _select(ContextSelectionService(t1_inputs.database), t1_inputs)
    length = len(first.payload_json.encode("utf-8"))
    before = _snapshot(t1_inputs)
    monkeypatch.setattr(selection, "MAX_STORED_SELECTION_BYTES", length)
    with t1_inputs.database.session() as session:
        assert (
            ContextSelectionService(t1_inputs.database)
            .read_in_session(
                session,
                run_id=t1_inputs.request.run_id,
                reference=first.reference,
                require_current_permission=False,
            )
            .payload_json
            == first.payload_json
        )
    monkeypatch.setattr(selection, "MAX_STORED_SELECTION_BYTES", length - 1)
    with t1_inputs.database.session() as session:
        with pytest.raises(ValueError, match="final_payload_limit"):
            ContextSelectionService(t1_inputs.database).read_in_session(
                session,
                run_id=t1_inputs.request.run_id,
                reference=first.reference,
                require_current_permission=False,
            )
    assert before == _snapshot(t1_inputs)
    _obs(
        tmp_path,
        "t1-final-cap.json",
        encoded_length=length,
        before=before,
        after=_snapshot(t1_inputs),
    )


@pytest.mark.asyncio
async def test_later_selection_uses_actual_original_graph_round(
    continuation_inputs: PreparedInputs,
    tmp_path: Path,
) -> None:
    observed = await _observed(continuation_inputs, MaterialHost(continuation_inputs))
    with continuation_inputs.database.session() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        admitted = admit_observed_round_in_session(
            session,
            run_id=continuation_inputs.request.run_id,
            expected_lease_owner=OWNER,
            database=continuation_inputs.database,
            observed=observed,
            previous=None,
        )
    before = _snapshot(continuation_inputs)
    selected = ContextSelectionService(continuation_inputs.database).select(
        run_id=continuation_inputs.request.run_id,
        graph_round=2,
        attempt_basis="next_round",
        previous_graph_ref=admitted.reference,
        expected_lease_owner=OWNER,
    )
    assert selected.receipt.decision_basis_ref is not None
    assert selected.receipt.decision_basis_ref.kind == "graph_round_admission"
    assert selected.receipt.decision_basis_ref.row.output_id == admitted.reference.row.output_id
    assert selected.request.current_round == 2
    acquired = [
        item
        for item in selected.receipt.considered_inputs
        if item.source_kind == "same_run_evidence"
    ]
    assert acquired and all(item.row_hash is not None for item in acquired)
    after = _snapshot(continuation_inputs)
    for table in before:
        if table != "run_role_outputs":
            assert before[table] == after[table], table
    _obs(
        tmp_path,
        "t1-original-graph.json",
        before=before,
        after=after,
        actual_graph_ref=admitted.reference.model_dump(mode="json"),
        receipt=json.loads(selected.payload_json),
    )


def test_explicit_reselection_keeps_parent_and_no_implicit_new_attempt(
    t1_inputs: T1Inputs,
    tmp_path: Path,
) -> None:
    service = ContextSelectionService(t1_inputs.database)
    first = _select(service, t1_inputs)
    second = _select(
        service,
        t1_inputs,
        attempt_no=2,
        attempt_basis="explicit_reselection",
        parent_selection_ref=first.reference,
    )
    assert second.receipt.parent_selection_id == first.reference.output_id
    before = _snapshot(t1_inputs)
    with pytest.raises(ValueError, match="parent_attempt_invalid"):
        _select(
            service,
            t1_inputs,
            attempt_no=4,
            attempt_basis="explicit_reselection",
            parent_selection_ref=second.reference,
        )
    assert before == _snapshot(t1_inputs)
    assert _select(service, t1_inputs).reference == first.reference
    _obs(
        tmp_path,
        "t1-explicit-parent.json",
        before=before,
        after=_snapshot(t1_inputs),
        first=json.loads(first.payload_json),
        second=json.loads(second.payload_json),
    )


@pytest.mark.parametrize("drift", ["deleted", "resigned", "unknown_schema"])
def test_original_external_ref_rejects_missing_or_resigned_selection(
    t1_inputs: T1Inputs,
    drift: str,
    tmp_path: Path,
) -> None:
    import hashlib

    service = ContextSelectionService(t1_inputs.database)
    first = _select(service, t1_inputs)
    with t1_inputs.database.session() as session:
        row = session.get(RunRoleOutputRecord, first.reference.output_id)
        assert row is not None
        if drift == "deleted":
            session.delete(row)
        elif drift == "unknown_schema":
            row.schema_version = "unknown-v99"
        else:
            body = json.loads(row.payload_json)
            body["current_full_request_hash"] = "f" * 64
            row.payload_json = json.dumps(
                body, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            row.payload_hash = hashlib.sha256(row.payload_json.encode()).hexdigest()
    before = _snapshot(t1_inputs)
    with t1_inputs.database.session() as session:
        with pytest.raises(ValueError, match="original_row_changed"):
            service.read_in_session(
                session,
                run_id=t1_inputs.request.run_id,
                reference=first.reference,
                require_current_permission=False,
            )
    assert before == _snapshot(t1_inputs)
    _obs(
        tmp_path,
        "t1-external-ref-drift.json",
        drift=drift,
        before=before,
        after=_snapshot(t1_inputs),
        original_ref=first.reference.model_dump(mode="json"),
    )


@pytest.mark.parametrize("remainder", ["0", "1"])
def test_raw_sql_timestamp_precision_is_checked_before_orm_projection(
    t1_inputs: T1Inputs,
    remainder: str,
    tmp_path: Path,
) -> None:
    path = t1_inputs.database.engine.url.database
    assert path is not None
    with sqlite3.connect(path) as connection:
        original = connection.execute(
            "SELECT created_at FROM run_role_outputs "
            "WHERE run_id=? AND role='research_task_request'",
            (t1_inputs.request.run_id,),
        ).fetchone()[0]
        assert "." in original
        changed = original + remainder
        connection.execute(
            "UPDATE run_role_outputs SET created_at=? "
            "WHERE run_id=? AND role='research_task_request'",
            (changed, t1_inputs.request.run_id),
        )
    before = _snapshot(t1_inputs)
    if remainder == "1":
        with pytest.raises(ValueError, match="time_precision_unrepresentable"):
            _select(ContextSelectionService(t1_inputs.database), t1_inputs)
        assert before == _snapshot(t1_inputs)
    else:
        first = _select(ContextSelectionService(t1_inputs.database), t1_inputs)
        assert first.receipt.task_anchor.recorded_at.isoformat().endswith("+00:00")
    _obs(
        tmp_path,
        "t1-raw-sql-precision.json",
        original=original,
        changed=changed,
        before=before,
        after=_snapshot(t1_inputs),
    )


def test_foreign_owner_and_elapsed_original_deadline_do_not_write(
    t1_inputs: T1Inputs,
    tmp_path: Path,
) -> None:
    with t1_inputs.database.session() as session:
        run = session.get(RunRecord, t1_inputs.request.run_id)
        assert run is not None
        run.lease_expires_at = t1_inputs.request.deadline_at + timedelta(minutes=1)
    before = _snapshot(t1_inputs)
    with pytest.raises(PermissionError, match="writer_permission_lost"):
        ContextSelectionService(t1_inputs.database).select(
            run_id=t1_inputs.request.run_id, graph_round=1, expected_lease_owner="foreign-owner"
        )
    with pytest.raises(PermissionError, match="deadline_elapsed"):
        _select(
            ContextSelectionService(
                t1_inputs.database, clock=lambda: t1_inputs.request.deadline_at
            ),
            t1_inputs,
        )
    assert before == _snapshot(t1_inputs)
    _obs(tmp_path, "t1-owner-deadline.json", before=before, after=_snapshot(t1_inputs))

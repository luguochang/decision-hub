"""Real SQLite caller/snapshot regressions; controlled projection seam, no model.

These tests exercise the actual LangGraph closures, Database.session,
provider borrowed-read gate, original row hash and lease checks. They do not
claim a new authenticated V3 report or replace full Graph original replay.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import event, text

from packages.contracts_py.decision_hub_contracts import ResearchSessionResult
from packages.kernel.decision_hub_kernel.application import research_graph_binding as binding
from packages.kernel.decision_hub_kernel.application.provider_input_receipts import (
    borrowed_provider_read,
    row_reference,
    sha,
)
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchEvidenceService,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    RunRecord,
    RunRoleOutputRecord,
)
from packages.orchestration.langgraph.graphs import agentic_research_graph as graph_module
from tests.orchestration.test_agentic_research_graph import _request, _result

OWNER = "physical-read-caller-test"


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "internal"))
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'caller.sqlite3'}")
    database.create_all()
    request = _request()
    now = datetime.now(UTC)
    body = '{"controlled_snapshot_original":true}'
    with database.session() as session:
        session.add(
            RunRecord(
                run_id=request.run_id,
                event_id=request.event_id,
                status="running",
                strategy_version="research.v1",
                lease_owner=OWNER,
                lease_expires_at=now + timedelta(minutes=3),
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            RunRoleOutputRecord(
                output_id="snapshot-original",
                run_id=request.run_id,
                role="research_graph_round:1",
                schema_version="research-graph-round-admission.v3",
                payload_json=body,
                payload_hash=sha(body),
                created_at=now,
            )
        )
    with database.session() as session:
        row = session.get(RunRoleOutputRecord, "snapshot-original")
        ref = binding.GraphRoundAdmissionRef(row=row_reference(row))
    result = ResearchSessionResult.model_validate(_result(request, candidates=[], horizons=[]))
    state = graph_module.initial_research_state(request)
    state.update(
        graph_round_admission_ref=ref.model_dump(mode="json"),
        latest_result=result.model_dump(mode="json"),
        coverage=result.final_coverage.model_dump(mode="json"),
        current_round=request.execution_budget.max_evidence_rounds,
    )
    seen = []

    @borrowed_provider_read
    def authenticated_read(session, *, run_id, expected_lease_owner):
        connection = session.connection()
        driver = connection.connection.driver_connection
        count = driver.total_changes
        binding._permission(session, run_id, expected_lease_owner)
        row = session.get(RunRoleOutputRecord, ref.row.output_id, populate_existing=True)
        assert row_reference(row) == ref.row
        assert session.connection() is connection and driver.in_transaction
        assert driver.total_changes == count
        seen.append({"physical_tx": driver.in_transaction, "changes_increment": 0})

    def prepared_read(session, original_state, expected_owner):
        authenticated_read(session, run_id=request.run_id, expected_lease_owner=expected_owner)
        return True, original_state

    def round_read(session, *, run_id, reference, expected_lease_owner, require_current_permission):
        assert reference == ref and require_current_permission is True
        authenticated_read(session, run_id=run_id, expected_lease_owner=expected_lease_owner)
        return SimpleNamespace(receipt=object(), state=state)

    monkeypatch.setattr(graph_module, "prepare_graph_state_in_session", prepared_read)
    monkeypatch.setattr(binding, "read_graph_round_in_session", round_read)
    monkeypatch.setattr(
        graph_module, "read_graph_recipe_selection_in_session", lambda session, run_id: None
    )

    def freeze(session, *, run_id, expected_lease_owner, **kwargs):
        authenticated_read(session, run_id=run_id, expected_lease_owner=expected_lease_owner)
        return SimpleNamespace(
            result=result,
            receipt=SimpleNamespace(decision_snapshot_id="controlled-final-snapshot"),
            reference=SimpleNamespace(model_dump=lambda **kwargs: {"controlled_final": True}),
        )

    monkeypatch.setattr(graph_module, "freeze_graph_final_in_session", freeze)
    graph = graph_module.build_agentic_research_graph(
        SimpleNamespace(),
        ResearchEvidenceService(database),
        expected_lease_owner=OWNER,
    )
    yield database, request, state, graph, seen, authenticated_read
    database.engine.dispose()


@pytest.mark.parametrize("stage", ["run_round", "route_after_round", "finalize"])
def test_each_graph_original_read_has_real_physical_snapshot(fixture, stage):
    database, request, state, graph, seen, _ = fixture
    if stage == "run_round":
        state["graph_final_projection_ref"] = {"controlled_final": True}
        value = asyncio.run(
            graph.builder.nodes["research_round" if stage == "run_round" else stage].runnable.afunc(
                state
            )
        )
        assert value == state
    elif stage == "route_after_round":
        value = graph.builder.branches["research_round"][stage].path.func(state)
        assert value == "finalize"
    else:
        value = asyncio.run(
            graph.builder.nodes["research_round" if stage == "run_round" else stage].runnable.afunc(
                state
            )
        )
        assert value["graph_final_projection_ref"] == {"controlled_final": True}
    assert seen and all(item == {"physical_tx": True, "changes_increment": 0} for item in seen)
    with database.session() as session:
        connection = session.connection()
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        driver = connection.connection.driver_connection
        before = driver.total_changes
        session.execute(
            text("UPDATE runs SET updated_at=updated_at WHERE run_id=:run"), {"run": request.run_id}
        )
        assert driver.in_transaction and driver.total_changes == before + 1
        assert session.get(RunRecord, request.run_id).status == "running"
        assert session.get(RunRoleOutputRecord, "snapshot-original").payload_json == (
            '{"controlled_snapshot_original":true}'
        )


def test_no_snapshot_tampered_original_and_stale_owner_still_refused(fixture):
    database, request, state, graph, seen, authenticated_read = fixture
    # Reproduce the original fresh-session SELECT/autobegin mismatch.
    with database.session() as session:
        assert session.connection().connection.driver_connection.in_transaction is False
        with pytest.raises(ValueError, match="research_provider_physical_snapshot_required"):
            authenticated_read(session, run_id=request.run_id, expected_lease_owner=OWNER)
    with database.session() as session:
        session.get(RunRoleOutputRecord, "snapshot-original").payload_json = '{"tampered":true}'
    route = graph.builder.branches["research_round"]["route_after_round"].path.func
    with pytest.raises(ValueError, match="research_provider_original_hash_invalid"):
        route(state)
    # Restore only the private test original and then invalidate the real lease.
    with database.session() as session:
        session.get(
            RunRoleOutputRecord, "snapshot-original"
        ).payload_json = '{"controlled_snapshot_original":true}'
        session.get(RunRecord, request.run_id).lease_owner = "other-owner"
    with pytest.raises(PermissionError, match="research_graph_writer_permission_lost"):
        route(state)
    assert not seen


def test_already_physical_snapshot_is_reused_without_double_begin(fixture):
    database, _, state, graph, seen, _ = fixture

    def actual_begin(connection):
        connection.exec_driver_sql("BEGIN")

    event.listen(database.engine, "begin", actual_begin)
    try:
        route = graph.builder.branches["research_round"]["route_after_round"].path.func
        assert route(state) == "finalize"
        assert seen == [{"physical_tx": True, "changes_increment": 0}]
    finally:
        event.remove(database.engine, "begin", actual_begin)

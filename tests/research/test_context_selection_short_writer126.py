"""Real v4 Factory originals and SQLite; controlled installation, no Host/model.

The installation fixture serves its receipt bytes in memory, so HANDLE pinning
of those nonexistent test paths uses the full-hash non-Windows fallback here.
Existing native CallbackSourceLease controls cover Windows HANDLE mechanics.
"""
from contextlib import nullcontext
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import hashlib
import threading

import pytest
from sqlalchemy import event, select

from packages.kernel.decision_hub_kernel.application import research_context_selection_v4 as selection
from packages.kernel.decision_hub_kernel.persistence.db import RunRecord, RunRoleOutputRecord, utcnow
from tests.market_risk.test_recipe_selection_v3 import installation_fixture, manifest
from tests.market_risk.test_spot_research_v3_integration import (
    OWNER, prepared, spot_component_consumer, spot_context_provider, spot_event_text, spot_inputs,
)
from tests.research.test_full_method_v2_bundle import forbid_external_connections

pytestmark = pytest.mark.usefixtures("forbid_external_connections")
assert all(x is not None for x in (installation_fixture, manifest, prepared,
    spot_component_consumer, spot_context_provider, spot_event_text, spot_inputs,
    forbid_external_connections))


@pytest.fixture(autouse=True)
def fixture_source_guard(monkeypatch):
    monkeypatch.setattr(selection, "callback_source_lease_scope", lambda: nullcontext(None))


def test_slow_selection_ro_allows_lease_writer_and_commits_once(spot_inputs, monkeypatch):
    inputs, _ = spot_inputs
    db, run_id = inputs.database, inputs.request.run_id
    service = selection.ContextSelectionService(db)
    blocked, proceed = threading.Event(), threading.Event()
    failures, result, prepared_values, source_writer_states = [], [], [], []
    physical_writers = set()
    source = selection._source
    body = selection._body
    commit = service._commit_prepared

    def statement(connection, _cursor, statement, _parameters, _context, _many):
        if statement == "BEGIN IMMEDIATE":
            physical_writers.add(id(connection.connection.driver_connection))

    def source_observed(session, **kwargs):
        native = session.connection().connection.driver_connection
        # If a pooled connection was reused, its actual transaction starts from
        # a new BEGIN; remove its prior-writer marker at that boundary below.
        source_writer_states.append(id(native) in physical_writers)
        assert id(native) not in physical_writers, "full _source must stay outside writer"
        return source(session, **kwargs)

    def boundary(connection, _cursor, statement_text, _parameters, _context, _many):
        if statement_text == "BEGIN":
            physical_writers.discard(id(connection.connection.driver_connection))

    def slow_body(*args, **kwargs):
        blocked.set()
        assert proceed.wait(15), "independent lease writer could not commit during RO preparation"
        return body(*args, **kwargs)

    def commit_observed(value):
        prepared_values.append(value)
        with pytest.raises(ValueError, match="original_preparation_unissued"):
            commit(replace(value))
        return commit(value)

    def run():
        try:
            result.append(service.select(run_id=run_id, graph_round=1, expected_lease_owner=OWNER))
        except BaseException as error:
            failures.append(error)

    event.listen(db.engine, "before_cursor_execute", boundary)
    event.listen(db.engine, "before_cursor_execute", statement)
    monkeypatch.setattr(selection, "_source", source_observed)
    monkeypatch.setattr(selection, "_body", slow_body)
    monkeypatch.setattr(service, "_commit_prepared", commit_observed)
    thread = threading.Thread(target=run)
    thread.start()
    try:
        assert blocked.wait(60)
        with db.session() as session:
            row = session.get(RunRecord, run_id)
            row.lease_expires_at = datetime.now(UTC) + timedelta(minutes=16)
            row.updated_at = utcnow()
        # COMMIT has returned before the paused expensive computation resumes.
        proceed.set()
        thread.join(60)
        assert not thread.is_alive() and not failures, failures
        original = result[0]
        replay = service.select(run_id=run_id, graph_round=1, expected_lease_owner=OWNER)
        assert replay == original
        assert len(prepared_values) == 2 and not any(source_writer_states)
        with db.session() as session:
            rows = session.scalars(select(RunRoleOutputRecord).where(
                RunRoleOutputRecord.run_id == run_id,
                RunRoleOutputRecord.role == "context_select.r1.a1",
            )).all()
            assert len(rows) == 1 and rows[0].payload_json == original.payload_json
    finally:
        proceed.set()
        thread.join(60)
        event.remove(db.engine, "before_cursor_execute", statement)
        event.remove(db.engine, "before_cursor_execute", boundary)


@pytest.mark.parametrize("change", ["raw_bytes", "absent_selection"])
def test_short_writer_rejects_changes_after_selection_preparation(spot_inputs, monkeypatch, change):
    inputs, _ = spot_inputs
    db, run_id = inputs.database, inputs.request.run_id
    service = selection.ContextSelectionService(db)
    commit = service._commit_prepared

    def changed(value):
        with db.session() as session:
            if change == "raw_bytes":
                row = session.scalar(select(RunRoleOutputRecord).where(
                    RunRoleOutputRecord.run_id == run_id,
                    RunRoleOutputRecord.role == "research_task_request",
                ))
                # Leave the stored hash unchanged: self-reported metadata is insufficient.
                row.payload_json += " "
            else:
                session.add(RunRoleOutputRecord(output_id="concurrent-selection", run_id=run_id,
                    role="context_select.r1.a1", schema_version=selection.SCHEMA,
                    payload_json="{}", payload_hash=hashlib.sha256(b"{}").hexdigest(),
                    created_at=utcnow()))
        with pytest.raises(ValueError, match="readset_changed"):
            commit(value)
        with db.session() as session:
            assert session.get(RunRoleOutputRecord, value.value.original.reference.output_id) is None
        raise RuntimeError("expected concurrent mutation rejected")

    monkeypatch.setattr(service, "_commit_prepared", changed)
    with pytest.raises(RuntimeError, match="expected concurrent mutation rejected"):
        service.select(run_id=run_id, graph_round=1, expected_lease_owner=OWNER)

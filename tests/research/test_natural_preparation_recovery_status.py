"""Tmp SQLite metadata orchestration controls, not a genuine Source qualification.

The six native tables and canonical state history use the existing lifecycle
readers. Owner/publication and public consumer are explicit test seams; they do
not issue a Source permit, invoke a model, or qualify a production recovery.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import sqlite3
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from packages.kernel.decision_hub_kernel.application import research_publication_jobs48 as jobs
from packages.kernel.decision_hub_kernel.persistence.db import Base, RunRecord, RunRoleOutputRecord
from packages.kernel.decision_hub_kernel.persistence.research_publication_bundle_records import (
    PublicationBundleJobRecord as Job, PublicationBundleRecord as Saved,
    PublicationBundleConfirmationRecord as Ack, PublicationBundleStateEventRecord as Event,
)

from packages.kernel.decision_hub_kernel.application import research_report_recovery_commands as MODULE
NOW = datetime(2026, 10, 12, tzinfo=timezone.utc)
RUN, REQUEST, PLAN = 'native-status-run', 'native-status-freeze', 'native-review-plan'
ABI = 'publication-source-snapshot-read.v2'
REASON = 'frozen_io_undeclared_authorization_resource'


@pytest.fixture
def control(tmp_path):
    path = tmp_path / 'metadata.db'
    engine = create_engine('sqlite:///' + path.as_posix())
    tables = [row.__table__ for row in (RunRecord, RunRoleOutputRecord, Job, Saved, Ack, Event)]
    Base.metadata.create_all(engine, tables=tables)
    with engine.connect() as connection:
        connection.exec_driver_sql('PRAGMA journal_mode=WAL')
    with Session(engine) as writer:
        writer.add(RunRecord(run_id=RUN, event_id='event', strategy_version='research.v1',
            runtime_version='fixture', admission_origin='scheduled_recheck', parent_run_id='parent',
            idempotency_key='research-review-plan:' + PLAN, created_at=NOW, updated_at=NOW))
        for role in (jobs.PUBLICATION_SOURCE_ROLE, 'graph_recipe_selection'):
            writer.add(RunRoleOutputRecord(output_id=role, run_id=RUN, role=role,
                payload_json='{}', payload_hash='a' * 64, created_at=NOW))
        writer.add(Job(run_id=RUN, request_id=REQUEST, event_id='event', artifact_id='artifact',
            publication_id='publication', publication_hash='a' * 64, publication_schema='source.v1',
            publication_created_at=NOW, generation=1, state_version=1, state='unconfirmed',
            reason_code=REASON, updated_at=NOW))
        body = dict(schema_version='publication-bundle-state-event.v2', run_id=RUN,
            request_id=REQUEST, version=1, generation=1, state='unconfirmed', reason_code=REASON,
            bundle_hash=None, occurred_at=NOW.isoformat(), transition_phase='unconfirmed',
            failure_diagnostic={'status': 'unavailable', 'sha256': None})
        raw = jobs.encoded(body)
        writer.add(Event(event_id=jobs.sha(raw), payload_json=raw.decode(), occurred_at=NOW,
            **{name: body[name] for name in ('run_id', 'request_id', 'version', 'generation',
                'state', 'reason_code', 'bundle_hash')}))
        writer.commit()
    ro_engine = create_engine('sqlite://', poolclass=NullPool, creator=lambda: sqlite3.connect(
        'file:' + path.as_posix() + '?mode=ro', uri=True))
    state = SimpleNamespace(engine=engine, sessions=[], closed=[], heads=[], calls=[],
        qualified_calls=0, finishes=0, mutate=None, exit_error=None, abi=ABI)
    life = object.__new__(jobs.DurablePublicationLifecycle)
    life.archive = SimpleNamespace(fenced=lambda _run: False)
    life._publication = lambda _session, _job: None
    life.saved_confirmation_owner_proof = lambda _session, _run, owner: dict(
        owner_id=owner, root_run_id='parent', lineage=[RUN, 'parent'], owner_source='receipt')
    original_head = life._preparation_authorization_head79
    def head(session, *args, **kwargs):
        state.heads.append(session)
        return original_head(session, *args, **kwargs)
    life._preparation_authorization_head79 = head
    @contextmanager
    def ro():
        with Session(ro_engine) as session:
            session.connection().exec_driver_sql('PRAGMA query_only=ON')
            session.connection().exec_driver_sql('BEGIN')
            native = session.connection().connection.driver_connection
            assert native.execute('PRAGMA query_only').fetchone() == (1,)
            assert native.in_transaction
            state.sessions.append((session, native))
            try:
                yield session
            finally:
                state.closed.append(session)
                session.rollback()
    life._ro = ro
    def qualifier(session, *, run_id):
        assert run_id == RUN
        state.qualified_calls += 1
        return state.abi
    def consumer(first, *, run_id, consume):
        assert run_id == RUN and first not in state.closed
        state.calls.append('qualification_entry')
        state.qualified_calls += 1
        if state.mutate:
            state.mutate()
        def finish(second):
            assert second is not first
            assert first not in state.closed and second not in state.closed
            assert len(state.sessions) == 2
            assert state.sessions[0][1] is not state.sessions[1][1]
            state.calls.append('fresh_exit')
            state.finishes += 1
            if state.exit_error:
                raise state.exit_error
        return consume(state.abi, finish)
    life.preparer = SimpleNamespace(reader_abi_qualification=qualifier,
        consume_reader_abi_qualification=consumer)
    state.life, state.service = life, MODULE.PreparationAuthorizationRecoveryCommandService(None, life)
    yield state
    ro_engine.dispose()
    engine.dispose()


def test_one_qualification_spans_two_full_independent_native_heads_and_fresh_exit(control):
    result = control.service.status(RUN, owner_id='owner')
    assert result.eligible and result.source_abi == ABI
    assert result.proof_scope == 'metadata_prechecks_only' and not result.automatic_retry
    assert len(control.heads) == 2 and control.heads[0] is not control.heads[1]
    assert control.qualified_calls == 1 and control.finishes == 1
    assert control.calls == ['qualification_entry', 'fresh_exit']
    assert len(control.closed) == 2


def test_second_complete_native_head_rejects_changed_run_cell_before_finish(control):
    def mutate():
        with control.engine.begin() as writer:
            writer.exec_driver_sql('UPDATE runs SET error_code=? WHERE run_id=?', ('changed', RUN))
    control.mutate = mutate
    with pytest.raises(ValueError, match='publication_preparation_recovery_metadata_changed'):
        control.service.status(RUN, owner_id='owner')
    assert len(control.heads) == 2 and control.finishes == 0 and len(control.closed) == 2


def test_source_fresh_exit_failure_never_returns_eligible_metadata(control):
    control.exit_error = ValueError('publication_reader_qualification_source_exit_changed')
    with pytest.raises(ValueError, match='publication_reader_qualification_source_exit_changed'):
        control.service.status(RUN, owner_id='owner')
    assert control.finishes == 1 and len(control.closed) == 2


def test_second_native_gap_history_is_revalidated_before_finish(control):
    def mutate():
        with control.engine.begin() as writer:
            writer.exec_driver_sql('UPDATE research_publication_bundle_state_events '
                'SET payload_json=payload_json || ? WHERE run_id=?', (' ', RUN))
    control.mutate = mutate
    with pytest.raises(ValueError, match='publication_retry_event_history_changed'):
        control.service.status(RUN, owner_id='owner')
    assert len(control.heads) == 2 and control.finishes == 0 and len(control.closed) == 2


def test_unsupported_abi_fails_before_second_metadata_and_finish(control):
    control.abi = None
    with pytest.raises(ValueError, match='publication_recovery_current_reader_required'):
        control.service.status(RUN, owner_id='owner')
    assert control.qualified_calls == 1 and control.finishes == 0 and len(control.heads) == 1


def test_legacy_preparer_keeps_two_complete_qualifications(control):
    del control.life.preparer.consume_reader_abi_qualification
    assert control.service.status(RUN, owner_id='owner').eligible
    assert control.qualified_calls == 2 and len(control.heads) == 2 and control.finishes == 0


def test_nonmatching_native_head_has_two_reads_and_no_source_qualification(control):
    with control.engine.begin() as writer:
        writer.exec_driver_sql('UPDATE research_publication_bundle_jobs SET state=?, reason_code=NULL WHERE run_id=?',
            ('queued', RUN))
    result = control.service.status(RUN, owner_id='owner')
    assert not result.eligible and result.native_sha256 is None and result.source_abi is None
    assert len(control.sessions) == 2 and control.qualified_calls == 0 and control.finishes == 0

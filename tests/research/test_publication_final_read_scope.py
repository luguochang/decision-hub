"""Read-scope bookkeeping controls; doubles grant no native/report authority.

Formal same-original HTTP/PC acceptance separately proves the actual reader.
"""
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, Field
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from packages.kernel.decision_hub_kernel.application import research_graph_binding as binding
from packages.kernel.decision_hub_kernel.ports.research_observation import ObservedExecutionRef


class Value(BaseModel):
    items: list[str] = Field(default_factory=list)


@pytest.fixture
def control(monkeypatch):
    engine = create_engine('sqlite+pysqlite:///:memory:')
    with Session(engine) as session:
        session.connection().exec_driver_sql('CREATE TABLE marker (value TEXT)')
        session.commit()
        session.connection().exec_driver_sql('BEGIN')
        state = {'full_reads': 0, 'source_matches': True}
        reference = binding.GraphFinalProjectionRef(row=ObservedExecutionRef(
            output_id='controlled-final', role='research_graph_final',
            schema_version='research-graph-final-projection.v3',
            payload_hash='a' * 64, recorded_at=datetime(2026, 10, 8, tzinfo=UTC),
        ))
        def full_reader(*args, **kwargs):
            state['full_reads'] += 1
            return binding.FrozenGraphFinal(reference, Value(), Value())
        monkeypatch.setattr(binding, '_read_graph_final_current_in_session', full_reader)
        monkeypatch.setattr(binding, 'replay_retained_graph_final_in_session', lambda *a, **k: None)
        monkeypatch.setattr(binding, '_read_final_receipt', lambda *a, **k: None)
        monkeypatch.setattr(binding._ReadSourceGuard, 'capture', lambda: SimpleNamespace(
            matches=lambda: state['source_matches']))
        def read():
            return binding.read_graph_final_in_session(session, run_id='controlled',
                reference=reference, expected_lease_owner=None, require_current_permission=False)
        yield session, state, read
        session.rollback()
    engine.dispose()


def test_completed_read_is_local_to_one_scope(control):
    session, state, read = control
    with binding.publication_final_read_scope(session):
        assert read().result == read().result
        assert state['full_reads'] == 1
    with binding.publication_final_read_scope(session):
        read()
    assert state['full_reads'] == 2


def test_write_after_reuse_prevents_returning_the_view(control):
    session, _, read = control
    with pytest.raises(ValueError, match='research_graph_original_source_changed'):
        with binding.publication_final_read_scope(session):
            read()
            read()
            session.connection().exec_driver_sql("INSERT INTO marker VALUES ('changed')")


def test_mutated_completed_result_is_not_reused(control):
    session, _, read = control
    with binding.publication_final_read_scope(session):
        read().result.items.append('changed')
        with pytest.raises(ValueError, match='research_graph_cached_final_changed'):
            read()


def test_source_check_at_end_is_required_before_returning(control):
    session, state, read = control
    with pytest.raises(ValueError, match='research_graph_original_source_changed'):
        with binding.publication_final_read_scope(session):
            read()
            read()
            state['source_matches'] = False

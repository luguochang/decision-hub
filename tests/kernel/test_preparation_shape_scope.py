"""Current preparation shape reuse with real fixture source/readset gates."""

from __future__ import annotations

from typing import ClassVar

import pytest
from pydantic import ConfigDict, StrictInt, ValidationError, model_validator
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts import binding_contract_base as binding
from packages.kernel.decision_hub_kernel.persistence import original_preparation as preparation
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    RunRoleOutputRecord,
    utcnow,
)


class Probe(binding.ResearchBindingContract):
    model_config = ConfigDict(extra="forbid", revalidate_instances="always")
    value: StrictInt
    checks: ClassVar[int] = 0
    reject: ClassVar[bool] = False

    @model_validator(mode="after")
    def fresh_semantics(self):
        type(self).checks += 1
        if type(self).reject:
            raise ValueError("probe_semantics_rejected")
        return self


@pytest.fixture
def database(tmp_path):
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'shape.sqlite3'}")
    database.create_all()
    with database.session() as session:
        session.add(RunRoleOutputRecord(
            output_id="original", run_id="fixture_run", role="original", payload_json="1",
            payload_hash="0" * 64, schema_version="fixture.v1", created_at=utcnow(),
        ))
    try:
        yield database
    finally:
        database.engine.dispose()


def test_actual_prepare_runs_once_and_row_change_still_rejected(database):
    operations = []
    stats = []

    def operation(session: Session):
        row = session.get(RunRoleOutputRecord, "original")
        assert row is not None
        operations.append(row.payload_json)
        memo = binding._SHAPE_MEMO.get()
        assert memo is not None
        stats.append(memo)
        before = Probe.checks
        payload = {"value": int(row.payload_json)}
        first = Probe.model_validate(payload)
        second = Probe.model_validate(dict(payload))
        assert first == second and first is not second and Probe.checks == before + 2
        assert memo.stats.hit_count == memo.stats.miss_count == 1
        Probe.reject = True
        try:
            with pytest.raises(ValidationError, match="probe_semantics_rejected"):
                Probe.model_validate(payload)
        finally:
            Probe.reject = False
        assert Probe.checks == before + 3
        return second

    prepared = preparation.prepare_originals(database, operation)
    assert operations == ["1"]
    assert not stats[0].active and not stats[0].successes and not stats[0].schemas
    assert binding._SHAPE_MEMO.get() is None
    with database.session() as session:
        preparation.begin_immediate(session)
        prepared.verify(session)
    with database.session() as session:
        row = session.get(RunRoleOutputRecord, "original")
        row.payload_json = "2"
    with database.session() as session:
        preparation.begin_immediate(session)
        with pytest.raises(ValueError, match="original_preparation_readset_changed"):
            prepared.verify(session)
    fresh = preparation.prepare_originals(database, operation)
    assert operations == ["1", "2"] and fresh.value.value == 2
    assert stats[1] is not stats[0] and stats[1].stats.miss_count == 1


def test_deadline_error_not_masked_and_scope_clears_before_any_writer(database):
    entered = []

    def deadline_failed(_session):
        memo = binding._SHAPE_MEMO.get()
        entered.append(memo)
        for _ in range(3):
            Probe.model_validate({"value": 1})
        assert memo.stats.hit_count == 2
        raise PermissionError("original_preparation_deadline_elapsed")

    with pytest.raises(PermissionError, match="original_preparation_deadline_elapsed"):
        preparation.prepare_originals(database, deadline_failed)
    assert len(entered) == 1 and not entered[0].active and not entered[0].successes
    assert binding._SHAPE_MEMO.get() is None
    with database.session() as session:
        row = session.get(RunRoleOutputRecord, "original")
        assert row.payload_json == "1"


def test_loaded_current_scope_replacement_rejected_at_entry_and_exit(monkeypatch):
    original = binding.shape_validation_scope
    monkeypatch.setattr(binding, "shape_validation_scope", lambda: None)
    with pytest.raises(ValueError, match="original_preparation_shape_scope_changed"):
        with preparation._current_shape_read_scope():
            pytest.fail("changed scope entered")
    monkeypatch.setattr(binding, "shape_validation_scope", original)
    with pytest.raises(ValueError, match="original_preparation_shape_scope_changed"):
        with preparation._current_shape_read_scope():
            memo = binding._SHAPE_MEMO.get()
            monkeypatch.setattr(binding, "shape_validation_scope", lambda: None)
    assert not memo.active and binding._SHAPE_MEMO.get() is None

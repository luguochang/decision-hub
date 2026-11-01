"""Real SQLite fresh-snapshot/readset controls; no Host or normal database."""
from contextlib import contextmanager
from dataclasses import replace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.kernel.decision_hub_kernel.persistence import original_preparation as prep
from packages.kernel.decision_hub_kernel.persistence.db import RunRoleOutputRecord, utcnow
from tests.kernel.test_original_preparation import database

assert database is not None


@contextmanager
def readonly(database):
    with database.engine.connect() as connection, Session(bind=connection) as session:
        connection.exec_driver_sql("PRAGMA query_only=ON")
        connection.exec_driver_sql("BEGIN")
        try:
            yield session
        finally:
            session.rollback()
            connection.exec_driver_sql("PRAGMA query_only=OFF")


def prepare(database):
    def original(session):
        assert session.scalars(select(RunRoleOutputRecord)).all()
        assert session.get(RunRoleOutputRecord, "absent") is None
        return "original"
    return prep.prepare_originals(database, original)


def test_issued_original_read_rechecks_fresh_ro_and_rejects_forgery(database):
    saved = prepare(database)
    with readonly(database) as session:
        saved.verify_read_snapshot(session)
        with pytest.raises(ValueError, match="original_preparation_unissued"):
            replace(saved).verify_read_snapshot(session)
    with database.session() as session:
        prep.begin_immediate(session)
        with pytest.raises(ValueError, match="read_snapshot_required"):
            saved.verify_read_snapshot(session)
        saved.verify(session)  # Existing writer consumer contract remains supported.


@pytest.mark.parametrize("change", ["bytes", "phantom", "absence"])
def test_fresh_read_rejects_changed_raw_bytes_ranges_and_absence(database, change):
    saved = prepare(database)
    with database.session() as session:
        if change == "bytes":
            session.get(RunRoleOutputRecord, "original").payload_json = '{"changed":true}'
        else:
            session.add(RunRoleOutputRecord(
                output_id="absent" if change == "absence" else "phantom",
                run_id="run", role=change, payload_json="{}", payload_hash="0" * 64,
                schema_version="original.v1", created_at=utcnow(),
            ))
    with readonly(database) as session, pytest.raises(ValueError, match="readset_changed"):
        saved.verify_read_snapshot(session)

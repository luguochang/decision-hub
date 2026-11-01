"""Actual SQLite snapshot/readset mechanics, not a replacement authority fixture."""

from __future__ import annotations

import sqlite3
import threading
import time
from collections.abc import Generator
from pathlib import Path

import pytest
from sqlalchemy import event, select
from sqlalchemy.engine import ExceptionContext
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from packages.kernel.decision_hub_kernel.persistence import original_preparation as preparation
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    RunRoleOutputRecord,
    utcnow,
)
from packages.kernel.decision_hub_kernel.persistence.sqlite_busy import (
    is_sqlite_initial_begin_busy,
    is_sqlite_storage_busy,
)


@pytest.fixture
def database(tmp_path: Path) -> Generator[Database]:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'originals.sqlite3'}")
    database.create_all()
    with database.session() as session:
        session.add(RunRoleOutputRecord(
            output_id="original", run_id="run", role="original", payload_json="{}",
            payload_hash="0" * 64, schema_version="original.v1", created_at=utcnow(),
        ))
    try:
        yield database
    finally:
        database.engine.dispose()


@pytest.mark.parametrize("pending", ["new", "dirty", "deleted"])
def test_read_preparation_never_flushes_pending_orm_writes(
    database: Database, pending: str,
) -> None:
    def invalid(session: Session) -> None:
        row = session.get(RunRoleOutputRecord, "original")
        assert row is not None
        if pending == "new":
            session.add(RunRoleOutputRecord(
                output_id="new", run_id="run", role="new", payload_json="{}",
                payload_hash="0" * 64, schema_version="original.v1", created_at=utcnow(),
            ))
        elif pending == "dirty":
            row.payload_json = '{"changed":true}'
        else:
            session.delete(row)

    with pytest.raises(ValueError, match="pending_write"):
        preparation.prepare_originals(database, invalid)
    with database.session() as session:
        rows = session.scalars(select(RunRoleOutputRecord)).all()
        assert len(rows) == 1 and rows[0].payload_json == "{}"


@pytest.mark.parametrize("change", ["bytes", "phantom", "absence"])
def test_short_writer_replays_original_rows_ranges_and_absence(
    database: Database, change: str,
) -> None:
    def originals(session: Session) -> None:
        assert session.scalars(select(RunRoleOutputRecord)).all()
        assert session.get(RunRoleOutputRecord, "absent") is None

    prepared = preparation.prepare_originals(database, originals)
    with database.session() as session:
        if change == "bytes":
            row = session.get(RunRoleOutputRecord, "original")
            assert row is not None
            row.payload_json = '{"changed":true}'
        else:
            session.add(RunRoleOutputRecord(
                output_id="absent" if change == "absence" else "phantom",
                run_id="run", role=change, payload_json="{}", payload_hash="0" * 64,
                schema_version="original.v1", created_at=utcnow(),
            ))
    with pytest.raises(ValueError, match="readset_changed"), database.session() as session:
        preparation.begin_immediate(session)
        prepared.verify(session)


def test_initial_busy_retry_does_not_repeat_preparation_or_business_work(
    database: Database,
) -> None:
    lock_ready, release = threading.Event(), threading.Event()
    locked: list[BaseException] = []
    path = database.sqlite_path
    assert path is not None

    def hold() -> None:
        try:
            with sqlite3.connect(path) as connection:
                connection.execute("BEGIN IMMEDIATE")
                lock_ready.set()
                assert release.wait(12)
                connection.rollback()
        except BaseException as error:
            locked.append(error)

    preparations = 0

    def prepare(session: Session) -> str:
        nonlocal preparations
        preparations += 1
        assert session.get(RunRoleOutputRecord, "original") is not None
        return "prepared once; no model"

    prepared = preparation.prepare_originals(database, prepare)
    owner = threading.Thread(target=hold)
    owner.start()
    assert lock_ready.wait(3)
    attempts: list[str] = []
    observed_busy: list[bool] = []

    def begin(_connection: object, _cursor: object, statement: str,
              _parameters: object, _context: object, _executemany: bool) -> None:
        if statement == "BEGIN IMMEDIATE":
            attempts.append(statement)

    def busy(context: ExceptionContext) -> None:
        # A timer can release before SQLite actually reports BUSY. Observe the
        # genuine first failed BEGIN, then let the unchanged retry acquire it.
        error = context.sqlalchemy_exception
        if error is not None and is_sqlite_initial_begin_busy(error):
            observed_busy.append(True)
            release.set()

    event.listen(database.engine, "handle_error", busy)
    started = time.monotonic()
    try:
        with database.session() as session:
            event.listen(session.connection(), "before_cursor_execute", begin)
            preparation.begin_immediate(session)
            prepared.verify(session)
            session.add(RunRoleOutputRecord(
                output_id="once", run_id="run", role="once", payload_json="{}",
                payload_hash="0" * 64, schema_version="original.v1", created_at=utcnow(),
            ))
            session.flush()
    finally:
        release.set()
        owner.join(3)
        event.remove(database.engine, "handle_error", busy)
    assert not owner.is_alive() and not locked
    assert attempts == ["BEGIN IMMEDIATE", "BEGIN IMMEDIATE"]
    assert observed_busy == [True]
    assert preparations == 1 and 5 <= time.monotonic() - started < 12
    with database.session() as session:
        assert session.query(RunRoleOutputRecord).filter_by(output_id="once").count() == 1


@pytest.mark.parametrize("code,initial,storage", [
    (sqlite3.SQLITE_BUSY, True, True), (sqlite3.SQLITE_LOCKED, True, True),
    (sqlite3.SQLITE_BUSY_SNAPSHOT, False, True), (sqlite3.SQLITE_ERROR, False, False),
])
def test_busy_classifier_uses_actual_sqlite_code_and_initial_statement(
    code: int, initial: bool, storage: bool,
) -> None:
    original = sqlite3.OperationalError("message must not decide retry")
    original.sqlite_errorcode = code
    error = OperationalError("BEGIN IMMEDIATE", (), original)
    assert is_sqlite_initial_begin_busy(error) is initial
    assert is_sqlite_storage_busy(error) is storage
    assert not is_sqlite_initial_begin_busy(OperationalError("UPDATE runs", (), original))
    assert not is_sqlite_storage_busy(ValueError("database is locked"))


def test_consumer_source_changes_are_checked_without_changing_graph_manifest(
    tmp_path: Path,
) -> None:
    class OriginalGraphGuard:
        def matches(self) -> bool:
            return True

    application = tmp_path / "packages/kernel/decision_hub_kernel/application"
    application.mkdir(parents=True)
    file = application / "commit.py"
    file.write_bytes(b"original source")
    source = preparation._ConsumerSourceGuard.capture(  # pyright: ignore[reportPrivateUsage]
        OriginalGraphGuard(), tmp_path,
    )
    assert source.matches()
    file.write_bytes(b"changed publication source")
    assert not source.matches()

"""Physical SQLite writers and fresh scheduler authority; no Provider calls."""

from __future__ import annotations

import sqlite3
from collections.abc import Generator, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from threading import Event

import pytest
from sqlalchemy import event, text
from sqlalchemy.orm import Session

from packages.kernel.decision_hub_kernel.application.review_schedule import ReviewScheduleService
from packages.kernel.decision_hub_kernel.application.study import ensure_study
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    EventRecord,
    ReviewScheduleRecord,
    RunRecord,
    StudyRecord,
)
from tests.research.test_tracking_continuity import (
    NOW,
    _publish,  # pyright: ignore[reportPrivateUsage]
    _report_bytes,  # pyright: ignore[reportPrivateUsage]
    _root,  # pyright: ignore[reportPrivateUsage]
    _update,  # pyright: ignore[reportPrivateUsage]
)


@pytest.fixture
def database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Database]:
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "isolated-data"))
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'scheduler-writers.sqlite3'}")
    database.create_all()
    try:
        yield database
    finally:
        database.engine.dispose()


def _another_root(database: Database) -> str:
    finished = NOW - timedelta(hours=1)
    with database.session() as session:
        study_id = ensure_study(
            session, question_key="separate-question", title="Independent study",
            market="crypto", instrument="BTC-USDT-SWAP", now=finished,
        )
        session.add(EventRecord(
            event_id="event:other", event_type="crypto_market_snapshot",
            occurred_at=finished, received_at=finished,
        ))
        session.add(RunRecord(
            run_id="run:other", event_id="event:other", study_id=study_id,
            status="completed", strategy_version="research.v1", runtime_version="controlled",
            admission_origin="manual", priority=100, created_at=finished, updated_at=finished,
        ))
    _publish(database, "run:other", finished)
    return "run:other"


@pytest.mark.parametrize("empty_study", [False, True])
def test_empty_maintenance_does_not_compete_with_an_existing_writer(
    database: Database, empty_study: bool,
) -> None:
    if empty_study:
        with database.session() as session:
            ensure_study(
                session, question_key="no-report", title="No saved basis",
                market="crypto", instrument="BTC-USDT-SWAP", now=NOW,
            )
    statements: list[str] = []

    def record_statement(
        _connection: object, _cursor: object, statement: str,
        _parameters: object, _context: object, _many: bool,
    ) -> None:
        if statement.strip().upper() == "BEGIN IMMEDIATE":
            statements.append(statement)

    event.listen(database.engine, "before_cursor_execute", record_statement)
    path = database.sqlite_path
    assert path is not None
    writer = sqlite3.connect(path)
    try:
        writer.execute("BEGIN IMMEDIATE")
        assert writer.in_transaction
        # An actual other connection owns the writer throughout this cycle.
        # The old unconditional maintenance BEGIN waits five seconds and fails.
        assert ReviewScheduleService(database, clock=lambda: NOW).plan_due() == ()
        assert statements == []
        assert writer.in_transaction
    finally:
        writer.rollback()
        writer.close()
        event.remove(database.engine, "before_cursor_execute", record_statement)


def test_discovery_refreshes_authority_after_its_read_only_hint(
    database: Database, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _study = _root(database)
    original_session = database.session
    replaced: list[str] = []
    changed = False

    @contextmanager
    def sessions() -> Generator[Session, None, None]:
        nonlocal changed
        with original_session() as session:
            yield session
        if not changed:
            changed = True
            replaced.append(_update(
                database, root, "writer-scope:new-basis",
                accepted_at=NOW - timedelta(minutes=4),
                finished_at=NOW - timedelta(minutes=1),
                all_next_minutes=10,
            ))

    monkeypatch.setattr(database, "session", sessions)
    discovered = ReviewScheduleService(database, clock=lambda: NOW)._discover_plans(  # pyright: ignore[reportPrivateUsage]
        NOW
    )
    assert discovered == set(replaced)
    with original_session() as session:
        plans = session.query(ReviewScheduleRecord).all()
        assert len(plans) == 3
        assert {plan.parent_run_id for plan in plans} == set(replaced)


def _pause_after_first_writer(
    database: Database, monkeypatch: pytest.MonkeyPatch,
) -> tuple[Event, Event]:
    original_session = database.session
    boundary, resume = Event(), Event()
    paused = False

    @contextmanager
    def sessions() -> Generator[Session, None, None]:
        nonlocal paused
        with original_session() as session:
            yield session
            driver = session.connection().connection.driver_connection
            writer = isinstance(driver, sqlite3.Connection) and driver.in_transaction
        if writer and not paused:
            paused = True
            # The actual context manager has committed and released SQLite's
            # writer. Pause here, never while holding that writer ourselves.
            boundary.set()
            assert resume.wait(5), "scheduler writer boundary was not released"

    monkeypatch.setattr(database, "session", sessions)
    return boundary, resume


def test_another_writer_commits_between_study_discoveries(
    database: Database, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _root(database)
    _another_root(database)
    original_session = database.session
    boundary, resume = _pause_after_first_writer(database, monkeypatch)
    scheduler = ReviewScheduleService(database, clock=lambda: NOW)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(scheduler._discover_plans, NOW)  # pyright: ignore[reportPrivateUsage]
        try:
            assert boundary.wait(5)
            with original_session() as session:
                # Only one Study was processed before the first writer ended.
                assert session.query(ReviewScheduleRecord).count() == 2
            with original_session() as session:
                session.execute(text("BEGIN IMMEDIATE"))
                session.add(EventRecord(
                    event_id="independent-writer", event_type="crypto_market_snapshot",
                    occurred_at=NOW, received_at=NOW,
                ))
                session.flush()
            with original_session() as session:
                assert session.get(EventRecord, "independent-writer") is not None
        finally:
            resume.set()
        assert future.result(timeout=5) == {"run:root", "run:other"}
    with original_session() as session:
        assert session.query(ReviewScheduleRecord).count() == 4


def test_retirement_rechecks_new_basis_between_each_plan_writer(
    database: Database, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, study_id = _root(database)
    original_bytes = _report_bytes(database, root)
    scheduler = ReviewScheduleService(database, clock=lambda: NOW)
    for horizon in ("24h", "72h"):
        scheduler._ensure_plan(  # pyright: ignore[reportPrivateUsage]
            parent_run_id=root, horizon=horizon, trigger_type="next_review",
            trigger_ref=f"physical-retire:{horizon}",
            trigger_at=NOW + timedelta(hours=1), now=NOW,
        )
    with database.session() as session:
        study = session.get(StudyRecord, study_id)
        assert study is not None
        study.status = "paused"
    original_session = database.session
    boundary, resume = _pause_after_first_writer(database, monkeypatch)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            scheduler._retire_superseded_plans, NOW, {root}  # pyright: ignore[reportPrivateUsage]
        )
        try:
            assert boundary.wait(5)
            with original_session() as session:
                assert session.query(ReviewScheduleRecord).filter_by(status="skipped").count() == 1
            with original_session() as session:
                session.execute(text("BEGIN IMMEDIATE"))
                study = session.get(StudyRecord, study_id)
                assert study is not None
                study.status = "active"
                session.flush()
        finally:
            resume.set()
        future.result(timeout=5)
    with original_session() as session:
        plans = session.query(ReviewScheduleRecord).all()
        assert sorted(plan.status for plan in plans) == ["planned", "skipped"]
    assert _report_bytes(database, root) == original_bytes


def test_retirement_does_not_cancel_a_child_dispatched_after_discovery(
    database: Database, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, study_id = _root(database)
    scheduler = ReviewScheduleService(database, clock=lambda: NOW)
    scheduler._ensure_plan(  # pyright: ignore[reportPrivateUsage]
        parent_run_id=root, horizon="24h", trigger_type="next_review",
        trigger_ref="leased-after-hint", trigger_at=NOW, now=NOW,
    )
    with database.session() as session:
        study = session.get(StudyRecord, study_id)
        assert study is not None
        study.status = "paused"
    original_session = database.session
    dispatched = False

    @contextmanager
    def sessions() -> Generator[Session, None, None]:
        nonlocal dispatched
        with original_session() as session:
            yield session
        if not dispatched:
            dispatched = True
            with original_session() as session:
                session.execute(text("BEGIN IMMEDIATE"))
                plan = session.query(ReviewScheduleRecord).one()
                plan.status, plan.child_run_id = "admitted", "run:leased"
                session.add(RunRecord(
                    run_id="run:leased", event_id="event:tracking", study_id=study_id,
                    status="running", strategy_version="research.v1", runtime_version="controlled",
                    admission_origin="scheduled_recheck", priority=10, parent_run_id=root,
                    lease_owner="real-isolated-writer", lease_expires_at=NOW + timedelta(minutes=5),
                    created_at=NOW, updated_at=NOW,
                ))

    monkeypatch.setattr(database, "session", sessions)
    scheduler._retire_superseded_plans(NOW, {root})  # pyright: ignore[reportPrivateUsage]
    with original_session() as session:
        child = session.get(RunRecord, "run:leased")
        plan = session.query(ReviewScheduleRecord).one()
        assert child is not None and child.status == "running"
        assert child.lease_owner == "real-isolated-writer"
        assert plan.status == "admitted" and plan.failure_code is None

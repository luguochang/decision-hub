# pyright: reportPrivateUsage=false
"""One deadline33 control node using private, actual SQLite stores.

An empty observational cohort can produce only a waiting receipt, never a
passing calibration. Event barriers exercise the next cooperative checkpoint;
they do not establish a hard time bound for arbitrary source/archive I/O.
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, NoReturn

import httpx
import pytest
from sqlalchemy import event, select
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from packages.evals.assessment_basis import _ArchiveRecorder
from packages.kernel.decision_hub_kernel.application import calibration
from packages.kernel.decision_hub_kernel.application.calibration_inputs import (
    AIProbabilityScope,
    CalibrationEvaluationPolicy,
)
from packages.kernel.decision_hub_kernel.persistence import original_preparation as preparation
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    RunRoleOutputRecord,
    StrategyCalibrationJobRecord,
    StrategyCalibrationProductionRecord,
)
from packages.provider_adapters.research.raw_capture import RawBodyArchive


@pytest.mark.asyncio
async def test_calibration_deadline33_native_cancel_join_rollback_and_uncertain_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Actual wrappers/writers/guards; no replacement producer, fit, or proof."""
    now = datetime(2026, 10, 9, 9, 0, tzinfo=UTC)
    databases: list[Database] = []
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "private-data"))

    def forbidden_network(*_args: object, **_kwargs: object) -> NoReturn:
        raise AssertionError("private calibration control attempted external HTTP")

    async def forbidden_async_network(*_args: object, **_kwargs: object) -> NoReturn:
        raise AssertionError("private calibration control attempted external HTTP")

    monkeypatch.setattr(httpx.Client, "request", forbidden_network)
    monkeypatch.setattr(httpx.AsyncClient, "request", forbidden_async_network)

    class ControlCancelled(RuntimeError):
        pass

    def new_database(name: str) -> Database:
        database = Database(f"sqlite+pysqlite:///{tmp_path / (name + '.sqlite3')}")
        database.create_all()
        databases.append(database)
        return database

    def original_row(database: Database) -> None:
        with database.session() as session:
            session.add(RunRoleOutputRecord(
                output_id="original", run_id="control", role="original", payload_json="{}",
                payload_hash="0" * 64, schema_version="control.v1", created_at=now,
            ))

    def new_producer(name: str):
        database = new_database(name)
        archive = RawBodyArchive(tmp_path / name / "raw")
        producer = calibration.KernelCalibrationProducer(
            database, archive=archive, clock=lambda: now,
        )
        stable = "1" * 64
        scope = AIProbabilityScope(
            market="crypto", instrument="BTC-USDT", horizon="24h",
            stable_recipe_hash=stable,
            strategy_version=calibration.ai_probability_strategy_version(stable),
            pack_definition_hash="2" * 64, compiled_policy_recipe_hash="3" * 64,
            method_recipe_hash="4" * 64, graph_recipe_hash="5" * 64,
        )
        plan = producer.register_plan(
            request_id="control-" + name, scope=scope,
            policy=CalibrationEvaluationPolicy(purpose="observational_diagnostic"),
            start_at=now - timedelta(days=2), end_at=now - timedelta(days=1),
            approval_source_ref="private-control:empty-observational-cohort",
        )
        return database, producer, plan

    # An expensive native SELECT must stop from the actual progress callback,
    # close its borrowed snapshot, and restore a usable pooled connection.
    long_select = (
        "SELECT sum(x) FROM (WITH RECURSIVE control(x) AS "
        "(SELECT 1 UNION ALL SELECT x + 1 FROM control WHERE x < 10000000) "
        "SELECT x FROM control)"
    )
    try:
        database = new_database("native-prepare")
        native_seen: list[sqlite3.Connection] = []
        query_started = threading.Event()
        progress_calls = 0

        def before_query(
            connection: Connection, _cursor: Any, statement: str,
            _parameters: Any, _context: Any, _many: bool,
        ) -> None:
            if statement == long_select:
                native = connection.connection.driver_connection
                assert isinstance(native, sqlite3.Connection)
                native_seen.append(native)
                query_started.set()

        def stop_native_query() -> None:
            nonlocal progress_calls
            if query_started.is_set():
                progress_calls += 1
                raise ControlCancelled("control_native_query_cancelled")

        def expensive_read(session: Session):
            return session.connection().exec_driver_sql(long_select).scalar_one()

        event.listen(database.engine, "before_cursor_execute", before_query)
        try:
            with pytest.raises(ControlCancelled, match="control_native_query_cancelled"):
                preparation.prepare_originals(
                    database, expensive_read, checkpoint=stop_native_query,
                )
        finally:
            event.remove(database.engine, "before_cursor_execute", before_query)
        assert progress_calls > 0 and native_seen
        assert all(not native.in_transaction for native in native_seen)
        # This real write rejects a leaked read authorizer; SELECT also rejects
        # an uncleared interrupting progress callback on the pooled connection.
        original_row(database)
        with database.session() as session:
            assert session.connection().exec_driver_sql("SELECT 1").scalar_one() == 1

        # Replay query digest uses the same actual native interruption scope.
        digest_calls = 0

        def stop_digest_query() -> None:
            nonlocal digest_calls
            digest_calls += 1
            if digest_calls >= 4:
                raise ControlCancelled("control_native_digest_cancelled")

        with sqlite3.connect(tmp_path / "native-digest.sqlite3") as native:
            native.execute("BEGIN")
            with pytest.raises(ControlCancelled, match="control_native_digest_cancelled"):
                with preparation._sqlite_checkpoint_scope(native, stop_digest_query):
                    preparation._query_digest(
                        native, long_select, (), checkpoint=stop_digest_query,
                    )
            assert digest_calls >= 4
            assert native.execute("SELECT 1").fetchone() == (1,)
            native.rollback()
            assert not native.in_transaction

        # Original byte/range proof remains live after adding checkpoint hooks.
        def read_originals(session: Session):
            row = session.get(RunRoleOutputRecord, "original")
            assert row is not None
            assert session.scalars(select(RunRoleOutputRecord)).all()
            return row.output_id

        prepared = preparation.prepare_originals(database, read_originals, checkpoint=lambda: None)
        with database.session() as session:
            row = session.get(RunRoleOutputRecord, "original")
            assert row is not None
            row.payload_json = '{"changed":true}'
        with pytest.raises(ValueError, match="readset_changed"), database.session() as session:
            preparation.begin_immediate(session)
            prepared.verify(session, checkpoint=lambda: None)

        fresh = preparation.prepare_originals(database, read_originals, checkpoint=lambda: None)
        assert isinstance(fresh.source, preparation._ConsumerSourceGuard)
        # Use the authentic Graph guard plus an isolated consumer source file;
        # this control never mutates a production source or replaces matches().
        consumer_root = tmp_path / "consumer-source"
        consumer_file = consumer_root / "packages/kernel/decision_hub_kernel/application/control.py"
        consumer_file.parent.mkdir(parents=True)
        consumer_file.write_bytes(b"original consumer source")
        source = preparation._ConsumerSourceGuard.capture(fresh.source.graph, consumer_root)
        source_prepared = replace(fresh, source=source)
        assert source.matches()
        consumer_file.write_bytes(b"changed consumer source")
        with pytest.raises(ValueError, match="source_changed"), database.session() as session:
            preparation.begin_immediate(session)
            source_prepared.verify(session, checkpoint=lambda: None)

        # Private helpers use the producer's authenticated dependency aliases.
        # Initialize them through the real loaded/source gate, as production does.
        calibration._require_producer_loaded()
        archive = RawBodyArchive(tmp_path / "archive-originals")
        digest, reference = archive.put(b"private control original")
        recorder = _ArchiveRecorder(archive)
        assert recorder.read(reference) == b"private control original"
        originals = list(recorder.records.values())
        calibration._verify_archives(originals, archive, checkpoint=lambda: None)
        (archive.root / digest[:2] / digest).write_bytes(b"changed private control original")
        with pytest.raises(ValueError, match="calibration_original_archive_changed"):
            calibration._verify_archives(originals, archive, checkpoint=lambda: None)

        # Positive mechanics control: actual empty cohort cannot manufacture P.
        baseline_db, baseline, baseline_plan = new_producer("baseline-waiting")
        assert await baseline.process_due(deadline_at=asyncio.get_running_loop().time() + 30) == 1
        with baseline_db.session() as session:
            productions = session.scalars(select(StrategyCalibrationProductionRecord)).all()
            assert len(productions) == 1
            assert productions[0].state == "waiting" and productions[0].report_json is None
            job = session.get(StrategyCalibrationJobRecord, baseline_plan.job_id)
            assert job is not None and job.status == "waiting" and job.attempt == 1

        # Already elapsed deadline never reaches claim/publication.
        elapsed_db, elapsed, elapsed_plan = new_producer("elapsed-before-claim")
        with pytest.raises(TimeoutError, match="calibration_deadline_elapsed"):
            await elapsed.process_due(deadline_at=asyncio.get_running_loop().time() - 1)
        with elapsed_db.session() as session:
            job = session.get(StrategyCalibrationJobRecord, elapsed_plan.job_id)
            assert job is not None and job.attempt == 0
            assert session.query(StrategyCalibrationProductionRecord).count() == 0

        # Hold actual worker at a read snapshot and at an uncommitted INSERT.
        # Cancellation must wait for that owner, then the next checkpoint must
        # close/rollback before the caller observes CancelledError.
        for stage in ("before-publish", "inside-writer"):
            db, producer, plan = new_producer(stage)
            entered, release = threading.Event(), threading.Event()
            native_at_barrier: list[sqlite3.Connection] = []

            def barrier(
                connection: Connection, _cursor: Any, statement: str,
                _parameters: Any, _context: Any, _many: bool,
            ) -> None:
                upper = statement.lstrip().upper()
                selected = (
                    "FROM ANALYTICAL_OUTLOOKS" in upper
                    if stage == "before-publish"
                    else upper.startswith("INSERT INTO STRATEGY_CALIBRATION_PRODUCTIONS")
                )
                if selected and not entered.is_set():
                    native = connection.connection.driver_connection
                    assert isinstance(native, sqlite3.Connection) and native.in_transaction
                    native_at_barrier.append(native)
                    entered.set()
                    assert release.wait(20), "private control barrier was not released"

            event.listen(db.engine, "after_cursor_execute", barrier)
            task = asyncio.create_task(producer.process_due(
                deadline_at=asyncio.get_running_loop().time() + 30,
            ))
            try:
                assert await asyncio.to_thread(entered.wait, 15), stage
                task.cancel()
                await asyncio.sleep(0.02)
                assert not task.done(), "caller detached from the blocked original worker"
                release.set()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, timeout=15)
            finally:
                release.set()
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                event.remove(db.engine, "after_cursor_execute", barrier)
            assert native_at_barrier and all(not item.in_transaction for item in native_at_barrier)
            with db.session() as session:
                assert session.query(StrategyCalibrationProductionRecord).count() == 0
                job = session.get(StrategyCalibrationJobRecord, plan.job_id)
                assert job is not None and job.attempt == 1 and job.status == "running"
                assert job.lease_owner is not None and job.lease_expires_at is not None
                assert job.last_production_id is None

        # Database.session really commits the waiting receipt, then its commit
        # wrapper raises uncertainty. The actual producer must propagate once;
        # it must neither replay business work nor claim a passing result.
        uncertain_db, uncertain, uncertain_plan = new_producer("commit-uncertain")
        real_commit = Session.commit
        uncertain_commits = 0

        def mark_publication(session: Session, _flush_context: Any, _instances: Any) -> None:
            if session.get_bind() is uncertain_db.engine and any(
                isinstance(item, StrategyCalibrationProductionRecord) for item in session.new
            ):
                session.info["control_publication_commit"] = True

        def commit_then_uncertain(session: Session) -> None:
            nonlocal uncertain_commits
            real_commit(session)
            if session.get_bind() is uncertain_db.engine and session.info.get("control_publication_commit"):
                uncertain_commits += 1
                raise RuntimeError("control_commit_outcome_unknown")

        event.listen(Session, "before_flush", mark_publication)
        try:
            with monkeypatch.context() as patch:
                patch.setattr(Session, "commit", commit_then_uncertain)
                with pytest.raises(RuntimeError, match="control_commit_outcome_unknown"):
                    await uncertain.process_due(deadline_at=asyncio.get_running_loop().time() + 30)
        finally:
            event.remove(Session, "before_flush", mark_publication)
        assert uncertain_commits == 1
        with uncertain_db.session() as session:
            productions = session.scalars(select(StrategyCalibrationProductionRecord)).all()
            assert len(productions) == 1
            assert productions[0].state == "waiting" and productions[0].report_json is None
            job = session.get(StrategyCalibrationJobRecord, uncertain_plan.job_id)
            assert job is not None and job.attempt == 1 and job.status == "waiting"
            assert job.last_production_id == productions[0].production_id
    finally:
        for database in databases:
            database.engine.dispose()

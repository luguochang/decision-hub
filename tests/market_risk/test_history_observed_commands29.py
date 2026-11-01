"""Four new wiring controls, prepared only until Root grants a test window.

Private SQLite only. The one fit wiring node reuses the sealed model24 artificial
history fixture; its 90 slots/60 joint days are a declared fixture, not real data.
No old mathematical test suite, network, model provider or product build runs.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
from sqlalchemy import Table, create_engine, event, func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from packages.kernel.decision_hub_kernel.application.history_observed_acquisition import (
    HistoryObservedAcquisition,
)
from packages.kernel.decision_hub_kernel.application.history_observed_commands import (
    HistoryObservedCommandService,
)
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.kernel.decision_hub_kernel.persistence.history_observed_records import (
    HistoryObservedAcquisitionJobRecord as Job,
)
from packages.kernel.decision_hub_kernel.persistence.history_observed_records import (
    HistoryObservedTrainingRecord as Record,
)
from packages.provider_adapters.research.raw_capture import RawBodyArchive

# Only the new command/session boundary and existing private fixture ports.
# pyright: reportPrivateUsage=false

ROOT = Path(__file__).resolve().parents[2]
MODEL_FIXTURE = ROOT / (
    "artifacts/market-risk-implementation-20261007/B2/"
    "history-observed-model24/test_history_observed_model24.py"
)


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def reject(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("control attempted external HTTP")

    async def async_reject(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("control attempted external HTTP")

    monkeypatch.setattr(httpx.Client, "request", reject)
    monkeypatch.setattr(httpx.AsyncClient, "request", async_reject)


@pytest.fixture
def private_commands(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # The actual Database.session commit/rollback wrapper; no default directories
    # or initialize/create_all that could conceal registration wiring.
    database = Database.__new__(Database)
    database.engine = create_engine(f"sqlite:///{tmp_path / 'private29.sqlite3'}")
    database.factory = sessionmaker(database.engine, expire_on_commit=False)
    for record in (Record, Job):
        cast(Table, record.__table__).create(database.engine)
    clock = SimpleNamespace(now=datetime(2030, 10, 9, 12, tzinfo=UTC))
    from apps.hub_worker.composition import build_history_observed_worker

    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "private-data"))
    commands = build_history_observed_worker(
        database, owner_id="private-command29", clock=lambda: clock.now,
    )
    archive = commands.ledger.builder.archive
    try:
        yield SimpleNamespace(db=database, clock=clock, archive=archive, commands=commands)
    finally:
        database.engine.dispose()


def test_explicit_registration_and_status_never_verify_stored_hash_or_fit(
    private_commands: Any, monkeypatch: pytest.MonkeyPatch,
):
    env, commands = private_commands, private_commands.commands
    with Session(env.db.engine) as session:
        assert session.scalar(select(func.count()).select_from(Record)) == 0
        assert session.scalar(select(func.count()).select_from(Job)) == 0
    with pytest.raises(ValueError, match="request_id_invalid"):
        commands.register("")
    protocol = commands.register("private-command29-register")
    assert commands.register(protocol.request_id) == protocol
    with Session(env.db.engine) as session:
        assert session.scalar(select(func.count()).select_from(Job)) == 180
        assert session.scalar(select(func.count()).select_from(Record)) == 1
    with commands._write_session() as session:
        # Stored metadata with deliberately unauthenticated payload/raw fields.
        # Status may show its stored hash but must not claim original verification.
        session.add(Record(
            record_id="private-unverified-dataset", registration_id=protocol.registration_id,
            request_id=protocol.request_id, kind="dataset", scope_id=protocol.scopes[0].scope_id,
            payload_json="not a dataset", payload_hash="0" * 64,
            original_ref="not-an-original", original_hash="0" * 64,
            known_at=env.clock.now, recorded_at=env.clock.now,
        ))

    def unused(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("status called training or original archive path")

    monkeypatch.setattr(commands.ledger, "prepare", unused)
    monkeypatch.setattr(commands.ledger, "dataset", unused)
    monkeypatch.setattr(commands.model, "prepare_fit", unused)
    monkeypatch.setattr(commands.model, "save", unused)
    monkeypatch.setattr(env.archive, "put", unused)
    originals_before = tuple(sorted(env.archive.root.rglob("*.json")))
    status = commands.status(protocol.registration_id)
    assert status.protocol_hash == protocol.payload_hash
    assert status.registered_at == protocol.registered_at
    assert status.deadline_at == protocol.registered_at + timedelta(hours=6)
    assert status.state_counts == (("held", 180),) and status.attempts == 0
    assert len(status.jobs) == 180
    assert {job.cutoff_at for job in status.jobs} == set(protocol.daily_cutoffs)
    assert tuple(sorted(env.archive.root.rglob("*.json"))) == originals_before
    assert any(row.payload_hash == "0" * 64 for row in status.stored)
    assert not status.dataset_originals_verified and not status.model_originals_verified
    assert not status.ready_for_real_fit and not status.financial_qualified
    with pytest.raises(DBAPIError):
        with commands._read_session() as session:
            session.connection().exec_driver_sql(
                "UPDATE market_history_acquisition_jobs SET attempts=attempts+1"
            )
    with pytest.raises(ValueError, match="registration_missing"):
        commands.status("wrong-registration")
    with env.db.engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA query_only").scalar_one() == 0


def test_main_default_role_and_required_registration_before_database(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
):
    # Import and exercise actual parser/role dispatch, not a copied parser.
    from apps.hub_worker import main

    calls: list[object] = []

    class PrivateDatabase:
        def __init__(self) -> None:
            calls.append("database")

        def initialize(self) -> None:
            calls.append("initialize")

    async def realtime_tick() -> Any:
        calls.append("realtime")
        return SimpleNamespace(
            polled=(), runs_started=0, outcomes_processed=0, notifications_delivered=0,
        )

    async def history_process(registration_id: str) -> bool:
        calls.append(("process_one", registration_id))
        return False

    def history_factory(*_args: Any, **kwargs: Any) -> Any:
        calls.append("history_factory")
        assert kwargs["owner_id"].startswith("history-observed:")
        return SimpleNamespace(
            process_one=history_process,
            status=lambda registration_id: SimpleNamespace(
                registration_id=registration_id, state_counts=(("held", 180),), attempts=0,
            ),
        )

    monkeypatch.setattr(main, "Database", PrivateDatabase)
    monkeypatch.setattr(
        main, "_configure_retained_recipe_registry_from_normal_launch", lambda: None,
    )
    monkeypatch.setattr(main.PilotSettings, "from_env", lambda: SimpleNamespace())
    monkeypatch.setattr(main, "_registry", lambda _settings: SimpleNamespace())
    monkeypatch.setattr(main, "build_realtime_worker", lambda *_args, **_kw: SimpleNamespace(
        tick=realtime_tick,
    ))
    monkeypatch.setattr(main, "build_history_observed_worker", history_factory)
    monkeypatch.setattr(sys, "argv", ["hub-worker", "--once"])
    assert main.run() == 0
    assert calls == ["database", "initialize", "realtime"]
    calls.clear()
    for arguments in (
        ["--role", "history-observed"],
        ["--role", "historical", "--registration-id", "wrong-role"],
    ):
        monkeypatch.setattr(sys, "argv", ["hub-worker", *arguments])
        with pytest.raises(SystemExit) as error:
            main.run()
        assert error.value.code == 2 and not calls
    monkeypatch.setattr(sys, "argv", [
        "hub-worker", "--role", "history-observed", "--registration-id", "explicit-R", "--once",
    ])
    assert main.run() == 0
    assert calls == ["database", "initialize", "history_factory", ("process_one", "explicit-R")]
    output = capsys.readouterr().out
    assert '"metadata_only": true' in output and '"ready_for_real_fit": false' in output


@pytest.fixture(scope="module")
def artificial_history(tmp_path_factory: pytest.TempPathFactory):
    # Reuse the sealed fixture algorithm once, not its 11 existing test nodes.
    spec = importlib.util.spec_from_file_location("command29_model24_fixture", MODEL_FIXTURE)
    assert spec is not None and spec.loader is not None
    module: Any = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    generator = cast(Any, module.history).__wrapped__(tmp_path_factory)
    history = next(generator)
    database = Database.__new__(Database)
    database.engine = history.engine
    database.factory = sessionmaker(history.engine, expire_on_commit=False)
    acquisition = HistoryObservedAcquisition(
        database, history.ledger, RawBodyArchive(history.root), owner_id="private-fit-wiring29",
    )
    commands = HistoryObservedCommandService(
        database, history.ledger, acquisition, history.service,
    )
    try:
        yield SimpleNamespace(history=history, db=database, commands=commands, BTC=module.BTC)
    finally:
        with pytest.raises(StopIteration):
            next(generator)


def test_actual_private_sqlite_ro_prepare_then_fresh_writer_freeze_fit(
    artificial_history: Any, monkeypatch: pytest.MonkeyPatch,
):
    env, calls, statements = artificial_history, [], []
    commands, history = env.commands, env.history

    def statement(_conn: Any, _cursor: Any, sql: str, *_rest: Any) -> None:
        if sql in {"BEGIN", "BEGIN IMMEDIATE"}:
            statements.append(sql)

    event.listen(env.db.engine, "before_cursor_execute", statement)

    def watch(phase: str, original: Any):
        def call(session: Session, *args: Any, **kwargs: Any):
            native = cast(sqlite3.Connection, session.connection().connection.driver_connection)
            calls.append((
                phase, session, session.get_transaction(), native.in_transaction,
                session.connection().exec_driver_sql("PRAGMA query_only").scalar_one(),
                threading.get_ident(), args,
            ))
            return original(session, *args, **kwargs)
        return call

    original_writer = commands.acquisition._writer
    writer_entries: list[bool] = []

    def writer(session: Session) -> None:
        native = cast(sqlite3.Connection, session.connection().connection.driver_connection)
        writer_entries.append(native.in_transaction)
        original_writer(session)

    monkeypatch.setattr(commands.acquisition, "_writer", writer)
    for owner, method, phase in (
        (commands.ledger, "prepare", "prepare"), (commands.ledger, "freeze", "freeze"),
        (commands.model, "prepare_fit", "prepare_fit"), (commands.model, "save", "save"),
    ):
        monkeypatch.setattr(owner, method, watch(phase, getattr(owner, method)))
    try:
        dataset = commands.freeze(history.protocol.registration_id, env.BTC, history.known)
        assert dataset == history.dataset
        model = commands.fit(history.protocol.registration_id, env.BTC)
        assert model.fit == history.prepared.fit
        assert model.model_known_at >= model.fit.fit_completed_at
        assert [item[0] for item in calls] == ["prepare", "freeze", "prepare_fit", "save"]
        assert [item[3] for item in calls] == [True] * 4
        assert [item[4] for item in calls] == [1, 0, 1, 0]
        assert len({item[5] for item in calls}) == 1
        assert calls[0][1] is not calls[1][1] and calls[0][2] is not calls[1][2]
        assert calls[2][1] is not calls[3][1] and calls[2][2] is not calls[3][2]
        assert writer_entries == [False, False]
        assert statements.count("BEGIN") == 2 and statements.count("BEGIN IMMEDIATE") == 2
        prepared = calls[3][6][0]
        assert prepared.issuer is commands.model and prepared.thread_id == threading.get_ident()
        with Session(env.db.engine) as session:
            saved = session.scalar(select(Record).where(
                Record.registration_id == history.protocol.registration_id, Record.kind == "model",
            ))
            assert saved is not None and saved.payload_hash == model.payload_hash
            assert (
                history.archive.read(saved.original_ref, saved.original_hash)
                == saved.payload_json.encode()
            )
    finally:
        event.remove(env.db.engine, "before_cursor_execute", statement)


def test_unknown_commit_has_no_retry_or_success_ack(private_commands: Any):
    env, commands = private_commands, private_commands.commands
    commits: list[str] = []

    class UnknownCommit(RuntimeError):
        pass

    class CommitThenRaise(Session):
        def commit(self) -> None:
            commits.append("one commit")
            super().commit()
            raise UnknownCommit("controlled commit outcome unknown to caller")

    env.db.factory = sessionmaker(env.db.engine, class_=CommitThenRaise, expire_on_commit=False)
    with pytest.raises(UnknownCommit, match="unknown to caller"):
        commands.register("private-command29-uncertain")
    assert commits == ["one commit"]
    # A late/durable row is evidence, not a fabricated successful command ACK.
    with Session(env.db.engine) as session:
        assert session.scalar(select(func.count()).select_from(Record)) == 1
        assert session.scalar(select(func.count()).select_from(Job)) == 180
    # No same-request replay is performed to turn this exception into success.

    begins: list[str] = []

    def reject_begin(_conn: Any, _cursor: Any, sql: str, *_rest: Any) -> None:
        if sql == "BEGIN IMMEDIATE":
            begins.append(sql)
            raise UnknownCommit("controlled BEGIN outcome unknown")

    event.listen(env.db.engine, "before_cursor_execute", reject_begin)
    try:
        with pytest.raises(UnknownCommit, match="BEGIN outcome unknown"):
            commands.register("private-command29-begin-unknown")
    finally:
        event.remove(env.db.engine, "before_cursor_execute", reject_begin)
    assert begins == ["BEGIN IMMEDIATE"] and commits == ["one commit"]

"""Six finite private forecast controls; prepared, not yet executed.

The one artificial model24 fixture is adapted to the real raw:// archive and
production builder. No original mathematical suite, HTTP, SDK or product runs.
"""

from __future__ import annotations

import hashlib
import importlib.util
import shutil
import sqlite3
import sys
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from types import FrameType, SimpleNamespace
from typing import Any, cast

import httpx
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import event, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from apps.hub_worker import composition
from apps.hub_worker import history_forecast_cli as cli
from packages.kernel.decision_hub_kernel.application.history_observed_dataset import (
    HistoryObservedArchive,
    HistoryObservedDatasetBuilder,
    HistoryObservedOriginals,
)
from packages.kernel.decision_hub_kernel.application.history_observed_forecast import (
    CONSUMER_FILES,
    HistoryForecastLocalCommands,
    HistoryForecastPreparation,
    HistoryObservedForecastService,
    forecast_id,
)
from packages.kernel.decision_hub_kernel.application.history_observed_ledger import SOURCE_FILES
from packages.kernel.decision_hub_kernel.application.history_observed_model import (
    HistoryObservedModelService,
)
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.kernel.decision_hub_kernel.persistence.history_observed_forecast_records import (
    HistoryObservedForecastRecord as ForecastRow,
)
from packages.kernel.decision_hub_kernel.persistence.risk_records import MarketCandleRecord
from packages.kernel.decision_hub_kernel.ports.history_observed_forecast import (
    HistoryForecastIdentityV1,
    RecordedHistoryForecastV1,
)
from packages.kernel.decision_hub_kernel.ports.market_path import (
    MarketCandle,
    canonical_hash,
    spot_scope,
)
from packages.provider_adapters.research.raw_capture import RawBodyArchive

# Controls call real private boundaries, never replace the production guards.
# pyright: reportPrivateUsage=false

ROOT = Path(__file__).resolve().parents[2]
MODEL_FIXTURE = ROOT / (
    "artifacts/market-risk-implementation-20261007/B2/"
    "history-observed-model24/test_history_observed_model24.py"
)
MIGRATION = ROOT / "migrations/versions/0083_history_observed_forecasts.py"


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def reject(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("private forecast control attempted HTTP")

    async def async_reject(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("private forecast control attempted HTTP")

    monkeypatch.setattr(httpx.Client, "request", reject)
    monkeypatch.setattr(httpx.AsyncClient, "request", async_reject)


def _load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _count(env: Any) -> int:
    with env.commands._read_session() as session:
        return cast(int, session.scalar(select(func.count()).select_from(ForecastRow)))


def _raw_path(env: Any, ref: str) -> Path:
    assert ref.startswith("raw://sha256/")
    digest = ref.rsplit("/", 1)[1]
    return env.history.root / "raw-captures" / digest[:2] / digest


def _archive_state(env: Any) -> dict[str, str]:
    return {
        str(path.relative_to(env.archive.root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in env.archive.root.rglob("*")
        if path.is_file()
    }


@contextmanager
def _changed_bytes(path: Path) -> Iterator[None]:
    original = path.read_bytes()
    path.write_bytes(original + b" ")
    try:
        yield
    finally:
        path.write_bytes(original)


@contextmanager
def _calls(*codes: Any) -> Iterator[list[Any]]:
    seen: list[Any] = []
    previous = sys.getprofile()

    def profile(frame: FrameType, kind: str, _arg: object) -> None:
        if kind == "call" and frame.f_code in codes:
            seen.append(frame.f_code)

    sys.setprofile(profile)
    try:
        yield seen
    finally:
        sys.setprofile(previous)


def _writer_refuses(env: Any, prepared: HistoryForecastPreparation) -> None:
    before = _count(env)
    with pytest.raises(ValueError):
        with env.commands._write_session() as session:
            env.local.forecasts.record_history_forecast(session, prepared)
    assert _count(env) == before


@pytest.fixture(scope="module")
def forecast_private(tmp_path_factory: pytest.TempPathFactory) -> Generator[Any, None, None]:
    module = _load(MODEL_FIXTURE, "forecast34_model24_fixture")
    capture_page = module.capture_page
    transport: dict[str, Any] = {}

    def archive(root: Path) -> HistoryObservedArchive:
        # Adapt only this artificial fixture's constructors to the real archive
        # layout. No production class, callable or source guard is replaced.
        transport["raw"] = RawBodyArchive(root.parent / "raw-captures")
        return HistoryObservedArchive(root.parent / "history-observed")

    def originals(_unused_reader: Any) -> HistoryObservedOriginals:
        return HistoryObservedOriginals(transport["raw"].read)

    def page(*args: Any, **kwargs: Any) -> Any:
        capture, records, checkpoint = capture_page(*args, **kwargs)
        body = Path(capture.body_ref).read_bytes()
        digest, ref = transport["raw"].put(body)
        assert digest == capture.body_hash
        capture.body_ref = ref
        for row in records:
            bar = MarketCandle.model_validate_json(row["candle_json"])
            changed = bar.model_copy(update={"capture_ref": ref})
            assert canonical_hash(changed.semantic_payload()) == row["revision_hash"]
            row["capture_ref"] = ref
            row["candle_json"] = changed.model_dump_json()
        checkpoint["capture_ref"] = ref
        return capture, records, checkpoint

    module.HistoryObservedArchive = archive
    module.HistoryObservedOriginals = originals
    module.capture_page = page
    generator = cast(Any, module.history).__wrapped__(tmp_path_factory)
    history = next(generator)
    migration = _load(MIGRATION, "forecast34_migration")
    with history.engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
    history.clock.now += timedelta(seconds=1)
    with module.begin(history.engine, True) as session:
        model = history.service.save(session, history.prepared)
        session.commit()
    cutoff = (history.clock.now + timedelta(minutes=2)).replace(second=0, microsecond=0)
    captures, records, _ = page(
        history.root,
        job_id="forecast34-future",
        phase="feature",
        cursor=str(int((cutoff - timedelta(minutes=1)).timestamp() * 1000)),
        rows=[(cutoff - timedelta(minutes=i), Decimal(100)) for i in range(2, 33)],
        completed=cutoff - timedelta(seconds=10),
    )
    with module.begin(history.engine, True) as session:
        session.add(captures)
        session.execute(MarketCandleRecord.__table__.insert(), records)
        session.commit()
    history.clock.now = cutoff + timedelta(seconds=1)
    database = Database.__new__(Database)
    database.engine = history.engine
    database.factory = sessionmaker(history.engine, expire_on_commit=False)
    (history.root / "db").mkdir()
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("DECISION_HUB_DATA_DIR", str(history.root))
        commands = composition.build_history_observed_worker(
            database,
            owner_id="forecast34-private",
            clock=lambda: history.clock.now,
        )
    mirror = history.root / "consumer-source-mirror"
    for name in dict.fromkeys((*SOURCE_FILES, *CONSUMER_FILES)):
        target = mirror / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    # This private mirror has the exact registered bytes, not a new authority.
    # It permits actual freshness negatives without mutating shared DEV sources.
    commands.ledger.root = mirror.resolve()
    local = HistoryForecastLocalCommands(commands)

    def identity(request: str) -> HistoryForecastIdentityV1:
        return HistoryForecastIdentityV1(
            operator_owner_claim="forecast34-private",
            request_id=request,
            registration_id=history.protocol.registration_id,
            scope=module.BTC,
            expected_model_hash=model.payload_hash,
            expected_model_known_at=model.model_known_at,
            cutoff_at=cutoff,
            threshold="0.02",
        )

    primary, pending = identity("forecast34-primary"), identity("forecast34-pending")
    prepared: list[HistoryForecastPreparation] = []
    for item in (primary, pending):
        with commands._read_session() as session:
            result = local.forecasts.prepare_history_forecast(session, item)
            assert isinstance(result, HistoryForecastPreparation)
            prepared.append(result)
    try:
        yield SimpleNamespace(
            history=history,
            module=module,
            migration=migration,
            db=database,
            commands=commands,
            local=local,
            archive=commands.ledger.builder.archive,
            mirror=mirror,
            primary=primary,
            pending=pending,
            first=prepared[0],
            draft=prepared[1],
            page=page,
            model=model,
        )
    finally:
        with pytest.raises(StopIteration):
            next(generator)


def test_0083_first_commit_same_request_and_ro_read_are_immutable(forecast_private: Any) -> None:
    env = forecast_private
    assert _count(env) == 0
    with _calls(HistoryObservedDatasetBuilder.window.__code__) as writer_math:
        with env.commands._write_session() as session:
            saved = env.local.forecasts.record_history_forecast(session, env.first)
    assert not writer_math, "forecast writer reconstructed 90-day window mathematics"
    assert _count(env) == 1
    assert saved.forecast.model_known_at <= saved.forecast.cutoff_at
    assert len(saved.selected_closes) == 31 and saved.horizon_seconds == 86400
    assert (saved.forecast.baseline_n, saved.forecast.conditional_n) == (60, 30)
    assert not saved.financial_qualified and not saved.promotion_allowed
    env.history.clock.now += timedelta(seconds=5)
    before = _archive_state(env)
    with _calls(
        HistoryObservedArchive.put.__code__,
        HistoryObservedModelService.forecast.__code__,
    ) as writes:
        # A new connection proves COMMIT visibility and genuine physical RO.
        with env.commands._read_session() as session:
            native = cast(sqlite3.Connection, session.connection().connection.driver_connection)
            total = native.total_changes
            assert session.connection().exec_driver_sql("PRAGMA query_only").scalar_one() == 1
            confirmed = env.local.forecasts.read_history_forecast(session, env.primary)
            assert native.total_changes == total
        with env.commands._read_session() as session:
            repeated = env.local.forecasts.prepare_history_forecast(session, env.primary)
    assert isinstance(repeated, RecordedHistoryForecastV1)
    assert saved == confirmed == repeated
    assert not writes and _archive_state(env) == before and _count(env) == 1
    assert saved.recorded_at < env.history.clock.now
    for change in (
        {"scope": spot_scope("ETH-USDT")},
        {"threshold": "0.05"},
        {"cutoff_at": env.primary.cutoff_at + timedelta(minutes=1)},
        {"expected_model_hash": "f" * 64},
        {"registration_id": "different-registration"},
    ):
        with pytest.raises(ValueError, match="request_conflict"):
            with env.commands._read_session() as session:
                env.local.forecasts.prepare_history_forecast(
                    session,
                    env.primary.model_copy(update=change),
                )
    with env.commands._read_session() as session:
        assert session.execute(text("SELECT * FROM old_frozen_control")).one() == (
            "old-frozen",
            "n=0",
            "old-forecast",
            "2026-11-06T00:01:00Z",
        )


def test_prepared_loaded_source_and_physical_transaction_changes_refuse(
    forecast_private: Any,
) -> None:
    env, draft = forecast_private, forecast_private.draft
    for changed in (
        replace(draft, issuer=object()),
        replace(draft, thread_id=draft.thread_id + 1),
        replace(draft, _auth=b"bad"),
        replace(draft, original_readset=draft.original_readset[:-1]),
        replace(draft, identity=draft.identity.model_copy(update={"threshold": "0.05"})),
        replace(draft, model_original_ref=draft.consumer_source_original_ref),
        replace(
            draft,
            forecast=draft.forecast.model_copy(
                update={"produced_at": draft.forecast.produced_at + timedelta(seconds=1)}
            ),
        ),
    ):
        _writer_refuses(env, changed)
    with _changed_bytes(env.mirror / CONSUMER_FILES[0]):
        _writer_refuses(env, draft)
    original = HistoryObservedForecastService._recheck.__code__
    try:
        HistoryObservedForecastService._recheck.__code__ = original.replace(co_filename="<foreign>")
        _writer_refuses(env, draft)
    finally:
        HistoryObservedForecastService._recheck.__code__ = original
    triggered: list[bool] = []

    def reopen(conn: Any, _cursor: Any, sql: str, *_rest: Any) -> None:
        if (
            sql.lstrip().upper().startswith("INSERT INTO MARKET_HISTORY_FORECASTS")
            and not triggered
        ):
            native = conn.connection.driver_connection
            native.execute("COMMIT")
            native.execute("BEGIN")
            triggered.append(True)

    event.listen(env.db.engine, "before_cursor_execute", reopen)
    try:
        _writer_refuses(env, draft)
    finally:
        event.remove(env.db.engine, "before_cursor_execute", reopen)
    assert triggered == [True]


def test_fresh_writer_checks_originals_revision_and_exit_without_window_math(
    forecast_private: Any,
) -> None:
    env, draft = forecast_private, forecast_private.draft
    for path in (
        Path(draft.forecast.feature_original_ref),
        Path(draft.model_original_ref),
        Path(draft.consumer_source_original_ref),
        _raw_path(env, draft.selected_closes[0].capture_ref),
    ):
        with _changed_bytes(path):
            _writer_refuses(env, draft)
    before = _count(env)
    with pytest.raises(ValueError):
        with env.commands._write_session() as session:
            capture, records, _ = env.page(
                env.history.root,
                job_id="forecast34-new-revision",
                phase="feature",
                cursor=str(int(env.pending.cutoff_at.timestamp() * 1000)),
                rows=[(draft.selected_closes[0].open_at, Decimal("100.01"))],
                completed=env.pending.cutoff_at - timedelta(seconds=1),
            )
            session.add(capture)
            session.execute(MarketCandleRecord.__table__.insert(), records)
            session.flush()
            env.local.forecasts.record_history_forecast(session, draft)
    assert _count(env) == before
    # Mutation happens after INSERT, so an entry-only check cannot pass this.
    for target in (
        Path(draft.forecast.feature_original_ref),
        _raw_path(env, draft.selected_closes[0].capture_ref),
    ):
        original, altered = target.read_bytes(), []

        def change_after_insert(
            _conn: Any,
            _cursor: Any,
            sql: str,
            *_rest: Any,
            captured_path: Path = target,
            captured_body: bytes = original,
            triggered: list[bool] = altered,
        ) -> None:
            if (
                sql.lstrip().upper().startswith("INSERT INTO MARKET_HISTORY_FORECASTS")
                and not triggered
            ):
                captured_path.write_bytes(captured_body + b" ")
                triggered.append(True)

        event.listen(env.db.engine, "after_cursor_execute", change_after_insert)
        try:
            with _calls(HistoryObservedDatasetBuilder.window.__code__) as writer_math:
                _writer_refuses(env, draft)
            assert not writer_math and altered == [True]
        finally:
            event.remove(env.db.engine, "after_cursor_execute", change_after_insert)
            target.write_bytes(original)


def test_reader_rejects_row_ref_body_and_clock_changes_without_writes(
    forecast_private: Any,
) -> None:
    env = forecast_private
    before = _archive_state(env)
    with env.commands._read_session() as session:
        row = session.get(ForecastRow, forecast_id(env.primary))
        assert row is not None
        original_ref, original_hash = row.original_ref, row.original_hash
    for field, changed in (
        ("payload_json", "{}"),
        ("payload_hash", "0" * 64),
        ("scope_id", spot_scope("ETH-USDT").scope_id),
        ("recorded_at", env.history.clock.now + timedelta(days=1)),
        ("original_ref", env.first.consumer_source_original_ref),
        ("original_hash", "0" * 64),
    ):
        with _calls(HistoryObservedArchive.put.__code__) as writes:
            with env.commands._write_session() as session:
                row = session.get(ForecastRow, forecast_id(env.primary), populate_existing=True)
                assert row is not None
                setattr(row, field, changed)
                session.flush()
                with pytest.raises(ValueError):
                    env.local.forecasts.read_history_forecast(session, env.primary)
                session.rollback()
        assert not writes
    for path in (
        Path(original_ref),
        Path(env.first.consumer_source_original_ref),
        Path(env.first.forecast.feature_original_ref),
    ):
        with _changed_bytes(path):
            with _calls(HistoryObservedArchive.put.__code__) as writes:
                with pytest.raises(ValueError):
                    env.local.read(env.primary)
            assert not writes
    assert original_hash and _archive_state(env) == before and _count(env) == 1


def test_unknown_commit_is_one_insert_then_read_only_confirmation_and_safe_downgrade(
    forecast_private: Any,
) -> None:
    env, commits, inserts = forecast_private, [], []

    class UnknownCommit(RuntimeError):
        pass

    class CommitThenRaise(Session):
        def commit(self) -> None:
            commits.append(True)
            super().commit()
            raise UnknownCommit("actual private COMMIT acknowledged outcome unknown")

    def insertion(_conn: Any, _cursor: Any, sql: str, *_rest: Any) -> None:
        if sql.lstrip().upper().startswith("INSERT INTO MARKET_HISTORY_FORECASTS"):
            inserts.append(True)

    factory = env.db.factory
    env.db.factory = sessionmaker(env.db.engine, class_=CommitThenRaise, expire_on_commit=False)
    event.listen(env.db.engine, "before_cursor_execute", insertion)
    try:
        with pytest.raises(UnknownCommit):
            with env.commands._write_session() as session:
                env.local.forecasts.record_history_forecast(session, env.draft)
    finally:
        env.db.factory = factory
        event.remove(env.db.engine, "before_cursor_execute", insertion)
    assert commits == inserts == [True] and _count(env) == 2
    with _calls(HistoryObservedArchive.put.__code__) as writes:
        confirmed = env.local.read(env.pending)
    assert not writes and confirmed.identity == env.pending
    assert confirmed.forecast == env.draft.forecast
    assert _count(env) == 2, "unknown COMMIT was retried instead of read-confirmed"
    with env.db.engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            with pytest.raises(ValueError, match="nonempty_originals_preserved"):
                env.migration.downgrade()
    assert _count(env) == 2


def test_cli_explicit_parse_existing_paths_no_initialize_or_network(
    forecast_private: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = forecast_private
    argv = [
        "read",
        "--database-path",
        str(env.history.root / "private.sqlite3"),
        "--data-directory",
        str(env.history.root),
        "--owner-id",
        env.primary.operator_owner_claim,
        "--request-id",
        env.primary.request_id,
        "--registration-id",
        env.primary.registration_id,
        "--scope-id",
        env.primary.scope.scope_id,
        "--model-hash",
        env.primary.expected_model_hash,
        "--model-known-at",
        env.primary.expected_model_known_at.isoformat(),
        "--cutoff-at",
        env.primary.cutoff_at.isoformat(),
        "--threshold",
        env.primary.threshold,
    ]
    command = cli.parse_command(argv)
    assert command.identity == env.primary and command.action == "read"
    for arguments in ([], ["record"], argv[:-2]):
        with pytest.raises(SystemExit):
            cli.parse_command(arguments)
    with pytest.raises(SystemExit):
        cli.parse_command([*argv[:-2], "--threshold", "0.03"])
    no_init = (Database.initialize.__code__, Database.create_all.__code__)
    before = _archive_state(env)
    with _calls(*no_init, HistoryObservedArchive.put.__code__) as unwanted:
        with pytest.raises(ValueError, match="absolute_paths_required"):
            cli.execute_command(replace(command, database_path=Path("relative.sqlite3")))
        missing = env.history.root / "must-not-be-created.sqlite3"
        with pytest.raises(FileNotFoundError):
            cli.execute_command(replace(command, database_path=missing))
        assert not missing.exists()
        real_builder = composition.build_history_observed_worker

        def private_clock(database: Database, *, owner_id: str) -> Any:
            return real_builder(database, owner_id=owner_id, clock=lambda: env.history.clock.now)

        monkeypatch.setattr(composition, "build_history_observed_worker", private_clock)
        receipt = cli.execute_command(command)
    assert not unwanted and _archive_state(env) == before
    assert receipt["read_only"] and receipt["originals_verified"]
    assert receipt["payload_hash"] and receipt["cutoff_at"] == env.primary.cutoff_at.isoformat()
    assert not receipt["owner_authenticated"] and not receipt["financial_qualified"]
    assert _count(env) == 2

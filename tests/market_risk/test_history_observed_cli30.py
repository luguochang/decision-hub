"""One pending CLI wiring control; no execution until Root grants a window.

Private SQLite only, zero received candles. The frozen empty dataset proves
command wiring, never real data or fit qualification. Existing command29/model24
nodes remain responsible for fit/preparation business behavior.
"""

from __future__ import annotations

import importlib
import json
import os
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from sqlalchemy import Table, create_engine, func, select
from sqlalchemy.orm import Session

from apps.hub_worker import composition
from apps.hub_worker import history_observed_cli as cli
from packages.kernel.decision_hub_kernel.persistence.db import Database, RawCaptureRecord
from packages.kernel.decision_hub_kernel.persistence.history_observed_records import (
    HistoryObservedAcquisitionJobRecord as Job,
)
from packages.kernel.decision_hub_kernel.persistence.history_observed_records import (
    HistoryObservedTrainingRecord as Record,
)
from packages.kernel.decision_hub_kernel.persistence.risk_records import MarketCandleRecord


def test_explicit_local_cli_real_registration_saved_freeze_and_uncertain_commit_no_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("CLI attempted migration, network or implicit acquisition")

    async def forbidden_async(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("CLI attempted network")

    monkeypatch.setattr(httpx.Client, "request", forbidden)
    monkeypatch.setattr(httpx.AsyncClient, "request", forbidden_async)
    monkeypatch.setattr(Database, "initialize", forbidden)
    monkeypatch.setattr(Database, "create_all", forbidden)
    from packages.kernel.decision_hub_kernel.application.history_observed_acquisition import (
        HistoryObservedAcquisition,
    )

    monkeypatch.setattr(HistoryObservedAcquisition, "process_one", forbidden_async)
    data = tmp_path / "private data"
    (data / "db").mkdir(parents=True)
    database_path = data / "db" / "private cli.sqlite3"
    engine = create_engine(f"sqlite+pysqlite:///{database_path}")
    try:
        for record in (Record, Job, MarketCandleRecord, RawCaptureRecord):
            cast(Table, record.__table__).create(engine)
        real_factory = composition.build_history_observed_worker
        now = datetime(2035, 10, 9, 12, tzinfo=UTC)

        def private_factory(database: Database, *, owner_id: str) -> Any:
            return real_factory(database, owner_id=owner_id, clock=lambda: now)

        monkeypatch.setattr(composition, "build_history_observed_worker", private_factory)
        monkeypatch.setenv("DECISION_HUB_DATA_DIR", "preserve prior environment")
        importlib.reload(cli)
        assert not list(data.rglob("*.json"))
        for arguments in ([], ["register"], ["fit", "--request-id", "R"]):
            with pytest.raises(SystemExit) as failure:
                cli.run(arguments)
            assert failure.value.code == 2
        with Session(engine) as session:
            assert session.scalar(select(func.count()).select_from(Record)) == 0

        def invoke(
            action: str, *extra: str, request: str = "exact registration request",
        ) -> dict[str, Any]:
            result = cli.run([
                action, "--database-path", str(database_path.resolve()),
                "--data-directory", str(data.resolve()), "--owner-id", "private-cli-owner",
                "--request-id", request, *extra,
            ])
            output = capsys.readouterr().out
            assert output.count("\n") == 1
            value = json.loads(output)
            assert result == (1 if value["outcome"] == "unconfirmed" else 0)
            assert str(data) not in output and "preserve prior environment" not in output
            assert value["owner_authenticated"] is False and value["owner_persisted"] is False
            assert value["ready_for_real_fit"] is False and value["financial_qualified"] is False
            return value

        first = invoke("register")
        second = invoke("register")
        assert first == second and first["committed"] is True
        registration = first["registration_id"]
        scope = first["scope_ids"][0]
        identity = ("--registration-id", registration)
        with Session(engine) as session:
            assert session.scalar(select(func.count()).select_from(Job)) == 180
        status = invoke("status", *identity)
        assert status["state_counts"] == {"held": 180} and status["attempts"] == 0
        assert not status["dataset_originals_verified"] and not status["model_originals_verified"]
        assert status["committed"] is False
        wrong = invoke("activate", *identity, request="different request")
        assert wrong["error_code"] == "history_observed_cli_registered_request_mismatch"
        wrong_scope = invoke("freeze", *identity, "--scope-id", "different scope")
        assert wrong_scope["error_code"] == "history_observed_cli_registered_scope_mismatch"
        status = invoke("status", *identity)
        assert status["state_counts"] == {"held": 180}
        active = invoke("activate", *identity)
        assert active["changed_held_jobs"] == 180 and active["collection_started"] is False
        frozen = invoke("freeze", *identity, "--scope-id", scope)
        assert frozen["source_admission"] == "not_evaluated"
        assert datetime.fromisoformat(frozen["data_information_asof"]) == now
        now += timedelta(minutes=2)
        reread = invoke("freeze", *identity, "--scope-id", scope)
        assert reread["dataset_hash"] == frozen["dataset_hash"]
        assert reread["data_information_asof"] == frozen["data_information_asof"]
        assert reread["sealed_at"] == frozen["sealed_at"]
        insufficient = invoke("fit", *identity, "--scope-id", scope)
        assert insufficient["outcome"] == "unconfirmed" and insufficient["committed"] is None
        with Session(engine) as session:
            assert session.scalar(select(func.count()).select_from(Record).where(
                Record.kind == "model",
            )) == 0
        assert os.environ["DECISION_HUB_DATA_DIR"] == "preserve prior environment"

        original_session = Database.session
        writes = 0

        @contextmanager
        def uncertain(database: Database) -> Generator[Session, None, None]:
            nonlocal writes
            writes += 1
            with original_session(database) as session:
                yield session
            raise RuntimeError("private SQL/body/signing credential must not reach stdout")

        monkeypatch.setattr(Database, "session", uncertain)
        unconfirmed = invoke("register", request="uncertain original registration")
        assert writes == 1 and unconfirmed["committed"] is None
        assert unconfirmed["automatic_retry"] is False
        assert unconfirmed["error_code"] == "history_observed_cli_operation_unconfirmed"
        with Session(engine) as session:
            assert session.scalar(select(func.count()).select_from(Record).where(
                Record.request_id == "uncertain original registration",
            )) == 1
    finally:
        engine.dispose()

"""Four prepared offline controls; no live DB, HTTP, Host or product models.

The successful pending node reuses actual Factory rows and the official saver.
Its stopped Runtime is an explicit fixture seam, never execution authority.
The tiny commit fixture certifies only initial-BEGIN classification/no replay.
"""

# This control intentionally exercises the exact private pending/fixture seams.
# pyright: reportPrivateUsage=false

from __future__ import annotations

import hashlib
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from apps.hub_worker import research as worker_module
from packages.kernel.decision_hub_kernel.application.dsh_sessions import DshSessionLinkService
from packages.kernel.decision_hub_kernel.application.research_context_selection_v4 import (
    ContextSelectionService,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    RunEventRecord,
    RunRecord,
    RunRoleOutputRecord,
)
from packages.kernel.decision_hub_kernel.persistence.sqlite_busy import (
    is_sqlite_initial_begin_busy,
)
from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError
from packages.runtime_adapters.dsh_runtime.web_runtime import DshWebResearchRuntime
from tests.dsh_native.test_execution_preparation_v2 import PreparedInputs
from tests.dsh_native.test_runtime_execution_v2 import PreparedHost
from tests.research.test_full_method_v2_bundle import forbid_external_connections
from tests.research.test_research_worker_preparation_pending import (
    _checkpoint_before_rpc,
    _full_sql_rows,
    _sql,
    _worker,
    inputs,
)

pytestmark = pytest.mark.usefixtures("forbid_external_connections")
assert inputs is not None and forbid_external_connections is not None


def _error(**changes: Any) -> AgentExecutionError:
    values: dict[str, Any] = dict(
        error_code="research_context_selection_pending",
        message="Context selection preparation was not confirmed",
        retryable=True,
        provider_id="dsh-web",
        origin="orchestration",
        cause_code="sqlite_initial_begin_busy",
    )
    values.update(changes)
    return AgentExecutionError(**values)


def _short_private_busy_timeout(native: Any, *_args: Any) -> None:
    assert isinstance(native, sqlite3.Connection)
    native.execute("PRAGMA busy_timeout=10")


def _capture_initial_busy(database: Database) -> AgentExecutionError:
    event.listen(database.engine, "checkout", _short_private_busy_timeout)
    try:
        with database.engine.connect() as blocker:
            blocker.exec_driver_sql("BEGIN IMMEDIATE")
            try:
                with pytest.raises(AgentExecutionError) as raised:
                    ContextSelectionService(database).select(
                        run_id="unexecuted-control", graph_round=1,
                        expected_lease_owner="unexecuted-control-owner",
                    )
                assert isinstance(raised.value.__cause__, OperationalError)
                assert is_sqlite_initial_begin_busy(raised.value.__cause__)
                assert worker_module._is_preparation_pending(raised.value, "execute")
                return raised.value
            finally:
                blocker.rollback()
    finally:
        event.remove(database.engine, "checkout", _short_private_busy_timeout)


@pytest.mark.asyncio
async def test_initial_selection_begin_busy_retains_authenticated_checkpoint_without_method(
    inputs: PreparedInputs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = PreparedHost(inputs)
    runtime = DshWebResearchRuntime(DshSessionLinkService(inputs.database), host)
    worker = _worker(inputs, runtime, tmp_path, "context-begin-owner", lease_seconds=240)
    try:
        await _checkpoint_before_rpc(inputs, worker, runtime, monkeypatch)
        before = _sql(inputs)
        assert "research_execution_method" not in before["roles"]
        error = _capture_initial_busy(inputs.database)
        assert _sql(inputs) == before, "denied initial BEGIN must execute no selection SQL"
        outcome = await worker._retain_preparation_pending(
            inputs.request.event_id, inputs.request.run_id, error,
        )
        after = _sql(inputs)
        assert outcome == ("pending", None)
        assert len(after["pending_events"]) == 1
        body = after["pending_events"][0]["body"]
        assert body["preparation"] is None
        assert body["original_checkpoint"]["run_id"] == inputs.request.run_id
        assert body["original_checkpoint"]["logical_round"] == 1
        assert body["diagnostic"]["cause_code"] == "sqlite_initial_begin_busy"
        assert body["diagnostic"]["error_code"] == "research_context_selection_pending"
        assert after["roles"] == before["roles"]
        assert {k: v for k, v in after.items() if k not in {"pending_events", "traces"}} == {
            k: v for k, v in before.items() if k not in {"pending_events", "traces"}
        }
        assert host.submit_calls == [] and host.result_calls == 0
    finally:
        await runtime.close()


def test_only_clean_initial_begin_matches_and_unknown_commit_is_never_replayed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = Database.__new__(Database)
    database.engine = create_engine(f"sqlite:///{tmp_path / 'initial-begin.sqlite'}")
    database.factory = sessionmaker(bind=database.engine, expire_on_commit=False)
    with database.engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE control_writes(value TEXT NOT NULL)")
    try:
        actual_error = _capture_initial_busy(database)
        begin_error = actual_error.__cause__
        assert isinstance(begin_error, OperationalError)
        original = cast(BaseException, begin_error.orig)
        for statement in ("BEGIN", "UPDATE control_writes SET value='x'", "COMMIT"):
            error = OperationalError(statement, {}, original)
            assert not is_sqlite_initial_begin_busy(error)
            assert not worker_module._is_preparation_pending(error, "execute")
        for changes in (
            {"error_code": "research_worker_failed"}, {"cause_code": "other_begin_busy"},
            {"message": "other prepare failure"}, {"provider_id": "other"},
            {"origin": "provider"}, {"retryable": False},
        ):
            assert not worker_module._is_preparation_pending(_error(**changes), "execute")
        assert not worker_module._is_preparation_pending(_error(), "commit")

        class DirtySession(Session):
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                super().__init__(*args, **kwargs)
                self.add(RunEventRecord(
                    run_id="control", sequence_no=1, event_type="control",
                    occurred_at=datetime.now(UTC), payload_json="{}",
                ))

        database.factory = cast(
            sessionmaker[Session], sessionmaker(bind=database.engine, class_=DirtySession)
        )
        event.listen(database.engine, "checkout", _short_private_busy_timeout)
        try:
            with database.engine.connect() as blocker:
                blocker.exec_driver_sql("BEGIN IMMEDIATE")
                try:
                    with pytest.raises(OperationalError):
                        ContextSelectionService(database).select()
                finally:
                    blocker.rollback()
        finally:
            event.remove(database.engine, "checkout", _short_private_busy_timeout)

        calls: list[str] = []

        class UnknownCommit(Session):
            def commit(self) -> None:
                calls.append("commit")
                super().commit()
                raise OperationalError("COMMIT", {}, original)

        def selected_body(_self: Any, session: Session, **_kwargs: Any) -> Any:
            calls.append("selection_body")
            session.connection().exec_driver_sql("INSERT INTO control_writes VALUES ('one')")
            return object()  # Commit-failure seam; never a valid selection receipt.

        database.factory = cast(
            sessionmaker[Session], sessionmaker(bind=database.engine, class_=UnknownCommit)
        )
        monkeypatch.setattr(ContextSelectionService, "select_in_session", selected_body)
        with pytest.raises(OperationalError) as commit_error:
            ContextSelectionService(database).select()
        assert not worker_module._is_preparation_pending(commit_error.value, "execute")
        assert calls == ["selection_body", "commit"]
        with database.engine.connect() as connection:
            assert connection.exec_driver_sql("SELECT value FROM control_writes").all() == [
                ("one",)
            ]
    finally:
        database.engine.dispose()


@pytest.mark.asyncio
async def test_new_wait_refuses_missing_checkpoint_changed_task_and_partial_method(
    inputs: PreparedInputs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = PreparedHost(inputs)
    runtime = DshWebResearchRuntime(DshSessionLinkService(inputs.database), host)
    worker = _worker(inputs, runtime, tmp_path, "context-proof-owner", lease_seconds=240)
    try:
        await _checkpoint_before_rpc(inputs, worker, runtime, monkeypatch)
        error = _capture_initial_busy(inputs.database)
        with inputs.database.session() as session:
            task = session.query(RunRoleOutputRecord).filter_by(
                run_id=inputs.request.run_id, role="research_task_request",
            ).one()
            original_id, original_payload = task.output_id, task.payload_json
            task.payload_json += " "
        before = _full_sql_rows(inputs)
        outcome = await worker._retain_preparation_pending(
            inputs.request.event_id, inputs.request.run_id, error,
        )
        assert outcome[0] == "ineligible"
        assert _full_sql_rows(inputs) == before
        with inputs.database.session() as session:
            task = session.get(RunRoleOutputRecord, original_id)
            assert task is not None
            task.payload_json = original_payload
            session.add(RunRoleOutputRecord(
                output_id="controlled-partial-method", run_id=inputs.request.run_id,
                role="research_execution_method", schema_version="research-execution-method.v3",
                payload_json="{}", payload_hash=hashlib.sha256(b"{}").hexdigest(),
                created_at=datetime.now(UTC),
            ))
        before = _full_sql_rows(inputs)
        outcome = await worker._retain_preparation_pending(
            inputs.request.event_id, inputs.request.run_id, error,
        )
        assert outcome[0] == "ineligible"
        assert _full_sql_rows(inputs) == before
        with inputs.database.session() as session:
            row = session.get(RunRoleOutputRecord, "controlled-partial-method")
            assert row is not None
            session.delete(row)
        original_checkpoint = worker.executor.checkpoint_path
        worker.executor.checkpoint_path = tmp_path / "missing-official-checkpoint.sqlite"
        before = _full_sql_rows(inputs)
        assert await worker._retain_preparation_pending(
            inputs.request.event_id, inputs.request.run_id, error,
        ) == ("ineligible", "research_preparation_checkpoint_missing")
        assert _full_sql_rows(inputs) == before
        worker.executor.checkpoint_path = original_checkpoint
        assert host.submit_calls == []
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_new_wait_has_no_false_ack_on_storage_busy_cancel_lease_or_deadline(
    inputs: PreparedInputs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = PreparedHost(inputs)
    runtime = DshWebResearchRuntime(DshSessionLinkService(inputs.database), host)
    worker = _worker(inputs, runtime, tmp_path, "context-permission-owner", lease_seconds=240)
    try:
        await _checkpoint_before_rpc(inputs, worker, runtime, monkeypatch)
        error = _capture_initial_busy(inputs.database)
        before = _full_sql_rows(inputs)
        event.listen(inputs.database.engine, "checkout", _short_private_busy_timeout)
        try:
            with inputs.database.engine.connect() as blocker:
                blocker.exec_driver_sql("BEGIN IMMEDIATE")
                try:
                    assert await worker._retain_preparation_pending(
                        inputs.request.event_id, inputs.request.run_id, error,
                    ) == ("storage_unavailable", "research_preparation_storage_busy")
                finally:
                    blocker.rollback()
        finally:
            event.remove(inputs.database.engine, "checkout", _short_private_busy_timeout)
        assert _full_sql_rows(inputs) == before
        for field, changed, expected in (
            ("status", "cancelled", "cancelled"),
            ("status", "failed", "lease_lost"),
            ("lease_owner", "foreign-owner", "lease_lost"),
            ("lease_expires_at", datetime.now(UTC) - timedelta(seconds=1), "lease_lost"),
        ):
            with inputs.database.session() as session:
                run = session.get(RunRecord, inputs.request.run_id)
                assert run is not None
                original = getattr(run, field)
                setattr(run, field, changed)
            before = _full_sql_rows(inputs)
            outcome = await worker._retain_preparation_pending(
                inputs.request.event_id, inputs.request.run_id, error,
            )
            assert outcome[0] == expected
            assert _full_sql_rows(inputs) == before
            with inputs.database.session() as session:
                run = session.get(RunRecord, inputs.request.run_id)
                assert run is not None
                setattr(run, field, original)
        expired_now = inputs.request.deadline_at + timedelta(seconds=1)
        with inputs.database.session() as session:
            run = session.get(RunRecord, inputs.request.run_id)
            assert run is not None
            run.lease_expires_at = expired_now + timedelta(seconds=240)
        before = _full_sql_rows(inputs)
        monkeypatch.setattr(worker_module, "utcnow", lambda: expired_now)
        outcome = await worker._retain_preparation_pending(
            inputs.request.event_id, inputs.request.run_id, error,
        )
        assert outcome == ("ineligible", "research_preparation_pending_deadline_elapsed")
        assert _full_sql_rows(inputs) == before
        assert host.submit_calls == []
    finally:
        await runtime.close()

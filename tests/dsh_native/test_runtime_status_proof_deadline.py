"""Finite proof/writer clocks and owned callbacks; no service or model."""
from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import OperationalError

from packages.contracts_py.decision_hub_contracts import DshSessionCompletion
from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError
from tests.dsh_native.test_runtime_operation_preparation import runtime


def boundary(instance, deadline, operation, writer):
    instance.status_confirmation_wait_seconds = 0.015
    instance.links.observe = writer
    instance.links.complete = writer
    instance._observations = ContextVar("status_proof_observations", default=None)
    submit = SimpleNamespace(deadline_at=deadline, request_hash="c" * 64,
                             run_id="run-preparation", deterministic_session_id="test-session",
                             generation=1)
    if operation == "status":
        return lambda: instance._observe_status(SimpleNamespace(), submit)
    completion = DshSessionCompletion(
        schema_version="dsh-session-completion.v1", run_id="run-preparation",
        dsh_session_id="test-session", generation=1, terminal_status="cancelled",
        last_seq=10, completed_at=datetime.now(UTC), trace_ref=None,
        result_ref=None, result_hash=None, error=None,
    )
    return lambda: instance._complete_original_result(completion, submit)


def busy(statement):
    original = sqlite3.OperationalError("database is locked")
    original.sqlite_errorcode = sqlite3.SQLITE_BUSY
    return OperationalError(statement, (), original)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["status", "completion"])
async def test_slow_original_proof_does_not_consume_first_writer_slice(monkeypatch, operation):
    instance, projector, counts, deadline = runtime(monkeypatch, delay=0.08)
    entries, ticks = [], []
    started = time.monotonic()

    def writer(*args, **kwargs):
        entries.append(time.monotonic())

    async def heartbeat():
        while not counts.exited.is_set():
            ticks.append(time.monotonic())
            await asyncio.sleep(0.005)

    beat = asyncio.create_task(heartbeat())
    try:
        await boundary(instance, deadline, operation, writer)()
        assert counts.proofs == 1 and len(entries) == 1
        assert entries[0] - started >= 0.06 and len(ticks) >= 3
    finally:
        await beat
        projector._executor.shutdown(wait=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["status", "completion"])
async def test_only_known_unacquired_begin_can_retry_after_slow_proof(monkeypatch, operation):
    instance, projector, counts, deadline = runtime(monkeypatch, delay=0.05)
    attempts, physical_writes = [], []

    def writer(*args, **kwargs):
        attempts.append(time.monotonic())
        if len(attempts) == 1:
            time.sleep(0.025)  # Only the original unacquired BEGIN is known.
            raise busy("BEGIN IMMEDIATE")
        physical_writes.append(True)

    try:
        await boundary(instance, deadline, operation, writer)()
        assert counts.proofs == 2 and len(attempts) == 2 and physical_writes == [True]
    finally:
        projector._executor.shutdown(wait=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["status", "completion"])
@pytest.mark.parametrize("failure", ["permission", "deadline"])
async def test_original_permission_and_deadline_still_refuse_any_writer(
    monkeypatch, operation, failure,
):
    instance, projector, counts, deadline = runtime(
        monkeypatch, delay=0.08,
        invalid="research_execution_run_not_active" if failure == "permission" else None,
    )
    if failure == "deadline":
        deadline = datetime.now(UTC) + timedelta(seconds=0.025)
        instance._binding_deadline.set(deadline)
    writes = []
    try:
        with pytest.raises(AgentExecutionError) as caught:
            await boundary(instance, deadline, operation, lambda *a, **k: writes.append(True))()
        assert counts.proofs == 1 and writes == []
        if failure == "permission":
            assert caught.value.cause_code == "research_execution_run_not_active"
            assert not caught.value.retryable
        else:
            assert datetime.now(UTC) >= deadline
            assert caught.value.cause_code == "dsh_web_deadline_elapsed"
    finally:
        projector._executor.shutdown(wait=True)


@pytest.mark.asyncio
async def test_cancel_during_status_proof_drains_original_callback(monkeypatch):
    instance, projector, counts, deadline = runtime(monkeypatch, delay=0.12)
    writes = []
    call = boundary(instance, deadline, "status", lambda *a, **k: writes.append(True))
    task = asyncio.create_task(call())
    try:
        while not counts.entered.is_set():
            await asyncio.sleep(0.001)
        await asyncio.sleep(0.03)  # Beyond the first 10ms observation window.
        task.cancel()
        await asyncio.sleep(0.01)
        task.cancel()
        assert not task.done() and not counts.exited.is_set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert counts.exited.is_set() and counts.proofs == 1 and writes == []
    finally:
        projector._executor.shutdown(wait=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["status", "completion"])
async def test_cancel_during_writer_drains_same_sqlite_session(
    tmp_path, monkeypatch, operation,
):
    instance, projector, counts, deadline = runtime(monkeypatch, delay=0.025)
    path = tmp_path / "owned-writer.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE receipts (id INTEGER PRIMARY KEY)")
    entered, release, closed = threading.Event(), threading.Event(), threading.Event()
    writes = []

    def writer(*args, **kwargs):
        connection = sqlite3.connect(path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("INSERT INTO receipts VALUES (1)")
            writes.append(True)
            entered.set()
            assert release.wait(2)
            connection.commit()
        finally:
            connection.close()
            closed.set()

    task = asyncio.create_task(boundary(instance, deadline, operation, writer)())
    try:
        while not entered.is_set():
            await asyncio.sleep(0.001)
        task.cancel()
        await asyncio.sleep(0.01)
        task.cancel()
        assert not task.done() and not closed.is_set()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert closed.is_set() and writes == [True] and counts.proofs == 1
        with sqlite3.connect(path) as connection:
            assert connection.execute("SELECT count(*) FROM receipts").fetchone()[0] == 1
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        projector._executor.shutdown(wait=True)


@pytest.mark.asyncio
async def test_unknown_status_commit_failure_is_not_replayed(monkeypatch):
    instance, projector, counts, deadline = runtime(monkeypatch, delay=0.025)
    writes = []

    def uncertain(*args, **kwargs):
        writes.append(True)
        raise busy("COMMIT")

    try:
        with pytest.raises(OperationalError) as caught:
            await boundary(instance, deadline, "status", uncertain)()
        assert caught.value.statement == "COMMIT"
        assert counts.proofs == 1 and writes == [True]
    finally:
        projector._executor.shutdown(wait=True)


@pytest.mark.asyncio
async def test_later_proof_keeps_original_deadline_without_renewing_network_grace(monkeypatch):
    instance, projector, counts, deadline = runtime(monkeypatch, delay=0.06)
    instance._observations = ContextVar("retry_proof_observations", default=None)
    calls = []

    async def unavailable():
        calls.append(time.monotonic())
        raise AgentExecutionError(
            "dsh_host_unavailable", "Controlled original Host error", retryable=True,
            origin="transport", cause_code="controlled_transport_failure",
        )

    try:
        with pytest.raises(AgentExecutionError) as caught:
            await instance._retry_host_call(
                unavailable, deadline_at=deadline, operation="readiness",
                network_grace_seconds=0.035,
            )
        assert counts.proofs == 2 and len(calls) == 1
        assert caught.value.error_code == "dsh_host_unavailable"
        assert caught.value.cause_code == "controlled_transport_failure"
    finally:
        projector._executor.shutdown(wait=True)

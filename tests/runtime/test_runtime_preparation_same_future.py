"""Draft wait-boundary gates: real executor/Future, no DB/Host/model/request.

Run only against the reviewed candidate modules (or after approved application).
The session below is an explicit transaction-lifetime stand-in; these tests do
not authenticate originals or establish that the long writer has been removed.
"""
from __future__ import annotations

import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError
from packages.runtime_adapters.dsh_runtime import cancellation_projection as projection
from packages.runtime_adapters.dsh_runtime import web_runtime as web


class _Sessions:
    """No database: record the real writer callback's context lifetime only."""

    def __init__(self):
        self.exited = threading.Event()
        self.statements = []
        self.completed = 0
        self.failed = 0

    @contextmanager
    def session(self):
        connection = SimpleNamespace(exec_driver_sql=self.statements.append)
        try:
            yield SimpleNamespace(connection=lambda: connection)
        except BaseException:
            self.failed += 1
            raise
        else:
            self.completed += 1
        finally:
            self.exited.set()


class _Projector(projection.DshCancellationProjector):
    def __init__(self):
        self._slots = threading.BoundedSemaphore(2)
        self._executor = ThreadPoolExecutor(max_workers=2)
        self._closed = False
        self._observations = {}
        self._observation_lock = threading.Lock()
        self.unsupported = None
        self.sessions = _Sessions()


async def _until(predicate):
    stop = asyncio.get_running_loop().time() + 2
    while not predicate():
        assert asyncio.get_running_loop().time() < stop, "controlled stage did not arrive"
        await asyncio.sleep(0.005)


def _records(caplog):
    prefix = "research_preparation_confirmation "
    return [json.loads(record.getMessage()[len(prefix):]) for record in caplog.records
            if record.getMessage().startswith(prefix)]


def _entry(monkeypatch, *, window=0.01, deadline_scope=None, invalid=None):
    projector = _Projector()
    runtime = web.DshWebResearchRuntime.__new__(web.DshWebResearchRuntime)
    runtime.cancellation_projector = projector
    runtime.preparation_confirmation_wait_seconds = window
    runtime.skill_preparation = None
    runtime.runtime_id = "dsh"
    runtime.links = SimpleNamespace(database=None)
    runtime._profile_for = lambda request: "controlled-profile"
    now = datetime.now(UTC)
    long_deadline = now + timedelta(seconds=5)
    short_deadline = now + timedelta(seconds=0.15)
    request = SimpleNamespace(
        run_id="run-preparation-wait-draft",
        deadline_at=short_deadline if deadline_scope == "request" else long_deadline,
        output_schema_ref="controlled.wait-boundary.only",
    )
    anchor = SimpleNamespace(
        generation=1,
        deadline_at=short_deadline if deadline_scope == "expected" else long_deadline,
    )
    turn = SimpleNamespace(
        anchor=object(), skill_repair_policy=None, observation_context=None,
        submit=SimpleNamespace(generation=1, deadline_at=long_deadline),
    )
    for name, default in (
        ("_task_anchor", anchor), ("_selection", None), ("_skill_repair_policy", None),
        ("_proof_operation_deadline", short_deadline if deadline_scope == "operation" else None),
        ("_observations", None),
    ):
        setattr(runtime, name, ContextVar(name, default=default))
    entered, release = threading.Event(), threading.Event()
    counts = SimpleNamespace(prepare=0, host_boundary=0, anchors=[])

    def controlled(_session, *_args, **_kwargs):
        counts.prepare += 1
        entered.set()
        assert release.wait(5), "test failed to release native callback"
        if invalid is not None:
            raise ValueError(invalid)
        return turn

    monkeypatch.setattr(web, "prepare_runtime_turn_in_session", controlled)

    async def invoke():
        try:
            result = await runtime._prepare_v2_turn(
                request, SimpleNamespace(upstream_identity="controlled"), "controlled",
            )
            # Stand-in for the immediately following Host boundary. No client
            # exists and no HTTP can be sent by this controlled test.
            counts.host_boundary += 1
            return result
        finally:
            counts.anchors.append(runtime._task_anchor.get())

    return SimpleNamespace(
        runtime=runtime, projector=projector, anchor=anchor, turn=turn,
        entered=entered, release=release, counts=counts, invoke=invoke,
        deadline=short_deadline,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [None, "research_runtime_original_task_changed"])
async def test_late_original_writer_is_observed_once_and_preserves_real_rejection(
    monkeypatch, caplog, invalid,
):
    item = _entry(monkeypatch, invalid=invalid)
    task = asyncio.create_task(item.invoke())
    try:
        await _until(item.entered.is_set)
        await _until(lambda: any(row["state"] == "pending" for row in _records(caplog)))
        assert not task.done()
        assert item.counts.prepare == 1 and item.counts.host_boundary == 0
        item.release.set()
        if invalid is None:
            assert await task is item.turn
            assert item.counts.anchors == [item.turn.anchor]
            assert item.counts.host_boundary == 1
            assert item.projector.sessions.completed == 1
        else:
            with pytest.raises(AgentExecutionError) as caught:
                await task
            assert caught.value.error_code == "research_method_binding_failed"
            assert caught.value.cause_code == invalid
            assert caught.value.retryable is False
            assert item.counts.anchors == [item.anchor]
            assert item.counts.host_boundary == 0
            assert item.projector.sessions.failed == 1
        assert item.projector.sessions.exited.is_set()
        assert item.projector.sessions.statements.count("BEGIN IMMEDIATE") == 1
        assert item.counts.prepare == 1
        records = _records(caplog)
        assert len({row["operation_id"] for row in records}) == 1
        assert sum(row["state"] == "pending" for row in records) == 1
        assert sum(row["state"] == ("ready" if invalid is None else "invalid")
                   for row in records) == 1
        assert any(row["state"] == "begin_acquired" for row in records)
        if invalid is None:
            assert any(row["state"] == "operation_completed" for row in records)
            assert any(row["state"] == "commit_done" for row in records)
    finally:
        item.release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        item.projector._executor.shutdown(wait=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", [
    "cancel_initial", "cancel_pending", "deadline_expected", "deadline_request",
    "deadline_operation",
])
async def test_cancellation_and_original_deadline_drain_exact_writer_without_anchor_or_host(
    monkeypatch, caplog, case,
):
    scope = case.removeprefix("deadline_") if case.startswith("deadline_") else None
    item = _entry(monkeypatch, window=1 if case == "cancel_initial" else 0.01,
                  deadline_scope=scope)
    task = asyncio.create_task(item.invoke())
    try:
        await _until(item.entered.is_set)
        if case != "cancel_initial":
            await _until(lambda: any(row["state"] == "pending" for row in _records(caplog)))
        if case.startswith("cancel_"):
            task.cancel()
            await asyncio.sleep(0.03)
            task.cancel()  # Cleanup must also withstand repeated cancellation.
        else:
            await _until(lambda: datetime.now(UTC) >= item.deadline)
        await asyncio.sleep(0.03)
        assert not task.done(), "ownership escaped while the accepted writer was still running"
        assert not item.projector.sessions.exited.is_set()
        assert item.counts.prepare == 1 and item.counts.host_boundary == 0
        item.release.set()
        if case.startswith("cancel_"):
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(AgentExecutionError) as caught:
                await task
            assert caught.value.error_code == "provider_timeout"
            assert caught.value.cause_code == "dsh_web_preparation_deadline_elapsed"
        assert item.projector.sessions.exited.is_set()
        # A late native completion is not falsely reported as a rollback.
        assert item.projector.sessions.completed == 1
        assert item.projector.sessions.statements.count("BEGIN IMMEDIATE") == 1
        assert item.counts.prepare == 1 and item.counts.host_boundary == 0
        assert item.counts.anchors == [item.anchor]
        records = _records(caplog)
        expected_state = (
            "cancelled_after_native_exit" if case.startswith("cancel_") else "deadline_elapsed"
        )
        assert sum(row["state"] == expected_state for row in records) == 1
        assert any(row["state"] == "commit_done" for row in records)
        assert len({row["operation_id"] for row in records}) == 1
    finally:
        item.release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        item.projector._executor.shutdown(wait=True)

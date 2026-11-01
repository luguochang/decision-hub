"""Finite native-Future/RPC clock gates for the normal18 failure branch."""
from __future__ import annotations

import asyncio
import threading
import time
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError
from packages.runtime_adapters.dsh_runtime import web_runtime as web
from tests.dsh_native.test_runtime_prehost_anchor_wait import ReadOnlyProjector


def runtime(monkeypatch, *, delay=0.08, invalid=None):
    instance = web.DshWebResearchRuntime.__new__(web.DshWebResearchRuntime)
    projector = ReadOnlyProjector()
    instance.cancellation_projector = projector
    instance.links = SimpleNamespace(database=object())
    instance.proof_read_wait_seconds = 0.01
    instance.poll_interval_seconds = 0.001
    instance.readiness_grace_seconds = 0.02
    deadline = datetime.now(UTC) + timedelta(seconds=1)
    anchor = web.RuntimeTaskAnchor('run-preparation', 'event-preparation', None,
        'a' * 64, bundle_hash='b' * 64)
    for name, default in (
        ('_task_anchor', anchor), ('_selection', None), ('_binding_deadline', deadline),
        ('_proof_operation_deadline', None),
    ):
        setattr(instance, name, ContextVar(name, default=default))
    counts = SimpleNamespace(proofs=0, calls=0, entered=threading.Event(), exited=threading.Event())

    def authenticate(_database, actual, selected, *, deadline_at, allow_cancelled):
        assert actual is anchor and selected is None and not allow_cancelled
        counts.proofs += 1
        counts.entered.set()
        try:
            time.sleep(delay)
            if invalid:
                raise ValueError(invalid)
        finally:
            counts.exited.set()

    async def ready():
        counts.calls += 1
        return SimpleNamespace(ready=True)

    monkeypatch.setattr(web, 'authenticate_runtime_binding', authenticate)
    instance.client = SimpleNamespace(readiness=ready)
    return instance, projector, counts, deadline


@pytest.mark.asyncio
async def test_readiness_clock_starts_after_same_future_proof_and_loop_remains_live(monkeypatch):
    instance, projector, counts, deadline = runtime(monkeypatch)
    ticks = []
    async def heartbeat():
        while not counts.exited.is_set():
            ticks.append(time.monotonic())
            await asyncio.sleep(0.005)
    beat = asyncio.create_task(heartbeat())
    try:
        result = await instance._wait_for_readiness(deadline_at=deadline)
        assert result.ready and (counts.proofs, counts.calls) == (1, 1)
        assert len(ticks) >= 3
    finally:
        await beat
        projector._executor.shutdown(wait=True)


@pytest.mark.asyncio
async def test_late_invalid_proof_cannot_send_a_host_operation(monkeypatch):
    instance, projector, counts, deadline = runtime(
        monkeypatch, invalid='research_runtime_original_task_changed')
    try:
        with pytest.raises(AgentExecutionError) as caught:
            await instance._wait_for_readiness(deadline_at=deadline)
        assert caught.value.cause_code == 'research_runtime_original_task_changed'
        assert not caught.value.retryable
        assert (counts.proofs, counts.calls) == (1, 0)
    finally:
        projector._executor.shutdown(wait=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('cancel_after_window', [False, True])
async def test_cancel_drains_the_original_native_callback(monkeypatch, cancel_after_window):
    instance, projector, counts, deadline = runtime(monkeypatch, delay=0.12)
    try:
        task = asyncio.create_task(instance._wait_for_readiness(deadline_at=deadline))
        while not counts.entered.is_set():
            await asyncio.sleep(0.001)
        if cancel_after_window:
            await asyncio.sleep(0.035)
        task.cancel()
        await asyncio.sleep(0.01)
        assert not task.done() and not counts.exited.is_set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert counts.exited.is_set() and (counts.proofs, counts.calls) == (1, 0)
    finally:
        projector._executor.shutdown(wait=True)


@pytest.mark.asyncio
async def test_original_deadline_is_not_replenished_by_readiness_grace(monkeypatch):
    instance, projector, counts, _deadline = runtime(monkeypatch, delay=0.12)
    deadline = datetime.now(UTC) + timedelta(seconds=0.025)
    instance._binding_deadline.set(deadline)
    try:
        with pytest.raises(AgentExecutionError):
            await instance._wait_for_readiness(deadline_at=deadline)
        assert datetime.now(UTC) >= deadline
        assert counts.proofs == 1 and counts.calls == 0
    finally:
        projector._executor.shutdown(wait=True)

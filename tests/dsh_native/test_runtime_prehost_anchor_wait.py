"""Pre-Host RO Future completion; no HTTP, model, SQLite writer or product start."""
from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError
from packages.runtime_adapters.dsh_runtime import cancellation_projection as projection
from packages.runtime_adapters.dsh_runtime import web_runtime as web
from tests.dsh_native.test_dsh_web_runtime import _request


class ReadOnlyProjector(projection.DshCancellationProjector):
    """Exercise the actual shielded Future issuer, with a pure read callback."""

    def __init__(self):
        self._slots = threading.BoundedSemaphore(2)
        self._executor = ThreadPoolExecutor(max_workers=2)
        self._closed = False
        self._observations = {}
        self._observation_lock = threading.Lock()
        self.unsupported = None
        self.read_calls = 0

    async def read(self, operation, *, timeout_seconds=0.25, observation_scope=None):
        self.read_calls += 1
        return await self._call(
            lambda: operation(None), timeout_seconds, read_only=True,
            observation_scope=observation_scope,
        )


def entry(monkeypatch, *, delay=0.08, invalid=None, probe_delay=0):
    projector = ReadOnlyProjector()
    instance = web.DshWebResearchRuntime.__new__(web.DshWebResearchRuntime)
    instance.cancellation_projector = projector
    instance.proof_read_wait_seconds = 0.02
    for name, default in (
        ("_selection", None), ("_task_anchor", None), ("_skill_repair_policy", None),
        ("_binding_deadline", None), ("_observations", None),
    ):
        setattr(instance, name, ContextVar(name, default=default))
    counts = SimpleNamespace(probes=0, proofs=0, host=0)
    anchor = web.RuntimeTaskAnchor(
        "run-prehost", "event-prehost", "snapshot-prehost", "a" * 64,
        bundle_hash="b" * 64,
    )

    def probe(_session, run_id):
        assert run_id == anchor.run_id
        counts.probes += 1
        time.sleep(probe_delay)
        return True

    def proof(_session, run_id):
        assert run_id == anchor.run_id
        counts.proofs += 1
        time.sleep(delay)
        if invalid:
            raise ValueError(invalid)
        return anchor

    async def host_boundary(_request, _sink, *, track_submission):
        del track_submission
        assert instance._task_anchor.get() is anchor
        counts.host += 1
        return "transport-result"

    async def mapped(result):
        return result

    monkeypatch.setattr(web, "_uses_native_execution", probe)
    monkeypatch.setattr(web, "read_runtime_task_anchor", proof)
    instance._execute = host_boundary
    instance._mapped_observation = mapped
    return instance, projector, counts


async def execute(instance, *, seconds=0.5):
    request = _request("run-prehost", deadline_seconds=seconds)
    return await instance._execute_entry(request, None, require_observed=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("probe_delay", [0, 0.08])
async def test_initial_ro_proof_can_finish_after_one_observation_window(monkeypatch, probe_delay):
    instance, projector, counts = entry(monkeypatch, probe_delay=probe_delay)
    try:
        result = await execute(instance)
        assert result == "transport-result"
        assert (counts.probes, counts.proofs, counts.host) == (1, 1, 1)
        # No second submission of either accepted native Future.
        assert projector.read_calls == 2
    finally:
        projector._executor.shutdown(wait=True)


@pytest.mark.asyncio
async def test_late_true_invalid_never_enters_host(monkeypatch):
    instance, projector, counts = entry(
        monkeypatch, invalid="research_runtime_original_task_changed",
    )
    try:
        with pytest.raises(AgentExecutionError) as raised:
            await execute(instance)
        assert raised.value.cause_code == "research_runtime_original_task_changed"
        assert raised.value.retryable is False
        assert (counts.probes, counts.proofs, counts.host) == (1, 1, 0)
        assert projector.read_calls == 2
    finally:
        projector._executor.shutdown(wait=True)


@pytest.mark.asyncio
async def test_original_absolute_deadline_never_enters_host(monkeypatch):
    instance, projector, counts = entry(monkeypatch, delay=0.15)
    try:
        deadline = datetime.now(UTC) + timedelta(seconds=0.05)
        with pytest.raises(AgentExecutionError) as raised:
            request = _request("run-prehost").model_copy(update={"deadline_at": deadline})
            await instance._execute_entry(request, None, require_observed=True)
        assert raised.value.cause_code == "cancellation_projection_wait_elapsed"
        assert datetime.now(UTC) >= deadline
        assert counts.host == 0 and counts.proofs == 1
        assert projector.read_calls == 2
        # A late completed RO callback cannot submit a Host operation itself.
        await asyncio.sleep(0.16)
        assert counts.host == 0 and counts.proofs == 1
    finally:
        projector._executor.shutdown(wait=True)


@pytest.mark.asyncio
async def test_later_binding_keeps_its_existing_single_proof_budget(monkeypatch):
    instance, projector, counts = entry(monkeypatch)
    deadline = datetime.now(UTC) + timedelta(seconds=0.5)

    def later_proof(_session):
        counts.proofs += 1
        time.sleep(0.15)
        return "current-proof"

    try:
        outcome = await instance._read_binding_with_contention_wait(
            later_proof, timeout_seconds=0.02, deadline_at=deadline,
        )
        assert outcome.state == "pending"
        assert datetime.now(UTC) < deadline
        assert counts.proofs == 1 and projector.read_calls == 1
        assert counts.host == 0
    finally:
        projector._executor.shutdown(wait=True)

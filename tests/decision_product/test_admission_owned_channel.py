"""Normal scheduler registration, renewal isolation and owned shutdown; no DB/network."""

from __future__ import annotations

import asyncio
import threading
from contextlib import suppress
from types import SimpleNamespace

import pytest

from packages.kernel.decision_hub_kernel.application import risk_research_admission
from packages.kernel.decision_hub_kernel.application import scheduler as scheduler_module
from packages.kernel.decision_hub_kernel.application.risk_admission_wait import observe_owned
from packages.kernel.decision_hub_kernel.application.scheduler import RealtimeScheduler


def _scheduler(action, observer=None):
    value = RealtimeScheduler(SimpleNamespace(), risk_research_tick=action, channel_observer=observer)

    async def idle(**_kwargs):
        return 0

    for name in (
        "_poll_sources", "_discovery_recovery", "_windows", "_runs", "_outcomes",
        "_observations", "_assessments", "_effect_tracking_requests", "_calibrations",
        "_notifications", "_risk_acquisition", "_risk_evaluations", "_risk_shadow",
        "_risk_memory", "_risk_enhancements", "_history_hourly", "_history_outcome",
    ):
        setattr(value, name, idle)
    return value


async def _event(event):
    assert await asyncio.to_thread(event.wait, 3), "bounded test barrier was not reached"


async def _stop(task):
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_normal_registration_keeps_admission_alive_and_other_deadlines(monkeypatch):
    # Immediate scaled deadlines expose the branch without a real 45-second wait.
    original_timeout = asyncio.timeout

    def scaled_timeout(seconds):
        return original_timeout(0 if seconds in {30, 45} else seconds)

    monkeypatch.setattr(scheduler_module.asyncio, "timeout", scaled_timeout)
    finished, monitor_finished = threading.Event(), threading.Event()
    calls, admissions, frames, action_threads = [], [], [], []

    async def admission():
        calls.append("prepare")
        action_threads.append(threading.get_ident())
        await asyncio.sleep(0)
        admissions.append("admit")
        return 1

    def observer(name, error, _elapsed, timeout, expired):
        frames.append((name, error, timeout, expired))
        if name == "risk_research_admission":
            finished.set()
        if name == "risk_monitor":
            monitor_finished.set()

    async def blocked_monitor():
        await asyncio.Event().wait()

    scheduler = _scheduler(admission, observer)
    scheduler._risk_evaluations = blocked_monitor
    task = asyncio.create_task(scheduler.run_forever())
    try:
        await _event(finished)
        await _event(monitor_finished)
        assert admissions == ["admit"]
        assert calls == ["prepare"]
        assert action_threads == [action_threads[0]]
        assert action_threads[0] != threading.get_ident()
        risk = next(row for row in frames if row[0] == "risk_research_admission")
        assert risk[1:] == (None, None, False)
        monitor = next(row for row in frames if row[0] == "risk_monitor")
        assert isinstance(monitor[1], TimeoutError)
        assert monitor[2:] == (30, True)
    finally:
        await _stop(task)


@pytest.mark.asyncio
async def test_synchronous_sibling_cannot_starve_admission_claim_renewal(monkeypatch):
    monkeypatch.setattr(risk_research_admission, "CLAIM_RENEW_INTERVAL_SECONDS", 0.001)
    admission_started, sibling_blocked = threading.Event(), threading.Event()
    renewed, admission_finished = threading.Event(), threading.Event()
    sibling_saw_renewal, admission_loops = [], []

    def renew(_key, _generation):
        renewed.set()
        return True

    lease = SimpleNamespace(_observe_owned=observe_owned, _renew_claim=renew)

    async def admission():
        admission_loops.append(asyncio.get_running_loop())
        admission_started.set()
        await _event(sibling_blocked)
        stop, lost = asyncio.Event(), asyncio.Event()
        renewer = asyncio.create_task(
            risk_research_admission.RiskResearchAdmissionService._keep_claim(
                lease, "isolated-request", 1, stop, lost
            )
        )
        try:
            await _event(renewed)
            assert not lost.is_set()
        finally:
            stop.set()
            await renewer
            admission_finished.set()
        return 1

    async def synchronous_sibling():
        await _event(admission_started)
        sibling_blocked.set()
        # Intentionally block the scheduler loop until the real renewal coroutine
        # proves progress on the owned admission loop.
        sibling_saw_renewal.append(renewed.wait(3))
        return 0

    scheduler = _scheduler(admission)
    scheduler._poll_sources = synchronous_sibling
    task = asyncio.create_task(scheduler.run_forever())
    try:
        await _event(admission_finished)
        assert sibling_saw_renewal == [True]
        assert admission_loops[0] is not asyncio.get_running_loop()
    finally:
        sibling_blocked.set()
        await _stop(task)


@pytest.mark.asyncio
async def test_shutdown_joins_accepted_work_and_closes_the_owned_loop():
    accepted, release, completed = threading.Event(), threading.Event(), threading.Event()
    loops, calls = [], []

    def work():
        accepted.set()
        assert release.wait(3), "test must release accepted work"
        completed.set()

    async def admission():
        calls.append("prepare")
        loops.append(asyncio.get_running_loop())
        await observe_owned(asyncio.to_thread(work))
        return 1

    scheduler = _scheduler(admission)
    task = asyncio.create_task(scheduler.run_forever())
    try:
        await _event(accepted)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        assert not completed.is_set()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert completed.is_set()
        assert loops[0].is_closed()
        assert calls == ["prepare"]
    finally:
        release.set()
        if not task.done():
            await _stop(task)

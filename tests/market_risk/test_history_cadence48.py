"""Private dispatch controls; synthetic ticks are not model/financial proof."""

from __future__ import annotations

import asyncio
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from apps.hub_worker.history_cadence48 import (
    HistoryCadence48,
    install_history_cadence48_on_realtime,
)
from apps.hub_worker.history_hourly41 import HistoryHourly41
from apps.hub_worker.history_hourly_v2 import HistoryHourlyV2
from packages.kernel.decision_hub_kernel.application.scheduler import RealtimeScheduler


class HourlyBoundary:
    def __init__(self, clock, *, slots=None, fences=None):
        self.clock = clock
        self.originals = SimpleNamespace(now=lambda: self.clock[0])
        self.calls = []
        self.slots = {} if slots is None else slots
        self.fences = set() if fences is None else fences

    async def tick(self, budget):
        self.calls.append((self.clock[0], budget))
        hour = self.clock[0].replace(minute=0, second=0, microsecond=0)
        for scope in ("BTC", "ETH"):
            key = (hour, scope)
            if key not in self.slots and scope not in self.fences:
                self.slots[key] = "first_saved"
                self.clock[0] += timedelta(seconds=6)
                return 1
        return 0


def initial_clock():
    return [datetime(2026, 10, 10, 2, 0, 0, tzinfo=UTC)]


@pytest.mark.asyncio
async def test_same_cycle_two_scopes_without_intervening_minute_and_idle_full_reads_zero():
    clock = initial_clock()
    hourly = HourlyBoundary(clock)
    cadence = HistoryCadence48(hourly)
    assert await cadence.tick_hourly() == 2
    assert [row[0].second for row in hourly.calls] == [0, 6]
    assert all(1 <= row[1] <= 90 for row in hourly.calls)
    for _ in range(100):
        assert await cadence.tick_hourly() == 0
    assert len(hourly.calls) == 2  # No idle source/auth calls or DB reads.
    clock[0] = datetime(2026, 10, 10, 3, 0, 0, tzinfo=UTC)
    assert await cadence.tick_hourly() == 2
    assert len(hourly.calls) == 4


@pytest.mark.asyncio
async def test_wall_clock_regression_does_not_schedule_historical_missing_hours():
    clock = initial_clock()
    hourly = HourlyBoundary(clock)
    cadence = HistoryCadence48(hourly)
    assert await cadence.tick_hourly() == 2
    clock[0] -= timedelta(hours=1)
    assert await cadence.tick_hourly() == 0
    assert len(hourly.calls) == 2


@pytest.mark.asyncio
async def test_startup_existing_first_slots_and_unknown_fence_remain_authoritative():
    clock = initial_clock()
    hour = clock[0]
    slots = {(hour, "BTC"): "first_saved"}
    hourly = HourlyBoundary(clock, slots=slots)
    assert await HistoryCadence48(hourly).tick_hourly() == 1
    assert slots[(hour, "BTC")] == "first_saved"
    assert len(slots) == 2
    # Restart only wakes the same official first-slot reader; it creates no
    # second result and cannot reopen the synthetic unresolved BTC fence.
    blocked = HourlyBoundary(initial_clock(), fences={"BTC"})
    assert await HistoryCadence48(blocked).tick_hourly() == 1
    assert all(key[1] != "BTC" for key in blocked.slots)
    assert await HistoryCadence48(blocked).tick_hourly() == 0
    assert blocked.fences == {"BTC"}


@pytest.mark.asyncio
async def test_hour_crossing_cannot_dispatch_second_scope_with_a_historical_cutoff():
    clock = [datetime(2026, 10, 10, 2, 59, 57, tzinfo=UTC)]
    hourly = HourlyBoundary(clock)
    cadence = HistoryCadence48(hourly)
    assert await cadence.tick_hourly() == 1
    assert len(hourly.calls) == 1
    assert all(key[0].hour == 2 for key in hourly.slots)
    assert await cadence.tick_hourly() == 2  # The new hour is a new normal wake.
    assert all(row[0] >= hourly.calls[0][0] for row in hourly.calls)


@pytest.mark.asyncio
async def test_unknown_error_is_visible_and_not_replayed_by_idle_wakes():
    clock = initial_clock()
    hourly = HourlyBoundary(clock)

    async def unknown(budget):
        hourly.calls.append((clock[0], budget))
        hourly.fences.add("BTC")
        raise ValueError("history_hourly_reservation_commit_unconfirmed")

    hourly.tick = unknown
    cadence = HistoryCadence48(hourly)
    with pytest.raises(ValueError, match="commit_unconfirmed"):
        await cadence.tick_hourly()
    assert not cadence._busy
    assert await cadence.tick_hourly() == 0
    assert len(hourly.calls) == 1 and hourly.fences == {"BTC"}


@pytest.mark.asyncio
async def test_finite_cycle_ceiling_and_no_second_action_after_budget_expiry(monkeypatch):
    clock = initial_clock()
    hourly = HourlyBoundary(clock)
    times = iter([0.0, 0.0, 115.0])
    monkeypatch.setattr("apps.hub_worker.history_cadence48.monotonic", lambda: next(times))
    assert await HistoryCadence48(hourly).tick_hourly() == 1
    assert len(hourly.calls) == 1 and hourly.calls[0][1] == 90


@pytest.mark.asyncio
async def test_cancellation_joins_original_thread_before_dispatch_owner_releases():
    entered, exited = threading.Event(), threading.Event()
    hourly = HistoryHourlyV2.__new__(HistoryHourlyV2)
    hourly._busy = False
    hourly.originals = SimpleNamespace(now=lambda: initial_clock()[0])

    def process_one(*, cancel, budget_seconds):
        entered.set()
        assert 1 <= budget_seconds <= 90
        cancel.wait(2)
        assert cancel.is_set()
        exited.set()
        return None

    hourly.process_one = process_one
    cadence = HistoryCadence48(hourly)
    work = asyncio.create_task(cadence.tick_hourly())
    async with asyncio.timeout(2):
        while not entered.is_set():
            await asyncio.sleep(0.001)
    work.cancel()
    with pytest.raises(asyncio.CancelledError):
        await work
    assert exited.is_set() and not hourly._busy and not cadence._busy
    assert await cadence.tick_hourly() == 0


def ingestion_boundary():
    return SimpleNamespace(
        registry=SimpleNamespace(manifests=lambda: ()),
        recover_discovery_admissions=lambda **_kwargs: None,
        runs=SimpleNamespace(admitted_targets=lambda **_kwargs: ()),
        event_watches=SimpleNamespace(advance=lambda **_kwargs: None),
    )


@pytest.mark.asyncio
async def test_dedicated_channel_progresses_while_sources_enhancements_and_publication_wait():
    entered = [asyncio.Event() for _ in range(3)]
    produced = asyncio.Event()
    clock = initial_clock()
    hourly = HourlyBoundary(clock)
    cadence = HistoryCadence48(hourly)

    async def hanging_enhancements():
        entered[0].set()
        await asyncio.Event().wait()
        return 0

    async def hanging_source():
        entered[1].set()
        await asyncio.Event().wait()

    async def hanging_publication():
        entered[2].set()
        await asyncio.Event().wait()
        return 0

    async def forecast():
        count = await cadence.tick_hourly()
        produced.set()
        return count

    scheduler = RealtimeScheduler(
        ingestion_boundary(), risk_enhancement_tick=hanging_enhancements,
        history_hourly_tick=forecast, history_outcome_tick=hanging_publication,
    )
    scheduler._poll_sources = hanging_source
    work = asyncio.create_task(scheduler.run_forever(0.01))
    try:
        await asyncio.wait_for(asyncio.gather(*(event.wait() for event in entered)), 1)
        await asyncio.wait_for(produced.wait(), 1)
        assert len(hourly.slots) == 2
        assert len(hourly.calls) == 2
    finally:
        work.cancel()
        await asyncio.gather(work, return_exceptions=True)


def test_installer_reuses_exact_objects_restores_explicit_base_and_rejects_double_or_fake():
    async def base():
        return 3

    async def legacy_wrapped():
        raise AssertionError("old sequential history wrapper ran")

    scheduler = RealtimeScheduler(ingestion_boundary(), risk_enhancement_tick=legacy_wrapped)
    realtime = SimpleNamespace(scheduler=scheduler)
    assert install_history_cadence48_on_realtime(realtime, enhancement_tick=base) is None
    scheduler._history_hourly_v2 = HourlyBoundary(initial_clock())
    with pytest.raises(ValueError, match="actual_v41"):
        install_history_cadence48_on_realtime(realtime, enhancement_tick=base)
    hourly = HistoryHourly41.__new__(HistoryHourly41)
    scheduler._history_hourly_v2 = hourly
    cadence = install_history_cadence48_on_realtime(realtime, enhancement_tick=base)
    assert cadence.hourly is hourly
    assert scheduler.risk_enhancement_tick is base
    assert scheduler.history_hourly_tick.__self__ is cadence
    assert scheduler.history_outcome_tick is None
    with pytest.raises(ValueError, match="already_bound"):
        install_history_cadence48_on_realtime(realtime, enhancement_tick=base)


@pytest.mark.asyncio
async def test_outcome_keeps_original_finite_budget_and_single_owner():
    calls = []

    async def tick(budget):
        calls.append(budget)
        return 1

    cadence = HistoryCadence48(HourlyBoundary(initial_clock()), SimpleNamespace(tick=tick))
    assert await cadence.tick_outcome() == 1
    assert calls == [115.0]
    assert await HistoryCadence48(HourlyBoundary(initial_clock())).tick_outcome() == 0


def test_main_overlay_binds_after_original_installers_and_preserves_legacy_path():
    root = Path(__file__).resolve().parents[2]
    path = root / "artifacts/market-risk-implementation-20261007/B2/history-cadence48/source/apps/hub_worker/main.py"
    body = path.read_text(encoding="utf-8")
    begin = body.index('if role == "realtime":')
    end = body.index('if role == "research":', begin)
    realtime = body[begin:end]
    assert realtime.index("base_enhancement_tick =") < realtime.index("install_history_hourly41_on_risk_lane(realtime")
    assert realtime.index("install_history_outcome40_on_risk_lane(realtime") < realtime.index("install_history_cadence48_on_realtime(realtime")
    assert "if history_bootstrap41 is not None:" in realtime
    assert "enhancement_tick=base_enhancement_tick" in realtime
    assert "install_history_hourly39_on_risk_lane(realtime, database)" in realtime

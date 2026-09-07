from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from packages.contracts_py.decision_hub_contracts import (
    EventWindowCapture,
    EventWindowSample,
)
from packages.kernel.decision_hub_kernel.application.event_watch import (
    DEFAULT_WINDOW_OFFSETS,
    EventWatchService,
)
from packages.kernel.decision_hub_kernel.persistence.db import Database

NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)


def _database(tmp_path: Path) -> Database:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'watch.sqlite3'}")
    database.create_all()
    return database


def _capture(*, observed_at: datetime, received_at: datetime) -> EventWindowCapture:
    return EventWindowCapture(
        schema_version="event-window-capture.v1",
        observed_at=observed_at,
        received_at=received_at,
        provider_id="fixture-market",
        payload_ref="fixture://market/window",
        payload_hash=hashlib.sha256(b"fixture-window").hexdigest(),
    )


def test_future_watch_is_idempotent_and_materializes_all_slots(tmp_path: Path) -> None:
    service = EventWatchService(_database(tmp_path), clock=lambda: NOW)
    scheduled = NOW + timedelta(hours=1)

    first = service.ensure_watch(
        event_id="evt-future",
        source_id="bls-calendar",
        event_family="inflation_release",
        scheduled_at=scheduled,
    )
    second = service.ensure_watch(
        event_id="evt-future",
        source_id="bls-calendar",
        event_family="inflation_release",
        scheduled_at=scheduled,
    )

    assert first == second
    assert first.status == "scheduled"
    assert first.baseline_status == "pending"
    assert first.window_offsets == list(DEFAULT_WINDOW_OFFSETS)
    samples = service.list_samples(first.watch_id)
    assert len(samples) == len(DEFAULT_WINDOW_OFFSETS)
    assert {item.status for item in samples} == {"pending"}


def test_window_advance_and_capture_require_due_pit_validated_slots(tmp_path: Path) -> None:
    clock = [NOW]
    service = EventWatchService(_database(tmp_path), clock=lambda: clock[0])
    watch = service.ensure_watch(
        event_id="evt-window",
        source_id="calendar",
        event_family="central_bank_speech",
        scheduled_at=NOW + timedelta(minutes=30),
    )

    clock[0] = NOW + timedelta(minutes=1)
    advanced = service.advance()
    assert advanced[0].status == "active"
    baseline = next(item for item in service.list_samples(watch.watch_id) if item.offset == "t-30m")
    assert baseline.status == "due"

    captured = service.capture(
        baseline.sample_id,
        _capture(observed_at=NOW, received_at=NOW + timedelta(minutes=1)),
    )
    assert captured.status == "captured"
    assert captured.payload_hash is not None

    with pytest.raises(ValueError, match="not_due"):
        service.capture(
            next(
                item
                for item in service.list_samples(watch.watch_id)
                if item.offset == "t-5m"
            ).sample_id,
            _capture(observed_at=NOW, received_at=NOW + timedelta(minutes=1)),
        )

    with pytest.raises(ValueError, match="pit_violation"):
        service.capture(
            next(
                item
                for item in service.list_samples(watch.watch_id)
                if item.offset == "t-5m"
            ).sample_id,
            _capture(
                observed_at=NOW + timedelta(minutes=2),
                received_at=NOW + timedelta(minutes=1),
            ),
        )


def test_late_event_is_retrospective_only_and_never_fakes_baseline(tmp_path: Path) -> None:
    service = EventWatchService(_database(tmp_path), clock=lambda: NOW)
    watch = service.ensure_watch(
        event_id="evt-late",
        source_id="calendar",
        event_family="labor_release",
        scheduled_at=NOW - timedelta(hours=11),
    )

    assert watch.status == "retrospective_only"
    assert watch.baseline_status == "unavailable"
    samples = service.list_samples(watch.watch_id)
    assert {item.status for item in samples if item.offset in {"t-30m", "t-5m"}} == {
        "baseline_unavailable"
    }
    assert all(item.status != "captured" for item in samples)


def test_watch_rejects_identity_and_window_mutation(tmp_path: Path) -> None:
    service = EventWatchService(_database(tmp_path), clock=lambda: NOW)
    scheduled = NOW + timedelta(hours=1)
    service.ensure_watch(
        event_id="evt-conflict",
        source_id="calendar",
        event_family="macro",
        scheduled_at=scheduled,
    )
    with pytest.raises(ValueError, match="identity_conflict"):
        service.ensure_watch(
            event_id="evt-conflict",
            source_id="other-calendar",
            event_family="macro",
            scheduled_at=scheduled,
        )
    with pytest.raises(ValueError, match="window_conflict"):
        service.ensure_watch(
            event_id="evt-conflict",
            source_id="calendar",
            event_family="macro",
            scheduled_at=scheduled,
            window_offsets=("t0",),
        )


def test_watch_normalizes_timezone_across_dst_boundary(tmp_path: Path) -> None:
    # The event is expressed in New York local time after the DST fall-back;
    # persisted targets are UTC instants and never depend on local wall-clock math.
    new_york = ZoneInfo("America/New_York")
    scheduled_local = datetime(2026, 11, 2, 8, 30, tzinfo=new_york)
    service = EventWatchService(_database(tmp_path), clock=lambda: NOW)

    watch = service.ensure_watch(
        event_id="evt-dst",
        source_id="calendar",
        event_family="macro_release",
        scheduled_at=scheduled_local,
    )

    assert watch.scheduled_at == datetime(2026, 11, 2, 13, 30, tzinfo=UTC)
    baseline = next(item for item in service.list_samples(watch.watch_id) if item.offset == "t-30m")
    assert baseline.target_at == datetime(2026, 11, 2, 13, 0, tzinfo=UTC)


def test_complete_window_survives_restart_and_captures_each_slot_once(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'watch-lifecycle.sqlite3'}"
    clock = [NOW]
    first_database = Database(database_url)
    first_database.create_all()
    service = EventWatchService(first_database, clock=lambda: clock[0])
    scheduled = NOW + timedelta(hours=1)
    watch = service.ensure_watch(
        event_id="evt-complete-window",
        source_id="official-calendar",
        event_family="central_bank_decision",
        scheduled_at=scheduled,
    )

    class FixtureSampler:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def capture(
            self,
            sample: EventWindowSample,
        ) -> EventWindowCapture:
            self.calls.append(sample.sample_id)
            return EventWindowCapture(
                schema_version="event-window-capture.v1",
                observed_at=clock[0],
                received_at=clock[0],
                provider_id="fixture-market",
                payload_ref=f"fixture://{sample.sample_id}",
                payload_hash=hashlib.sha256(sample.sample_id.encode()).hexdigest(),
            )

    sampler = FixtureSampler()
    offsets = {
        "t-30m": timedelta(minutes=-30),
        "t-5m": timedelta(minutes=-5),
        "t0": timedelta(),
        "t+1m": timedelta(minutes=1),
        "t+5m": timedelta(minutes=5),
        "t+30m": timedelta(minutes=30),
        "t+24h": timedelta(hours=24),
        "t+72h": timedelta(hours=72),
    }

    for index, offset in enumerate(DEFAULT_WINDOW_OFFSETS):
        if index == 4:
            # Recreate the database facade and service to simulate process restart.
            service = EventWatchService(Database(database_url), clock=lambda: clock[0])
        clock[0] = scheduled + offsets[offset]
        first = asyncio.run(service.capture_due(sampler))
        duplicate = asyncio.run(service.capture_due(sampler))
        assert first.attempted == 1
        assert first.captured == 1
        assert first.failed == 0
        assert duplicate.attempted == 0

    persisted = service.get_watch(watch.watch_id)
    assert persisted is not None
    assert persisted.status == "completed"
    assert persisted.baseline_status == "ready"
    samples = service.list_samples(watch.watch_id)
    assert len(samples) == len(DEFAULT_WINDOW_OFFSETS)
    assert {item.status for item in samples} == {"captured"}
    assert len(sampler.calls) == len(DEFAULT_WINDOW_OFFSETS)
    assert len(set(sampler.calls)) == len(DEFAULT_WINDOW_OFFSETS)

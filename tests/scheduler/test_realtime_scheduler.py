from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

from packages.contracts_py.decision_hub_contracts.models import (
    EventWindowCapture,
    EventWindowSample,
    SourceManifest,
    SourceType,
    TextEnvelope,
)
from packages.kernel.decision_hub_kernel.application.scheduler import RealtimeScheduler
from packages.kernel.decision_hub_kernel.application.source_ingest import (
    RunTarget,
    SourceIngestionService,
)
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.source_adapters.registry import SourceRegistry


class PendingSource:
    manifest = SourceManifest(
        source_id="pending-source",
        source_type="official_feed",
        version="fixture.v1",
        authority_level="official",
    )

    async def poll(self, cursor: str | None = None):
        from packages.kernel.decision_hub_kernel.ports.sources import SourcePollResult

        now = datetime(2026, 8, 27, 1, 0, tzinfo=UTC)
        text = "The committee may cut rates to support growth."
        return SourcePollResult(
            source_id=self.manifest.source_id,
            cursor_before=cursor,
            cursor_after="one",
            envelopes=(
                TextEnvelope(
                    source_id=self.manifest.source_id,
                    source_type=SourceType.official_feed,
                    observed_at=now,
                    received_at=now,
                    raw_text=text,
                    language="en",
                    content_hash=hashlib.sha256(text.encode()).hexdigest(),
                ),
            ),
            fetched_at=now,
        )


def test_scheduler_recovers_admitted_run_after_cursor_commit(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'scheduler-recovery.sqlite3'}")
    database.create_all()
    registry = SourceRegistry()
    source = PendingSource()
    registry.register(source)
    now = datetime(2026, 8, 27, 1, 0, 1, tzinfo=UTC)
    ingestion = SourceIngestionService(database, registry, clock=lambda: now)
    first = asyncio.run(ingestion.poll_once(source.manifest.source_id))
    assert len(first.run_targets) == 1
    targets: list[RunTarget] = []

    async def execute(target: RunTarget) -> None:
        targets.append(target)

    report = asyncio.run(
        RealtimeScheduler(ingestion, run_executor=execute, clock=lambda: now).tick()
    )

    assert report.runs_started == 1
    assert [target.run_id for target in targets] == [first.run_targets[0].run_id]
    assert report.polled[0].skipped is True


def test_scheduler_source_failure_does_not_block_other_sources(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'scheduler-isolation.sqlite3'}")
    database.create_all()

    class FailingSource(PendingSource):
        manifest = SourceManifest(
            source_id="failing-source",
            source_type="official_feed",
            version="fixture.v1",
            authority_level="official",
        )

        async def poll(self, cursor: str | None = None):
            raise RuntimeError("source_timeout")

    registry = SourceRegistry()
    registry.register(FailingSource())
    registry.register(PendingSource())
    now = datetime(2026, 8, 27, 1, 0, 1, tzinfo=UTC)
    ingestion = SourceIngestionService(database, registry, clock=lambda: now)
    targets: list[RunTarget] = []

    async def execute(target: RunTarget) -> None:
        targets.append(target)

    report = asyncio.run(
        RealtimeScheduler(ingestion, run_executor=execute, clock=lambda: now).tick()
    )

    assert report.polled[0].error_code == "source_timeout"
    assert report.polled[1].accepted_count == 1
    assert report.runs_started == 1
    assert len(targets) == 1


def test_scheduler_captures_due_window_once_across_repeated_ticks(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'window-scheduler.sqlite3'}")
    database.create_all()
    registry = SourceRegistry()
    now = [datetime(2026, 8, 27, 1, 0, tzinfo=UTC)]
    ingestion = SourceIngestionService(database, registry, clock=lambda: now[0])
    watch = ingestion.event_watches.ensure_watch(
        event_id="evt-scheduled",
        source_id="calendar",
        event_family="inflation_release",
        scheduled_at=now[0] + timedelta(minutes=30),
    )

    class FixtureSampler:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def capture(self, sample: EventWindowSample) -> EventWindowCapture:
            self.calls.append(sample.sample_id)
            return EventWindowCapture(
                schema_version="event-window-capture.v1",
                observed_at=now[0],
                received_at=now[0],
                provider_id="fixture-market",
                payload_ref=f"fixture://{sample.sample_id}",
                payload_hash=hashlib.sha256(sample.sample_id.encode()).hexdigest(),
            )

    sampler = FixtureSampler()
    scheduler = RealtimeScheduler(
        ingestion,
        window_sampler=sampler,
        clock=lambda: now[0],
    )

    first = asyncio.run(scheduler.tick())
    second = asyncio.run(scheduler.tick())

    assert first.window_samples_attempted == 1
    assert first.window_samples_captured == 1
    assert second.window_samples_attempted == 0
    assert sampler.calls == [f"{watch.watch_id}:t-30m"]
    baseline = next(
        item
        for item in ingestion.event_watches.list_samples(watch.watch_id)
        if item.offset == "t-30m"
    )
    assert baseline.status == "captured"


def test_sampler_failure_is_visible_and_late_slot_fails_closed(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'window-failure.sqlite3'}")
    database.create_all()
    registry = SourceRegistry()
    now = [datetime(2026, 8, 27, 1, 0, tzinfo=UTC)]
    ingestion = SourceIngestionService(database, registry, clock=lambda: now[0])
    watch = ingestion.event_watches.ensure_watch(
        event_id="evt-failed-window",
        source_id="calendar",
        event_family="inflation_release",
        scheduled_at=now[0] + timedelta(minutes=30),
    )

    class FailedSampler:
        async def capture(
            self,
            sample: EventWindowSample,
        ) -> EventWindowCapture | None:
            del sample
            raise RuntimeError("provider timeout")

    scheduler = RealtimeScheduler(
        ingestion,
        window_sampler=FailedSampler(),
        clock=lambda: now[0],
    )
    failed = asyncio.run(scheduler.tick())
    assert failed.window_samples_failed == 1
    due = next(
        item
        for item in ingestion.event_watches.list_samples(watch.watch_id)
        if item.offset == "t-30m"
    )
    assert due.status == "due"
    assert due.error_code == "event_window_capture_failed"

    now[0] += timedelta(minutes=6)
    expired = asyncio.run(scheduler.tick())
    assert expired.window_samples_attempted == 0
    missing = next(
        item
        for item in ingestion.event_watches.list_samples(watch.watch_id)
        if item.offset == "t-30m"
    )
    assert missing.status == "baseline_unavailable"
    assert missing.error_code == "event_window_capture_failed"

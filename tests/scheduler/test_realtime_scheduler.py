from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime
from pathlib import Path

from packages.contracts_py.decision_hub_contracts.models import (
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

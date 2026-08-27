from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from apps.hub_api.main import create_app
from packages.contracts_py.decision_hub_contracts.models import SourceType, TextEnvelope
from packages.kernel.decision_hub_kernel.application.outbox import NotificationDispatcher
from packages.kernel.decision_hub_kernel.application.outcome_due import DueOutcomeService
from packages.kernel.decision_hub_kernel.application.scheduler import RealtimeScheduler
from packages.kernel.decision_hub_kernel.application.source_ingest import (
    RunTarget,
    SourceIngestionService,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    ForecastRecord,
    OutcomeRecord,
    RunRecord,
)
from packages.kernel.decision_hub_kernel.ports.sources import (
    MarketQuote,
    PriceWindow,
    SourceManifest,
    SourcePollResult,
)
from packages.orchestration.langgraph import build_analyze_text_service
from packages.provider_adapters.notifications.local import LocalNotificationAdapter
from packages.runtime_adapters.fake_runtime.runtime import FakeAgentRuntime
from packages.source_adapters.registry import SourceRegistry


class RealtimeFixtureSource:
    manifest = SourceManifest(
        source_id="realtime-fixture",
        source_type="official_feed",
        version="fixture.v1",
        capabilities=("poll",),
        authority_level="official",
    )

    async def poll(self, cursor: str | None = None) -> SourcePollResult:
        now = datetime.now(UTC)
        text = "The committee says rates may stay higher for longer."
        return SourcePollResult(
            source_id=self.manifest.source_id,
            cursor_before=cursor,
            cursor_after="event-1",
            envelopes=(
                TextEnvelope(
                    source_id=self.manifest.source_id,
                    source_type=SourceType.official_feed,
                    observed_at=now - timedelta(seconds=1),
                    published_at=now - timedelta(seconds=2),
                    received_at=now,
                    raw_text=text,
                    language="en",
                    event_hint="fomc-statement",
                    content_hash=hashlib.sha256(text.encode()).hexdigest(),
                ),
            ),
            fetched_at=now,
        )


def test_source_poll_reuses_r0_chain_and_duplicate_poll_reuses_run(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'realtime.sqlite3'}")
    app = create_app(
        database,
        source_connectors=[RealtimeFixtureSource()],
        sources_enabled=True,
    )
    client = TestClient(app)

    first = client.post("/v1/sources/realtime-fixture/poll")
    second = client.post("/v1/sources/realtime-fixture/poll")

    assert first.status_code == 200
    assert first.json()["accepted_count"] == 1
    assert len(first.json()["run_targets"]) == 1
    run_id = first.json()["run_targets"][0]["run_id"]
    run = client.get(f"/v1/runs/{run_id}").json()
    assert run["status"] == "completed"
    assert run["artifact_id"]
    assert second.status_code == 200
    assert second.json()["duplicate_count"] == 1
    assert second.json()["run_targets"] == []
    with database.session() as session:
        assert session.query(RunRecord).count() == 1


def test_source_poll_requires_explicit_enablement(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'disabled.sqlite3'}")
    app = create_app(database, source_connectors=[RealtimeFixtureSource()], sources_enabled=False)
    response = TestClient(app).post("/v1/sources/realtime-fixture/poll")
    assert response.status_code == 409


class E2ESource(RealtimeFixtureSource):
    manifest = SourceManifest(
        source_id="e2e-source",
        source_type="official_feed",
        version="fixture.v1",
        capabilities=("poll",),
        authority_level="official",
    )

    async def poll(self, cursor: str | None = None) -> SourcePollResult:
        now = datetime(2026, 8, 27, 1, 0, tzinfo=UTC)
        text = "Powell says higher for longer."
        return SourcePollResult(
            source_id=self.manifest.source_id,
            cursor_before=cursor,
            cursor_after="event-1",
            envelopes=(
                TextEnvelope(
                    source_id=self.manifest.source_id,
                    source_type=SourceType.official_feed,
                    observed_at=now - timedelta(seconds=1),
                    published_at=now - timedelta(seconds=2),
                    received_at=now,
                    raw_text=text,
                    language="en",
                    event_hint="fomc-statement",
                    content_hash=hashlib.sha256(text.encode()).hexdigest(),
                ),
            ),
            fetched_at=now,
        )


class E2EMarket:
    async def window(
        self, instrument: str, emitted_at: datetime, expires_at: datetime
    ) -> PriceWindow | None:
        return PriceWindow(
            entry=MarketQuote(
                instrument=instrument,
                observed_at=emitted_at,
                received_at=expires_at,
                bid=100,
                ask=101,
                last=100.5,
                source_id="fixture-market",
            ),
            exit=MarketQuote(
                instrument=instrument,
                observed_at=expires_at,
                received_at=expires_at,
                bid=102,
                ask=103,
                last=102.5,
                source_id="fixture-market",
            ),
        )


def test_scheduler_reuses_r0_chain_then_evaluates_due_forecast_and_notifies(
    tmp_path: Path,
) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'realtime-e2e.sqlite3'}")
    database.create_all()
    registry = SourceRegistry()
    source = E2ESource()
    registry.register(source)
    now = datetime(2026, 8, 27, 1, 0, 1, tzinfo=UTC)
    ingestion = SourceIngestionService(database, registry, clock=lambda: now)
    analyzer = build_analyze_text_service(database, FakeAgentRuntime())
    notifications = tmp_path / "notifications.jsonl"

    async def execute(target: RunTarget) -> None:
        assert await analyzer.run_admitted(target.event_id, target.run_id) is True

    scheduler = RealtimeScheduler(
        ingestion,
        run_executor=execute,
        outcomes=DueOutcomeService(database, E2EMarket()),
        notifications=NotificationDispatcher(
            database,
            {"local": LocalNotificationAdapter(notifications)},
            clock=lambda: now,
        ),
        clock=lambda: now,
    )

    first = asyncio.run(scheduler.tick())
    assert first.runs_started == 1
    assert first.notifications_delivered == 1
    assert notifications.read_text().count("artifact_id") == 1

    with database.session() as session:
        forecast = session.query(ForecastRecord).order_by(ForecastRecord.horizon.asc()).first()
        assert forecast is not None
        forecast.expires_at = now - timedelta(seconds=1)
        forecast_id = forecast.forecast_id

    second = asyncio.run(scheduler.tick())
    assert second.runs_started == 0
    assert second.outcomes_processed == 1
    with database.session() as session:
        assert session.query(OutcomeRecord).filter_by(forecast_id=forecast_id).count() == 1

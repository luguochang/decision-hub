from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.hub_api.asset_routes import asset_router
from packages.kernel.decision_hub_kernel.application.event_watch import EventWatchService
from packages.kernel.decision_hub_kernel.persistence.db import (
    EventOccurrenceRecord,
    MacroEventRecord,
    MacroEventVintageRecord,
    RunRecord,
)
from packages.query_views.event_assets import event_assets
from tests.research.test_study_identity import NOW, setup


def records(tmp_path):
    db = setup(tmp_path)
    with db.session() as session:
        for n in range(2):
            session.add(
                MacroEventRecord(
                    macro_event_id=f"macro-{n}",
                    event_id=f"event-{n}",
                    external_event_id=f"official-{n}",
                    family="fomc",
                    scheduled_at=NOW,
                    source_id="fed",
                    time_precision="date",
                    scheduled_date="2026-09-29",
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
            session.add(
                EventOccurrenceRecord(
                    event_id=f"event-{n}",
                    occurrence_id=f"occurrence-{n}",
                    linked_at=NOW,
                    basis="official",
                )
            )
            session.add(
                RunRecord(
                    run_id=f"run-{n}",
                    event_id=f"event-{n}",
                    strategy_version="research.v1",
                    status="failed",
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
        session.add(
            EventOccurrenceRecord(
                event_id="corrected-event",
                occurrence_id="occurrence-0",
                linked_at=NOW,
                basis="official",
            )
        )
        session.add(
            RunRecord(
                run_id="corrected-run",
                event_id="corrected-event",
                strategy_version="research.v1",
                status="admitted",
                created_at=NOW + timedelta(hours=1),
                updated_at=NOW,
            )
        )
        for n in range(2):
            session.add(
                MacroEventVintageRecord(
                    vintage_id=f"vintage-{n}",
                    macro_event_id="macro-0",
                    event_id="event-0",
                    observed_at=NOW + timedelta(minutes=n),
                    scheduled_at=NOW,
                    time_precision="date",
                    scheduled_date="2026-09-29",
                    source_timezone="America/New_York",
                    actual_value=0 if n else None,
                    status="released" if n else "scheduled",
                    payload_hash=str(n) * 64,
                    created_at=NOW + timedelta(minutes=n + 5),
                )
            )
    for event in ["event-0", "event-1", "corrected-event"]:
        EventWatchService(db, clock=lambda: NOW).ensure_watch(
            event_id=event,
            source_id="fed",
            event_family="fomc",
            scheduled_at=NOW,
            window_offsets=("t-5m", "t+1m"),
        )
    return db


def test_archive_joins_only_immutable_occurrence_and_preserves_date_and_zero(tmp_path):
    db = records(tmp_path)
    result = event_assets(db, "macro-0", limit=1)
    assert result.run_total == 2 and result.runs[0].run_id == "corrected-run"
    assert result.sample_total == 2 and len(result.samples) == 1
    assert result.vintage_total == 2 and result.vintages[0].actual_value == 0
    assert result.vintages[0].scheduled_at is None
    assert result.vintages[0].recorded_at > result.vintages[0].observed_at
    second = event_assets(db, "macro-0", limit=1, run_offset=1, vintage_offset=1, sample_offset=1)
    assert second.runs[0].run_id == "run-0" and second.vintages[0].actual_value is None
    assert second.samples[0].sample_id != result.samples[0].sample_id
    assert second.samples[0].received_at is None
    assert event_assets(db, "macro-0", run_offset=100).runs == []
    with pytest.raises(KeyError):
        event_assets(db, "missing")


def test_archive_http_validation_and_missing_event(tmp_path):
    db = records(tmp_path)
    app = FastAPI()
    app.include_router(asset_router(db, tmp_path, clock=lambda: NOW))
    client = TestClient(app)
    assert client.get("/v1/macro-events/missing/archive").status_code == 404
    assert client.get("/v1/macro-events/macro-0/archive?sample_offset=-1").status_code == 422
    response = client.get("/v1/macro-events/macro-0/archive").json()
    assert response["occurrence_id"] == "occurrence-0"
    assert len(response["samples"]) == 2
    assert all(item["observed_at"] is None for item in response["samples"])

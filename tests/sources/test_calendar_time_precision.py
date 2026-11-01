from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from packages.kernel.decision_hub_kernel.application.macro_event import (
    list_macro_event_vintages,
    list_macro_events,
)
from packages.kernel.decision_hub_kernel.application.source_ingest import SourceIngestionService
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    EventWatchRecord,
    EventWindowSampleRecord,
    RunRecord,
)
from packages.kernel.decision_hub_kernel.ports.sources import SourceManifest, SourcePollFailure
from packages.source_adapters.official_feeds import OfficialCalendarSource
from packages.source_adapters.registry import SourceRegistry


def ics(start: str, status: str = "CONFIRMED") -> bytes:
    return (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:meeting\r\n"
        f"DTSTART{start}\r\nSUMMARY:Policy meeting\r\nSTATUS:{status}\r\n"
        "END:VEVENT\r\nEND:VCALENDAR\r\n"
    ).encode()


@pytest.mark.asyncio
@pytest.mark.parametrize("record_only", [False, True])
async def test_date_only_is_saved_without_fictitious_sampling_then_rescheduled_and_cancelled(
    tmp_path: Path, record_only: bool,
) -> None:
    year = datetime.now(UTC).year + 2
    body = ics(f";VALUE=DATE:{year}1029")

    async def fetcher(_url: str) -> tuple[int, bytes]:
        return 200, body

    source = OfficialCalendarSource(
        SourceManifest(source_id="calendar", source_type="official_feed", version="v2"),
        "https://example.org/calendar.ics",
        source_timezone="America/New_York",
        fetcher=fetcher,
    )
    registry = SourceRegistry()
    registry.register(source)
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'calendar.sqlite3'}")
    database.create_all()
    ingestion = SourceIngestionService(database, registry)
    if record_only:
        from packages.provider_adapters.research.discovery import CryptoMacroDiscoveryPolicy
        ingestion.strategy_selector = CryptoMacroDiscoveryPolicy(
            source_ids=[], high_impact_terms=[], include_baseline=False
        )
    result = await ingestion.poll_once("calendar")
    assert result.error_code is None
    assert result.accepted_count == 1
    assert result.run_targets == ()
    with database.session() as session:
        assert session.query(EventWatchRecord).count() == 0
        assert session.query(RunRecord).count() == 0
    # Late on the announced source day it is still upcoming, not yesterday.
    now = datetime(year, 10, 30, 1, tzinfo=UTC)
    events = list_macro_events(database, scope="upcoming", now=now)
    assert len(events) == 1
    event = events[0]
    assert event.scheduled_at is None
    assert str(event.scheduled_date) == f"{year}-10-29"
    assert event.updated_at.utcoffset() == timedelta(0)
    assert event.source_timezone == "America/New_York"
    assert list_macro_events(database, scope="upcoming", now=now + timedelta(hours=6)) == []

    body = ics(f";TZID=America/New_York:{year}1029T083000")
    assert (await ingestion.poll_once("calendar")).error_code is None
    exact = list_macro_events(database)[0]
    assert exact.time_precision == "exact"
    assert exact.scheduled_at == datetime(year, 10, 29, 12, 30, tzinfo=UTC)
    body = ics(f";TZID=America/New_York:{year}1115T083000")
    assert (await ingestion.poll_once("calendar")).error_code is None
    revised = list_macro_events(database)[0]
    assert revised.scheduled_at == datetime(year, 11, 15, 13, 30, tzinfo=UTC)  # DST ended
    body = ics(f";TZID=America/New_York:{year}1115T083000", "CANCELLED")
    assert (await ingestion.poll_once("calendar")).error_code is None
    assert list_macro_events(database, scope="upcoming") == []
    with database.session() as session:
        watches = session.query(EventWatchRecord).all()
        assert len(watches) == 2
        assert all(row.status == "cancelled" for row in watches)
        assert all(row.status == "cancelled" for row in session.query(RunRecord))
        assert all(
            row.error_code == "calendar_schedule_superseded"
            for row in session.query(EventWindowSampleRecord)
        )
    vintages = list_macro_event_vintages(database, event.macro_event_id)
    assert len(vintages) == 4
    assert [row.time_precision for row in vintages] == ["date", "exact", "exact", "exact"]
    assert vintages[0].scheduled_at is None
    assert vintages[1].scheduled_at == exact.scheduled_at
    assert vintages[2].scheduled_at == revised.scheduled_at
    assert vintages[-1].status == "cancelled"
    assert all(row.observed_at.utcoffset() == timedelta(0) for row in vintages)


@pytest.mark.asyncio
async def test_floating_ics_time_requires_declared_source_timezone() -> None:
    async def fetcher(_url: str) -> tuple[int, bytes]:
        return 200, ics(":20301029T083000")

    source = OfficialCalendarSource(
        SourceManifest(source_id="calendar", source_type="official_feed", version="v2"),
        "https://example.org/calendar.ics",
        fetcher=fetcher,
    )
    with pytest.raises(SourcePollFailure, match="calendar_timezone_missing"):
        await source.poll()

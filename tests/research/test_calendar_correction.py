import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from packages.kernel.decision_hub_kernel.application.calendar_correction import (
    correct_legacy_calendar,
)
from packages.kernel.decision_hub_kernel.application.event_watch import EventWatchService
from packages.kernel.decision_hub_kernel.application.macro_event import (
    get_macro_event,
    list_macro_events,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    CalendarCorrectionRecord,
    Database,
    EventWatchRecord,
    MacroEventRecord,
    MacroEventVintageRecord,
    ObservationRecord,
)
from packages.kernel.decision_hub_kernel.ports.sources import SourceManifest
from packages.source_adapters.official_feeds import OfficialHtmlCalendarSource


@pytest.mark.asyncio
@pytest.mark.parametrize("reject_receipt", [False, True])
@pytest.mark.parametrize("known_date", [False, True])
async def test_calendar_correction_is_atomic_idempotent_and_preserves_original(
    tmp_path: Path,
    reject_receipt: bool,
    known_date: bool,
) -> None:
    now = datetime.now(UTC)
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'repair.sqlite3'}")
    database.create_all()
    with database.session() as session:
        session.add(
            MacroEventRecord(
                macro_event_id="bad",
                event_id="old",
                external_event_id="wrong-notes-date",
                family="fomc",
                scheduled_at=now + timedelta(days=100),
                time_precision="date" if known_date else "unknown",
                correction_ref="earlier-receipt" if known_date else None,
                source_id="fed-calendar",
                source_authority="official",
                status="scheduled",
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            MacroEventVintageRecord(
                vintage_id="original",
                macro_event_id="bad",
                event_id="old",
                observed_at=now,
                revision_flag=False,
                status="scheduled",
                payload_hash="a" * 64,
                created_at=now,
            )
        )
    EventWatchService(database).ensure_watch(
        event_id="old",
        source_id="fed-calendar",
        event_family="fomc",
        scheduled_at=now + timedelta(days=100),
    )

    async def fetcher(_url: str) -> tuple[int, str]:
        return 200, f"<h2>{now.year + 2} FOMC Meetings</h2><ul><li>December 20</li></ul>"

    source = OfficialHtmlCalendarSource(
        SourceManifest(source_id="fed-calendar", source_type="official_feed", version="v2"),
        "https://www.federalreserve.gov/calendar",
        family="fomc",
        event_title="FOMC",
        fetcher=fetcher,
    )
    result = await source.poll()
    args = dict(
        source_id="fed-calendar",
        parser_version="v2",
        source_content_hash="b" * 64,
        source_content_ref="verified-official.html",
        envelopes=result.envelopes,
        valid_external_ids={result.envelopes[0].macro_event.external_event_id},
        now=now,
        audited_invalid_ids={"wrong-notes-date"} if known_date else set(),
    )
    if reject_receipt:
        with database.engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TRIGGER reject_receipt BEFORE INSERT ON calendar_corrections "
                    "BEGIN SELECT RAISE(ABORT, 'injected'); END"
                )
            )
        with pytest.raises(IntegrityError, match="injected"):
            correct_legacy_calendar(database, **args)
        with database.session() as session:
            assert session.query(MacroEventRecord).count() == 1
            assert session.query(ObservationRecord).count() == 0
            assert session.query(CalendarCorrectionRecord).count() == 0
            assert session.query(EventWatchRecord).one().status == "scheduled"
        assert get_macro_event(database, "bad").invalidated_at is None
        return

    receipt = correct_legacy_calendar(database, **args)
    repeated = correct_legacy_calendar(database, **args)
    assert repeated["replayed"] is True
    assert repeated["correction_id"] == receipt["correction_id"]
    assert receipt["invalidated"] == ["bad"]
    assert len(list_macro_events(database)) == 1
    original = get_macro_event(database, "bad")
    assert original.invalidated_at == now
    assert original.status == "scheduled"  # no fabricated official cancellation
    with database.session() as session:
        assert session.get(MacroEventVintageRecord, "original").payload_hash == "a" * 64
        assert session.query(MacroEventVintageRecord).count() == 2
        assert session.query(EventWatchRecord).one().status == "cancelled"
        record = session.query(CalendarCorrectionRecord).one()
        assert json.loads(record.before_json)[0]["event_id"] == "old"

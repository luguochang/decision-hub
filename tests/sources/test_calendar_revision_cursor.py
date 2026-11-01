from __future__ import annotations

from datetime import UTC, datetime

import pytest

from packages.kernel.decision_hub_kernel.ports.sources import SourceManifest
from packages.source_adapters.official_feeds import (
    OfficialCalendarSource,
    OfficialHtmlCalendarSource,
)


def calendar(*events: tuple[str, str, str]) -> bytes:
    rows = ["BEGIN:VCALENDAR", "VERSION:2.0"]
    for uid, start, summary in events:
        rows.extend(
            [
                "BEGIN:VEVENT",
                f"UID:{uid}",
                f"DTSTART:{start}",
                f"SUMMARY:{summary}",
                "END:VEVENT",
            ]
        )
    rows.append("END:VCALENDAR")
    return "\r\n".join(rows).encode()


@pytest.mark.asyncio
async def test_ics_revision_and_earlier_insert_survive_restart_and_batch_limit() -> None:
    body = calendar(("late", "20301220T130000Z", "CPI"))

    async def fetcher(_url: str) -> tuple[int, bytes]:
        return 200, body

    def connector() -> OfficialCalendarSource:
        return OfficialCalendarSource(
            SourceManifest(
                source_id="calendar", source_type="official_feed", version="v2", max_batch=1
            ),
            "https://example.org/calendar.ics",
            fetcher=fetcher,
        )

    first = await connector().poll()
    assert (await connector().poll(first.cursor_after)).envelopes == ()
    body = calendar(
        ("late", "20301220T133000Z", "CPI revised time"),
        ("earlier", "20301101T130000Z", "Employment situation"),
    )
    inserted = await connector().poll(first.cursor_after)
    assert [row.event_hint for row in inserted.envelopes] == ["earlier"]
    revision = await connector().poll(inserted.cursor_after)
    assert [row.event_hint for row in revision.envelopes] == ["late"]
    assert revision.envelopes[0].scheduled_at == datetime(2030, 12, 20, 13, 30, tzinfo=UTC)
    assert revision.envelopes[0].revision_of == f"sha256:{first.envelopes[0].content_hash}"
    assert (await connector().poll(revision.cursor_after)).envelopes == ()


@pytest.mark.asyncio
async def test_html_earlier_insert_after_old_watermark_is_not_lost() -> None:
    year = datetime.now(UTC).year + 2
    body = f"<h2>{year} FOMC Meetings</h2><ul><li>December 20</li></ul>"

    async def fetcher(_url: str) -> tuple[int, str]:
        return 200, body

    source = OfficialHtmlCalendarSource(
        SourceManifest(source_id="fed-calendar", source_type="official_feed", version="v2"),
        "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
        family="fomc",
        event_title="FOMC meeting",
        fetcher=fetcher,
    )
    first = await source.poll(f"{year}-12-20T00:00:00+00:00|legacy-id")
    assert len(first.envelopes) == 1  # v1 migration performs one idempotent rescan
    body = f"<h2>{year} FOMC Meetings</h2><ul><li>November 1</li><li>December 20</li></ul>"
    second = await source.poll(first.cursor_after)
    assert len(second.envelopes) == 1
    assert second.envelopes[0].scheduled_at == datetime(year, 11, 1, tzinfo=UTC)
    assert (await source.poll(second.cursor_after)).envelopes == ()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cursor",
    [
        "invalid",
        "calendar-v2:{",
        "calendar-v2:[]",
        'calendar-v2:{"source_id":"other","seen":{}}',
        'calendar-v2:{"source_id":"calendar","seen":{"id":"not-a-hash"}}',
    ],
)
async def test_corrupt_or_cross_source_cursor_is_not_silently_reset(cursor: str) -> None:
    async def fetcher(_url: str) -> tuple[int, bytes]:
        return 200, calendar(("one", "20300101T130000Z", "CPI"))

    source = OfficialCalendarSource(
        SourceManifest(source_id="calendar", source_type="official_feed", version="v2"),
        "https://example.org/calendar.ics",
        fetcher=fetcher,
    )
    with pytest.raises(ValueError, match="source_parse_error:calendar_cursor"):
        await source.poll(cursor)


@pytest.mark.asyncio
async def test_conflicting_duplicate_uid_does_not_silently_overwrite() -> None:
    async def fetcher(_url: str) -> tuple[int, bytes]:
        return 200, calendar(
            ("same", "20300101T130000Z", "CPI"), ("same", "20300102T130000Z", "CPI")
        )

    source = OfficialCalendarSource(
        SourceManifest(source_id="calendar", source_type="official_feed", version="v2"),
        "https://example.org/calendar.ics",
        fetcher=fetcher,
    )
    with pytest.raises(ValueError, match="calendar_identity_conflict"):
        await source.poll()

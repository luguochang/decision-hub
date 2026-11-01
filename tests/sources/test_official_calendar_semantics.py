import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from packages.source_adapters.official_feeds.html_calendar import parse_official_schedule_html

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures/sources/official-calendar-2026-09-29"


def parse(source: str, body: str | None = None):
    return parse_official_schedule_html(
        body if body is not None else (FIXTURES / f"{source}.html").read_text(encoding="utf-8"),
        source_id=source,
        family="test",
        event_title="Meeting",
    )


def test_official_fixtures_are_unedited_archived_pages():
    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    for source, entry in manifest.items():
        assert (
            hashlib.sha256((FIXTURES / f"{source}.html").read_bytes()).hexdigest()
            == entry["sha256"]
        )


def test_fed_real_page_has_all_2026_meeting_end_dates_not_minutes_or_2028_note():
    rows = parse("fed-calendar")
    dates = [row[1].date().isoformat() for row in rows if row[1].year == 2026]
    assert dates == [
        "2026-01-28",
        "2026-03-18",
        "2026-04-29",
        "2026-06-17",
        "2026-07-29",
        "2026-09-16",
        "2026-10-28",
        "2026-12-09",
    ]
    assert not any(row[1].year == 2028 for row in rows)
    assert datetime(2023, 2, 1, tzinfo=UTC) in [row[1] for row in rows]


def test_boj_real_page_reads_meeting_column_and_last_day_only():
    rows = parse("boj-calendar")
    assert len(rows) == 16
    assert [row[1].date().isoformat() for row in rows if row[1].year == 2026] == [
        "2026-01-23",
        "2026-03-19",
        "2026-04-28",
        "2026-06-16",
        "2026-07-31",
        "2026-09-18",
        "2026-10-30",
        "2026-12-18",
    ]
    assert datetime(2027, 5, 7, tzinfo=UTC) not in [row[1] for row in rows]


def test_ecb_real_page_excludes_non_policy_and_day_one():
    rows = parse("ecb-calendar")
    assert [row[1].date().isoformat() for row in rows if row[1].year == 2026] == [
        "2026-10-29",
        "2026-12-17",
    ]
    # The first day lacks a label, but October 12 is explicitly Day 2.
    assert datetime(2028, 10, 11, tzinfo=UTC) not in [row[1] for row in rows]
    assert datetime(2028, 10, 12, tzinfo=UTC) in [row[1] for row in rows]
    assert len([row for row in rows if row[1].year == 2028]) == 8
    assert all("Day 1" not in row[2] for row in rows)
    assert all("non-monetary" not in row[2] for row in rows)


def test_ecb_unpaired_single_day_meeting_is_retained():
    rows = parse(
        "ecb-calendar",
        "<main><dl><dt>11/10/2028</dt><dd>Governing Council of the ECB: "
        "monetary policy meeting in Frankfurt</dd></dl></main>",
    )
    assert [row[1] for row in rows] == [datetime(2028, 10, 11, tzinfo=UTC)]


@pytest.mark.parametrize("source", ["fed-calendar", "boj-calendar", "ecb-calendar"])
def test_official_structure_change_is_a_parse_failure_not_healthy_zero_coverage(source):
    # A branded successful HTML response without the actual calendar isn't data.
    body = (
        '<main><div class="panel"><div class="panel-heading">2026 FOMC Meetings</div>'
        "</div><p>Temporarily unavailable</p></main>"
    )
    with pytest.raises(ValueError, match="source_parse_error"):
        parse(source, body)

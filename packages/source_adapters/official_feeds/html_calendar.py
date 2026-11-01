from __future__ import annotations

import hashlib
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from bs4 import BeautifulSoup, Tag
from pydantic import HttpUrl, TypeAdapter

from packages.contracts_py.decision_hub_contracts.models import (
    MacroEventPayload,
    SourceType,
    TextEnvelope,
)
from packages.kernel.decision_hub_kernel.ports.sources import (
    SourceManifest,
    SourcePollFailure,
    SourcePollResult,
)
from packages.provider_adapters.research.raw_capture import CapturingAsyncClient

from .calendar_cursor import select_calendar_changes
from .diagnostics import calendar_snapshot, response_snapshot

HtmlCalendarFetcher = Callable[[str], Awaitable[tuple[int, str]]]

_MONTHS = {
    name.casefold(): index
    for index, name in enumerate(
        (
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ),
        start=1,
    )
}
_DATE = re.compile(
    r"(?P<month>January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+(?P<start>\d{1,2})(?:\s*[-–—]\s*(?P<end>\d{1,2}))?(?:,?\s+(?P<year>20\d{2}))?",
    re.IGNORECASE,
)
_YEAR = re.compile(r"\b(20\d{2})\b")


async def _fetch(url: str) -> tuple[int, str]:
    async with CapturingAsyncClient(timeout=20) as client:
        response = await client.get(url, follow_redirects=True)
        return response.status_code, response.text


class OfficialHtmlCalendarSource:
    """Extract day-level policy meetings from an official schedule page."""

    def __init__(
        self,
        manifest: SourceManifest,
        endpoint: str,
        *,
        family: str,
        event_title: str,
        source_timezone: str | None = None,
        fetcher: HtmlCalendarFetcher | None = None,
    ) -> None:
        self.manifest = manifest
        self.endpoint = endpoint
        self.family = family
        self.event_title = event_title
        self.source_timezone = source_timezone
        self.fetcher = fetcher or _fetch

    async def poll(self, cursor: str | None = None) -> SourcePollResult:
        status, body = await self.fetcher(self.endpoint)
        snapshot = response_snapshot(status, body, self.manifest.version)
        received_at = datetime.now(UTC)
        lower_bound = received_at - timedelta(days=1)
        try:
            parsed = parse_official_schedule_html(
                body,
                source_id=self.manifest.source_id,
                family=self.family,
                event_title=self.event_title,
            )
        except ValueError as exc:
            raise SourcePollFailure(
                "source_parse_error", snapshot.model_copy(update={"parse_status": "invalid"})
            ) from exc
        events = [event for event in parsed if event[1] >= lower_bound]
        candidates = tuple(
            self._to_envelope(uid, start, summary, received_at) for uid, start, summary in events
        )
        envelopes, cursor_after = select_calendar_changes(
            candidates,
            source_id=self.manifest.source_id,
            cursor=cursor,
            max_batch=self.manifest.max_batch,
        )
        return SourcePollResult(
            source_id=self.manifest.source_id,
            cursor_before=cursor,
            cursor_after=cursor_after,
            envelopes=envelopes,
            fetched_at=received_at,
            content_snapshot=calendar_snapshot(
                snapshot, candidates, parsed_count=len(parsed), now=received_at
            ),
        )

    def _to_envelope(
        self,
        uid: str,
        start: datetime,
        summary: str,
        received_at: datetime,
    ) -> TextEnvelope:
        raw_text = (
            f"{summary} (official scheduled date: {start.date().isoformat()}; "
            f"time not announced; source timezone: {self.source_timezone or 'unknown'})"
        )
        return TextEnvelope(
            source_id=self.manifest.source_id,
            source_type=SourceType.official_feed,
            observed_at=received_at,
            received_at=received_at,
            raw_text=raw_text,
            language="en",
            event_hint=uid,
            event_family=self.family,
            scheduled_at=start,
            source_url=TypeAdapter(HttpUrl).validate_python(self.endpoint),
            macro_event=MacroEventPayload(
                external_event_id=uid,
                family=self.family,
                scheduled_at=start,
                scheduled_date=start.date(),
                time_precision="date",
                source_timezone=self.source_timezone,
                source_authority=self.manifest.authority_level,
                status="scheduled",
            ),
            content_hash=hashlib.sha256(raw_text.encode()).hexdigest(),
        )


def parse_official_schedule_html(
    body: str,
    *,
    source_id: str,
    family: str,
    event_title: str,
) -> list[tuple[str, datetime, str]]:
    """Read meeting dates from source-specific fields, never release-date prose.

    The returned UTC midnight is a legacy day-level transport value; callers
    must retain date precision and must not infer a publication time from it.
    """
    soup = BeautifulSoup(body, "html.parser")
    if source_id == "fed-calendar":
        rows = _fed_meetings(soup)
    elif source_id == "boj-calendar":
        rows = _boj_meetings(soup)
    elif source_id == "ecb-calendar":
        rows = _ecb_meetings(soup)
    else:
        # Explicit, compact year-heading/list fixtures and simple custom feeds.
        # Official production documents use only their structural adapters.
        rows = _simple_meetings(soup)
    events: dict[str, tuple[str, datetime, str]] = {}
    for scheduled_at, text in rows:
        uid = f"{source_id}:{family}:{scheduled_at.date().isoformat()}"
        events[uid] = (uid, scheduled_at, f"{event_title}: {text[:240]}")
    return sorted(events.values(), key=lambda item: (item[1], item[0]))


def _text(element: Tag) -> str:
    return " ".join(element.get_text(" ", strip=True).split())


def _meeting_end(year: int, month_text: str, day_text: str) -> datetime:
    # Fed uses e.g. Jan/Feb 31-1. BOJ uses Jan. 22 (Thurs.), 23 (Fri.).
    clean = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", day_text)
    months = re.findall(
        r"\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
        r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\b",
        month_text,
        re.IGNORECASE,
    )
    days = re.findall(r"(?<!\d)\d{1,2}(?!\d)", clean)
    if not months or not days:
        raise ValueError("source_parse_error:meeting_date_missing")
    month = next(value for name, value in _MONTHS.items() if name[:3] == months[-1][:3].casefold())
    try:
        return datetime(year, month, int(days[-1]), tzinfo=UTC)
    except ValueError as exc:
        raise ValueError("source_parse_error:meeting_date_invalid") from exc


def _fed_meetings(soup: BeautifulSoup) -> list[tuple[datetime, str]]:
    rows = []
    if not soup.select(".panel"):
        # Retain explicitly structured compact feeds. Only a meeting list
        # immediately after its year heading qualifies; prose notes do not.
        for heading in soup.find_all(("h2", "h3", "h4")):
            year = re.fullmatch(r"(20\d{2}) FOMC Meetings", _text(heading))
            listing = heading.find_next_sibling()
            if year and listing and listing.name in {"ul", "ol"}:
                for item in listing.find_all("li", recursive=False):
                    label = _text(item)
                    rows.append((_meeting_end(int(year[1]), label, label), label))
    for panel in soup.select(".panel"):
        heading = panel.select_one(".panel-heading")
        if heading is None or not (
            year := re.search(r"\b(20\d{2}) FOMC Meetings\b", _text(heading))
        ):
            continue
        meetings = panel.select(".fomc-meeting")
        if not meetings:
            raise ValueError("source_parse_error:fed_meeting_structure_changed")
        for meeting in meetings:
            month, day = (
                meeting.select_one(".fomc-meeting__month"),
                meeting.select_one(".fomc-meeting__date"),
            )
            if month is None or day is None:
                raise ValueError("source_parse_error:fed_meeting_fields_missing")
            label = f"{_text(month)} {_text(day)}"
            rows.append((_meeting_end(int(year[1]), _text(month), _text(day)), label))
    if not rows:
        raise ValueError("source_parse_error:fed_meetings_missing")
    return rows


def _boj_meetings(soup: BeautifulSoup) -> list[tuple[datetime, str]]:
    rows = []
    for table in soup.find_all("table"):
        if not any("Date of MPM" in _text(th) for th in table.find_all("th")):
            continue
        caption = table.find("caption")
        year = _YEAR.search(_text(caption)) if caption else None
        if year is None:
            raise ValueError("source_parse_error:boj_year_missing")
        for tr in table.select("tbody tr"):
            cells = tr.find_all("td", recursive=False)
            if not cells:
                continue
            # Only the meeting column. Outlook, opinions and minutes are
            # separate event families and must never become an MPM occurrence.
            label = re.sub(r"\s*\[PDF[^\]]*\]", "", _text(cells[0]))
            rows.append((_meeting_end(int(year[1]), label, label), label))
    if not rows:
        raise ValueError("source_parse_error:boj_meetings_missing")
    return rows


def _ecb_meetings(soup: BeautifulSoup) -> list[tuple[datetime, str]]:
    rows = []
    for dt in soup.select("main dl dt"):
        dd = dt.find_next_sibling()
        if dd is None or dd.name != "dd":
            raise ValueError("source_parse_error:ecb_description_missing")
        description = _text(dd)
        normalized = description.casefold()
        if "non-monetary" in normalized or "monetary policy meeting" not in normalized:
            continue
        if "day 1" in normalized:
            continue
        try:
            scheduled = datetime.strptime(_text(dt), "%d/%m/%Y").replace(tzinfo=UTC)
        except ValueError as exc:
            raise ValueError("source_parse_error:ecb_meeting_date_invalid") from exc
        rows.append((scheduled, f"{_text(dt)} {description}"))
    if not rows:
        raise ValueError("source_parse_error:ecb_meetings_missing")
    # Some first days lack a Day 1 label (e.g. 2028-10-11). An explicitly
    # labelled next-day conclusion identifies the same two-day meeting.
    final_days = {day for day, description in rows if "day 2" in description.casefold()}
    return [
        (day, description)
        for day, description in rows
        if "day 2" in description.casefold() or day + timedelta(days=1) not in final_days
    ]


def _simple_meetings(soup: BeautifulSoup) -> list[tuple[datetime, str]]:
    rows = []
    year = None
    for element in soup.find_all(("h1", "h2", "h3", "h4", "li", "p")):
        label = _text(element)
        if element.name in {"h1", "h2", "h3", "h4"}:
            match = _YEAR.search(label)
            year = int(match[1]) if match else None
            continue
        for match in _DATE.finditer(label):
            selected_year = int(match["year"]) if match["year"] else year
            if selected_year is not None:
                rows.append(
                    (
                        _meeting_end(selected_year, match["month"], match["end"] or match["start"]),
                        label,
                    )
                )
    return rows

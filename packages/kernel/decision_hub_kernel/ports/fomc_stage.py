"""Immutable FOMC schedules derived from official date and timing originals."""

from __future__ import annotations

import calendar
import hashlib
import json
import re
from dataclasses import asdict, dataclass, replace
from datetime import UTC, date, datetime, time
from typing import Literal
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup, Tag

CALENDAR_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
POLICY_URL = "https://www.federalreserve.gov/newsevents/pressreleases/monetary20240809a.htm"
MONTHS = {name: number for number, name in enumerate(calendar.month_name) if name}
DAY = re.compile(r"(" + "|".join(MONTHS) + r")\s+(\d{1,2})(?:,\s*(20\d{2}))?")
CLOCK = r"(\d{1,2}(?::\d{2})?\s+[ap]\.m\.)"
RULE = re.compile(
    r"policy statement at " + CLOCK + r" Eastern Time on the second day of each regularly "
    r"scheduled meeting, and the Chair holds a news conference at "
    + CLOCK
    + r" Eastern Time the same day",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class FomcHttpOriginal:
    body: bytes
    receipt: bytes


@dataclass(frozen=True)
class FomcNativeOriginal:
    """Native reader authenticates these fields; no HTTP receipt is synthesized."""

    body: bytes
    url: str
    body_sha256: str
    body_ref: str
    byte_count: int
    received_at: datetime
    native_binding: bytes
    known_at: datetime | None = None


@dataclass(frozen=True)
class FomcOriginalRef:
    url: str
    body_sha256: str
    receipt_sha256: str | None
    received_at: datetime
    native_binding_sha256: str | None = None
    known_at: datetime | None = None


@dataclass(frozen=True)
class FomcStage:
    stage_id: Literal["policy_statement", "press_conference"]
    scheduled_at: datetime
    basis: Literal["official_date_and_policy_rule"]
    publication_status: Literal["expected"]
    source_timezone: Literal["America/New_York"]
    source_refs: tuple[FomcOriginalRef, ...]


@dataclass(frozen=True)
class FomcStageSchedule:
    meeting_date: date | None
    calendar_time_precision: Literal["date"]
    status: Literal["derived_schedule", "date_only", "not_in_calendar"]
    stages: tuple[FomcStage, ...]
    source_refs: tuple[FomcOriginalRef, ...]
    known_at: datetime
    reason_codes: tuple[str, ...]
    payload_hash: str = ""


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("fomc_timezone_required")
    return value.astimezone(UTC)


def _text(value: Tag) -> str:
    return " ".join(value.get_text(" ", strip=True).split())


def _read_native(original: FomcNativeOriginal, url: str) -> tuple[str, FomcOriginalRef]:
    digest = hashlib.sha256(original.body).hexdigest()
    known = _utc(original.known_at or original.received_at)
    if (
        original.url != url
        or original.body_sha256 != digest
        or original.body_ref != "raw://sha256/" + digest
        or original.byte_count != len(original.body)
        or not original.native_binding
        or known < _utc(original.received_at)
    ):
        raise ValueError("fomc_native_original_invalid")
    return original.body.decode("utf-8"), FomcOriginalRef(
        url,
        digest,
        None,
        _utc(original.received_at),
        hashlib.sha256(original.native_binding).hexdigest(),
        known,
    )


def _read(original: FomcHttpOriginal | FomcNativeOriginal, url: str) -> tuple[str, FomcOriginalRef]:
    if isinstance(original, FomcNativeOriginal):
        return _read_native(original, url)
    try:
        receipt = json.loads(original.receipt)
        digest = hashlib.sha256(original.body).hexdigest()
        received = _utc(datetime.fromisoformat(receipt["received_at"]))
        if (
            receipt["url"] != url
            or receipt["final_url"] != url
            or type(receipt["http_status"]) is not int
            or receipt["http_status"] != 200
            or receipt["TLS_verified"] is not True
            or receipt["body_sha256"] != digest
            or receipt["body_bytes"] != len(original.body)
        ):
            raise ValueError("invalid_original")
        reference = FomcOriginalRef(
            url, digest, hashlib.sha256(original.receipt).hexdigest(), received
        )
        return original.body.decode("utf-8"), reference
    except (KeyError, TypeError, ValueError, UnicodeDecodeError) as exc:
        raise ValueError("fomc_http_original_invalid") from exc


def _dates(body: str) -> list[tuple[str, datetime, str]]:
    from packages.source_adapters.official_feeds.html_calendar import parse_official_schedule_html

    return parse_official_schedule_html(
        body,
        source_id="fed-calendar",
        family="fomc",
        event_title="Federal Open Market Committee meeting",
    )


def _meeting(body: str, requested: date) -> str | None:
    if not any(stamp.date() == requested for _, stamp, _ in _dates(body)):
        return None
    soup = BeautifulSoup(body, "html.parser")
    matching_rows: list[str] = []
    for panel in soup.select(".panel"):
        heading = panel.select_one(".panel-heading")
        if heading is None or not re.search(rf"\b{requested.year} FOMC Meetings\b", _text(heading)):
            continue
        for row in panel.select(".fomc-meeting"):
            fragment = '<div class="panel">' + str(heading) + str(row) + "</div>"
            if any(stamp.date() == requested for _, stamp, _ in _dates(fragment)):
                matching_rows.append(_text(row))
    # Unknown or duplicate rows cannot assert one unambiguous regular meeting.
    return matching_rows[0] if len(matching_rows) == 1 else "regularity unverified"


def _clock(value: str) -> time:
    match = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s+([ap])\.m\.", value, re.IGNORECASE)
    if match is None:
        raise ValueError("invalid_policy_clock")
    hour, minute = int(match[1]), int(match[2] or "0")
    if not 1 <= hour <= 12:
        raise ValueError("invalid_policy_clock")
    return time(hour % 12 + (12 if match[3].lower() == "p" else 0), minute)


def _policy(body: str) -> tuple[set[date], time, time] | None:
    article = BeautifulSoup(body, "html.parser").select_one("#article")
    rules = [] if article is None else list(RULE.finditer(_text(article)))
    if article is None or len(rules) != 1:
        return None
    rule = rules[0]
    days = set()
    for heading in article.find_all("p"):
        scope = re.fullmatch(r"For (20\d{2}):", _text(heading))
        listing = heading.find_next_sibling("ul")
        if scope is None or listing is None:
            continue
        for row in listing.find_all("li", recursive=False):
            matches = list(DAY.finditer(_text(row)))
            if len(matches) == 2:
                end = matches[-1]
                days.add(date(int(end[3] or scope[1]), MONTHS[end[1]], int(end[2])))
    try:
        statement, presser = _clock(rule[1]), _clock(rule[2])
    except ValueError:
        return None
    return (days, statement, presser) if days and statement < presser else None


def _json_date(value: object) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError("unexpected_fomc_schedule_value")


def _result(
    meeting: date | None,
    status: Literal["derived_schedule", "date_only", "not_in_calendar"],
    refs: tuple[FomcOriginalRef, ...],
    stages: tuple[FomcStage, ...] = (),
    reasons: tuple[str, ...] = (),
) -> FomcStageSchedule:
    result = FomcStageSchedule(
        meeting,
        "date",
        status,
        stages,
        refs,
        max(ref.known_at or ref.received_at for ref in refs),
        reasons,
    )
    body = asdict(result)
    body.pop("payload_hash")
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"), default=_json_date).encode()
    return replace(result, payload_hash=hashlib.sha256(raw).hexdigest())


def _stages(
    meeting: date, statement: time, presser: time, refs: tuple[FomcOriginalRef, ...]
) -> tuple[FomcStage, ...]:
    stage_clocks: tuple[tuple[Literal["policy_statement", "press_conference"], time], ...] = (
        ("policy_statement", statement),
        ("press_conference", presser),
    )
    return tuple(
        FomcStage(
            stage,
            datetime.combine(meeting, clock, tzinfo=ZoneInfo("America/New_York")).astimezone(UTC),
            "official_date_and_policy_rule",
            "expected",
            "America/New_York",
            refs,
        )
        for stage, clock in stage_clocks
    )


def derive_fomc_stage_schedule(
    calendar_original: FomcHttpOriginal | FomcNativeOriginal,
    policy_original: FomcHttpOriginal | FomcNativeOriginal | None,
    *,
    meeting_date: date,
    as_of: datetime,
) -> FomcStageSchedule:
    """Caller authenticates captured provenance; this pure value grants no permission."""
    cutoff = _utc(as_of)
    body, calendar_ref = _read(calendar_original, CALENDAR_URL)
    if (calendar_ref.known_at or calendar_ref.received_at) > cutoff:
        raise ValueError("fomc_calendar_not_known_at_cutoff")
    label = _meeting(body, meeting_date)
    if label is None:
        return _result(
            None, "not_in_calendar", (calendar_ref,), reasons=("meeting_not_in_calendar",)
        )
    if re.search(
        r"cancelled|canceled|unscheduled|notational|regularity unverified", label, re.IGNORECASE
    ):
        return _result(
            meeting_date,
            "date_only",
            (calendar_ref,),
            reasons=("meeting_not_regular_or_cancelled",),
        )
    if policy_original is None:
        return _result(
            meeting_date, "date_only", (calendar_ref,), reasons=("policy_original_unavailable",)
        )
    policy_body, policy_ref = _read(policy_original, POLICY_URL)
    if (policy_ref.known_at or policy_ref.received_at) > cutoff:
        return _result(
            meeting_date, "date_only", (calendar_ref,), reasons=("policy_not_known_at_cutoff",)
        )
    rule = _policy(policy_body)
    if rule is None:
        return _result(
            meeting_date,
            "date_only",
            (calendar_ref, policy_ref),
            reasons=("policy_rule_unavailable_or_conflicting",),
        )
    dates, statement, presser = rule
    if meeting_date not in dates:
        return _result(
            meeting_date,
            "date_only",
            (calendar_ref, policy_ref),
            reasons=("meeting_not_covered_by_policy",),
        )
    refs = (calendar_ref, policy_ref)
    return _result(
        meeting_date, "derived_schedule", refs, _stages(meeting_date, statement, presser, refs)
    )


def stages_in_window(
    schedule: FomcStageSchedule, start: datetime, end: datetime
) -> tuple[FomcStage, ...]:
    lower, upper = _utc(start), _utc(end)
    if lower >= upper or schedule.known_at > lower:
        raise ValueError("fomc_stage_window_invalid_or_unknown")
    return tuple(stage for stage in schedule.stages if lower < stage.scheduled_at <= upper)

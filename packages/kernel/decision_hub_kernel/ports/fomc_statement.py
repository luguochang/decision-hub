"""Explicit official statement numeric facts; capture/PIT and review stay external."""

from __future__ import annotations

import hashlib
import re
from calendar import month_name
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Literal, cast
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup, Tag

NUMBER = r"(?:[0-9]+/[0-9]+|[0-9]+(?:\.[0-9]+)?(?:-[0-9]+/[0-9]+)?)"
ACTION = re.compile(
    r"\bthe Committee decided to (maintain|raise|lower) the target range "
    r"for the federal funds rate "
    r"(at|by) (?:("
    + NUMBER
    + r") percentage points? to )?("
    + NUMBER
    + r") to ("
    + NUMBER
    + r") percent\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class FomcStatement:
    meeting_date: date
    released_at: datetime
    target_lower: Decimal
    target_upper: Decimal
    rate_change_bp: Decimal
    action: Literal["maintain", "raise", "lower"]
    source_url: str
    raw_body_hash: str
    statement_text_hash: str


def _one(root: BeautifulSoup | Tag, selector: str) -> Tag:
    rows = root.select(selector)
    if len(rows) != 1:
        raise ValueError("fomc_statement_identity_ambiguous")
    return rows[0]


def _number(value: str) -> Decimal:
    if re.fullmatch(NUMBER, value) is None or len(value) > 24:
        raise ValueError("fomc_statement_number_invalid")
    if "/" not in value:
        return Decimal(value)
    integer, fraction = value.split("-", 1) if "-" in value else ("0", value)
    numerator, denominator = (int(part) for part in fraction.split("/"))
    if denominator not in {2, 4, 8, 16} or not 0 <= numerator < denominator:
        raise ValueError("fomc_statement_fraction_unsupported")
    return Decimal(integer) + Decimal(numerator) / Decimal(denominator)


def _url_date(value: str) -> date:
    try:
        target = urlsplit(value)
        match = re.fullmatch(r"/newsevents/pressreleases/monetary(20\d{6})[a-z]\.htm", target.path)
        if (
            target.scheme != "https"
            or target.hostname not in {"www.federalreserve.gov", "federalreserve.gov"}
            or target.port not in {None, 443}
            or target.username
            or target.password
            or target.query
            or target.fragment
            or re.search(r"[\x00-\x20\x7f]", value)
            or match is None
        ):
            raise ValueError("fomc_statement_url_invalid")
        text = match[1]
        return date(int(text[:4]), int(text[4:6]), int(text[6:]))
    except ValueError as error:
        raise ValueError("fomc_statement_url_invalid") from error


def _clock(root: Tag, day: date) -> datetime:
    header = " ".join(_one(root, ".article__time").get_text(" ", strip=True).split())
    if header != f"{month_name[day.month]} {day.day}, {day.year}":
        raise ValueError("fomc_statement_date_mismatch")
    clock = _one(root, ".releaseTime")
    text = " ".join(" ".join(clock.find_all(string=True, recursive=False)).split())
    match = re.fullmatch(r"For release at (\d{1,2}):(\d{2}) ([ap])\.m\. (EDT|EST)", text)
    if match is None or not 1 <= int(match[1]) <= 12:
        raise ValueError("fomc_statement_clock_invalid")
    naive = datetime(
        day.year,
        day.month,
        day.day,
        int(match[1]) % 12 + (12 if match[3] == "p" else 0),
        int(match[2]),
    )
    zone = ZoneInfo("America/New_York")
    candidates = {
        local.astimezone(UTC)
        for fold in (0, 1)
        if (local := naive.replace(tzinfo=zone, fold=fold)).tzname() == match[4]
        and local.astimezone(UTC).astimezone(zone).replace(tzinfo=None) == naive
    }
    if len(candidates) != 1:
        raise ValueError("fomc_statement_clock_invalid")
    return candidates.pop()


def _decision(root: Tag) -> tuple[Decimal, Decimal, Decimal, Literal["maintain", "raise", "lower"]]:
    text = " ".join(root.get_text(" ", strip=True).split())
    matches = list(ACTION.finditer(text))
    if len(matches) != 1:
        raise ValueError("fomc_statement_decision_missing_or_ambiguous")
    action, connector, size, lower, upper = matches[0].groups()
    action = action.casefold()
    if (action == "maintain") != (connector == "at" and size is None):
        raise ValueError("fomc_statement_action_invalid")
    low, high = _number(lower), _number(upper)
    if low > high:
        raise ValueError("fomc_statement_range_invalid")
    change = Decimal(0)
    if action != "maintain":
        if connector.casefold() != "by" or size is None or _number(size) <= 0:
            raise ValueError("fomc_statement_action_invalid")
        change = _number(size) * 100
    if action == "lower":
        change = -change
    return low, high, change, cast(Literal["maintain", "raise", "lower"], action)


def parse_fomc_statement(body: bytes, *, source_url: str) -> FomcStatement:
    if not body or len(body) > 2 * 1024 * 1024:
        raise ValueError("fomc_statement_document_size_invalid")
    day = _url_date(source_url)
    root = _one(BeautifulSoup(body.decode("utf-8"), "html.parser"), "#article")
    if (
        " ".join(_one(root, "h3.title").get_text(" ", strip=True).split())
        != "Federal Reserve issues FOMC statement"
    ):
        raise ValueError("fomc_statement_title_invalid")
    low, high, change, action = _decision(root)
    return FomcStatement(
        day,
        _clock(root, day),
        low,
        high,
        change,
        action,
        source_url,
        hashlib.sha256(body).hexdigest(),
        hashlib.sha256(" ".join(root.get_text(" ", strip=True).split()).encode()).hexdigest(),
    )

"""Pure BEA PIO release text; capture/PIT/first-vintage authentication is external."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup, Tag

MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
CHANGE = r"(increased|decreased) ([0-9]+(?:\.[0-9]+)?) percent"
PREFIXES = ("From the preceding month,", "From the same month one year ago,")


@dataclass(frozen=True, slots=True)
class PceRelease:
    reference_period: str
    release_number: str
    released_at: datetime
    source_url: str
    raw_body_hash: str
    headline_mom: Decimal
    core_mom: Decimal
    headline_yoy: Decimal
    core_yoy: Decimal
    unit: Literal["percent"]


def _text(node: Tag) -> str:
    return " ".join(node.get_text(" ", strip=True).split())


def _one(node: BeautifulSoup | Tag, selector: str) -> Tag:
    values = node.select(selector)
    if len(values) != 1:
        raise ValueError("pce_release_identity_ambiguous")
    return values[0]


def _source_path(source_url: str) -> str:
    if re.search(r"[\x00-\x20\x7f]", source_url):
        raise ValueError("pce_release_source_invalid")
    try:
        url = urlsplit(source_url)
        valid = (
            url.scheme == "https" and url.hostname in {"www.bea.gov", "bea.gov"}
            and url.port in {None, 443} and not url.username and not url.password
            and not url.query and not url.fragment
        )
    except ValueError as error:
        raise ValueError("pce_release_source_invalid") from error
    if not valid:
        raise ValueError("pce_release_source_invalid")
    return url.path


def _period(article: Tag) -> tuple[str, str, str]:
    titles = [_text(tag) for tag in article.select("h1")
              if _text(tag).startswith("Personal Income and Outlays")]
    match = re.fullmatch(r"Personal Income and Outlays, ([A-Za-z]+) (20\d{2})",
                         titles[0] if len(titles) == 1 else "")
    if match is None or match[1] not in MONTHS:
        raise ValueError("pce_release_identity_title_invalid")
    month, year = match.groups()
    return f"{year}-{MONTHS.index(month) + 1:02d}", month, year


def _clock(article: Tag) -> tuple[datetime, str]:
    embargo = _one(article, ".release-embargo")
    text = _text(_one(embargo, ".field--name-field-release-date"))
    match = re.fullmatch(
        r"EMBARGOED UNTIL RELEASE AT (\d{1,2}):(\d{2}) ([ap])\.m\. (EDT|EST), "
        r"([A-Za-z]+), ([A-Za-z]+) (\d{1,2}), (20\d{2})", text,
    )
    if match is None:
        raise ValueError("pce_release_clock_invalid")
    hour, minute, ampm, abbreviation, weekday, month, day, year = match.groups()
    if month not in MONTHS or not 1 <= int(hour) <= 12:
        raise ValueError("pce_release_clock_invalid")
    try:
        naive = datetime(int(year), MONTHS.index(month) + 1, int(day),
                         int(hour) % 12 + (12 if ampm == "p" else 0), int(minute))
    except ValueError as error:
        raise ValueError("pce_release_clock_invalid") from error
    zone = ZoneInfo("America/New_York")
    candidates: set[datetime] = set()
    for fold in (0, 1):
        local = naive.replace(tzinfo=zone, fold=fold)
        utc = local.astimezone(UTC)
        if local.tzname() == abbreviation and utc.astimezone(zone).replace(tzinfo=None) == naive:
            candidates.add(utc)
    if len(candidates) != 1 or WEEKDAYS[naive.weekday()] != weekday:
        raise ValueError("pce_release_clock_invalid")
    numbers = re.findall(r"\bBEA (\d{2})[–-](\d+)\b", _text(embargo))
    if len(numbers) != 1 or numbers[0][0] != year[-2:]:
        raise ValueError("pce_release_identity_release_number_invalid")
    return candidates.pop(), "BEA " + "-".join(numbers[0])


def _prices(body: Tag, month: str, *, yearly: bool) -> tuple[Decimal, Decimal]:
    prefix = PREFIXES[int(yearly)]
    paragraphs = [_text(p) for p in body.select("p") if _text(p).startswith(prefix)]
    tail = " from one year ago" if yearly else ""
    pattern = (
        re.escape(prefix) + r" the PCE price index for " + re.escape(month) + " " + CHANGE
        + r"\s*\. Excluding food and energy, the PCE price index " + CHANGE
        + re.escape(tail) + r"\s*\."
    )
    match = re.fullmatch(pattern, paragraphs[0] if len(paragraphs) == 1 else "")
    if match is None:
        raise ValueError("pce_release_price_paragraph_missing_or_ambiguous")
    headline_verb, headline, core_verb, core = match.groups()
    return (
        Decimal(headline).copy_negate() if headline_verb == "decreased" else Decimal(headline),
        Decimal(core).copy_negate() if core_verb == "decreased" else Decimal(core),
    )


def _table_column(table: Tag, month: str) -> tuple[list[list[str]], int, int]:
    headings = table.select("thead")
    heading = _text(headings[0]).casefold() if len(headings) == 1 else ""
    rows = [[_text(cell) for cell in row.select(":scope > td")]
            for row in table.select("tbody > tr")]
    if not rows:
        raise ValueError("pce_release_table_missing")
    prefix = "personal income and related measures [percent change from "
    if rows[0] and rows[0][0] == "":
        columns = rows.pop(0)
        if (heading != prefix + "preceding month]" or columns.count(month) != 1
                or any(value not in MONTHS for value in columns[1:])
                or len(set(columns)) != len(columns)):
            raise ValueError("pce_release_table_period_ambiguous")
        return rows, columns.index(month), len(columns)
    previous = MONTHS[(MONTHS.index(month) - 1) % 12]
    if heading != prefix + previous.casefold() + " to " + month.casefold() + "]":
        raise ValueError("pce_release_table_period_ambiguous")
    return rows, 1, 2


def _check_table(body: Tag, month: str, expected: tuple[Decimal, Decimal]) -> None:
    tables = [table for table in body.select("table")
              if _text(table).startswith("Personal Income and Related Measures")]
    if len(tables) != 1:
        raise ValueError("pce_release_table_missing_or_ambiguous")
    rows, column, width = _table_column(tables[0], month)
    for label, value in zip(
        ("PCE price index", "PCE price index excluding food and energy"), expected, strict=True,
    ):
        selected = [row for row in rows if row and row[0].replace("index,", "index") == label]
        if len(selected) != 1 or len(selected[0]) != width:
            raise ValueError("pce_release_table_metric_missing_or_ambiguous")
        text = selected[0][column]
        if not re.fullmatch(r"[+-]?[0-9]+(?:\.[0-9]+)?", text) or Decimal(text) != value:
            raise ValueError("pce_release_table_price_conflict")


def parse_pce_release(body: bytes, *, source_url: str) -> PceRelease:
    """Parse explicit release-month prices; never infer consensus or first-known time."""
    if not body or len(body) > 2 * 1024 * 1024:
        raise ValueError("pce_release_document_size_invalid")
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("pce_release_document_encoding_invalid") from error
    source_path = _source_path(source_url)
    article = _one(BeautifulSoup(text, "html.parser"), "article")
    period, month, reference_year = _period(article)
    released_at, release_number = _clock(article)
    local_release = released_at.astimezone(ZoneInfo("America/New_York"))
    expected_path = (f"/news/{local_release.year}/personal-income-and-outlays-"
                     f"{month.casefold()}-{reference_year}")
    if (source_path != expected_path or article.get("about") != source_path
            or period > local_release.strftime("%Y-%m")):
        raise ValueError("pce_release_identity_url_mismatch")
    release_body = _one(article, ".release-body")
    monthly = _prices(release_body, month, yearly=False)
    yearly = _prices(release_body, month, yearly=True)
    _check_table(release_body, month, monthly)
    return PceRelease(
        reference_period=period, release_number=release_number, released_at=released_at,
        source_url=source_url, raw_body_hash=hashlib.sha256(body).hexdigest(),
        headline_mom=monthly[0], core_mom=monthly[1], headline_yoy=yearly[0], core_yoy=yearly[1],
        unit="percent",
    )

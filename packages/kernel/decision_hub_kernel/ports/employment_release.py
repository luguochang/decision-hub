"""Pure BLS employment edition parser; document values grant no execution authority."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

MONTHS = (
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
)
COUNT = r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)"


@dataclass(frozen=True, slots=True)
class EmploymentRevision:
    reference_period: str
    previous: str
    revised: str
    difference: str


@dataclass(frozen=True, slots=True)
class EmploymentRelease:
    reference_period: str
    release_id: str
    released_at: datetime
    payroll_change: str
    unemployment_rate: str
    earnings_mom: str
    earnings_yoy: str
    revisions: tuple[EmploymentRevision, ...]


def _one(pattern: str, text: str, reason: str) -> re.Match[str]:
    matches = list(re.finditer(pattern, text, re.IGNORECASE))
    if len(matches) != 1:
        raise ValueError("employment_release_" + reason)
    return matches[0]


def _document(raw: bytes) -> str:
    soup = BeautifulSoup(raw.decode("utf-8-sig"), "html.parser")
    candidates = [
        p.get_text()
        for p in soup.find_all("pre")
        if "THE EMPLOYMENT SITUATION" in p.get_text().upper()
    ]
    if len(candidates) != 1:
        raise ValueError("employment_release_unique_document_required")
    return candidates[0].replace("\r\n", "\n").replace("\xa0", " ")


def _edition(text: str, source_url: str, period: str, expected: datetime) -> tuple[str, str]:
    url = urlsplit(source_url)
    path = re.fullmatch(r"/news\.release/archives/empsit_(\d{8})\.htm", url.path)
    if (
        url.scheme != "https"
        or url.hostname not in {"www.bls.gov", "bls.gov"}
        or url.username
        or url.password
        or url.port not in {None, 443}
        or url.query
        or url.fragment
        or path is None
    ):
        raise ValueError("employment_release_official_edition_url_required")
    title = _one(
        r"THE EMPLOYMENT SITUATION\s*[—–-]\s*([A-Z]+)\s+(20\d{2})",
        text,
        "reference_period_ambiguous",
    )
    month, year = title.group(1).title(), title.group(2)
    if month not in MONTHS or f"{year}-{MONTHS.index(month) + 1:02d}" != period:
        raise ValueError("employment_release_reference_period_mismatch")
    header = text[: title.start()]
    if (
        "Transmission of material in this news release is embargoed until" not in header
        or re.search(r"\b(?:CORRECTED|UPDATED|REVISED)\b", header, re.IGNORECASE)
    ):
        raise ValueError("employment_release_first_edition_unclear")
    release_id = _one(r"\bUSDL-\d{2}-\d+\b", header, "release_id_ambiguous").group()
    clock = _one(
        r"(\d{1,2}:\d{2})\s*([ap])\.m\.\s*\(ET\)\s*"
        r"(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s*"
        r"([A-Za-z]+ \d{1,2}, 20\d{2})",
        header,
        "publication_clock_ambiguous",
    )
    day = datetime.strptime(clock.group(4), "%B %d, %Y")
    minute = datetime.strptime(clock.group(1) + " " + clock.group(2) + "m", "%I:%M %p")
    declared = datetime.combine(day.date(), minute.time(), ZoneInfo("America/New_York"))
    if (
        day.strftime("%A") != clock.group(3)
        or release_id.split("-")[1] != day.strftime("%y")
        or expected.tzinfo is None
        or expected.utcoffset() is None
        or declared.astimezone(UTC) != expected.astimezone(UTC)
        or path.group(1) != day.strftime("%m%d%Y")
    ):
        raise ValueError("employment_release_publication_clock_mismatch")
    return month, release_id


def _paragraphs(text: str) -> list[str]:
    return [" ".join(p.split()) for p in re.split(r"\n\s*\n", text) if p.strip()]


def _payroll(paragraph: str, month: str) -> str:
    positive = r"(?:rose|increased|grew|edged up)"
    negative = r"(?:fell|declined|decreased|edged down)"
    direct = re.match(
        r"Total nonfarm payroll employment ("
        + positive
        + "|"
        + negative
        + r") by ("
        + COUNT
        + ") in "
        + month
        + r"\b",
        paragraph,
    )
    if direct is not None:
        value = int(direct.group(2).replace(",", ""))
        if value < 0:
            raise ValueError("employment_release_payroll_direction_ambiguous")
        return str(-value if re.fullmatch(negative, direct.group(1)) else value)
    parenthetical = _one(
        r"^Total nonfarm payroll employment (?:changed little|was little changed)"
        + " in "
        + month
        + r" \(("
        + COUNT
        + r")\)",
        paragraph,
        "current_payroll_unavailable",
    )
    return str(int(parenthetical.group(1).replace(",", "")))


def _earnings(paragraphs: list[str], month: str) -> tuple[str, str]:
    selected = [
        p
        for p in paragraphs
        if "average hourly earnings for all employees on private nonfarm payrolls" in p
    ]
    if len(selected) != 1:
        raise ValueError("employment_release_all_employee_earnings_unavailable")
    paragraph = selected[0]
    sentences = re.split(r"\.\s+(?=[A-Z])", paragraph)
    first = sentences[0]
    if not first.startswith("In " + month + ", average hourly earnings for all employees"):
        raise ValueError("employment_release_earnings_period_mismatch")
    rate = _one(
        r"(?<!\d)(\d+(?:\.\d+)?) percent\b", first, "published_monthly_earnings_rate_required"
    ).group(1)
    if re.search(r"\b(?:fell|declined|decreased|edged down)\b", first):
        rate = "-" + rate if Decimal(rate) else rate
    elif not re.search(r"\b(?:rose|increased|edged up)\b", first):
        if not ("unchanged" in first and Decimal(rate) == 0):
            raise ValueError("employment_release_earnings_direction_ambiguous")
    annual = _one(
        r"^Over the past 12 months, average hourly earnings have "
        r"(increased|decreased) by (\d+(?:\.\d+)?) percent\.?$",
        sentences[1] if len(sentences) > 1 else "",
        "published_annual_earnings_rate_required",
    )
    yoy = "-" if annual.group(1) == "decreased" and Decimal(annual.group(2)) else ""
    return rate, yoy + annual.group(2)


def _unemployment(paragraph: str, month: str) -> str:
    sentences = [
        sentence
        for sentence in re.split(r"\.\s+(?=[A-Z])", paragraph)
        if re.search(r"\bunemployment rate\b", sentence, re.IGNORECASE)
    ]
    if len(sentences) != 1:
        raise ValueError("employment_release_headline_unemployment_unavailable")
    periods = re.findall(r"\b(?:" + "|".join(MONTHS) + r")\b", sentences[0], re.IGNORECASE)
    if {value.casefold() for value in periods} != {month.casefold()}:
        raise ValueError("employment_release_household_reference_period_mismatch")
    rate = _one(
        r"\bunemployment rate(?:, at| (?:was|stood at|remained at|changed little at|"
        r"rose to|fell to|increased to|decreased to))\s+(\d+(?:\.\d+)?) percent\b",
        sentences[0],
        "headline_unemployment_unavailable",
    ).group(1)
    if not Decimal(0) <= Decimal(rate) <= Decimal(100):
        raise ValueError("employment_release_unemployment_range_invalid")
    return rate


def _revisions(paragraphs: list[str], period: str) -> tuple[EmploymentRevision, ...]:
    rows = [p for p in paragraphs if "The change in total nonfarm payroll employment for" in p]
    if not rows:
        return ()
    if len(rows) != 1:
        raise ValueError("employment_release_revisions_ambiguous")
    pattern = (
        r"(?:The change in total nonfarm payroll employment for|and the change for) "
        r"([A-Za-z]+) was revised (up|down) by ("
        + COUNT
        + r"), from ("
        + COUNT
        + r") to ("
        + COUNT
        + r")"
    )
    matches = list(re.finditer(pattern, rows[0]))
    year, month = map(int, period.split("-"))
    previous = {}
    for ago in (2, 1):
        index = year * 12 + month - 1 - ago
        previous[MONTHS[index % 12]] = f"{index // 12}-{index % 12 + 1:02d}"
    if len(matches) != 2 or {m.group(1) for m in matches} != set(previous):
        raise ValueError("employment_release_revision_months_ambiguous")
    result = []
    for match in matches:
        amount, before, after = [int(match.group(i).replace(",", "")) for i in (3, 4, 5)]
        difference = amount if match.group(2) == "up" else -amount
        if amount < 0 or after - before != difference:
            raise ValueError("employment_release_revision_difference_invalid")
        result.append(
            EmploymentRevision(previous[match.group(1)], str(before), str(after), str(difference))
        )
    return tuple(sorted(result, key=lambda row: row.reference_period))


def parse_employment_release(
    raw: bytes,
    *,
    source_url: str,
    expected_period: str,
    expected_release_at: datetime,
) -> EmploymentRelease:
    """Read exactly this declared edition; receipt/knowledge clocks belong to its native reader."""
    text = _document(raw)
    month, release_id = _edition(text, source_url, expected_period, expected_release_at)
    if text.count("Household Survey Data") != 1 or text.count("Establishment Survey Data") != 1:
        raise ValueError("employment_release_survey_sections_ambiguous")
    household, establishment = text.split("Household Survey Data", 1)[1].split(
        "Establishment Survey Data", 1
    )
    household_rows, establishment_rows = _paragraphs(household), _paragraphs(establishment)
    if not household_rows or not establishment_rows:
        raise ValueError("employment_release_survey_headlines_missing")
    if sum(p.count("Total nonfarm payroll employment") for p in establishment_rows) != 1:
        raise ValueError("employment_release_current_payroll_ambiguous")
    rate = _unemployment(household_rows[0], month)
    mom, yoy = _earnings(establishment_rows, month)
    return EmploymentRelease(
        expected_period,
        release_id,
        expected_release_at.astimezone(UTC),
        _payroll(establishment_rows[0], month),
        rate,
        mom,
        yoy,
        _revisions(establishment_rows, expected_period),
    )


# Shared pure document primitives; no new parsing or authority semantics.
employment_release_document = _document
employment_release_single_match = _one

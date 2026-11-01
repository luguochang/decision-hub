"""Explicit BLS next-release periods and frozen, prospective NFP source rules."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from .employment_release import (
    MONTHS,
    employment_release_document,
    parse_employment_release,
)
from .employment_release import (
    employment_release_single_match as _one,
)
from .market_path import aware_utc, canonical_hash

ADAPTER = "bls_employment_release_selector.v1"
RSS_URL = "https://www.bls.gov/feed/bls_latest.rss"
CALENDAR_URL = "https://www.bls.gov/schedule/news_release/bls.ics"
METRICS = (
    ("nonfarm_payroll_change", "persons"),
    ("unemployment_rate", "percent"),
    ("average_hourly_earnings_mom", "percent"),
)
Body = Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class EmploymentNextRelease:
    previous_period: str
    previous_released_at: datetime
    reference_period: str
    scheduled_at: datetime
    announcement: str


def _clock(date: str, clock: str, weekday: str) -> datetime:
    day = datetime.strptime(date, "%B %d, %Y")
    minute = datetime.strptime(clock.replace(".", ""), "%I:%M %p")
    if day.strftime("%A") != weekday:
        raise ValueError("employment_next_release_weekday_mismatch")
    return datetime.combine(day.date(), minute.time(), ZoneInfo("America/New_York")).astimezone(UTC)


def parse_employment_next_release(raw: bytes, *, source_url: str) -> EmploymentNextRelease:
    """Both reference year/month and release clock must be written by the publisher."""
    text = " ".join(employment_release_document(raw).split())
    title = _one(
        r"THE EMPLOYMENT SITUATION\s*[—–-]\s*([A-Z]+)\s+(20\d{2})",
        text,
        "reference_period_ambiguous",
    )
    if title[1].title() not in MONTHS:
        raise ValueError("employment_next_release_previous_period_invalid")
    previous = f"{title[2]}-{MONTHS.index(title[1].title()) + 1:02d}"
    header = _one(
        r"(\d{1,2}:\d{2}\s*[ap]\.m\.)\s*\(ET\)\s*"
        r"(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s*"
        r"([A-Za-z]+ \d{1,2}, 20\d{2})",
        text[: title.start()],
        "publication_clock_ambiguous",
    )
    released = _clock(header[3], header[1], header[2])
    parse_employment_release(
        raw, source_url=source_url, expected_period=previous, expected_release_at=released
    )
    _one(r"The Employment Situation for\b", text, "next_announcement_ambiguous")
    announced = _one(
        r"The Employment Situation for ([A-Za-z]+) (20\d{2}) is scheduled to be "
        r"(?:published|released) on (Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday), "
        r"([A-Za-z]+ \d{1,2}, 20\d{2}), at (\d{1,2}:\d{2}\s*[ap]\.m\.) \(ET\)\.",
        text,
        "next_announcement_explicit_period_and_clock_required",
    )
    if announced[1].title() not in MONTHS:
        raise ValueError("employment_next_release_reference_month_invalid")
    period = f"{announced[2]}-{MONTHS.index(announced[1].title()) + 1:02d}"
    scheduled = _clock(announced[4], announced[5], announced[3])
    period_start = datetime.fromisoformat(period + "-01T00:00:00+00:00")
    if not previous < period or not released < scheduled or period_start > scheduled:
        raise ValueError("employment_next_release_period_or_clock_inconsistent")
    return EmploymentNextRelease(previous, released, period, scheduled, announced[0])


def employment_selector_rule(event: Body, period: str, evidence_ref: Body) -> dict[str, Any]:
    if (
        re.fullmatch(r"20\d{2}-(0[1-9]|1[0-2])", period) is None
        or event["family"] != "nfp"
        or event["stage_id"] != "release"
        or not evidence_ref["ref"].startswith("derived:nfp-next-release:")
        or re.fullmatch(r"[0-9a-f]{64}", evidence_ref["hash"]) is None
    ):
        raise ValueError("employment_selector_identity_invalid")
    at = aware_utc(datetime.fromisoformat(event["scheduled_at"].replace("Z", "+00:00")))
    return dict(
        schema_version="event-scenario-observation-rule.v1",
        adapter=ADAPTER,
        source_id="bls-releases",
        adapter_version="official-feed.v2",
        feed_url=RSS_URL,
        event_key=event["event_key"],
        family="nfp",
        stage_id="release",
        scheduled_at=at.isoformat().replace("+00:00", "Z"),
        reference_period=period,
        vintage_policy="current_reference_month_first_estimate",
        period_evidence_ref=dict(evidence_ref),
        selector=dict(
            title_family="Employment Situation",
            official_hosts=["www.bls.gov", "bls.gov"],
            path_policy="reviewed_empsit_archived_edition.v1",
        ),
        metrics=[
            dict(metric_id=name, unit=unit, reference_period=period, stage_id="release")
            for name, unit in METRICS
        ],
    )


def employment_source_rule(entry: Body) -> dict[str, Any] | None:
    original = json.loads(entry["original_json"])
    proof = original.get("employment_next_release_original")
    if not isinstance(proof, dict):
        return None
    parsed = original.get("parsed", {})
    payload = parsed.get("macro_event", {})
    text = parsed.get("raw_text", "")
    digest = canonical_hash(proof)
    reference = dict(ref="derived:nfp-next-release:" + digest, hash=digest)
    if (
        entry["source_id"] != "bls-calendar"
        or entry["family"] != "nfp"
        or entry["stage_id"] != "release"
        or entry["time_precision"] != "exact"
        or original.get("source_id") != "bls-calendar"
        or parsed.get("source_id") != "bls-calendar"
        or parsed.get("source_url") != CALENDAR_URL
        or payload.get("family") != "nfp"
        or payload.get("time_precision") != "exact"
        or payload.get("status") != "scheduled"
        or payload.get("external_event_id") != entry["event_key"]
        or payload.get("scheduled_at") != entry["scheduled_at"]
        or hashlib.sha256(text.encode()).hexdigest() != entry["source_ref"]["hash"]
        or proof.get("schema_version") != "nfp-next-release-original.v1"
        or proof.get("event_key") != entry["event_key"]
        or proof.get("calendar_ref") != entry["source_ref"]
        or proof.get("scheduled_at") != entry["scheduled_at"]
        or reference not in entry["dependency_refs"]
        or proof.get("reference_period_basis") != "explicit_prior_official_next_release_statement"
    ):
        return None
    rule = employment_selector_rule(entry, proof["reference_period"], reference)
    rule_hash = canonical_hash(rule)
    return dict(
        schedule_ref=entry["source_ref"],
        observation_rule_ref=dict(ref="event-source-rule:" + rule_hash, hash=rule_hash),
        rule=rule,
    )


def declared_employment_source_rule(
    event: Body, supported: list[Body], entries: list[Body]
) -> dict[str, Any]:
    reference = event["observation_rule_ref"]
    selected = [item for item in supported if item["observation_rule_ref"] == reference]
    matches = [
        entry
        for entry in entries
        if all(
            entry[key] == event[key] for key in ("event_key", "family", "stage_id", "scheduled_at")
        )
    ]
    if len(selected) != 1 or len(matches) != 1:
        raise ValueError("event_native_employment_source_rule_pending")
    expected = employment_source_rule(matches[0])
    if expected is None or selected[0] != expected:
        raise ValueError("event_native_employment_source_rule_pending")
    return dict(
        source_ref=reference,
        role="observation_rule",
        body_json=json.dumps(
            expected["rule"],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ),
    )

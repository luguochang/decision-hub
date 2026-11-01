"""FRED observation periods are not publication clocks.

This module checks time eligibility only. Publication/version references must
come from authenticated source originals; a nonempty reference is not itself
proof. Current fredgraph CSV supplies neither of them. Kernel ``known_at`` is
the admitted FactVersion clock, not the provider's receipt clock.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from typing import Literal

PublicTimePrecision = Literal["unknown", "date", "second", "microsecond"]


def fred_observation_date(value: str) -> date:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("fred_observation_date_invalid")
    return date.fromisoformat(value)


def fred_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("fred_time_timezone_required")
    return value.astimezone(UTC)


@dataclass(frozen=True)
class FredTimeQualification:
    observation_date: date
    public_available_upper_bound: datetime | None
    known_at: datetime | None
    system_pit_eligible: bool
    historical_public_pit_eligible: bool
    minute_forward_eligible: bool
    reasons: tuple[str, ...]


def qualify_fred_time(
    *,
    observation_date: str,
    received_at: datetime,
    cutoff_at: datetime,
    known_at: datetime | None,
    published_at: datetime | None = None,
    public_time_precision: PublicTimePrecision = "unknown",
    public_date: date | None = None,
    public_day_timezone: tzinfo | None = None,
    publication_ref: str | None = None,
    version_ref: str | None = None,
    revision_at: datetime | None = None,
) -> FredTimeQualification:
    period = fred_observation_date(observation_date)
    received, cutoff = fred_utc(received_at), fred_utc(cutoff_at)
    known = None if known_at is None else fred_utc(known_at)
    published = None if published_at is None else fred_utc(published_at)
    revision = None if revision_at is None else fred_utc(revision_at)
    reasons: list[str] = []
    if public_time_precision not in {"unknown", "date", "second", "microsecond"}:
        raise ValueError("fred_public_time_precision_invalid")
    if period > cutoff.date():
        reasons.append("observation_period_after_cutoff")
    if received > cutoff:
        reasons.append("provider_received_after_cutoff")
    if known is None:
        reasons.append("kernel_known_at_unavailable")
    elif known < received:
        reasons.append("kernel_known_before_receipt")
    elif known > cutoff:
        reasons.append("kernel_known_after_cutoff")
    system_pit = not reasons

    public_bound = None
    if public_time_precision in {"second", "microsecond"} and published is not None:
        public_bound = published
    elif public_time_precision == "date" and public_date is not None:
        if public_day_timezone is None:
            reasons.append("public_day_timezone_unavailable")
        else:
            # End of the named source-local day, never UTC midnight guessed
            # from a CSV observation date. Equality at the bound is safe.
            public_bound = fred_utc(datetime.combine(
                public_date + timedelta(days=1), time.min, public_day_timezone
            ))
    else:
        reasons.append("publication_time_unavailable")
    invalid_source_clock = False
    if published is not None and published > received:
        reasons.append("publication_after_receipt")
        invalid_source_clock = True
    if revision is not None:
        if revision > received:
            reasons.append("revision_after_receipt")
            invalid_source_clock = True
        if public_bound is not None:
            public_bound = max(public_bound, revision)
    if not publication_ref or not publication_ref.strip():
        reasons.append("publication_original_unavailable")
    if not version_ref or not version_ref.strip():
        reasons.append("vintage_original_unavailable")
    if public_bound is not None and public_bound > cutoff:
        reasons.append("public_version_after_cutoff")
    historical_pit = (
        public_bound is not None
        and public_bound <= cutoff
        and period <= cutoff.date()
        and not invalid_source_clock
        and bool(publication_ref and publication_ref.strip())
        and bool(version_ref and version_ref.strip())
    )
    if public_time_precision == "date":
        reasons.append("day_precision_not_minute_qualified")
    return FredTimeQualification(
        observation_date=period,
        public_available_upper_bound=public_bound,
        known_at=known,
        system_pit_eligible=system_pit,
        historical_public_pit_eligible=historical_pit,
        minute_forward_eligible=(
            system_pit and historical_pit
            and public_time_precision in {"second", "microsecond"}
        ),
        reasons=tuple(reasons),
    )

"""Re-read official schedule bytes; current calendar projections are not proof."""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import or_, select

from ..persistence.db import (
    EventOccurrenceRecord,
    MacroEventVintageRecord,
    ObservationRecord,
    OccurrenceRecord,
    RawCaptureRecord,
    SourcePollAttemptRecord,
    as_utc,
)
from ..ports.market_path import aware_utc, canonical_hash
from ..ports.sources import SourceContentSnapshot
from .cpi_schedule_original import read_cpi_schedule_original_in_session

SOURCES = {
    "bea-calendar": (
        "https://www.bea.gov/news/schedule/ics/online-calendar-subscription.ics",
        "bea-personal-income-schedule.v1",
    ),
    "bls-calendar": ("https://www.bls.gov/schedule/news_release/bls.ics", "official-calendar.v2"),
    "fed-calendar": (
        "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
        "official-html-calendar.v2",
    ),
}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def row_body(row, names):
    result = {}
    for key in names:
        value = getattr(row, key)
        result[key] = (
            as_utc(value).isoformat().replace("+00:00", "Z")
            if isinstance(value, datetime)
            else value
        )
    return result


@dataclass(frozen=True)
class OfficialScheduleOriginal:
    source_ref: dict
    source_id: str
    event_key: str
    event_id: str
    family: str
    scheduled_at: datetime | None
    scheduled_date: object
    time_precision: str
    source_timezone: str | None
    known_at: datetime
    occurrence_ref: str | None
    identity_basis: str
    original_json: str
    raw_capture_refs: tuple[dict, ...]


class OfficialScheduleNotKnownAtCutoff(ValueError):
    """A unique native binding is valid, but its poll is not complete at cutoff."""


def _calendar_snapshot(parent: Any, version: str) -> SourceContentSnapshot | None:
    if parent.completed_at is None:
        if parent.status != "running" or parent.content_snapshot_json is not None:
            raise ValueError("official_schedule_inflight_parent_invalid")
        return None
    snapshot = SourceContentSnapshot.model_validate_json(parent.content_snapshot_json or "{}")
    if (
        parent.status != "succeeded"
        or snapshot.http_status != 200
        or snapshot.parser_version != version
        or snapshot.parse_status != "valid"
    ):
        raise ValueError("official_schedule_snapshot_invalid")
    return snapshot


def _calendar_raw(
    parent: Any, capture: Any, received: datetime, *, archive: Any, url: str, version: str
) -> bytes:
    clocks = [
        as_utc(t)
        for t in (parent.started_at, capture.started_at, capture.completed_at, capture.recorded_at)
    ]
    complete = as_utc(parent.completed_at)
    if complete is not None:
        clocks.append(complete)
    if (
        capture.url != url
        or capture.adapter_version != version
        or capture.status_code != 200
        or capture.error_code is not None
        or any(t is None for t in clocks)
        or clocks != sorted(clocks)
        or clocks[2] > received
        or (complete is not None and received > complete)
        or not isinstance(capture.body_hash, str)
        or capture.body_ref != "raw://sha256/" + capture.body_hash
    ):
        raise ValueError("official_schedule_native_binding_invalid")
    raw = archive.read(capture.body_ref)
    if len(raw) != capture.byte_count or hashlib.sha256(raw).hexdigest() != capture.body_hash:
        raise ValueError("official_schedule_archive_invalid")
    return raw


def _calendar_matches(
    session: Any, row: Any, received: datetime, *, archive: Any, url: str, version: str
) -> list[tuple[Any, Any, Any]]:
    attempts = session.scalars(
        select(SourcePollAttemptRecord).where(
            SourcePollAttemptRecord.source_id == row.source_id,
            SourcePollAttemptRecord.status.in_(("succeeded", "running")),
            SourcePollAttemptRecord.started_at <= received,
            or_(
                SourcePollAttemptRecord.completed_at >= received,
                SourcePollAttemptRecord.completed_at.is_(None),
            ),
        )
    ).all()
    matches = []
    for parent in attempts:
        snapshot = _calendar_snapshot(parent, version)
        query = select(RawCaptureRecord).where(
            RawCaptureRecord.context_id == "source:" + parent.attempt_id
        )
        if snapshot is not None:
            query = query.where(RawCaptureRecord.body_hash == snapshot.content_hash)
        captures = session.scalars(query).all()
        if snapshot is None and len(captures) != 1:
            raise ValueError("official_schedule_inflight_capture_ambiguous_or_missing")
        for capture in captures:
            raw = _calendar_raw(
                parent, capture, received, archive=archive, url=url, version=version
            )
            items = _parse(row.source_id, raw, received)
            if snapshot is not None and snapshot.parsed_count != len(items):
                raise ValueError("official_schedule_parse_count_invalid")
            selected = [
                item
                for item in items
                if item.event_hint == row.event_hint and item.raw_text == row.text
            ]
            if len(selected) == 1:
                matches.append((parent, capture, selected[0]))
    return matches


def _parse(source_id, raw, received):
    from packages.kernel.decision_hub_kernel.ports.sources import SourceManifest
    from packages.source_adapters.official_feeds.calendar import OfficialCalendarSource
    from packages.source_adapters.official_feeds.bea_release import BeaReleaseScheduleSource
    from packages.source_adapters.official_feeds.html_calendar import (
        OfficialHtmlCalendarSource,
        parse_official_schedule_html,
    )

    url, version = SOURCES[source_id]
    manifest = SourceManifest(
        source_id=source_id,
        source_type="official_feed",
        version=version,
        capabilities=("poll", "calendar", "revision"),
        authority_level="official",
        poll_interval_seconds=900,
        max_batch=100,
        allowed_domains=(
            {"bls-calendar": "bls.gov", "bea-calendar": "bea.gov",
             "fed-calendar": "federalreserve.gov"}[source_id],
        ),
    )
    if source_id in {"bls-calendar", "bea-calendar"}:
        source_class = (
            BeaReleaseScheduleSource if source_id == "bea-calendar" else OfficialCalendarSource
        )
        return source_class(
            manifest, url, source_timezone="America/New_York"
        )._parse_events(raw, received)
    source = OfficialHtmlCalendarSource(
        manifest,
        url,
        family="fomc",
        event_title="Federal Open Market Committee meeting",
        source_timezone="America/New_York",
    )
    return [
        source._to_envelope(uid, start, summary, received)
        for uid, start, summary in parse_official_schedule_html(
            raw.decode("utf-8"),
            source_id=source_id,
            family=source.family,
            event_title=source.event_title,
        )
    ]


def _vintage(session, row, payload, cutoff):
    rows = session.scalars(
        select(MacroEventVintageRecord).where(MacroEventVintageRecord.event_id == row.event_id)
    ).all()
    matches = []
    for value in rows:
        body = row_body(
            value,
            (
                "macro_event_id",
                "event_id",
                "observed_at",
                "scheduled_at",
                "scheduled_date",
                "time_precision",
                "source_timezone",
                "prior_value",
                "consensus_value",
                "actual_value",
                "surprise",
                "surprise_z",
                "revision_flag",
                "status",
            ),
        )
        # Existing macro ledger seals datetime.isoformat() (+00:00), not Z.
        for key in ("observed_at", "scheduled_at"):
            if body[key] is not None:
                body[key] = body[key].replace("Z", "+00:00")
        if (
            value.payload_hash == canonical_hash(body)
            and as_utc(value.created_at) <= cutoff
            and as_utc(value.observed_at) == as_utc(row.received_at)
            and as_utc(value.scheduled_at) == payload.scheduled_at
            and value.time_precision == payload.time_precision
            and value.source_timezone == payload.source_timezone
            and value.status == payload.status
            and value.scheduled_date
            == (payload.scheduled_date.isoformat() if payload.scheduled_date else None)
        ):
            matches.append((value, body))
    if len(matches) != 1:
        raise ValueError("official_schedule_vintage_invalid")
    return matches[0]


def schedule_identity(event_key, family, scheduled_at, scheduled_date, precision, timezone):
    """Calendar membership includes schedule semantics, not only a reusable UID."""
    return dict(
        event_key=event_key,
        family=family,
        scheduled_at=scheduled_at.isoformat() if scheduled_at and precision == "exact" else None,
        scheduled_date=scheduled_date.isoformat() if scheduled_date else None,
        time_precision=precision,
        source_timezone=timezone,
    )


def read_official_schedule_original_in_session(session, observation_id, *, archive, cutoff_at):
    cutoff = aware_utc(cutoff_at)
    row = session.get(ObservationRecord, observation_id, populate_existing=True)
    if row is None:
        raise ValueError("official_schedule_observation_missing")
    received = as_utc(row.received_at)
    if received > cutoff or row.source_type != "official_feed" or row.published_at is not None:
        raise ValueError("official_schedule_pit_invalid")
    if row.source_id == "cpi-publication-schedule":
        original = read_cpi_schedule_original_in_session(
            session, observation_id, archive=archive, cutoff_at=cutoff
        )
        value = original.payload
        attempt = session.get(SourcePollAttemptRecord, value.attempt_id, populate_existing=True)
        captures = [
            session.get(RawCaptureRecord, c.capture_id, populate_existing=True)
            for c in (value.fred_capture, value.dol_capture)
            if c is not None
        ]
        from packages.contracts_py.decision_hub_contracts import MacroEventPayload

        payload = MacroEventPayload(
            external_event_id=value.event_key,
            family="cpi",
            scheduled_at=value.scheduled_at,
            scheduled_date=value.scheduled_date,
            time_precision="exact",
            source_timezone=value.source_timezone,
        )
        parsed = dict(payload=value.model_dump(mode="json"), text=row.text)
        known = original.known_at
    elif row.source_id in SOURCES:
        url, version = SOURCES[row.source_id]
        matches = _calendar_matches(
            session, row, received, archive=archive, url=url, version=version
        )
        if len(matches) != 1:
            raise ValueError("official_schedule_parent_ambiguous_or_missing")
        attempt, capture, envelope = matches[0]
        captures, known = [capture], as_utc(attempt.completed_at)
        if (
            envelope.content_hash != row.content_hash
            or str(envelope.source_url) != row.source_url
            or envelope.observed_at != as_utc(row.observed_at)
            or envelope.scheduled_at != as_utc(row.scheduled_at)
        ):
            raise ValueError("official_schedule_observation_changed")
        payload, parsed = envelope.macro_event, envelope.model_dump(mode="json")
    else:
        raise ValueError("official_schedule_source_unsupported")
    if (
        payload.family not in {"cpi", "nfp", "fomc", "pce"}
        or hashlib.sha256(row.text.encode()).hexdigest() != row.content_hash
    ):
        raise ValueError("official_schedule_family_or_hash_invalid")
    vintage, vintage_body = _vintage(session, row, payload, cutoff)
    if row.source_id in SOURCES and (known is None or known > cutoff):
        if attempt.error_code is not None or any(
            value is None or as_utc(value.recorded_at) > cutoff for value in captures
        ):
            raise ValueError("official_schedule_native_binding_invalid")
        raise OfficialScheduleNotKnownAtCutoff("official_schedule_not_known_at_cutoff")
    link = session.get(EventOccurrenceRecord, row.event_id, populate_existing=True)
    occurrence = (
        session.get(OccurrenceRecord, link.occurrence_id, populate_existing=True)
        if link and as_utc(link.linked_at) <= cutoff
        else None
    )
    body = dict(
        source_id=row.source_id,
        event_id=row.event_id,
        observation_id=row.observation_id,
        content_hash=row.content_hash,
        parsed=parsed,
        vintage_id=vintage.vintage_id,
        vintage=vintage_body,
        poll=row_body(
            attempt,
            (
                "attempt_id",
                "source_id",
                "status",
                "started_at",
                "completed_at",
                "received_count",
                "content_snapshot_json",
            ),
        ),
        captures=[
            row_body(
                c,
                (
                    "capture_id",
                    "context_id",
                    "url",
                    "status_code",
                    "body_hash",
                    "body_ref",
                    "byte_count",
                    "adapter_version",
                    "error_code",
                    "started_at",
                    "completed_at",
                    "recorded_at",
                ),
            )
            for c in captures
        ],
    )
    return OfficialScheduleOriginal(
        dict(ref=row.observation_id, hash=row.content_hash),
        row.source_id,
        payload.external_event_id,
        row.event_id,
        payload.family,
        payload.scheduled_at if payload.time_precision == "exact" else None,
        payload.scheduled_date,
        payload.time_precision,
        payload.source_timezone,
        known,
        occurrence.occurrence_id if occurrence else None,
        occurrence.identity_basis if occurrence else "source_event_key_only",
        canonical(body),
        tuple(dict(ref=c.body_ref, hash=c.body_hash) for c in captures),
    )


def read_schedule_coverage_in_session(session, source_id, *, archive, cutoff_at):
    """A successful full calendar parse, including empty result, is coverage proof."""
    cutoff = aware_utc(cutoff_at)
    attempt = session.scalar(
        select(SourcePollAttemptRecord)
        .where(
            SourcePollAttemptRecord.source_id == source_id,
            SourcePollAttemptRecord.status.in_(("succeeded", "empty")),
            SourcePollAttemptRecord.completed_at <= cutoff,
        )
        .order_by(SourcePollAttemptRecord.completed_at.desc(), SourcePollAttemptRecord.attempt_id)
        .limit(1)
    )
    if attempt is None:
        raise ValueError("schedule_source_not_admitted")
    snapshot = SourceContentSnapshot.model_validate_json(attempt.content_snapshot_json or "{}")
    version = (
        "cpi-publication-schedule.v1"
        if source_id == "cpi-publication-schedule"
        else SOURCES[source_id][1]
    )
    if (
        snapshot.http_status != 200
        or snapshot.parser_version != version
        or snapshot.parse_status != "valid"
    ):
        raise ValueError("schedule_coverage_parse_invalid")
    captures = session.scalars(
        select(RawCaptureRecord).where(
            RawCaptureRecord.context_id == "source:" + attempt.attempt_id,
            RawCaptureRecord.body_hash == snapshot.content_hash,
        )
    ).all()
    if len(captures) != 1:
        raise ValueError("schedule_coverage_raw_ambiguous")
    capture = captures[0]
    clocks = [
        as_utc(x)
        for x in (
            attempt.started_at,
            capture.started_at,
            capture.completed_at,
            capture.recorded_at,
            attempt.completed_at,
        )
    ]
    if (
        any(x is None for x in clocks)
        or clocks != sorted(clocks)
        or clocks[-1] > cutoff
        or capture.adapter_version != version
        or capture.status_code != 200
        or capture.error_code
    ):
        raise ValueError("schedule_coverage_native_clock_invalid")
    raw = archive.read(capture.body_ref)
    if (
        capture.body_ref != "raw://sha256/" + capture.body_hash
        or len(raw) != capture.byte_count
        or hashlib.sha256(raw).hexdigest() != capture.body_hash
    ):
        raise ValueError("schedule_coverage_archive_invalid")
    if source_id == "cpi-publication-schedule":
        import re

        from packages.provider_adapters.research.raw_capture import safe_url

        from ..ports.cpi_schedule import fred_calendar_url, parse_fred_cpi_calendar

        years = set(
            re.findall(
                r"(20\d{2}) Economic Release Calendar - Consumer Price Index", raw.decode("utf-8")
            )
        )
        if len(years) != 1:
            raise ValueError("schedule_coverage_year_ambiguous")
        year = int(next(iter(years)))
        if capture.url != safe_url(fred_calendar_url(year)):
            raise ValueError("schedule_coverage_url_invalid")
        parsed = parse_fred_cpi_calendar(raw, year=year)
        events = [
            schedule_identity(
                "fred-cpi:" + str(day), "cpi", instant, day, "exact", "America/Chicago"
            )
            for day, _, instant in parsed
        ]
    else:
        if capture.url != SOURCES[source_id][0]:
            raise ValueError("schedule_coverage_url_invalid")
        parsed = _parse(source_id, raw, as_utc(capture.completed_at))
        events = [
            schedule_identity(
                item.macro_event.external_event_id,
                item.macro_event.family,
                item.macro_event.scheduled_at,
                item.macro_event.scheduled_date,
                item.macro_event.time_precision,
                item.macro_event.source_timezone,
            )
            for item in parsed
        ]
    count = len(events)
    if count != snapshot.parsed_count:
        raise ValueError("schedule_coverage_count_invalid")
    body = dict(
        poll=row_body(
            attempt,
            (
                "attempt_id",
                "source_id",
                "status",
                "started_at",
                "completed_at",
                "received_count",
                "content_snapshot_json",
            ),
        ),
        capture=row_body(
            capture,
            (
                "capture_id",
                "context_id",
                "url",
                "status_code",
                "body_hash",
                "body_ref",
                "byte_count",
                "adapter_version",
                "error_code",
                "started_at",
                "completed_at",
                "recorded_at",
            ),
        ),
        parser_version=version,
        parsed_count=count,
        events=events,
    )
    return (
        dict(ref="source-poll:" + attempt.attempt_id, hash=canonical_hash(body)),
        as_utc(attempt.completed_at),
        canonical(body),
    )

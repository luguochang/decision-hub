from __future__ import annotations

import hashlib
import json
import math
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts import (
    MacroEventView,
    MacroEventVintageView,
    TextEnvelope,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    EventOccurrenceRecord,
    EventWatchRecord,
    EventWindowSampleRecord,
    MacroEventRecord,
    MacroEventVintageRecord,
    RunRecord,
    as_utc,
)

_CALENDAR_LABELS = json.loads(
    (Path(__file__).resolve().parents[4] / "contracts/presentation/calendar-labels.json")
    .read_text(encoding="utf-8")
)


def upsert_macro_event_in_session(
    session: Session,
    *,
    event_id: str,
    envelope: TextEnvelope,
    now: datetime,
) -> MacroEventRecord | None:
    """Persist current event state and one immutable point-in-time vintage."""

    payload = envelope.macro_event
    if payload is None:
        return None
    existing = (
        session.query(MacroEventRecord)
        .filter_by(source_id=envelope.source_id, external_event_id=payload.external_event_id)
        .one_or_none()
    )
    identity = f"{envelope.source_id}:{payload.external_event_id}"
    macro_event_id = (
        existing.macro_event_id
        if existing is not None
        else f"macro_{hashlib.sha256(identity.encode()).hexdigest()[:32]}"
    )
    surprise = (
        payload.actual_value - payload.consensus_value
        if payload.actual_value is not None and payload.consensus_value is not None
        else None
    )
    surprise_z = _surprise_z(session, payload.family, surprise)
    if existing is None:
        existing = MacroEventRecord(
            macro_event_id=macro_event_id,
            event_id=event_id,
            external_event_id=payload.external_event_id,
            family=payload.family,
            scheduled_at=payload.scheduled_at,
            scheduled_date=payload.scheduled_date.isoformat() if payload.scheduled_date else None,
            time_precision=payload.time_precision,
            source_timezone=payload.source_timezone,
            source_id=envelope.source_id,
            source_url=str(envelope.source_url) if envelope.source_url else None,
            source_authority=payload.source_authority,
            unit=payload.unit,
            prior_value=payload.prior_value,
            consensus_value=payload.consensus_value,
            actual_value=payload.actual_value,
            vintage_at=payload.vintage_at or envelope.received_at,
            revision_flag=payload.revision_flag,
            surprise=surprise,
            surprise_z=surprise_z,
            status=payload.status,
            created_at=now,
            updated_at=now,
        )
        session.add(existing)
    else:
        if (
            as_utc(existing.scheduled_at) != as_utc(payload.scheduled_at)
            or existing.time_precision != payload.time_precision
            or payload.status == "cancelled"
        ):
            retire_calendar_schedule_in_session(session, event_id=existing.event_id, now=now)
        existing.event_id = event_id
        existing.family = payload.family
        existing.scheduled_at = payload.scheduled_at
        existing.scheduled_date = (
            payload.scheduled_date.isoformat() if payload.scheduled_date else None
        )
        existing.time_precision = payload.time_precision
        existing.source_timezone = payload.source_timezone
        existing.source_url = (
            str(envelope.source_url) if envelope.source_url else existing.source_url
        )
        existing.source_authority = payload.source_authority
        existing.unit = payload.unit or existing.unit
        existing.prior_value = (
            payload.prior_value if payload.prior_value is not None else existing.prior_value
        )
        existing.consensus_value = (
            payload.consensus_value
            if payload.consensus_value is not None
            else existing.consensus_value
        )
        existing.actual_value = (
            payload.actual_value if payload.actual_value is not None else existing.actual_value
        )
        existing.vintage_at = payload.vintage_at or envelope.received_at
        existing.revision_flag = payload.revision_flag
        actual_value = existing.actual_value
        consensus_value = existing.consensus_value
        existing.surprise = (
            actual_value - consensus_value
            if actual_value is not None and consensus_value is not None
            else None
        )
        existing.surprise_z = _surprise_z(session, payload.family, existing.surprise)
        existing.status = payload.status
        existing.updated_at = now

    received_at = as_utc(envelope.received_at)
    if received_at is None:  # pragma: no cover - TextEnvelope invariant
        raise ValueError("macro_event_received_at_missing")
    vintage_payload = {
        "macro_event_id": macro_event_id,
        "event_id": event_id,
        "observed_at": received_at.isoformat(),
        "scheduled_at": payload.scheduled_at.isoformat(),
        "scheduled_date": existing.scheduled_date,
        "time_precision": payload.time_precision,
        "source_timezone": payload.source_timezone,
        "prior_value": payload.prior_value,
        "consensus_value": payload.consensus_value,
        "actual_value": payload.actual_value,
        "surprise": surprise,
        "surprise_z": surprise_z,
        "revision_flag": payload.revision_flag,
        "status": payload.status,
    }
    canonical = json.dumps(vintage_payload, sort_keys=True, separators=(",", ":"))
    payload_hash = hashlib.sha256(canonical.encode()).hexdigest()
    duplicate = (
        session.query(MacroEventVintageRecord)
        .filter_by(macro_event_id=macro_event_id, payload_hash=payload_hash)
        .one_or_none()
    )
    if duplicate is None:
        session.add(
            MacroEventVintageRecord(
                vintage_id=f"macro_vintage_{uuid.uuid4().hex}",
                macro_event_id=macro_event_id,
                event_id=event_id,
                observed_at=envelope.received_at,
                scheduled_at=payload.scheduled_at,
                scheduled_date=existing.scheduled_date,
                time_precision=payload.time_precision,
                source_timezone=payload.source_timezone,
                prior_value=payload.prior_value,
                consensus_value=payload.consensus_value,
                actual_value=payload.actual_value,
                surprise=surprise,
                surprise_z=surprise_z,
                revision_flag=payload.revision_flag,
                status=payload.status,
                payload_hash=payload_hash,
                created_at=now,
            )
        )
    return existing


def retire_calendar_schedule_in_session(session: Session, *, event_id: str, now: datetime) -> None:
    """Preserve old samples and Runs while preventing stale future execution."""
    identity = session.get(EventOccurrenceRecord, event_id)
    event_ids = {event_id}
    if identity:
        event_ids.update(
            row.event_id
            for row in session.query(EventOccurrenceRecord).filter_by(
                occurrence_id=identity.occurrence_id,
            )
        )
    watches = session.query(EventWatchRecord).filter(EventWatchRecord.event_id.in_(event_ids)).all()
    for watch in watches:
        if watch.status in {"scheduled", "active"}:
            watch.status = "cancelled"
            watch.next_tick_at = None
            watch.updated_at = now
        for sample in session.query(EventWindowSampleRecord).filter_by(watch_id=watch.watch_id):
            if sample.status in {"pending", "due"}:
                sample.status = "missing"
                sample.error_code = "calendar_schedule_superseded"
    for run in session.query(RunRecord).filter(
        RunRecord.event_id.in_(event_ids),
        RunRecord.admission_origin == "automatic",
        RunRecord.available_at.is_not(None),
    ):
        if run.status in {"admitted", "queued"}:
            run.status = "cancelled"
            run.error_code = "calendar_schedule_superseded"
            run.updated_at = now


def reconcile_macro_release_in_session(
    session: Session,
    *,
    event_id: str,
    envelope: TextEnvelope,
    now: datetime,
) -> MacroEventRecord | None:
    """Advance a scheduled official event when its official release arrives.

    Numeric actuals stay null until a typed parser provides them. This updates
    only release state/provenance and never treats prior or article prose as a
    consensus value.
    """

    if envelope.macro_event is not None or not envelope.event_family:
        return None
    release_at = as_utc(envelope.published_at or envelope.observed_at)
    if release_at is None:  # pragma: no cover - TextEnvelope invariant
        return None
    candidates = (
        session.query(MacroEventRecord)
        .filter(
            MacroEventRecord.family == envelope.event_family,
            MacroEventRecord.scheduled_at >= release_at - timedelta(days=2),
            MacroEventRecord.scheduled_at <= release_at + timedelta(days=2),
        )
        .all()
    )
    if not candidates:
        return None
    event = min(
        candidates,
        key=lambda item: abs(
            ((as_utc(item.scheduled_at) or release_at) - release_at).total_seconds()
        ),
    )
    status = "revised" if envelope.revision_of else "released"
    event.event_id = event_id
    event.status = status
    event.revision_flag = status == "revised"
    event.vintage_at = release_at
    event.updated_at = now
    vintage_payload = {
        "macro_event_id": event.macro_event_id,
        "event_id": event_id,
        "observed_at": release_at.isoformat(),
        "scheduled_at": (as_utc(event.scheduled_at) or release_at).isoformat(),
        "scheduled_date": event.scheduled_date,
        "time_precision": event.time_precision,
        "source_timezone": event.source_timezone,
        "prior_value": event.prior_value,
        "consensus_value": event.consensus_value,
        "actual_value": event.actual_value,
        "surprise": event.surprise,
        "surprise_z": event.surprise_z,
        "revision_flag": event.revision_flag,
        "status": status,
    }
    canonical = json.dumps(vintage_payload, sort_keys=True, separators=(",", ":"))
    payload_hash = hashlib.sha256(canonical.encode()).hexdigest()
    if (
        session.query(MacroEventVintageRecord)
        .filter_by(macro_event_id=event.macro_event_id, payload_hash=payload_hash)
        .one_or_none()
        is None
    ):
        session.add(
            MacroEventVintageRecord(
                vintage_id=f"macro_vintage_{uuid.uuid4().hex}",
                macro_event_id=event.macro_event_id,
                event_id=event_id,
                observed_at=release_at,
                scheduled_at=event.scheduled_at,
                scheduled_date=event.scheduled_date,
                time_precision=event.time_precision,
                source_timezone=event.source_timezone,
                prior_value=event.prior_value,
                consensus_value=event.consensus_value,
                actual_value=event.actual_value,
                surprise=event.surprise,
                surprise_z=event.surprise_z,
                revision_flag=event.revision_flag,
                status=status,
                payload_hash=payload_hash,
                created_at=now,
            )
        )
    return event


def list_macro_events(
    database: Database,
    *,
    limit: int = 50,
    offset: int = 0,
    scope: Literal["all", "upcoming", "history"] = "all",
    q: str | None = None,
    family: str | None = None,
    now: datetime | None = None,
) -> list[MacroEventView]:
    return list_macro_events_page(
        database,
        limit=limit,
        offset=offset,
        scope=scope,
        q=q,
        family=family,
        now=now,
    ).items


@dataclass(frozen=True)
class MacroEventListPage:
    items: list[MacroEventView]
    total_count: int


def list_macro_events_page(
    database: Database,
    *,
    limit: int = 50,
    offset: int = 0,
    scope: Literal["all", "upcoming", "history"] = "all",
    q: str | None = None,
    family: str | None = None,
    now: datetime | None = None,
) -> MacroEventListPage:
    """Apply search before paging, preserving the established calendar order."""

    current = as_utc(now or datetime.now().astimezone())
    if current is None:  # pragma: no cover
        raise ValueError("macro_event_clock_missing")
    needle = " ".join(q.split()).casefold() if q else None
    selected_family = family.strip().casefold() if family else None
    with database.session() as session:
        rows = (
            session.query(MacroEventRecord).filter(MacroEventRecord.invalidated_at.is_(None)).all()
        )
        if selected_family:
            rows = [row for row in rows if row.family.casefold() == selected_family]
        if needle:
            rows = [
                row
                for row in rows
                if any(
                    needle in value.casefold()
                    for value in (
                        row.family,
                        row.external_event_id,
                        row.source_id,
                        row.event_id,
                        row.macro_event_id,
                        _CALENDAR_LABELS["families"].get(row.family.casefold(), ""),
                        _CALENDAR_LABELS["sources"].get(row.source_id, ""),
                    )
                )
            ]
        if scope == "upcoming":
            rows = [row for row in rows if _is_upcoming(row, current) and row.status != "cancelled"]
        elif scope == "history":
            rows = [row for row in rows if not _is_upcoming(row, current)]
        rows.sort(key=lambda row: _calendar_sort_key(row, current))
        total_count = len(rows)
        rows = rows[max(0, offset) : max(0, offset) + max(1, limit)]
        return MacroEventListPage(
            items=[_macro_event_view(row) for row in rows], total_count=total_count
        )


def list_macro_event_families(database: Database) -> list[str]:
    """Stable filter choices from the ledger, independent of search and pagination."""
    with database.session() as session:
        return [row[0] for row in session.query(MacroEventRecord.family)
                .filter(MacroEventRecord.invalidated_at.is_(None))
                .distinct().order_by(MacroEventRecord.family).all()]


def get_macro_event(database: Database, macro_event_id: str) -> MacroEventView | None:
    """Resolve a stable macro event identity independently of list pagination."""

    with database.session() as session:
        row = session.get(MacroEventRecord, macro_event_id)
        return _macro_event_view(row) if row is not None else None


def _macro_event_view(row: MacroEventRecord) -> MacroEventView:
    return MacroEventView.model_validate(
        {
            "macro_event_id": row.macro_event_id,
            "family": row.family,
            "scheduled_at": as_utc(row.scheduled_at) if row.time_precision == "exact" else None,
            "scheduled_date": row.scheduled_date,
            "time_precision": row.time_precision,
            "source_timezone": row.source_timezone,
            "invalidated_at": as_utc(row.invalidated_at),
            "correction_ref": row.correction_ref,
            "source_id": row.source_id,
            "source_url": row.source_url,
            "source_authority": row.source_authority,
            "unit": row.unit,
            "prior_value": row.prior_value,
            "consensus_value": row.consensus_value,
            "actual_value": row.actual_value,
            "surprise": row.surprise,
            "surprise_z": row.surprise_z,
            "revision_flag": row.revision_flag,
            "status": row.status,
            "updated_at": as_utc(row.updated_at),
        }
    )


def list_macro_event_vintages(
    database: Database,
    macro_event_id: str,
    *,
    limit: int = 100,
) -> list[MacroEventVintageView]:
    """Return append-only releases for an event, oldest first for replay."""

    with database.session() as session:
        rows = (
            session.query(MacroEventVintageRecord)
            .filter_by(macro_event_id=macro_event_id)
            .order_by(
                MacroEventVintageRecord.observed_at.asc(), MacroEventVintageRecord.vintage_id.asc()
            )
            .limit(max(1, min(limit, 500)))
            .all()
        )
        return [
            MacroEventVintageView(
                vintage_id=row.vintage_id,
                macro_event_id=row.macro_event_id,
                observed_at=as_utc(row.observed_at),
                scheduled_at=as_utc(row.scheduled_at) if row.time_precision == "exact" else None,
                scheduled_date=row.scheduled_date,
                time_precision=row.time_precision,
                source_timezone=row.source_timezone,
                prior_value=row.prior_value,
                consensus_value=row.consensus_value,
                actual_value=row.actual_value,
                surprise=row.surprise,
                surprise_z=row.surprise_z,
                revision_flag=row.revision_flag,
                status=row.status,
                payload_hash=row.payload_hash,
                created_at=as_utc(row.created_at),
            )
            for row in rows
        ]


def _calendar_sort_key(row: MacroEventRecord, now: datetime) -> tuple[int, float, str]:
    scheduled_at = as_utc(row.scheduled_at) or now
    if _is_upcoming(row, now):
        return (0, (scheduled_at - now).total_seconds(), row.macro_event_id)
    return (1, -(scheduled_at - now).total_seconds(), row.macro_event_id)


def _is_upcoming(row: MacroEventRecord, now: datetime) -> bool:
    if row.time_precision == "date" and row.scheduled_date:
        local_day = now.astimezone(ZoneInfo(row.source_timezone or "UTC")).date()
        return row.scheduled_date >= local_day.isoformat()
    return (as_utc(row.scheduled_at) or now) >= now


def _surprise_z(session: Session, family: str, surprise: float | None) -> float | None:
    if surprise is None:
        return None
    history = [
        float(value)
        for (value,) in (
            session.query(MacroEventVintageRecord.surprise)
            .join(
                MacroEventRecord,
                MacroEventRecord.macro_event_id == MacroEventVintageRecord.macro_event_id,
            )
            .filter(
                MacroEventRecord.family == family,
                MacroEventVintageRecord.surprise.is_not(None),
                MacroEventVintageRecord.revision_flag.is_(False),
            )
            .order_by(MacroEventVintageRecord.observed_at.asc())
            .all()
        )
        if value is not None
    ]
    if len(history) < 5:
        return None
    mean = sum(history) / len(history)
    variance = sum((item - mean) ** 2 for item in history) / len(history)
    deviation = math.sqrt(variance)
    return None if deviation == 0 else (surprise - mean) / deviation

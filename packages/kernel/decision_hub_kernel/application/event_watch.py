from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts import (
    EventWatch,
    EventWindowCapture,
    EventWindowSample,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    EventWatchRecord,
    EventWindowSampleRecord,
    as_utc,
    utcnow,
)
from packages.kernel.decision_hub_kernel.ports.sources import EventWindowSamplerPort

DEFAULT_WINDOW_OFFSETS = (
    "t-30m",
    "t-5m",
    "t0",
    "t+1m",
    "t+5m",
    "t+30m",
    "t+24h",
    "t+72h",
)
_OFFSET_MINUTES: dict[str, int] = {
    "t-30m": -30,
    "t-5m": -5,
    "t0": 0,
    "t+1m": 1,
    "t+5m": 5,
    "t+30m": 30,
    "t+24h": 24 * 60,
    "t+72h": 72 * 60,
}
_BASELINE_OFFSETS = frozenset({"t-30m", "t-5m"})
_CAPTURE_GRACE = timedelta(minutes=5)
WatchStatus = Literal[
    "scheduled",
    "active",
    "completed",
    "retrospective_only",
    "cancelled",
]
BaselineStatus = Literal["pending", "ready", "unavailable"]
SampleStatus = Literal[
    "pending",
    "due",
    "captured",
    "missing",
    "baseline_unavailable",
]


class EventWindowSamplingReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    attempted: int = 0
    captured: int = 0
    failed: int = 0


class EventWatchService:
    """Own durable future-event windows without fetching market data.

    The service only schedules immutable slots and records provider payload
    references. A typed market adapter is injected later through ``capture``;
    this boundary deliberately does not know financial fields or provider APIs.
    """

    def __init__(
        self,
        database: Database,
        *,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.database = database
        self.clock = clock

    def ensure_watch(
        self,
        *,
        event_id: str,
        source_id: str,
        event_family: str,
        scheduled_at: datetime,
        window_offsets: Iterable[str] = DEFAULT_WINDOW_OFFSETS,
    ) -> EventWatch:
        scheduled = _aware(scheduled_at)
        offsets = _normalize_offsets(window_offsets)
        now = _aware(self.clock())
        watch_id = _watch_id(event_id, scheduled)
        with self.database.session() as session:
            existing = session.get(EventWatchRecord, watch_id)
            if existing is None:
                by_event = (
                    session.query(EventWatchRecord).filter_by(event_id=event_id).first()
                )
                if by_event is not None:
                    if (
                        _stored_utc(by_event.scheduled_at) != scheduled
                        or by_event.source_id != source_id
                        or by_event.event_family != event_family
                    ):
                        raise ValueError("event_watch_identity_conflict")
                    return _watch_model(by_event)
                status, baseline = _initial_state(scheduled, now)
                created = now
                existing = EventWatchRecord(
                    watch_id=watch_id,
                    event_id=event_id,
                    source_id=source_id,
                    event_family=event_family,
                    scheduled_at=scheduled,
                    status=status,
                    window_offsets_json=json.dumps(offsets, separators=(",", ":")),
                    baseline_status=baseline,
                    created_at=created,
                    updated_at=created,
                    next_tick_at=None,
                )
                session.add(existing)
                session.flush()
                for offset in offsets:
                    target = scheduled + timedelta(minutes=_OFFSET_MINUTES[offset])
                    slot_status = (
                        "baseline_unavailable"
                        if offset in _BASELINE_OFFSETS and target < now
                        else "pending"
                    )
                    session.add(
                        EventWindowSampleRecord(
                            sample_id=f"{watch_id}:{offset}",
                            watch_id=watch_id,
                            event_id=event_id,
                            offset=offset,
                            target_at=target,
                            status=slot_status,
                        )
                    )
            else:
                if (
                    existing.event_id != event_id
                    or existing.source_id != source_id
                    or existing.event_family != event_family
                    or _stored_utc(existing.scheduled_at) != scheduled
                ):
                    raise ValueError("event_watch_identity_conflict")
                persisted_offsets = tuple(json.loads(existing.window_offsets_json))
                if persisted_offsets != offsets:
                    raise ValueError("event_watch_window_conflict")
            _refresh(existing, session, now)
            return _watch_model(existing)

    def advance(self, *, now: datetime | None = None) -> tuple[EventWatch, ...]:
        """Move scheduled slots to ``due`` and mark irrecoverably late baselines."""

        current = _aware(now or self.clock())
        with self.database.session() as session:
            watches = (
                session.query(EventWatchRecord)
                .filter(EventWatchRecord.status.in_(("scheduled", "active", "retrospective_only")))
                .order_by(EventWatchRecord.scheduled_at, EventWatchRecord.watch_id)
                .all()
            )
            for watch in watches:
                samples = (
                    session.query(EventWindowSampleRecord)
                    .filter_by(watch_id=watch.watch_id)
                    .order_by(EventWindowSampleRecord.target_at, EventWindowSampleRecord.offset)
                    .all()
                )
                for sample in samples:
                    if sample.status == "pending" and _stored_utc(sample.target_at) <= current:
                        sample.status = "due"
                _refresh(watch, session, current, samples=samples)
            return tuple(_watch_model(item) for item in watches)

    def capture(self, sample_id: str, capture: EventWindowCapture) -> EventWindowSample:
        """Attach a validated external payload reference to one due slot."""

        received = _aware(capture.received_at)
        observed = _aware(capture.observed_at)
        now = _aware(self.clock())
        if observed > received or received > now:
            raise ValueError("event_window_capture_pit_violation")
        with self.database.session() as session:
            sample = session.get(EventWindowSampleRecord, sample_id)
            if sample is None:
                raise KeyError(sample_id)
            if sample.status not in {"pending", "due"}:
                if sample.status == "captured":
                    if sample.payload_hash != capture.payload_hash:
                        raise ValueError("event_window_capture_identity_conflict")
                    return _sample_model(sample)
                raise ValueError("event_window_sample_not_capturable")
            if _stored_utc(sample.target_at) > now:
                raise ValueError("event_window_sample_not_due")
            sample.status = "captured"
            sample.observed_at = observed
            sample.received_at = received
            sample.provider_id = capture.provider_id
            sample.payload_ref = capture.payload_ref
            sample.payload_hash = capture.payload_hash
            sample.error_code = None
            watch = session.get(EventWatchRecord, sample.watch_id)
            if watch is None:  # pragma: no cover - database integrity guard
                raise ValueError("event_watch_not_found")
            _refresh(watch, session, now)
            return _sample_model(sample)

    async def capture_due(
        self,
        sampler: EventWindowSamplerPort,
        *,
        now: datetime | None = None,
    ) -> EventWindowSamplingReport:
        current = _aware(now or self.clock())
        self.advance(now=current)
        due = self._due_samples()
        captured = 0
        failed = 0
        for sample in due:
            try:
                result = await sampler.capture(sample)
                if result is None:
                    self.record_error(sample.sample_id, "event_window_provider_unavailable")
                    failed += 1
                    continue
                self.capture(sample.sample_id, result)
                captured += 1
            except Exception as exc:
                self.record_error(
                    sample.sample_id,
                    getattr(exc, "error_code", None) or "event_window_capture_failed",
                )
                failed += 1
        return EventWindowSamplingReport(
            attempted=len(due),
            captured=captured,
            failed=failed,
        )

    def record_error(self, sample_id: str, error_code: str) -> EventWindowSample:
        if not error_code:
            raise ValueError("event_window_error_code_required")
        with self.database.session() as session:
            sample = session.get(EventWindowSampleRecord, sample_id)
            if sample is None:
                raise KeyError(sample_id)
            if sample.status not in {"pending", "due"}:
                return _sample_model(sample)
            sample.error_code = error_code
            return _sample_model(sample)

    def get_watch(self, watch_id: str) -> EventWatch | None:
        with self.database.session() as session:
            row = session.get(EventWatchRecord, watch_id)
            return _watch_model(row) if row is not None else None

    def get_watch_by_event_id(self, event_id: str) -> EventWatch | None:
        """Return the durable watch for one event without exposing SQL rows."""

        with self.database.session() as session:
            row = (
                session.query(EventWatchRecord)
                .filter_by(event_id=event_id)
                .one_or_none()
            )
            return _watch_model(row) if row is not None else None

    def list_samples(self, watch_id: str) -> tuple[EventWindowSample, ...]:
        with self.database.session() as session:
            rows = (
                session.query(EventWindowSampleRecord)
                .filter_by(watch_id=watch_id)
                .order_by(EventWindowSampleRecord.target_at, EventWindowSampleRecord.offset)
                .all()
            )
            return tuple(_sample_model(item) for item in rows)

    def list_event_samples(self, event_id: str) -> tuple[EventWindowSample, ...]:
        """Project samples by event id for adapters and the Gateway boundary."""

        with self.database.session() as session:
            rows = (
                session.query(EventWindowSampleRecord)
                .filter_by(event_id=event_id)
                .order_by(EventWindowSampleRecord.target_at, EventWindowSampleRecord.offset)
                .all()
            )
            return tuple(_sample_model(item) for item in rows)

    def _due_samples(self) -> tuple[EventWindowSample, ...]:
        with self.database.session() as session:
            rows = (
                session.query(EventWindowSampleRecord)
                .filter_by(status="due")
                .order_by(EventWindowSampleRecord.target_at, EventWindowSampleRecord.sample_id)
                .all()
            )
            return tuple(_sample_model(item) for item in rows)


def _normalize_offsets(offsets: Iterable[str]) -> tuple[str, ...]:
    values = tuple(dict.fromkeys(str(item).strip() for item in offsets))
    if not values or any(item not in _OFFSET_MINUTES for item in values):
        raise ValueError("event_watch_window_offset_invalid")
    return tuple(sorted(values, key=lambda item: (_OFFSET_MINUTES[item], item)))


def _initial_state(scheduled: datetime, now: datetime) -> tuple[str, str]:
    if scheduled + timedelta(minutes=-5) < now:
        return "retrospective_only", "unavailable"
    return ("active" if scheduled <= now else "scheduled"), "pending"


def _refresh(
    watch: EventWatchRecord,
    session: Session,
    now: datetime,
    *,
    samples: list[EventWindowSampleRecord] | None = None,
) -> None:
    rows = samples or (
        session.query(EventWindowSampleRecord)
        .filter_by(watch_id=watch.watch_id)
        .order_by(EventWindowSampleRecord.target_at, EventWindowSampleRecord.offset)
        .all()
    )
    for sample in rows:
        if sample.status == "pending" and _stored_utc(sample.target_at) <= now:
            sample.status = "due"
        if sample.status == "due" and _stored_utc(sample.target_at) + _CAPTURE_GRACE < now:
            sample.status = (
                "baseline_unavailable" if sample.offset in _BASELINE_OFFSETS else "missing"
            )
            sample.error_code = sample.error_code or "event_window_capture_deadline_elapsed"
    baseline_rows = [item for item in rows if item.offset in _BASELINE_OFFSETS]
    if baseline_rows and all(item.status == "captured" for item in baseline_rows):
        watch.baseline_status = "ready"
    elif now >= _stored_utc(watch.scheduled_at):
        watch.baseline_status = "unavailable"
    if now > _stored_utc(watch.scheduled_at) and watch.baseline_status == "unavailable":
        watch.status = "retrospective_only"
    elif watch.status != "cancelled":
        window_started = any(item.status in {"due", "captured"} for item in rows)
        watch.status = (
            "active"
            if window_started or now >= _stored_utc(watch.scheduled_at)
            else "scheduled"
        )
    terminal_sample_statuses = {"captured", "missing", "baseline_unavailable"}
    if rows and all(item.status in terminal_sample_statuses for item in rows):
        watch.status = "completed" if watch.baseline_status == "ready" else "retrospective_only"
    future = [
        _stored_utc(item.target_at)
        for item in rows
        if item.status == "pending" and _stored_utc(item.target_at) > now
    ]
    watch.next_tick_at = min(future) if future else None
    watch.updated_at = now


def _watch_model(row: EventWatchRecord) -> EventWatch:
    return EventWatch(
        schema_version="event-watch.v1",
        watch_id=row.watch_id,
        event_id=row.event_id,
        source_id=row.source_id,
        event_family=row.event_family,
        scheduled_at=_stored_utc(row.scheduled_at),
        status=cast(WatchStatus, row.status),
        window_offsets=list(json.loads(row.window_offsets_json)),
        baseline_status=cast(BaselineStatus, row.baseline_status),
        created_at=_stored_utc(row.created_at),
        updated_at=_stored_utc(row.updated_at),
        next_tick_at=_stored_utc(row.next_tick_at) if row.next_tick_at else None,
    )


def _sample_model(row: EventWindowSampleRecord) -> EventWindowSample:
    return EventWindowSample(
        schema_version="event-window-sample.v1",
        sample_id=row.sample_id,
        watch_id=row.watch_id,
        event_id=row.event_id,
        offset=row.offset,
        target_at=_stored_utc(row.target_at),
        status=cast(SampleStatus, row.status),
        observed_at=_stored_utc(row.observed_at) if row.observed_at else None,
        received_at=_stored_utc(row.received_at) if row.received_at else None,
        provider_id=row.provider_id,
        payload_ref=row.payload_ref,
        payload_hash=row.payload_hash,
        error_code=row.error_code,
    )


def _watch_id(event_id: str, scheduled_at: datetime) -> str:
    digest = hashlib.sha256(f"{event_id}:{scheduled_at.isoformat()}".encode()).hexdigest()
    return f"watch_{digest[:32]}"


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("event_watch_timestamp_must_be_aware")
    return value.astimezone(UTC)


def _stored_utc(value: datetime) -> datetime:
    normalized = as_utc(value)
    if normalized is None:  # pragma: no cover - non-null database guard
        raise ValueError("event_watch_timestamp_missing")
    return normalized

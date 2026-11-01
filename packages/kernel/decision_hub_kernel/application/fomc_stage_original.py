"""Authenticate native policy and calendar parents before deriving expected stages."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any

from packages.provider_adapters.research.raw_capture import RawBodyArchive
from packages.source_adapters.official_feeds.fomc_policy import SOURCE_ID, VERSION, policy_envelope
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..persistence.db import ObservationRecord, RawCaptureRecord, SourcePollAttemptRecord, as_utc
from ..ports.fomc_stage import (
    CALENDAR_URL,
    POLICY_URL,
    FomcNativeOriginal,
    FomcStageSchedule,
    derive_fomc_stage_schedule,
    stages_in_window,
)
from ..ports.market_path import aware_utc, canonical_hash
from ..ports.sources import SourceContentSnapshot
from .official_schedule_original import OfficialScheduleOriginal, canonical, row_body

CAPTURE_FIELDS = (
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
)
POLL_FIELDS = (
    "attempt_id",
    "source_id",
    "status",
    "started_at",
    "completed_at",
    "received_count",
    "content_snapshot_json",
)


@dataclass(frozen=True)
class FomcPolicyOriginal:
    native: FomcNativeOriginal
    source_ref: dict[str, str]
    raw_ref: dict[str, str]
    proof: dict[str, Any]


def _native(
    parent: SourcePollAttemptRecord,
    capture: RawCaptureRecord,
    *,
    archive: RawBodyArchive,
    cutoff: datetime,
    url: str,
    version: str,
) -> FomcNativeOriginal:
    clocks = [
        as_utc(t)
        for t in (
            parent.started_at,
            capture.started_at,
            capture.completed_at,
            capture.recorded_at,
            parent.completed_at,
        )
    ]
    if (
        parent.status not in {"succeeded", "empty"}
        or any(t is None for t in clocks)
        or clocks != sorted(clocks)
        or clocks[-1] > cutoff
        or capture.context_id != "source:" + parent.attempt_id
        or capture.url != url
        or capture.adapter_version != version
        or capture.status_code != 200
        or capture.error_code is not None
        or not capture.body_hash
        or not capture.body_ref
        or capture.body_ref != "raw://sha256/" + capture.body_hash
    ):
        raise ValueError("fomc_native_parent_invalid")
    raw = archive.read(capture.body_ref)
    if len(raw) != capture.byte_count or hashlib.sha256(raw).hexdigest() != capture.body_hash:
        raise ValueError("fomc_native_archive_invalid")
    proof = dict(
        kind="native_capture_binding",
        poll=row_body(parent, POLL_FIELDS),
        capture=row_body(capture, CAPTURE_FIELDS),
    )
    return FomcNativeOriginal(
        raw,
        url,
        capture.body_hash,
        capture.body_ref,
        capture.byte_count,
        as_utc(capture.completed_at),
        canonical(proof).encode(),
        as_utc(parent.completed_at),
    )


def _policy_ids(text: str) -> tuple[str, str]:
    value = json.loads(text)
    if not isinstance(value, dict) or value.get("kind") != "fomc_policy_native_binding":
        raise ValueError("fomc_policy_binding_shape_invalid")
    capture = value.get("capture")
    attempt_id = value.get("attempt_id")
    capture_id = capture.get("capture_id") if isinstance(capture, dict) else None
    if not isinstance(attempt_id, str) or not isinstance(capture_id, str):
        raise ValueError("fomc_policy_binding_shape_invalid")
    return attempt_id, capture_id


def read_fomc_policy_original_in_session(
    session: Session,
    *,
    archive: RawBodyArchive,
    cutoff_at: datetime,
) -> FomcPolicyOriginal | None:
    cutoff = aware_utc(cutoff_at)
    parent = session.scalar(
        select(SourcePollAttemptRecord)
        .where(
            SourcePollAttemptRecord.source_id == SOURCE_ID,
            SourcePollAttemptRecord.status.in_(("succeeded", "empty")),
            SourcePollAttemptRecord.completed_at <= cutoff,
        )
        .order_by(SourcePollAttemptRecord.completed_at.desc(), SourcePollAttemptRecord.attempt_id)
        .limit(1)
    )
    if parent is None:
        return None
    if parent.received_count != 1:
        raise ValueError("fomc_policy_parent_missing")
    captures = session.scalars(
        select(RawCaptureRecord).where(
            RawCaptureRecord.context_id == "source:" + parent.attempt_id,
        )
    ).all()
    if len(captures) != 1:
        raise ValueError("fomc_policy_capture_ambiguous_or_missing")
    capture = captures[0]
    native = _native(
        parent, capture, archive=archive, cutoff=cutoff, url=POLICY_URL, version=VERSION
    )
    rows = session.scalars(
        select(ObservationRecord).where(
            ObservationRecord.source_id == SOURCE_ID,
            ObservationRecord.received_at == as_utc(capture.completed_at),
        )
    ).all()
    if len(rows) != 1:
        raise ValueError("fomc_policy_observation_ambiguous_or_missing")
    row = rows[0]
    if _policy_ids(row.text) != (parent.attempt_id, capture.capture_id):
        raise ValueError("fomc_policy_observation_parent_invalid")
    _validate_policy_observation(row, parent, capture)
    proof = json.loads(native.native_binding)
    proof.update(observation_id=row.observation_id, content_hash=row.content_hash, text=row.text)
    return FomcPolicyOriginal(
        native,
        dict(ref=row.observation_id, hash=row.content_hash),
        dict(ref=str(capture.body_ref), hash=str(capture.body_hash)),
        proof,
    )


def _validate_policy_observation(
    row: ObservationRecord,
    parent: SourcePollAttemptRecord,
    capture: RawCaptureRecord,
) -> None:
    snapshot = SourceContentSnapshot.model_validate_json(parent.content_snapshot_json or "{}")
    binding = dict(
        capture_id=capture.capture_id,
        raw_body_hash=capture.body_hash,
        raw_body_ref=capture.body_ref,
        url=POLICY_URL,
        received_at=as_utc(capture.completed_at).isoformat(),
    )
    envelope = policy_envelope(binding, parent.attempt_id)
    if (
        row.text != envelope.raw_text
        or row.content_hash != envelope.content_hash
        or row.event_hint != envelope.event_hint
        or row.source_type != "official_feed"
        or row.source_url != POLICY_URL
        or row.published_at is not None
        or row.scheduled_at is not None
        or as_utc(row.received_at) != envelope.received_at
        or as_utc(row.observed_at) != envelope.observed_at
        or snapshot.http_status != 200
        or snapshot.content_hash != capture.body_hash
        or snapshot.parser_version != VERSION
        or snapshot.parse_status != "valid"
        or snapshot.parsed_count != 1
    ):
        raise ValueError("fomc_policy_observation_invalid")


def _calendar_native(
    session: Session,
    proof: dict[str, Any],
    *,
    archive: RawBodyArchive,
    cutoff: datetime,
) -> FomcNativeOriginal:
    parent = session.get(
        SourcePollAttemptRecord, proof["poll"]["attempt_id"], populate_existing=True
    )
    capture = session.get(RawCaptureRecord, proof["capture"]["capture_id"], populate_existing=True)
    if parent is None or capture is None or parent.source_id != "fed-calendar":
        raise ValueError("fomc_calendar_parent_missing")
    if (
        row_body(parent, POLL_FIELDS) != proof["poll"]
        or row_body(capture, CAPTURE_FIELDS) != proof["capture"]
    ):
        raise ValueError("fomc_calendar_coverage_changed")
    return _native(
        parent,
        capture,
        archive=archive,
        cutoff=cutoff,
        url=CALENDAR_URL,
        version="official-html-calendar.v2",
    )


def _stage_material(
    original: OfficialScheduleOriginal,
    policy: FomcPolicyOriginal,
    calendar: FomcNativeOriginal,
    schedule: FomcStageSchedule,
    coverage_json: str,
) -> tuple[str, dict[str, str], list[dict[str, str]]]:
    proof = dict(
        kind="fomc_derived_expected_stage_original",
        calendar_original=json.loads(original.original_json),
        calendar_coverage=json.loads(coverage_json),
        policy_original=policy.proof,
        schedule=asdict(schedule),
        financial_qualified=False,
        execution_authorized=False,
    )
    text = json.dumps(proof, sort_keys=True, separators=(",", ":"), default=lambda v: v.isoformat())
    reference = dict(
        ref="fomc-stage-original:" + canonical_hash(json.loads(text)),
        hash=canonical_hash(json.loads(text)),
    )
    raw_refs = list(original.raw_capture_refs)
    for item in (dict(ref=calendar.body_ref, hash=calendar.body_sha256), policy.raw_ref):
        if item not in raw_refs:
            raw_refs.append(item)
    return text, reference, raw_refs


def fomc_stage_entries_in_session(
    session: Session,
    original: OfficialScheduleOriginal,
    coverage_json: str,
    *,
    archive: RawBodyArchive,
    as_of: datetime,
    window_end: datetime,
) -> list[dict[str, Any]] | None:
    """None preserves the original date row; a known rule may select zero stages."""
    policy = read_fomc_policy_original_in_session(session, archive=archive, cutoff_at=as_of)
    meeting = original.scheduled_date
    if policy is None or not isinstance(meeting, date):
        return None
    calendar = _calendar_native(session, json.loads(coverage_json), archive=archive, cutoff=as_of)
    schedule = derive_fomc_stage_schedule(
        calendar,
        policy.native,
        meeting_date=meeting,
        as_of=as_of,
    )
    if schedule.status != "derived_schedule":
        return None
    text, reference, raw_refs = _stage_material(original, policy, calendar, schedule, coverage_json)
    return [
        dict(
            source_ref=reference,
            source_id=original.source_id,
            event_key=original.event_key,
            event_id=original.event_id,
            family="fomc",
            stage_id=stage.stage_id,
            scheduled_at=stage.scheduled_at,
            scheduled_date=meeting.isoformat(),
            time_precision="exact",
            source_timezone=stage.source_timezone,
            known_at=schedule.known_at,
            occurrence_ref=original.occurrence_ref,
            identity_basis=original.identity_basis,
            overlap_group="agenda-overlap:" + canonical_hash([stage.scheduled_at.isoformat()]),
            dependency_refs=[original.source_ref, policy.source_ref],
            dependency_status="unknown",
            materiality_reason="官方日期与适用时刻规则推导的预期阶段；尚非实际声明、SEP或执行授权",
            next_check_at=None,
            original_json=text,
            raw_capture_refs=raw_refs,
        )
        for stage in stages_in_window(schedule, as_of, window_end)
    ]

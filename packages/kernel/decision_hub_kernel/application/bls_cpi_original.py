"""Source-authenticated BLS metric originals; no Fact/forecast qualification."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from ..persistence.db import ObservationRecord, RawCaptureRecord, SourcePollAttemptRecord, as_utc
from ..ports.bls_cpi import BLS_SOURCE_ID, BLS_URL, BLS_VERSION, BlsCpiMetricPayload, parse_bls_cpi_metrics
from ..ports.sources import SourceContentSnapshot


@dataclass(frozen=True)
class BlsCpiOriginal:
    observation_id: str
    payload: BlsCpiMetricPayload
    known_at: datetime


def read_bls_cpi_original_in_session(session: Session, observation_id: str, *, archive, cutoff_at: datetime) -> BlsCpiOriginal:
    if cutoff_at.tzinfo is None or cutoff_at.utcoffset() is None:
        raise ValueError("bls_cutoff_offset_required")
    row = session.get(ObservationRecord, observation_id, populate_existing=True)
    if row is None:
        raise ValueError("bls_observation_missing")
    payload = BlsCpiMetricPayload.model_validate_json(row.text)
    raw_text = json.dumps(payload.model_dump(mode="json"), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    capture = session.get(RawCaptureRecord, payload.capture_id, populate_existing=True)
    attempt = session.get(SourcePollAttemptRecord, payload.attempt_id, populate_existing=True)
    if (row.text != raw_text or hashlib.sha256(row.text.encode()).hexdigest() != row.content_hash
        or row.source_id != BLS_SOURCE_ID or row.source_type != "official_feed" or row.source_url != BLS_URL
        or row.event_hint != "cpi_index_metric"
        or row.published_at is not None or row.scheduled_at is not None
        or as_utc(row.received_at) != payload.received_at or as_utc(row.observed_at) != payload.received_at
        or capture is None or attempt is None
        or attempt.source_id != BLS_SOURCE_ID or attempt.status != "succeeded" or not 1 <= attempt.received_count <= 4
        or capture.context_id != "source:" + payload.attempt_id or capture.url != BLS_URL
        or capture.adapter_version != BLS_VERSION or capture.status_code != 200 or capture.error_code is not None
        or capture.body_hash != payload.raw_body_hash or capture.body_ref != payload.raw_body_ref
        or as_utc(capture.completed_at) != payload.received_at):
        raise ValueError("bls_native_original_binding_invalid")
    lower, started, completed, recorded, admitted = (as_utc(t) for t in (attempt.started_at, capture.started_at, capture.completed_at, capture.recorded_at, attempt.completed_at))
    cutoff = as_utc(cutoff_at)
    if any(t is None for t in (lower, started, completed, recorded, admitted, cutoff)) or not lower <= started <= completed <= recorded <= admitted <= cutoff:
        raise ValueError("bls_original_pit_invalid")
    snapshot = SourceContentSnapshot.model_validate_json(attempt.content_snapshot_json or "{}")
    if snapshot.http_status != 200 or snapshot.content_hash != payload.raw_body_hash or snapshot.parser_version != BLS_VERSION or snapshot.parse_status != "valid" or snapshot.parsed_count != 4:
        raise ValueError("bls_attempt_content_binding_invalid")
    body = archive.read(payload.raw_body_ref)
    if len(body) != capture.byte_count or hashlib.sha256(body).hexdigest() != payload.raw_body_hash:
        raise ValueError("bls_raw_archive_invalid")
    originals = parse_bls_cpi_metrics(body, reference_period=payload.reference_period, provenance={
        "capture_id": payload.capture_id, "attempt_id": payload.attempt_id, "received_at": payload.received_at,
        "request_startyear": payload.request_startyear, "request_endyear": payload.request_endyear,
    })
    matched = next(value for value in originals if value.metric_id == payload.metric_id)
    if matched != payload:
        raise ValueError("bls_metric_original_reparse_mismatch")
    # Completion is the first fully committed successful source admission bound
    # by this reader. Reference periods and public dates cannot move it earlier.
    return BlsCpiOriginal(row.observation_id, payload, admitted)

"""Normal native nowcast original reader; no consensus/Fact/Action authority."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from ..persistence.db import ObservationRecord, RawCaptureRecord, SourcePollAttemptRecord, as_utc
from ..ports.cpi_nowcast import NOWCAST_SOURCE_ID, NOWCAST_URL, NOWCAST_VERSION, CpiNowcastMetricPayload, parse_cpi_nowcast_metrics
from ..ports.sources import SourceContentSnapshot


@dataclass(frozen=True)
class CpiNowcastOriginal:
    observation_id: str
    payload: CpiNowcastMetricPayload
    known_at: datetime


def read_cpi_nowcast_original_in_session(session: Session, observation_id: str, *, archive, cutoff_at: datetime) -> CpiNowcastOriginal:
    if cutoff_at.tzinfo is None or cutoff_at.utcoffset() is None:
        raise ValueError("cpi_nowcast_cutoff_offset_required")
    row = session.get(ObservationRecord, observation_id, populate_existing=True)
    if row is None:
        raise ValueError("cpi_nowcast_observation_missing")
    payload = CpiNowcastMetricPayload.model_validate_json(row.text)
    raw = json.dumps(payload.model_dump(mode="json"), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    capture = session.get(RawCaptureRecord, payload.capture_id, populate_existing=True)
    attempt = session.get(SourcePollAttemptRecord, payload.attempt_id, populate_existing=True)
    if (row.text != raw or hashlib.sha256(row.text.encode()).hexdigest() != row.content_hash
        or row.source_id != NOWCAST_SOURCE_ID or row.source_type != "official_feed" or row.source_url != NOWCAST_URL
        or row.event_hint != "cpi_nowcast_metric" or row.published_at is not None or row.scheduled_at is not None
        or as_utc(row.received_at) != payload.received_at or as_utc(row.observed_at) != payload.received_at
        or payload.value is None or capture is None or attempt is None
        or attempt.source_id != NOWCAST_SOURCE_ID or attempt.status != "succeeded" or not 1 <= attempt.received_count <= 8
        or capture.context_id != "source:"+payload.attempt_id or capture.url != NOWCAST_URL
        or capture.adapter_version != NOWCAST_VERSION or capture.status_code != 200 or capture.error_code is not None
        or capture.body_hash != payload.raw_body_hash or capture.body_ref != payload.raw_body_ref
        or as_utc(capture.completed_at) != payload.received_at):
        raise ValueError("cpi_nowcast_native_original_binding_invalid")
    lower, started, completed, recorded, admitted = (as_utc(t) for t in (attempt.started_at, capture.started_at, capture.completed_at, capture.recorded_at, attempt.completed_at))
    cutoff = as_utc(cutoff_at)
    if any(t is None for t in (lower, started, completed, recorded, admitted, cutoff)) or not lower <= started <= completed <= recorded <= admitted <= cutoff:
        raise ValueError("cpi_nowcast_original_pit_invalid")
    snapshot = SourceContentSnapshot.model_validate_json(attempt.content_snapshot_json or "{}")
    if snapshot.http_status != 200 or snapshot.content_hash != payload.raw_body_hash or snapshot.parser_version != NOWCAST_VERSION or snapshot.parse_status != "valid" or snapshot.parsed_count not in {4, 8}:
        raise ValueError("cpi_nowcast_attempt_content_binding_invalid")
    body = archive.read(payload.raw_body_ref)
    if len(body) != capture.byte_count or hashlib.sha256(body).hexdigest() != payload.raw_body_hash:
        raise ValueError("cpi_nowcast_raw_archive_invalid")
    originals = parse_cpi_nowcast_metrics(body, reference_period=payload.reference_period, provenance={"capture_id":payload.capture_id,"attempt_id":payload.attempt_id,"received_at":payload.received_at})
    matched = next(value for value in originals if value.metric_id == payload.metric_id)
    if matched != payload:
        raise ValueError("cpi_nowcast_original_reparse_mismatch")
    return CpiNowcastOriginal(row.observation_id, payload, admitted)

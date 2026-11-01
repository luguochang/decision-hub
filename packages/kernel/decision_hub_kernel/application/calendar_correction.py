"""One auditable transaction for the v1 official-calendar parser correction."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime

from packages.contracts_py.decision_hub_contracts.models import TextEnvelope
from packages.kernel.decision_hub_kernel.application.admission import AdmissionService
from packages.kernel.decision_hub_kernel.application.macro_event import (
    retire_calendar_schedule_in_session,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    CalendarCorrectionRecord,
    Database,
    MacroEventRecord,
    as_utc,
)


def correct_legacy_calendar(
    database: Database,
    *,
    source_id: str,
    parser_version: str,
    source_content_hash: str,
    source_content_ref: str,
    valid_external_ids: set[str],
    envelopes: tuple[TextEnvelope, ...],
    now: datetime,
    audited_invalid_ids: set[str] | None = None,
) -> dict:
    if source_id not in {"fed-calendar", "boj-calendar", "ecb-calendar"}:
        raise ValueError("calendar_correction_source_not_supported")
    if not valid_external_ids or not envelopes:
        raise ValueError("calendar_correction_requires_verified_coverage")
    if any(item.source_id != source_id for item in envelopes):
        raise ValueError("calendar_correction_source_mismatch")
    audited_invalid_ids = audited_invalid_ids or set()
    if audited_invalid_ids & valid_external_ids:
        raise ValueError("calendar_correction_cannot_invalidate_verified_event")
    correction_id = (
        "calendar_fix_"
        + hashlib.sha256(
            f"{source_id}:{parser_version}:{source_content_hash}".encode()
        ).hexdigest()[:32]
    )
    with database.session() as session:
        previous = session.get(CalendarCorrectionRecord, correction_id)
        if previous:
            return _receipt(previous, replayed=True)
        rows = (
            session.query(MacroEventRecord)
            .filter_by(
                source_id=source_id,
                invalidated_at=None,
            )
            .all()
        )
        rows = [
            row
            for row in rows
            if (as_utc(row.scheduled_at) or now) >= now
            and row.status == "scheduled"
            and (
                (row.time_precision == "unknown" and row.correction_ref is None)
                or row.external_event_id in audited_invalid_ids
            )
        ]
        before = [
            {
                "macro_event_id": row.macro_event_id,
                "event_id": row.event_id,
                "external_event_id": row.external_event_id,
                "scheduled_at": (as_utc(row.scheduled_at) or now).isoformat(),
                "time_precision": row.time_precision,
                "source_timezone": row.source_timezone,
                "status": row.status,
            }
            for row in rows
        ]
        invalidated = [row for row in rows if row.external_event_id not in valid_external_ids]
        # The shared session makes observation admission, historical receipt,
        # invalidation and retirement atomic; a failed receipt rolls back all.
        AdmissionService(database, clock=lambda: now).admit_envelopes(envelopes, session=session)
        session.flush()
        for row in rows:
            row.correction_ref = correction_id
        for row in invalidated:
            row.invalidated_at = now
            row.updated_at = now
            retire_calendar_schedule_in_session(session, event_id=row.event_id, now=now)
        verified = (
            session.query(MacroEventRecord)
            .filter(
                MacroEventRecord.source_id == source_id,
                MacroEventRecord.external_event_id.in_(valid_external_ids),
            )
            .all()
        )
        record = CalendarCorrectionRecord(
            correction_id=correction_id,
            source_id=source_id,
            parser_version=parser_version,
            source_content_hash=source_content_hash,
            source_content_ref=source_content_ref,
            reason=(
                "official_html_day2_grouping_correction"
                if audited_invalid_ids
                else "official_html_v1_parse_and_time_precision_correction"
            ),
            before_json=json.dumps(before, sort_keys=True),
            invalidated_ids_json=json.dumps(sorted(row.macro_event_id for row in invalidated)),
            verified_ids_json=json.dumps(sorted(row.macro_event_id for row in verified)),
            created_at=now,
        )
        session.add(record)
        session.flush()
        return _receipt(record, replayed=False)


def _receipt(record: CalendarCorrectionRecord, *, replayed: bool) -> dict:
    return {
        "correction_id": record.correction_id,
        "source_id": record.source_id,
        "source_content_hash": record.source_content_hash,
        "before": json.loads(record.before_json),
        "invalidated": json.loads(record.invalidated_ids_json),
        "verified": json.loads(record.verified_ids_json),
        "replayed": replayed,
    }

"""Authenticate normal official schedule rows, including independent DOL period."""
import hashlib,json
from datetime import datetime
from dataclasses import dataclass
from ..persistence.db import ObservationRecord,RawCaptureRecord,SourcePollAttemptRecord,as_utc
from ..ports.cpi_schedule import CPI_SCHEDULE_SOURCE_ID,CPI_SCHEDULE_VERSION,CpiSchedulePayload,parse_cpi_publication_schedules
from ..ports.sources import SourceContentSnapshot

@dataclass(frozen=True)
class CpiScheduleOriginal:
    observation_id:str
    payload:CpiSchedulePayload
    known_at:datetime

def read_cpi_schedule_original_in_session(session,observation_id,*,archive,cutoff_at):
    if cutoff_at.tzinfo is None or cutoff_at.utcoffset() is None:raise ValueError('cpi_schedule_cutoff_offset_required')
    row=session.get(ObservationRecord,observation_id,populate_existing=True)
    if row is None:raise ValueError('cpi_schedule_observation_missing')
    value=CpiSchedulePayload.model_validate_json(row.text)
    canonical=json.dumps(value.model_dump(mode='json'),ensure_ascii=False,sort_keys=True,separators=(',',':'))
    attempt=session.get(SourcePollAttemptRecord,value.attempt_id,populate_existing=True)
    if (row.text!=canonical or hashlib.sha256(row.text.encode()).hexdigest()!=row.content_hash or row.source_id!=CPI_SCHEDULE_SOURCE_ID
        or row.event_hint!=value.event_key or row.source_type!='official_feed' or row.source_url!=value.fred_capture.url
        or row.published_at is not None or as_utc(row.scheduled_at)!=value.scheduled_at
        or as_utc(row.observed_at)!=value.received_at or as_utc(row.received_at)!=value.received_at
        or attempt is None or attempt.source_id!=CPI_SCHEDULE_SOURCE_ID or attempt.status!='succeeded' or not 1<=attempt.received_count<=12):raise ValueError('cpi_schedule_observation_parent_invalid')
    known=as_utc(attempt.completed_at);cutoff=as_utc(cutoff_at)
    if known is None or known>cutoff:raise ValueError('cpi_schedule_pit_invalid')
    bodies=[]
    from packages.provider_adapters.research.raw_capture import safe_url
    for binding in (value.fred_capture,value.dol_capture):
        if binding is None:bodies.append(None);continue
        capture=session.get(RawCaptureRecord,binding.capture_id,populate_existing=True)
        if (capture is None or capture.context_id!='source:'+value.attempt_id or capture.adapter_version!=CPI_SCHEDULE_VERSION
            or capture.url!=safe_url(binding.url) or capture.status_code!=200 or capture.error_code is not None
            or capture.body_hash!=binding.raw_body_hash or capture.body_ref!=binding.raw_body_ref
            or as_utc(capture.completed_at)!=binding.received_at):raise ValueError('cpi_schedule_capture_binding_invalid')
        clocks=[as_utc(t) for t in (attempt.started_at,capture.started_at,capture.completed_at,capture.recorded_at,attempt.completed_at)]
        if any(t is None for t in clocks) or clocks!=sorted(clocks):raise ValueError('cpi_schedule_native_clock_invalid')
        body=archive.read(binding.raw_body_ref)
        if len(body)!=capture.byte_count or hashlib.sha256(body).hexdigest()!=binding.raw_body_hash:raise ValueError('cpi_schedule_raw_archive_invalid')
        bodies.append(body)
    snapshot=SourceContentSnapshot.model_validate_json(attempt.content_snapshot_json or '{}')
    if snapshot.http_status!=200 or snapshot.content_hash!=value.fred_capture.raw_body_hash or snapshot.parser_version!=CPI_SCHEDULE_VERSION or snapshot.parse_status!='valid':raise ValueError('cpi_schedule_attempt_snapshot_invalid')
    items=parse_cpi_publication_schedules(bodies[0],bodies[1],year=value.calendar_year,fred_capture=value.fred_capture.model_dump(mode='python'),dol_capture=value.dol_capture.model_dump(mode='python') if value.dol_capture else None,attempt_id=value.attempt_id)
    if snapshot.parsed_count!=len(items) or next((p for p in items if p.event_key==value.event_key),None)!=value:raise ValueError('cpi_schedule_original_reparse_mismatch')
    return CpiScheduleOriginal(row.observation_id,value,known)

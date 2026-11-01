"""Authenticate source/capture/archive; never backfill historical known clocks."""
import hashlib,json
from dataclasses import dataclass
from datetime import datetime
from ..persistence.db import ObservationRecord,RawCaptureRecord,SourcePollAttemptRecord,as_utc
from ..ports.cpi_nowcast_history import SOURCE,VERSION,CpiHistoryPayload,parse_cpi_history
from ..ports.market_path import aware_utc
from ..ports.sources import SourceContentSnapshot
from packages.provider_adapters.research.raw_capture import safe_url

@dataclass(frozen=True)
class CpiHistoryOriginal:
    observation_id:str
    payload:CpiHistoryPayload
    known_at:datetime
    month_body:bytes
    year_body:bytes

def read_cpi_history_original_in_session(session,observation_id,*,archive,cutoff_at):
    cutoff=aware_utc(cutoff_at);row=session.get(ObservationRecord,observation_id,populate_existing=True)
    if row is None:raise ValueError('cpi_history_observation_missing')
    payload=CpiHistoryPayload.model_validate_json(row.text);canonical=json.dumps(payload.model_dump(mode='json'),sort_keys=True,ensure_ascii=False,separators=(',',':'))
    attempt=session.get(SourcePollAttemptRecord,payload.attempt_id,populate_existing=True)
    if (row.text!=canonical or hashlib.sha256(row.text.encode()).hexdigest()!=row.content_hash or row.source_id!=SOURCE or row.source_type!='official_feed' or row.source_url!=payload.month_capture.url or row.event_hint!='cpi_nowcast_history'
        or row.published_at is not None or as_utc(row.received_at)!=payload.received_at or as_utc(row.observed_at)!=payload.received_at or attempt is None or attempt.source_id!=SOURCE or attempt.status!='succeeded' or attempt.received_count!=1):raise ValueError('cpi_history_observation_parent_invalid')
    known=as_utc(attempt.completed_at)
    if known is None or known>cutoff:raise ValueError('cpi_history_known_after_cutoff')
    bodies=[]
    for ref in (payload.month_capture,payload.year_capture):
        capture=session.get(RawCaptureRecord,ref.capture_id,populate_existing=True)
        if (capture is None or capture.context_id!='source:'+payload.attempt_id or capture.adapter_version!=VERSION or capture.url!=safe_url(ref.url) or capture.status_code!=200 or capture.error_code is not None or capture.body_ref!=ref.raw_body_ref or capture.body_hash!=ref.raw_body_hash or as_utc(capture.completed_at)!=ref.received_at):raise ValueError('cpi_history_native_capture_crosswired')
        clocks=[as_utc(v) for v in (attempt.started_at,capture.started_at,capture.completed_at,capture.recorded_at,attempt.completed_at)]
        if any(v is None for v in clocks) or clocks!=sorted(clocks):raise ValueError('cpi_history_native_clocks_invalid')
        body=archive.read(ref.raw_body_ref)
        if len(body)!=capture.byte_count or hashlib.sha256(body).hexdigest()!=ref.raw_body_hash:raise ValueError('cpi_history_raw_original_changed')
        bodies.append(body)
    monthly,yearly=parse_cpi_history(*bodies)
    snapshot=SourceContentSnapshot.model_validate_json(attempt.content_snapshot_json or '{}')
    if len(monthly)!=payload.period_count or snapshot.parsed_count!=payload.period_count or snapshot.parser_version!=VERSION or snapshot.parse_status!='valid' or snapshot.http_status!=200 or snapshot.content_hash!=payload.month_capture.raw_body_hash:raise ValueError('cpi_history_source_snapshot_invalid')
    return CpiHistoryOriginal(row.observation_id,payload,known,*bodies)

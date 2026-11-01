"""Native historical release reader; observation/capture/attempt/raw all bound."""
import hashlib,json
from dataclasses import dataclass
from datetime import datetime
from ..persistence.db import ObservationRecord,RawCaptureRecord,SourcePollAttemptRecord,as_utc
from ..ports.cpi_release_history import SOURCE,VERSION,CpiReleaseHistoryPayload,parse_cpi_release_history
from ..ports.sources import SourceContentSnapshot
from ..ports.market_path import aware_utc
@dataclass(frozen=True)
class CpiReleaseHistoryOriginal:
    observation_id:str
    payload:CpiReleaseHistoryPayload
    known_at:datetime
def read_cpi_release_history_original_in_session(session,observation_id,*,archive,cutoff_at):
    cutoff=aware_utc(cutoff_at);row=session.get(ObservationRecord,observation_id,populate_existing=True)
    if row is None:raise ValueError('cpi_release_history_original_missing')
    p=CpiReleaseHistoryPayload.model_validate_json(row.text);c=session.get(RawCaptureRecord,p.capture_id,populate_existing=True);a=session.get(SourcePollAttemptRecord,p.attempt_id,populate_existing=True)
    raw=json.dumps(p.model_dump(mode='json'),sort_keys=True,ensure_ascii=False,separators=(',',':'))
    if row.text!=raw or row.content_hash!=hashlib.sha256(raw.encode()).hexdigest() or row.source_id!=SOURCE or row.source_type!='official_feed' or row.source_url!=p.document_url or row.event_hint!='cpi_historical_release' or row.published_at is not None or row.scheduled_at is not None or as_utc(row.received_at)!=p.received_at or as_utc(row.observed_at)!=p.received_at or c is None or a is None or a.source_id!=SOURCE or a.status!='succeeded' or not 1<=a.received_count<=2 or c.context_id!='source:'+p.attempt_id or c.adapter_version!=VERSION or c.url!=p.document_url or c.status_code!=200 or c.error_code is not None or c.body_hash!=p.raw_body_hash or c.body_ref!=p.raw_body_ref or as_utc(c.completed_at)!=p.received_at:raise ValueError('cpi_release_history_original_identity_invalid')
    clocks=[as_utc(t) for t in (a.started_at,c.started_at,c.completed_at,c.recorded_at,a.completed_at)]
    if any(t is None for t in clocks) or clocks!=sorted(clocks) or clocks[-1]>cutoff:raise ValueError('cpi_release_history_original_clock_invalid')
    snap=SourceContentSnapshot.model_validate_json(a.content_snapshot_json or '{}')
    if snap.http_status!=200 or snap.parser_version!=VERSION or snap.parse_status!='valid' or snap.parsed_count!=2:raise ValueError('cpi_release_history_attempt_invalid')
    body=archive.read(p.raw_body_ref)
    if len(body)!=c.byte_count or hashlib.sha256(body).hexdigest()!=p.raw_body_hash:raise ValueError('cpi_release_history_raw_invalid')
    expected=parse_cpi_release_history(body,provenance={'document_url':p.document_url,'capture_id':p.capture_id,'attempt_id':p.attempt_id,'received_at':p.received_at})
    if expected!=p:raise ValueError('cpi_release_history_reparse_invalid')
    return CpiReleaseHistoryOriginal(row.observation_id,p,clocks[-1])

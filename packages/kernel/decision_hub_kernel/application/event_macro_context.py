"""Native daily macro originals and causal-policy context, independent of Runs."""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import hashlib, json
from sqlalchemy import select
from ..persistence.db import ObservationRecord, RawCaptureRecord, SourcePollAttemptRecord, as_utc
from ..ports.market_path import aware_utc, canonical_hash
from ..ports.sources import SourceContentSnapshot
from ..ports.macro_daily import VERSION,SERIES,MacroDailyPayload,source_id,source_url,capture_url,parse_macro_daily
from ..ports.event_macro_context import EventMacroSeries,EventMacroContext

@dataclass(frozen=True)
class MacroDailyOriginal:
    observation_id:str
    payload:MacroDailyPayload
    content_hash:str
    known_at:datetime

def read_macro_daily_original_in_session(session,observation_id,*,archive,cutoff_at):
    cutoff=aware_utc(cutoff_at);row=session.get(ObservationRecord,observation_id,populate_existing=True)
    if row is None:raise ValueError('macro_daily_observation_missing')
    value=MacroDailyPayload.model_validate_json(row.text)
    raw=json.dumps(value.model_dump(mode='json'),ensure_ascii=False,sort_keys=True,separators=(',',':'))
    capture=session.get(RawCaptureRecord,value.capture_id,populate_existing=True)
    attempt=session.get(SourcePollAttemptRecord,value.attempt_id,populate_existing=True)
    if (row.text!=raw or row.content_hash!=hashlib.sha256(raw.encode()).hexdigest()
        or row.source_id!=value.source_id or row.source_type!='official_feed' or row.source_url!=value.source_url
        or row.event_hint!='macro_daily_background' or row.published_at is not None or row.scheduled_at is not None
        or as_utc(row.received_at)!=value.received_at or as_utc(row.observed_at)!=value.received_at
        or capture is None or attempt is None or attempt.source_id!=value.source_id or attempt.status!='succeeded' or attempt.received_count!=1
        or capture.context_id!='source:'+value.attempt_id or capture.url!=capture_url(value.series_id) or capture.adapter_version!=VERSION
        or capture.status_code!=200 or capture.error_code is not None or capture.body_hash!=value.raw_body_hash
        or capture.body_ref!=value.raw_body_ref or as_utc(capture.completed_at)!=value.received_at):
        raise ValueError('macro_daily_native_binding_invalid')
    clocks=[as_utc(x) for x in (attempt.started_at,capture.started_at,capture.completed_at,capture.recorded_at,attempt.completed_at)]
    if any(x is None for x in clocks) or not clocks[0]<=clocks[1]<=clocks[2]<=clocks[3]<=clocks[4]<=cutoff:
        raise ValueError('macro_daily_native_clock_invalid')
    snap=SourceContentSnapshot.model_validate_json(attempt.content_snapshot_json or '{}')
    if snap.http_status!=200 or snap.content_hash!=value.raw_body_hash or snap.parser_version!=VERSION or snap.parse_status!='valid' or snap.parsed_count!=1:
        raise ValueError('macro_daily_attempt_content_invalid')
    body=archive.read(value.raw_body_ref)
    if len(body)!=capture.byte_count or hashlib.sha256(body).hexdigest()!=value.raw_body_hash:
        raise ValueError('macro_daily_archive_invalid')
    parsed=parse_macro_daily(body,series_id=value.series_id,provenance={'capture_id':value.capture_id,'attempt_id':value.attempt_id,'received_at':value.received_at})
    if parsed!=value:raise ValueError('macro_daily_original_reparse_mismatch')
    return MacroDailyOriginal(row.observation_id,value,row.content_hash,clocks[4])

def collect_event_macro_context_in_session(session,*,archive,cutoff_at):
    cutoff=aware_utc(cutoff_at);series=[];gaps=[]
    for identity,(_,_,max_age_days) in SERIES.items():
        rows=session.scalars(select(ObservationRecord).where(ObservationRecord.source_id==source_id(identity),ObservationRecord.received_at<=cutoff).order_by(ObservationRecord.received_at.desc(),ObservationRecord.observation_id).limit(8)).all()
        selected=None
        for row in rows:
            # A source observation may be admitted while its attempt is still
            # completing; it has no knowledge authority before that commit.
            try:payload=MacroDailyPayload.model_validate_json(row.text)
            except ValueError:
                gaps.append(identity+':original_invalid');break
            attempt=session.get(SourcePollAttemptRecord,payload.attempt_id,populate_existing=True)
            if attempt is None or attempt.status!='succeeded' or as_utc(attempt.completed_at) is None or as_utc(attempt.completed_at)>cutoff:continue
            try:selected=read_macro_daily_original_in_session(session,row.observation_id,archive=archive,cutoff_at=cutoff)
            except (ValueError,FileNotFoundError):gaps.append(identity+':original_invalid')
            break
        if selected is None:
            if not any(x.startswith(identity+':') for x in gaps):gaps.append(identity+':source_not_admitted')
            continue
        value=selected.payload;points=value.points
        if (cutoff.date()-points[-1].observation_date).days>max_age_days:
            gaps.append(identity+':observation_stale');continue
        series.append(EventMacroSeries(series_id=identity,label=value.label,unit=value.unit,value=points[-1].value,observation_date=points[-1].observation_date,
            change_1_observation=points[-1].value-points[-2].value,change_5_observations=points[-1].value-points[0].value,
            known_at=selected.known_at,observation_ref=selected.observation_id,content_hash=selected.content_hash,raw_body_ref=value.raw_body_ref,raw_body_hash=value.raw_body_hash))
    by={s.series_id:s for s in series};state='unknown'
    if {'DFII10','DTWEXBGS'}<=by.keys():
        # Transparent research regime, not a fitted return model. Actual yield
        # uses percentage points; broad dollar uses a relative five-period move.
        real=by['DFII10'].change_5_observations
        dollar=by['DTWEXBGS'];base=dollar.value-dollar.change_5_observations
        usd=dollar.change_5_observations/base
        state='headwind' if real>=Decimal('.05') and usd>=Decimal('.0025') else 'tailwind' if real<=Decimal('-.05') and usd<=Decimal('-.0025') else 'mixed'
    gaps.extend(('daily_background_not_intraday_or_historical_vintage','survey_consensus_and_event_pricing_not_bound'))
    value=EventMacroContext(as_of=cutoff,state=state,series=tuple(series),gaps=tuple(gaps),payload_hash='0'*64)
    return value.model_copy(update={'payload_hash':canonical_hash(value.model_dump(mode='json',exclude={'payload_hash'}))})

def context_input_identity(context):
    """Stable selection identity; current clock alone is not new evidence."""
    return context.input_hash

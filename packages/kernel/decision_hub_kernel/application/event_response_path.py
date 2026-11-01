"""Normal native sources and OKX minute originals feed a reproducible model."""
import hashlib
from datetime import timedelta
from sqlalchemy import select
from ..persistence.db import ObservationRecord,SourcePollAttemptRecord,as_utc
from ..ports.market_path import aware_utc,canonical_hash,spot_scope
from ..ports.bls_cpi import BLS_SOURCE_ID,parse_bls_cpi_metric_batches,BlsCpiMetricPayload
from ..ports.cpi_release_history import SOURCE
from ..ports.event_response_path import EventResponseModel
from ..decision.action_research_policy import research_action_catalog
from ..decision.event_response_model import response_sample
from .market_path_store import MarketPathStore
from .bls_cpi_original import read_bls_cpi_original_in_session
from .cpi_release_history_original import read_cpi_release_history_original_in_session

def _response_original(session,observation_id,*,archive,cutoff,kind,reader):
    from .event_read_scope import _native_read_once
    def archive_guard(value):
        if hashlib.sha256(archive.read(value.payload.raw_body_ref)).hexdigest()!=value.payload.raw_body_hash:
            raise ValueError('event_response_raw_original_changed')
    value=_native_read_once(session,archive,(kind,observation_id),lambda:reader(session,observation_id,archive=archive,cutoff_at=cutoff),validate=archive_guard)
    if value.known_at>cutoff:
        raise ValueError('bls_original_pit_invalid' if kind=='response_bls' else 'cpi_release_history_original_clock_invalid')
    return value

def collect_event_response_model_in_session(session,instrument,*,archive,cutoff_at,macro_context=None):
    cutoff=aware_utc(cutoff_at);scope=spot_scope(instrument);catalog=research_action_catalog(instrument)
    rows=session.scalars(select(ObservationRecord).where(ObservationRecord.source_id==SOURCE,ObservationRecord.received_at<=cutoff).order_by(ObservationRecord.received_at.desc(),ObservationRecord.observation_id)).all()
    releases={}
    for row in rows:
        value=_response_original(session,row.observation_id,archive=archive,cutoff=cutoff,kind='response_release',reader=read_cpi_release_history_original_in_session)
        releases.setdefault(value.payload.event_key,(value,row.content_hash))
    candidates=session.scalars(select(ObservationRecord).where(ObservationRecord.source_id==BLS_SOURCE_ID,ObservationRecord.received_at<=cutoff).order_by(ObservationRecord.received_at.desc(),ObservationRecord.observation_id)).all()
    bls=None;bls_hash=None;body=None
    for row in candidates:
        payload=BlsCpiMetricPayload.model_validate_json(row.text);attempt=session.get(SourcePollAttemptRecord,payload.attempt_id,populate_existing=True)
        if attempt is None or attempt.status!='succeeded' or as_utc(attempt.completed_at) is None or as_utc(attempt.completed_at)>cutoff:continue
        bls=_response_original(session,row.observation_id,archive=archive,cutoff=cutoff,kind='response_bls',reader=read_bls_cpi_original_in_session);bls_hash=row.content_hash;body=archive.read(bls.payload.raw_body_ref);break
    periods=tuple(dict.fromkeys(release.payload.reference_period for release,_hash in releases.values()
        if release.payload.scheduled_at+timedelta(hours=24,seconds=60)<=cutoff))
    # The same authenticated BLS bytes were formerly decoded/indexed once per
    # historical event. Only pure indexing is shared; each period gets fresh
    # original metadata, and the native reader/PIT/archive checks above remain.
    rates_by_period=dict(zip(periods,parse_bls_cpi_metric_batches(body,reference_periods=periods,
        provenance={'capture_id':bls.payload.capture_id,'attempt_id':bls.payload.attempt_id,
            'received_at':bls.payload.received_at,'request_startyear':bls.payload.request_startyear,
            'request_endyear':bls.payload.request_endyear}),strict=True)) if bls is not None else {}
    samples=[];excluded=[];store=MarketPathStore(None,archive)
    for key,(release,release_hash) in sorted(releases.items()):
        t0=release.payload.scheduled_at;end=t0+timedelta(hours=24,seconds=60)
        if end>cutoff:excluded.append(key+':event_horizon_not_mature');continue
        if bls is None:excluded.append(key+':native_bls_actual_prior_missing');continue
        rates=rates_by_period[release.payload.reference_period]
        captures=[]
        start=t0.replace(minute=0,second=0,microsecond=0)-timedelta(hours=21)
        bars=store.load_in_session(session,scope,start_at=start,end_at=end,as_of=cutoff,on_original=lambda ref,hash:captures.append((ref,hash)))
        if len(bars)!=int((end-start).total_seconds()/60):excluded.append(key+':native_price_path_incomplete');continue
        if macro_context is None:
            sample=response_sample(release,release_hash=release_hash,bls=bls,bls_hash=bls_hash,rates={r.metric_id:r for r in rates},scope=scope,bars=bars,captures=captures)
        else:
            from ..decision.event_response_model_v2 import response_sample_v2
            sample=response_sample_v2(release,release_hash=release_hash,bls=bls,bls_hash=bls_hash,rates={r.metric_id:r for r in rates},scope=scope,bars=bars,captures=captures,macro_context=macro_context)
        samples.append(sample)
    if not rows:excluded.append('cpi_release_history_source_not_admitted')
    body={'instrument':instrument,'scope_id':scope.scope_id,'as_of':cutoff,'samples':tuple(samples),'excluded':tuple(excluded),'fee_fraction_each_side':'.001','slippage_fraction_each_side':'.0005','cost_policy_ref':catalog['cost_policy']['ref'],'catalog_hash':canonical_hash(catalog),'payload_hash':'0'*64}
    if macro_context is None:value=EventResponseModel(**body)
    else:
        from ..ports.event_response_path import EventResponseModelV2
        from ..decision.event_opportunity_policy import event_policy_catalog
        if macro_context.as_of!=cutoff:raise ValueError('event_response_macro_cutoff_mismatch')
        policies=event_policy_catalog(scope.scope_id,macro_context)
        value=EventResponseModelV2(**body,macro_context_hash=macro_context.input_hash,policy_catalog_hash=canonical_hash([p.model_dump(mode='json') for p in policies]))
    return value.model_copy(update={'payload_hash':canonical_hash(value.model_dump(mode='json',exclude={'payload_hash'}))})

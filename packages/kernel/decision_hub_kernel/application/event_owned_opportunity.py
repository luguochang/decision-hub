"""Calendar-owned immutable opportunities: no manual Run or model required."""
import json,hashlib
from dataclasses import dataclass
from datetime import datetime,timedelta
from weakref import WeakKeyDictionary
from pathlib import Path
from sqlalchemy import select
from ..persistence.db import as_utc,utcnow,ObservationRecord,SourcePollAttemptRecord,RawCaptureRecord
from ..persistence.risk_records import MarketCandleRecord
from ..persistence.event_scenario_records import EventScenarioOutputRecord,EventScenarioForecastRecord,EventScenarioJobRecord
from ..ports.event_opportunity import EventOwnedOpportunitySnapshot,EventOwnedOpportunitySnapshotV2
from ..ports.event_response_path import EventResponseModelV2
from ..ports.market_path import aware_utc,canonical_hash
from ..decision.event_opportunity import build_event_opportunity
from .event_opportunity_snapshot import event_opportunity_user_view
from .event_scenario_autoproduction import read_event_owned_scenario_in_session
from .event_response_path import collect_event_response_model_in_session
from .cpi_pre_event_context import _archive

KIND='causal_opportunity';SCHEMA='event-opportunity-snapshot.v2'
__all__=['event_opportunity_user_view','freeze_event_owned_opportunity_in_session','prepare_event_owned_opportunity_in_session','commit_prepared_event_owned_opportunity_in_session','read_event_owned_opportunity_in_session','read_latest_event_owned_opportunity_for_event_in_session']
def _json(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def _parse(payload):
    schema=json.loads(payload).get('schema_version')
    if schema=='event-opportunity-snapshot.v1':return EventOwnedOpportunitySnapshot.model_validate_json(payload)
    if schema==SCHEMA:return EventOwnedOpportunitySnapshotV2.model_validate_json(payload)
    raise ValueError('event_owned_opportunity_schema_unsupported')
def _build(scenario,model,*,published_at,context=None):
    if isinstance(model,EventResponseModelV2):
        from ..decision.event_opportunity_v2 import build_event_opportunity_v2
        return build_event_opportunity_v2(scenario,model,published_at=published_at,macro_context=context)
    return build_event_opportunity(scenario,model,published_at=published_at)
def _instrument(value):
    if value not in {'BTC-USDT','ETH-USDT'}:raise ValueError('event_owned_opportunity_scope_not_enabled')
    return value
def _inputs(scenario,model,revision_parent_ref=None):
    body={'forecast_id':scenario.snapshot_id,'forecast_hash':scenario.payload_hash,'estimation_window_6h':int(model.as_of.timestamp())//21600,'price_inputs':model.model_dump(mode='json',exclude={'as_of','payload_hash'})}
    if isinstance(model,EventResponseModelV2):body['revision_parent_ref']=revision_parent_ref
    return canonical_hash(body)
def _method(model):
    if isinstance(model,EventResponseModelV2):
        from ..decision import event_response_model_v2,event_opportunity_v2,event_opportunity_policy
        from . import event_macro_context
        return canonical_hash({'version':model.model_version,'catalog_hash':model.catalog_hash,'policy_catalog_hash':model.policy_catalog_hash,'fee':str(model.fee_fraction_each_side),'slippage':str(model.slippage_fraction_each_side),'algorithm_sha256':{module.__name__.rsplit('.',1)[-1]:hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest() for module in (event_response_model_v2,event_opportunity_v2,event_opportunity_policy,event_macro_context)}})
    from ..decision import event_response_model,event_opportunity
    return canonical_hash({'version':model.model_version,'catalog_hash':model.catalog_hash,'fee':str(model.fee_fraction_each_side),'slippage':str(model.slippage_fraction_each_side),'response_algorithm_sha256':hashlib.sha256(Path(event_response_model.__file__).read_bytes()).hexdigest(),'plan_algorithm_sha256':hashlib.sha256(Path(event_opportunity.__file__).read_bytes()).hexdigest()})
def _with_parent(value,parent):
    value=value.model_copy(update={'revision_parent_ref':parent})
    if isinstance(value,EventOwnedOpportunitySnapshotV2) and parent is not None:
        value=value.model_copy(update={'snapshot_id':'event-opp-'+canonical_hash([value.snapshot_id,parent])[:48]})
    return value.model_copy(update={'payload_hash':canonical_hash(value.model_dump(mode='json',exclude={'payload_hash'}))})

def _ensure_physical_read_snapshot(session):
    # SQLite's legacy SELECT mode does not start a transaction. Freeze the
    # source/macro/release view before the first read, not just price loading.
    connection=session.connection()
    if connection.dialect.name=='sqlite':
        native=connection.connection.driver_connection
        if native is None:raise ValueError('event_opportunity_physical_snapshot_required')
        if not native.in_transaction:connection.exec_driver_sql('BEGIN')

def read_event_owned_opportunity_in_session(session,output_id,*,archive=None,cutoff_at):
    _ensure_physical_read_snapshot(session)
    archive=archive or _archive();cutoff=aware_utc(cutoff_at);row=session.get(EventScenarioOutputRecord,output_id,populate_existing=True)
    if row is None:raise ValueError('event_owned_opportunity_original_missing')
    value=_parse(row.payload_json);body=value.model_dump(mode='json')
    if (row.output_id!=value.snapshot_id or row.forecast_id!=value.forecast_id or row.owner_forecast_hash!=value.scenario_snapshot_hash or row.event_key!=value.event_key or row.kind!=KIND or row.scope_key!=value.instrument or row.schema_version!=value.schema_version or row.payload_json!=_json(body) or row.payload_hash!=canonical_hash(body) or value.payload_hash!=canonical_hash(value.model_dump(mode='json',exclude={'payload_hash'})) or row.revision_parent_ref!=value.revision_parent_ref or as_utc(row.published_at)!=value.published_at or value.published_at>cutoff):raise ValueError('event_owned_opportunity_native_identity_invalid')
    scenario=read_event_owned_scenario_in_session(session,value.forecast_id,archive=archive,cutoff_at=value.as_of)
    if scenario.payload_hash!=value.scenario_snapshot_hash or scenario.event_key!=value.event_key or scenario.reference_period!=value.reference_period or scenario.scheduled_at!=value.scheduled_at:raise ValueError('event_owned_opportunity_forecast_original_crosswired')
    context=None
    if isinstance(value,EventOwnedOpportunitySnapshotV2):
        from .event_macro_context import collect_event_macro_context_in_session
        from .event_read_scope import _native_read_once
        def macro_archive_guard(context):
            for series in context.series:
                if hashlib.sha256(archive.read(series.raw_body_ref)).hexdigest()!=series.raw_body_hash:raise ValueError('event_owned_opportunity_macro_original_changed')
        context=_native_read_once(session,archive,('macro',value.as_of),lambda:collect_event_macro_context_in_session(session,archive=archive,cutoff_at=value.as_of),validate=macro_archive_guard)
        if context!=value.macro_context:raise ValueError('event_owned_opportunity_macro_original_changed')
    model=collect_event_response_model_in_session(session,value.instrument,archive=archive,cutoff_at=value.as_of,macro_context=context)
    expected=_with_parent(_build(scenario,model,published_at=value.published_at,context=context),row.revision_parent_ref)
    if row.inputs_hash!=_inputs(scenario,model,value.revision_parent_ref) or row.model_hash!=_method(model) or expected!=value:raise ValueError('event_owned_opportunity_model_original_changed')
    if row.revision_parent_ref:
        parent=session.get(EventScenarioOutputRecord,row.revision_parent_ref,populate_existing=True)
        if parent is None or parent.kind!=KIND or parent.event_key!=row.event_key or parent.scope_key!=row.scope_key or as_utc(parent.published_at)>=value.published_at:raise ValueError('event_owned_opportunity_revision_lineage_invalid')
        prior=_parse(parent.payload_json);prior_body=prior.model_dump(mode='json')
        if (parent.output_id!=prior.snapshot_id or parent.forecast_id!=prior.forecast_id or parent.owner_forecast_hash!=prior.scenario_snapshot_hash or parent.schema_version!=prior.schema_version or parent.revision_parent_ref!=prior.revision_parent_ref or parent.payload_json!=_json(prior_body) or parent.payload_hash!=canonical_hash(prior_body) or prior.payload_hash!=canonical_hash(prior.model_dump(mode='json',exclude={'payload_hash'})) or as_utc(parent.published_at)!=prior.published_at or prior.event_key!=value.event_key or prior.instrument!=value.instrument):raise ValueError('event_owned_opportunity_parent_original_invalid')
        original=read_event_owned_scenario_in_session(session,prior.forecast_id,archive=archive,cutoff_at=prior.as_of)
        if original.payload_hash!=prior.scenario_snapshot_hash:raise ValueError('event_owned_opportunity_parent_forecast_invalid')
        if parent.inputs_hash!=_inputs(original,prior.model,prior.revision_parent_ref):raise ValueError('event_owned_opportunity_parent_inputs_invalid')
    return value

def read_latest_event_owned_opportunity_for_event_in_session(session,event_key,instrument,*,archive=None,cutoff_at):
    _ensure_physical_read_snapshot(session)
    cutoff=aware_utc(cutoff_at);instrument=_instrument(instrument)
    row=session.scalar(select(EventScenarioOutputRecord).where(EventScenarioOutputRecord.event_key==event_key,EventScenarioOutputRecord.kind==KIND,EventScenarioOutputRecord.scope_key==instrument,EventScenarioOutputRecord.published_at<=cutoff).order_by(EventScenarioOutputRecord.published_at.desc(),EventScenarioOutputRecord.output_id).limit(1))
    return read_event_owned_opportunity_in_session(session,row.output_id,archive=archive,cutoff_at=cutoff) if row else None

_GUARD_TYPES={'observation':ObservationRecord,'source_attempt':SourcePollAttemptRecord,'raw_capture':RawCaptureRecord,'forecast':EventScenarioForecastRecord,'forecast_job':EventScenarioJobRecord}
_ISSUED=WeakKeyDictionary()
@dataclass(frozen=True,eq=False)
class PreparedEventOwnedOpportunity:
    scenario:object
    model:object
    source_guard:tuple
    path_guard:tuple
    raw_guard:tuple
    context:object=None
def _row_hash(row):
    body={}
    for column in row.__table__.columns:
        value=getattr(row,column.name)
        body[column.name]=as_utc(value).isoformat() if isinstance(value,datetime) else value
    return canonical_hash(body)
def _path_guard(session,model):
    result=[]
    for sample in model.samples:
        start=sample.scheduled_at.replace(minute=0,second=0,microsecond=0)-timedelta(hours=21);end=sample.reaction_end_at
        rows=session.scalars(select(MarketCandleRecord).where(MarketCandleRecord.scope_id==model.scope_id,MarketCandleRecord.open_at>=start,MarketCandleRecord.close_at<=end,MarketCandleRecord.known_at<=model.as_of).order_by(MarketCandleRecord.candle_id)).all()
        result.append((start,end,canonical_hash([(r.candle_id,_row_hash(r)) for r in rows])))
    return tuple(result)
def _prepare_guard(session,scenario,model,context=None,*,archive=None):
    keys={(t,k) for t,k,h in scenario.source_guard}
    keys.update({('forecast',scenario.forecast_id),('forecast_job',scenario.job_id)})
    def capture_ids(value):
        if isinstance(value,dict):
            if isinstance(value.get('capture_id'),str):yield value['capture_id']
            for child in value.values():yield from capture_ids(child)
        elif isinstance(value,list):
            for child in value:yield from capture_ids(child)
    if isinstance(model,EventResponseModelV2) and context is None:
        from .event_macro_context import collect_event_macro_context_in_session
        context=collect_event_macro_context_in_session(session,archive=archive or _archive(),cutoff_at=model.as_of)
    observations={oid for sample in model.samples for oid in (sample.release_observation_id,sample.bls_observation_id)}
    if context is not None:observations.update(x.observation_ref for x in context.series)
    for oid in sorted(observations):
            row=session.get(ObservationRecord,oid,populate_existing=True)
            if row is None:raise ValueError('event_opportunity_source_guard_missing')
            payload=json.loads(row.text);keys.update({('observation',oid),('source_attempt',payload['attempt_id'])})
            keys.update(('raw_capture',cid) for cid in capture_ids(payload))
    rawrefs={ref for s in model.samples for ref,hash in s.capture_originals}
    for c in session.scalars(select(RawCaptureRecord).where(RawCaptureRecord.body_ref.in_(rawrefs),RawCaptureRecord.recorded_at<=model.as_of)):
        keys.add(('raw_capture',c.capture_id))
    guard=[];raw=[]
    for table,key in sorted(keys):
        cls=_GUARD_TYPES.get(table);row=session.get(cls,key,populate_existing=True) if cls else None
        if row is None:raise ValueError('event_opportunity_source_guard_missing')
        guard.append((table,key,_row_hash(row)))
        if table=='raw_capture' and row.body_ref and row.body_hash:raw.append((row.body_ref,row.body_hash))
    return tuple(guard),tuple(sorted(set(raw)))

def opportunity_reuse_context_fingerprint_in_session(session,forecast_id,instrument,*,cutoff_at,archive=None):
    from .event_macro_context import collect_event_macro_context_in_session
    return collect_event_macro_context_in_session(session,archive=archive or _archive(),cutoff_at=cutoff_at).input_hash

def prepare_event_owned_opportunity_in_session(session,forecast_id,*,instrument,archive=None):
    """RO scope: full native originals and price replay, outside writer lock."""
    _ensure_physical_read_snapshot(session)
    archive=archive or _archive();instrument=_instrument(instrument);cutoff=utcnow()
    scenario=read_event_owned_scenario_in_session(session,forecast_id,archive=archive,cutoff_at=cutoff)
    if cutoff>=scenario.scheduled_at:raise ValueError('event_owned_opportunity_pre_event_window_closed')
    from .event_macro_context import collect_event_macro_context_in_session
    context=collect_event_macro_context_in_session(session,archive=archive,cutoff_at=cutoff)
    model=collect_event_response_model_in_session(session,instrument,archive=archive,cutoff_at=cutoff,macro_context=context)
    if not model.samples:raise ValueError('event_owned_opportunity_price_samples_missing')
    sources,raw=_prepare_guard(session,scenario,model,context)
    prepared=PreparedEventOwnedOpportunity(scenario,model,sources,_path_guard(session,model),raw,context)
    _ISSUED[prepared]=True
    return prepared

def commit_prepared_event_owned_opportunity_in_session(session,prepared,*,archive=None):
    """Short fresh writer; actual caller checks its job owner/generation/lease.

    Native preparation identity is one use and weakly held, not a verified-data
    cache. Writer rechecks source rows, bounded path versions and archived bytes.
    """
    if not isinstance(prepared,PreparedEventOwnedOpportunity) or not _ISSUED.pop(prepared,False):raise ValueError('event_owned_opportunity_preparation_not_native')
    archive=archive or _archive();scenario=prepared.scenario;model=prepared.model;instrument=_instrument(model.instrument);forecast_id=scenario.forecast_id
    now=utcnow()
    if now>=scenario.scheduled_at or now>=model.as_of+timedelta(hours=6):raise ValueError('event_owned_opportunity_preparation_expired')
    for table,key,digest in prepared.source_guard:
        row=session.get(_GUARD_TYPES[table],key,populate_existing=True)
        if row is None or _row_hash(row)!=digest:raise ValueError('event_owned_opportunity_source_guard_changed')
    if _path_guard(session,model)!=prepared.path_guard:raise ValueError('event_owned_opportunity_price_guard_changed')
    for ref,digest in prepared.raw_guard:
        if hashlib.sha256(archive.read(ref)).hexdigest()!=digest:raise ValueError('event_owned_opportunity_raw_guard_changed')
    if prepared.context is not None:
        from .event_macro_context import collect_event_macro_context_in_session
        if collect_event_macro_context_in_session(session,archive=archive,cutoff_at=model.as_of)!=prepared.context:raise ValueError('event_owned_opportunity_macro_guard_changed')
    parent=session.scalar(select(EventScenarioOutputRecord).where(EventScenarioOutputRecord.event_key==scenario.event_key,EventScenarioOutputRecord.kind==KIND,EventScenarioOutputRecord.scope_key==instrument).order_by(EventScenarioOutputRecord.published_at.desc(),EventScenarioOutputRecord.output_id).limit(1))
    method_hash=_method(model)
    if parent is not None and parent.forecast_id==forecast_id and parent.schema_version==SCHEMA:
        prior=_parse(parent.payload_json)
        if _inputs(scenario,prior.model)==_inputs(scenario,model) and parent.model_hash==method_hash and now<prior.model_valid_until:
            body=prior.model_dump(mode='json')
            expected=_with_parent(_build(scenario,prior.model,published_at=prior.published_at,context=prior.macro_context),parent.revision_parent_ref)
            if (parent.output_id!=prior.snapshot_id or parent.owner_forecast_hash!=scenario.payload_hash or parent.payload_hash!=canonical_hash(body) or parent.payload_json!=_json(body) or prior.payload_hash!=canonical_hash(prior.model_dump(mode='json',exclude={'payload_hash'})) or parent.inputs_hash!=_inputs(scenario,prior.model,prior.revision_parent_ref) or _method(prior.model)!=method_hash or as_utc(parent.published_at)!=prior.published_at or prior.published_at>now or expected!=prior):raise ValueError('event_owned_opportunity_existing_original_invalid')
            return prior
    inputs_hash=_inputs(scenario,model,parent.output_id if parent else None)
    # Native clock after source/price work; caller/model cannot fabricate it.
    value=_with_parent(_build(scenario,model,published_at=utcnow(),context=prepared.context),parent.output_id if parent else None)
    body=value.model_dump(mode='json')
    session.add(EventScenarioOutputRecord(output_id=value.snapshot_id,forecast_id=forecast_id,owner_forecast_hash=scenario.payload_hash,event_key=value.event_key,kind=KIND,scope_key=instrument,inputs_hash=inputs_hash,model_hash=method_hash,schema_version=SCHEMA,payload_json=_json(body),payload_hash=canonical_hash(body),published_at=value.published_at,revision_parent_ref=value.revision_parent_ref));session.flush()
    return value

def freeze_event_owned_opportunity_in_session(session,forecast_id,*,instrument,archive=None):
    """Convenience for isolated controls; normal jobs use two separate scopes."""
    prepared=prepare_event_owned_opportunity_in_session(session,forecast_id,instrument=instrument,archive=archive)
    return commit_prepared_event_owned_opportunity_in_session(session,prepared,archive=archive)

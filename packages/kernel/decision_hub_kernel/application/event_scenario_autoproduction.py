"""Durable calendar-event forecasting; no model call or synthetic Run.

The realtime lane consumes normal authenticated observations. Pure numerical
work runs outside the SQLite writer; short lease/CAS and publication writes
are atomic. Event originals and subsequent revisions are append-only.
"""
import hashlib,json,uuid
from dataclasses import dataclass
from datetime import datetime,timedelta
from pathlib import Path
from sqlalchemy import select,text
from packages.contracts_py.decision_hub_contracts import ResearchInputEvidence
from ..persistence.db import ObservationRecord,SourcePollAttemptRecord,RawCaptureRecord,as_utc,utcnow
from ..persistence.event_scenario_records import EventScenarioJobRecord as Job,EventScenarioForecastRecord as Forecast
from ..ports.market_path import aware_utc,canonical_hash
from ..ports.cpi_pre_event import CpiContextSource
from ..ports.cpi_schedule import CPI_SCHEDULE_SOURCE_ID,CpiSchedulePayload
from ..ports.bls_cpi import BLS_SOURCE_ID,BlsCpiMetricPayload
from ..ports.event_scenario_autoproduction import CpiEventOwnedScenarioSnapshot,EventScenarioStatus,EventScenarioEventView,EventScenarioProducerReport,seal_event_owned_scenario
from ..decision.cpi_event_scenario import compute_cpi_scenario_body
from .cpi_pre_event_context import collect_cpi_pre_event_context_in_session,_archive,_source,_admitted
from .cpi_schedule_original import read_cpi_schedule_original_in_session
from .bls_cpi_original import read_bls_cpi_original_in_session
from .cpi_history_original import read_cpi_history_original_in_session
from .event_scenario_snapshot import _history,_source as _history_source

PREFIX='event-owned-scenario-context:'
SCHEMA='event-owned-scenario-snapshot.v1'
LEASE_SECONDS=180  # Pure local computation lease, not a research/model timeout.
RETRY_SECONDS=60
def _json(value):return json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'))
def _error(exc):
    code=getattr(exc,'error_code',None) or str(exc).splitlines()[0]
    # Operational code only; never persist a native prompt, auth or traceback.
    return code[:128] if code and all(c.isalnum() or c in '_:.-' for c in code) else 'event_scenario_'+type(exc).__name__.lower()
def _model_recipe():
    from ..decision import cpi_event_scenario as algorithm
    from ..ports import cpi_nowcast_history as codec
    return {'algorithm_sha256':hashlib.sha256(Path(algorithm.__file__).read_bytes()).hexdigest(),
        'history_codec_sha256':hashlib.sha256(Path(codec.__file__).read_bytes()).hexdigest(),
        'protocol':'cpi-paired-stage-residual.v1','tolerance_pp':'0.05','joint_prior_half_count':'0.5','slot':'UTC_day'}
def _input_manifest(context,history_source):
    # Identity excludes only the derived current cutoff. Real source-known,
    # original hash, methodology, metric units and reference month remain.
    body=context.model_dump(mode='json');body.pop('cutoff_at')
    return {'context':body,'history_source':history_source.model_dump(mode='json') if history_source else None}
def _row_hash(row):
    body={}
    for column in row.__table__.columns:
        value=getattr(row,column.name)
        body[column.name]=as_utc(value).isoformat() if isinstance(value,datetime) else value
    return canonical_hash(body)
_TABLES={'observation':ObservationRecord,'source_attempt':SourcePollAttemptRecord,'raw_capture':RawCaptureRecord}
def _source_guard(session,context,history_source):
    ids={context.schedule_source.observation_id,*[p.source.observation_id for p in (*context.nowcast,*context.prior)],history_source.observation_id}
    result={}
    def capture_ids(value):
        if isinstance(value,dict):
            if isinstance(value.get('capture_id'),str):yield value['capture_id']
            for nested in value.values():yield from capture_ids(nested)
        elif isinstance(value,list):
            for nested in value:yield from capture_ids(nested)
    for oid in sorted(ids):
        row=session.get(ObservationRecord,oid,populate_existing=True)
        if row is None:raise ValueError('event_scenario_source_observation_missing')
        payload=json.loads(row.text);attempt_id=payload['attempt_id']
        result[('observation',oid)]=_row_hash(row)
        attempt=session.get(SourcePollAttemptRecord,attempt_id,populate_existing=True)
        if attempt is None:raise ValueError('event_scenario_source_attempt_missing')
        result[('source_attempt',attempt_id)]=_row_hash(attempt)
        for cid in capture_ids(payload):
            capture=session.get(RawCaptureRecord,cid,populate_existing=True)
            if capture is None:raise ValueError('event_scenario_raw_capture_missing')
            result[('raw_capture',cid)]=_row_hash(capture)
    return tuple((table,key,value) for (table,key),value in sorted(result.items()))
def _validate_guard(session,guard):
    for table,key,expected in guard:
        cls=_TABLES.get(table);row=session.get(cls,key,populate_existing=True) if cls else None
        if row is None or _row_hash(row)!=expected:raise ValueError('event_scenario_source_guard_changed')
def _reject_known_actual(session,period,*,archive,cutoff_at):
    if period is None:return
    rows=session.scalars(select(ObservationRecord).where(ObservationRecord.source_id==BLS_SOURCE_ID,ObservationRecord.received_at<=cutoff_at).order_by(ObservationRecord.received_at.desc()))
    for row in rows:
        data=BlsCpiMetricPayload.model_validate_json(row.text)
        if data.reference_period==period and data.actual_value is not None and _admitted(session,data,cutoff_at):
            read_bls_cpi_original_in_session(session,row.observation_id,archive=archive,cutoff_at=cutoff_at)
            raise ValueError('event_scenario_target_actual_already_known')
def _contexts(session,*,archive,cutoff_at):
    events={}
    rows=session.scalars(select(ObservationRecord).where(ObservationRecord.source_id==CPI_SCHEDULE_SOURCE_ID,ObservationRecord.received_at<=cutoff_at,ObservationRecord.scheduled_at>cutoff_at).order_by(ObservationRecord.received_at.desc(),ObservationRecord.observation_id))
    for row in rows:
        value=CpiSchedulePayload.model_validate_json(row.text)
        if value.event_key in events or not _admitted(session,value,cutoff_at):continue
        original=read_cpi_schedule_original_in_session(session,row.observation_id,archive=archive,cutoff_at=cutoff_at)
        events[value.event_key]=original.payload.scheduled_at
    return tuple(collect_cpi_pre_event_context_in_session(session,archive=archive,cutoff_at=cutoff_at,event_key=key) for key in sorted(events,key=lambda k:(events[k],k)))

def read_event_owned_scenario_in_session(session,forecast_id,*,archive=None,cutoff_at):
    from .event_read_scope import _native_read_once
    archive=archive or _archive();cutoff=aware_utc(cutoff_at)
    def archive_guard(value):
        for table,key,_ in value.source_guard:
            if table=='raw_capture':
                capture=session.get(RawCaptureRecord,key,populate_existing=True)
                if capture is None or not capture.body_ref or hashlib.sha256(archive.read(capture.body_ref)).hexdigest()!=capture.body_hash:
                    raise ValueError('event_owned_scenario_raw_original_changed')
    value=_native_read_once(session,archive,('scenario',forecast_id),lambda:_read_event_owned_scenario_in_session(session,forecast_id,archive=archive,cutoff_at=cutoff),validate=archive_guard)
    # Eligibility belongs to each caller's cutoff, even when the same original
    # was authenticated earlier in this physical read snapshot.
    if value.published_at>cutoff:raise ValueError('event_owned_scenario_original_identity_invalid')
    return value

def _read_event_owned_scenario_in_session(session,forecast_id,*,archive,cutoff_at):
    archive=archive or _archive();cutoff=aware_utc(cutoff_at)
    row=session.get(Forecast,forecast_id,populate_existing=True)
    if row is None:raise ValueError('event_owned_scenario_original_missing')
    value=CpiEventOwnedScenarioSnapshot.model_validate_json(row.payload_json);body=value.model_dump(mode='json')
    job=session.get(Job,value.job_id,populate_existing=True)
    if (row.forecast_id!=value.forecast_id or row.forecast_id!=forecast_id or row.job_id!=value.job_id or row.schema_version!=SCHEMA
        or row.payload_json!=_json(body) or row.payload_hash!=canonical_hash(body) or value.payload_hash!=canonical_hash(value.model_dump(mode='json',exclude={'payload_hash'}))
        or (row.event_key,row.reference_period,row.slot_id,row.input_hash,row.model_hash,row.revision_parent_ref)!=(value.event_key,value.reference_period,value.slot_id,value.input_hash,value.model_hash,value.revision_parent_ref)
        or (as_utc(row.as_of),as_utc(row.published_at),as_utc(row.scheduled_at))!=(value.as_of,value.published_at,value.scheduled_at) or value.published_at>cutoff
        or job is None or job.job_kind!='forecast' or job.scope_key!='global' or job.state!='published' or job.forecast_id!=forecast_id
        or (job.event_key,job.slot_id,job.input_hash,job.model_hash)!=(value.event_key,value.slot_id,value.input_hash,value.model_hash)
        or as_utc(job.completed_at)!=value.published_at):raise ValueError('event_owned_scenario_original_identity_invalid')
    recipe=_model_recipe()
    if value.model_recipe!=recipe or value.model_hash!=canonical_hash(recipe):raise ValueError('event_owned_scenario_model_version_not_loaded')
    context=collect_cpi_pre_event_context_in_session(session,cutoff_at=value.as_of,archive=archive,event_key=value.event_key)
    if context!=value.input_context:raise ValueError('event_owned_scenario_context_selection_changed')
    _reject_known_actual(session,value.reference_period,archive=archive,cutoff_at=value.published_at)
    history=read_cpi_history_original_in_session(session,value.history_source.observation_id,archive=archive,cutoff_at=value.as_of)
    selected=_history(session,archive=archive,cutoff_at=value.as_of)
    if _history_source(session,history)!=value.history_source or selected is None or selected.observation_id!=history.observation_id:raise ValueError('event_owned_scenario_history_selection_changed')
    manifest=_input_manifest(context,value.history_source)
    if value.input_hash!=canonical_hash(manifest) or job.input_manifest_json!=_json(manifest):raise ValueError('event_owned_scenario_input_manifest_changed')
    guard=_source_guard(session,context,value.history_source)
    if guard!=value.source_guard:raise ValueError('event_owned_scenario_source_guard_changed')
    _validate_guard(session,value.source_guard)
    expected=compute_cpi_scenario_body(context,history_source=value.history_source,month_body=history.month_body,year_body=history.year_body)
    expected.update(snapshot_id=value.forecast_id,forecast_id=value.forecast_id,job_id=value.job_id,slot_id=value.slot_id,published_at=value.published_at,input_hash=value.input_hash,model_hash=value.model_hash,model_recipe=recipe,source_guard=guard,revision_parent_ref=value.revision_parent_ref)
    if seal_event_owned_scenario(expected)!=value:raise ValueError('event_owned_scenario_algorithm_or_parameters_changed')
    if value.revision_parent_ref:
        parent=session.get(Forecast,value.revision_parent_ref,populate_existing=True)
        if parent is None or parent.event_key!=value.event_key or as_utc(parent.published_at)>value.as_of:raise ValueError('event_owned_scenario_revision_parent_invalid')
    return value

def read_latest_event_owned_scenario_for_event_in_session(session,event_key,*,archive=None,cutoff_at):
    cutoff=aware_utc(cutoff_at)
    row=session.scalar(select(Forecast).where(Forecast.event_key==event_key,Forecast.published_at<=cutoff).order_by(Forecast.published_at.desc(),Forecast.forecast_id).limit(1))
    return read_event_owned_scenario_in_session(session,row.forecast_id,archive=archive,cutoff_at=cutoff) if row else None

def read_event_scenario_status_in_session(session,event_key,*,cutoff_at):
    cutoff=aware_utc(cutoff_at)
    row=session.scalar(select(Job).where(Job.event_key==event_key,Job.job_kind=='forecast',Job.scope_key=='global',Job.updated_at<=cutoff).order_by(Job.updated_at.desc(),Job.job_id).limit(1))
    if row is None:return None
    forecast=session.get(Forecast,row.forecast_id) if row.forecast_id else None
    return EventScenarioStatus(event_key=event_key,state=row.state,forecast_id=row.forecast_id,forecast_hash=CpiEventOwnedScenarioSnapshot.model_validate_json(forecast.payload_json).payload_hash if forecast else None,
        last_error_code=row.last_error_code,next_attempt_at=as_utc(row.next_attempt_at),attempts=row.attempts,updated_at=as_utc(row.updated_at))

def read_event_scenario_events_in_session(session,*,cutoff_at,limit=12):
    """Keep future and the preceding 30 days of events discoverable after T0."""
    cutoff=aware_utc(cutoff_at)
    if not isinstance(limit,int) or isinstance(limit,bool) or not 1<=limit<=100:raise ValueError('event_scenario_events_limit_invalid')
    rows=session.scalars(select(Job).where(Job.job_kind=='forecast',Job.scope_key=='global',Job.created_at<=cutoff,Job.scheduled_at>=cutoff-timedelta(days=30)).order_by(Job.scheduled_at,Job.updated_at.desc(),Job.job_id))
    result=[];seen=set()
    for row in rows:
        if row.event_key in seen:continue
        seen.add(row.event_key);status=read_event_scenario_status_in_session(session,row.event_key,cutoff_at=cutoff)
        result.append(EventScenarioEventView(event_key=row.event_key,scheduled_at=as_utc(row.scheduled_at),reference_period=row.reference_period,status=status,forecast_id=status.forecast_id if status else None))
        if len(result)>=limit:break
    return tuple(result)

def read_event_scenario_event_in_session(session,event_key,*,cutoff_at):
    """Exact metadata lookup also keeps past published forecasts discoverable."""
    cutoff=aware_utc(cutoff_at)
    row=session.scalar(select(Job).where(Job.event_key==event_key,Job.job_kind=='forecast',Job.scope_key=='global',Job.created_at<=cutoff).order_by(Job.created_at.desc(),Job.job_id).limit(1))
    if row is None:
        row=session.scalar(select(Forecast).where(Forecast.event_key==event_key,Forecast.published_at<=cutoff).order_by(Forecast.published_at.desc(),Forecast.forecast_id).limit(1))
        if row is None:return None
    status=read_event_scenario_status_in_session(session,event_key,cutoff_at=cutoff)
    latest=session.scalar(select(Forecast).where(Forecast.event_key==event_key,Forecast.published_at<=cutoff).order_by(Forecast.published_at.desc(),Forecast.forecast_id).limit(1))
    return EventScenarioEventView(event_key=event_key,scheduled_at=as_utc(row.scheduled_at),reference_period=row.reference_period,status=status,forecast_id=latest.forecast_id if latest else None)

def read_event_scenario_opportunity_status_in_session(session,event_key,instrument,*,cutoff_at):
    cutoff=aware_utc(cutoff_at)
    row=session.scalar(select(Job).where(Job.event_key==event_key,Job.job_kind=='opportunity',Job.scope_key==instrument,Job.updated_at<=cutoff).order_by(Job.updated_at.desc(),Job.job_id).limit(1))
    if row is None:return None
    parent=session.get(Forecast,row.parent_forecast_id) if row.parent_forecast_id else None
    return EventScenarioStatus(event_key=event_key,state=row.state,forecast_id=row.parent_forecast_id,forecast_hash=CpiEventOwnedScenarioSnapshot.model_validate_json(parent.payload_json).payload_hash if parent else None,last_error_code=row.last_error_code,next_attempt_at=as_utc(row.next_attempt_at),attempts=row.attempts,updated_at=as_utc(row.updated_at))

def select_event_owned_scenario_for_context_in_session(session,*,archive=None,cutoff_at):
    archive=archive or _archive();cutoff=aware_utc(cutoff_at)
    context=collect_cpi_pre_event_context_in_session(session,archive=archive,cutoff_at=cutoff)
    if context.event_key is None:return None
    value=read_latest_event_owned_scenario_for_event_in_session(session,context.event_key,archive=archive,cutoff_at=cutoff)
    if value is None:return None
    if cutoff>=value.scheduled_at:raise ValueError('event_owned_scenario_Task_freeze_at_or_after_T0')
    _reject_known_actual(session,value.reference_period,archive=archive,cutoff_at=cutoff)
    return value

def _evidence(value):
    summary={'schema_version':'event-owned-scenario-context.v1','forecast_ref':value.forecast_id,'forecast_hash':value.payload_hash,'event_key':value.event_key,'reference_period':value.reference_period,
        'T0':value.scheduled_at.isoformat(),'as_of':value.as_of.isoformat(),'published_at':value.published_at.isoformat(),'model_hash':value.model_hash,'status':value.probability_status,'estimation_basis':value.estimation_basis,
        'baseline_kind':value.baseline_kind,'target_definition':value.target_definition,'headline_baseline':str(value.headline_baseline),'core_baseline':str(value.core_baseline),'tolerance_pp':str(value.tolerance_pp),
        'history_event_pairs':value.sample_count,'joint_scenarios':[[s.scenario_id,str(s.event_result_probability),s.matched_historical_events] for s in value.scenarios],
        'asset_reaction_probability':None,'strategy_net_profit_probability':None,'financial_qualified':False,'meaning':'事前联合通胀情景相对上月；不是市场共识惊喜、资产涨跌或策略胜率。原件reader重新认证全部来源和计算。'}
    identity=PREFIX+value.forecast_id
    return ResearchInputEvidence(evidence_id=identity,source_id=identity,kind='document',authority='unverified',source_url=None,published_at=value.published_at,observed_at=value.as_of,received_at=value.published_at,content_hash=canonical_hash(summary),excerpt=_json(summary))

def event_owned_scenario_context_inputs_in_session(session,*,cutoff_at,archive=None):
    value=select_event_owned_scenario_for_context_in_session(session,archive=archive,cutoff_at=cutoff_at)
    return (_evidence(value),) if value else ()

def validate_event_owned_scenario_context_in_session(session,inputs,*,cutoff_at,archive=None):
    values=[v for v in inputs if v.source_id.startswith(PREFIX)]
    if not values:return None
    if len(values)!=1:raise ValueError('event_owned_scenario_Task_context_multiple')
    actual=ResearchInputEvidence.model_validate_json(values[0].model_dump_json())
    summary=json.loads(actual.excerpt)
    value=read_event_owned_scenario_in_session(session,summary.get('forecast_ref'),archive=archive,cutoff_at=cutoff_at)
    cutoff=aware_utc(cutoff_at)
    if cutoff>=value.scheduled_at:raise ValueError('event_owned_scenario_Task_freeze_at_or_after_T0')
    _reject_known_actual(session,value.reference_period,archive=archive or _archive(),cutoff_at=cutoff)
    if actual!=_evidence(value):raise ValueError('event_owned_scenario_Task_context_changed_or_partial')
    # The Task preserves the already-published version, not a newer revision
    # published after this Task's actual freeze.
    selected=select_event_owned_scenario_for_context_in_session(session,archive=archive,cutoff_at=cutoff)
    if selected is None or selected.forecast_id!=value.forecast_id:raise ValueError('event_owned_scenario_Task_selection_crosswired')
    return value

@dataclass(frozen=True)
class TaskEventScenarioOriginal:
    run_id:str
    task_request_hash:str
    forecast_id:str
    forecast_hash:str
    forecast:CpiEventOwnedScenarioSnapshot

def read_task_event_scenario_in_session(session,run_id,*,archive=None,cutoff_at):
    from .research_binding import FrozenResearchBundle,read_research_bundle_in_session
    bundle=read_research_bundle_in_session(session,run_id,assessed_at=cutoff_at)
    if not isinstance(bundle,FrozenResearchBundle):raise ValueError('event_owned_scenario_normal_Task_missing')
    value=validate_event_owned_scenario_context_in_session(session,bundle.request.input_evidence,cutoff_at=bundle.request.context_manifest.created_at,archive=archive)
    if value is None:raise ValueError('event_owned_scenario_Task_not_bound')
    return TaskEventScenarioOriginal(run_id,bundle.task.request_hash,value.forecast_id,value.payload_hash,value)

class EventScenarioAutoproducer:
    def __init__(self,database,*,archive=None,owner=None):
        self.database=database;self.archive=archive or _archive();self.owner=owner or 'event-forecaster:'+uuid.uuid4().hex
        from .event_opportunity_reuse import OpportunityReuseWitnesses
        self._opportunity_reuse=OpportunityReuseWitnesses()
    def _write(self):
        return self.database.session()
    def _opportunity(self,forecast_id,instrument):
        # Separate durable progress: a missing asset-price source must not
        # revoke a valid numerical event forecast or become silent success.
        recipe={'hook':'event-owned-opportunity.v1'};model_hash=canonical_hash(recipe);now=utcnow()
        # Full native read once per process/changed inputs; warm reuse still
        # checks fresh originals and finite release windows outside the writer.
        with self.database.session() as session:
            reusable=self._opportunity_reuse.reusable(session,forecast_id,instrument,archive=self.archive,cutoff_at=now)
        with self._write() as session:
            session.execute(text('BEGIN IMMEDIATE'));forecast=session.get(Forecast,forecast_id,populate_existing=True)
            if forecast is None:raise ValueError('event_owned_opportunity_owner_missing')
            value=CpiEventOwnedScenarioSnapshot.model_validate_json(forecast.payload_json)
            if now>=value.scheduled_at:return None
            manifest={'forecast_id':value.forecast_id,'forecast_hash':value.payload_hash};input_hash=canonical_hash(manifest)
            identity=canonical_hash([value.event_key,value.slot_id,'opportunity',instrument,input_hash,model_hash]);jid='event-job:'+identity
            job=session.get(Job,jid,populate_existing=True)
            if job is None:
                job=Job(job_id=jid,event_key=value.event_key,scheduled_at=value.scheduled_at,reference_period=value.reference_period,slot_id=value.slot_id,job_kind='opportunity',scope_key=instrument,parent_forecast_id=forecast_id,input_hash=input_hash,model_hash=model_hash,input_manifest_json=_json(manifest),state='pending',attempts=0,lease_generation=0,created_at=now,updated_at=now)
                session.add(job);session.flush()
            if job.state=='running' and as_utc(job.lease_expires_at)>now:return None
            if reusable is not None:
                if job.state!='published':
                    job.state='published';job.last_error_code=None;job.forecast_id=forecast_id
                    job.updated_at=now;job.completed_at=now;job.lease_owner=None;job.lease_expires_at=None
                    job.next_attempt_at=reusable.model_valid_until
                return None
            if job.state in ('failed','waiting_inputs') and as_utc(job.next_attempt_at) and as_utc(job.next_attempt_at)>now:return None
            job.state='running';job.attempts+=1;job.lease_generation+=1;job.lease_owner=self.owner;job.lease_expires_at=now+timedelta(seconds=LEASE_SECONDS);job.updated_at=now;generation=job.lease_generation
        prepared=None
        try:
            from .event_owned_opportunity import prepare_event_owned_opportunity_in_session,commit_prepared_event_owned_opportunity_in_session
            from .event_opportunity_reuse import begin_opportunity_read_snapshot_in_session
            # Peer service authenticates all asset originals before its actual
            # publication. No caller can inject a fake publication timestamp.
            with self.database.session() as session:
                begin_opportunity_read_snapshot_in_session(session)
                prepared=prepare_event_owned_opportunity_in_session(session,forecast_id,instrument=instrument,archive=self.archive)
                self._opportunity_reuse.prepared(session,prepared,archive=self.archive)
            with self._write() as session:
                session.execute(text('BEGIN IMMEDIATE'));job=session.get(Job,jid,populate_existing=True);commit_clock=utcnow()
                if job is None or job.state!='running' or job.lease_owner!=self.owner or job.lease_generation!=generation or as_utc(job.lease_expires_at)<=commit_clock:raise ValueError('event_owned_opportunity_lease_lost')
                output=commit_prepared_event_owned_opportunity_in_session(session,prepared,archive=self.archive)
                if output is None:raise ValueError('event_owned_opportunity_original_not_produced')
                witness=self._opportunity_reuse.committed(session,prepared,output,archive=self.archive)
                # Output publication and operational success share one commit.
                now=utcnow();job.state='published';job.last_error_code=None;job.updated_at=now
                job.completed_at=now;job.forecast_id=forecast_id;job.lease_owner=None;job.lease_expires_at=None
                job.next_attempt_at=output.model_valid_until
            self._opportunity_reuse.remember(witness)
            return None
        except Exception as exc:
            code=_error(exc);state='waiting_inputs' if code.endswith('_missing') or code.endswith('_unavailable') else 'failed';output=None
        finally:
            if prepared is not None:self._opportunity_reuse.discard(prepared)
        with self._write() as session:
            session.execute(text('BEGIN IMMEDIATE'));job=session.get(Job,jid,populate_existing=True)
            if job and job.state=='running' and job.lease_owner==self.owner and job.lease_generation==generation:
                now=utcnow();job.state=state;job.last_error_code=code;job.updated_at=now;job.completed_at=now if state=='published' else None;job.forecast_id=forecast_id;job.lease_owner=None;job.lease_expires_at=None;job.next_attempt_at=now+timedelta(seconds=RETRY_SECONDS)
        return code
    def tick(self):
        cutoff=utcnow();recipe=_model_recipe();model_hash=canonical_hash(recipe);registered=waiting=failed=0;published=[];active=[];errors=[]
        with self._write() as session:
            session.execute(text('BEGIN IMMEDIATE'))
            for expired in session.scalars(select(Job).where(Job.scheduled_at<=cutoff,Job.state.in_(('pending','running','failed','waiting_inputs')))):
                expired.state='missed';expired.last_error_code='event_scenario_publication_window_missed';expired.updated_at=cutoff;expired.completed_at=cutoff;expired.lease_owner=None;expired.lease_expires_at=None;expired.next_attempt_at=None
        with self.database.session() as session:
            contexts=_contexts(session,archive=self.archive,cutoff_at=cutoff)
            history=_history(session,archive=self.archive,cutoff_at=cutoff) if any(len(c.nowcast)==len(c.prior)==4 for c in contexts) else None
            hs=_history_source(session,history) if history else None
        if not contexts:return EventScenarioProducerReport(errors=('no_authenticated_upcoming_cpi_schedule',))
        for context in contexts:
            manifest=_input_manifest(context,hs);input_hash=canonical_hash(manifest);slot=cutoff.date().isoformat()
            identity=canonical_hash([context.event_key,slot,'forecast','global',input_hash,model_hash]);job_id='event-job:'+identity
            missing=[v for v in context.incomplete_reasons if v!='market_survey_consensus_unavailable']
            if hs is None:missing.append('authenticated_nowcast_residual_history_unavailable')
            claimed=False;generation=None
            with self._write() as session:
                session.execute(text('BEGIN IMMEDIATE'))
                row=session.get(Job,job_id,populate_existing=True)
                if row is None:
                    row=Job(job_id=job_id,event_key=context.event_key,scheduled_at=context.scheduled_at,reference_period=context.reference_period,slot_id=slot,job_kind='forecast',scope_key='global',input_hash=input_hash,model_hash=model_hash,input_manifest_json=_json(manifest),state='waiting_inputs' if missing else 'pending',attempts=0,lease_generation=0,created_at=cutoff,updated_at=cutoff,last_error_code=missing[0] if missing else None)
                    session.add(row);session.flush();registered+=1
                if missing:waiting+=1;continue
                if row.state=='published':active.append(row.forecast_id);continue
                now=utcnow()
                if row.state=='missed' or (as_utc(row.next_attempt_at) and as_utc(row.next_attempt_at)>now) or (row.state=='running' and as_utc(row.lease_expires_at)>now):continue
                row.state='running';row.attempts+=1;row.lease_generation+=1;row.lease_owner=self.owner;row.lease_expires_at=now+timedelta(seconds=LEASE_SECONDS);row.updated_at=now;row.last_error_code=None;row.next_attempt_at=None;generation=row.lease_generation;claimed=True
            if not claimed:continue
            try:
                # All original readers, archive I/O and numerical work are
                # outside the writer lease transaction.
                with self.database.session() as session:
                    _reject_known_actual(session,context.reference_period,archive=self.archive,cutoff_at=cutoff)
                    fresh=collect_cpi_pre_event_context_in_session(session,archive=self.archive,cutoff_at=cutoff,event_key=context.event_key)
                    current_history=_history(session,archive=self.archive,cutoff_at=cutoff)
                    current_hs=_history_source(session,current_history) if current_history else None
                    if fresh!=context or current_hs!=hs:raise ValueError('event_scenario_claimed_inputs_changed')
                    guard=_source_guard(session,context,hs)
                    previous=session.scalar(select(Forecast).where(Forecast.event_key==context.event_key,Forecast.published_at<=cutoff).order_by(Forecast.published_at.desc(),Forecast.forecast_id).limit(1))
                    parent=previous.forecast_id if previous else None
                body=compute_cpi_scenario_body(context,history_source=hs,month_body=current_history.month_body,year_body=current_history.year_body)
                with self._write() as session:
                    session.execute(text('BEGIN IMMEDIATE'));row=session.get(Job,job_id,populate_existing=True);now=utcnow()
                    if row is None or row.state!='running' or row.lease_owner!=self.owner or row.lease_generation!=generation or as_utc(row.lease_expires_at)<=now:raise ValueError('event_scenario_lease_lost')
                    _validate_guard(session,guard)
                    _reject_known_actual(session,context.reference_period,archive=self.archive,cutoff_at=now)
                    publication=utcnow()  # After actual computation and guards.
                    if publication>=context.scheduled_at:raise ValueError('event_scenario_publication_window_missed')
                    fid='event-forecast:'+identity
                    body.update(snapshot_id=fid,forecast_id=fid,job_id=job_id,slot_id=slot,published_at=publication,input_hash=input_hash,model_hash=model_hash,model_recipe=recipe,source_guard=guard,revision_parent_ref=parent)
                    value=seal_event_owned_scenario(body);payload=value.model_dump(mode='json')
                    session.add(Forecast(forecast_id=fid,job_id=job_id,event_key=value.event_key,scheduled_at=value.scheduled_at,reference_period=value.reference_period,slot_id=slot,input_hash=input_hash,model_hash=model_hash,as_of=cutoff,published_at=publication,revision_parent_ref=parent,schema_version=SCHEMA,payload_json=_json(payload),payload_hash=canonical_hash(payload)))
                    row.state='published';row.forecast_id=fid;row.completed_at=publication;row.updated_at=publication;row.lease_owner=None;row.lease_expires_at=None;row.last_error_code=None;session.flush()
                published.append(fid);active.append(fid)
            except Exception as exc:
                code=_error(exc);errors.append(code);failed+=1
                with self._write() as session:
                    session.execute(text('BEGIN IMMEDIATE'));row=session.get(Job,job_id,populate_existing=True)
                    if row and row.state=='running' and row.lease_owner==self.owner and row.lease_generation==generation:
                        now=utcnow();row.state='missed' if code=='event_scenario_publication_window_missed' or now>=as_utc(row.scheduled_at) else 'failed';row.last_error_code=code;row.updated_at=now;row.next_attempt_at=now+timedelta(seconds=RETRY_SECONDS) if row.state=='failed' else None;row.lease_owner=None;row.lease_expires_at=None
        # Numeric publication has COMMITed. Opportunity original readers may
        # then append asset-specific causal plans without inventing a Run.
        for fid in dict.fromkeys(active):
            for instrument in ('BTC-USDT','ETH-USDT'):
                try:
                    code=self._opportunity(fid,instrument)
                    if code:errors.append('opportunity:'+instrument+':'+code)
                except Exception as exc:
                    errors.append('opportunity:'+instrument+':'+_error(exc))
        return EventScenarioProducerReport(registered=registered,waiting_inputs=waiting,published_forecast_ids=tuple(published),active_forecast_ids=tuple(dict.fromkeys(active)),failed=failed,errors=tuple(errors))

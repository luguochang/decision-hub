"""First immutable event forecast; actual source clocks and algorithm reread."""
import json
from sqlalchemy import select,func
from packages.contracts_py.decision_hub_contracts import ResearchInputEvidence
from ..persistence.db import ObservationRecord,SourcePollAttemptRecord,RunRecord,RunRoleOutputRecord,as_utc,utcnow
from ..ports.market_path import aware_utc,canonical_hash
from ..ports.cpi_pre_event import CpiContextSource
from ..ports.cpi_nowcast_history import SOURCE,CpiHistoryPayload
from ..ports.event_scenario import CpiEventScenarioSnapshot
from ..decision.cpi_event_scenario import build_cpi_event_scenario,compute_cpi_scenario_body
from ..ports.event_scenario import seal_cpi_scenario
from .cpi_history_original import read_cpi_history_original_in_session
from .cpi_pre_event_context import collect_cpi_pre_event_context_in_session,_archive

ROLE='event_scenario_forecast.cpi';SCHEMA='event-scenario-snapshot.v1';PREFIX='event-scenario-context:'
def _json(value):return json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'))
def _source(session,original):
    row=session.get(ObservationRecord,original.observation_id,populate_existing=True)
    return CpiContextSource(observation_id=row.observation_id,content_hash=row.content_hash,known_at=original.known_at)
def _history(session,*,archive,cutoff_at):
    cutoff=aware_utc(cutoff_at)
    rows=session.scalars(select(ObservationRecord).where(ObservationRecord.source_id==SOURCE,ObservationRecord.received_at<=cutoff).order_by(ObservationRecord.received_at.desc(),ObservationRecord.observation_id).execution_options(yield_per=32))
    for row in rows:
        payload=CpiHistoryPayload.model_validate_json(row.text);attempt=session.get(SourcePollAttemptRecord,payload.attempt_id,populate_existing=True)
        if attempt is None or attempt.status!='succeeded' or as_utc(attempt.completed_at) is None or as_utc(attempt.completed_at)>cutoff:continue
        return read_cpi_history_original_in_session(session,row.observation_id,archive=archive,cutoff_at=cutoff)
    return None
def _row(session,run_id):
    return session.scalar(select(RunRoleOutputRecord).where(RunRoleOutputRecord.run_id==run_id,RunRoleOutputRecord.role==ROLE).execution_options(populate_existing=True))

def read_latest_event_scenario_for_event_in_session(session,event_key,*,archive=None,cutoff_at):
    """API event lookup still invokes the full original reader, not SHA-only.

    Invalid latest data raises visibly; it is not silently replaced by an
    earlier seemingly successful forecast. No model, write or Run creation.
    """
    cutoff=aware_utc(cutoff_at)
    row=session.scalar(select(RunRoleOutputRecord).where(RunRoleOutputRecord.role==ROLE,RunRoleOutputRecord.schema_version==SCHEMA,
        RunRoleOutputRecord.created_at<=cutoff,func.json_extract(RunRoleOutputRecord.payload_json,'$.event_key')==event_key).order_by(RunRoleOutputRecord.created_at.desc(),RunRoleOutputRecord.output_id).limit(1))
    if row is None:return None
    return read_event_scenario_snapshot_in_session(session,row.run_id,archive=archive,cutoff_at=cutoff)

def read_event_scenario_snapshot_in_session(session,run_id,*,archive=None,cutoff_at):
    archive=archive or _archive();cutoff=aware_utc(cutoff_at);row=_row(session,run_id)
    if row is None:raise ValueError('event_scenario_snapshot_not_frozen')
    snapshot=CpiEventScenarioSnapshot.model_validate_json(row.payload_json)
    body=snapshot.model_dump(mode='json')
    if (row.run_id!=snapshot.run_id or snapshot.run_id!=run_id or row.role!=ROLE or row.schema_version!=SCHEMA or row.output_id!=snapshot.snapshot_id
        or row.payload_json!=_json(body) or row.payload_hash!=canonical_hash(body) or snapshot.payload_hash!=canonical_hash(snapshot.model_dump(mode='json',exclude={'payload_hash'}))
        or as_utc(row.created_at)!=snapshot.published_at or snapshot.published_at>cutoff or session.get(RunRecord,run_id) is None):raise ValueError('event_scenario_frozen_original_identity_invalid')
    context=collect_cpi_pre_event_context_in_session(session,cutoff_at=snapshot.as_of,archive=archive)
    if context!=snapshot.input_context:raise ValueError('event_scenario_input_context_changed')
    history=read_cpi_history_original_in_session(session,snapshot.history_source.observation_id,archive=archive,cutoff_at=snapshot.as_of)
    if _source(session,history)!=snapshot.history_source:raise ValueError('event_scenario_history_original_crosswired')
    selected=_history(session,archive=archive,cutoff_at=snapshot.as_of)
    if selected is None or selected.observation_id!=history.observation_id:raise ValueError('event_scenario_history_selection_changed')
    expected=build_cpi_event_scenario(context,run_id=run_id,history_source=snapshot.history_source,month_body=history.month_body,year_body=history.year_body,published_at=snapshot.published_at)
    if expected!=snapshot:raise ValueError('event_scenario_algorithm_or_parameters_changed')
    return snapshot

def freeze_event_scenario_snapshot_in_session(session,run_id,*,cutoff_at,archive=None):
    """Caller is the normal Task preparer, not a model or request DTO."""
    archive=archive or _archive();cutoff=aware_utc(cutoff_at)
    if session.get(RunRecord,run_id) is None:raise ValueError('event_scenario_normal_run_missing')
    existing=_row(session,run_id)
    if existing is not None:
        value=read_event_scenario_snapshot_in_session(session,run_id,archive=archive,cutoff_at=utcnow())
        if value.as_of!=cutoff:raise ValueError('event_scenario_first_freeze_conflict')
        return value
    context=collect_cpi_pre_event_context_in_session(session,cutoff_at=cutoff,archive=archive)
    if context.scheduled_at is None or context.reference_period is None or len(context.nowcast)!=4 or len(context.prior)!=4:return None
    history=_history(session,archive=archive,cutoff_at=cutoff)
    if history is None:return None
    calculated=compute_cpi_scenario_body(context,history_source=_source(session,history),month_body=history.month_body,year_body=history.year_body)
    calculated.update(snapshot_id="event-scenario:"+canonical_hash([run_id,context.event_key]),run_id=run_id,published_at=utcnow())
    snapshot=seal_cpi_scenario(calculated)
    body=snapshot.model_dump(mode='json')
    session.add(RunRoleOutputRecord(output_id=snapshot.snapshot_id,run_id=run_id,role=ROLE,schema_version=SCHEMA,payload_json=_json(body),payload_hash=canonical_hash(body),created_at=snapshot.published_at))
    session.flush()
    return snapshot

def _evidence(snapshot):
    summary={'schema_version':'event-scenario-context.v1','snapshot_ref':snapshot.snapshot_id,'snapshot_hash':snapshot.payload_hash,'event_key':snapshot.event_key,'reference_period':snapshot.reference_period,
        'T0':snapshot.scheduled_at.isoformat(),'as_of':snapshot.as_of.isoformat(),'published_at':snapshot.published_at.isoformat(),'model':snapshot.model_version,'status':snapshot.probability_status,'estimation_basis':snapshot.estimation_basis,
        'baseline_kind':snapshot.baseline_kind,'target_definition':snapshot.target_definition,'headline_baseline':str(snapshot.headline_baseline),'core_baseline':str(snapshot.core_baseline),'tolerance_pp':str(snapshot.tolerance_pp),
        'history_event_pairs':snapshot.sample_count,'joint_scenarios':[[s.scenario_id,str(s.event_result_probability),s.matched_historical_events] for s in snapshot.scenarios],
        'asset_reaction_probability':None,'strategy_net_profit_probability':None,'financial_qualified':False,
        'meaning':'联合概率是相对上月的连续通胀结果；不是市场惊喜、资产涨跌或策略胜率。全文与所有输入由snapshot_ref的原件reader认证。'}
    raw=_json(summary);identity=PREFIX+snapshot.snapshot_id
    return ResearchInputEvidence(evidence_id=identity,source_id=identity,kind='document',authority='unverified',source_url=None,published_at=snapshot.published_at,observed_at=snapshot.as_of,received_at=snapshot.published_at,content_hash=canonical_hash(summary),excerpt=raw)

def event_scenario_context_inputs_in_session(session,run_id,*,cutoff_at,archive=None):
    snapshot=freeze_event_scenario_snapshot_in_session(session,run_id,cutoff_at=cutoff_at,archive=archive)
    return (_evidence(snapshot),) if snapshot is not None else ()

def validate_event_scenario_context_in_session(session,run_id,inputs,*,cutoff_at,archive=None):
    values=[i for i in inputs if i.source_id.startswith(PREFIX)]
    if not values:return
    if len(values)!=1:raise ValueError('event_scenario_context_multiple_or_crosswired')
    snapshot=read_event_scenario_snapshot_in_session(session,run_id,cutoff_at=cutoff_at,archive=archive)
    if aware_utc(cutoff_at)>=snapshot.scheduled_at:raise ValueError('event_scenario_Task_freeze_at_or_after_T0')
    # Detach any independently generated contract DTO to the canonical class.
    actual=ResearchInputEvidence.model_validate_json(values[0].model_dump_json())
    if actual!=_evidence(snapshot):raise ValueError('event_scenario_context_changed_or_partial')

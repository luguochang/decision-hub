"""Native immutable event preplans and full reread for API and Task consumers."""
import json
from sqlalchemy import select,func
from packages.contracts_py.decision_hub_contracts import ResearchInputEvidence,RunStatus
from ..persistence.db import RunRoleOutputRecord,RunRecord,as_utc,utcnow
from ..ports.market_path import aware_utc,canonical_hash
from ..ports.event_opportunity import EventOpportunitySnapshot
from ..decision.event_opportunity import build_event_opportunity
from .event_scenario_snapshot import read_event_scenario_snapshot_in_session
from .event_response_path import collect_event_response_model_in_session
from .cpi_pre_event_context import _archive
from .research_run_scope import read_run_spot_admission_in_session

PREFIX='event-opportunity-context:';SCHEMA='event-opportunity-snapshot.v1'
def _json(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def _role(instrument):
    if instrument not in {'BTC-USDT','ETH-USDT'}:raise ValueError('event_opportunity_instrument_unsupported')
    return 'event_opportunity_forecast.cpi.'+instrument
def _row(session,run_id,instrument):
    return session.scalar(select(RunRoleOutputRecord).where(RunRoleOutputRecord.run_id==run_id,RunRoleOutputRecord.role==_role(instrument)).execution_options(populate_existing=True))
def _scope(session,run_id,instrument):
    run=session.get(RunRecord,run_id,populate_existing=True)
    if run is None:raise ValueError('event_opportunity_run_missing')
    binding=read_run_spot_admission_in_session(session,run_id)
    if binding is None or binding.scope.instrument!=instrument:raise ValueError('event_opportunity_native_spot_scope_mismatch')

def read_event_opportunity_snapshot_in_session(session,run_id,*,instrument,archive=None,cutoff_at):
    archive=archive or _archive();cutoff=aware_utc(cutoff_at);_scope(session,run_id,instrument);row=_row(session,run_id,instrument)
    if row is None:raise ValueError('event_opportunity_snapshot_not_frozen')
    value=EventOpportunitySnapshot.model_validate_json(row.payload_json);body=value.model_dump(mode='json')
    if (value.run_id!=run_id or value.instrument!=instrument or row.output_id!=value.snapshot_id or row.schema_version!=SCHEMA or row.payload_json!=_json(body) or row.payload_hash!=canonical_hash(body) or value.payload_hash!=canonical_hash(value.model_dump(mode='json',exclude={'payload_hash'})) or as_utc(row.created_at)!=value.published_at or value.published_at>cutoff):raise ValueError('event_opportunity_original_identity_invalid')
    if value.task_mechanism_reviews[0].run_id!=run_id:raise ValueError('event_opportunity_Task_owner_mismatch')
    # New event-owned original and legacy Run-owned original remain separate.
    legacy=session.scalar(select(RunRoleOutputRecord).where(RunRoleOutputRecord.run_id==run_id,RunRoleOutputRecord.role=='event_scenario_forecast.cpi'))
    if legacy is not None:scenario=read_event_scenario_snapshot_in_session(session,run_id,archive=archive,cutoff_at=cutoff)
    else:
        from .event_scenario_autoproduction import read_event_owned_scenario_in_session
        scenario=read_event_owned_scenario_in_session(session,value.scenario_snapshot_ref,archive=archive,cutoff_at=value.as_of)
    if scenario.snapshot_id!=value.scenario_snapshot_ref or scenario.payload_hash!=value.scenario_snapshot_hash:raise ValueError('event_opportunity_scenario_original_crosswired')
    model=collect_event_response_model_in_session(session,instrument,archive=archive,cutoff_at=value.as_of)
    expected=build_event_opportunity(scenario,model,published_at=value.published_at,run_id=run_id)
    if expected!=value:raise ValueError('event_opportunity_model_or_rules_changed')
    return value

def read_latest_event_opportunity_for_event_in_session(session,event_key,instrument,*,archive=None,cutoff_at):
    cutoff=aware_utc(cutoff_at)
    row=session.scalar(select(RunRoleOutputRecord).where(RunRoleOutputRecord.role==_role(instrument),RunRoleOutputRecord.schema_version==SCHEMA,RunRoleOutputRecord.created_at<=cutoff,func.json_extract(RunRoleOutputRecord.payload_json,'$.event_key')==event_key).order_by(RunRoleOutputRecord.created_at.desc(),RunRoleOutputRecord.output_id).limit(1))
    return read_event_opportunity_snapshot_in_session(session,row.run_id,instrument=instrument,archive=archive,cutoff_at=cutoff) if row else None

def freeze_event_opportunity_snapshot_in_session(session,run_id,*,instrument,cutoff_at,archive=None):
    archive=archive or _archive();cutoff=aware_utc(cutoff_at);_scope(session,run_id,instrument);existing=_row(session,run_id,instrument)
    if existing:
        return read_event_opportunity_snapshot_in_session(session,run_id,instrument=instrument,archive=archive,cutoff_at=cutoff)
    run=session.get(RunRecord,run_id,populate_existing=True)
    if run.status not in {RunStatus.admitted.value,RunStatus.running.value}:raise ValueError('event_opportunity_first_freeze_Run_not_active')
    scenario_row=session.scalar(select(RunRoleOutputRecord).where(RunRoleOutputRecord.run_id==run_id,RunRoleOutputRecord.role=='event_scenario_forecast.cpi'))
    if scenario_row is None:
        from .event_scenario_autoproduction import select_event_owned_scenario_for_context_in_session
        scenario=select_event_owned_scenario_for_context_in_session(session,archive=archive,cutoff_at=cutoff)
        if scenario is None:return None
        model_cutoff=cutoff
    else:
        scenario=read_event_scenario_snapshot_in_session(session,run_id,archive=archive,cutoff_at=utcnow())
        model_cutoff=scenario.as_of
    # The source/model as-of is the original authenticated scenario cutoff.
    # Task/publication clocks can advance; the research information set cannot.
    model=collect_event_response_model_in_session(session,instrument,archive=archive,cutoff_at=model_cutoff)
    value=build_event_opportunity(scenario,model,published_at=utcnow(),run_id=run_id);body=value.model_dump(mode='json')
    session.add(RunRoleOutputRecord(output_id=value.snapshot_id,run_id=run_id,role=_role(instrument),schema_version=SCHEMA,payload_json=_json(body),payload_hash=canonical_hash(body),created_at=value.published_at));session.flush()
    return value

def event_macro_context_user_view(value):
    """Pure projection of the saved native context; no new data or clocks."""
    context=getattr(value,'macro_context',None)
    if context is None:return None
    from ..ports.macro_daily import source_url
    return dict(schema_version='event-macro-context-view.v1',as_of=context.as_of.isoformat(),state=context.state,
        series=[dict(series_id=s.series_id,label=s.label,unit=s.unit,value=str(s.value),
            observation_date=s.observation_date.isoformat(),known_at=s.known_at.isoformat(),
            change_5_observations=str(s.change_5_observations),source_url=source_url(s.series_id),
            observation_ref=s.observation_ref,content_hash=s.content_hash) for s in context.series],gaps=context.gaps)

def event_opportunity_user_view(value,*,evaluation_at=None):
    """One original-derived Chinese view. No generation or new authority."""
    evaluated=aware_utc(evaluation_at or utcnow());expired=evaluated>=value.model_valid_until
    return dict(schema_version='event-opportunity-view.v1',snapshot_ref=value.snapshot_id,snapshot_hash=value.payload_hash,event_key=value.event_key,reference_period=value.reference_period,instrument=value.instrument,
        anchor='event_release',T0=value.scheduled_at.isoformat(),as_of=value.as_of.isoformat(),event_probability_as_of=value.event_probability_as_of.isoformat(),market_model_as_of=value.market_model_as_of.isoformat(),model_valid_until=value.model_valid_until.isoformat(),published_at=value.published_at.isoformat(),entry_valid_from=value.entry_valid_from.isoformat(),entry_valid_until=value.entry_valid_until.isoformat(),exit_valid_until=value.exit_valid_until.isoformat(),reestimate_required_at=value.reestimate_required_at.isoformat(),
        title='CPI公布前条件机会预案',proposal_status='预案待公布情景与临近价格确认',baseline='相对上月；不是相对市场共识',evaluated_at=evaluated.isoformat(),model_expired=expired,model_estimate_status='reestimate_due' if expired else 'estimated_uncalibrated',asset_reaction_estimate=value.asset_reaction_estimate.model_dump(mode='json'),strategy_net_profit_estimate=value.strategy_net_profit_estimate.model_dump(mode='json'),strategy_net_profit_given_entry_estimate=value.strategy_net_profit_given_entry_estimate.model_dump(mode='json'),
        price_estimation_status=value.price_estimation_status,missing_scenario_ids=value.missing_scenario_ids,macro_context=event_macro_context_user_view(value),plans=[dict(scenario_id=p.scenario_id,event_result_probability=str(p.event_result_probability),main_chain=[n.description for n in p.causal_chain.nodes],counter_mechanism=p.alternative_mechanism,activation_required=p.activation_required,flat=p.flat_proposal.model_dump(mode='json'),long=p.long_proposal.model_dump(mode='json'),scenario_asset_reaction_estimate=p.scenario_asset_reaction_estimate.model_dump(mode='json'),scenario_strategy_net_profit_estimate=p.scenario_strategy_net_profit_estimate.model_dump(mode='json'),armed=False) for p in value.plans],gaps=value.gaps,financial_qualified=False,action_execution_authorized=False,automatic_monitoring_armed=False)
def _evidence(value):
    # Fixed brief index remains below the existing 4k excerpt cap. Full reader
    # and API use the immutable envelope, never reconstruct from this summary.
    summary=dict(schema_version='event-opportunity-context.v1',snapshot_ref=value.snapshot_id,snapshot_hash=value.payload_hash,event_key=value.event_key,instrument=value.instrument,anchor='event_release',T0=value.scheduled_at.isoformat(),entry_window=[value.entry_valid_from.isoformat(),value.entry_valid_until.isoformat()],exit_valid_until=value.exit_valid_until.isoformat(),reestimate_required_at=value.reestimate_required_at.isoformat(),event_scenarios=[[p.scenario_id,str(p.event_result_probability)] for p in value.plans],asset_estimate=value.asset_reaction_estimate.model_dump(mode='json',exclude={'assumptions'}),net_estimate=value.strategy_net_profit_estimate.model_dump(mode='json',exclude={'assumptions'}),
        conditional_actions='空仓：公布后6h收益>0且UTC完整小时量比>=1.2连续2分钟确认，下一分钟纸面买入；6h收益<-0.5%退出，事件24h+60s硬退出。持多：弱势两分钟确认减半；不推定真实持仓。',status='预案未武装；事件概率不是资产涨跌/策略胜率；prior_month不是市场共识；未覆盖分格不可用；详见原snapshot。',execution_authorized=False)
    text=_json(summary)
    if len(text)>4000:raise ValueError('event_opportunity_context_cannot_be_truncated')
    identity=PREFIX+value.snapshot_id
    return ResearchInputEvidence(evidence_id=identity,source_id=identity,kind='document',authority='unverified',source_url=None,published_at=value.published_at,observed_at=value.as_of,received_at=value.published_at,content_hash=canonical_hash(summary),excerpt=text)
def event_opportunity_context_inputs_in_session(session,run_id,*,instrument,cutoff_at,archive=None):
    value=freeze_event_opportunity_snapshot_in_session(session,run_id,instrument=instrument,cutoff_at=cutoff_at,archive=archive)
    return (_evidence(value),) if value else ()
def validate_event_opportunity_context_in_session(session,run_id,inputs,*,cutoff_at,archive=None):
    values=[i for i in inputs if i.source_id.startswith(PREFIX)]
    if not values:return
    if len(values)!=1:raise ValueError('event_opportunity_context_multiple')
    summary=json.loads(values[0].excerpt);value=read_event_opportunity_snapshot_in_session(session,run_id,instrument=summary['instrument'],archive=archive,cutoff_at=cutoff_at)
    if not session.scalar(select(RunRoleOutputRecord).where(RunRoleOutputRecord.run_id==run_id,RunRoleOutputRecord.role=='event_scenario_forecast.cpi')):
        from .event_scenario_autoproduction import validate_event_owned_scenario_context_in_session
        scenario=validate_event_owned_scenario_context_in_session(session,inputs,cutoff_at=cutoff_at,archive=archive)
        if scenario is None or scenario.forecast_id!=value.scenario_snapshot_ref or scenario.payload_hash!=value.scenario_snapshot_hash:raise ValueError('event_opportunity_Task_scenario_original_crosswired')
    if aware_utc(cutoff_at)>=value.scheduled_at or ResearchInputEvidence.model_validate_json(values[0].model_dump_json())!=_evidence(value):raise ValueError('event_opportunity_context_crosswired_or_partial')

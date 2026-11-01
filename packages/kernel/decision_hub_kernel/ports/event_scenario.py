"""Different probability targets; generic event identity, CPI plugin first."""
from decimal import Decimal,localcontext
from typing import Literal
from pydantic import AwareDatetime,Field,model_validator
from .market_path import FrozenContract,canonical_hash
from .cpi_pre_event import CpiPreEventContextBinding,CpiContextSource

Band=Literal['cooler','near','hotter']
class EventDistributionSpec(FrozenContract):
    event_family:Literal['cpi','fomc']
    partition_kind:Literal['cpi_joint_3x3','fomc_rate_discrete']
    scenario_ids:tuple[str,...]
    probability_target:Literal['event_outcome']='event_outcome'
    @model_validator(mode='after')
    def valid_spec(self):
        if not self.scenario_ids or len(set(self.scenario_ids))!=len(self.scenario_ids):raise ValueError('event_scenario_partition_duplicate_or_empty')
        if (self.event_family=='cpi')!=(self.partition_kind=='cpi_joint_3x3'):raise ValueError('event_scenario_plugin_crosswired')
        if self.event_family=='cpi' and set(self.scenario_ids)!={f'cpi:headline_{h}:core_{c}' for h in ('cooler','near','hotter') for c in ('cooler','near','hotter')}:raise ValueError('cpi_joint_partition_not_exhaustive')
        return self

class EventScenarioOutcome(FrozenContract):
    scenario_id:str
    headline_band:Band|None=None
    core_band:Band|None=None
    rate_change_bp:Decimal|None=None
    event_result_probability:Decimal
    matched_historical_events:int=Field(ge=0)
    asset_reaction_probability:None=None
    strategy_net_profit_probability:None=None
    @model_validator(mode='after')
    def valid_probability(self):
        if not self.event_result_probability.is_finite() or not 0<=self.event_result_probability<=1:raise ValueError('event_scenario_probability_invalid')
        if self.headline_band is not None and self.scenario_id!=f'cpi:headline_{self.headline_band}:core_{self.core_band}':raise ValueError('cpi_scenario_label_crosswired')
        return self

class FrozenEventScenarioBase(FrozenContract):
    """Reusable immutable storage/clock contract; FOMC producer is separate."""
    schema_version:Literal['event-scenario-snapshot.v1']='event-scenario-snapshot.v1'
    snapshot_id:str
    event_key:str
    scheduled_at:AwareDatetime
    as_of:AwareDatetime
    published_at:AwareDatetime
    spec:EventDistributionSpec
    model_version:str
    probability_status:Literal['estimated_uncalibrated']='estimated_uncalibrated'
    scenarios:tuple[EventScenarioOutcome,...]
    assumptions:tuple[str,...]
    financial_qualified:Literal[False]=False
    action_execution_authorized:Literal[False]=False
    payload_hash:str=Field(pattern=r'^[a-f0-9]{64}$')
    @model_validator(mode='after')
    def valid_snapshot(self):
        if not self.as_of<=self.published_at<self.scheduled_at:raise ValueError('event_scenario_not_published_before_T0')
        if set(v.scenario_id for v in self.scenarios)!=set(self.spec.scenario_ids) or len(self.scenarios)!=len(self.spec.scenario_ids):raise ValueError('event_scenario_partition_mismatch')
        with localcontext() as ctx:
            ctx.prec=128
            if sum((v.event_result_probability for v in self.scenarios),Decimal(0))!=1:raise ValueError('event_scenario_probability_mass_not_one')
        return self

class CpiScenarioForecastValueBase(FrozenEventScenarioBase):
    reference_period:str=Field(pattern=r'^20\d{2}-(0[1-9]|1[0-2])$')
    input_context:CpiPreEventContextBinding
    input_context_hash:str=Field(pattern=r'^[a-f0-9]{64}$')
    history_source:CpiContextSource
    model_version:Literal['cpi-paired-stage-residual.v1']='cpi-paired-stage-residual.v1'
    estimation_basis:Literal['historical_comparison_estimate']='historical_comparison_estimate'
    baseline_kind:Literal['prior_month']='prior_month'
    baseline_ref:str
    headline_baseline:Decimal
    core_baseline:Decimal
    tolerance_pp:Decimal
    target_definition:Literal['index_derived_continuous_percent_change']='index_derived_continuous_percent_change'
    rate_rounding_policy:Literal['no_rate_rounding_before_band_assignment']='no_rate_rounding_before_band_assignment'
    nowcast_center_precision:Literal['public_html_2dp_model_point']='public_html_2dp_model_point'
    center_quantization_half_width_pp:Literal['0.005']='0.005'
    stage_month_offset:int=Field(ge=0,le=1)
    stage_day:int=Field(ge=1,le=31)
    sample_count:int=Field(ge=1)
    sample_hash:str=Field(pattern=r'^[a-f0-9]{64}$')
    excluded_periods:tuple[tuple[str,str],...]
    dirichlet_pseudocount:Literal['0.5']='0.5'
    rounding_protocol:Literal['decimal28-last-cell-residual.v1']='decimal28-last-cell-residual.v1'
    joint_metric_order:Literal['headline_mom,core_mom,headline_yoy,core_yoy']='headline_mom,core_mom,headline_yoy,core_yoy'
    marginal_support_quantiles:tuple[tuple[str,Decimal,Decimal,Decimal],...]
    @model_validator(mode='after')
    def valid_cpi(self):
        context=self.input_context
        if (self.spec.event_family!='cpi' or context.content_hash!=self.input_context_hash or self.baseline_ref!=context.content_hash
            or context.event_key!=self.event_key or context.scheduled_at!=self.scheduled_at or context.reference_period!=self.reference_period
            or context.cutoff_at!=self.as_of or len(context.nowcast)!=4 or len(context.prior)!=4
            or not self.tolerance_pp.is_finite() or self.tolerance_pp<=0
            or self.history_source.known_at>self.as_of or self.history_source.known_at>=self.scheduled_at
            or any(p.source.known_at>=self.scheduled_at for p in (*context.nowcast,*context.prior))
            or context.schedule_source is None or context.schedule_source.known_at>=self.scheduled_at):raise ValueError('cpi_scenario_input_identity_or_clock_invalid')
        if sum(v.matched_historical_events for v in self.scenarios)!=self.sample_count:raise ValueError('cpi_scenario_sample_count_mismatch')
        return self

class CpiEventScenarioSnapshot(CpiScenarioForecastValueBase):
    run_id:str

def seal_cpi_scenario(body):
    draft=CpiEventScenarioSnapshot.model_validate(body|{'payload_hash':'0'*64})
    return CpiEventScenarioSnapshot.model_validate(draft.model_dump(mode='json',exclude={'payload_hash'})|{'payload_hash':canonical_hash(draft.model_dump(mode='json',exclude={'payload_hash'}))})

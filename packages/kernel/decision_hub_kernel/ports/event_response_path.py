"""Event-bound price outcomes have their own denominator and target."""
from typing import Literal
from datetime import timedelta
from decimal import Decimal
from pydantic import AwareDatetime,Field,model_validator
from .market_path import FrozenContract

class EventResponseSample(FrozenContract):
    event_key:str
    reference_period:str
    scheduled_at:AwareDatetime
    release_observation_id:str
    release_content_hash:str
    release_known_at:AwareDatetime
    bls_observation_id:str
    bls_content_hash:str
    bls_known_at:AwareDatetime
    headline_actual:Decimal
    headline_prior:Decimal
    core_actual:Decimal
    core_prior:Decimal
    scenario_id:str
    anchor:Literal['event_release']='event_release'
    reaction_start_at:AwareDatetime
    reaction_end_at:AwareDatetime
    reaction_return:Decimal
    strategy_entered:bool
    strategy_entry_at:AwareDatetime|None
    strategy_exit_at:AwareDatetime|None
    strategy_net_return:Decimal
    path_semantic_hash:str
    path_provenance_hash:str
    capture_originals:tuple[tuple[str,str],...]
    @model_validator(mode='after')
    def timing(self):
        if (self.reaction_start_at-self.scheduled_at).total_seconds()!=60 or (self.reaction_end_at-self.reaction_start_at).total_seconds()!=86400:raise ValueError('event_response_anchor_invalid')
        if self.strategy_entered!=(self.strategy_entry_at is not None) or self.strategy_entered!=(self.strategy_exit_at is not None):raise ValueError('event_response_trade_identity_invalid')
        if self.strategy_entered and not self.reaction_start_at<=self.strategy_entry_at<self.scheduled_at+timedelta(hours=6):raise ValueError('event_response_entry_window_invalid')
        if self.strategy_exit_at is not None and not self.strategy_entry_at<self.strategy_exit_at<=self.reaction_end_at:raise ValueError('event_response_exit_window_invalid')
        if not self.strategy_entered and self.strategy_net_return!=0:raise ValueError('event_response_no_trade_not_profit')
        return self

class EventResponseModel(FrozenContract):
    schema_version:Literal['event-response-model.v1']='event-response-model.v1'
    instrument:Literal['BTC-USDT','ETH-USDT']
    scope_id:str
    as_of:AwareDatetime
    model_version:Literal['cpi-price-confirmation-empirical.v1']='cpi-price-confirmation-empirical.v1'
    timing_protocol:Literal['closed_minute_confirmation_plus_60s_next_minute_open.v1']='closed_minute_confirmation_plus_60s_next_minute_open.v1'
    baseline_kind:Literal['prior_month']='prior_month'
    sample_selection:Literal['registered_release_documents_with_complete_native_OKX_1m']='registered_release_documents_with_complete_native_OKX_1m'
    history_vintage:Literal['retrospective_not_historical_PIT']='retrospective_not_historical_PIT'
    samples:tuple[EventResponseSample,...]
    excluded:tuple[str,...]
    fee_fraction_each_side:Decimal
    slippage_fraction_each_side:Decimal
    cost_policy_ref:str
    catalog_hash:str
    payload_hash:str
    @model_validator(mode='after')
    def timing(self):
        if len({s.event_key for s in self.samples})!=len(self.samples):raise ValueError('event_response_duplicate_denominator')
        if self.scope_id!=f'crypto:spot:okx:{self.instrument}:trade:1m':raise ValueError('event_response_scope_invalid')
        if any(s.reaction_end_at>self.as_of or max(s.release_known_at,s.bls_known_at)>self.as_of for s in self.samples):raise ValueError('event_response_unmatured_or_future_known')
        return self

class EventProbabilityEstimate(FrozenContract):
    target:str
    probability_status:Literal['estimated_uncalibrated','unavailable']
    basis:Literal['event_day_climatology','same_prior_month_scenario','conditional_on_entered']
    scenario_conditioned:bool
    value:Decimal|None=Field(ge=0,le=1)
    empirical_fraction:Decimal|None=Field(ge=0,le=1)
    lower_95:Decimal|None=Field(ge=0,le=1)
    upper_95:Decimal|None=Field(ge=0,le=1)
    events:int=Field(ge=0)
    entered:int=Field(ge=0)
    successes:int=Field(ge=0)
    model_ref:str
    model_hash:str
    assumptions:tuple[str,...]
    @model_validator(mode='after')
    def probability(self):
        if self.successes>self.events or self.entered>self.events:raise ValueError('event_estimate_denominator_invalid')
        if (self.events==0)!=(self.probability_status=='unavailable') or (self.value is None)!=(self.events==0):raise ValueError('event_estimate_zero_samples_no_default')
        if self.events and (self.empirical_fraction!=Decimal(self.successes)/self.events or self.value!=(Decimal(self.successes)+Decimal('.5'))/(self.events+1) or not 0<=self.lower_95<=self.upper_95<=1):raise ValueError('event_estimate_formula_invalid')
        return self

# New counterfactual research version; the v1 fields and defaults above stay
# byte-for-byte serializable so existing immutable records still authenticate.
class EventResponseSampleV2(EventResponseSample):
    policy_id:str
    policy_hash:str=Field(pattern=r'^[a-f0-9]{64}$')

class EventResponseModelV2(EventResponseModel):
    schema_version:Literal['event-response-model.v2']='event-response-model.v2'
    model_version:Literal['cpi-scenario-policy-empirical.v2']='cpi-scenario-policy-empirical.v2'
    history_vintage:Literal['current_policy_counterfactual_on_retrospective_prices']='current_policy_counterfactual_on_retrospective_prices'
    samples:tuple[EventResponseSampleV2,...]
    macro_context_hash:str=Field(pattern=r'^[a-f0-9]{64}$')
    policy_catalog_hash:str=Field(pattern=r'^[a-f0-9]{64}$')

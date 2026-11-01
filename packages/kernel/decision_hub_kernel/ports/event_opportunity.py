"""Internal conditional event plans; public Action ABI and rights unchanged."""
from decimal import Decimal
from datetime import timedelta
from typing import Literal
from pydantic import AwareDatetime,model_validator
from packages.contracts_py.decision_hub_contracts.generated.research_signal import ResearchActionCandidateV1,ResearchMechanismReviewV1,ResearchMechanismNodeV1,ResearchMechanismEdgeV1
from .market_path import FrozenContract,canonical_hash
from .event_response_path import EventResponseModel,EventProbabilityEstimate
class EventCausalChain(FrozenContract):
    review_id:str
    owner_forecast_id:str
    cutoff:AwareDatetime
    scope_id:str
    horizon:Literal['24h']='24h'
    nodes:tuple[ResearchMechanismNodeV1,...]
    edges:tuple[ResearchMechanismEdgeV1,...]
    alternative_refs:tuple[str,...]
    priced_in_status:Literal['unknown']='unknown'
    priced_in_refs:tuple[str,...]
    unresolved:tuple[str,...]
    affected_plan_refs:tuple[str,...]
class EventConditionalPlan(FrozenContract):
    scenario_id:str
    event_result_probability:Decimal
    activation_baseline:Literal['prior_month']='prior_month'
    activation_required:tuple[str,...]
    causal_chain:EventCausalChain
    alternative_mechanism:str
    flat_proposal:ResearchActionCandidateV1
    long_proposal:ResearchActionCandidateV1
    scenario_asset_reaction_estimate:EventProbabilityEstimate
    scenario_strategy_net_profit_estimate:EventProbabilityEstimate
    proposal_status:Literal['preplan_awaits_release_and_price_confirmation']='preplan_awaits_release_and_price_confirmation'
    price_anchor_resolved_at:None=None
    price_anchor_source_ref:None=None
    armed:Literal[False]=False
    execution_authorized:Literal[False]=False
    @model_validator(mode='after')
    def scopes(self):
        if self.flat_proposal.position_context!='flat' or self.long_proposal.position_context!='long' or self.flat_proposal.action!='enter_long' or self.long_proposal.action!='reduce_long':raise ValueError('event_preplan_position_or_action_invalid')
        if any(not p.entry or not p.exit or not p.invalidation or p.probability.kind!='unavailable' or p.probability.value is not None for p in (self.flat_proposal,self.long_proposal)):raise ValueError('event_preplan_incomplete_or_false_Action_probability')
        return self
class EventOpportunityBase(FrozenContract):
    schema_version:Literal['event-opportunity-snapshot.v1']='event-opportunity-snapshot.v1'
    snapshot_id:str
    event_key:str
    reference_period:str
    instrument:Literal['BTC-USDT','ETH-USDT']
    asset_scope:str
    anchor:Literal['event_release']='event_release'
    scheduled_at:AwareDatetime
    event_probability_as_of:AwareDatetime
    as_of:AwareDatetime
    published_at:AwareDatetime
    scenario_snapshot_ref:str
    scenario_snapshot_hash:str
    entry_valid_from:AwareDatetime
    entry_valid_until:AwareDatetime
    exit_valid_until:AwareDatetime
    market_model_as_of:AwareDatetime
    model_valid_until:AwareDatetime
    reestimate_required_at:AwareDatetime
    revision_parent_ref:str|None=None
    model:EventResponseModel
    asset_reaction_estimate:EventProbabilityEstimate
    strategy_net_profit_estimate:EventProbabilityEstimate
    strategy_net_profit_given_entry_estimate:EventProbabilityEstimate
    plans:tuple[EventConditionalPlan,...]
    price_estimation_status:Literal['no_authenticated_price_samples','event_day_baseline_only','all_scenarios_supported_estimated_uncalibrated']
    missing_scenario_ids:tuple[str,...]
    gaps:tuple[str,...]
    financial_qualified:Literal[False]=False
    action_execution_authorized:Literal[False]=False
    automatic_monitoring_armed:Literal[False]=False
    payload_hash:str
    @model_validator(mode='after')
    def identity(self):
        if not self.event_probability_as_of<=self.as_of<=self.published_at<self.scheduled_at or self.market_model_as_of!=self.as_of or self.model.as_of!=self.as_of or self.asset_scope!=self.model.scope_id or self.instrument!=self.model.instrument:raise ValueError('event_opportunity_clock_scope_invalid')
        if self.entry_valid_from!=self.scheduled_at+timedelta(seconds=60) or self.entry_valid_until!=self.scheduled_at+timedelta(hours=6) or self.exit_valid_until!=self.scheduled_at+timedelta(hours=24,seconds=60):raise ValueError('event_opportunity_future_event_anchor_invalid')
        if self.reestimate_required_at!=min(self.as_of+timedelta(hours=6),self.scheduled_at-timedelta(seconds=1)) or self.model_valid_until!=self.reestimate_required_at:raise ValueError('event_opportunity_reestimate_invalid')
        if len(self.plans)!=9 or len({p.scenario_id for p in self.plans})!=9 or sum((p.event_result_probability for p in self.plans),Decimal(0))!=1:raise ValueError('event_opportunity_partition_invalid')
        missing=tuple(p.scenario_id for p in self.plans if p.scenario_asset_reaction_estimate.events==0)
        expected='no_authenticated_price_samples' if not self.model.samples else 'event_day_baseline_only' if missing else 'all_scenarios_supported_estimated_uncalibrated'
        if self.missing_scenario_ids!=missing or self.price_estimation_status!=expected:raise ValueError('event_opportunity_coverage_claim_invalid')
        if canonical_hash(self.model.model_dump(mode='json',exclude={'payload_hash'}))!=self.model.payload_hash:raise ValueError('event_opportunity_model_hash_invalid')
        for p in self.plans:
            for a in (p.flat_proposal,p.long_proposal):
                if a.as_of!=self.as_of or a.valid_until!=self.exit_valid_until or a.next_review_at!=self.reestimate_required_at or a.scope_id!=self.asset_scope or a.mechanism_review_ref!=p.causal_chain.review_id:raise ValueError('event_opportunity_proposal_binding_invalid')
        return self
class EventOpportunitySnapshot(EventOpportunityBase):
    owner_kind:Literal['research_task']='research_task'
    run_id:str
    task_mechanism_reviews:tuple[ResearchMechanismReviewV1,...]
    @model_validator(mode='after')
    def task_identity(self):
        if len(self.task_mechanism_reviews)!=9 or any(m.run_id!=self.run_id for m in self.task_mechanism_reviews) or {m.review_id for m in self.task_mechanism_reviews}!={p.causal_chain.review_id for p in self.plans}:raise ValueError('event_opportunity_Task_mechanism_identity_invalid')
        return self
class EventOwnedOpportunitySnapshot(EventOpportunityBase):
    owner_kind:Literal['calendar_event']='calendar_event'
    forecast_id:str
    @model_validator(mode='after')
    def event_identity(self):
        if self.forecast_id!=self.scenario_snapshot_ref or any(p.causal_chain.owner_forecast_id!=self.forecast_id for p in self.plans):raise ValueError('event_owned_opportunity_forecast_identity_invalid')
        return self

from .event_macro_context import EventMacroContext
from .event_opportunity_policy import EventPolicyDefinition
from .event_response_path import EventResponseModelV2

class EventConditionalPlanV2(EventConditionalPlan):
    policy:EventPolicyDefinition

class EventOwnedOpportunitySnapshotV2(EventOwnedOpportunitySnapshot):
    schema_version:Literal['event-opportunity-snapshot.v2']='event-opportunity-snapshot.v2'
    model:EventResponseModelV2
    plans:tuple[EventConditionalPlanV2,...]
    macro_context:EventMacroContext
    policy_catalog_hash:str
    @model_validator(mode='after')
    def policy_binding(self):
        if self.macro_context.as_of!=self.as_of or self.model.macro_context_hash!=self.macro_context.input_hash or self.model.policy_catalog_hash!=self.policy_catalog_hash:
            raise ValueError('event_opportunity_policy_context_binding_invalid')
        return self

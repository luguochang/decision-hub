"""Versioned research rules, separate from Action publication permissions."""
from typing import Literal
from pydantic import Field, model_validator
from .market_path import FrozenContract


class EventPolicyDefinition(FrozenContract):
    schema_version: Literal['event-opportunity-policy.v2'] = 'event-opportunity-policy.v2'
    policy_id: str
    policy_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    scenario_id: str
    headline_band: Literal['cooler', 'near', 'hotter']
    core_band: Literal['cooler', 'near', 'hotter']
    scope_id: str
    variant: str
    context_state: Literal['unknown', 'headwind', 'tailwind', 'mixed']
    macro_context_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    hypothesis_status: Literal['unvalidated_research_rule'] = 'unvalidated_research_rule'
    selector_basis: Literal['CPI_continuous_MoM_relative_prior_month_not_consensus'] = 'CPI_continuous_MoM_relative_prior_month_not_consensus'
    flat_action: Literal['enter_long'] = 'enter_long'
    long_action: Literal['reduce_long'] = 'reduce_long'
    flat_entry: dict
    flat_exit: dict
    flat_invalidation: dict
    long_entry: dict
    long_exit: dict
    long_invalidation: dict
    entry_confirmations: int = Field(ge=2, le=4)
    protection_confirmations: Literal[1] = 1
    long_confirmations: Literal[2] = 2
    reaction_delay_seconds: Literal[60] = 60
    entry_start_seconds: Literal[60] = 60
    entry_end_seconds: Literal[21600] = 21600
    exit_end_seconds: Literal[86460] = 86460
    fee_fraction_each_side: Literal['0.001'] = '0.001'
    slippage_fraction_each_side: Literal['0.0005'] = '0.0005'
    anchor_recipe: Literal['closed_minute_return_6h_and_complete_UTC_hour_volume.v1'] = 'closed_minute_return_6h_and_complete_UTC_hour_volume.v1'
    rationale: str
    counter_hypothesis: str
    gaps: tuple[str, ...]

    @model_validator(mode='after')
    def identity(self):
        expected = f'cpi:headline_{self.headline_band}:core_{self.core_band}'
        if self.scenario_id != expected:
            raise ValueError('event_policy_scenario_identity_invalid')
        if self.scope_id not in {'crypto:spot:okx:BTC-USDT:trade:1m', 'crypto:spot:okx:ETH-USDT:trade:1m'}:
            raise ValueError('event_policy_scope_not_enabled')
        return self

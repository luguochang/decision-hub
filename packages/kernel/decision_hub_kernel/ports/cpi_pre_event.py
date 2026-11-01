"""A frozen CPI input binding, never a forecast or market-consensus receipt."""
from decimal import Decimal
from typing import Literal
from pydantic import AwareDatetime,Field,model_validator
from .market_path import FrozenContract,canonical_hash
from .bls_cpi import period_offset

class CpiContextSource(FrozenContract):
    observation_id:str=Field(min_length=1)
    content_hash:str=Field(pattern=r'^[a-f0-9]{64}$')
    known_at:AwareDatetime

class CpiContextMetric(FrozenContract):
    metric_id:Literal['headline_mom','core_mom','headline_yoy','core_yoy']
    role:Literal['nowcast','prior']
    reference_period:str=Field(pattern=r'^20\d{2}-(0[1-9]|1[0-2])$')
    value:Decimal
    source:CpiContextSource
    unit:Literal['percent']='percent'
    seasonal_adjustment:Literal['seasonally_adjusted','not_seasonally_adjusted']
    source_update_label:str|None=None
    methodology_notices:tuple[str,...]=()
    @model_validator(mode='after')
    def valid_value(self):
        expected='seasonally_adjusted' if self.metric_id.endswith('_mom') else 'not_seasonally_adjusted'
        if not self.value.is_finite() or self.seasonal_adjustment!=expected:raise ValueError('cpi_context_metric_invalid')
        return self

class CpiPreEventContextBinding(FrozenContract):
    schema_version:Literal['cpi-pre-event-context.v1']='cpi-pre-event-context.v1'
    cutoff_at:AwareDatetime
    schedule_source:CpiContextSource|None
    event_key:str|None
    scheduled_at:AwareDatetime|None
    reference_period:str|None=Field(default=None,pattern=r'^20\d{2}-(0[1-9]|1[0-2])$')
    nowcast:tuple[CpiContextMetric,...]=()
    prior:tuple[CpiContextMetric,...]=()
    consensus:None=None
    actual:None=None
    post_release_surprise:None=None
    incomplete_reasons:tuple[str,...]=()
    forecast_completed:Literal[False]=False
    financial_qualified:Literal[False]=False
    @model_validator(mode='after')
    def valid_pit_roles(self):
        if (self.schedule_source is None)!=(self.scheduled_at is None) or (self.schedule_source is None)!=(self.event_key is None):raise ValueError('cpi_context_schedule_identity_invalid')
        if self.scheduled_at is not None and self.scheduled_at<=self.cutoff_at:raise ValueError('cpi_context_event_already_public')
        if self.reference_period is None and (self.nowcast or self.prior):raise ValueError('cpi_context_period_not_authenticated')
        for role,items in (('nowcast',self.nowcast),('prior',self.prior)):
            if len(items) not in {0,4} or len({p.metric_id for p in items})!=len(items):raise ValueError('cpi_context_metric_set_invalid')
            for point in items:
                expected=self.reference_period if role=='nowcast' else period_offset(self.reference_period,-1)
                if point.role!=role or point.reference_period!=expected or point.source.known_at>self.cutoff_at:raise ValueError('cpi_context_role_or_period_invalid')
        if self.schedule_source and self.schedule_source.known_at>self.cutoff_at:raise ValueError('cpi_context_schedule_known_late')
        return self
    @property
    def content_hash(self):return canonical_hash(self.model_dump(mode='json'))

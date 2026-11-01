"""Authenticated daily background; never survey consensus or historical vintage."""
from datetime import date
from decimal import Decimal
from typing import Literal
from pydantic import AwareDatetime, Field, model_validator
from .market_path import FrozenContract, canonical_hash

class EventMacroSeries(FrozenContract):
    series_id: Literal['DGS10','DFII10','DTWEXBGS']
    label: str
    unit: Literal['percent','index_jan_2006_100']
    value: Decimal
    observation_date: date
    change_1_observation: Decimal
    change_5_observations: Decimal
    known_at: AwareDatetime
    observation_ref: str
    content_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    raw_body_ref: str
    raw_body_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    frequency: Literal['daily']='daily'
    public_time_precision: Literal['unknown']='unknown'
    history_vintage: Literal['current_visible_not_historical_vintage']='current_visible_not_historical_vintage'
    @model_validator(mode='after')
    def valid(self):
        from .macro_daily import SERIES
        label,unit,_=SERIES[self.series_id]
        if self.label!=label or self.unit!=unit or self.observation_date>self.known_at.date() or any(not v.is_finite() for v in (self.value,self.change_1_observation,self.change_5_observations)) or self.raw_body_ref!='raw://sha256/'+self.raw_body_hash:
            raise ValueError('event_macro_series_identity_invalid')
        if self.series_id=='DTWEXBGS' and min(self.value,self.value-self.change_1_observation,self.value-self.change_5_observations)<=0:
            raise ValueError('event_macro_dollar_index_nonpositive')
        return self

class EventMacroContext(FrozenContract):
    schema_version: Literal['event-macro-context.v1']='event-macro-context.v1'
    as_of: AwareDatetime
    state: Literal['unknown','headwind','tailwind','mixed']
    series: tuple[EventMacroSeries,...]
    gaps: tuple[str,...]
    payload_hash: str
    @property
    def input_hash(self):
        return canonical_hash(self.model_dump(mode='json',exclude={'as_of','payload_hash'}))
    @model_validator(mode='after')
    def valid(self):
        if len({x.series_id for x in self.series})!=len(self.series) or any(x.known_at>self.as_of for x in self.series):
            raise ValueError('event_macro_context_duplicate_or_future_known')
        if self.payload_hash!='0'*64 and canonical_hash(self.model_dump(mode='json',exclude={'payload_hash'}))!=self.payload_hash:
            raise ValueError('event_macro_context_hash_invalid')
        return self

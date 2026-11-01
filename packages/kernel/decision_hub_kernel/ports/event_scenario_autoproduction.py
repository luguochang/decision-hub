"""Event-owned forecasts and operational progress; no synthetic Run owner."""
from typing import Literal
from pydantic import AwareDatetime,Field,model_validator
from .market_path import FrozenContract,canonical_hash
from .event_scenario import CpiScenarioForecastValueBase

class CpiEventOwnedScenarioSnapshot(CpiScenarioForecastValueBase):
    schema_version:Literal['event-owned-scenario-snapshot.v1']='event-owned-scenario-snapshot.v1'
    owner_kind:Literal['calendar_event']='calendar_event'
    forecast_id:str
    job_id:str
    slot_id:str=Field(pattern=r'^20\d{2}-\d{2}-\d{2}$')
    input_hash:str=Field(pattern=r'^[a-f0-9]{64}$')
    model_hash:str=Field(pattern=r'^[a-f0-9]{64}$')
    model_recipe:dict[str,str]
    source_guard:tuple[tuple[str,str,str],...]
    revision_parent_ref:str|None=None
    @model_validator(mode='after')
    def owned_identity(self):
        if self.snapshot_id!=self.forecast_id or self.slot_id!=self.as_of.date().isoformat():raise ValueError('event_owned_forecast_identity_invalid')
        if not self.source_guard or len(set((t,k) for t,k,h in self.source_guard))!=len(self.source_guard):raise ValueError('event_owned_source_guard_invalid')
        return self

class EventScenarioStatus(FrozenContract):
    event_key:str
    state:Literal['waiting_inputs','pending','running','published','failed','missed']
    forecast_id:str|None=None
    forecast_hash:str|None=None
    last_error_code:str|None=None
    next_attempt_at:AwareDatetime|None=None
    attempts:int=Field(ge=0)
    updated_at:AwareDatetime

class EventScenarioEventView(FrozenContract):
    event_key:str
    scheduled_at:AwareDatetime
    reference_period:str|None
    status:EventScenarioStatus|None=None
    forecast_id:str|None=None

class EventScenarioProducerReport(FrozenContract):
    registered:int=0
    waiting_inputs:int=0
    published_forecast_ids:tuple[str,...]=()
    active_forecast_ids:tuple[str,...]=()
    failed:int=0
    errors:tuple[str,...]=()

def seal_event_owned_scenario(body):
    draft=CpiEventOwnedScenarioSnapshot.model_validate(body|{'payload_hash':'0'*64})
    canonical=draft.model_dump(mode='json',exclude={'payload_hash'})
    return CpiEventOwnedScenarioSnapshot.model_validate(canonical|{'payload_hash':canonical_hash(canonical)})

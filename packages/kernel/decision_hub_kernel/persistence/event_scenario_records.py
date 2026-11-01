"""Calendar-event-owned forecast jobs and immutable published outputs."""
from datetime import datetime
from sqlalchemy import CheckConstraint,DateTime,Index,Integer,String,Text,UniqueConstraint
from sqlalchemy.orm import Mapped,mapped_column
from .db import Base

class EventScenarioJobRecord(Base):
    __tablename__='event_scenario_jobs'
    __table_args__=(UniqueConstraint('event_key','slot_id','job_kind','scope_key','input_hash','model_hash',name='uq_event_scenario_job'),
        CheckConstraint("state IN ('waiting_inputs','pending','running','published','failed','missed')",name='ck_event_scenario_job_state'),
        CheckConstraint("job_kind IN ('forecast','opportunity')",name='ck_event_scenario_job_kind'),
        CheckConstraint('attempts>=0 AND lease_generation>=0',name='ck_event_scenario_job_generation'),Index('ix_event_scenario_jobs_due','state','next_attempt_at','lease_expires_at'))
    job_id:Mapped[str]=mapped_column(String(96),primary_key=True)
    event_key:Mapped[str]=mapped_column(String(200),index=True)
    scheduled_at:Mapped[datetime]=mapped_column(DateTime(timezone=True))
    reference_period:Mapped[str|None]=mapped_column(String(7),nullable=True)
    slot_id:Mapped[str]=mapped_column(String(10))
    job_kind:Mapped[str]=mapped_column(String(16),default='forecast')
    scope_key:Mapped[str]=mapped_column(String(128),default='global')
    parent_forecast_id:Mapped[str|None]=mapped_column(String(96),nullable=True)
    input_hash:Mapped[str]=mapped_column(String(64))
    model_hash:Mapped[str]=mapped_column(String(64))
    input_manifest_json:Mapped[str]=mapped_column(Text)
    state:Mapped[str]=mapped_column(String(24))
    attempts:Mapped[int]=mapped_column(Integer,default=0)
    lease_owner:Mapped[str|None]=mapped_column(String(128),nullable=True)
    lease_generation:Mapped[int]=mapped_column(Integer,default=0)
    lease_expires_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
    next_attempt_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
    last_error_code:Mapped[str|None]=mapped_column(String(128),nullable=True)
    forecast_id:Mapped[str|None]=mapped_column(String(96),nullable=True)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True))
    updated_at:Mapped[datetime]=mapped_column(DateTime(timezone=True))
    completed_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)

class EventScenarioForecastRecord(Base):
    __tablename__='event_scenario_forecasts'
    __table_args__=(UniqueConstraint('job_id',name='uq_event_scenario_forecast_job'),Index('ix_event_scenario_forecast_event_time','event_key','published_at'))
    forecast_id:Mapped[str]=mapped_column(String(96),primary_key=True)
    job_id:Mapped[str]=mapped_column(String(96))
    event_key:Mapped[str]=mapped_column(String(200))
    scheduled_at:Mapped[datetime]=mapped_column(DateTime(timezone=True))
    reference_period:Mapped[str]=mapped_column(String(7))
    slot_id:Mapped[str]=mapped_column(String(10))
    input_hash:Mapped[str]=mapped_column(String(64))
    model_hash:Mapped[str]=mapped_column(String(64))
    as_of:Mapped[datetime]=mapped_column(DateTime(timezone=True))
    published_at:Mapped[datetime]=mapped_column(DateTime(timezone=True))
    revision_parent_ref:Mapped[str|None]=mapped_column(String(96),nullable=True)
    schema_version:Mapped[str]=mapped_column(String(64))
    payload_json:Mapped[str]=mapped_column(Text)
    payload_hash:Mapped[str]=mapped_column(String(64))

class EventScenarioOutputRecord(Base):
    __tablename__='event_scenario_outputs'
    __table_args__=(UniqueConstraint('forecast_id','kind','scope_key','inputs_hash','model_hash',name='uq_event_scenario_output'),Index('ix_event_scenario_output_event_scope','event_key','scope_key','published_at'))
    output_id:Mapped[str]=mapped_column(String(128),primary_key=True)
    forecast_id:Mapped[str]=mapped_column(String(96))
    owner_forecast_hash:Mapped[str]=mapped_column(String(64))
    event_key:Mapped[str]=mapped_column(String(200))
    kind:Mapped[str]=mapped_column(String(32))
    scope_key:Mapped[str]=mapped_column(String(128))
    inputs_hash:Mapped[str]=mapped_column(String(64))
    model_hash:Mapped[str]=mapped_column(String(64))
    schema_version:Mapped[str]=mapped_column(String(64))
    payload_json:Mapped[str]=mapped_column(Text)
    payload_hash:Mapped[str]=mapped_column(String(64))
    published_at:Mapped[datetime]=mapped_column(DateTime(timezone=True))
    revision_parent_ref:Mapped[str|None]=mapped_column(String(128),nullable=True)

"""Private metadata, pending explicit canonical migration; no production registration."""
from datetime import datetime
from packages.kernel.decision_hub_kernel.persistence.db import Base as LearningBase
from sqlalchemy import CheckConstraint, DDL, DateTime, Integer, String, Text, UniqueConstraint, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

class LearningProtocolRecord(LearningBase):
    __tablename__ = 'research_learning_protocols'
    protocol_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    payload_json: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(String(64), unique=True)

class LearningBindingRecord(LearningBase):
    __tablename__ = 'research_learning_bindings'
    __table_args__ = (
        UniqueConstraint('protocol_id', 'source_kind', 'source_id', 'source_hash'),
        CheckConstraint("source_kind IN ('action_outcome','execution_observation')"),
    )
    binding_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    job_id: Mapped[str] = mapped_column(String(128), unique=True)
    protocol_id: Mapped[str] = mapped_column(String(200))
    source_kind: Mapped[str] = mapped_column(String(32))
    source_id: Mapped[str] = mapped_column(String(200))
    source_hash: Mapped[str] = mapped_column(String(64))
    frozen_at: Mapped[object] = mapped_column(DateTime(timezone=True))
    payload_json: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(String(64))

class LearningStageRecord(LearningBase):
    __tablename__ = 'research_learning_stages'
    __table_args__ = (
        UniqueConstraint('job_id', 'phase'),
        CheckConstraint("phase IN ('context','dispatch','reflection','package','trial','consumed','comparison')"),
    )
    stage_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    job_id: Mapped[str] = mapped_column(String(128), index=True)
    phase: Mapped[str] = mapped_column(String(32))
    payload_json: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(String(64))
for table, identity in ((LearningProtocolRecord.__table__, 'protocol_id=NEW.protocol_id OR payload_hash=NEW.payload_hash'), (LearningBindingRecord.__table__, 'binding_id=NEW.binding_id OR job_id=NEW.job_id OR (protocol_id=NEW.protocol_id AND source_kind=NEW.source_kind AND source_id=NEW.source_id AND source_hash=NEW.source_hash)'), (LearningStageRecord.__table__, 'stage_id=NEW.stage_id OR (job_id=NEW.job_id AND phase=NEW.phase)')):
    for operation in ('UPDATE', 'DELETE'):
        event.listen(table, 'after_create', DDL(f"CREATE TRIGGER {table.name}_no_{operation.lower()} BEFORE {operation} ON {table.name} BEGIN SELECT RAISE(ABORT,'research_learning_original_immutable'); END").execute_if(dialect='sqlite'))
    event.listen(table, 'after_create', DDL(f"CREATE TRIGGER {table.name}_no_replace BEFORE INSERT ON {table.name} WHEN EXISTS(SELECT 1 FROM {table.name} WHERE {identity}) BEGIN SELECT RAISE(ABORT,'research_learning_original_immutable'); END").execute_if(dialect='sqlite'))

class LearningUnitRecord(LearningBase):
    __tablename__ = 'research_learning_units'
    __table_args__ = (UniqueConstraint('protocol_id', 'portfolio_hash', 'opportunity_id', 'method_hash'),)
    unit_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    protocol_id: Mapped[str] = mapped_column(String(200))
    portfolio_hash: Mapped[str] = mapped_column(String(64))
    opportunity_id: Mapped[str] = mapped_column(String(128))
    method_hash: Mapped[str] = mapped_column(String(64))
    job_id: Mapped[str] = mapped_column(String(128), unique=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload_json: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(String(64))
for operation in ('UPDATE', 'DELETE'):
    event.listen(LearningUnitRecord.__table__, 'after_create', DDL(f"CREATE TRIGGER learning_unit_no_{operation.lower()} BEFORE {operation} ON research_learning_units BEGIN SELECT RAISE(ABORT,'learning_unit_immutable'); END").execute_if(dialect='sqlite'))
event.listen(LearningUnitRecord.__table__, 'after_create', DDL("CREATE TRIGGER learning_unit_no_replace BEFORE INSERT ON research_learning_units WHEN EXISTS(SELECT 1 FROM research_learning_units WHERE unit_id=NEW.unit_id OR job_id=NEW.job_id OR (protocol_id=NEW.protocol_id AND portfolio_hash=NEW.portfolio_hash AND opportunity_id=NEW.opportunity_id AND method_hash=NEW.method_hash)) BEGIN SELECT RAISE(ABORT,'learning_unit_immutable'); END").execute_if(dialect='sqlite'))


class LearningRegistrationRecord(LearningBase):
    __tablename__ = 'research_learning_registrations'
    __table_args__ = (UniqueConstraint('protocol_id', 'portfolio_id'),)
    registration_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    protocol_id: Mapped[str] = mapped_column(String(200))
    portfolio_id: Mapped[str] = mapped_column(String(128), index=True)
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload_json: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(String(64))


for operation in ('UPDATE', 'DELETE'):
    event.listen(LearningRegistrationRecord.__table__, 'after_create', DDL(
        f"CREATE TRIGGER learning_registration_no_{operation.lower()} BEFORE {operation} ON research_learning_registrations BEGIN SELECT RAISE(ABORT,'learning_registration_immutable'); END"
    ).execute_if(dialect='sqlite'))
event.listen(LearningRegistrationRecord.__table__, 'after_create', DDL(
    "CREATE TRIGGER learning_registration_no_replace BEFORE INSERT ON research_learning_registrations WHEN EXISTS(SELECT 1 FROM research_learning_registrations WHERE registration_id=NEW.registration_id OR (protocol_id=NEW.protocol_id AND portfolio_id=NEW.portfolio_id)) BEGIN SELECT RAISE(ABORT,'learning_registration_immutable'); END"
).execute_if(dialect='sqlite'))


class LearningScanCursorRecord(LearningBase):
    """Operational progress only; no source evidence or permission grant."""
    __tablename__ = 'research_learning_scan_cursors'
    __table_args__ = (CheckConstraint('version >= 1'),)
    scanner_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    version: Mapped[int] = mapped_column(Integer)
    last_registration_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

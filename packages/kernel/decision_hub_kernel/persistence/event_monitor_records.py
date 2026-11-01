"""Mutable event monitor heads plus immutable evaluations, notice intents and receipts."""

from datetime import datetime

from sqlalchemy import (
    DDL,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class EventOpportunityMonitorRecord(Base):
    __tablename__ = "event_opportunity_monitor_heads"
    __table_args__ = (
        UniqueConstraint(
            "forecast_id", "instrument", "position_context", name="uq_event_monitor_owner"
        ),
        CheckConstraint(
            "state IN ('waiting','triggered','invalidated','expired','missing')",
            name="ck_event_monitor_state",
        ),
        CheckConstraint("position_context IN ('flat','long')", name="ck_event_monitor_position"),
        Index("ix_event_monitor_due", "next_due", "lease_expires_at"),
    )
    monitor_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    event_key: Mapped[str] = mapped_column(String(200), index=True)
    forecast_id: Mapped[str] = mapped_column(String(96))
    instrument: Mapped[str] = mapped_column(String(16))
    position_context: Mapped[str] = mapped_column(String(8))
    activation_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    opportunity_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    opportunity_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    scenario_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    state: Mapped[str] = mapped_column(String(24), default="waiting")
    state_version: Mapped[int] = mapped_column(Integer, default=0)
    latest_evaluation_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    last_input_watermark: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_due: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_generation: Mapped[int] = mapped_column(Integer, default=0)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EventOpportunityEvaluationRecord(Base):
    __tablename__ = "event_opportunity_evaluations"
    __table_args__ = (UniqueConstraint("monitor_id", "tick_at", name="uq_event_monitor_tick"),)
    evaluation_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    monitor_id: Mapped[str] = mapped_column(String(96), index=True)
    event_key: Mapped[str] = mapped_column(String(200), index=True)
    tick_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload_json: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EventOpportunityNoticeRecord(Base):
    __tablename__ = "event_opportunity_notices"
    __table_args__ = (UniqueConstraint("dedupe_key", name="uq_event_notice_dedupe"),)
    notice_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    event_key: Mapped[str] = mapped_column(String(200), index=True)
    dedupe_key: Mapped[str] = mapped_column(String(256))
    outbox_id: Mapped[int] = mapped_column(Integer)
    payload_json: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class EventOpportunityCommandRecord(Base):
    __tablename__ = "event_opportunity_commands"
    __table_args__ = (
        UniqueConstraint("notice_id", "owner_id", "request_id", name="uq_event_notice_command"),
    )
    command_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    notice_id: Mapped[str] = mapped_column(String(96), index=True)
    owner_id: Mapped[str] = mapped_column(String(128))
    request_id: Mapped[str] = mapped_column(String(128))
    command: Mapped[str] = mapped_column(String(16))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


IMMUTABLE_EVENT_MONITOR_TABLES = (
    EventOpportunityEvaluationRecord.__table__,
    EventOpportunityNoticeRecord.__table__,
    EventOpportunityCommandRecord.__table__,
)
for _table in IMMUTABLE_EVENT_MONITOR_TABLES:
    _keys = [tuple(c.name for c in _table.primary_key.columns)]
    _keys.extend(
        tuple(c.name for c in x.columns)
        for x in _table.constraints
        if isinstance(x, UniqueConstraint)
    )
    _conflicts = " OR ".join("(" + " AND ".join(f"{n}=NEW.{n}" for n in key) + ")" for key in _keys)
    event.listen(
        _table,
        "after_create",
        DDL(
            f"CREATE TRIGGER trg_{_table.name}_immutable_insert BEFORE INSERT ON {_table.name} "
            f"WHEN EXISTS (SELECT 1 FROM {_table.name} WHERE {_conflicts}) "
            f"BEGIN SELECT RAISE(ABORT, 'UNIQUE immutable identity: {_table.name}'); END"
        ).execute_if(dialect="sqlite"),
    )
    for _operation in ("UPDATE", "DELETE"):
        event.listen(
            _table,
            "after_create",
            DDL(
                f"CREATE TRIGGER trg_{_table.name}_immutable_{_operation.lower()} "
                f"BEFORE {_operation} ON {_table.name} "
                f"BEGIN SELECT RAISE(ABORT, '{_table.name}_immutable'); END"
            ).execute_if(dialect="sqlite"),
        )

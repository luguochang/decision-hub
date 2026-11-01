"""Durable admission intents; deep research remains in the existing Run queue."""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base

SPOT_REQUEST_PROTOCOL = "risk-research-request.spot.v1"


class RiskResearchPlanRecord(Base):
    __tablename__ = "risk_research_plans"
    plan_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    scope_id: Mapped[str] = mapped_column(String(192), unique=True)
    instrument: Mapped[str] = mapped_column(String(32))
    state: Mapped[str] = mapped_column(String(16), default="active")
    interval_seconds: Mapped[int] = mapped_column(Integer, default=86400)
    next_due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint("state IN ('active','paused')", name="ck_risk_research_plan_state"),
        CheckConstraint("interval_seconds = 86400", name="ck_risk_research_plan_interval"),
    )


class RiskResearchRequestRecord(Base):
    __tablename__ = "risk_research_requests"
    request_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    protocol_version: Mapped[str] = mapped_column(String(80), default=SPOT_REQUEST_PROTOCOL, server_default=SPOT_REQUEST_PROTOCOL)
    scope_id: Mapped[str] = mapped_column(String(192))
    instrument: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(24), default="pending_admission")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_generation: Mapped[int] = mapped_column(Integer, default=0)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    event_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    run_id: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True)
    error_code: Mapped[str | None] = mapped_column(String(160), nullable=True)
    __table_args__ = (
        Index("ix_risk_research_request_due", "status", "available_at", "lease_expires_at"),
        Index("ix_risk_research_request_scope_time", "scope_id", "created_at"),
        CheckConstraint(
            "status IN ('pending_admission','claiming','queued','completed','failed')",
            name="ck_risk_research_request_status",
        ),
        CheckConstraint("attempts >= 0 AND attempts <= 3", name="ck_risk_research_attempts"),
    )


class RiskResearchTriggerRecord(Base):
    __tablename__ = "risk_research_triggers"
    trigger_key: Mapped[str] = mapped_column(String(256), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(128), index=True)
    kind: Mapped[str] = mapped_column(String(24))
    scope_id: Mapped[str] = mapped_column(String(192))
    source_ref: Mapped[str] = mapped_column(Text)
    payload_json: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(String(64))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

from .perpetual_research_schedule_records import PerpetualResearchScheduleRecord  # noqa: E402,F401

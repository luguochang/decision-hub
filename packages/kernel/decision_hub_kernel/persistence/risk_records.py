"""Market risk ledger. Register on initialization; historical records are additive."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class MarketCandleRecord(Base):
    __tablename__ = "market_candles"
    __table_args__ = (
        UniqueConstraint("scope_id", "open_at", "revision_hash", name="uq_market_candle_version"),
        Index("ix_market_candle_scope_time", "scope_id", "open_at", "known_at"),
    )
    candle_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    scope_id: Mapped[str] = mapped_column(String(192))
    venue: Mapped[str] = mapped_column(String(64))
    instrument: Mapped[str] = mapped_column(String(128))
    price_type: Mapped[str] = mapped_column(String(32))
    interval_seconds: Mapped[int] = mapped_column(Integer)
    open_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    close_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    known_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revision_hash: Mapped[str] = mapped_column(String(64))
    candle_json: Mapped[str] = mapped_column(Text)
    capture_ref: Mapped[str] = mapped_column(Text)
    capture_hash: Mapped[str] = mapped_column(String(64))


class RiskMonitorPlanRecord(Base):
    __tablename__ = "risk_monitor_plans"
    __table_args__ = (
        CheckConstraint(
            "state IN ('active','paused','degraded','expired')", name="ck_risk_plan_state"
        ),
        CheckConstraint("lease_generation >= 0", name="ck_risk_plan_generation"),
        Index("ix_risk_monitor_due", "state", "next_due_at", "lease_expires_at"),
    )
    plan_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    scope_id: Mapped[str] = mapped_column(String(192), index=True)
    rule_version: Mapped[str] = mapped_column(String(128))
    rule_json: Mapped[str] = mapped_column(Text)
    rule_hash: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(16), default="active")
    next_due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_generation: Mapped[int] = mapped_column(Integer, default=0)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_evaluated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_data_as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_truth: Mapped[str | None] = mapped_column(String(16), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    enter_streak: Mapped[int] = mapped_column(Integer, default=0)
    exit_streak: Mapped[int] = mapped_column(Integer, default=0)
    consecutive_errors: Mapped[int] = mapped_column(Integer, default=0)
    active_episode_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RiskConditionEvaluationRecord(Base):
    __tablename__ = "risk_condition_evaluations"
    __table_args__ = (
        UniqueConstraint("plan_id", "rule_version", "tick_at", name="uq_risk_evaluation_tick"),
        CheckConstraint("truth_state IN ('true','false','unknown','error')", name="ck_risk_truth"),
    )
    evaluation_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    plan_id: Mapped[str] = mapped_column(String(80), index=True)
    rule_version: Mapped[str] = mapped_column(String(128))
    tick_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    truth_state: Mapped[str] = mapped_column(String(16))
    data_state: Mapped[str] = mapped_column(String(32))
    value_json: Mapped[str] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(String(128), nullable=True)
    input_json: Mapped[str] = mapped_column(Text)
    input_hash: Mapped[str] = mapped_column(String(64))
    episode_id: Mapped[str | None] = mapped_column(String(80), nullable=True)


class RiskAlertRecord(Base):
    __tablename__ = "risk_alerts"
    __table_args__ = (Index("ix_risk_alert_scope_time", "scope_id", "created_at"),)
    alert_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    episode_id: Mapped[str] = mapped_column(String(80), unique=True)
    plan_id: Mapped[str] = mapped_column(String(80), index=True)
    evaluation_id: Mapped[str] = mapped_column(String(80))
    scope_id: Mapped[str] = mapped_column(String(192))
    rule_version: Mapped[str] = mapped_column(String(128))
    alarm_kind: Mapped[str] = mapped_column(String(32))
    severity: Mapped[str] = mapped_column(String(32))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    payload_json: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(String(64))
    review_schedule_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)


class RiskAlertDeliveryReceiptRecord(Base):
    __tablename__ = "risk_alert_delivery_receipts"
    __table_args__ = (
        UniqueConstraint("alert_id", "receipt_kind", "request_id", name="uq_risk_delivery_request"),
        CheckConstraint("receipt_kind IN ('display','ack','send')", name="ck_risk_receipt_kind"),
    )
    receipt_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    alert_id: Mapped[str] = mapped_column(String(80), index=True)
    receipt_kind: Mapped[str] = mapped_column(String(16))
    channel: Mapped[str] = mapped_column(String(64))
    request_id: Mapped[str] = mapped_column(String(128))
    outbox_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload_json: Mapped[str] = mapped_column(Text)


class RiskMonitorCommandRecord(Base):
    __tablename__ = "risk_monitor_commands"
    request_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    plan_id: Mapped[str] = mapped_column(String(80), index=True)
    payload_hash: Mapped[str] = mapped_column(String(64))
    result_json: Mapped[str] = mapped_column(Text)
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MarketPathAcquisitionRecord(Base):
    __tablename__ = "market_path_acquisitions"
    __table_args__ = (
        UniqueConstraint("scope_id", "slot_at", name="uq_market_acquisition_slot"),
        Index("ix_market_acquisition_scope_known", "scope_id", "known_at"),
        CheckConstraint(
            "status IN ('running','succeeded','failed')", name="ck_market_acquisition_status"
        ),
        CheckConstraint(
            "health_state IN ('active','degraded')", name="ck_market_acquisition_health"
        ),
    )
    acquisition_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    scope_id: Mapped[str] = mapped_column(String(192))
    slot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    window_start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    window_end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    known_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16))
    consecutive_errors: Mapped[int] = mapped_column(Integer, default=0)
    health_state: Mapped[str] = mapped_column(String(16), default="active")
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    feature_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    feature_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    receipt_json: Mapped[str] = mapped_column(Text)
    receipt_hash: Mapped[str] = mapped_column(String(64))

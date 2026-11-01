"""Additive finite-shadow ledger; model/forecast/observation payloads are immutable."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class RiskShadowTrialRecord(Base):
    __tablename__ = "risk_shadow_trials"
    __table_args__ = (
        CheckConstraint(
            "state IN ('registered','running','paused','draining','completed','failed')",
            name="ck_risk_shadow_trial_state",
        ),
    )
    trial_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(128), unique=True)
    registration_hash: Mapped[str] = mapped_column(String(64))
    protocol_json: Mapped[str] = mapped_column(Text)
    protocol_hash: Mapped[str] = mapped_column(String(64))
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    first_cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_window_end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    label_deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(String(16))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    conclusion: Mapped[str | None] = mapped_column(String(64), nullable=True)


class RiskPathJobRecord(Base):
    __tablename__ = "risk_path_jobs"
    __table_args__ = (
        UniqueConstraint("trial_id", "kind", "scope_id", "cutoff_at", name="uq_risk_path_job"),
        CheckConstraint("kind IN ('fit','forecast','label','finalize')", name="ck_risk_path_kind"),
        CheckConstraint(
            "state IN ('pending','running','succeeded','missed','failed','expired')",
            name="ck_risk_path_job_state",
        ),
        CheckConstraint("lease_generation >= 0 AND attempts >= 0", name="ck_risk_path_lease"),
        Index("ix_risk_path_job_due", "state", "next_attempt_at", "lease_expires_at"),
    )
    job_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    trial_id: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(16))
    scope_id: Mapped[str] = mapped_column(String(192))
    cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(String(16))
    attempts: Mapped[int] = mapped_column(Integer)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_generation: Mapped[int] = mapped_column(Integer)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    checkpoint_json: Mapped[str] = mapped_column(Text)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RiskPredictionRecord(Base):
    __tablename__ = "risk_predictions"
    __table_args__ = (
        UniqueConstraint(
            "trial_id",
            "scope_id",
            "method",
            "cutoff_at",
            "horizon_seconds",
            "target_version",
            name="uq_risk_path_prediction",
        ),
        Index("ix_risk_prediction_scope_time", "scope_id", "cutoff_at"),
    )
    prediction_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    trial_id: Mapped[str] = mapped_column(String(80))
    scope_id: Mapped[str] = mapped_column(String(192))
    method: Mapped[str] = mapped_column(String(64))
    cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    horizon_seconds: Mapped[int] = mapped_column(Integer)
    target_version: Mapped[str] = mapped_column(String(64))
    model_hash: Mapped[str] = mapped_column(String(64))
    feature_manifest_json: Mapped[str] = mapped_column(Text)
    feature_manifest_hash: Mapped[str] = mapped_column(String(64))
    payload_json: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(String(64))


class RiskPathObservationRecord(Base):
    __tablename__ = "risk_path_observations"
    __table_args__ = (
        UniqueConstraint(
            "scope_id",
            "cutoff_at",
            "calculator_version",
            "payload_hash",
            name="uq_risk_path_observation",
        ),
        Index("ix_risk_path_observation_known", "scope_id", "cutoff_at", "label_known_at"),
    )
    observation_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    scope_id: Mapped[str] = mapped_column(String(192))
    cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    window_end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    calculator_version: Mapped[str] = mapped_column(String(64))
    path_hash: Mapped[str] = mapped_column(String(64))
    label_known_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    quality: Mapped[str] = mapped_column(String(32))
    payload_json: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(String(64))


class RiskEvaluationReportRecord(Base):
    __tablename__ = "risk_evaluation_reports"
    __table_args__ = (
        UniqueConstraint("trial_id", "report_version", name="uq_risk_path_evaluation"),
    )
    report_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    trial_id: Mapped[str] = mapped_column(String(80))
    report_version: Mapped[str] = mapped_column(String(64))
    protocol_hash: Mapped[str] = mapped_column(String(64))
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload_json: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(String(64))

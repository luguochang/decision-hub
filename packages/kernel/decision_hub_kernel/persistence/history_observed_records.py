"""New isolated ledgers; no imports or writes to legacy trial/model records."""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class HistoryObservedTrainingRecord(Base):
    __tablename__ = "market_history_training_records"
    __table_args__ = (
        UniqueConstraint("registration_id", "kind", "scope_id", name="uq_history_observed_record"),
        UniqueConstraint("request_id", "kind", "scope_id", name="uq_history_observed_request"),
        CheckConstraint("kind IN ('protocol','dataset','model')", name="ck_history_observed_kind"),
    )
    record_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    registration_id: Mapped[str] = mapped_column(String(80))
    request_id: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(16))
    scope_id: Mapped[str] = mapped_column(String(192))
    payload_json: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(String(64))
    original_ref: Mapped[str] = mapped_column(String(2048))
    original_hash: Mapped[str] = mapped_column(String(64))
    known_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class HistoryObservedAcquisitionJobRecord(Base):
    __tablename__ = "market_history_acquisition_jobs"
    __table_args__ = (
        UniqueConstraint(
            "registration_id", "scope_id", "cutoff_at", name="uq_history_observed_job"
        ),
        CheckConstraint("attempts >= 0", name="ck_history_observed_attempts"),
        CheckConstraint(
            "state IN ('held','pending','running','completed','partial','failed','expired')",
            name="ck_history_observed_state",
        ),
        CheckConstraint("lease_generation >= 0", name="ck_history_observed_lease_generation"),
    )
    job_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    registration_id: Mapped[str] = mapped_column(String(80))
    scope_id: Mapped[str] = mapped_column(String(192))
    cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(String(16))
    attempts: Mapped[int] = mapped_column(Integer)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_generation: Mapped[int] = mapped_column(Integer)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    checkpoint_json: Mapped[str] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    protocol_hash: Mapped[str] = mapped_column(String(64))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

"""Additive V2 originals and bounded missing-page jobs/attempt audit."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class HistoryDatasetV2OriginalRecord(Base):
    __tablename__ = "market_history_v2_originals"
    __table_args__ = (
        UniqueConstraint("kind", "owner_claim", "request_id", name="uq_history_v2_request"),
    )
    original_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))
    owner_claim: Mapped[str] = mapped_column(String(128))
    request_id: Mapped[str] = mapped_column(String(128))
    plan_id: Mapped[str] = mapped_column(String(96), index=True)
    payload_json: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(String(64))
    original_ref: Mapped[str] = mapped_column(String(2048))
    original_hash: Mapped[str] = mapped_column(String(64))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class HistoryMissingV2EpochRecord(Base):
    __tablename__ = "market_history_v2_epochs"
    epoch_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    plan_id: Mapped[str] = mapped_column(String(96), index=True)
    payload_hash: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(24))
    attempts: Mapped[int] = mapped_column(Integer)
    consecutive_transport_errors: Mapped[int] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class HistoryMissingV2JobRecord(Base):
    __tablename__ = "market_history_v2_jobs"
    job_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    epoch_id: Mapped[str] = mapped_column(String(96), index=True)
    plan_id: Mapped[str] = mapped_column(String(96))
    scope_id: Mapped[str] = mapped_column(String(192))
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    cursor: Mapped[str | None] = mapped_column(String(32), nullable=True)
    max_pages: Mapped[int] = mapped_column(Integer)
    page_count: Mapped[int] = mapped_column(Integer)
    attempts: Mapped[int] = mapped_column(Integer)
    retry_used: Mapped[int] = mapped_column(Integer)
    generation: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(24))
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)


class HistoryMissingV2AttemptRecord(Base):
    __tablename__ = "market_history_v2_attempts"
    attempt_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    epoch_id: Mapped[str] = mapped_column(String(96), index=True)
    job_id: Mapped[str] = mapped_column(String(96))
    generation: Mapped[int] = mapped_column(Integer)
    cursor: Mapped[str] = mapped_column(String(32))
    reserved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(String(32))
    capture_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fixture_mode: Mapped[bool] = mapped_column(Boolean)

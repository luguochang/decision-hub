"""Dedicated audit journal; no ProductCommand or publication identity changes."""
from datetime import datetime
from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint, Index, CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base

class ReportAuditJobRecord(Base):
    __tablename__ = "research_report_audit_jobs"
    __table_args__ = (
        UniqueConstraint("run_id", "request_key", name="uq_report_audit_request"),
        Index("ix_report_audit_claim", "state", "created_at", "audit_id"),
        CheckConstraint("native_exited IN (0,1)", name="ck_report_audit_native_exited"),
    )
    audit_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(128), index=True)
    request_key: Mapped[str] = mapped_column(String(64))
    owner_id: Mapped[str] = mapped_column(String(128))
    identity_json: Mapped[str] = mapped_column(Text)
    identity_hash: Mapped[str] = mapped_column(String(64))
    receipt_json: Mapped[str] = mapped_column(Text)
    receipt_hash: Mapped[str] = mapped_column(String(64))
    epoch: Mapped[str] = mapped_column(String(64))
    active_slot: Mapped[str | None] = mapped_column(String(16), unique=True, nullable=True)
    state: Mapped[str] = mapped_column(String(24))
    version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    native_exit_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    native_exited: Mapped[int] = mapped_column(Integer)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reason_code: Mapped[str | None] = mapped_column(String(160), nullable=True)

class ReportAuditConfirmationRecord(Base):
    __tablename__ = "research_report_audit_confirmations"
    audit_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    identity_hash: Mapped[str] = mapped_column(String(64))
    receipt_hash: Mapped[str] = mapped_column(String(64))
    result_hash: Mapped[str] = mapped_column(String(64))
    result_bytes: Mapped[int] = mapped_column(Integer)
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

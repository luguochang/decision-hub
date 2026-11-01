"""Immutable local audit of explicit V2 reconciliation/resumption."""

from datetime import datetime

from sqlalchemy import DateTime, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class HistoryMissingResumeV2Record(Base):
    __tablename__ = "market_history_v2_resume_audits"
    __table_args__ = (
        UniqueConstraint("owner_claim", "request_id", name="uq_history_v2_resume_request"),
    )
    resume_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    owner_claim: Mapped[str] = mapped_column(String(128))
    request_id: Mapped[str] = mapped_column(String(128))
    epoch_id: Mapped[str] = mapped_column(String(96), index=True)
    plan_id: Mapped[str] = mapped_column(String(96))
    payload_json: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(String(64))
    original_ref: Mapped[str] = mapped_column(String(2048))
    original_hash: Mapped[str] = mapped_column(String(64))
    source_json: Mapped[str] = mapped_column(Text)
    source_hash: Mapped[str] = mapped_column(String(64))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

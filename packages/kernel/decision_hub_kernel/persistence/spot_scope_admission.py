"""Immutable, bounded first-trial authorizations; not a product release registry."""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class SpotResearchAdmissionRecord(Base):
    __tablename__ = "spot_research_scope_admissions"
    __table_args__ = (
        CheckConstraint("purpose = 'controlled_trial'", name="ck_spot_admission_purpose"),
        Index("ix_spot_admission_scope_expiry", "research_scope_id", "expires_at"),
    )
    admission_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(256), unique=True)
    research_scope_id: Mapped[str] = mapped_column(String(128))
    path_scope_id: Mapped[str] = mapped_column(String(192))
    instrument: Mapped[str] = mapped_column(String(128))
    purpose: Mapped[str] = mapped_column(String(32))
    binding_hash: Mapped[str] = mapped_column(String(64))
    proof_json: Mapped[str] = mapped_column(Text)
    proof_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

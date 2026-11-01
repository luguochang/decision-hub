"""Immutable authorization-at-creation for one spot research Run."""

from datetime import datetime

from sqlalchemy import DateTime, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class RunSpotAdmissionRecord(Base):
    __tablename__ = "research_run_spot_admissions"
    __table_args__ = (Index("ix_run_spot_admission_source", "admission_id", "created_at"),)
    run_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(128))
    study_id: Mapped[str] = mapped_column(String(128))
    admission_id: Mapped[str] = mapped_column(String(80))
    scope_id: Mapped[str] = mapped_column(String(192))
    proof_hash: Mapped[str] = mapped_column(String(64))
    payload_json: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

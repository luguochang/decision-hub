"""Mutable periodic configuration/cursor; immutable commands stay ProductCommand."""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class PerpetualResearchScheduleRecord(Base):
    __tablename__ = "perpetual_research_schedules"
    __table_args__ = (
        CheckConstraint("state IN ('active','paused')", name="ck_perp_schedule_state"),
        CheckConstraint("state_version >= 1", name="ck_perp_schedule_version"),
        Index("ix_perp_schedule_due", "state", "next_due_at", "schedule_id"),
    )
    schedule_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(128))
    partition_json: Mapped[str] = mapped_column(Text)
    horizon: Mapped[str] = mapped_column(String(8))
    state: Mapped[str] = mapped_column(String(16))
    state_version: Mapped[int] = mapped_column(Integer)
    revision_id: Mapped[str] = mapped_column(String(128))
    revision_hash: Mapped[str] = mapped_column(String(64))
    next_due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

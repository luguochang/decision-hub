"""Operational coverage slots remain visible when input production fails."""
from datetime import datetime
from sqlalchemy import CheckConstraint, DateTime, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base


class EventCoverageRecord(Base):
    __tablename__ = 'event_prevision_coverage'
    __table_args__ = (
        UniqueConstraint('event_key', 'slot_kind', name='uq_event_prevision_coverage_slot'),
        CheckConstraint("state IN ('pending','ready','late','missing','missed')", name='ck_event_prevision_coverage_state'),
        Index('ix_event_prevision_coverage_due', 'due_at', 'state'),
    )
    coverage_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    event_key: Mapped[str] = mapped_column(String(200))
    reference_period: Mapped[str | None] = mapped_column(String(7), nullable=True)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    slot_kind: Mapped[str] = mapped_column(String(16))
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(String(16))
    forecast_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    forecast_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    first_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    notice_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reason_code: Mapped[str | None] = mapped_column(String(128), nullable=True)

"""Append-only actual-result and unique-scenario activation originals."""
from datetime import datetime

from sqlalchemy import DateTime, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class EventRealizationRecord(Base):
    __tablename__ = "event_realizations"
    __table_args__ = (
        UniqueConstraint("forecast_id", "input_hash", name="uq_event_realization_input"),
        Index("ix_event_realization_event_time", "event_key", "known_at", "recorded_at"),
    )
    realization_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    event_key: Mapped[str] = mapped_column(String(200))
    forecast_id: Mapped[str] = mapped_column(String(96), index=True)
    owner_forecast_hash: Mapped[str] = mapped_column(String(64))
    reference_period: Mapped[str] = mapped_column(String(7))
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    known_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    input_hash: Mapped[str] = mapped_column(String(64))
    revision_parent_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    schema_version: Mapped[str] = mapped_column(String(64))
    payload_json: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(String(64))

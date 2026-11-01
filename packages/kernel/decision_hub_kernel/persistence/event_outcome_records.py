"""Append-only event probability scores and price-path paper outcomes."""

from datetime import datetime

from sqlalchemy import DateTime, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class EventOutcomeRecord(Base):
    __tablename__ = "event_outcomes"
    __table_args__ = (
        UniqueConstraint(
            "forecast_id", "realization_id", "input_hash", name="uq_event_outcome_input"
        ),
        Index("ix_event_outcome_event_time", "event_key", "recorded_at"),
    )
    outcome_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    event_key: Mapped[str] = mapped_column(String(200))
    forecast_id: Mapped[str] = mapped_column(String(96), index=True)
    realization_id: Mapped[str] = mapped_column(String(128))
    owner_forecast_hash: Mapped[str] = mapped_column(String(64))
    realization_hash: Mapped[str] = mapped_column(String(64))
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    input_hash: Mapped[str] = mapped_column(String(64))
    revision_parent_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    schema_version: Mapped[str] = mapped_column(String(64))
    payload_json: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(String(64))

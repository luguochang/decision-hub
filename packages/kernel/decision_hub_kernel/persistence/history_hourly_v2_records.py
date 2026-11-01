"""Independent durable slots for the V2 prospective hourly producer."""

import hashlib
from datetime import datetime
from pathlib import Path

from sqlalchemy import Boolean, DateTime, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base

LOADED_SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


class HistoryHourlyV2SlotRecord(Base):
    __tablename__ = "market_history_v2_hourly_slots"
    __table_args__ = (
        UniqueConstraint(
            "owner_claim", "scope_id", "hour_at", name="uq_history_v2_hourly_owner_scope_hour"
        ),
    )

    slot_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    owner_claim: Mapped[str] = mapped_column(String(128))
    scope_id: Mapped[str] = mapped_column(String(192))
    hour_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    model_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    request_id: Mapped[str] = mapped_column(String(128))
    forecast_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    status: Mapped[str] = mapped_column(String(32))
    reason_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    receipt_json: Mapped[str] = mapped_column(Text)
    receipt_hash: Mapped[str] = mapped_column(String(64))
    source_original_ref: Mapped[str] = mapped_column(Text)
    source_original_hash: Mapped[str] = mapped_column(String(64))
    fixture_mode: Mapped[bool] = mapped_column(Boolean)

"""Separate immutable forecast rows; never expand legacy training record kinds."""

import hashlib
from datetime import datetime
from pathlib import Path

from sqlalchemy import DateTime, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base

LOADED_SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


class HistoryObservedForecastRecord(Base):
    __tablename__ = "market_history_forecasts"
    __table_args__ = (
        UniqueConstraint("operator_owner_claim", "request_id", name="uq_history_forecast_request"),
    )
    forecast_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    operator_owner_claim: Mapped[str] = mapped_column(String(128))
    request_id: Mapped[str] = mapped_column(String(128))
    registration_id: Mapped[str] = mapped_column(String(80))
    scope_id: Mapped[str] = mapped_column(String(192))
    model_hash: Mapped[str] = mapped_column(String(64))
    model_known_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    threshold: Mapped[str] = mapped_column(String(8))
    payload_json: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(String(64))
    original_ref: Mapped[str] = mapped_column(String(2048))
    original_hash: Mapped[str] = mapped_column(String(64))
    produced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

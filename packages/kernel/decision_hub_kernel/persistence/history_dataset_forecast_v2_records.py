"""Separate V2 forecast originals; no legacy trial or forecast row changes."""

import hashlib
from datetime import datetime
from pathlib import Path

from sqlalchemy import DateTime, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base

LOADED_SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


class HistoryDatasetForecastV2Record(Base):
    __tablename__ = "market_history_v2_forecasts"
    __table_args__ = (
        UniqueConstraint("owner_claim", "request_id", name="uq_history_v2_forecast_request"),
    )
    forecast_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    owner_claim: Mapped[str] = mapped_column(String(128))
    request_id: Mapped[str] = mapped_column(String(128))
    scope_id: Mapped[str] = mapped_column(String(192))
    model_id: Mapped[str] = mapped_column(String(96))
    model_hash: Mapped[str] = mapped_column(String(64))
    cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload_json: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(String(64))
    original_ref: Mapped[str] = mapped_column(Text)
    original_hash: Mapped[str] = mapped_column(String(64))
    produced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

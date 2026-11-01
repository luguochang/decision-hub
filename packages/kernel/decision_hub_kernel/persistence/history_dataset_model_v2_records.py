"""Independent V2 model storage, never a legacy experiment or prediction row."""

import hashlib
from datetime import datetime
from pathlib import Path

from sqlalchemy import DateTime, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base

LOADED_SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


class HistoryDatasetModelV2Record(Base):
    __tablename__ = "market_history_v2_models"
    __table_args__ = (
        UniqueConstraint(
            "owner_claim", "request_id", "scope_id", name="uq_history_v2_model_request"
        ),
    )
    model_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    owner_claim: Mapped[str] = mapped_column(String(128))
    request_id: Mapped[str] = mapped_column(String(128))
    scope_id: Mapped[str] = mapped_column(String(192))
    plan_id: Mapped[str] = mapped_column(String(96))
    dataset_id: Mapped[str] = mapped_column(String(96))
    payload_json: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(String(64))
    original_ref: Mapped[str] = mapped_column(Text)
    original_hash: Mapped[str] = mapped_column(String(64))
    known_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

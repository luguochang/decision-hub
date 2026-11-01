"""First-save originals plus append-only qualification acceptance/version rows."""

import hashlib
from datetime import datetime
from pathlib import Path

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base

LOADED_SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


class HistoryModelQualificationV3Record(Base):
    __tablename__ = "market_history_model_qualifications_v3"
    __table_args__ = (
        UniqueConstraint(
            "owner_claim", "request_id", "scope_id", name="uq_history_qualification_v3_request"
        ),
    )
    qualification_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    owner_claim: Mapped[str] = mapped_column(String(128))
    request_id: Mapped[str] = mapped_column(String(128))
    scope_id: Mapped[str] = mapped_column(String(192))
    model_id: Mapped[str] = mapped_column(String(96))
    model_hash: Mapped[str] = mapped_column(String(64))
    dataset_id: Mapped[str] = mapped_column(String(96))
    dataset_hash: Mapped[str] = mapped_column(String(64))
    model_known_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    data_information_asof: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    bundle_ref: Mapped[str] = mapped_column(Text)
    bundle_hash: Mapped[str] = mapped_column(String(64))
    manifest_hash: Mapped[str] = mapped_column(String(64))
    payload_json: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(String(64))
    original_ref: Mapped[str] = mapped_column(Text)
    original_hash: Mapped[str] = mapped_column(String(64))
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class HistoryModelQualificationAcceptanceV3Record(Base):
    __tablename__ = "market_history_model_qualification_events_v3"
    __table_args__ = (
        UniqueConstraint("qualification_id", "version", name="uq_history_qualification_v3_version"),
    )
    event_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    qualification_id: Mapped[str] = mapped_column(String(96), index=True)
    qualification_hash: Mapped[str] = mapped_column(String(64))
    owner_claim: Mapped[str] = mapped_column(String(128))
    request_id: Mapped[str] = mapped_column(String(128))
    state: Mapped[str] = mapped_column(String(16))
    version: Mapped[int] = mapped_column(Integer)
    confirmation_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload_json: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(String(64))
    original_ref: Mapped[str] = mapped_column(Text)
    original_hash: Mapped[str] = mapped_column(String(64))


class HistoryQualifiedForecastBindingV3Record(Base):
    __tablename__ = "market_history_qualified_forecast_bindings_v3"
    forecast_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    owner_claim: Mapped[str] = mapped_column(String(128))
    qualification_id: Mapped[str] = mapped_column(String(96))
    qualification_hash: Mapped[str] = mapped_column(String(64))
    qualification_version: Mapped[int] = mapped_column(Integer)
    acceptance_id: Mapped[str] = mapped_column(String(96))
    acceptance_hash: Mapped[str] = mapped_column(String(64))
    bundle_hash: Mapped[str] = mapped_column(String(64))
    forecast_hash: Mapped[str] = mapped_column(String(64))
    payload_json: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(String(64))
    original_ref: Mapped[str] = mapped_column(Text)
    original_hash: Mapped[str] = mapped_column(String(64))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class HistoryQualifiedForecastAcceptanceV3Record(Base):
    __tablename__ = "market_history_qualified_forecast_acceptances_v3"
    forecast_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    owner_claim: Mapped[str] = mapped_column(String(128))
    forecast_hash: Mapped[str] = mapped_column(String(64))
    binding_hash: Mapped[str] = mapped_column(String(64))
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload_json: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(String(64))
    original_ref: Mapped[str] = mapped_column(Text)
    original_hash: Mapped[str] = mapped_column(String(64))

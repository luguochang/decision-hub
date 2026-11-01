"""Uninstalled bundle lifecycle tables; require a migration before wiring."""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, utcnow


class PublicationBundleJobRecord(Base):
    __tablename__ = "research_publication_bundle_jobs"
    run_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(128), unique=True)
    event_id: Mapped[str] = mapped_column(String(128))
    artifact_id: Mapped[str] = mapped_column(String(128), unique=True)
    publication_id: Mapped[str] = mapped_column(String(128), unique=True)
    publication_hash: Mapped[str] = mapped_column(String(64))
    publication_schema: Mapped[str] = mapped_column(String(128))
    publication_created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    generation: Mapped[int] = mapped_column(Integer)
    owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    state: Mapped[str] = mapped_column(String(24))
    bundle_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    state_version: Mapped[int] = mapped_column(Integer, default=0)
    selection_native_json: Mapped[str] = mapped_column(Text, default="")
    selection_native_hash: Mapped[str] = mapped_column(String(64), default="")
    reader_version: Mapped[str] = mapped_column(String(96), default="frozen-publication-reader.v1")
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    reason_code: Mapped[str | None] = mapped_column(String(160), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PublicationBundleRecord(Base):
    __tablename__ = "research_publication_bundles"
    bundle_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(128), unique=True)
    request_id: Mapped[str] = mapped_column(String(128), unique=True)
    generation: Mapped[int] = mapped_column(Integer)
    bundle_ref: Mapped[str] = mapped_column(String(192))
    bundle_bytes: Mapped[int] = mapped_column(Integer)
    material_ref: Mapped[str] = mapped_column(Text)
    material_hash: Mapped[str] = mapped_column(String(64))
    material_bytes: Mapped[int] = mapped_column(Integer)
    report_hash: Mapped[str] = mapped_column(String(64))
    report_bytes: Mapped[int] = mapped_column(Integer)
    reader_hash: Mapped[str] = mapped_column(String(64))
    prepared_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PublicationBundleConfirmationRecord(Base):
    __tablename__ = "research_publication_bundle_confirmations"
    run_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(128), unique=True)
    generation: Mapped[int] = mapped_column(Integer)
    bundle_hash: Mapped[str] = mapped_column(String(64), unique=True)
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PublicationBundleStateEventRecord(Base):
    __tablename__ = "research_publication_bundle_state_events"
    __table_args__ = (UniqueConstraint("run_id", "version", name="uq_publication_bundle_state_version"),)
    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(128), index=True)
    request_id: Mapped[str] = mapped_column(String(128))
    version: Mapped[int] = mapped_column(Integer)
    generation: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(24))
    reason_code: Mapped[str | None] = mapped_column(String(160), nullable=True)
    bundle_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload_json: Mapped[str] = mapped_column(Text)

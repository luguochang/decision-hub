"""Additive B4 records; no reuse of the directional ActivePointer table."""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class MarketCaseRecord(Base):
    __tablename__ = "market_cases"
    __table_args__ = (
        Index("ix_market_case_pit", "market", "instrument", "horizon", "available_at"),
    )
    case_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(256), unique=True)
    outlook_id: Mapped[str] = mapped_column(String(128), index=True)
    market: Mapped[str] = mapped_column(String(64))
    instrument: Mapped[str] = mapped_column(String(128))
    horizon: Mapped[str] = mapped_column(String(32))
    recipe_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload_json: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(String(64))


class MarketCaseRelationRecord(Base):
    __tablename__ = "market_case_relations"
    __table_args__ = (Index("ix_market_case_relation_left", "left_case_id", "created_at"),)
    relation_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(256), unique=True)
    left_case_id: Mapped[str] = mapped_column(String(128))
    right_case_id: Mapped[str] = mapped_column(String(128), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload_json: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(String(64))


class MarketComponentVersionRecord(Base):
    __tablename__ = "market_component_versions"
    __table_args__ = (Index("ix_market_component_scope", "kind", "component_ref", "scope_id"),)
    version_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    component_ref: Mapped[str] = mapped_column(String(128))
    scope_id: Mapped[str] = mapped_column(String(192))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload_json: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(String(64))


class MarketComponentShadowRecord(Base):
    __tablename__ = "market_component_shadows"
    trial_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    version_id: Mapped[str] = mapped_column(String(128), index=True)
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload_json: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(String(64))


class MarketComponentReviewRecord(Base):
    __tablename__ = "market_component_reviews"
    review_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(256), unique=True)
    version_id: Mapped[str] = mapped_column(String(128), index=True)
    trial_id: Mapped[str] = mapped_column(String(128))
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload_json: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(String(64))


class MarketComponentPointerRecord(Base):
    __tablename__ = "market_component_pointers"
    __table_args__ = (
        UniqueConstraint("kind", "component_ref", "scope_id", name="uq_market_component_pointer"),
        CheckConstraint("generation >= 1", name="ck_market_component_generation"),
    )
    pointer_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    component_ref: Mapped[str] = mapped_column(String(128))
    scope_id: Mapped[str] = mapped_column(String(192))
    version_id: Mapped[str] = mapped_column(String(128))
    generation: Mapped[int] = mapped_column(Integer)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    audit_id: Mapped[str] = mapped_column(String(128))


class MarketComponentAuditRecord(Base):
    __tablename__ = "market_component_audits"
    audit_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(256), unique=True)
    pointer_id: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload_json: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(String(64))


class MarketExtensionRegistrationRecord(Base):
    __tablename__ = "market_extension_registrations"
    registration_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(256), unique=True)
    scope_id: Mapped[str] = mapped_column(String(192), index=True)
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    manifest_json: Mapped[str] = mapped_column(Text)
    manifest_hash: Mapped[str] = mapped_column(String(64))


class MarketExtensionAdmissionRecord(Base):
    __tablename__ = "market_extension_admissions"
    admission_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    registration_id: Mapped[str] = mapped_column(String(128), index=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload_json: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(String(64))

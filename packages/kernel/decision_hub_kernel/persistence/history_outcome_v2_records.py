"""Additive outcome originals and bounded metadata; acceptance is a separate row.

No recorded_at or database default proves post-COMMIT acceptance. The service
must read a subject on an independent connection, save that real clock in an
acceptance original, and bind a null pointer once with a fresh writer.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base

LOADED_SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


class _OutcomeOriginalFields:
    """Immutable original fields shared by the five independent tables."""

    owner_claim: Mapped[str] = mapped_column(String(128))
    request_id: Mapped[str] = mapped_column(String(128))
    payload_json: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(String(64))
    original_ref: Mapped[str] = mapped_column(Text)
    original_hash: Mapped[str] = mapped_column(String(64))
    source_files_json: Mapped[str] = mapped_column(Text)
    source_original_ref: Mapped[str] = mapped_column(Text)
    source_original_hash: Mapped[str] = mapped_column(String(64))
    produced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    fixture_mode: Mapped[bool] = mapped_column(Boolean)


class HistoryOutcomeProtocolV2Record(_OutcomeOriginalFields, Base):
    __tablename__ = "market_history_v2_outcome_protocols"
    __table_args__ = (
        UniqueConstraint("owner_claim", "request_id", name="uq_history_outcome_protocol_request"),
        Index("ix_history_outcome_protocol_owner_hour", "owner_claim", "first_hour"),
        CheckConstraint("opportunity_count = 48", name="ck_history_outcome_48_opportunities"),
        CheckConstraint(
            "(confirmed_at IS NULL AND confirmation_id IS NULL "
            "AND confirmation_original_ref IS NULL AND confirmation_original_hash IS NULL "
            "AND confirmation_payload_hash IS NULL) OR "
            "(confirmed_at IS NOT NULL AND confirmation_id IS NOT NULL "
            "AND confirmation_original_ref IS NOT NULL AND confirmation_original_hash IS NOT NULL "
            "AND confirmation_payload_hash IS NOT NULL)",
            name="ck_history_outcome_protocol_confirmation",
        ),
        CheckConstraint(
            "confirmed_at IS NULL OR (recorded_at <= confirmed_at AND confirmed_at < first_hour)",
            name="ck_history_outcome_protocol_confirmation_clock",
        ),
    )

    protocol_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    first_hour: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_hour: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    opportunity_count: Mapped[int] = mapped_column(Integer)
    # Independent protocol readback R; service binds this complete tuple once.
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmation_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    confirmation_original_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    confirmation_original_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confirmation_payload_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)


class HistoryOutcomeOpportunityV2Record(_OutcomeOriginalFields, Base):
    __tablename__ = "market_history_v2_outcome_opportunities"
    __table_args__ = (
        UniqueConstraint(
            "owner_claim", "request_id", name="uq_history_outcome_opportunity_request"
        ),
        UniqueConstraint(
            "protocol_id", "scope_id", "hour_at", name="uq_history_outcome_protocol_scope_hour"
        ),
        Index("ix_history_outcome_due", "state", "next_due_at", "lease_expires_at"),
        Index("ix_history_outcome_owner_protocol", "owner_claim", "protocol_id", "hour_at"),
        CheckConstraint("attempts >= 0 AND attempts <= 4", name="ck_history_outcome_attempts"),
        CheckConstraint("generation >= 0", name="ck_history_outcome_generation"),
        CheckConstraint(
            "(lease_owner IS NULL AND lease_expires_at IS NULL) OR "
            "(lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="ck_history_outcome_lease_pair",
        ),
        CheckConstraint(
            "(label_cutoff_at IS NULL AND label_anchor_basis IS NULL) OR "
            "(label_cutoff_at IS NOT NULL AND label_anchor_basis IS NOT NULL "
            "AND label_anchor_basis IN "
            "('actual_first_dispatch','scheduled_counterfactual'))",
            name="ck_history_outcome_label_binding",
        ),
        CheckConstraint(
            "(first_accepted_observation_id IS NULL AND first_acceptance_id IS NULL) OR "
            "(first_accepted_observation_id IS NOT NULL AND first_acceptance_id IS NOT NULL)",
            name="ck_history_outcome_first_acceptance_pair",
        ),
    )

    opportunity_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    protocol_id: Mapped[str] = mapped_column(String(96))
    scope_id: Mapped[str] = mapped_column(String(192))
    model_id: Mapped[str] = mapped_column(String(96))
    hour_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    dispatch_deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Only execution metadata and null-to-first binding pointers are mutable.
    label_cutoff_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    label_anchor_basis: Mapped[str | None] = mapped_column(String(32), nullable=True)
    first_publication_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    first_accepted_observation_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    first_acceptance_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    state: Mapped[str] = mapped_column(String(32))
    attempts: Mapped[int] = mapped_column(Integer)
    generation: Mapped[int] = mapped_column(Integer)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    label_deadline_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)


class HistoryOutcomePublicationV2Record(_OutcomeOriginalFields, Base):
    __tablename__ = "market_history_v2_outcome_publications"
    __table_args__ = (
        UniqueConstraint(
            "owner_claim", "request_id", name="uq_history_outcome_publication_request"
        ),
        UniqueConstraint("kind", "subject_id", name="uq_history_outcome_publication_acceptance"),
        UniqueConstraint(
            "opportunity_id",
            "kind",
            "binding_version",
            name="uq_history_outcome_first_publication",
        ),
        Index("ix_history_outcome_publication_owner_protocol", "owner_claim", "protocol_id"),
        Index("ix_history_outcome_publication_opportunity", "opportunity_id", "recorded_at"),
        CheckConstraint(
            "(kind = 'publication' AND accepted_kind IS NULL AND subject_id IS NULL "
            "AND independently_verified_at IS NULL AND opportunity_id IS NOT NULL "
            "AND scope_id IS NOT NULL AND model_id IS NOT NULL AND hour_at IS NOT NULL) OR "
            "(kind = 'acceptance' AND accepted_kind IS NOT NULL "
            "AND accepted_kind IN ('publication','protocol') "
            "AND subject_id IS NOT NULL AND independently_verified_at IS NOT NULL)",
            name="ck_history_outcome_publication_kind",
        ),
        CheckConstraint(
            "binding_version IS NULL OR binding_version = 1",
            name="ck_history_outcome_first_binding_version",
        ),
        CheckConstraint(
            "independently_verified_at IS NULL OR "
            "(independently_verified_at <= produced_at AND produced_at <= recorded_at)",
            name="ck_history_outcome_publication_acceptance_clock",
        ),
    )

    publication_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    protocol_id: Mapped[str] = mapped_column(String(96))
    opportunity_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    scope_id: Mapped[str | None] = mapped_column(String(192), nullable=True)
    model_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    hour_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    kind: Mapped[str] = mapped_column(String(16))
    accepted_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    subject_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    independently_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    binding_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    classification: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reason_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    dispatch_slot_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    forecast_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    actual_cutoff_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    label_cutoff_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class HistoryOutcomeObservationV2Record(_OutcomeOriginalFields, Base):
    __tablename__ = "market_history_v2_outcome_observations"
    __table_args__ = (
        UniqueConstraint(
            "owner_claim", "request_id", name="uq_history_outcome_observation_request"
        ),
        UniqueConstraint("kind", "subject_id", name="uq_history_outcome_observation_acceptance"),
        UniqueConstraint(
            "opportunity_id", "kind", "attempt", name="uq_history_outcome_observation_attempt"
        ),
        Index("ix_history_outcome_observation_owner_protocol", "owner_claim", "protocol_id"),
        Index("ix_history_outcome_observation_opportunity", "opportunity_id", "recorded_at"),
        CheckConstraint(
            "attempt >= 1 AND attempt <= 4", name="ck_history_outcome_observation_attempt"
        ),
        CheckConstraint(
            "(kind = 'observation' AND accepted_kind IS NULL AND subject_id IS NULL "
            "AND independently_verified_at IS NULL) OR "
            "(kind = 'acceptance' AND accepted_kind IS NOT NULL "
            "AND accepted_kind = 'observation' AND subject_id IS NOT NULL "
            "AND independently_verified_at IS NOT NULL)",
            name="ck_history_outcome_observation_kind",
        ),
        CheckConstraint(
            "independently_verified_at IS NULL OR "
            "(independently_verified_at <= produced_at AND produced_at <= recorded_at)",
            name="ck_history_outcome_observation_acceptance_clock",
        ),
    )

    observation_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    protocol_id: Mapped[str] = mapped_column(String(96))
    opportunity_id: Mapped[str] = mapped_column(String(96))
    publication_id: Mapped[str] = mapped_column(String(96))
    scope_id: Mapped[str] = mapped_column(String(192))
    kind: Mapped[str] = mapped_column(String(16))
    accepted_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    subject_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    independently_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    attempt: Mapped[int] = mapped_column(Integer)
    classification: Mapped[str] = mapped_column(String(32))
    reason_code: Mapped[str] = mapped_column(String(128))
    label_cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    window_start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    window_end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    label_deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class HistoryOutcomeScoreV2Record(_OutcomeOriginalFields, Base):
    __tablename__ = "market_history_v2_outcome_scores"
    __table_args__ = (
        UniqueConstraint("owner_claim", "request_id", name="uq_history_outcome_score_request"),
        Index(
            "ix_history_outcome_score_owner_protocol", "owner_claim", "protocol_id", "actual_as_of"
        ),
    )

    score_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    protocol_id: Mapped[str] = mapped_column(String(96))
    actual_as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True))

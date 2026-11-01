from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, overload

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    or_,
    select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from packages.contracts_py.decision_hub_contracts.models import (
    ArtifactView,
    CallView,
    Direction,
    EvaluationView,
    EvidenceLineageView,
    Forecast,
    GateDecision,
    GateStatus,
    OrchestrationLineageView,
    RunInspectorView,
    RunStatus,
    RunTimelineItemView,
    RunView,
    SourceHealth,
    SourceManifest,
    StepView,
    VersionLineageView,
)


def utcnow() -> datetime:
    return datetime.now(UTC)


@overload
def as_utc(value: None) -> None: ...


@overload
def as_utc(value: datetime) -> datetime: ...


def as_utc(value: datetime | None) -> datetime | None:
    """Normalize SQLite's timezone-less datetime values at the public boundary."""
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class Base(DeclarativeBase):
    pass


class UsageCollectionRecord(Base):
    __tablename__ = "usage_collection_jobs"
    job_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(128), index=True)
    session_id: Mapped[str] = mapped_column(String(256))
    generation: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    next_check_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_view_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_snapshot_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ResearchUsageSnapshotRecord(Base):
    """Immutable metadata-only model usage reconciliations, separate from reports."""

    __tablename__ = "research_usage_snapshots"
    snapshot_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(128), index=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload_sha256: Mapped[str] = mapped_column(String(64))
    payload_json: Mapped[str] = mapped_column(Text)


class EventRecord(Base):
    __tablename__ = "events"
    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(128), default="macro_event")
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    generation: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(32), default="admitted")


class ObservationRecord(Base):
    __tablename__ = "observations"
    observation_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(128), index=True)
    source_id: Mapped[str] = mapped_column(String(128))
    source_type: Mapped[str] = mapped_column(String(32))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    language: Mapped[str] = mapped_column(String(16))
    text: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64), unique=True)
    source_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    event_hint: Mapped[str | None] = mapped_column(String(128), nullable=True)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revision_of: Mapped[str | None] = mapped_column(String(128), nullable=True)


class SnapshotRecord(Base):
    __tablename__ = "snapshots"
    snapshot_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(128), index=True)
    cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    snapshot_hash: Mapped[str] = mapped_column(String(64))
    evidence_json: Mapped[str] = mapped_column(Text)
    pack_version: Mapped[str] = mapped_column(String(64), default="crypto_macro.v1")
    snapshot_type: Mapped[str] = mapped_column(String(32), default="trigger")
    run_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    parent_snapshot_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    generation: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class EventWatchRecord(Base):
    """Durable schedule for a known future event and its comparison window."""

    __tablename__ = "event_watches"
    __table_args__ = (UniqueConstraint("event_id", name="uq_event_watches_event_id"),)

    watch_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(128), index=True)
    source_id: Mapped[str] = mapped_column(String(128), index=True)
    event_family: Mapped[str] = mapped_column(String(128))
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(32), default="scheduled", index=True)
    window_offsets_json: Mapped[str] = mapped_column(Text)
    baseline_status: Mapped[str] = mapped_column(String(32), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    next_tick_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )


class MacroEventRecord(Base):
    """Point-in-time macro event ledger; calendar, expectation and release stay separate."""

    __tablename__ = "macro_events"
    __table_args__ = (
        UniqueConstraint(
            "source_id",
            "external_event_id",
            name="uq_macro_events_source_external",
        ),
    )

    macro_event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(128), index=True)
    external_event_id: Mapped[str] = mapped_column(String(256))
    family: Mapped[str] = mapped_column(String(128), index=True)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    source_id: Mapped[str] = mapped_column(String(128), index=True)
    source_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    scheduled_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    time_precision: Mapped[str] = mapped_column(String(16), default="unknown")
    source_timezone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    correction_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_authority: Mapped[str] = mapped_column(String(32), default="official")
    unit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    prior_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    consensus_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    actual_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    vintage_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revision_flag: Mapped[bool] = mapped_column(default=False)
    surprise: Mapped[float | None] = mapped_column(Float, nullable=True)
    surprise_z: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="scheduled", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CalendarCorrectionRecord(Base):
    """Auditable parser correction, distinct from an official event cancellation."""

    __tablename__ = "calendar_corrections"
    correction_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(128), index=True)
    parser_version: Mapped[str] = mapped_column(String(128))
    source_content_hash: Mapped[str] = mapped_column(String(64))
    source_content_ref: Mapped[str] = mapped_column(String(2048))
    reason: Mapped[str] = mapped_column(String(256))
    before_json: Mapped[str] = mapped_column(Text)
    invalidated_ids_json: Mapped[str] = mapped_column(Text)
    verified_ids_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MacroEventVintageRecord(Base):
    """Append-only point-in-time values for one macro event revision."""

    __tablename__ = "macro_event_vintages"
    __table_args__ = (
        UniqueConstraint(
            "macro_event_id",
            "payload_hash",
            name="uq_macro_event_vintages_payload",
        ),
    )

    vintage_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    macro_event_id: Mapped[str] = mapped_column(String(128), index=True)
    event_id: Mapped[str] = mapped_column(String(128), index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    prior_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scheduled_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    time_precision: Mapped[str] = mapped_column(String(16), default="unknown")
    source_timezone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    consensus_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    actual_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    surprise: Mapped[float | None] = mapped_column(Float, nullable=True)
    surprise_z: Mapped[float | None] = mapped_column(Float, nullable=True)
    revision_flag: Mapped[bool] = mapped_column(default=False)
    status: Mapped[str] = mapped_column(String(32))
    payload_hash: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EventWindowSampleRecord(Base):
    """One idempotent sample slot; payloads stay outside the Kernel ledger."""

    __tablename__ = "event_window_samples"
    __table_args__ = (
        UniqueConstraint("watch_id", "offset", name="uq_event_window_samples_offset"),
    )

    sample_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    watch_id: Mapped[str] = mapped_column(String(128), index=True)
    event_id: Mapped[str] = mapped_column(String(128), index=True)
    offset: Mapped[str] = mapped_column(String(32))
    target_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    provider_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    payload_ref: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    payload_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)


class ResearchEvidenceRecord(Base):
    __tablename__ = "research_evidence"
    evidence_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(128), index=True)
    capability_id: Mapped[str] = mapped_column(String(128), index=True)
    requirement_id: Mapped[str] = mapped_column(String(128), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    authority: Mapped[str] = mapped_column(String(32))
    source_id: Mapped[str] = mapped_column(String(256))
    source_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    excerpt: Mapped[str] = mapped_column(Text)
    content_ref: Mapped[str | None] = mapped_column(String(2048), nullable=True, index=True)
    acquisition_receipt_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True, index=True
    )
    source_content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    parser_ref: Mapped[str | None] = mapped_column(String(256), nullable=True)
    structured_payload_ref: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    tool_call_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    research_session_id: Mapped[str] = mapped_column(String(256), index=True)
    round: Mapped[int] = mapped_column(Integer)
    quality: Mapped[str] = mapped_column(String(32))
    freshness_status: Mapped[str] = mapped_column(String(32))
    conflict_group: Mapped[str | None] = mapped_column(String(128), nullable=True)
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SourceAcquisitionRecord(Base):
    """Durable metadata for browser/HTTP acquisition receipts.

    Raw browser artifacts stay in the content-addressed artifact store.  The
    Kernel only stores the immutable receipt and its canonical hash so a
    later Evidence admission can prove which acquisition produced a claim.
    """

    __tablename__ = "source_acquisition_receipts"

    receipt_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(128), index=True)
    url: Mapped[str] = mapped_column(String(2048))
    access_mode: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), index=True)
    request_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    request_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    observed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    received_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    artifacts_json: Mapped[str] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(String(200), nullable=True)
    receipt_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ResearchDocumentRecord(Base):
    __tablename__ = "research_documents"

    document_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    title: Mapped[str] = mapped_column(String(512))
    source_type: Mapped[str] = mapped_column(String(32))
    source_profile_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    author: Mapped[str | None] = mapped_column(String(256), nullable=True)
    market_scopes_json: Mapped[str] = mapped_column(Text)
    instrument_scopes_json: Mapped[str] = mapped_column(Text)
    privacy_mode: Mapped[str] = mapped_column(String(32), default="local_only")
    visibility: Mapped[str] = mapped_column(String(32), default="private")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    latest_version_id: Mapped[str | None] = mapped_column(String(128), nullable=True)


class ResearchDocumentVersionRecord(Base):
    __tablename__ = "research_document_versions"

    version_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    document_id: Mapped[str] = mapped_column(String(128), index=True)
    original_file_ref: Mapped[str] = mapped_column(String(2048))
    original_file_hash: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), default="received", index=True)
    correction_status: Mapped[str] = mapped_column(String(32), default="not_requested", index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    supersedes_version_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    parse_receipt_ref: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    corrected_content_ref: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    block_count: Mapped[int] = mapped_column(Integer, default=0)
    claim_count: Mapped[int] = mapped_column(Integer, default=0)
    quality_json: Mapped[str] = mapped_column(Text, default="{}")
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ResearchDocumentBlockRecord(Base):
    __tablename__ = "research_document_blocks"

    block_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    version_id: Mapped[str] = mapped_column(String(128), index=True)
    block_type: Mapped[str] = mapped_column(String(32))
    page_no: Mapped[int] = mapped_column(Integer, index=True)
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    locator: Mapped[str] = mapped_column(String(2048))
    bbox_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float] = mapped_column(Float)
    has_numeric_content: Mapped[bool] = mapped_column(default=False)
    needs_review: Mapped[bool] = mapped_column(default=False, index=True)


class ResearchDocumentCorrectionRecord(Base):
    __tablename__ = "research_document_corrections"

    correction_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    version_id: Mapped[str] = mapped_column(String(128), index=True)
    correction_model_ref: Mapped[str] = mapped_column(String(256))
    prompt_ref: Mapped[str] = mapped_column(String(256))
    corrected_content_ref: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    quality_status: Mapped[str] = mapped_column(String(32), index=True)
    changes_json: Mapped[str] = mapped_column(Text, default="[]")
    unresolved_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AnalystClaimRecord(Base):
    __tablename__ = "analyst_claims"

    claim_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    version_id: Mapped[str] = mapped_column(String(128), index=True)
    claim_type: Mapped[str] = mapped_column(String(32))
    stance: Mapped[str] = mapped_column(String(32))
    claim_text: Mapped[str] = mapped_column(Text)
    supporting_quote: Mapped[str] = mapped_column(Text)
    locator_refs_json: Mapped[str] = mapped_column(Text)
    market_scope: Mapped[str] = mapped_column(String(64), index=True)
    instrument_scope_json: Mapped[str] = mapped_column(Text)
    horizon: Mapped[str | None] = mapped_column(String(64), nullable=True)
    conditions_json: Mapped[str] = mapped_column(Text)
    invalidation_conditions_json: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="candidate", index=True)
    attribution_confidence: Mapped[str] = mapped_column(String(32))
    requires_independent_evidence: Mapped[bool] = mapped_column(default=True)
    review_reason: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ResearchRunScopeRecord(Base):
    __tablename__ = "research_run_scopes"

    run_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    research_family: Mapped[str] = mapped_column(String(64), index=True)
    domain_pack_ref: Mapped[str] = mapped_column(String(128))
    instrument_scope_json: Mapped[str] = mapped_column(Text)
    library_context_mode: Mapped[str] = mapped_column(String(32))
    selected_version_ids_json: Mapped[str] = mapped_column(Text)
    allow_remote_library_content: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0"
    )
    input_receipts_json: Mapped[str] = mapped_column(Text, default="[]", server_default="[]")
    request_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DataJobRecord(Base):
    """Owner-triggered history load; it is deliberately independent of Runs."""

    __tablename__ = "data_jobs"
    job_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(256), unique=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    requested_by: Mapped[str] = mapped_column(String(32))
    dataset_ids_json: Mapped[str] = mapped_column(Text)
    window_start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    window_end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    partition_refs_json: Mapped[str] = mapped_column(Text, default="[]")
    coverage_manifest_ref: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    errors_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ResearchFactRecord(Base):
    """Canonical typed fact linked to one immutable EvidenceCandidate."""

    __tablename__ = "research_facts"
    fact_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(128), index=True)
    capability_id: Mapped[str] = mapped_column(String(128), index=True)
    evidence_id: Mapped[str] = mapped_column(String(128), index=True)
    requirement_id: Mapped[str] = mapped_column(String(128), index=True)
    metric_family: Mapped[str] = mapped_column(String(128), index=True)
    field: Mapped[str] = mapped_column(String(128), index=True)
    instrument: Mapped[str | None] = mapped_column(String(128), nullable=True)
    venue: Mapped[str | None] = mapped_column(String(128), nullable=True)
    value_json: Mapped[str] = mapped_column(Text)
    unit: Mapped[str] = mapped_column(String(64))
    window_start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    window_end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    event_offset: Mapped[str | None] = mapped_column(String(64), nullable=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_id: Mapped[str] = mapped_column(String(256), index=True)
    independence_group: Mapped[str] = mapped_column(String(256), index=True)
    quality: Mapped[str] = mapped_column(String(32))
    delay_class: Mapped[str] = mapped_column(String(32))
    payload_schema_ref: Mapped[str] = mapped_column(String(256))
    payload_hash: Mapped[str] = mapped_column(String(64), index=True)
    attributes_json: Mapped[str] = mapped_column(Text)
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RawCaptureRecord(Base):
    __tablename__ = "raw_captures"
    __table_args__ = (
        Index("ix_raw_capture_body_identity", "body_ref", "body_hash", unique=False),
    )
    capture_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    context_id: Mapped[str] = mapped_column(String(256), index=True)
    url: Mapped[str] = mapped_column(String(2048))
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    body_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    body_ref: Mapped[str | None] = mapped_column(String(256), nullable=True)
    byte_count: Mapped[int] = mapped_column(Integer)
    media_type: Mapped[str] = mapped_column(String(128))
    adapter_version: Mapped[str] = mapped_column(String(128))
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ParsedVersionRecord(Base):
    __tablename__ = "parsed_versions"
    parsed_version_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    original_evidence_id: Mapped[str] = mapped_column(String(128))
    content_hash: Mapped[str] = mapped_column(String(64))
    parser_version: Mapped[str] = mapped_column(String(256))
    capture_ids_json: Mapped[str] = mapped_column(Text)
    content_ref: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    acquisition_receipt_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    archive_status: Mapped[str] = mapped_column(String(32))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EvidenceParsedLinkRecord(Base):
    __tablename__ = "evidence_parsed_links"
    evidence_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    parsed_version_id: Mapped[str] = mapped_column(String(128), index=True)


class FactVersionRecord(Base):
    __tablename__ = "fact_versions"
    __table_args__ = (
        Index("ix_fact_versions_pit", "market", "scope_instrument", "known_at"),
        Index("ix_fact_versions_semantic", "semantic_key", "known_at"),
    )
    fact_version_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    semantic_key: Mapped[str] = mapped_column(String(64))
    market: Mapped[str] = mapped_column(String(64))
    scope_instrument: Mapped[str] = mapped_column(String(128))
    field: Mapped[str] = mapped_column(String(128), index=True)
    source_id: Mapped[str] = mapped_column(String(256), index=True)
    original_fact_id: Mapped[str] = mapped_column(String(128))
    parsed_version_id: Mapped[str] = mapped_column(String(128), index=True)
    payload_json: Mapped[str] = mapped_column(Text)
    known_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RunInputRefRecord(Base):
    __tablename__ = "run_input_refs"
    fact_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(128), index=True)
    fact_version_id: Mapped[str] = mapped_column(String(128), index=True)
    use_mode: Mapped[str] = mapped_column(String(32))
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    adopted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AnalyticalOutlookRecord(Base):
    __tablename__ = "analytical_outlooks"
    __table_args__ = (
        UniqueConstraint(
            "run_id", "method_version", "horizon", name="uq_outlook_run_method_horizon"
        ),
        Index("ix_outlooks_cohort", "market", "instrument", "method_version", "horizon"),
    )
    outlook_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(128), index=True)
    artifact_id: Mapped[str] = mapped_column(String(128), index=True)
    study_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    market: Mapped[str] = mapped_column(String(64))
    instrument: Mapped[str] = mapped_column(String(128))
    method_family: Mapped[str] = mapped_column(String(64))
    method_version: Mapped[str] = mapped_column(String(256))
    horizon: Mapped[str] = mapped_column(String(16))
    action: Mapped[str] = mapped_column(String(32))
    subjective_probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    probability_status: Mapped[str] = mapped_column(String(32))
    data_as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    window_start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    window_end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    result_hash: Mapped[str] = mapped_column(String(64))
    evidence_refs_json: Mapped[str] = mapped_column(Text)
    fact_version_refs_json: Mapped[str] = mapped_column(Text)
    raw_payload_json: Mapped[str] = mapped_column(Text)
    trigger: Mapped[str] = mapped_column(Text)
    invalidation: Mapped[str] = mapped_column(Text)
    cohort: Mapped[str] = mapped_column(String(32))
    eligibility: Mapped[str] = mapped_column(String(32))
    exclusion_reasons_json: Mapped[str] = mapped_column(Text)
    research_quality: Mapped[str] = mapped_column(String(32))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PublicationDecisionRecord(Base):
    __tablename__ = "publication_decisions"
    artifact_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(128), index=True)
    status: Mapped[str] = mapped_column(String(32))
    result_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decisions_json: Mapped[str] = mapped_column(Text)
    published_horizons_json: Mapped[str] = mapped_column(Text)
    review_refs_json: Mapped[str] = mapped_column(Text)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ExecutionPermissionRecord(Base):
    __tablename__ = "execution_permissions"
    artifact_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    allowed: Mapped[bool] = mapped_column(Boolean)
    policy_version: Mapped[str] = mapped_column(String(128))
    reasons_json: Mapped[str] = mapped_column(Text)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AnalyticalObservationRecord(Base):
    __tablename__ = "analytical_observations"
    outlook_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    market_return_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    directional_return_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    direction_correct: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quality_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    benchmark: Mapped[str | None] = mapped_column(String(64), nullable=True)
    window_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)


class AnalyticalObservationAttemptRecord(Base):
    __tablename__ = "analytical_observation_attempts"
    outlook_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    attempt: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(String(32))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)


class AnalyticalAssessmentJobRecord(Base):
    __tablename__ = "analytical_assessment_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'retry', 'completed')",
            name="ck_analytical_assessment_job_status",
        ),
        CheckConstraint("attempt >= 0", name="ck_analytical_assessment_job_attempt"),
        CheckConstraint(
            "status != 'running' OR "
            "(lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="ck_analytical_assessment_job_running_lease",
        ),
        CheckConstraint(
            "status != 'completed' OR "
            "(evaluated_at IS NOT NULL AND basis_json IS NOT NULL AND basis_hash IS NOT NULL "
            "AND assessment_json IS NOT NULL AND assessment_hash IS NOT NULL)",
            name="ck_analytical_assessment_job_completed_projection",
        ),
        Index("ix_assessment_jobs_status_due", "status", "next_attempt_at"),
        Index("ix_assessment_jobs_lease_expires_at", "lease_expires_at"),
    )
    outlook_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    status: Mapped[str] = mapped_column(String(32))
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verifier_version: Mapped[str] = mapped_column(String(128))
    basis_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    basis_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    assessment_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    assessment_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)


class CapabilityReuseRecord(Base):
    __tablename__ = "capability_reuse_batches"
    batch_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    query_key: Mapped[str] = mapped_column(String(64), index=True)
    run_id: Mapped[str] = mapped_column(String(128))
    result_json: Mapped[str] = mapped_column(Text)
    fact_versions_json: Mapped[str] = mapped_column(Text)
    parsed_versions_json: Mapped[str] = mapped_column(Text)
    known_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ResearchResultRecord(Base):
    __tablename__ = "research_results"
    run_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    schema_version: Mapped[str] = mapped_column(String(64))
    payload_json: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ResearchTraceRecord(Base):
    __tablename__ = "research_trace_events"
    __table_args__ = (
        UniqueConstraint("run_id", "sequence_no", name="uq_research_trace_sequence"),
        UniqueConstraint("run_id", "event_hash", name="uq_research_trace_hash"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(128), index=True)
    research_session_id: Mapped[str] = mapped_column(String(256), index=True)
    sequence_no: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(64))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    stage: Mapped[str] = mapped_column(String(64))
    summary: Mapped[str] = mapped_column(Text)
    reference_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reference_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    status: Mapped[str] = mapped_column(String(32))
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_provenance_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ResearchCommandRecord(Base):
    __tablename__ = "research_commands"
    request_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(128), index=True)
    command: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str] = mapped_column(Text)
    target_run_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ResearchToolCallReservationRecord(Base):
    __tablename__ = "research_tool_call_reservations"
    run_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    research_session_id: Mapped[str] = mapped_column(String(256), nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    capability_id: Mapped[str] = mapped_column(String(128), nullable=False)
    query_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    reserved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DshSessionLinkRecord(Base):
    __tablename__ = "dsh_session_links"
    __table_args__ = (UniqueConstraint("dsh_session_id", name="uq_dsh_session_links_session_id"),)
    run_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    dsh_session_id: Mapped[str] = mapped_column(String(256), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    upstream_identity_json: Mapped[str] = mapped_column(Text, nullable=False)
    plugin_build_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # Nullable for rows created before the durable deadline migration. The
    # gateway rejects such rows instead of guessing a time boundary.
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    max_tool_calls: Mapped[int | None] = mapped_column(Integer, nullable=True)
    model_step_timeout_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tool_calls_started: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    terminal_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    trace_ref: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    result_ref: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    result_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DshSessionPromptRecord(Base):
    __tablename__ = "dsh_session_prompts"
    run_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    generation: Mapped[int] = mapped_column(Integer, primary_key=True)
    dsh_session_id: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    request_id: Mapped[str] = mapped_column(String(256), nullable=False, unique=True)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RunRecord(Base):
    __tablename__ = "runs"
    run_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(128), index=True)
    study_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(256), unique=True, nullable=True)
    snapshot_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    decision_snapshot_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default=RunStatus.admitted.value)
    strategy_version: Mapped[str] = mapped_column(String(64), default="baseline.v1")
    runtime_version: Mapped[str] = mapped_column(String(64), default="fake.v1")
    admission_origin: Mapped[str] = mapped_column(String(32), default="manual", index=True)
    priority: Mapped[int] = mapped_column(Integer, default=100, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    artifact_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    available_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    parent_run_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class OccurrenceRecord(Base):
    """Stable identity only; the existing macro ledger remains the calendar authority."""

    __tablename__ = "occurrences"
    occurrence_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    identity_key: Mapped[str] = mapped_column(String(64), unique=True)
    identity_basis: Mapped[str] = mapped_column(String(64))
    family: Mapped[str] = mapped_column(String(128))
    source_id: Mapped[str] = mapped_column(String(256))
    macro_event_id: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EventOccurrenceRecord(Base):
    __tablename__ = "event_occurrences"
    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    occurrence_id: Mapped[str] = mapped_column(String(128), index=True)
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    basis: Mapped[str] = mapped_column(String(64))


class EventWatchLinkRecord(Base):
    __tablename__ = "event_watch_links"
    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    watch_id: Mapped[str] = mapped_column(String(128), index=True)
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class StudyRecord(Base):
    __tablename__ = "studies"
    study_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    scope_key: Mapped[str] = mapped_column(String(64), unique=True)
    title: Mapped[str] = mapped_column(String(512))
    market: Mapped[str] = mapped_column(String(64))
    instrument: Mapped[str] = mapped_column(String(128))
    horizon_scope: Mapped[str] = mapped_column(String(128))
    question_key: Mapped[str] = mapped_column(String(256))
    identity_basis: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class StudyOccurrenceRecord(Base):
    __tablename__ = "study_occurrences"
    study_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    occurrence_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ReportRevisionRecord(Base):
    __tablename__ = "report_revisions"
    revision_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    study_id: Mapped[str] = mapped_column(String(128), index=True)
    source_run_id: Mapped[str] = mapped_column(String(128), unique=True)
    artifact_id: Mapped[str] = mapped_column(String(128), unique=True)
    previous_revision_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    strategy_version: Mapped[str] = mapped_column(String(64))
    input_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    publication_status: Mapped[str] = mapped_column(String(32))
    change_json: Mapped[str] = mapped_column(Text)
    identity_basis: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AdmissionDecisionRecord(Base):
    __tablename__ = "admission_decisions"
    decision_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    attempt_id: Mapped[str] = mapped_column(String(128), index=True)
    event_id: Mapped[str] = mapped_column(String(128), index=True)
    study_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    source_id: Mapped[str] = mapped_column(String(128), index=True)
    strategy_version: Mapped[str] = mapped_column(String(64))
    policy_version: Mapped[str] = mapped_column(String(128))
    decision: Mapped[str] = mapped_column(String(32))
    reason_code: Mapped[str] = mapped_column(String(128))
    details_json: Mapped[str] = mapped_column(Text)
    run_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class DiscoveryAdmissionRecoveryJobRecord(Base):
    """Mutable slots for immutable source admission originals; never an event."""

    __tablename__ = "discovery_admission_recovery_jobs"
    __table_args__ = (
        Index("ix_discovery_recovery_due", "status", "next_check_at", "job_id"),
        CheckConstraint(
            "status IN ('pending', 'completed', 'closed')",
            name="ck_discovery_recovery_status",
        ),
        CheckConstraint("attempts >= 0", name="ck_discovery_recovery_attempts"),
    )

    job_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    root_decision_id: Mapped[str] = mapped_column(String(128), unique=True)
    contract_sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))
    next_check_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer)
    terminal_decision_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ReviewScheduleRecord(Base):
    """Durable trigger plan for a child research recheck."""

    __tablename__ = "review_schedules"
    __table_args__ = (UniqueConstraint("trigger_hash", name="uq_review_schedule_trigger"),)

    review_plan_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    parent_run_id: Mapped[str] = mapped_column(String(128), index=True)
    horizon: Mapped[str] = mapped_column(String(16), index=True)
    trigger_type: Mapped[str] = mapped_column(String(32), index=True)
    trigger_ref: Mapped[str] = mapped_column(String(256))
    trigger_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    trigger_hash: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(32), default="planned", index=True)
    child_run_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    failure_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RunEventRecord(Base):
    __tablename__ = "run_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(128), index=True)
    sequence_no: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(128))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload_json: Mapped[str] = mapped_column(Text, default="{}")


class RunCallRecord(Base):
    __tablename__ = "run_calls"
    call_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(128), index=True)
    role: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="running")
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    runtime_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    runtime_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    api_mode: Mapped[str | None] = mapped_column(String(32), nullable=True)
    schema_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_status: Mapped[str] = mapped_column(String(32), default="unknown")
    pricing_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    retryable: Mapped[bool] = mapped_column(default=False)


class RunStepRecord(Base):
    __tablename__ = "run_steps"
    step_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(128), index=True)
    step_name: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="running")
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)


class RunRoleOutputRecord(Base):
    __tablename__ = "run_role_outputs"
    __table_args__ = (UniqueConstraint("run_id", "role", name="uq_run_role_output"),)
    output_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(128), index=True)
    role: Mapped[str] = mapped_column(String(64))
    payload_json: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(String(64))
    schema_version: Mapped[str] = mapped_column(String(64), default="agent-payload.v1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ArtifactRecord(Base):
    __tablename__ = "artifacts"
    artifact_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(128), index=True)
    event_id: Mapped[str] = mapped_column(String(128), index=True)
    gate_status: Mapped[str] = mapped_column(String(32))
    headline: Mapped[str] = mapped_column(String(512))
    summary: Mapped[str] = mapped_column(Text)
    payload_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ForecastRecord(Base):
    __tablename__ = "forecasts"
    forecast_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    artifact_id: Mapped[str] = mapped_column(String(128), index=True)
    instrument: Mapped[str] = mapped_column(String(128))
    horizon: Mapped[str] = mapped_column(String(16))
    direction: Mapped[str] = mapped_column(String(16))
    probability: Mapped[float] = mapped_column(Float)
    trigger: Mapped[str] = mapped_column(Text)
    invalidation: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    forecasted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    as_of: Mapped[str | None] = mapped_column(String(64), nullable=True)
    strategy_version: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    input_snapshot_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    input_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    feature_snapshot_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_cutoff_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    calibration_receipt_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True, index=True
    )


class StrategyCalibrationRecord(Base):
    """Immutable kernel-owned evidence that a strategy passed walk-forward evaluation."""

    __tablename__ = "strategy_calibrations"
    __table_args__ = (
        UniqueConstraint(
            "strategy_version",
            "horizon",
            "dataset_hash",
            name="uq_strategy_calibration_dataset",
        ),
    )

    receipt_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    strategy_version: Mapped[str] = mapped_column(String(128), index=True)
    horizon: Mapped[str] = mapped_column(String(16), index=True)
    dataset_hash: Mapped[str] = mapped_column(String(64), index=True)
    sample_count: Mapped[int] = mapped_column(Integer)
    evaluated_sample_count: Mapped[int] = mapped_column(Integer)
    fold_count: Mapped[int] = mapped_column(Integer)
    accuracy: Mapped[float] = mapped_column(Float)
    accuracy_lower_bound: Mapped[float] = mapped_column(Float)
    brier_score: Mapped[float] = mapped_column(Float)
    baseline_brier_score: Mapped[float] = mapped_column(Float)
    log_loss: Mapped[float] = mapped_column(Float)
    net_return_pct: Mapped[float] = mapped_column(Float)
    max_drawdown_pct: Mapped[float] = mapped_column(Float)
    coverage_ratio: Mapped[float] = mapped_column(Float)
    max_regime_share: Mapped[float] = mapped_column(Float)
    calibration_slope: Mapped[float] = mapped_column(Float)
    calibration_intercept: Mapped[float] = mapped_column(Float)
    passed: Mapped[bool] = mapped_column(default=False, index=True)
    failure_reasons_json: Mapped[str] = mapped_column(Text)
    report_json: Mapped[str] = mapped_column(Text)
    report_hash: Mapped[str] = mapped_column(String(64), unique=True)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class StrategyCalibrationJobRecord(Base):
    """Immutable sampling plan with only its owned retry/lease state mutable."""
    __tablename__ = "strategy_calibration_jobs"
    __table_args__ = (Index("ix_calibration_jobs_due", "status", "next_attempt_at", "job_id"),)
    job_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(256), unique=True)
    plan_json: Mapped[str] = mapped_column(Text)
    plan_hash: Mapped[str] = mapped_column(String(64))
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_production_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)


class StrategyCalibrationProductionRecord(Base):
    """Append-only full authenticated inputs and the exact unextended report."""
    __tablename__ = "strategy_calibration_productions"
    __table_args__ = (UniqueConstraint("job_id", "inputs_hash", "producer_hash", name="uq_calibration_production_inputs"),)
    production_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    job_id: Mapped[str] = mapped_column(String(128), index=True)
    scope_hash: Mapped[str] = mapped_column(String(64), index=True)
    inputs_hash: Mapped[str] = mapped_column(String(64))
    producer_hash: Mapped[str] = mapped_column(String(64))
    sample_dataset_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    server_known_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(String(32), index=True)
    payload_json: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(String(64), unique=True)
    report_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    report_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)


class StrategyProbabilityActivationRecord(Base):
    """An explicit immutable interval and exact production IDs, never latest."""
    __tablename__ = "strategy_probability_activations"
    __table_args__ = (Index("ix_probability_activation_scope", "market", "instrument", "pack_definition_hash", "compiled_policy_recipe_hash", "valid_from", "valid_until"),)
    activation_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(256), unique=True)
    market: Mapped[str] = mapped_column(String(64))
    instrument: Mapped[str] = mapped_column(String(128))
    pack_definition_hash: Mapped[str] = mapped_column(String(64))
    compiled_policy_recipe_hash: Mapped[str] = mapped_column(String(64))
    server_known_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload_json: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(String(64), unique=True)


class GateDecisionRecord(Base):
    __tablename__ = "gate_decisions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(128), index=True)
    artifact_id: Mapped[str] = mapped_column(String(128), index=True)
    rule_id: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32))
    reason_code: Mapped[str] = mapped_column(String(128))
    input_hash: Mapped[str] = mapped_column(String(64))


class OutcomeRecord(Base):
    __tablename__ = "outcomes"
    outcome_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    forecast_id: Mapped[str] = mapped_column(String(128), index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    direction_correct: Mapped[bool] = mapped_column(default=False)
    return_pct: Mapped[float] = mapped_column(Float, default=0)
    fees: Mapped[float] = mapped_column(Float, default=0)
    slippage: Mapped[float] = mapped_column(Float, default=0)
    quality_status: Mapped[str] = mapped_column(String(32), default="estimated")


class EvaluationRecord(Base):
    __tablename__ = "evaluations"
    evaluation_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    forecast_id: Mapped[str] = mapped_column(String(128), index=True)
    brier_score: Mapped[float] = mapped_column(Float)
    net_return_pct: Mapped[float] = mapped_column(Float)
    direction_correct: Mapped[bool] = mapped_column(default=False)
    label_status: Mapped[str] = mapped_column(String(32))
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ResearchValueEvaluationRecord(Base):
    """Append-only run-level value evaluation; directional labels stay separate."""

    __tablename__ = "research_value_evaluations"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "evaluation_version",
            name="uq_research_value_evaluation_version",
        ),
    )

    evaluation_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(128), index=True)
    artifact_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    evaluation_version: Mapped[str] = mapped_column(String(128))
    payload_json: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(String(64), unique=True)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class RevisionFeedbackEvaluationRecord(Base):
    """One immutable available/unavailable evaluation attachment per saved opinion."""

    __tablename__ = "revision_feedback_evaluations"
    __table_args__ = (
        Index("ix_revision_feedback_owner_revision", "owner_id", "revision_id"),
        CheckConstraint("status IN ('available', 'unavailable')",
                        name="ck_revision_feedback_status"),
        CheckConstraint(
            "(status = 'available' AND reason_code IS NULL AND evaluation_id IS NOT NULL "
            "AND value_payload_hash IS NOT NULL) OR "
            "(status = 'unavailable' AND reason_code IS NOT NULL AND evaluation_id IS NULL "
            "AND value_payload_hash IS NULL)",
            name="ck_revision_feedback_value_presence",
        ),
        {"sqlite_autoincrement": True},
    )

    binding_seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    binding_id: Mapped[str] = mapped_column(String(128), unique=True)
    feedback_id: Mapped[str] = mapped_column(String(128), unique=True)
    request_id: Mapped[str] = mapped_column(String(256), unique=True)
    owner_id: Mapped[str] = mapped_column(String(128))
    revision_id: Mapped[str] = mapped_column(String(128))
    run_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    artifact_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    schema_version: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32))
    reason_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    evaluation_id: Mapped[str | None] = mapped_column(String(128), unique=True, nullable=True)
    evaluation_version: Mapped[str] = mapped_column(String(128))
    value_payload_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    basis_json: Mapped[str] = mapped_column(Text)
    receipt_json: Mapped[str] = mapped_column(Text)
    basis_hash: Mapped[str] = mapped_column(String(64))
    receipt_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ProductReadReceiptRecord(Base):
    __tablename__ = "product_read_receipts"
    revision_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ProductCommandRecord(Base):
    __tablename__ = "product_commands"
    request_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(128))
    target_id: Mapped[str] = mapped_column(String(128), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    payload_hash: Mapped[str] = mapped_column(String(64))
    result_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class StudyEffectTrackingRequestJobRecord(Base):
    """Durable acceptance; completed ProductCommand remains the final ACK."""

    __tablename__ = "study_effect_tracking_request_jobs"
    __table_args__ = (
        Index("ix_effect_tracking_requests_due", "state", "next_attempt_at", "request_id"),
        Index("ix_effect_tracking_requests_owner_study", "owner_id", "study_id", "accepted_at"),
        CheckConstraint("state IN ('accepted', 'running', 'retrying', 'completed', 'failed')",
                        name="ck_effect_tracking_request_state"),
        CheckConstraint("attempts >= 0 AND attempts <= 5", name="ck_effect_tracking_request_attempts"),
    )

    request_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(128))
    study_id: Mapped[str] = mapped_column(String(128))
    revision_id: Mapped[str] = mapped_column(String(128))
    source_run_id: Mapped[str] = mapped_column(String(128))
    artifact_id: Mapped[str] = mapped_column(String(128))
    payload_json: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(String(64))
    source_json: Mapped[str] = mapped_column(Text)
    source_hash: Mapped[str] = mapped_column(String(64))
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(String(32), default="accepted")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)


class OutboxRecord(Base):
    __tablename__ = "outbox"
    __table_args__ = (
        CheckConstraint(
            "(subject_kind = 'report' AND artifact_id IS NOT NULL "
            "AND (subject_id IS NULL OR subject_id = artifact_id)) OR "
            "(subject_kind IN ('risk_alert', 'action_revision', 'event_opportunity') AND artifact_id IS NULL "
            "AND subject_id IS NOT NULL AND payload_json IS NOT NULL "
            "AND payload_hash IS NOT NULL)",
            name="ck_outbox_subject_identity",
        ),
        CheckConstraint("claim_generation >= 0", name="ck_outbox_claim_generation"),
        Index("ix_outbox_due_claim", "sent_at", "failed_at", "next_attempt_at", "claim_expires_at"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    artifact_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    subject_kind: Mapped[str] = mapped_column(String(32), default="report", server_default="report")
    subject_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    channel: Mapped[str] = mapped_column(String(64), default="local")
    dedupe_key: Mapped[str] = mapped_column(String(256), unique=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    dispatch_status: Mapped[str] = mapped_column(String(32), default="queued", server_default="queued")
    claim_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    claim_generation: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    claim_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    provider_accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempt_history_json: Mapped[str] = mapped_column(Text, default="[]", server_default="[]")


class SourceStateRecord(Base):
    __tablename__ = "source_states"
    source_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    source_type: Mapped[str] = mapped_column(String(64))
    manifest_version: Mapped[str] = mapped_column(String(64))
    cursor: Mapped[str | None] = mapped_column(Text, nullable=True)
    poll_owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    poll_lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), default="unknown")
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    next_poll_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SourcePollAttemptRecord(Base):
    __tablename__ = "source_poll_attempts"
    __table_args__ = (
        Index("ix_source_poll_attempts_source_started", "source_id", "started_at"),
    )

    attempt_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)
    trigger_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cursor_before: Mapped[str | None] = mapped_column(Text, nullable=True)
    cursor_after: Mapped[str | None] = mapped_column(Text, nullable=True)
    received_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    accepted_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duplicate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    revision_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    content_snapshot_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class ResearchMemoRecord(Base):
    __tablename__ = "research_memos"
    memo_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(256), unique=True)
    run_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    snapshot_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    domain_pack_ref: Mapped[str] = mapped_column(String(128))
    created_by: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), default="submitted")
    content_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class FeedbackRecord(Base):
    __tablename__ = "workbench_feedback"
    feedback_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(256), unique=True)
    target_type: Mapped[str] = mapped_column(String(32))
    target_id: Mapped[str] = mapped_column(String(128), index=True)
    created_by: Mapped[str] = mapped_column(String(128))
    verdict: Mapped[str] = mapped_column(String(32))
    notes: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CapabilityRecord(Base):
    __tablename__ = "capability_manifests"
    capability_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    version: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="discovered")
    manifest_json: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EvaluationDatasetRecord(Base):
    __tablename__ = "evaluation_datasets"
    dataset_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    split: Mapped[str] = mapped_column(String(32))
    manifest_hash: Mapped[str] = mapped_column(String(64), unique=True)
    manifest_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CandidateVersionRecord(Base):
    __tablename__ = "candidate_versions"
    execution_scope: Mapped[str | None] = mapped_column(String(32), nullable=True)
    candidate_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    candidate_type: Mapped[str] = mapped_column(String(32))
    content_hash: Mapped[str] = mapped_column(String(64), unique=True)
    version: Mapped[str] = mapped_column(String(64))
    parent_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="candidate")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    content_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)
    source: Mapped[str] = mapped_column(String(64), default="owner")


class ExperimentRecord(Base):
    __tablename__ = "experiments"
    execution_scope: Mapped[str | None] = mapped_column(String(32), nullable=True)
    experiment_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(String(128), index=True)
    baseline_ref: Mapped[str] = mapped_column(String(128))
    candidate_refs_json: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="registered")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    strategy_version: Mapped[str] = mapped_column(String(64), default="baseline.v1")
    runtime_id: Mapped[str] = mapped_column(String(64), default="replay")
    runtime_version: Mapped[str] = mapped_column(String(64), default="replay.v1")
    provider_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    schema_version: Mapped[str] = mapped_column(String(64), default="experiment.v1")
    random_seed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    randomness_policy: Mapped[str] = mapped_column(String(32), default="deterministic")
    deadline_seconds: Mapped[int] = mapped_column(Integer, default=300)
    max_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)


class ExperimentResultRecord(Base):
    __tablename__ = "experiment_results"
    result_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    experiment_id: Mapped[str] = mapped_column(String(128), index=True)
    candidate_id: Mapped[str] = mapped_column(String(128), index=True)
    sample_count: Mapped[int] = mapped_column(Integer)
    brier_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    p95_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    safety_violations: Mapped[int] = mapped_column(Integer, default=0)
    event_family_counts_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    stage: Mapped[str | None] = mapped_column(String(32), nullable=True)
    failure_counts_json: Mapped[str] = mapped_column(Text, default="{}")
    evidence_coverage: Mapped[float | None] = mapped_column(Float, nullable=True)
    directional_accuracy: Mapped[float | None] = mapped_column(Float, nullable=True)
    raw_artifact_refs_json: Mapped[str] = mapped_column(Text, default="[]")
    scorer_version: Mapped[str] = mapped_column(String(64), default="evaluation.v1")


class EvaluationStageReceiptRecord(Base):
    __tablename__ = "evaluation_stage_receipts"
    experiment_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    binding_hash: Mapped[str] = mapped_column(String(64))
    binding_json: Mapped[str] = mapped_column(Text)
    completion_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    completion_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ActivePointerRecord(Base):
    __tablename__ = "active_pointers"
    pointer_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    domain_pack_ref: Mapped[str] = mapped_column(String(128), unique=True)
    candidate_id: Mapped[str] = mapped_column(String(128))
    generation: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PromotionDecisionRecord(Base):
    __tablename__ = "promotion_decisions"
    decision_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(256), unique=True)
    domain_pack_ref: Mapped[str] = mapped_column(String(128), index=True)
    candidate_id: Mapped[str] = mapped_column(String(128), index=True)
    owner: Mapped[str] = mapped_column(String(128))
    decision: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str] = mapped_column(Text)
    evaluation_refs_json: Mapped[str] = mapped_column(Text)
    previous_candidate_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    resulting_candidate_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    resulting_generation: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class FailurePatternRecord(Base):
    __tablename__ = "failure_patterns"
    pattern_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    failure_code: Mapped[str] = mapped_column(String(128), unique=True)
    occurrence_count: Mapped[int] = mapped_column(Integer)
    impact: Mapped[str] = mapped_column(Text)
    source_refs_json: Mapped[str] = mapped_column(Text, default="[]")
    root_cause_hypothesis: Mapped[str | None] = mapped_column(Text, nullable=True)
    remediation_refs_json: Mapped[str] = mapped_column(Text, default="[]")
    status: Mapped[str] = mapped_column(String(32), default="open")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ExperienceRecord(Base):
    __tablename__ = "experiences"
    experience_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(256), unique=True)
    domain_pack_ref: Mapped[str] = mapped_column(String(128), index=True)
    event_family: Mapped[str] = mapped_column(String(128), index=True)
    lesson: Mapped[str] = mapped_column(Text)
    applicable_conditions_json: Mapped[str] = mapped_column(Text, default="[]")
    evidence_refs_json: Mapped[str] = mapped_column(Text, default="[]")
    outcome_refs_json: Mapped[str] = mapped_column(Text, default="[]")
    evaluation_refs_json: Mapped[str] = mapped_column(Text, default="[]")
    source_type: Mapped[str] = mapped_column(String(32))
    created_by: Mapped[str] = mapped_column(String(128))
    content_hash: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(32), default="candidate")
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EvolutionPlanningDecisionRecord(Base):
    __tablename__ = "evolution_planning_decisions"
    job_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    context_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    content_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class EvolutionContextSnapshotRecord(Base):
    __tablename__ = "evolution_context_snapshots"
    job_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    content_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EvolutionPlanningCallRecord(Base):
    __tablename__ = "evolution_planning_calls"
    call_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    job_id: Mapped[str] = mapped_column(String(128), index=True)
    job_attempt: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32))
    details_json: Mapped[str] = mapped_column(Text)


class EvolutionJobRecord(Base):
    __tablename__ = "evolution_jobs"
    job_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    schema_version: Mapped[str] = mapped_column(String(64))
    trigger_key: Mapped[str] = mapped_column(String(512), unique=True, index=True)
    trigger_type: Mapped[str] = mapped_column(String(32))
    domain_pack_ref: Mapped[str] = mapped_column(String(128), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    stage: Mapped[str] = mapped_column(String(32))
    input_refs_json: Mapped[str] = mapped_column(Text)
    candidate_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    experiment_refs_json: Mapped[str] = mapped_column(Text)
    result_refs_json: Mapped[str] = mapped_column(Text)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ServiceRuntimeConfigRecord(Base):
    __tablename__ = "service_runtime_configs"
    config_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    service_id: Mapped[str] = mapped_column(String(128), index=True)
    instance_id: Mapped[str] = mapped_column(String(128))
    process_id: Mapped[int] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    fingerprint: Mapped[str] = mapped_column(String(64))
    settings_json: Mapped[str] = mapped_column(Text)


class ServiceHeartbeatRecord(Base):
    __tablename__ = "service_heartbeats"
    service_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    role: Mapped[str] = mapped_column(String(32))
    instance_id: Mapped[str] = mapped_column(String(128))
    version: Mapped[str] = mapped_column(String(64))
    mode: Mapped[str] = mapped_column(String(64))
    interval_seconds: Mapped[float] = mapped_column(Float)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)


class ProspectiveTrialRecord(Base):
    __tablename__ = "prospective_trials"
    trial_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(128), unique=True)
    definition_hash: Mapped[str] = mapped_column(String(64))
    definition_json: Mapped[str] = mapped_column(Text)
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ProspectivePairRecord(Base):
    __tablename__ = "prospective_pairs"
    __table_args__ = (UniqueConstraint("trial_id", "study_id", name="uq_trial_study_pair"),)
    pair_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    trial_id: Mapped[str] = mapped_column(String(80), index=True)
    study_id: Mapped[str] = mapped_column(String(128))
    outlook_id: Mapped[str] = mapped_column(String(128))
    input_json: Mapped[str] = mapped_column(Text)
    input_hash: Mapped[str] = mapped_column(String(64))
    source_recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    admitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    window_end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    exclusion_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)


class ProspectiveArmRecord(Base):
    __tablename__ = "prospective_arms"
    pair_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    arm: Mapped[str] = mapped_column(String(16), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    recipe_hash: Mapped[str] = mapped_column(String(64))
    owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)


class ProspectiveLabelRecord(Base):
    __tablename__ = "prospective_labels"
    pair_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    attempt: Mapped[int] = mapped_column(Integer)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    lease_owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)


class ProspectiveLabelAttemptRecord(Base):
    __tablename__ = "prospective_label_attempts"
    pair_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    attempt: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(String(32))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)


class Database:
    def __init__(self, url: str | None = None) -> None:
        data_dir = Path(os.getenv("DECISION_HUB_DATA_DIR", "data/decision-hub"))
        data_dir.mkdir(parents=True, exist_ok=True)
        db_path = data_dir / "db" / "decision_hub.sqlite3"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(
            url or f"sqlite+pysqlite:///{db_path}", connect_args={"check_same_thread": False}
        )
        if self.engine.dialect.name == "sqlite":
            event.listen(self.engine, "connect", _configure_sqlite)
        self.factory = sessionmaker(self.engine, expire_on_commit=False)

    def create_all(self) -> None:
        from . import (  # noqa: F401
            action_execution_records,
            action_quote_repository,
            action_quote_sampling_records,
            action_records,
            decision_settings_records,
            event_coverage_records,
            event_monitor_records,
            event_outcome_records,
            event_realization_records,
            event_scenario_records,
            history_observed_records,
            market_case_records,
            path_forecast_records,
            perpetual_paper_records,
            research_quality_records,
            research_learning_records,
            research_skill_behavior,
            research_method_trial,
            research_method_trial_selection,
            research_skill_callback_jobs,
            risk_records,
            risk_research_records,
            run_spot_admission,
            spot_scope_admission,
        )

        Base.metadata.create_all(self.engine)

    def initialize(self) -> None:
        """Apply the checked-in Alembic head for an application startup."""
        from alembic import command

        config = self._alembic_config()
        # Embedded migrations must preserve the API/worker's logging handlers.
        config.attributes["configure_logger"] = False
        command.upgrade(config, "head")

    def expected_migration_heads(self) -> tuple[str, ...]:
        """Return the checked-in Alembic heads instead of duplicating a revision constant."""
        from alembic.script import ScriptDirectory

        return tuple(ScriptDirectory.from_config(self._alembic_config()).get_heads())

    def _alembic_config(self):
        from alembic.config import Config

        config = Config(str(Path(__file__).resolve().parents[4] / "alembic.ini"))
        config.set_main_option(
            "sqlalchemy.url",
            self.engine.url.render_as_string(hide_password=False),
        )
        return config

    @contextmanager
    def session(self) -> Generator[Session, None, None]:
        session = self.factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get_run_view(self, run_id: str) -> RunView | None:
        with self.session() as session:
            run = session.get(RunRecord, run_id)
            if not run:
                return None
            artifact = session.get(ArtifactRecord, run.artifact_id) if run.artifact_id else None
            return _ledger_run_view(run, artifact)

    def get_run_record(self, run_id: str) -> RunRecord | None:
        with self.session() as session:
            return session.get(RunRecord, run_id)

    def get_event_type(self, event_id: str) -> str | None:
        with self.session() as session:
            event = session.get(EventRecord, event_id)
            return event.event_type if event is not None else None

    @property
    def sqlite_path(self) -> Path | None:
        database = self.engine.url.database
        if self.engine.dialect.name != "sqlite" or not database or database == ":memory:":
            return None
        return Path(database)

    def get_snapshot_evidence(self, snapshot_id: str) -> list[dict[str, object]]:
        with self.session() as session:
            snapshot = session.get(SnapshotRecord, snapshot_id)
            if not snapshot:
                raise KeyError(snapshot_id)
            payload = json.loads(snapshot.evidence_json)
        if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
            raise ValueError("snapshot evidence is invalid")
        return payload

    def save_role_output(
        self, run_id: str, role: str, payload: dict[str, object], schema_version: str
    ) -> str:
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        payload_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        with self.session() as session:
            existing = (
                session.query(RunRoleOutputRecord).filter_by(run_id=run_id, role=role).first()
            )
            if existing:
                return existing.output_id
            output_id = f"out_{__import__('uuid').uuid4().hex}"
            session.add(
                RunRoleOutputRecord(
                    output_id=output_id,
                    run_id=run_id,
                    role=role,
                    payload_json=serialized,
                    payload_hash=payload_hash,
                    schema_version=schema_version,
                    created_at=utcnow(),
                )
            )
            return output_id

    def get_role_output(self, output_id: str) -> dict[str, object]:
        with self.session() as session:
            output = session.get(RunRoleOutputRecord, output_id)
            if not output:
                raise KeyError(output_id)
            payload = json.loads(output.payload_json)
        if not isinstance(payload, dict):
            raise ValueError("role output is invalid")
        return payload

    def find_role_output(self, run_id: str, role: str) -> tuple[str, dict[str, object]] | None:
        with self.session() as session:
            output = session.query(RunRoleOutputRecord).filter_by(run_id=run_id, role=role).first()
            if not output:
                return None
            payload = json.loads(output.payload_json)
        if not isinstance(payload, dict):
            raise ValueError("role output is invalid")
        return output.output_id, payload

    def find_role_output_envelope(
        self, run_id: str, role: str
    ) -> tuple[str, dict[str, object], str] | None:
        """Return a role output with its durable provenance schema."""

        with self.session() as session:
            output = session.query(RunRoleOutputRecord).filter_by(run_id=run_id, role=role).first()
            if not output:
                return None
            payload = json.loads(output.payload_json)
        if not isinstance(payload, dict):
            raise ValueError("role output is invalid")
        return output.output_id, payload, output.schema_version

    def record_run_event(
        self,
        run_id: str,
        sequence_no: int,
        event_type: str,
        payload: dict[str, object] | None = None,
    ) -> None:
        with self.session() as session:
            existing = (
                session.query(RunEventRecord)
                .filter_by(run_id=run_id, event_type=event_type)
                .first()
            )
            if existing:
                return
            session.add(
                RunEventRecord(
                    run_id=run_id,
                    sequence_no=sequence_no,
                    event_type=event_type,
                    occurred_at=utcnow(),
                    payload_json=json.dumps(payload or {}, ensure_ascii=False),
                )
            )

    def ensure_source_state(self, manifest: SourceManifest) -> None:
        with self.session() as session:
            existing = session.get(SourceStateRecord, manifest.source_id)
            if existing:
                return
            session.add(
                SourceStateRecord(
                    source_id=manifest.source_id,
                    source_type=manifest.source_type,
                    manifest_version=manifest.version,
                    updated_at=utcnow(),
                )
            )

    def get_source_health(self, source_id: str) -> SourceHealth:
        with self.session() as session:
            state = session.get(SourceStateRecord, source_id)
            if not state:
                raise KeyError(source_id)
            return SourceHealth(
                source_id=state.source_id,
                status=state.status,
                cursor=state.cursor,
                last_success_at=as_utc(state.last_success_at),
                last_error_at=as_utc(state.last_error_at),
                consecutive_failures=state.consecutive_failures,
                latency_ms=state.latency_ms,
                error_code=state.error_code,
                next_poll_at=as_utc(state.next_poll_at),
            )

    def list_source_health(self) -> list[SourceHealth]:
        with self.session() as session:
            rows = (
                session.query(SourceStateRecord).order_by(SourceStateRecord.source_id.asc()).all()
            )
            return [
                SourceHealth(
                    source_id=row.source_id,
                    status=row.status,
                    cursor=row.cursor,
                    last_success_at=as_utc(row.last_success_at),
                    last_error_at=as_utc(row.last_error_at),
                    consecutive_failures=row.consecutive_failures,
                    latency_ms=row.latency_ms,
                    error_code=row.error_code,
                    next_poll_at=as_utc(row.next_poll_at),
                )
                for row in rows
            ]

    def update_source_success(
        self,
        source_id: str,
        cursor: str | None,
        latency_ms: int,
        *,
        next_poll_at: datetime | None = None,
    ) -> None:
        with self.session() as session:
            state = session.get(SourceStateRecord, source_id)
            if not state:
                raise KeyError(source_id)
            now = utcnow()
            state.cursor = cursor
            state.status = "healthy"
            state.last_success_at = now
            state.consecutive_failures = 0
            state.latency_ms = latency_ms
            state.error_code = None
            state.next_poll_at = next_poll_at
            state.updated_at = now

    def update_source_failure(
        self,
        source_id: str,
        error_code: str,
        latency_ms: int,
        *,
        next_poll_at: datetime | None = None,
    ) -> None:
        with self.session() as session:
            state = session.get(SourceStateRecord, source_id)
            if not state:
                raise KeyError(source_id)
            now = utcnow()
            state.status = "degraded"
            state.last_error_at = now
            state.consecutive_failures += 1
            state.latency_ms = latency_ms
            state.error_code = error_code
            state.next_poll_at = next_poll_at
            state.updated_at = now

    def update_source_disabled(
        self, source_id: str, *, next_poll_at: datetime | None = None
    ) -> None:
        with self.session() as session:
            state = session.get(SourceStateRecord, source_id)
            if not state:
                raise KeyError(source_id)
            now = utcnow()
            state.status = "disabled"
            state.consecutive_failures = 0
            state.latency_ms = 0
            state.error_code = "source_disabled"
            state.next_poll_at = next_poll_at
            state.updated_at = now

    def recovery_candidates(self) -> list[str]:
        with self.session() as session:
            rows = session.scalars(
                select(RunRecord)
                .where(RunRecord.status.in_([RunStatus.admitted.value, RunStatus.running.value]))
                .order_by(
                    RunRecord.priority.desc(),
                    RunRecord.created_at.asc(),
                    RunRecord.run_id.asc(),
                )
            ).all()
            return [row.run_id for row in rows]

    def get_artifact_view(self, artifact_id: str) -> ArtifactView | None:
        with self.session() as session:
            artifact = session.get(ArtifactRecord, artifact_id)
            if not artifact:
                return None
            forecasts = session.scalars(
                select(ForecastRecord).where(ForecastRecord.artifact_id == artifact_id)
            ).all()
            gates = session.scalars(
                select(GateDecisionRecord).where(GateDecisionRecord.artifact_id == artifact_id)
            ).all()
            payload = json.loads(artifact.payload_json)
            return ArtifactView(
                artifact_id=artifact.artifact_id,
                run_id=artifact.run_id,
                event_id=artifact.event_id,
                gate_status=GateStatus(artifact.gate_status),
                headline=artifact.headline,
                summary=artifact.summary,
                facts=payload.get("facts", []),
                inferences=payload.get("inferences", []),
                counter_thesis=payload.get("counter_thesis", ""),
                uncertainty=payload.get("uncertainty", []),
                transmission_chain=payload.get("transmission_chain", []),
                citations=payload.get("citations", []),
                forecasts=[
                    Forecast(
                        forecast_id=f.forecast_id,
                        artifact_id=f.artifact_id,
                        instrument=f.instrument,
                        horizon=f.horizon,
                        direction=Direction(f.direction),
                        probability=f.probability,
                        trigger=f.trigger,
                        invalidation=f.invalidation,
                        expires_at=f.expires_at,
                    )
                    for f in forecasts
                ],
                gate_decisions=[
                    GateDecision(
                        rule_id=g.rule_id,
                        status=g.status,
                        reason_code=g.reason_code,
                        input_hash=g.input_hash,
                    )
                    for g in gates
                ],
                created_at=artifact.created_at,
            )

    def latest_runs(self, limit: int = 20) -> list[RunView]:
        return self.ledger_runs(limit=limit)[0]

    def ledger_runs(
        self, *, limit: int = 20, offset: int = 0,
        created_before: datetime | None = None,
        status: str | None = None, kind: str | None = None, q: str | None = None,
    ) -> tuple[list[RunView], int]:
        """Page every execution family with a shared list/detail projection."""
        with self.session() as session:
            query = session.query(RunRecord, ArtifactRecord).outerjoin(
                ArtifactRecord, ArtifactRecord.artifact_id == RunRecord.artifact_id
            )
            if created_before is not None:
                query = query.filter(RunRecord.created_at <= created_before)
            if status:
                query = query.filter(RunRecord.status == status)
            if kind in {"research", "baseline"}:
                query = query.filter(RunRecord.strategy_version == f"{kind}.v1")
            elif kind == "other":
                query = query.filter(
                    RunRecord.strategy_version.notin_(["research.v1", "baseline.v1"])
                )
            if q and q.strip():
                keyword = q.strip()
                query = query.filter(or_(
                    RunRecord.run_id.contains(keyword, autoescape=True),
                    RunRecord.event_id.contains(keyword, autoescape=True),
                    RunRecord.error_code.contains(keyword, autoescape=True),
                    ArtifactRecord.headline.contains(keyword, autoescape=True),
                ))
            total = query.count()
            rows = (
                query.order_by(RunRecord.created_at.desc(), RunRecord.run_id.desc())
                .offset(offset).limit(limit).all()
            )
            return [_ledger_run_view(run, artifact) for run, artifact in rows], total

    def get_evaluation(self, evaluation_id: str) -> EvaluationView | None:
        with self.session() as session:
            evaluation = session.get(EvaluationRecord, evaluation_id)
            if not evaluation:
                return None
            return EvaluationView(
                evaluation_id=evaluation.evaluation_id,
                forecast_id=evaluation.forecast_id,
                brier_score=evaluation.brier_score,
                net_return_pct=evaluation.net_return_pct,
                direction_correct=evaluation.direction_correct,
                label_status=evaluation.label_status,
                evaluated_at=as_utc(evaluation.evaluated_at),
            )

    def get_timeline(self, run_id: str) -> list[RunTimelineItemView]:
        with self.session() as session:
            rows = session.scalars(
                select(RunEventRecord)
                .where(RunEventRecord.run_id == run_id)
                .order_by(RunEventRecord.sequence_no.asc())
            ).all()
            return [_timeline_view(row) for row in rows]

    def get_run_calls(self, run_id: str) -> list[CallView]:
        with self.session() as session:
            rows = session.scalars(
                select(RunCallRecord)
                .where(RunCallRecord.run_id == run_id)
                .order_by(RunCallRecord.started_at.asc())
            ).all()
            return [
                CallView(
                    call_id=row.call_id,
                    run_id=row.run_id,
                    role=row.role,
                    status=row.status,
                    attempt=row.attempt,
                    started_at=as_utc(row.started_at),
                    finished_at=as_utc(row.finished_at),
                    latency_ms=row.latency_ms,
                    runtime_id=row.runtime_id,
                    runtime_version=row.runtime_version,
                    provider_id=row.provider_id,
                    model=row.model,
                    api_mode=row.api_mode,
                    schema_version=row.schema_version,
                    prompt_tokens=row.prompt_tokens,
                    completion_tokens=row.completion_tokens,
                    total_tokens=row.total_tokens,
                    cost_usd=row.cost_usd,
                    cost_status=row.cost_status,
                    pricing_version=row.pricing_version,
                    error_code=row.error_code,
                    retryable=row.retryable,
                )
                for row in rows
            ]

    def get_run_steps(self, run_id: str) -> list[StepView]:
        with self.session() as session:
            rows = session.scalars(
                select(RunStepRecord)
                .where(RunStepRecord.run_id == run_id)
                .order_by(RunStepRecord.started_at.asc(), RunStepRecord.attempt.asc())
            ).all()
            return [
                StepView(
                    step_id=row.step_id,
                    run_id=row.run_id,
                    step_name=row.step_name,
                    status=row.status,
                    attempt=row.attempt,
                    started_at=as_utc(row.started_at),
                    finished_at=as_utc(row.finished_at),
                    latency_ms=row.latency_ms,
                    error_code=row.error_code,
                )
                for row in rows
            ]

    def get_run_inspector(self, run_id: str) -> RunInspectorView | None:
        run = self.get_run_view(run_id)
        if not run:
            return None
        artifact = self.get_artifact_view(run.artifact_id) if run.artifact_id else None
        with self.session() as session:
            dsh_link = session.get(DshSessionLinkRecord, run_id)
            evaluations = session.scalars(
                select(EvaluationRecord)
                .join(ForecastRecord, EvaluationRecord.forecast_id == ForecastRecord.forecast_id)
                .join(ArtifactRecord, ForecastRecord.artifact_id == ArtifactRecord.artifact_id)
                .where(ArtifactRecord.run_id == run_id)
                .order_by(EvaluationRecord.evaluated_at.asc())
            ).all()
            snapshot = session.get(SnapshotRecord, run.snapshot_id) if run.snapshot_id else None
            evidence_lineage: list[EvidenceLineageView] = []
            if snapshot is not None:
                raw_evidence = json.loads(snapshot.evidence_json)
                if isinstance(raw_evidence, list):
                    evidence_lineage = [
                        _evidence_lineage_view(item, snapshot)
                        for item in raw_evidence
                        if isinstance(item, dict)
                    ]
        evaluation_views = [
            EvaluationView(
                evaluation_id=item.evaluation_id,
                forecast_id=item.forecast_id,
                brier_score=item.brier_score,
                net_return_pct=item.net_return_pct,
                direction_correct=item.direction_correct,
                label_status=item.label_status,
                evaluated_at=as_utc(item.evaluated_at),
            )
            for item in evaluations
        ]
        timeline = self.get_timeline(run_id)
        calls = self.get_run_calls(run_id)
        specialist_roles = sorted(
            {
                item.role
                for item in calls
                if item.role not in {"decision_synthesis", "research_supervisor"}
            }
        )
        successful_specialists = sorted(
            {
                item.role
                for item in calls
                if item.status == "succeeded"
                and item.role not in {"decision_synthesis", "research_supervisor"}
            }
        )
        supervisor_mode = any(item.role == "research_supervisor" for item in calls)
        experiment_refs = sorted(
            {
                item.reference_id
                for item in timeline
                if item.reference_type == "experiment" and item.reference_id is not None
            }
        )
        return RunInspectorView(
            run=run,
            timeline=timeline,
            steps=self.get_run_steps(run_id),
            calls=calls,
            artifact=artifact,
            evaluation_count=len(evaluation_views),
            evaluations=evaluation_views,
            snapshot_cutoff_at=as_utc(snapshot.cutoff_at) if snapshot else None,
            snapshot_hash=snapshot.snapshot_hash if snapshot else None,
            evidence_lineage=evidence_lineage,
            versions=VersionLineageView(
                strategy_version=run.strategy_version,
                runtime_version=run.runtime_version,
                pack_version=snapshot.pack_version if snapshot else None,
                provider_ids=sorted({item.provider_id for item in calls if item.provider_id}),
                models=sorted({item.model for item in calls if item.model}),
                schema_versions=sorted(
                    {item.schema_version for item in calls if item.schema_version}
                ),
                pricing_versions=sorted(
                    {item.pricing_version for item in calls if item.pricing_version}
                ),
            ),
            orchestration=OrchestrationLineageView(
                mode=(
                    "dsh_native" if dsh_link is not None
                    else "supervisor_candidate" if supervisor_mode
                    else "unknown" if run.strategy_version == "research.v1" and not calls
                    else "fixed_graph"
                ),
                supervisor_role="research_supervisor" if supervisor_mode else None,
                planned_capabilities=specialist_roles,
                required_capabilities=specialist_roles,
                specialist_coverage=successful_specialists,
                missing_capabilities=sorted(set(specialist_roles) - set(successful_specialists)),
                replan_count=min(
                    1, sum(item.event_type == "supervisor.replanned" for item in timeline)
                ),
                experiment_refs=experiment_refs,
            ),
        )


def _ledger_run_view(run: RunRecord, artifact: ArtifactRecord | None) -> RunView:
    return RunView(
        run_id=run.run_id, event_id=run.event_id, status=RunStatus(run.status),
        strategy_version=run.strategy_version, runtime_version=run.runtime_version,
        snapshot_id=run.snapshot_id, artifact_id=run.artifact_id,
        created_at=as_utc(run.created_at), updated_at=as_utc(run.updated_at),
        finished_at=as_utc(run.finished_at), latency_ms=run.latency_ms,
        cost_usd=run.cost_usd, error_code=run.error_code,
        headline=artifact.headline if artifact else None,
        gate_status=GateStatus(artifact.gate_status) if artifact else None,
    )


def _timeline_view(row: RunEventRecord) -> RunTimelineItemView:
    payload = json.loads(row.payload_json)
    reference_type: str | None = None
    reference_id: str | None = None
    if isinstance(payload, dict):
        for key, kind in (
            ("snapshot_id", "snapshot"),
            ("artifact_id", "artifact"),
            ("experiment_id", "experiment"),
            ("candidate_id", "candidate"),
        ):
            value = payload.get(key)
            if isinstance(value, str):
                reference_type = kind
                reference_id = value
                break
    return RunTimelineItemView(
        sequence_no=row.sequence_no,
        event_type=row.event_type,
        occurred_at=as_utc(row.occurred_at) or row.occurred_at,
        reference_type=reference_type,
        reference_id=reference_id,
    )


def _evidence_lineage_view(
    item: dict[object, object], snapshot: SnapshotRecord
) -> EvidenceLineageView:
    cutoff_at = _parse_utc_timestamp(item.get("cutoff_at")) or as_utc(snapshot.cutoff_at)
    observed_at = _parse_utc_timestamp(item.get("observed_at"))
    published_at = _parse_utc_timestamp(item.get("published_at"))
    received_at = _parse_utc_timestamp(item.get("received_at")) or cutoff_at
    return EvidenceLineageView.model_validate(
        {
            "evidence_id": item.get("evidence_id"),
            "source_id": item.get("source_id"),
            "source_type": item.get("source_type"),
            "observed_at": observed_at,
            "published_at": published_at,
            "received_at": received_at,
            "cutoff_at": cutoff_at,
            "content_hash": item.get("content_hash"),
        }
    )


def _parse_utc_timestamp(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return as_utc(value)
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return as_utc(parsed)
    raise TypeError("timestamp must be an ISO-8601 string or datetime")


def _configure_sqlite(dbapi_connection: Any, _connection_record: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()

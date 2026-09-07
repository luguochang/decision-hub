from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import (
    DateTime,
    Float,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
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


def as_utc(value: datetime | None) -> datetime | None:
    """Normalize SQLite's timezone-less datetime values at the public boundary."""
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class Base(DeclarativeBase):
    pass


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
    structured_payload_ref: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    tool_call_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    research_session_id: Mapped[str] = mapped_column(String(256), index=True)
    round: Mapped[int] = mapped_column(Integer)
    quality: Mapped[str] = mapped_column(String(32))
    freshness_status: Mapped[str] = mapped_column(String(32))
    conflict_group: Mapped[str | None] = mapped_column(String(128), nullable=True)
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


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
    window_start_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    window_end_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    event_offset: Mapped[str | None] = mapped_column(String(64), nullable=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    source_id: Mapped[str] = mapped_column(String(256), index=True)
    independence_group: Mapped[str] = mapped_column(String(256), index=True)
    quality: Mapped[str] = mapped_column(String(32))
    delay_class: Mapped[str] = mapped_column(String(32))
    payload_schema_ref: Mapped[str] = mapped_column(String(256))
    payload_hash: Mapped[str] = mapped_column(String(64), index=True)
    attributes_json: Mapped[str] = mapped_column(Text)
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


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
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    reserved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class DshSessionLinkRecord(Base):
    __tablename__ = "dsh_session_links"
    __table_args__ = (
        UniqueConstraint("dsh_session_id", name="uq_dsh_session_links_session_id"),
    )
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
    parent_run_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True, index=True
    )
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


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


class OutboxRecord(Base):
    __tablename__ = "outbox"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    artifact_id: Mapped[str] = mapped_column(String(128), index=True)
    channel: Mapped[str] = mapped_column(String(64), default="local")
    dedupe_key: Mapped[str] = mapped_column(String(256), unique=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)


class SourceStateRecord(Base):
    __tablename__ = "source_states"
    source_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    source_type: Mapped[str] = mapped_column(String(64))
    manifest_version: Mapped[str] = mapped_column(String(64))
    cursor: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="unknown")
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    next_poll_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


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
        Base.metadata.create_all(self.engine)

    def initialize(self) -> None:
        """Apply the checked-in Alembic head for an application startup."""
        from alembic import command

        command.upgrade(self._alembic_config(), "head")

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
            return RunView(
                run_id=run.run_id,
                event_id=run.event_id,
                status=RunStatus(run.status),
                strategy_version=run.strategy_version,
                runtime_version=run.runtime_version,
                snapshot_id=run.snapshot_id,
                artifact_id=run.artifact_id,
                created_at=run.created_at,
                updated_at=run.updated_at,
                finished_at=run.finished_at,
                latency_ms=run.latency_ms,
                cost_usd=run.cost_usd,
                error_code=run.error_code,
                headline=artifact.headline if artifact else None,
                gate_status=GateStatus(artifact.gate_status) if artifact else None,
            )

    def get_run_record(self, run_id: str) -> RunRecord | None:
        with self.session() as session:
            return session.get(RunRecord, run_id)

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
        with self.session() as session:
            rows = session.scalars(
                select(RunRecord).order_by(RunRecord.created_at.desc()).limit(limit)
            ).all()
            artifacts = {
                artifact.artifact_id: artifact
                for artifact in session.scalars(select(ArtifactRecord)).all()
            }
            return [
                RunView(
                    run_id=row.run_id,
                    event_id=row.event_id,
                    status=RunStatus(row.status),
                    strategy_version=row.strategy_version,
                    runtime_version=row.runtime_version,
                    snapshot_id=row.snapshot_id,
                    artifact_id=row.artifact_id,
                    created_at=row.created_at,
                    updated_at=row.updated_at,
                    finished_at=row.finished_at,
                    latency_ms=row.latency_ms,
                    cost_usd=row.cost_usd,
                    error_code=row.error_code,
                    headline=artifacts[row.artifact_id].headline
                    if row.artifact_id in artifacts
                    else None,
                    gate_status=(
                        GateStatus(artifacts[row.artifact_id].gate_status)
                        if row.artifact_id in artifacts
                        else None
                    ),
                )
                for row in rows
            ]

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
                evaluated_at=evaluation.evaluated_at,
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
                    started_at=row.started_at,
                    finished_at=row.finished_at,
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
                    started_at=row.started_at,
                    finished_at=row.finished_at,
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
                evaluated_at=item.evaluated_at,
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
            snapshot_cutoff_at=snapshot.cutoff_at if snapshot else None,
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
                mode="supervisor_candidate" if supervisor_mode else "fixed_graph",
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

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class SourceType(StrEnum):
    manual = "manual"
    transcript = "transcript"
    official_feed = "official_feed"
    web = "web"
    document = "document"


class RunStatus(StrEnum):
    admitted = "admitted"
    running = "running"
    completed = "completed"
    degraded = "degraded"
    failed = "failed"
    cancelled = "cancelled"


class GateStatus(StrEnum):
    publish = "publish"
    degraded = "degraded"
    research_only = "research_only"
    reject = "reject"


class Direction(StrEnum):
    long = "long"
    short = "short"
    neutral = "neutral"
    no_trade = "no_trade"


class TextEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "text-envelope.v1"
    source_id: str = Field(min_length=1)
    source_type: SourceType
    observed_at: datetime
    published_at: datetime | None = None
    received_at: datetime
    raw_text: str = Field(min_length=1, max_length=200_000)
    language: str = Field(min_length=2, max_length=16)
    event_hint: str | None = Field(default=None, max_length=128)
    source_url: HttpUrl | None = None
    revision_of: str | None = None
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class ObservationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=200_000)
    source_id: str = "manual-text"
    source_type: SourceType = SourceType.manual
    observed_at: datetime | None = None
    published_at: datetime | None = None
    language: str = "zh"
    event_hint: str | None = Field(default=None, max_length=128)
    source_url: HttpUrl | None = None


class Forecast(BaseModel):
    model_config = ConfigDict(extra="forbid")

    forecast_id: str
    artifact_id: str
    instrument: str
    horizon: str
    direction: Direction
    probability: float = Field(ge=0, le=1)
    trigger: str
    invalidation: str
    expires_at: datetime


class GateDecision(BaseModel):
    rule_id: str
    status: str
    reason_code: str
    input_hash: str


class ArtifactView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: str
    run_id: str
    event_id: str
    gate_status: GateStatus
    headline: str
    summary: str
    facts: list[str]
    inferences: list[str]
    counter_thesis: str
    uncertainty: list[str]
    transmission_chain: list[str] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)
    forecasts: list[Forecast] = Field(default_factory=list)
    gate_decisions: list[GateDecision] = Field(default_factory=list)
    created_at: datetime


class RunView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    event_id: str
    status: RunStatus
    strategy_version: str
    snapshot_id: str | None = None
    artifact_id: str | None = None
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None = None
    latency_ms: int | None = None
    cost_usd: float | None = None
    error_code: str | None = None
    headline: str | None = None
    gate_status: GateStatus | None = None


class CallView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    call_id: str
    run_id: str
    role: str
    status: str
    attempt: int
    started_at: datetime
    finished_at: datetime | None = None
    latency_ms: int | None = None
    runtime_id: str | None = None
    runtime_version: str | None = None
    provider_id: str | None = None
    model: str | None = None
    api_mode: str | None = None
    schema_version: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cost_usd: float | None = None
    cost_status: str = "unknown"
    pricing_version: str | None = None
    error_code: str | None = None
    retryable: bool = False


class StepView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_id: str
    run_id: str
    step_name: str
    status: str
    attempt: int
    started_at: datetime
    finished_at: datetime | None = None
    latency_ms: int | None = None
    error_code: str | None = None


class RunInspectorView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run: RunView
    timeline: list[dict[str, object]]
    steps: list[StepView]
    calls: list[CallView]
    artifact: ArtifactView | None = None
    evaluation_count: int = 0
    evaluations: list[EvaluationView] = Field(default_factory=list)
    snapshot_cutoff_at: datetime | None = None
    snapshot_hash: str | None = None


class InboxView(BaseModel):
    pending_count: int
    running_count: int
    latest: list[RunView]


class DecisionDeskSummary(BaseModel):
    inbox: InboxView
    published_count_30d: int
    forecast_count: int
    evaluated_count: int
    health_status: str
    active_strategy: str
    active_pack: str


class ErrorEnvelope(BaseModel):
    code: str
    message: str
    details: dict[str, Any] | None = None
    retryable: bool = False
    request_id: str


class OutcomeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    forecast_id: str
    return_pct: float
    direction_correct: bool
    fees: float = 0
    slippage: float = 0
    quality_status: str = "estimated"


class EvaluationView(BaseModel):
    evaluation_id: str
    forecast_id: str
    brier_score: float
    net_return_pct: float
    direction_correct: bool
    label_status: str
    evaluated_at: datetime

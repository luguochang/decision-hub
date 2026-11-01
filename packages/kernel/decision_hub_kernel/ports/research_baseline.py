"""Typed server baseline originals. Shapes alone confer no source authority."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from packages.contracts_py.decision_hub_contracts import (
    CoverageAssessment,
    ResearchCapabilityInvocation,
    ResearchCapabilityResult,
)

from .research_observation import ObservedExecutionRef

Sha = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BaselineRequest(_Contract):
    schema_version: Literal["research-baseline-request.v1"]
    run_id: str
    research_session_id: str
    generation: Annotated[int, Field(strict=True, ge=1)]
    request_hash: Sha


class BaselineSlot(_Contract):
    ordinal: Annotated[int, Field(strict=True, ge=0, le=4)]
    phase: Literal["hard", "soft"]
    invocation: ResearchCapabilityInvocation
    unavailable_reason: str | None


class BaselinePlan(_Contract):
    schema_version: Literal["research-baseline-plan.v1"]
    run_id: str
    research_session_id: str
    generation: Literal[1]
    request_hash: Sha
    execution_request_hash: Sha
    method_hash: Sha
    catalog_hash: Sha
    catalog_json: str
    instrument: Literal["BTC-USDT", "ETH-USDT"]
    planned_at: AwareDatetime
    acquisition_close_at: AwareDatetime
    deadline_at: AwareDatetime
    slots: tuple[BaselineSlot, ...]
    max_parallel: Literal[2]
    protocol_hash: Sha
    plan_hash: Sha


class BaselineError(_Contract):
    request_id: str
    capability_id: str
    requirement_id: str
    error_code: str


class BaselineBatch(_Contract):
    schema_version: Literal["research-baseline-batch.v1"]
    status: Literal["ready", "hard_gap"]
    run_id: str
    research_session_id: str
    generation: Literal[1]
    request_hash: Sha
    method_hash: Sha
    plan_ref: ObservedExecutionRef
    results: tuple[ResearchCapabilityResult, ...]
    errors: tuple[BaselineError, ...]
    coverage: CoverageAssessment
    remaining_tool_calls: Annotated[int, Field(strict=True, ge=0)]
    acquisition_close_at: AwareDatetime
    recorded_at: AwareDatetime
    origin: Literal["server_baseline"]
    skill_execution_verified: Literal[False]
    model_understanding_verified: Literal[False]


class BaselineDelivery(BaselineBatch):
    schema_version: Literal["research-baseline-delivery.v1"]  # type: ignore[assignment]
    batch_ref: ObservedExecutionRef


class BaselineSkipped(_Contract):
    schema_version: Literal["research-baseline-skipped.v1"]
    status: Literal["skipped"]
    run_id: str
    research_session_id: str
    generation: Annotated[int, Field(strict=True, ge=1)]
    request_hash: Sha
    reason_code: Literal["method_not_opted_in"]

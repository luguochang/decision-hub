"""Internal Runtime observations; a locator is neither execution nor publication permission."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Literal, Protocol

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictInt, StrictStr

from packages.contracts_py.decision_hub_contracts import (
    DshSessionAccepted,
    DshSessionResult,
    DshSessionStatus,
    ResearchSessionRequest,
    ResearchSessionResult,
    ResearchSynthesisCandidate,
)
from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
    ActionReviewAuthorizationV2,
    ActionReviewAuthorizationV3,
    ActionReviewRequestV2,
    ActionReviewRequestV3,
    ResearchDecisionSynthesisV2,
    ResearchDecisionSynthesisV3,
)
from packages.contracts_py.decision_hub_contracts.generated.research_signal import (
    ResearchNativeSelectionV1,
)
from packages.kernel.decision_hub_kernel.ports.research import ResearchTraceSink
from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError

if TYPE_CHECKING:
    from packages.kernel.decision_hub_kernel.application.research_context_dispatch import (
        ContextSelectionReference,
    )

Sha = Annotated[str, Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")]


class _Observation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ObservedExecutionRef(_Observation):
    output_id: str
    role: str
    schema_version: str
    payload_hash: Sha
    recorded_at: AwareDatetime


class ResearchNativeObservationContext(_Observation):
    run_id: str
    event_id: str
    snapshot_id: str | None
    request_id: str
    dsh_session_id: str
    generation: Annotated[int, Field(strict=True, ge=1)]
    original_task_request_hash: Sha
    bundle_hash: Sha
    method_hash: Sha
    execution_request_hash: Sha
    first_linked_request_hash: Sha
    prompt_hash: Sha
    rows: tuple[ObservedExecutionRef, ...]


class ResearchObservedError(_Observation):
    error_code: str
    message: str
    retryable: bool
    attempt: int
    provider_id: str | None
    model: str | None
    origin: str
    cause_code: str | None
    capability_id: str | None
    tool_call_id: str | None
    deadline_ms: int | None

    @classmethod
    def from_error(cls, error: AgentExecutionError) -> ResearchObservedError:
        return cls(
            error_code=error.error_code,
            message=str(error),
            retryable=error.retryable,
            attempt=error.attempt,
            provider_id=error.provider_id,
            model=error.model,
            origin=error.origin,
            cause_code=error.cause_code,
            capability_id=error.capability_id,
            tool_call_id=error.tool_call_id,
            deadline_ms=error.deadline_ms,
        )


class ObservedAgentExecutionError(AgentExecutionError):
    """Original AgentExecutionError ABI plus this call's confirmed failure locator."""

    def __init__(
        self, original: AgentExecutionError, observation_ref: ObservedExecutionRef
    ) -> None:
        super().__init__(
            original.error_code,
            str(original),
            retryable=original.retryable,
            attempt=original.attempt,
            provider_id=original.provider_id,
            model=original.model,
            origin=original.origin,
            cause_code=original.cause_code,
            capability_id=original.capability_id,
            tool_call_id=original.tool_call_id,
            deadline_ms=original.deadline_ms,
        )
        self.observation_ref = observation_ref
        for note in getattr(original, "__notes__", ()):
            self.add_note(note)


class ResearchTransportObservation(_Observation):
    schema_version: Literal["research-transport-observation.v1"]
    context: ResearchNativeObservationContext
    phase: Literal["accepted", "status", "cancel", "result", "error"]
    reply: DshSessionAccepted | DshSessionStatus | DshSessionResult | None
    error_code: str | None
    error: ResearchObservedError | None = None
    trust_level: Literal["request_correlated_unverified"] = "request_correlated_unverified"
    events_json_hash: Sha | None = None
    final_response_hash: Sha | None = None
    recorded_at: AwareDatetime
    # Unknown run-wide cost stays unknown. This ledger never charges usage.
    total_tokens: None = None
    estimated_cost_usd: None = None


class ResearchSynthesisSelectionWitness(_Observation):
    kind: Literal["tool_capture", "structured_response", "evidence_only"]
    candidate_payload_hash: Sha | None
    candidate: ResearchSynthesisCandidate | None
    final_response_hash: Sha | None
    call_id: str | None
    call_event_index: Annotated[int | None, Field(strict=True, ge=0)]
    result_event_index: Annotated[int | None, Field(strict=True, ge=0)]
    payload_occurrence: Annotated[int | None, Field(strict=True, ge=0)]
    # None is a JSON-string decode step, never an object key or array index.
    payload_path: tuple[StrictStr | StrictInt | None, ...] | None
    call_event_hash: Sha | None
    result_event_hash: Sha | None
    call_seq: Annotated[int | None, Field(strict=True, ge=0)]
    result_seq: Annotated[int | None, Field(strict=True, ge=0)]
    # Host session events do not attest the generation in which a pair was produced.
    origin_generation: None = None
    presence_generation: Annotated[int | None, Field(strict=True, ge=1)] = None
    presence_ref: ObservedExecutionRef | None = None


class ResearchMappedObservation(_Observation):
    schema_version: Literal["research-mapped-observation.v1"]
    scope: Literal["runtime_mapping_observation_only"] = "runtime_mapping_observation_only"
    context: ResearchNativeObservationContext
    mapping_input_ref: ObservedExecutionRef
    latest_transport_ref: ObservedExecutionRef
    mapping_input_generation: Annotated[int, Field(strict=True, ge=1)]
    latest_transport_generation: Annotated[int, Field(strict=True, ge=1)]
    selection: ResearchSynthesisSelectionWitness
    result: ResearchSessionResult
    result_content_hash: Sha
    recorded_at: AwareDatetime


class ResearchPerpetualSynthesisObservation(_Observation):
    """Original versioned native packet; DTO shape is not reviewer authentication."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, regex_engine="python-re")

    synthesis: ResearchDecisionSynthesisV2 | ResearchDecisionSynthesisV3
    review_request: ActionReviewRequestV2 | ActionReviewRequestV3
    authorization: ActionReviewAuthorizationV2 | ActionReviewAuthorizationV3 | None
    # Same canonical wire as the research_signal port subclass. The generated
    # base avoids a circular import through ObservedExecutionRef in that port.
    selection: ResearchNativeSelectionV1
    raw_candidate_hash: Sha
    review_wrapper_json: str | None
    review_call_id: str | None
    review_call_event_index: Annotated[int | None, Field(strict=True, ge=0)]
    review_result_event_index: Annotated[int | None, Field(strict=True, ge=0)]


class ResearchMappedObservationV2(_Observation):
    """Perpetual results have their own carrier; no fake legacy candidate/witness."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, regex_engine="python-re")

    schema_version: Literal["research-mapped-observation.v2"]
    scope: Literal["runtime_mapping_observation_only"] = "runtime_mapping_observation_only"
    context: ResearchNativeObservationContext
    mapping_input_ref: ObservedExecutionRef
    latest_transport_ref: ObservedExecutionRef
    mapping_input_generation: Annotated[int, Field(strict=True, ge=1)]
    latest_transport_generation: Annotated[int, Field(strict=True, ge=1)]
    perpetual_synthesis: ResearchPerpetualSynthesisObservation
    result: ResearchSessionResult
    result_content_hash: Sha
    recorded_at: AwareDatetime


class ObservedResearchResult(_Observation):
    result: ResearchSessionResult
    observation_ref: ObservedExecutionRef


class SelectedObservedResearchRuntime(Protocol):
    async def execute_selected_observed(
        self,
        request: ResearchSessionRequest,
        selection_reference: ContextSelectionReference,
        *,
        expected_lease_owner: str,
        trace_sink: ResearchTraceSink | None = None,
    ) -> ObservedResearchResult: ...


class ObservedResearchRuntime(Protocol):
    async def execute_observed(
        self,
        request: ResearchSessionRequest,
        trace_sink: ResearchTraceSink | None = None,
    ) -> ObservedResearchResult: ...

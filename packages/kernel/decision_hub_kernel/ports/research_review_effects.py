"""Server-derived review effects; model receipt fields cannot confer authority."""

from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from packages.contracts_py.decision_hub_contracts import IndependentReviewReceiptV2
from packages.kernel.decision_hub_kernel.decision.research_quality import ResearchChallengeV1
from packages.kernel.decision_hub_kernel.ports.research_observation import ObservedExecutionRef

Sha = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class ResearchReviewEffectOriginal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["research-review-effect.v1"] = "research-review-effect.v1"
    run_id: str
    generation: int = Field(ge=1)
    root_session_id: str
    mapped_ref: ObservedExecutionRef
    transport_ref: ObservedExecutionRef
    selected_call_id: str
    selected_candidate_hash: Sha
    source_result_hash: Sha
    public_review_id: str
    receipt: IndependentReviewReceiptV2
    receipt_hash: Sha
    reviewer_call_id: str
    reviewer_call_event_index: int = Field(ge=0)
    reviewer_result_event_index: int = Field(ge=0)
    reviewer_call_event_hash: Sha
    reviewer_result_event_hash: Sha
    frozen_wrapper_hash: Sha
    frozen_candidate_hash: Sha
    reviewer_prompt_hash: Sha
    child_session_id: str
    challenges: tuple[ResearchChallengeV1, ...]
    known_at: AwareDatetime
    producer_authenticated: Literal[True] = True
    proof_scope: Literal["actual_frozen_reviewer_native_return"] = (
        "actual_frozen_reviewer_native_return"
    )
    financial_qualified: Literal[False] = False


class ResearchReviewEffects(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_result_hash: Sha
    selected_candidate_hash: Sha
    mapped_ref: ObservedExecutionRef
    transport_ref: ObservedExecutionRef
    review_effect_ref: ObservedExecutionRef | None = None
    public_review_ids: tuple[str, ...]
    challenges: tuple[ResearchChallengeV1, ...]

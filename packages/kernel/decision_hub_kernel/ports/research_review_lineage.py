"""Server-derived native issue lineage; model findings cannot supply this proof."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .research_observation import ObservedExecutionRef

Sha = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class ResearchReviewIssueLineageV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["research-review-issue-lineage.v1"] = "research-review-issue-lineage.v1"
    lineage_id: str = Field(pattern=r"^review-lineage:[a-f0-9]{64}$")
    issue_identity_hash: Sha
    run_id: str
    root_session_id: str
    previous_generation: int = Field(ge=1)
    generation: int = Field(ge=1)
    previous_effect_ref: ObservedExecutionRef
    effect_ref: ObservedExecutionRef
    previous_mapped_ref: ObservedExecutionRef
    mapped_ref: ObservedExecutionRef
    previous_transport_ref: ObservedExecutionRef
    transport_ref: ObservedExecutionRef
    previous_receipt_hash: Sha
    receipt_hash: Sha
    previous_result_hash: Sha
    result_hash: Sha
    previous_review_id: str
    review_id: str
    previous_challenge_id: str
    challenge_id: str
    proof_scope: Literal["server_exact_native_parent_issue_association"] = (
        "server_exact_native_parent_issue_association"
    )
    financial_qualified: Literal[False] = False

    @model_validator(mode="after")
    def ordered_original_versions(self):
        if (self.previous_generation > self.generation
                or self.previous_effect_ref.recorded_at > self.effect_ref.recorded_at
                or self.previous_effect_ref == self.effect_ref
                or self.previous_mapped_ref == self.mapped_ref):
            raise ValueError("research_review_lineage_version_binding_invalid")
        for effect in (self.previous_effect_ref, self.effect_ref):
            if effect.schema_version != "research-review-effect.v1":
                raise ValueError("research_review_lineage_effect_schema_invalid")
        for mapped in (self.previous_mapped_ref, self.mapped_ref):
            if mapped.schema_version != "research-mapped-observation.v1":
                raise ValueError("research_review_lineage_mapping_schema_invalid")
        return self

    @property
    def proof_refs(self) -> tuple[ObservedExecutionRef, ...]:
        return (self.previous_effect_ref, self.effect_ref,
                self.previous_mapped_ref, self.mapped_ref,
                self.previous_transport_ref, self.transport_ref)

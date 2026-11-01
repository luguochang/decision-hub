"""Independent component contracts; these pointers never authorize directional trading."""

from __future__ import annotations

from typing import Literal, Protocol

from pydantic import AwareDatetime, Field, model_validator

from .market_path import FrozenContract, canonical_hash

ComponentKind = Literal["source_parser", "feature", "model", "research_recipe", "monitor_rule"]


class ComponentManifest(FrozenContract):
    schema_version: Literal["market-component-manifest.v1"] = "market-component-manifest.v1"
    version_id: str = Field(min_length=1, max_length=128)
    kind: ComponentKind
    component_ref: str = Field(min_length=1, max_length=128)
    scope_id: str = Field(min_length=1, max_length=192)
    parent_version_id: str | None = None
    input_contract: str = Field(min_length=1)
    output_contract: str = Field(min_length=1)
    code_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    resource_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    parameters: dict

    @property
    def payload_hash(self) -> str:
        return canonical_hash(self.model_dump(mode="json"))


class ShadowTrial(FrozenContract):
    schema_version: Literal["market-component-shadow.v1"] = "market-component-shadow.v1"
    trial_id: str
    version_id: str
    manifest_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    protocol_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    baseline_version_id: str | None
    registered_at: AwareDatetime
    start_at: AwareDatetime
    end_at: AwareDatetime

    @model_validator(mode="after")
    def prospective(self) -> ShadowTrial:
        if not self.registered_at <= self.start_at < self.end_at:
            raise ValueError("component_shadow_backdated")
        return self


class ComponentQualification(FrozenContract):
    """Returned only by an in-process original reader, never accepted from a user DTO."""

    trial_id: str
    manifest_hash: str
    protocol_hash: str
    original_refs: tuple[str, ...]
    original_hashes: tuple[str, ...]
    known_at: AwareDatetime
    status: Literal["passed", "failed", "insufficient", "unavailable"]
    reasons: tuple[str, ...]
    sample_qualified: bool = False
    cost_verified: bool = False
    independent_blocks: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def original_binding(self) -> ComponentQualification:
        if len(self.original_refs) != len(self.original_hashes):
            raise ValueError("component_original_refs_invalid")
        if self.status == "passed" and (
            not self.original_refs
            or self.reasons
            or not self.sample_qualified
            or not self.cost_verified
        ):
            raise ValueError("component_qualification_incomplete")
        return self


class ComponentOriginalReader(Protocol):
    def verify(
        self,
        manifest: ComponentManifest,
        trial: ShadowTrial,
    ) -> ComponentQualification: ...


class ComponentReview(FrozenContract):
    review_id: str
    request_id: str
    version_id: str
    trial_id: str
    reviewer_ref: str = Field(min_length=1)
    reviewed_at: AwareDatetime
    qualification: ComponentQualification
    decision: Literal["approved", "not_admitted"]


class ComponentPointer(FrozenContract):
    kind: ComponentKind
    component_ref: str
    scope_id: str
    version_id: str
    generation: int = Field(ge=1)
    changed_at: AwareDatetime
    audit_id: str

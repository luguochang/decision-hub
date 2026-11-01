"""Frozen original report contract. Unwired candidate; no new read permission."""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from .research_observation import ObservedExecutionRef


class FrozenPublicationBundleV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["research-publication-bundle.v1"] = "research-publication-bundle.v1"
    run_id: str
    event_id: str
    artifact_id: str
    publication_ref: ObservedExecutionRef
    recipe_selection_ref: ObservedExecutionRef
    original_published_at: AwareDatetime
    original_read_at: AwareDatetime
    quality_json: str
    report_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    report_bytes: int = Field(ge=1)
    material_ref: str
    material_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    material_bytes: int = Field(ge=1, strict=True)
    trusted_reader_source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    frozen_replay_status: Literal["same_original"] = "same_original"

    @field_validator("material_ref")
    @classmethod
    def relative_material(cls, value: str) -> str:
        import re
        if not re.fullmatch(r"materials/[a-f0-9]{64}\.json", value):
            raise ValueError("publication_bundle_material_ref_invalid")
        return value

    @model_validator(mode="after")
    def original_only(self) -> FrozenPublicationBundleV1:
        if self.material_ref != "materials/" + self.material_sha256 + ".json":
            raise ValueError("publication_bundle_material_ref_invalid")
        from ..application.research_quality_view import ResearchQualityViewV1
        quality = ResearchQualityViewV1.model_validate_json(self.quality_json)
        if (quality.status != "available" or quality.decision is None
                or quality.run_id != self.run_id
                or quality.publication_ref != self.publication_ref
                or quality.recipe_selection_ref != self.recipe_selection_ref
                or quality.published_at != self.original_published_at
                or quality.as_of != self.original_read_at):
            raise ValueError("publication_bundle_original_identity_changed")
        content = quality.decision.full_report_json.encode("utf-8")
        if (hashlib.sha256(content).hexdigest() != self.report_sha256
                or quality.decision.full_report_hash != self.report_sha256
                or len(content) != self.report_bytes):
            raise ValueError("publication_bundle_report_original_changed")
        return self

    def quality(self, *, read_at: datetime) -> ResearchQualityViewV1:
        from ..application.research_quality_view import ResearchQualityViewV1
        original = ResearchQualityViewV1.model_validate_json(self.quality_json)
        # The only transport change allowed by the existing view contract.
        # Decision, report, usage, unknowns and published_at remain original.
        if read_at < self.original_published_at:
            raise ValueError("publication_bundle_read_before_publication")
        return ResearchQualityViewV1.model_validate(
            {**original.model_dump(mode="python"), "as_of": read_at}
        )

    def report(self) -> bytes:
        from ..application.research_quality_view import ResearchQualityViewV1
        quality = ResearchQualityViewV1.model_validate_json(self.quality_json)
        assert quality.decision is not None
        return quality.decision.full_report_json.encode("utf-8")

"""Typed research adapters; no persistence, qualification, or native trust grant."""

from __future__ import annotations

from pydantic import AwareDatetime, TypeAdapter, field_validator

from packages.contracts_py.decision_hub_contracts import (
    AnalystClaim,
    ResearchDocument,
    ResearchDocumentVersion,
    ResearchSynthesisCandidate,
)
from packages.contracts_py.decision_hub_contracts.generated.research_signal import (
    ResearchActionCandidateV1,
    ResearchClaimSignalV1,
    ResearchClaimSourceContextV1,
    ResearchMechanismReviewV1,
    ResearchPublicationTimeV1,
)
from packages.contracts_py.decision_hub_contracts.generated.research_signal import (
    ResearchActionSynthesisSidecarV1 as _GeneratedSidecar,
)
from packages.contracts_py.decision_hub_contracts.generated.research_signal import (
    ResearchDecisionSynthesisV1 as _GeneratedSynthesis,
)
from packages.contracts_py.decision_hub_contracts.generated.research_signal import (
    ResearchNativeSelectionV1 as _GeneratedSelection,
)
from packages.kernel.decision_hub_kernel.ports.research_observation import ObservedExecutionRef
from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
    ActionReviewAuthorizationV2,
    ActionReviewRequestV2,
    ResearchDecisionSynthesisV2,
)

ClaimSignal = ResearchClaimSignalV1
ClaimSourceContext = ResearchClaimSourceContextV1
PublicationTime = ResearchPublicationTimeV1
MechanismReview = ResearchMechanismReviewV1
ResearchActionCandidate = ResearchActionCandidateV1


class ResearchDecisionSynthesis(_GeneratedSynthesis):
    """Reuse the original candidate type, including its frozen serialization."""

    research_candidate_v1: ResearchSynthesisCandidate


def _native_reference(value):
    if isinstance(value, dict) and isinstance(value.get("recorded_at"), str):
        return ObservedExecutionRef.model_validate(
            {
                **value,
                "recorded_at": TypeAdapter(AwareDatetime).validate_python(value["recorded_at"]),
            }
        )
    return value


class ResearchNativeSelection(_GeneratedSelection):
    presence_ref: ObservedExecutionRef | None

    @field_validator("presence_ref", mode="before")
    @classmethod
    def parse_native_reference(cls, value):
        return _native_reference(value)


class ResearchActionSynthesisSidecar(_GeneratedSidecar):
    """References are declarations until the trusted native reader resolves them."""

    selected_native_observation_ref: ObservedExecutionRef
    selection_witness: ResearchNativeSelection
    synthesis: ResearchDecisionSynthesis

    @field_validator("selected_native_observation_ref", mode="before")
    @classmethod
    def parse_native_reference(cls, value):
        return _native_reference(value)


# Versioned names make the producer/reader ABI explicit.
ResearchDecisionSynthesisV1 = ResearchDecisionSynthesis
ResearchActionSynthesisSidecarV1 = ResearchActionSynthesisSidecar


def claim_signal_from_library(
    claim: AnalystClaim,
    version: ResearchDocumentVersion,
    document: ResearchDocument,
    source: ClaimSourceContext,
) -> ClaimSignal:
    """Adapt existing article/claim identities; caller persists a frozen snapshot.

    Version identity is version_id, not document.latest_version_id or version_count.
    No timestamp, author horizon, missing condition, or independent corroboration
    is invented. Rights and first-seen are caller-supplied captured metadata.
    """
    if claim.version_id != version.version_id or version.document_id != document.document_id:
        raise ValueError("research_claim_document_version_mismatch")
    if source.first_seen_at > version.received_at:
        raise ValueError("research_claim_first_seen_after_received")
    published = source.publication
    if (version.published_at is None) != (
        published.published_at is None
    ) or version.published_at != published.published_at:
        raise ValueError("research_claim_publication_mismatch")
    return ClaimSignal.model_validate(
        {
            "schema_version": "research-claim-signal.v1",
            "claim_id": claim.claim_id,
            "version_id": version.version_id,
            "document_id": document.document_id,
            "supersedes_version_id": version.supersedes_version_id,
            "source": source.model_dump(mode="json"),
            "author": document.author,
            "content_ref": version.original_file_ref,
            "content_hash": version.original_file_hash,
            "claim_type": claim.claim_type,
            "stance": claim.stance,
            "proposition": claim.claim_text,
            "supporting_quote": claim.supporting_quote,
            "quoted_evidence_refs": claim.locator_refs,
            "market_scope": claim.market_scope,
            "instrument_scopes": claim.instrument_scope,
            "horizon": claim.horizon,
            "claimed_conditions": claim.conditions,
            "invalidation_conditions": claim.invalidation_conditions,
            "review_status": claim.status,
            "attribution_confidence": claim.attribution_confidence,
            "requires_independent_evidence": True,
        }
    )


def legacy_candidate_projection(synthesis: ResearchDecisionSynthesis) -> ResearchSynthesisCandidate:
    """Identity projection, with no defaults or fields injected into the old DTO."""
    return synthesis.research_candidate_v1


__all__ = [
    "ClaimSignal",
    "ClaimSourceContext",
    "PublicationTime",
    "MechanismReview",
    "ResearchActionCandidate",
    "ResearchDecisionSynthesis",
    "ResearchNativeSelection",
    "ResearchActionSynthesisSidecar",
    "ResearchDecisionSynthesisV1",
    "ResearchActionSynthesisSidecarV1",
    "ResearchDecisionSynthesisV2",
    "ActionReviewRequestV2",
    "ActionReviewAuthorizationV2",
    "claim_signal_from_library",
    "legacy_candidate_projection",
]

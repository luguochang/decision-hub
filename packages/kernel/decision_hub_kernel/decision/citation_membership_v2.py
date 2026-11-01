"""Pure membership check; a locator or mapper allowlist is not acceptance."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from pydantic import BaseModel, ConfigDict

from packages.contracts_py.decision_hub_contracts import ResearchSessionResult
from packages.kernel.decision_hub_kernel.application.research_publication_binding import (
    observation_sha,
)
from packages.kernel.decision_hub_kernel.application.research_review import (
    research_result_content_hash,
)
from packages.kernel.decision_hub_kernel.ports.research_observation import Sha

CitationDisposition = Literal["accepted", "rejected", "not_applicable"]


class CitationEntryV2(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    path: str
    evidence_id: str


class CitationMembershipCheckV2(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["research-citation-membership.v2"] = "research-citation-membership.v2"
    disposition: CitationDisposition
    entries: list[CitationEntryV2]
    accepted_evidence_ids: list[str]
    original_input_ids: list[str]
    rejected_evidence_ids: list[str]
    result_content_hash: Sha
    input_set_hash: Sha


def check_citation_membership_v2(
    result: ResearchSessionResult,
    *,
    accepted_evidence_ids: Iterable[str],
    original_input_ids: Iterable[str],
) -> CitationMembershipCheckV2:
    """Check every claim reference without upgrading Evidence or changing it."""
    accepted = sorted(set(accepted_evidence_ids))
    original = sorted(set(original_input_ids))
    entries: list[CitationEntryV2] = []
    if result.causal_case is not None:
        for index, ref in enumerate(result.causal_case.evidence_refs):
            entries.append(
                CitationEntryV2(path=f"causal_case.evidence_refs[{index}]", evidence_id=ref)
            )
        for name, chain in (
            ("main_chain", result.causal_case.main_chain),
            ("opposite_chain", result.causal_case.opposite_chain),
        ):
            for link_index, link in enumerate(chain):
                for ref_index, ref in enumerate(link.evidence_refs):
                    entries.append(
                        CitationEntryV2(
                            path=(f"causal_case.{name}[{link_index}].evidence_refs[{ref_index}]"),
                            evidence_id=ref,
                        )
                    )
    for horizon_index, horizon in enumerate(result.horizons):
        for ref_index, ref in enumerate(horizon.evidence_refs):
            entries.append(
                CitationEntryV2(
                    path=f"horizons[{horizon_index}].evidence_refs[{ref_index}]",
                    evidence_id=ref,
                )
            )
    rejected = sorted({entry.evidence_id for entry in entries} - set(accepted) - set(original))
    disposition: CitationDisposition = (
        "rejected"
        if rejected
        else "not_applicable"
        if result.causal_case is None and not result.horizons
        else "accepted"
    )
    return CitationMembershipCheckV2(
        disposition=disposition,
        entries=entries,
        accepted_evidence_ids=accepted,
        original_input_ids=original,
        rejected_evidence_ids=rejected,
        result_content_hash=research_result_content_hash(result),
        input_set_hash=observation_sha(
            {"accepted_evidence_ids": accepted, "original_input_ids": original}
        ),
    )

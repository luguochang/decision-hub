"""Citation admission around the unchanged v1 resource/time projection."""

from __future__ import annotations

from datetime import datetime

from packages.contracts_py.decision_hub_contracts import (
    EvidenceCandidate,
    FactEnvelope,
    ResearchSessionRequest,
    ResearchSessionResult,
)
from packages.kernel.decision_hub_kernel.application.research_graph_projection import (
    AgenticResearchState,
    project_final,
    project_round,
)
from packages.kernel.decision_hub_kernel.application.research_review import (
    bind_reviews_to_result_version,
)
from packages.kernel.decision_hub_kernel.decision.citation_membership_v2 import (
    CitationMembershipCheckV2,
    check_citation_membership_v2,
)

RECIPE_VERSION_V2 = "research-graph-projection.v2"
CITATION_REJECTED = "citation_membership_rejected"


def citation_check_for_material(
    result: ResearchSessionResult,
    base_request: ResearchSessionRequest,
    evidence: list[EvidenceCandidate],
) -> CitationMembershipCheckV2:
    return check_citation_membership_v2(
        result,
        accepted_evidence_ids=[e.evidence_id for e in evidence if e.quality == "accepted"],
        original_input_ids=[e.evidence_id for e in base_request.input_evidence],
    )


def eligible_synthesis(result: ResearchSessionResult, check: CitationMembershipCheckV2) -> bool:
    return bool(
        check.disposition == "accepted"
        and not result.synthesis_failure_code
        and result.causal_case is not None
        and result.horizons
    )


def project_round_v2(
    state: AgenticResearchState,
    base_request: ResearchSessionRequest,
    request: ResearchSessionRequest,
    result: ResearchSessionResult,
    all_evidence: list[EvidenceCandidate],
    all_facts: list[FactEnvelope],
    accepted: list[EvidenceCandidate],
    *,
    prior_selected_result: ResearchSessionResult | None = None,
    candidate_reference_at: datetime | None = None,
) -> tuple[AgenticResearchState, CitationMembershipCheckV2]:
    check = citation_check_for_material(result, base_request, all_evidence)
    candidate = result
    if check.disposition == "rejected":
        candidate = result.model_copy(
            update={
                "causal_case": None,
                "horizons": [],
                "synthesis_failure_code": CITATION_REJECTED,
            }
        )
    delta = project_round(
        state, base_request, request, candidate, all_evidence, all_facts, accepted,
        candidate_reference_at=candidate_reference_at,
    )
    if not eligible_synthesis(result, check) and prior_selected_result is not None:
        previous_check = citation_check_for_material(
            prior_selected_result, base_request, all_evidence
        )
        if not eligible_synthesis(prior_selected_result, previous_check):
            raise ValueError("research_graph_prior_citation_not_admissible")
        delta["latest_result"] = prior_selected_result.model_dump(mode="json")
        delta["current_thesis"] = state.get("current_thesis")
        delta["current_horizon_actions"] = state.get("current_horizon_actions", {})
    if check.disposition == "rejected":
        # This independently blocks the v1 BTC sufficient-snapshot shortcut.
        delta["synthesis_failure_code"] = CITATION_REJECTED
        delta["loop_blocks_completion"] = True
        delta["loop_should_continue"] = False
        delta["loop_reason_code"] = "synthesis_incomplete"
        delta["loop_summary"] = "Synthesis cites Evidence not accepted in this Run: " + ", ".join(
            check.rejected_evidence_ids
        )
    return delta, check


def project_final_v2(
    state: AgenticResearchState,
    base_request: ResearchSessionRequest,
    evidence: list[EvidenceCandidate],
) -> tuple[ResearchSessionResult, CitationMembershipCheckV2]:
    final = project_final(state)
    check = citation_check_for_material(final, base_request, evidence)
    if check.disposition == "rejected":
        # A previous valid selection must still be valid against current
        # original material. Never authenticate a stale self-claimed pass.
        raise ValueError("research_graph_final_citation_not_admissible")
    if state.get("synthesis_failure_code") == CITATION_REJECTED and final.status == "completed":
        raise ValueError("research_graph_citation_close_bypass")
    final = bind_reviews_to_result_version(final)
    return final, citation_check_for_material(final, base_request, evidence)

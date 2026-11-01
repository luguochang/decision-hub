"""Quality-governed candidate recipe. Existing V1/V2 implementations stay unchanged."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from packages.contracts_py.decision_hub_contracts import (
    CoverageAssessment,
    EvidenceCandidate,
    ExecutionBudget,
    FactEnvelope,
    ResearchSessionRequest,
    ResearchSessionResult,
    ResearchStopReason,
)

from ..decision.citation_membership_v2 import CitationMembershipCheckV2
from ..decision.research_quality import QualityDecisionV1
from ..ports.market_path import canonical_hash
from . import research_graph_projection as v1
from .research_graph_projection_v2 import citation_check_for_material, project_round_v2
from .research_quality_projection import QualityProjectionContextV1, quality_decision_from_state
from .research_review import bind_reviews_to_result_version
from .research_semantic_progress import (
    accepted_refs_for_progress, classify_semantic_progress,
)

RECIPE_VERSION_V3 = "research-graph-projection.v3"
RECIPE_VERSION = RECIPE_VERSION_V3
AgenticResearchState = v1.AgenticResearchState

# These projections do not decide completion or call V1's continuation/finalizer.
initial_research_state = v1.initial_research_state
requirements_for_round = v1.requirements_for_round
attempts_from_result = v1.attempts_from_result
project_failure = v1.project_failure
request_for_state = v1.request_for_state


def _context(state, explicit):
    if explicit is not None:
        return explicit
    value = state.get("quality_projection_context")
    # Persisted Graph state is JSON wire. Strict original refs require
    # Pydantic's JSON mode for aware timestamps, not relaxed DTO types.
    return QualityProjectionContextV1.model_validate_json(json.dumps(value)) if value else None


def quality_for_state(state, *, quality_context=None) -> QualityDecisionV1:
    decision = quality_decision_from_state(state, context=_context(state, quality_context))
    saved = state.get("quality_decision")
    if saved is not None and QualityDecisionV1.model_validate(saved) != decision:
        raise ValueError("research_graph_quality_sidecar_changed")
    return decision


def should_continue_after_round(state: AgenticResearchState, *, quality_context=None) -> bool:
    return quality_for_state(state, quality_context=quality_context).should_continue


def _stop_reason(
    state: AgenticResearchState,
    coverage: CoverageAssessment,
    budget: ExecutionBudget,
    *,
    quality_context=None,
    quality_decision: QualityDecisionV1 | None = None,
) -> tuple[str, str]:
    decision = quality_decision or quality_for_state(state, quality_context=quality_context)
    if decision.status == "sufficient":
        return "sufficient", "All independent quality scopes and critical challenges passed."
    if decision.should_continue:
        raise ValueError("research_graph_quality_finalized_before_continuation")
    reasons = "; ".join(decision.reason_codes) or "Research output remains bounded."
    if "research_budget_timeout" in decision.reason_codes:
        return "deadline", reasons
    if "research_budget_exhausted" in decision.reason_codes:
        if (
            state.get("estimated_cost_usd") is not None
            and state["estimated_cost_usd"] >= budget.max_estimated_cost_usd
        ):
            return "cost_budget", reasons
        if (
            state.get("total_tool_calls", 0) >= budget.max_tool_calls
            or state.get("total_subagents", 0) >= budget.max_subagents
        ):
            return "tool_budget", reasons
        return "round_budget", reasons
    if "research_no_progress" in decision.reason_codes:
        return "no_progress", reasons
    if decision.report_status == "untrusted":
        return "synthesis_incomplete", reasons
    if decision.unresolved_challenge_ids:
        return "mixed", reasons
    # Legacy DTO has no 'partial' code. Bounded no_actionable_path carries the
    # independent typed partial sidecar; it never masquerades as sufficient.
    return "no_actionable_path", reasons


def project_round_v3(
    state: AgenticResearchState,
    base_request: ResearchSessionRequest,
    request: ResearchSessionRequest,
    result: ResearchSessionResult,
    all_evidence: list[EvidenceCandidate],
    all_facts: list[FactEnvelope],
    accepted: list[EvidenceCandidate],
    *,
    prior_selected_result=None,
    quality_context=None,
    candidate_reference_at: datetime | None = None,
) -> tuple[AgenticResearchState, CitationMembershipCheckV2, QualityDecisionV1]:
    # Both contexts come from authenticated native Graph admissions. A
    # model-only rewrite or repeated review id never resets the no-gain guard.
    prior_raw = state.get("quality_source_result") or state.get("latest_result")
    prior_result = ResearchSessionResult.model_validate(prior_raw) if prior_raw else None
    cutoff = v1._decision_cutoff(state, base_request)
    resolved_lineage_ids = frozenset(state.get("semantic_resolved_lineage_ids", []))
    independent_seen = bool(state.get("semantic_independent_review_seen", False))
    gain = classify_semantic_progress(
        run_id=base_request.run_id,
        previous_result=prior_result, previous_context=_context(state, None),
        result=result, context=quality_context,
        accepted_evidence_refs=accepted_refs_for_progress(all_evidence, base_request, cutoff),
        previous_accepted_evidence_refs=accepted_refs_for_progress(
            [EvidenceCandidate.model_validate(item) for item in state.get("evidence", [])],
            base_request, cutoff,
        ),
        already_resolved_lineage_ids=resolved_lineage_ids,
        independent_review_seen=independent_seen,
    )
    citation = citation_check_for_material(result, base_request, all_evidence)
    reset = gain.gained and citation.disposition == "accepted"
    projection_state = {**state, "no_progress_rounds": 0} if reset else state
    delta, check = project_round_v2(
        projection_state,
        base_request,
        request,
        result,
        all_evidence,
        all_facts,
        accepted,
        prior_selected_result=prior_selected_result,
        candidate_reference_at=candidate_reference_at,
    )
    if reset:
        delta["no_progress_rounds"] = 0
    # Frozen Graph state, not model fields. Counting is at most once
    # per original issue lineage/first independent qualification.
    delta["semantic_resolved_lineage_ids"] = sorted(
        resolved_lineage_ids | (set(gain.resolved_lineage_ids) if reset else set())
    )
    delta["semantic_independent_review_seen"] = (
        independent_seen or (reset and gain.independent_review_established)
    )
    merged = {**state, **delta}
    merged.pop("quality_decision", None)
    # The current raw synthesis remains available for audit even if citation
    # rejection makes V2 select an earlier valid synthesis for bounded output.
    merged["quality_source_result"] = result.model_dump(mode="json")
    if quality_context is not None:
        merged["quality_projection_context"] = quality_context.model_dump(mode="json")
    decision = quality_decision_from_state(merged, context=_context(merged, quality_context))
    delta.update(
        {
            "quality_source_result": merged["quality_source_result"],
            "quality_decision": decision.model_dump(mode="json"),
            "quality_projection_context": merged.get("quality_projection_context"),
        }
    )
    return delta, check, decision


@dataclass(frozen=True)
class ResearchQualityFinalizationV3:
    result: ResearchSessionResult
    citation_check: CitationMembershipCheckV2
    quality: QualityDecisionV1
    source_result_hash: str
    projected_result_hash: str
    recipe_version: str = RECIPE_VERSION_V3


def finalize(
    state: AgenticResearchState,
    base_request: ResearchSessionRequest,
    evidence: list[EvidenceCandidate],
    *,
    quality_context=None,
) -> ResearchQualityFinalizationV3:
    quality = quality_for_state(state, quality_context=quality_context)
    latest = ResearchSessionResult.model_validate(state["latest_result"])
    coverage = CoverageAssessment.model_validate(
        state.get("coverage") or latest.final_coverage.model_dump()
    )
    code, detail = _stop_reason(
        state,
        coverage,
        base_request.execution_budget,
        quality_context=quality_context,
        quality_decision=quality,
    )
    stop = ResearchStopReason(
        code=code,
        detail=detail,
        bounded=quality.status != "sufficient",
        remaining_hard_gaps=[
            item.requirement_id for item in coverage.gaps if item.importance == "hard"
        ],
    )
    values = {
        "status": "completed" if quality.status == "sufficient" else "degraded",
        "final_coverage": coverage,
        "stop_reason": stop,
        "evidence_candidates": evidence,
        "facts": [FactEnvelope.model_validate(item) for item in state.get("facts", [])],
        "rounds": [type(latest.rounds[0]).model_validate(item) for item in state.get("rounds", [])]
        or latest.rounds,
        "total_tool_calls": state.get("total_tool_calls", latest.total_tool_calls),
        "total_subagents": state.get("total_subagents", latest.total_subagents),
        "total_tokens": state.get("total_tokens"),
        "estimated_cost_usd": state.get("estimated_cost_usd"),
        "synthesis_failure_code": state.get("synthesis_failure_code"),
    }
    # Preserve actual main/counter chains, uncertainties, direction hypotheses,
    # and subjective values for audit. Publication is independently gated;
    # do not manufacture a neutral 0.5 probability to stand in for unknown risk.
    final = ResearchSessionResult.model_validate(latest.model_copy(update=values).model_dump())
    final = bind_reviews_to_result_version(final)
    check = citation_check_for_material(final, base_request, evidence)
    if check.disposition == "rejected":
        raise ValueError("research_graph_final_citation_not_admissible")
    return ResearchQualityFinalizationV3(
        final,
        check,
        quality,
        quality.source_result_hash,
        canonical_hash(final.model_dump(mode="json")),
    )


def project_final_v3(state, base_request, evidence, *, quality_context=None):
    bundle = finalize(state, base_request, evidence, quality_context=quality_context)
    return bundle.result, bundle.citation_check, bundle.quality


def project_final(state: AgenticResearchState, *, quality_context=None) -> ResearchSessionResult:
    request = ResearchSessionRequest.model_validate(state["request"])
    evidence = [EvidenceCandidate.model_validate(item) for item in state.get("evidence", [])]
    return finalize(state, request, evidence, quality_context=quality_context).result

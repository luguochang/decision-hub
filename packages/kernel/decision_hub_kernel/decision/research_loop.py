from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from packages.contracts_py.decision_hub_contracts import (
    CoverageAssessment,
    EvidenceCandidate,
    EvidenceRequirement,
    ResearchReview,
    ResearchSessionRequest,
    ResearchSessionResult,
)

ResearchLoopReason = Literal[
    "reviewer_challenge",
    "evidence_gap",
    "causal_question",
    "sufficient",
    "no_actionable_path",
    "no_progress",
    "synthesis_incomplete",
]


@dataclass(frozen=True, slots=True)
class ResearchLoopAssessment:
    """Deterministic decision for the next bounded research round.

    Models may propose unresolved questions, but only requirement identifiers
    declared by the immutable request are executable.  This keeps a free-form
    model limitation from creating a new capability or an endless loop.
    """

    reason_code: ResearchLoopReason
    target_requirement_ids: tuple[str, ...]
    canonical_inquiry_ids: tuple[str, ...]
    unmapped_inquiries: tuple[str, ...]
    reviewer_id: str | None
    reviewer_fingerprint: str | None
    allow_retried_routes: bool
    should_continue: bool
    blocks_completion: bool
    summary: str


RESEARCH_ACQUISITION_CLOSING_ERROR = "research_acquisition_closing_reserve_reached"


def research_acquisition_close_at(request: ResearchSessionRequest) -> datetime:
    """Use the frozen V2 reserve, or the unchanged legacy two-step formula.

    This is an acquisition-admission cutoff, never a new execution deadline.
    Freeze/review/resubmit can require three or more steps; two-step reserve
    does not guarantee complete synthesis or financial qualification.
    """
    deadline = request.deadline_at
    if deadline.tzinfo is None or deadline.utcoffset() is None:
        raise ValueError("research_acquisition_deadline_timezone_required")
    total = request.execution_budget.total_deadline_seconds
    step = request.execution_budget.per_model_step_timeout_seconds
    if any(type(value) not in (int, float) or not math.isfinite(value) or value <= 0
           for value in (total, step)):
        raise ValueError("research_acquisition_original_budget_invalid")
    budget_version = getattr(request.execution_budget, "schema_version", None)
    if budget_version == "research-execution-budget.v2":
        reserve = getattr(request.execution_budget, "closing_reserve_seconds", None)
        if type(reserve) is not int or type(total) is not int or not 1 <= reserve < total:
            raise ValueError("research_closing_reserve_invalid")
    elif budget_version is None:
        reserve = min(float(total), 2.0 * float(step))
    else:
        raise ValueError("research_execution_budget_version_unsupported")
    return deadline.astimezone(UTC) - timedelta(seconds=reserve)


def assess_research_loop(
    *,
    requirements: Sequence[EvidenceRequirement],
    coverage: CoverageAssessment,
    result: ResearchSessionResult,
    evidence: Sequence[EvidenceCandidate],
    attempted_capabilities_by_requirement: Mapping[str, Sequence[str]],
    allowed_capabilities: Sequence[str],
    handled_review_fingerprints: Sequence[str] = (),
    input_evidence_ids: Sequence[str] = (),
) -> ResearchLoopAssessment:
    known = {item.requirement_id: item for item in requirements}
    allowed = set(allowed_capabilities)
    attempted = {
        requirement_id: set(capabilities)
        for requirement_id, capabilities in attempted_capabilities_by_requirement.items()
    }

    raw_inquiries = _raw_inquiries(result)
    canonical_inquiries = _unique(item for item in raw_inquiries if item in known)
    unmapped_inquiries = _unique(item for item in raw_inquiries if item not in known)

    evidence_to_requirement = {
        item.evidence_id: item.requirement_id
        for item in evidence
        if item.requirement_id in known and item.quality == "accepted"
    }
    review = _unhandled_review(result.reviews or [], handled_review_fingerprints)
    reviewer_targets = (
        _unique(
            [
                *(
                    evidence_to_requirement[ref]
                    for ref in review.evidence_refs
                    if ref in evidence_to_requirement
                ),
                *(item for item in review.limitations if item in known),
            ]
        )
        if review is not None
        else ()
    )

    hard_gaps = _unique(
        gap.requirement_id
        for gap in coverage.gaps
        if gap.importance == "hard" and gap.requirement_id in known
    )
    soft_gaps = _unique(
        gap.requirement_id
        for gap in coverage.gaps
        if gap.importance != "hard" and gap.requirement_id in known
    )
    synthesis_problem = _synthesis_problem(
        result,
        evidence=evidence,
        input_evidence_ids=input_evidence_ids,
    )

    if review is not None:
        candidates = reviewer_targets
        reason: ResearchLoopReason = "reviewer_challenge"
        allow_retry = True
        blocks_completion = True
    elif hard_gaps:
        candidates = hard_gaps
        reason = "evidence_gap"
        allow_retry = False
        blocks_completion = True
    elif canonical_inquiries:
        candidates = canonical_inquiries
        reason = "causal_question"
        allow_retry = False
        blocks_completion = any(known[item].importance == "hard" for item in candidates)
    elif synthesis_problem is not None:
        return ResearchLoopAssessment(
            reason_code="synthesis_incomplete",
            target_requirement_ids=(),
            canonical_inquiry_ids=canonical_inquiries,
            unmapped_inquiries=unmapped_inquiries,
            reviewer_id=None,
            reviewer_fingerprint=None,
            allow_retried_routes=False,
            should_continue=False,
            blocks_completion=True,
            summary=synthesis_problem,
        )
    elif soft_gaps:
        candidates = soft_gaps
        reason = "evidence_gap"
        allow_retry = False
        blocks_completion = False
    else:
        detail = "All declared hard evidence and causal inquiries are resolved."
        if unmapped_inquiries:
            detail += " Free-form limitations were retained but cannot create new requirements."
        return ResearchLoopAssessment(
            reason_code="sufficient",
            target_requirement_ids=(),
            canonical_inquiry_ids=canonical_inquiries,
            unmapped_inquiries=unmapped_inquiries,
            reviewer_id=None,
            reviewer_fingerprint=None,
            allow_retried_routes=False,
            should_continue=False,
            blocks_completion=False,
            summary=detail,
        )

    actionable = tuple(
        requirement_id
        for requirement_id in candidates
        if _has_route(
            known[requirement_id],
            allowed=allowed,
            attempted=attempted.get(requirement_id, set()),
            allow_retry=allow_retry,
        )
    )
    review_fingerprint = review_challenge_fingerprint(review) if review is not None else None
    if actionable:
        labels = ", ".join(actionable)
        return ResearchLoopAssessment(
            reason_code=reason,
            target_requirement_ids=actionable,
            canonical_inquiry_ids=canonical_inquiries,
            unmapped_inquiries=unmapped_inquiries,
            reviewer_id=review.review_id if review is not None else None,
            reviewer_fingerprint=review_fingerprint,
            allow_retried_routes=allow_retry,
            should_continue=True,
            blocks_completion=blocks_completion,
            summary=f"{reason} requires a bounded plan revision for: {labels}.",
        )

    reason_detail = (
        "The reviewer challenge could not be mapped to an executable declared requirement."
        if review is not None and not candidates
        else "All declared capability routes for the targeted requirements were exhausted."
    )
    return ResearchLoopAssessment(
        reason_code="no_actionable_path",
        target_requirement_ids=tuple(candidates),
        canonical_inquiry_ids=canonical_inquiries,
        unmapped_inquiries=unmapped_inquiries,
        reviewer_id=review.review_id if review is not None else None,
        reviewer_fingerprint=review_fingerprint,
        allow_retried_routes=allow_retry,
        should_continue=False,
        blocks_completion=blocks_completion,
        summary=reason_detail,
    )


def review_challenge_fingerprint(review: ResearchReview) -> str:
    payload = {
        "result_hash": review.result_hash,
        "target_claim": review.target_claim.strip(),
        "challenge": review.challenge.strip(),
        "limitations": sorted(item.strip() for item in review.limitations),
        "evidence_refs": sorted(review.evidence_refs),
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _raw_inquiries(result: ResearchSessionResult) -> tuple[str, ...]:
    values: list[str] = []
    if result.causal_case is not None:
        values.extend(result.causal_case.unresolved_questions)
    for horizon in result.horizons:
        values.extend(horizon.missing_facts)
    return _unique(item.strip() for item in values if item.strip())


def _synthesis_problem(
    result: ResearchSessionResult,
    *,
    evidence: Sequence[EvidenceCandidate],
    input_evidence_ids: Sequence[str],
) -> str | None:
    if result.causal_case is None:
        return "Synthesis is incomplete: no causal case was produced."
    horizons = [item.horizon for item in result.horizons]
    if len(horizons) != 3 or set(horizons) != {"30m", "24h", "72h"}:
        return "Synthesis is incomplete: distinct 30m, 24h, and 72h decisions are required."
    allowed_refs = {
        item.evidence_id for item in evidence if item.quality == "accepted"
    } | set(input_evidence_ids)
    cited_refs = {
        ref
        for link in [
            *result.causal_case.main_chain,
            *result.causal_case.opposite_chain,
        ]
        for ref in link.evidence_refs
    } | {ref for item in result.horizons for ref in item.evidence_refs}
    unknown_refs = sorted(cited_refs - allowed_refs)
    if unknown_refs:
        return (
            "Synthesis is incomplete: claims cite evidence that is not accepted in this Run: "
            + ", ".join(unknown_refs)
            + "."
        )
    return None


def _unhandled_review(
    reviews: Sequence[ResearchReview],
    handled_review_fingerprints: Sequence[str],
) -> ResearchReview | None:
    handled = set(handled_review_fingerprints)
    for review in reviews:
        if review.resolution != "requires_new_evidence":
            continue
        if review_challenge_fingerprint(review) not in handled:
            return review
    return None


def _has_route(
    requirement: EvidenceRequirement,
    *,
    allowed: set[str],
    attempted: set[str],
    allow_retry: bool,
) -> bool:
    declared = set(requirement.preferred_capabilities) | set(requirement.allowed_fallbacks)
    executable = declared & allowed
    return bool(executable if allow_retry else executable - attempted)


def _unique(items) -> tuple[str, ...]:
    return tuple(dict.fromkeys(items))

"""Trusted graph material to quality input. No I/O, runtime dispatch, or model metadata trust."""

from __future__ import annotations

import json
import math
from typing import Any, Literal

from pydantic import AwareDatetime, Field, SerializerFunctionWrapHandler, model_serializer

from packages.contracts_py.decision_hub_contracts import (
    CoverageAssessment,
    EvidenceCandidate,
    ResearchSessionRequest,
    ResearchSessionResult,
)

from ..decision.horizon_contract import validate_horizon_contract
from ..decision.research_quality import (
    FrozenQualityContract,
    QualityDecisionV1,
    ResearchChallengeV1,
    ResearchKeyRiskV1,
    ResearchQualityInputV1,
    assess_research_quality,
)
from ..ports.market_path import canonical_hash
from ..ports.research_review_lineage import ResearchReviewIssueLineageV1
from .research_graph_projection import (
    AgenticResearchState,
    _decision_cutoff,
    requirements_for_round,
)


class QualityProjectionContextV1(FrozenQualityContract):
    """Kernel-authenticated receipt facts; never accept this object from model output.

    A reference's existence here is not an authentication mechanism. The caller
    must read and verify its immutable receipt through the current admission.
    Missing proofs remain unknown; they are not inferred from 'completed'.
    """

    schema_version: Literal["quality-projection-context.v1"] = "quality-projection-context.v1"
    scope_id: str = Field(default="unknown:unadmitted", min_length=1)
    bound_result_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    runtime_attestation_ref: str | None = None
    effective_budget_ref: str | None = None
    assessed_at: AwareDatetime | None = None
    selection_manifest_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    additional_required_context_refs: tuple[str, ...] = ()
    context_fidelity_verified: bool = False
    challenges: tuple[ResearchChallengeV1, ...] = ()
    authenticated_review_ids: tuple[str, ...] = ()
    authenticated_review_lineage: tuple[ResearchReviewIssueLineageV1, ...] = ()
    authenticated_retained_context_refs: tuple[str, ...] = ()
    key_risks: tuple[ResearchKeyRiskV1, ...] = ()
    path_risk_model_qualified: bool = False
    path_risk_qualification_ref: str | None = None
    execution_support_verified: bool = False
    monitor_state: Literal["active", "paused", "degraded", "unavailable", "unknown"] = "unknown"

    @model_serializer(mode="wrap")
    def preserve_original_wire(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        value: dict[str, Any] = handler(self)
        for field in ("authenticated_review_ids", "authenticated_retained_context_refs",
                      "authenticated_review_lineage"):
            if field not in self.model_fields_set:
                value.pop(field, None)
        return value


def _unique(items):
    return tuple(dict.fromkeys(items))


def observed_budget_state(state, request, result, context):
    if state.get("continuation_failure") or state.get("synthesis_failure_code"):
        failure = state.get("continuation_failure") or {}
        provenance = failure.get("provenance") or {}
        code = str(provenance.get("error_code") or "")
        return "timeout" if "timeout" in code or "deadline" in code else "failed"
    now = context.assessed_at
    if now is not None and now >= request.deadline_at:
        return "timeout"
    values = [state.get(name) for name in ("total_tool_calls", "total_subagents", "current_round")]
    cost = state.get("estimated_cost_usd")
    if any(not isinstance(item, int) or isinstance(item, bool) or item < 0 for item in values):
        return "unknown"
    limits = request.execution_budget
    if (
        values[0] >= limits.max_tool_calls
        or values[1] >= limits.max_subagents
        or values[2] >= limits.max_evidence_rounds
    ):
        return "exhausted"
    if (
        cost is None
        or not isinstance(cost, (int, float))
        or isinstance(cost, bool)
        or not math.isfinite(cost)
        or cost < 0
    ):
        return "unknown"
    if cost >= limits.max_estimated_cost_usd:
        return "exhausted"
    if not context.effective_budget_ref:
        return "unknown"
    if result.status in {"failed", "cancelled"}:
        return "failed"
    return "available"


def quality_input_from_state(
    state: AgenticResearchState,
    *,
    context: QualityProjectionContextV1 | None = None,
) -> ResearchQualityInputV1:
    request = ResearchSessionRequest.model_validate(state["request"])
    result = ResearchSessionResult.model_validate(
        state.get("quality_source_result") or state["latest_result"]
    )
    context = context or QualityProjectionContextV1()
    context = QualityProjectionContextV1.model_validate(context.model_dump())
    report = result.model_dump(mode="json")
    result_hash = canonical_hash(report)
    bound = context.bound_result_hash == result_hash
    cutoff = _decision_cutoff(state, request)
    evidence = [EvidenceCandidate.model_validate(raw) for raw in state.get("evidence", [])]
    accepted = _unique(
        [
            *(
                item.evidence_id
                for item in evidence
                if item.quality == "accepted"
                and item.received_at <= cutoff
                and item.observed_at <= cutoff
            ),
            *(
                item.evidence_id
                for item in request.input_evidence
                if item.received_at <= cutoff
                and (item.observed_at is None or item.observed_at <= cutoff)
            ),
        ]
    )
    coverage = CoverageAssessment.model_validate(state.get("coverage") or report["final_coverage"])
    covered = (
        coverage.status == "sufficient"
        and coverage.hard_coverage_ratio == 1
        and not any(item.importance == "hard" for item in coverage.gaps)
        and not any(
            item.severity == "hard" and item.resolution_status == "open"
            for item in coverage.conflicts
        )
    )
    chains = (
        [*result.causal_case.main_chain, *result.causal_case.opposite_chain]
        if result.causal_case
        else []
    )
    challenges = list(context.challenges if bound else ())
    mapped = {item.source_review_id for item in challenges}
    if bound:
        mapped.update(context.authenticated_review_ids)
    for review in result.reviews or []:
        if review.review_id in mapped:
            continue
        # Legacy resolution labels have no structured effects or resolution
        # proof. Keep them unresolved rather than guessing the review passed.
        challenges.append(
            ResearchChallengeV1(
                challenge_id="legacy-review:" + review.review_id,
                source_review_id=review.review_id,
                severity="critical"
                if review.resolution == "requires_new_evidence"
                else "important",
                blocking_scope=("direction", "path_risk", "report"),
                evidence_refs=tuple(review.evidence_refs),
                status="open" if review.resolution == "requires_new_evidence" else "deferred",
                reason=review.challenge
                + " [legacy resolution lacks structured current-result impact proof]",
            )
        )
    key_risks = (
        context.key_risks
        if bound and context.key_risks
        else tuple(
            ResearchKeyRiskV1(
                risk_id="counter:" + item.link_id,
                statement=item.statement,
                evidence_refs=tuple(item.evidence_refs),
                affected_scope=("direction", "path_risk"),
            )
            for item in (result.causal_case.opposite_chain if result.causal_case else [])
        )
    )
    required = _unique(
        [
            *(ref for item in chains for ref in item.evidence_refs),
            *(ref for item in challenges for ref in item.evidence_refs),
            *context.additional_required_context_refs,
        ]
    )
    manifest = result.context_manifest or request.context_manifest
    selected = tuple(manifest.effective_inputs) if manifest else ()
    fidelity = bool(
        bound
        and context.context_fidelity_verified
        and manifest
        and context.selection_manifest_hash == canonical_hash(manifest.model_dump(mode="json"))
        and context.additional_required_context_refs
    )
    if fidelity:
        # The creation manifest names initial inputs. Later tool/reviewer
        # bodies count only after the server verifies the selected dispatch.
        selected = _unique([*selected, *context.authenticated_retained_context_refs])
    route_requirements = requirements_for_round(
        request.evidence_requirements, state, allowed_capabilities=request.allowed_capabilities
    )
    executable = (
        tuple(item.requirement_id for item in route_requirements)
        if state.get("loop_should_continue")
        else ()
    )
    unknowns = _unique(
        [
            *(result.causal_case.unresolved_questions if result.causal_case else []),
            *(ref for item in result.horizons for ref in item.missing_facts),
        ]
    )
    horizon_check = validate_horizon_contract(result.horizons, cutoff)
    if not horizon_check.valid:
        unknowns = (*unknowns, "horizon_contract_invalid:" + ",".join(horizon_check.issue_codes))
    trusted = (
        bound
        and horizon_check.valid
        and not state.get("synthesis_failure_code")
        and not state.get("continuation_failure")
    )
    return ResearchQualityInputV1(
        run_id=request.run_id,
        scope_id=context.scope_id,
        source_result_hash=result_hash,
        full_report_json=json.dumps(
            report, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ),
        accepted_evidence_refs=accepted,
        factual_coverage_complete=covered,
        direction_evidence_verified=covered,
        runtime_attestation_ref=context.runtime_attestation_ref if trusted else None,
        path_risk_model_qualified=context.path_risk_model_qualified if bound else False,
        path_risk_qualification_ref=context.path_risk_qualification_ref if bound else None,
        execution_support_verified=context.execution_support_verified if bound else False,
        required_horizons=("24h",),
        required_context_refs=required,
        retained_context_refs=selected,
        context_fidelity_verified=fidelity,
        challenges=tuple(challenges),
        key_risks=key_risks,
        unknowns=unknowns,
        declared_requirement_ids=tuple(
            item.requirement_id for item in request.evidence_requirements
        ),
        executable_requirement_ids=executable,
        unmapped_inquiries=tuple(state.get("loop_unmapped_inquiries", [])),
        budget_state=observed_budget_state(state, request, result, context),
        no_progress=int(state.get("no_progress_rounds", 0)) >= 2,
        monitor_state=context.monitor_state,
    )


def quality_decision_from_state(
    state: AgenticResearchState, *, context: QualityProjectionContextV1 | None = None
) -> QualityDecisionV1:
    return assess_research_quality(quality_input_from_state(state, context=context))

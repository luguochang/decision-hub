"""One pure quality decision for continuation and publication, independent of runtime."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    model_serializer,
    model_validator,
)

from ..ports.market_path import canonical_hash

QualityScope = Literal["direction", "path_risk", "execution", "report"]
QualityEffect = Literal["unchanged", "increased", "reduced", "reversed", "unknown"]


class FrozenQualityContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ResearchChallengeV1(FrozenQualityContract):
    schema_version: Literal["research-challenge.v1"] = "research-challenge.v1"
    challenge_id: str = Field(min_length=1)
    source_review_id: str | None = None
    severity: Literal["critical", "important", "minor"]
    blocking_scope: tuple[QualityScope, ...] = Field(min_length=1)
    evidence_refs: tuple[str, ...] = ()
    status: Literal["open", "answered", "rejected", "deferred"] = "open"
    disposition: Literal["accepted", "rebutted", "unresolved"] = "unresolved"
    resolution_evidence_refs: tuple[str, ...] = ()
    resolved_result_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    effect_on_direction: QualityEffect = "unknown"
    effect_on_tail_risk: QualityEffect = "unknown"
    effect_on_monitor: QualityEffect = "unknown"
    effect_on_report: QualityEffect = "unknown"
    reason: str = ""
    reviewer_version: str = ""

    @model_serializer(mode="wrap")
    def preserve_original_wire(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        value: dict[str, Any] = handler(self)
        # Old stored challenges have no report-effect field. Preserve their
        # original payload and hash; absent proof still means unknown.
        if "effect_on_report" not in self.model_fields_set:
            value.pop("effect_on_report", None)
        return value


class ResearchKeyRiskV1(FrozenQualityContract):
    risk_id: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    evidence_refs: tuple[str, ...] = ()
    unknown_reason: str | None = None
    affected_scope: tuple[QualityScope, ...] = Field(min_length=1)


class ResearchQualityInputV1(FrozenQualityContract):
    schema_version: Literal["research-quality-input.v1"] = "research-quality-input.v1"
    run_id: str = Field(min_length=1)
    scope_id: str = Field(min_length=1)
    source_result_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    full_report_json: str = Field(min_length=1)
    accepted_evidence_refs: tuple[str, ...] = ()
    factual_coverage_complete: bool = False
    direction_evidence_verified: bool = False
    path_risk_model_qualified: bool = False
    path_risk_qualification_ref: str | None = None
    execution_support_verified: bool = False
    runtime_attestation_ref: str | None = None
    required_horizons: tuple[Literal["30m", "24h", "72h"], ...] = ("24h",)
    required_context_refs: tuple[str, ...] = ()
    retained_context_refs: tuple[str, ...] = ()
    context_fidelity_verified: bool = False
    challenges: tuple[ResearchChallengeV1, ...] = ()
    key_risks: tuple[ResearchKeyRiskV1, ...] = ()
    unknowns: tuple[str, ...] = ()
    declared_requirement_ids: tuple[str, ...] = ()
    executable_requirement_ids: tuple[str, ...] = ()
    unmapped_inquiries: tuple[str, ...] = ()
    budget_state: Literal["available", "exhausted", "timeout", "failed", "unknown"] = "unknown"
    no_progress: bool = False
    monitor_state: Literal["active", "paused", "degraded", "unavailable", "unknown"] = "unknown"

    @model_validator(mode="after")
    def validate_binding(self) -> ResearchQualityInputV1:
        def reject_constant(_value):
            raise ValueError("research_quality_report_nonfinite")

        body = json.loads(self.full_report_json, parse_constant=reject_constant)
        if not isinstance(body, dict) or canonical_hash(body) != self.source_result_hash:
            raise ValueError("research_quality_result_hash_mismatch")
        if not set(self.executable_requirement_ids).issubset(self.declared_requirement_ids):
            raise ValueError("research_quality_undeclared_route")
        if len({item.challenge_id for item in self.challenges}) != len(self.challenges):
            raise ValueError("research_quality_duplicate_challenge")
        if not self.required_horizons or len(set(self.required_horizons)) != len(
            self.required_horizons
        ):
            raise ValueError("research_quality_horizons_invalid")
        return self


class QualityDecisionV1(FrozenQualityContract):
    schema_version: Literal["quality-decision.v1"] = "quality-decision.v1"
    run_id: str
    scope_id: str
    source_result_hash: str
    full_report_hash: str
    full_report_json: str
    status: Literal["continue", "sufficient", "partial", "unresolved"]
    should_continue: bool
    blocks_completion: bool
    publication_mode: Literal["complete", "partial", "research_only"]
    factual_status: Literal["complete", "incomplete"]
    direction_status: Literal["eligible", "blocked", "unknown"]
    path_risk_status: Literal["eligible", "blocked", "unknown"]
    execution_status: Literal["supported", "blocked", "unknown"]
    context_status: Literal["complete", "incomplete"]
    report_status: Literal["complete", "incomplete", "untrusted"]
    monitor_state: Literal["active", "paused", "degraded", "unavailable", "unknown"]
    unresolved_challenge_ids: tuple[str, ...]
    reason_codes: tuple[str, ...]
    retained_evidence_refs: tuple[str, ...]
    challenges: tuple[ResearchChallengeV1, ...]
    key_risks: tuple[ResearchKeyRiskV1, ...]
    unknowns: tuple[str, ...]
    unmapped_inquiries: tuple[str, ...]
    decision_hash: str

    @model_validator(mode="after")
    def validate_seal(self) -> QualityDecisionV1:
        if (
            hashlib.sha256(self.full_report_json.encode()).hexdigest() != self.full_report_hash
            or canonical_hash(json.loads(self.full_report_json)) != self.source_result_hash
        ):
            raise ValueError("research_quality_report_seal_mismatch")
        if (
            canonical_hash(self.model_dump(mode="json", exclude={"decision_hash"}))
            != self.decision_hash
        ):
            raise ValueError("research_quality_decision_seal_mismatch")
        return self


def _challenge_resolved(item, source_hash, accepted) -> bool:
    expected = {"answered": "accepted", "rejected": "rebutted"}.get(item.status)
    effects = {
        "direction": item.effect_on_direction,
        "path_risk": item.effect_on_tail_risk,
        "execution": item.effect_on_monitor,
        "report": item.effect_on_report,
    }
    return bool(
        expected is not None
        and item.disposition == expected
        and item.reason.strip()
        and item.reviewer_version.strip()
        and item.resolved_result_hash == source_hash
        and item.resolution_evidence_refs
        and set(item.resolution_evidence_refs).issubset(accepted)
        and all(effects[scope] != "unknown" for scope in item.blocking_scope)
    )


def assess_research_quality(value: ResearchQualityInputV1) -> QualityDecisionV1:
    """Caller provides attested eligibility/selection facts; this function cannot certify them.

    It does not call a model, edit a result, grant a capability, or create a probability.
    Sidecar identity binds exactly this report; old Runs require their frozen old recipe.
    """
    value = ResearchQualityInputV1.model_validate(value.model_dump())
    report = json.loads(value.full_report_json)
    accepted = set(value.accepted_evidence_refs)
    reasons, blocked, unresolved = [], set(), []
    supported_report = report.get("schema_version") == "research-session-result.v1"
    if not supported_report:
        reasons.append("research_report_schema_unsupported")
    context_complete = (
        bool(value.required_context_refs)
        and set(value.required_context_refs).issubset(value.retained_context_refs)
        and value.context_fidelity_verified
    )
    if not context_complete:
        reasons.append("protected_context_incomplete")
    causal = report.get("causal_case")
    causal = causal if isinstance(causal, dict) else {}
    main, counter = causal.get("main_chain"), causal.get("opposite_chain")
    chain_complete = (
        bool(causal.get("thesis"))
        and isinstance(main, list)
        and bool(main)
        and isinstance(counter, list)
        and bool(counter)
    )
    cited = set()
    if chain_complete:
        for link in [*main, *counter]:
            if (
                not isinstance(link, dict)
                or not link.get("statement")
                or not link.get("evidence_refs")
                or not link.get("confirmation")
                or not link.get("invalidation")
            ):
                chain_complete = False
                continue
            cited.update(link["evidence_refs"])
    horizons = report.get("horizons") or []
    selected = [
        item
        for item in horizons
        if isinstance(item, dict) and item.get("horizon") in value.required_horizons
    ]
    horizon_complete = len(selected) == len(value.required_horizons) and {
        item.get("horizon") for item in selected
    } == set(value.required_horizons)
    for item in selected:
        if not item.get("trigger") or not item.get("invalidation") or not item.get("evidence_refs"):
            horizon_complete = False
        cited.update(item.get("evidence_refs") or ())
    if not chain_complete:
        reasons.append("main_or_counter_chain_incomplete")
    if not horizon_complete:
        reasons.append("horizon_report_incomplete")
    if not cited or not cited.issubset(accepted):
        reasons.append("report_evidence_unverified")
    risks_complete = bool(value.key_risks)
    for item in value.key_risks:
        if item.evidence_refs:
            if not set(item.evidence_refs).issubset(accepted):
                risks_complete = False
        elif not item.unknown_reason or not item.unknown_reason.strip():
            risks_complete = False
    if not risks_complete:
        reasons.append("key_risks_missing_or_unverified")
    for item in value.challenges:
        if not _challenge_resolved(item, value.source_result_hash, accepted):
            unresolved.append(item.challenge_id)
            if item.severity in {"critical", "important"}:
                blocked.update(item.blocking_scope)
            reasons.append("challenge_unresolved:" + item.challenge_id)
        if not set(item.evidence_refs).issubset(accepted):
            blocked.update(item.blocking_scope)
            reasons.append("challenge_evidence_unverified:" + item.challenge_id)
    mapped_reviews = {item.source_review_id for item in value.challenges}
    for review in report.get("reviews") or []:
        if (
            isinstance(review, dict)
            and review.get("resolution") == "requires_new_evidence"
            and review.get("review_id") not in mapped_reviews
        ):
            identity = str(review.get("review_id") or "missing_review_id")
            unresolved.append("unmapped_review:" + identity)
            blocked.update(("direction", "path_risk", "report"))
            reasons.append("critical_review_not_classified:" + identity)
    report_complete = (
        chain_complete
        and horizon_complete
        and risks_complete
        and bool(cited)
        and cited.issubset(accepted)
    )
    attested = (
        supported_report
        and bool(value.runtime_attestation_ref)
        and not report.get("synthesis_failure_code")
        and report.get("status") not in {"failed", "cancelled"}
    )
    if not attested:
        reasons.append("research_synthesis_untrusted")
    if not value.factual_coverage_complete:
        reasons.append("factual_coverage_incomplete")
    direction = (
        "blocked"
        if "direction" in blocked or "report" in blocked
        else "eligible"
        if value.direction_evidence_verified
        and value.factual_coverage_complete
        and report_complete
        and context_complete
        and attested
        else "unknown"
    )
    path = (
        "blocked"
        if "path_risk" in blocked
        else "eligible"
        if value.path_risk_model_qualified
        and value.path_risk_qualification_ref
        and attested
        and context_complete
        else "unknown"
    )
    execution = (
        "blocked"
        if "execution" in blocked
        else "supported"
        if value.execution_support_verified and attested and context_complete
        else "unknown"
    )
    if path == "unknown":
        reasons.append("path_risk_qualification_unknown")
    if execution == "unknown":
        reasons.append("execution_support_unknown")
    full = (
        direction == "eligible"
        and path == "eligible"
        and execution == "supported"
        and "report" not in blocked
        and not unresolved
    )
    budget_available = value.budget_state == "available"
    can_continue = (
        budget_available and bool(value.executable_requirement_ids) and not value.no_progress
    )
    if value.budget_state != "available":
        reasons.append("research_budget_" + value.budget_state)
    if value.no_progress:
        reasons.append("research_no_progress")
    if not budget_available:
        status, publication = "unresolved", "research_only"
    elif full:
        status, publication = "sufficient", "complete"
    elif can_continue:
        status, publication = "continue", "partial" if direction == "eligible" else "research_only"
    elif direction == "eligible":
        status, publication = "partial", "partial"
    else:
        status, publication = "unresolved", "research_only"
    unknowns = list(value.unknowns)
    unknowns.extend(causal.get("unresolved_questions") or ())
    for item in selected:
        unknowns.extend(item.get("missing_facts") or ())
    unknowns.extend(item.unknown_reason for item in value.key_risks if item.unknown_reason)
    payload = dict(
        run_id=value.run_id,
        scope_id=value.scope_id,
        source_result_hash=value.source_result_hash,
        full_report_hash=hashlib.sha256(value.full_report_json.encode()).hexdigest(),
        full_report_json=value.full_report_json,
        status=status,
        should_continue=status == "continue",
        blocks_completion=status != "sufficient",
        publication_mode=publication,
        factual_status="complete" if value.factual_coverage_complete else "incomplete",
        direction_status=direction,
        path_risk_status=path,
        execution_status=execution,
        context_status="complete" if context_complete else "incomplete",
        report_status="untrusted"
        if not attested
        else "complete"
        if report_complete
        else "incomplete",
        monitor_state=value.monitor_state,
        unresolved_challenge_ids=tuple(dict.fromkeys(unresolved)),
        reason_codes=tuple(dict.fromkeys(reasons)),
        retained_evidence_refs=value.accepted_evidence_refs,
        challenges=value.challenges,
        key_risks=value.key_risks,
        unknowns=tuple(dict.fromkeys(unknowns)),
        unmapped_inquiries=value.unmapped_inquiries,
    )
    # Serialize typed sidecars before hashing; the decision is independently content-addressed.
    wire = {
        "schema_version": "quality-decision.v1",
        **payload,
        "challenges": [item.model_dump(mode="json") for item in value.challenges],
        "key_risks": [item.model_dump(mode="json") for item in value.key_risks],
    }
    return QualityDecisionV1(**payload, decision_hash=canonical_hash(wire))

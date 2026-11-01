from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Literal

from packages.contracts_py.decision_hub_contracts import (
    ConflictItem,
    CoverageAssessment,
    EvidenceCandidate,
    EvidenceGap,
    EvidenceRequirement,
    FactEnvelope,
)
from packages.kernel.decision_hub_kernel.decision.relationship_gate import (
    validate_baseline_after,
)


class EvidenceSufficiencyError(ValueError):
    """Raised when a coverage assessment cannot be built from typed evidence."""


GapReason = Literal[
    "missing",
    "stale",
    "low_authority",
    "insufficient_sources",
    "conflict",
    "semantic_mismatch",
    "no_baseline",
    "window_missing",
]

AUTHORITY_RANK = {
    "unverified": 0,
    "search_derived": 1,
    "verified_web": 2,
    "audited_aggregator": 3,
    "exchange": 4,
    "official": 5,
}


def assess_evidence_sufficiency(
    requirements: Iterable[EvidenceRequirement],
    evidence: Iterable[EvidenceCandidate],
    *,
    cutoff_at: datetime,
    assessed_at: datetime | None = None,
    facts: Iterable[FactEnvelope] = (),
) -> CoverageAssessment:
    """Evaluate freshness, authority, independence and conflicts deterministically.

    The model may suggest a gap, but this function is the product Gate that decides
    whether a requirement is covered. Evidence with a non-accepted quality or a
    future timestamp never contributes to coverage.
    """

    cutoff = _aware(cutoff_at)
    when = _aware(assessed_at or cutoff)
    requirement_list = tuple(requirements)
    by_requirement: dict[str, list[EvidenceCandidate]] = defaultdict(list)
    for item in evidence:
        by_requirement[item.requirement_id].append(item)
    facts_by_requirement: dict[str, list[FactEnvelope]] = defaultdict(list)
    for item in facts:
        facts_by_requirement[item.requirement_id].append(item)

    covered: list[str] = []
    gaps: list[EvidenceGap] = []
    conflicts: list[ConflictItem] = []
    hard_total = sum(item.importance == "hard" for item in requirement_list)
    soft_total = sum(item.importance == "soft" for item in requirement_list)
    hard_covered = 0
    soft_covered = 0

    for requirement in requirement_list:
        candidates = by_requirement.get(requirement.requirement_id, [])
        conflict_items = _conflicts(requirement, candidates)
        conflicts.extend(conflict_items)
        usable = [
            item
            for item in candidates
            if item.quality == "accepted"
            and item.freshness_status == "fresh"
            and _is_before_cutoff(item, cutoff)
            and _authority_matches(item, requirement)
        ]
        semantic_reason, semantic_evidence_ids, _semantic_groups = _semantic_assessment(
            requirement,
            usable,
            facts_by_requirement.get(requirement.requirement_id, []),
            cutoff,
        )
        if _has_semantic_policy(requirement):
            usable = [item for item in usable if item.evidence_id in semantic_evidence_ids]
        independent_sources = {item.source_id for item in usable}
        has_conflict = any(item.severity == "hard" for item in conflict_items)
        is_covered = (
            len(independent_sources) >= requirement.minimum_independent_sources
            and not has_conflict
            and semantic_reason is None
        )
        if is_covered:
            covered.append(requirement.requirement_id)
            if requirement.importance == "hard":
                hard_covered += 1
            else:
                soft_covered += 1
            continue

        reason = _gap_reason(
            requirement,
            candidates,
            usable,
            has_conflict,
            cutoff,
            semantic_reason=semantic_reason,
        )
        gaps.append(
            EvidenceGap(
                requirement_id=requirement.requirement_id,
                importance=requirement.importance,
                reason_code=reason,
                query_hint=_query_hint(requirement, reason),
                attempted_capabilities=[],
                blocks_directional_output=requirement.importance == "hard",
            )
        )

    hard_gaps = [item for item in gaps if item.importance == "hard"]
    status = "sufficient" if not hard_gaps else "insufficient"
    return CoverageAssessment(
        status=status,
        covered_requirement_ids=covered,
        gaps=gaps,
        conflicts=conflicts,
        hard_coverage_ratio=(hard_covered / hard_total if hard_total else 1.0),
        soft_coverage_ratio=(soft_covered / soft_total if soft_total else 1.0),
        assessed_at=when,
    )


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise EvidenceSufficiencyError("coverage_timestamp_must_be_timezone_aware")
    return value.astimezone(UTC)


def _is_before_cutoff(item: EvidenceCandidate, cutoff: datetime) -> bool:
    return item.observed_at <= cutoff and item.received_at <= cutoff


def _authority_matches(item: EvidenceCandidate, requirement: EvidenceRequirement) -> bool:
    priorities = {value.strip().lower() for value in requirement.source_priority}
    authority = item.authority.lower()
    floor = requirement.authority_floor
    try:
        floor_rank = AUTHORITY_RANK[floor]
        authority_rank = AUTHORITY_RANK[authority]
    except KeyError as exc:  # defensive fail-closed check for untrusted DTOs
        raise EvidenceSufficiencyError("unknown_evidence_authority") from exc
    return authority in priorities and authority_rank >= floor_rank


def _conflicts(
    requirement: EvidenceRequirement,
    candidates: Iterable[EvidenceCandidate],
) -> list[ConflictItem]:
    grouped: dict[str, list[EvidenceCandidate]] = defaultdict(list)
    for item in candidates:
        if item.conflict_group:
            grouped[item.conflict_group].append(item)
    result: list[ConflictItem] = []
    for group, items in sorted(grouped.items()):
        refs = list(dict.fromkeys(item.evidence_id for item in items))
        if len(refs) < 2:
            continue
        result.append(
            ConflictItem(
                conflict_id=f"conflict:{requirement.requirement_id}:{group}",
                requirement_id=requirement.requirement_id,
                evidence_refs=refs,
                severity=requirement.importance,
                summary=f"Evidence sources disagree within conflict group {group}.",
                resolution_status="open",
            )
        )
    return result


def _gap_reason(
    requirement: EvidenceRequirement,
    candidates: list[EvidenceCandidate],
    usable: list[EvidenceCandidate],
    has_conflict: bool,
    cutoff: datetime,
    *,
    semantic_reason: GapReason | None = None,
) -> GapReason:
    if has_conflict:
        return "conflict"
    if candidates and all(item.received_at > cutoff for item in candidates):
        return "missing"
    if candidates and not usable:
        if any(item.freshness_status == "stale" for item in candidates):
            return "stale"
        if any(not _authority_matches(item, requirement) for item in candidates):
            return "low_authority"
    if semantic_reason is not None:
        return semantic_reason
    if len({item.source_id for item in usable}) < requirement.minimum_independent_sources:
        return "insufficient_sources"
    return "missing"


def _query_hint(requirement: EvidenceRequirement, reason: GapReason) -> str:
    suffix = {
        "stale": "Find a fresher observation before the cutoff.",
        "conflict": "Resolve the conflicting sources with an authoritative source.",
        "low_authority": "Prefer the highest-priority source listed by the pack.",
        "insufficient_sources": "Find an independent source for the same requirement.",
        "semantic_mismatch": (
            "Acquire typed facts with the required metric family, fields and units."
        ),
        "no_baseline": "Acquire an event-relative baseline before claiming a delta.",
        "window_missing": "Acquire all required event-window offsets before claiming transmission.",
        "missing": "Acquire evidence for this requirement.",
    }[reason]
    return f"{requirement.description} {suffix}"


def _has_semantic_policy(requirement: EvidenceRequirement) -> bool:
    return bool(
        (requirement.accepted_metric_families or [])
        or (requirement.required_metric_families or [])
        or (requirement.required_fields or [])
        or (requirement.required_event_offsets or [])
        or (requirement.field_units or {})
        or (requirement.allowed_delay_classes or [])
        or bool(requirement.venue_required)
        or requirement.semantic_policy_ref
    )


def _semantic_assessment(
    requirement: EvidenceRequirement,
    usable_evidence: list[EvidenceCandidate],
    facts: list[FactEnvelope],
    cutoff: datetime,
) -> tuple[GapReason | None, set[str], set[str]]:
    if not _has_semantic_policy(requirement):
        return None, {item.evidence_id for item in usable_evidence}, {
            item.source_id for item in usable_evidence
        }

    evidence_ids = {item.evidence_id for item in usable_evidence}
    valid: list[FactEnvelope] = []
    for fact in facts:
        if (
            fact.quality != "accepted"
            or fact.evidence_id not in evidence_ids
            or fact.observed_at > cutoff
            or fact.received_at > cutoff
        ):
            continue
        if (
            (requirement.accepted_metric_families or [])
            and fact.metric_family not in (requirement.accepted_metric_families or [])
        ):
            continue
        if (
            (requirement.allowed_delay_classes or [])
            and fact.delay_class not in (requirement.allowed_delay_classes or [])
        ):
            continue
        allowed_units = (requirement.field_units or {}).get(fact.field)
        if allowed_units and fact.unit not in allowed_units:
            continue
        valid.append(fact)

    if not valid:
        return "semantic_mismatch", set(), set()

    metric_families = {item.metric_family for item in valid}
    if not set(requirement.required_metric_families or []) <= metric_families:
        return "semantic_mismatch", {item.evidence_id for item in valid}, {
            item.independence_group for item in valid
        }
    fields = {item.field for item in valid}
    if not set(requirement.required_fields or []) <= fields:
        return "semantic_mismatch", {item.evidence_id for item in valid}, {
            item.independence_group for item in valid
        }
    offsets = {item.event_offset for item in valid if item.event_offset is not None}
    missing_offsets = set(requirement.required_event_offsets or []) - offsets
    if missing_offsets:
        reason: GapReason = (
            "no_baseline"
            if any(item.startswith("t-") or item == "baseline" for item in missing_offsets)
            else "window_missing"
        )
        return reason, {item.evidence_id for item in valid}, {
            item.independence_group for item in valid
        }
    relationship_reason = _relationship_assessment(requirement, valid)
    if relationship_reason is not None:
        return relationship_reason, {item.evidence_id for item in valid}, {
            item.independence_group for item in valid
        }
    venues = {item.venue for item in valid if item.venue}
    if requirement.venue_required and len(venues) < (requirement.minimum_venues or 1):
        return "semantic_mismatch", {item.evidence_id for item in valid}, {
            item.independence_group for item in valid
        }
    groups = {item.independence_group for item in valid}
    if len(groups) < (requirement.minimum_independence_groups or 1):
        return "insufficient_sources", {item.evidence_id for item in valid}, groups
    return None, {item.evidence_id for item in valid}, groups


def _relationship_assessment(
    requirement: EvidenceRequirement,
    facts: list[FactEnvelope],
) -> GapReason | None:
    """Require event-window anchors to come from one logical observable.

    Aggregate offset checks alone can accidentally combine a baseline from one
    instrument/source with an after value from another.  Grouping first and
    passing each candidate pair through the relationship gate keeps the check
    provider-neutral while preserving requirements that only have one side of a
    window (for example a baseline-only policy fact).
    """

    required_offsets = set(requirement.required_event_offsets or [])
    baseline_offsets = required_offsets & {"baseline", "t-5m", "t-1h"}
    after_offsets = required_offsets & {"t0", "after", "t+1m", "t+5m"}
    if not baseline_offsets or not after_offsets:
        return None

    anchor_field = _relationship_anchor_field(requirement.required_fields or [])
    if anchor_field is None:
        return None
    groups: dict[tuple[str | None, str | None, str | None, str, str], list[FactEnvelope]] = (
        defaultdict(list)
    )
    for fact in facts:
        groups[
            (
                fact.metric_family,
                fact.instrument,
                fact.venue,
                fact.source_id,
                fact.independence_group,
            )
        ].append(fact)

    families = set(requirement.required_metric_families or [])
    for family in families or {None}:
        family_groups = [
            group
            for key, group in groups.items()
            if family is None or key[0] == family
        ]
        if any(
            _group_has_relationship(
                group,
                anchor_field=anchor_field,
                baseline_offsets=baseline_offsets,
                after_offsets=after_offsets,
                derived_fields=set(requirement.required_fields or []) - {anchor_field},
            )
            for group in family_groups
        ):
            continue
        return "semantic_mismatch"
    return None


def _relationship_anchor_field(required_fields: list[str]) -> str | None:
    for preferred in ("level", "price", "open_interest", "baseline"):
        if preferred in required_fields:
            return preferred
    return required_fields[0] if required_fields else None


def _group_has_relationship(
    facts: list[FactEnvelope],
    *,
    anchor_field: str,
    baseline_offsets: set[str],
    after_offsets: set[str],
    derived_fields: set[str],
) -> bool:
    baseline_facts = [
        fact
        for fact in facts
        if fact.field == anchor_field and fact.event_offset in baseline_offsets
    ]
    after_facts = [
        fact
        for fact in facts
        if fact.field == anchor_field and fact.event_offset in after_offsets
    ]
    for baseline in baseline_facts:
        for after in after_facts:
            event_id_valid, event_id = _relationship_event_id(baseline, after)
            if not event_id_valid:
                continue
            try:
                validate_baseline_after(
                    baseline,
                    after,
                    event_id=None if event_id is None else event_id,
                )
            except ValueError:
                continue
            return True
    for baseline in baseline_facts:
        for after in facts:
            if (
                after.field not in derived_fields
                or after.field not in {"event_return", "open_interest_delta", "delta"}
                or after.event_offset not in after_offsets
                or after.window_start_at is None
                or after.window_end_at is None
                or after.observed_at < baseline.observed_at
                or not (after.window_start_at <= baseline.observed_at <= after.window_end_at)
            ):
                continue
            event_id_valid, _event_id = _relationship_event_id(baseline, after)
            if event_id_valid:
                return True
    return False


def _relationship_event_id(
    baseline: FactEnvelope,
    after: FactEnvelope,
) -> tuple[bool, str | None]:
    values: list[str | None] = []
    for fact in (baseline, after):
        value = fact.attributes.get("event_id")
        if value is None:
            values.append(None)
        elif isinstance(value, str) and value.strip():
            values.append(value.strip())
        else:
            return False, None
    if any(value is not None for value in values) and (
        values[0] is None or values[1] is None or values[0] != values[1]
    ):
        return False, None
    return True, values[0]

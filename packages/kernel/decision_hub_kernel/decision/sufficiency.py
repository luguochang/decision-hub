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
)


class EvidenceSufficiencyError(ValueError):
    """Raised when a coverage assessment cannot be built from typed evidence."""


GapReason = Literal[
    "missing", "stale", "low_authority", "insufficient_sources", "conflict"
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
        independent_sources = {item.source_id for item in usable}
        has_conflict = any(item.severity == "hard" for item in conflict_items)
        is_covered = (
            len(independent_sources) >= requirement.minimum_independent_sources
            and not has_conflict
        )
        if is_covered:
            covered.append(requirement.requirement_id)
            if requirement.importance == "hard":
                hard_covered += 1
            else:
                soft_covered += 1
            continue

        reason = _gap_reason(requirement, candidates, usable, has_conflict, cutoff)
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
    if len({item.source_id for item in usable}) < requirement.minimum_independent_sources:
        return "insufficient_sources"
    return "missing"


def _query_hint(requirement: EvidenceRequirement, reason: GapReason) -> str:
    suffix = {
        "stale": "Find a fresher observation before the cutoff.",
        "conflict": "Resolve the conflicting sources with an authoritative source.",
        "low_authority": "Prefer the highest-priority source listed by the pack.",
        "insufficient_sources": "Find an independent source for the same requirement.",
        "missing": "Acquire evidence for this requirement.",
    }[reason]
    return f"{requirement.description} {suffix}"

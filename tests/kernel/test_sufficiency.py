from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from packages.contracts_py.decision_hub_contracts import (
    EvidenceCandidate,
    EvidenceRequirement,
)
from packages.kernel.decision_hub_kernel.decision.sufficiency import (
    EvidenceSufficiencyError,
    assess_evidence_sufficiency,
)

NOW = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)


def _requirement(**overrides: Any) -> EvidenceRequirement:
    values = {
        "requirement_id": "rates",
        "description": "US 2Y/10Y yields",
        "importance": "hard",
        "source_priority": ["official"],
        "authority_floor": "official",
        "preferred_capabilities": ["official.macro"],
        "freshness_seconds": 600,
        "minimum_independent_sources": 1,
        "allowed_fallbacks": ["web.search"],
        "confidence_cap": 0.5,
    }
    values.update(overrides)
    return EvidenceRequirement.model_validate(values)


def _evidence(**overrides: Any) -> EvidenceCandidate:
    values = {
        "evidence_id": "evidence-1",
        "requirement_id": "rates",
        "kind": "official",
        "authority": "official",
        "source_id": "treasury",
        "source_url": "https://home.treasury.gov/data",
        "published_at": NOW - timedelta(seconds=20),
        "observed_at": NOW - timedelta(seconds=10),
        "received_at": NOW - timedelta(seconds=5),
        "content_hash": "0" * 64,
        "excerpt": "2Y 3.9, 10Y 4.2",
        "structured_payload_ref": None,
        "tool_call_id": "tool-1",
        "research_session_id": "session-1",
        "round": 1,
        "quality": "accepted",
        "freshness_status": "fresh",
        "conflict_group": None,
    }
    values.update(overrides)
    return EvidenceCandidate.model_validate(values)


def test_hard_requirement_is_covered_only_by_fresh_authoritative_evidence() -> None:
    result = assess_evidence_sufficiency([_requirement()], [_evidence()], cutoff_at=NOW)
    assert result.status == "sufficient"
    assert result.hard_coverage_ratio == 1
    assert result.gaps == []


def test_authority_floor_rejects_lower_ranked_evidence_even_when_source_is_preferred() -> None:
    requirement = _requirement(
        source_priority=["official", "verified_web"],
        authority_floor="official",
    )
    result = assess_evidence_sufficiency(
        [requirement],
        [_evidence(authority="verified_web", kind="web")],
        cutoff_at=NOW,
    )
    assert result.status == "insufficient"
    assert result.gaps[0].reason_code == "low_authority"


def test_missing_or_stale_hard_requirement_blocks_direction() -> None:
    result = assess_evidence_sufficiency(
        [_requirement()],
        [_evidence(freshness_status="stale")],
        cutoff_at=NOW,
    )
    assert result.status == "insufficient"
    assert result.gaps[0].reason_code == "stale"
    assert result.gaps[0].blocks_directional_output is True


def test_independence_and_conflict_are_deterministic() -> None:
    requirement = _requirement(minimum_independent_sources=2)
    first = _evidence(evidence_id="evidence-1", source_id="official-a", conflict_group="rates")
    second = _evidence(evidence_id="evidence-2", source_id="official-b", conflict_group="rates")
    result = assess_evidence_sufficiency([requirement], [first, second], cutoff_at=NOW)
    assert result.status == "insufficient"
    assert result.gaps[0].reason_code == "conflict"
    assert result.conflicts[0].evidence_refs == ["evidence-1", "evidence-2"]


def test_naive_datetime_is_rejected() -> None:
    with pytest.raises(EvidenceSufficiencyError):
        assess_evidence_sufficiency(
            [_requirement()],
            [_evidence()],
            cutoff_at=datetime(2026, 8, 29, 12),
        )

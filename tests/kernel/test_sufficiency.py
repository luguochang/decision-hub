from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from packages.contracts_py.decision_hub_contracts import (
    EvidenceCandidate,
    EvidenceRequirement,
    FactEnvelope,
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


def _fact(**overrides: Any) -> FactEnvelope:
    values = {
        "schema_version": "fact-envelope.v1",
        "fact_id": "fact-1",
        "evidence_id": "evidence-1",
        "requirement_id": "rates",
        "metric_family": "macro.rates",
        "field": "level",
        "instrument": "DGS2",
        "venue": None,
        "value": 4.2,
        "unit": "yield_percent",
        "window_start_at": NOW - timedelta(minutes=5),
        "window_end_at": NOW - timedelta(minutes=4),
        "event_offset": "t-5m",
        "observed_at": NOW - timedelta(seconds=10),
        "received_at": NOW - timedelta(seconds=5),
        "published_at": NOW - timedelta(minutes=5),
        "source_id": "treasury",
        "independence_group": "treasury:DGS2",
        "quality": "accepted",
        "delay_class": "realtime",
        "payload_schema_ref": "test.fact.v1",
        "payload_hash": "1" * 64,
        "attributes": {},
    }
    values.update(overrides)
    return FactEnvelope.model_validate(values)


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


def test_crypto_derivatives_cannot_satisfy_policy_expectation_pricing() -> None:
    requirement = _requirement(
        requirement_id="expectation_pricing",
        source_priority=["exchange"],
        authority_floor="exchange",
        accepted_metric_families=["macro.policy_expectation"],
        required_metric_families=["macro.policy_expectation"],
        required_fields=["level", "delta"],
        required_event_offsets=["t-5m", "t+1m"],
        field_units={"level": ["probability"], "delta": ["percentage_point"]},
        allowed_delay_classes=["realtime"],
        semantic_policy_ref="crypto_macro.fact_semantics.v1#expectation_pricing",
    )
    evidence = _evidence(
        requirement_id="expectation_pricing",
        authority="exchange",
        kind="market",
    )
    fact = _fact(
        requirement_id="expectation_pricing",
        metric_family="crypto.derivatives",
        field="funding_rate",
        unit="rate",
    )

    result = assess_evidence_sufficiency(
        [requirement], [evidence], facts=[fact], cutoff_at=NOW
    )

    assert result.status == "insufficient"
    assert result.gaps[0].reason_code == "semantic_mismatch"


def test_current_snapshot_cannot_satisfy_an_event_window_delta() -> None:
    requirement = _requirement(
        accepted_metric_families=["crypto.spot"],
        required_metric_families=["crypto.spot"],
        required_fields=["price", "volume", "event_return"],
        required_event_offsets=["t-5m", "t+1m"],
        field_units={
            "price": ["usdt"],
            "volume": ["btc"],
            "event_return": ["percent"],
        },
        venue_required=True,
        allowed_delay_classes=["realtime"],
        semantic_policy_ref="crypto_macro.fact_semantics.v1#crypto_spot",
    )
    facts = [
        _fact(
            fact_id="price",
            metric_family="crypto.spot",
            field="price",
            unit="usdt",
            venue="coinex",
            event_offset="t+1m",
        ),
        _fact(
            fact_id="volume",
            metric_family="crypto.spot",
            field="volume",
            unit="btc",
            venue="coinex",
            event_offset="t+1m",
        ),
        _fact(
            fact_id="return",
            metric_family="crypto.spot",
            field="event_return",
            unit="percent",
            venue="coinex",
            event_offset="t+1m",
        ),
    ]

    result = assess_evidence_sufficiency(
        [requirement], [_evidence()], facts=facts, cutoff_at=NOW
    )

    assert result.status == "insufficient"
    assert result.gaps[0].reason_code == "no_baseline"


def test_wrong_fact_unit_is_a_semantic_mismatch() -> None:
    requirement = _requirement(
        accepted_metric_families=["macro.rates"],
        required_fields=["level"],
        field_units={"level": ["yield_percent"]},
        allowed_delay_classes=["realtime"],
        semantic_policy_ref="crypto_macro.fact_semantics.v1#rates",
    )
    result = assess_evidence_sufficiency(
        [requirement],
        [_evidence()],
        facts=[_fact(unit="usd")],
        cutoff_at=NOW,
    )
    assert result.gaps[0].reason_code == "semantic_mismatch"


def test_complete_semantic_fact_set_covers_requirement() -> None:
    requirement = _requirement(
        accepted_metric_families=["macro.rates"],
        required_metric_families=["macro.rates"],
        required_fields=["level", "event_return"],
        required_event_offsets=["t-5m", "t+1m"],
        field_units={"level": ["yield_percent"], "event_return": ["bps"]},
        allowed_delay_classes=["realtime"],
        semantic_policy_ref="crypto_macro.fact_semantics.v1#rates",
    )
    facts = [
        _fact(fact_id="baseline", field="level", event_offset="t-5m"),
        _fact(fact_id="reaction", field="event_return", unit="bps", event_offset="t+1m"),
    ]
    result = assess_evidence_sufficiency(
        [requirement], [_evidence()], facts=facts, cutoff_at=NOW
    )
    assert result.status == "sufficient"
    assert result.covered_requirement_ids == ["rates"]

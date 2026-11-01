from __future__ import annotations

from types import SimpleNamespace

from packages.query_views.research.role_packets import build_role_packets


def test_role_packets_are_bounded_and_explicit_when_evidence_is_missing() -> None:
    packets, transitions = build_role_packets(
        run_id="run-role-packets",
        rounds=[],
        evidence=[],
        coverage=None,
        causal_case=None,
        action_summary=SimpleNamespace(
            research_outlook=SimpleNamespace(outlook="unresolved"),
            headline="研究资料不足",
        ),
        reviews=[],
        trace=[],
    )

    roles = {packet.role for packet in packets}
    assert {"manager", "market_structure", "data_quality", "synthesizer"} <= roles
    assert all(packet.schema_version == "role-packet.v1" for packet in packets)
    assert all(packet.status == "blocked" for packet in packets if packet.role != "manager")
    assert transitions == []


def test_role_packet_ids_are_stable_for_same_snapshot() -> None:
    kwargs = dict(
        run_id="run-role-packets",
        rounds=[],
        evidence=[],
        coverage=None,
        causal_case=None,
        action_summary=SimpleNamespace(research_outlook=None, headline="bounded"),
        reviews=[],
        trace=[],
    )
    first, _ = build_role_packets(**kwargs)
    second, _ = build_role_packets(**kwargs)
    assert [item.packet_id for item in first] == [item.packet_id for item in second]


def test_counter_packet_follows_accepted_citations_across_requirements():
    evidence = [SimpleNamespace(
        evidence_id=identity, requirement_id="macro_transmission", quality=quality,
        source_id="source", excerpt="observation",
    ) for identity, quality in [("accepted", "accepted"), ("pending", "candidate")]]
    causal = SimpleNamespace(
        opposite_chain=[SimpleNamespace(
            statement="Counter explanation", evidence_refs=["accepted", "pending", "missing"],
        )], main_chain=[], evidence_refs=["accepted"], thesis="Research",
    )
    coverage = SimpleNamespace(
        gaps=[], status="sufficient", hard_coverage_ratio=1.0, soft_coverage_ratio=1.0,
    )
    kwargs = dict(
        run_id="counter-citations", rounds=[], evidence=evidence, coverage=coverage,
        causal_case=causal, action_summary=None, reviews=[], trace=[],
    )
    packets, _ = build_role_packets(**kwargs)
    counter = next(item for item in packets if item.role == "counter_thesis")
    assert counter.evidence_refs == ["accepted"]
    assert counter.status == "sufficient"
    assert counter.claims == ["Counter explanation"]
    # A cited requirement's open gap must still constrain the counter packet.
    coverage.gaps = [SimpleNamespace(requirement_id="macro_transmission")]
    packets, _ = build_role_packets(**kwargs)
    counter = next(item for item in packets if item.role == "counter_thesis")
    assert counter.gap_ids == ["macro_transmission"]
    assert counter.status == "partial"


def test_empty_role_does_not_claim_verified_no_gaps():
    packets, _ = build_role_packets(
        run_id="empty-role", rounds=[], evidence=[],
        coverage=SimpleNamespace(
            gaps=[], status="sufficient", hard_coverage_ratio=1.0, soft_coverage_ratio=1.0,
        ), causal_case=None, action_summary=None, reviews=[], trace=[],
    )
    counter = next(item for item in packets if item.role == "counter_thesis")
    assert counter.status == "blocked"
    assert "不能据此认定职责充分" in counter.conclusion

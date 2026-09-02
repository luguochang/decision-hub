from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from packages.contracts_py.decision_hub_contracts import EvidenceCandidate
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    research_evidence_content_hash,
)
from packages.provider_adapters.research import CryptoMacroFactPack, FactReplayManifest

PACK_ROOT = Path(__file__).resolve().parents[2] / "packs" / "crypto_macro"
NOW = datetime(2026, 8, 29, 4, 0, tzinfo=UTC)


def _evidence(requirement_id: str, source_id: str, *, stale: bool = False) -> EvidenceCandidate:
    observed = NOW - timedelta(days=2 if stale else 0, seconds=10)
    published = observed - timedelta(seconds=1)
    excerpt = f"{requirement_id} observed from {source_id}."
    content_hash = research_evidence_content_hash(
        requirement_id=requirement_id,
        kind="market" if source_id.startswith("exchange") else "official",
        authority="exchange" if source_id.startswith("exchange") else "official",
        source_id=source_id,
        source_url=None,
        published_at=published,
        excerpt=excerpt,
        structured_payload_ref=None,
    )
    return EvidenceCandidate(
        evidence_id=f"ev-{requirement_id}-{source_id}",
        requirement_id=requirement_id,
        kind="market" if source_id.startswith("exchange") else "official",
        authority="exchange" if source_id.startswith("exchange") else "official",
        source_id=source_id,
        source_url=None,
        published_at=published,
        observed_at=observed,
        received_at=observed,
        content_hash=content_hash,
        excerpt=excerpt,
        structured_payload_ref=None,
        tool_call_id=None,
        research_session_id="fact-pack-test",
        round=1,
        quality="accepted",
        freshness_status="fresh" if not stale else "stale",
        conflict_group=None,
    )


def test_crypto_macro_fact_pack_locks_six_minimum_requirements_and_ladders() -> None:
    pack = CryptoMacroFactPack.from_pack(PACK_ROOT)

    assert [item.requirement_id for item in pack.requirements] == [
        "event.identity",
        "policy.delta",
        "expectation.pricing",
        "macro.transmission",
        "crypto.spot",
        "crypto.derivatives",
    ]
    assert pack.capability_ladder("event.identity") == (
        "official.macro",
        "web.fetch",
        "web.search",
    )
    assert pack.requirement("crypto.derivatives").canonical_requirement_id == (
        "derivatives_crowding"
    )
    all_requirements = pack.all_contract_requirements()
    assert len(all_requirements) == 8
    event_identity = next(
        item for item in all_requirements if item.requirement_id == "event_identity"
    )
    assert event_identity.authority_floor == "official"
    assert event_identity.preferred_capabilities == [
        "official.macro",
        "web.fetch",
        "web.search",
    ]


def test_fact_pack_assessment_is_fail_closed_when_a_hard_fact_is_stale() -> None:
    pack = CryptoMacroFactPack.from_pack(PACK_ROOT)
    evidence = [
        _evidence(item.canonical_requirement_id, "exchange-a")
        for item in pack.requirements
        if item.requirement_id != "crypto.derivatives"
    ]
    evidence.append(_evidence("derivatives_crowding", "exchange-a", stale=True))

    assessment = pack.assess(evidence, cutoff_at=NOW)

    assert assessment.status == "research_only"
    assert "crypto.derivatives" in assessment.missing_requirement_ids


def test_g2_replay_manifest_has_success_stale_and_provider_failure_for_each_fact() -> None:
    manifest = FactReplayManifest.load(PACK_ROOT / "evidence" / "g2_replay_manifest.json")

    assert manifest.pack_id == "crypto_macro.v1"
    assert set(manifest.requirements) == {
        "event.identity",
        "policy.delta",
        "expectation.pricing",
        "macro.transmission",
        "crypto.spot",
        "crypto.derivatives",
    }
    for variants in manifest.requirements.values():
        assert variants["success"].status == "sufficient"
        assert variants["stale"].status == "stale"
        assert variants["provider_failure"].error_code in {
            "search_provider_failed",
            "research_capability_timeout",
        }

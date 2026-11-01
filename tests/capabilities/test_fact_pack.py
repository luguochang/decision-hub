from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from packages.contracts_py.decision_hub_contracts import EvidenceCandidate, FactEnvelope
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
    assert len(all_requirements) == 10
    event_identity = next(
        item for item in all_requirements if item.requirement_id == "event_identity"
    )
    assert event_identity.authority_floor == "official"
    assert event_identity.preferred_capabilities == [
        "official.macro",
        "web.fetch",
        "web.search",
    ]
    market_history = next(
        item for item in all_requirements if item.requirement_id == "market_regime_context"
    )
    assert market_history.importance == "soft"
    assert market_history.preferred_capabilities == ["data.historical"]
    assert market_history.accepted_metric_families == ["crypto.spot.history"]
    assert market_history.required_fields == [
        "period_close",
        "sma_20",
        "sma_60",
        "return_20_pct",
        "realized_vol_20_pct",
        "volume_ratio_20",
        "rolling_high_20",
        "rolling_low_20",
        "row_count",
    ]
    market_narrative = next(
        item for item in all_requirements if item.requirement_id == "market_narrative"
    )
    assert market_narrative.importance == "soft"
    assert market_narrative.preferred_capabilities == ["web.search"]
    assert market_narrative.allowed_fallbacks == ["web.fetch"]
    assert market_narrative.semantic_policy_ref is None
    assert market_narrative.required_fields == []


def test_fact_pack_selects_hard_requirements_by_event_family() -> None:
    pack = CryptoMacroFactPack.from_pack(PACK_ROOT)

    monetary = {
        item.requirement_id: item.importance
        for item in pack.contract_requirements_for_event("monetary_policy_decision")
    }
    geopolitical = {
        item.requirement_id: item.importance
        for item in pack.contract_requirements_for_event("geopolitical_shock")
    }
    unknown = {
        item.requirement_id: item.importance
        for item in pack.contract_requirements_for_event("unknown_event")
    }

    assert monetary["expectation_pricing"] == "hard"
    assert monetary["macro_transmission"] == "hard"
    assert geopolitical["expectation_pricing"] == "soft"
    assert geopolitical["macro_transmission"] == "soft"
    assert geopolitical["crypto_spot_confirmation"] == "hard"
    assert geopolitical["derivatives_crowding"] == "hard"
    assert unknown == monetary
    assert "market_narrative" not in monetary
    assert "market_narrative" not in geopolitical


def test_fact_pack_uses_current_snapshot_semantics_for_live_btc_input() -> None:
    pack = CryptoMacroFactPack.from_pack(PACK_ROOT)

    requirements = {
        item.requirement_id: item
        for item in pack.contract_requirements_for_event("crypto_market_snapshot")
    }
    legacy_named = {
        item.requirement_id: item
        for item in pack.contract_requirements_for_event("btc-current-snapshot-006")
    }

    assert {
        item.requirement_id for item in requirements.values() if item.importance == "hard"
    } == {"crypto_spot_confirmation", "market_regime_context"}
    assert requirements["derivatives_crowding"].importance == "soft"
    assert set(requirements) == {
        "macro_transmission",
        "cross_asset_confirmation",
        "crypto_spot_confirmation",
        "derivatives_crowding",
        "market_regime_context",
        "market_narrative",
    }
    assert requirements["crypto_spot_confirmation"].required_fields == ["price", "volume"]
    assert requirements["crypto_spot_confirmation"].required_event_offsets == []
    assert requirements["crypto_spot_confirmation"].minimum_independent_sources == 2
    assert requirements["crypto_spot_confirmation"].minimum_venues == 2
    assert requirements["crypto_spot_confirmation"].minimum_independence_groups == 2
    assert requirements["derivatives_crowding"].required_fields == [
        "funding_rate",
        "open_interest",
        "open_interest_delta",
        "basis",
        "crowding_signal",
    ]
    assert requirements["derivatives_crowding"].required_event_offsets == []
    assert requirements["derivatives_crowding"].minimum_independent_sources == 2
    assert requirements["derivatives_crowding"].minimum_venues == 2
    assert requirements["derivatives_crowding"].minimum_independence_groups == 2
    assert legacy_named == requirements


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


def test_fact_pack_assessment_passes_typed_facts_to_the_semantic_gate() -> None:
    pack = CryptoMacroFactPack.from_pack(PACK_ROOT)
    evidence = [_evidence("crypto_spot_confirmation", "exchange-a")]
    event_at = NOW - timedelta(minutes=10)

    def fact(
        fact_id: str,
        field: str,
        unit: str,
        offset: str,
        observed_at: datetime,
        *,
        window_start_at: datetime,
        window_end_at: datetime,
        value: float | str,
    ) -> FactEnvelope:
        return FactEnvelope(
            schema_version="fact-envelope.v1",
            fact_id=fact_id,
            evidence_id=evidence[0].evidence_id,
            requirement_id="crypto_spot_confirmation",
            metric_family="crypto.spot",
            field=field,
            instrument="BTC-USDT",
            venue="okx",
            value=value,
            unit=unit,
            window_start_at=window_start_at,
            window_end_at=window_end_at,
            event_offset=offset,
            observed_at=observed_at,
            received_at=observed_at,
            published_at=observed_at,
            source_id="exchange-a",
            independence_group="exchange-a:BTC-USDT",
            quality="accepted",
            delay_class="realtime",
            payload_schema_ref="test.crypto-spot.v1",
            payload_hash="1" * 64,
            attributes={"event_id": "event-spot"},
        )

    baseline_at = event_at - timedelta(minutes=5)
    after_at = event_at + timedelta(minutes=1)
    typed_facts = [
        fact(
            "spot-price-baseline",
            "price",
            "usdt",
            "t-5m",
            baseline_at,
            window_start_at=baseline_at,
            window_end_at=baseline_at,
            value=60000.0,
        ),
        fact(
            "spot-price-after",
            "price",
            "usdt",
            "t+1m",
            after_at,
            window_start_at=after_at,
            window_end_at=after_at,
            value=60100.0,
        ),
        fact(
            "spot-volume-after",
            "volume",
            "btc",
            "t+1m",
            after_at,
            window_start_at=after_at,
            window_end_at=after_at,
            value=12.0,
        ),
        fact(
            "spot-return",
            "event_return",
            "percent",
            "t+1m",
            after_at,
            window_start_at=baseline_at,
            window_end_at=after_at,
            value=0.1667,
        ),
    ]

    without_facts = pack.assess(evidence, cutoff_at=NOW)
    with_facts = pack.assess(evidence, facts=typed_facts, cutoff_at=NOW)

    assert "crypto.spot" in without_facts.missing_requirement_ids
    assert "crypto.spot" in with_facts.covered_requirement_ids


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

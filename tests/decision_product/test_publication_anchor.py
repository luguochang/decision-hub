"""Frozen pre-publication identity becomes one acyclic, deterministic target."""
from datetime import UTC, datetime

import pytest

from packages.kernel.decision_hub_kernel.decision.publication_anchor import freeze_publication_anchor
from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash


def ref(name):
    return {"ref": name, "hash": canonical_hash(name)}


def pending(horizon="4h"):
    return {
        "schema_version": "pending-publication-target.v1", "target_id": "btc-core-slot",
        "instrument_ref": ref("okx-btc-swap-metadata"), "horizon": horizon,
        "duration_seconds": {"4h": 14400, "24h": 86400, "72h": 259200}[horizon],
        "anchor": "publication_bound", "grid_seconds": 60,
        "clock_rule": "strict_next_utc_minute", "clock_policy_ref": ref("clock-v1"),
        "price_basis": "trade_1m_close", "threshold_fraction": "0.01",
        "method_ref": ref("structured-subjective-v1"), "target_family_ref": ref("btc-4h-family"),
        "label_rule_ref": ref("direction-band-v1"), "protocol_ref": ref("hourly-slot-v1"),
        "conditioning_ref": None, "valid_until": "2026-10-19T12:00:00Z",
        "invalidation_refs": [ref("frozen-price-domain")],
    }


def inputs(horizon="4h"):
    target = pending(horizon)
    target_ref = {"ref": "pending-target-1", "hash": canonical_hash(target)}
    rule = {k: target[k] for k in (
        "anchor", "horizon", "duration_seconds", "grid_seconds", "clock_rule", "price_basis",
        "threshold_fraction", "method_ref", "clock_policy_ref", "valid_until", "invalidation_refs",
    )}
    rule.update(schema_version="publication-anchor-rule.v1", candidate_target_ref=target_ref)
    return {
        "candidate_target": target, "anchor_rule": rule, "candidate_target_ref": target_ref,
        "anchor_rule_ref": {"ref": "anchor-rule-1", "hash": canonical_hash(rule)},
        "opportunity_id": "slot-owner-account-btc-4h", "commit_id": "publish-original-command",
    }


@pytest.mark.parametrize(("assigned", "start", "end", "horizon"), [
    ("2026-10-19T10:05:00+00:00", "2026-10-19T10:06:00Z", "2026-10-19T14:06:00Z", "4h"),
    ("2026-10-19T10:05:59.999999+00:00", "2026-10-19T10:06:00Z", "2026-10-20T10:06:00Z", "24h"),
    ("2026-10-18T23:59:59+00:00", "2026-10-19T00:00:00Z", "2026-10-22T00:00:00Z", "72h"),
])
def test_anchor_preserves_reviewed_inputs_and_never_rounds_back(assigned, start, end, horizon):
    values = inputs(horizon)
    original = canonical_hash(values)
    frozen = freeze_publication_anchor(**values, assigned_at=datetime.fromisoformat(assigned))
    target, receipt = frozen.target.model_dump(mode="json"), frozen.receipt.model_dump(mode="json")
    assert target["window_start"] == start and target["window_end"] == end
    assert receipt["target_start"] == start and receipt["target_end"] == end
    assert target["candidate_target_ref"] == values["candidate_target_ref"]
    assert receipt["final_target_ref"] == frozen.target_ref.model_dump(mode="json")
    assert frozen.target_ref.hash == canonical_hash(target)
    assert frozen.receipt_ref.hash == canonical_hash(receipt)
    assert canonical_hash(values) == original
    assert freeze_publication_anchor(**values, assigned_at=datetime.fromisoformat(assigned)) == frozen


@pytest.mark.parametrize("mutation", ["target_body", "rule_body", "rehashed_other_horizon"])
def test_anchor_rejects_substitution_even_when_shape_remains_valid(mutation):
    values = inputs()
    if mutation == "target_body":
        values["candidate_target"]["instrument_ref"] = ref("eth-instead-of-btc")
    elif mutation == "rule_body":
        values["anchor_rule"]["threshold_fraction"] = "0.02"
    else:
        values["anchor_rule"]["horizon"] = "24h"
        values["anchor_rule"]["duration_seconds"] = 86400
        values["anchor_rule_ref"]["hash"] = canonical_hash(values["anchor_rule"])
    with pytest.raises(ValueError, match="publication_anchor_(?:source_hash|rule_binding)_mismatch"):
        freeze_publication_anchor(**values, assigned_at=datetime(2026, 10, 19, 10, tzinfo=UTC))


def test_expired_candidate_cannot_gain_a_fresh_window_or_use_a_naive_clock():
    with pytest.raises(ValueError, match="publication_anchor_candidate_expired"):
        freeze_publication_anchor(**inputs(), assigned_at=datetime(2026, 10, 19, 12, tzinfo=UTC))
    with pytest.raises(ValueError, match="timezone_required"):
        freeze_publication_anchor(**inputs(), assigned_at=datetime(2026, 10, 19, 10))

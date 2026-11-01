from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from packages.kernel.decision_hub_kernel.decision.action_features import (
    action_feature_manifest,
    action_feature_window,
    build_action_features,
    condition_sample_interval_seconds,
)
from packages.kernel.decision_hub_kernel.ports.action_monitor import (
    ConditionEvaluationV2,
    PerConditionResultV2,
)
from packages.kernel.decision_hub_kernel.ports.market_path import (
    MarketCandle,
    canonical_hash,
    spot_scope,
)

T0 = datetime(2026, 10, 13, tzinfo=UTC)
ASOF = T0 + timedelta(hours=21, minutes=30, seconds=30)
SCOPE = spot_scope("BTC-USDT")


@pytest.fixture(scope="module")
def candles():
    result = []
    for i in range(1290):
        start = T0 + timedelta(minutes=i)
        close = Decimal("100") + Decimal(i) / Decimal("1000")
        result.append(MarketCandle(
            scope_id=SCOPE.scope_id, venue="okx", instrument="BTC-USDT",
            open_at=start, close_at=start + timedelta(minutes=1),
            open=close, high=close + 1, low=close - 1, close=close,
            volume_base=Decimal(2 if 1200 <= i < 1260 else 1),
            received_at=start + timedelta(minutes=1, seconds=1),
            accepted_at=start + timedelta(minutes=1, seconds=2),
            capture_ref="raw:" + str(i // 300), capture_hash="a" * 64, row_index=i % 300,
        ))
    return tuple(result)


def values(snapshot):
    return {value.feature: value for value in snapshot.features}


def test_six_features_arithmetic_clocks_and_original_hash(candles):
    snapshot = build_action_features(SCOPE, candles, as_of=ASOF)
    found = values(snapshot)
    assert len(found) == 6 and snapshot.gaps == ()
    assert found["close"].value == Decimal("101.289")
    assert found["box_high_6h"].value == Decimal("102.289")
    assert found["box_low_6h"].value == Decimal("99.930")
    assert found["risk_unit_20h"].value == Decimal("2.059")
    assert found["volume_ratio_1h"].value == Decimal(2)
    # 101.289 / 100.929 - 1 = 360 / 100929; 18 decimal places.
    assert found["return_6h"].value == Decimal("0.003566863834973100")
    assert found["close"].observed_at == ASOF.replace(second=0)
    assert found["volume_ratio_1h"].observed_at == T0 + timedelta(hours=21)
    assert found["volume_ratio_1h"].known_at == T0 + timedelta(hours=21, seconds=2)
    body = json.loads(snapshot.payload_json)
    assert canonical_hash(body) == snapshot.input_hash
    assert canonical_hash(action_feature_manifest("BTC-USDT")) == snapshot.manifest_hash
    assert snapshot.feature_map()["close", SCOPE.scope_id] == found["close"]


def test_later_clock_does_not_make_same_hour_input_new(candles):
    one = build_action_features(SCOPE, candles, as_of=ASOF)
    two = build_action_features(SCOPE, candles, as_of=ASOF + timedelta(seconds=15))
    assert one.input_hash != two.input_hash
    assert values(one)["volume_ratio_1h"] == values(two)["volume_ratio_1h"]
    assert one.closed_bar_at == two.closed_bar_at


def test_missing_old_bar_only_blocks_its_dependent_features(candles):
    snap = build_action_features(SCOPE, candles[:10] + candles[11:], as_of=ASOF)
    found = values(snap)
    assert found["close"].quality == found["box_low_6h"].quality == "accepted"
    assert found["risk_unit_20h"].quality == found["volume_ratio_1h"].quality == "unknown"
    assert len(snap.gaps) == 2


def test_capture_lag_uses_latest_known_bar_without_relabeling_its_age(candles):
    snap = build_action_features(SCOPE, candles[:-1], as_of=ASOF)
    found = values(snap)
    assert found["close"].value == candles[-2].close
    assert found["close"].observed_at == candles[-2].close_at
    assert found["return_6h"].value is not None
    assert found["risk_unit_20h"].value == Decimal("2.059")
    assert snap.closed_bar_at == candles[-2].close_at


def test_hour_boundary_lag_uses_previous_complete_hours(candles):
    previous = tuple(bar.model_copy(update={
        "open_at": bar.open_at - timedelta(hours=1),
        "close_at": bar.close_at - timedelta(hours=1),
        "received_at": bar.received_at - timedelta(hours=1),
        "accepted_at": bar.accepted_at - timedelta(hours=1),
    }) for bar in candles[:60])
    cutoff = T0 + timedelta(hours=21)
    snapshot = build_action_features(SCOPE, (*previous, *candles[:1259]), as_of=cutoff)
    found = values(snapshot)
    assert found["close"].observed_at == cutoff - timedelta(minutes=1)
    assert found["risk_unit_20h"].value == Decimal("2.059")
    assert found["volume_ratio_1h"].value == 1
    assert found["volume_ratio_1h"].observed_at == cutoff - timedelta(hours=1)


@pytest.mark.parametrize("change,code", [
    ({"received_at": ASOF + timedelta(seconds=1)}, "outside_known_window"),
    ({"scope_id": "wrong"}, "scope_mismatch"),
    ({"capture_ref": None}, "original_reference_missing"),
])
def test_invalid_input_rejected(candles, change, code):
    wrong = (*candles[:-1], candles[-1].model_copy(update=change))
    with pytest.raises(ValueError, match=code):
        build_action_features(SCOPE, wrong, as_of=ASOF)


def test_duplicate_revision_is_not_a_new_bar(candles):
    with pytest.raises(ValueError, match="duplicate_bar"):
        build_action_features(SCOPE, (*candles, candles[-1]), as_of=ASOF)


def test_missing_volume_not_synthetic_zero(candles):
    rows = list(candles)
    rows[50] = rows[50].model_copy(update={"volume_base": None})
    found = values(build_action_features(SCOPE, tuple(rows), as_of=ASOF))
    assert found["volume_ratio_1h"].value is None
    assert found["risk_unit_20h"].quality == "accepted"


def test_zero_historical_volume_and_range_are_unknown(candles):
    rows = tuple(bar.model_copy(update={
        "volume_base": Decimal(0), "open": Decimal(100), "high": Decimal(100),
        "low": Decimal(100), "close": Decimal(100),
    }) for bar in candles)
    snapshot = build_action_features(SCOPE, rows, as_of=ASOF)
    found = values(snapshot)
    assert found["volume_ratio_1h"].value is None
    assert found["risk_unit_20h"].value is None
    assert found["close"].value == Decimal(100)


def test_utc_window_and_cadence():
    start, end = action_feature_window(ASOF)
    assert start == T0 - timedelta(hours=1) and end == ASOF.replace(second=0)
    assert condition_sample_interval_seconds(("close", "risk_unit_20h")) == 60
    assert condition_sample_interval_seconds(("volume_ratio_1h", "risk_unit_20h")) == 3600
    with pytest.raises(ValueError, match="cadence_undefined"):
        condition_sample_interval_seconds(("not_registered",))
    with pytest.raises(ValueError, match="timezone"):
        action_feature_window(datetime(2026, 10, 13))


def test_unknown_inputs_keep_all_six_named_gaps():
    snapshot = build_action_features(SCOPE, (), as_of=ASOF)
    assert len(snapshot.gaps) == 6 and snapshot.closed_bar_at is None
    assert all(row.value is None and row.quality == "unknown" for row in snapshot.features)


def test_eth_has_independent_manifest_and_units(candles):
    eth = spot_scope("ETH-USDT")
    rows = tuple(bar.model_copy(update={"scope_id": eth.scope_id, "instrument": eth.instrument})
                 for bar in candles)
    snapshot = build_action_features(eth, rows, as_of=ASOF)
    assert values(snapshot)["close"].unit == "USDT/ETH"
    assert snapshot.manifest_hash != canonical_hash(action_feature_manifest("BTC-USDT"))


def condition_result():
    return {"id": "entry-close", "truth": "true", "raw_truth": "true", "status": "ok",
            "reason": None, "input_refs": ["action-feature:abc"], "confirmation_count": 2,
            "last_bar_at": ASOF.isoformat(), "confirmed": True}


def evaluation():
    return {
        "schema_version": "decision-condition-evaluation.v2", "id": "eval-1", "plan_id": "p1",
        "revision_id": "r1", "tick_at": ASOF.isoformat(), "as_of": ASOF.isoformat(),
        "evaluated_at": ASOF.isoformat(), "lease_generation": 1, "input_ref": "input-1",
        "input_hash": "a" * 64, "truth": "true", "status": "ok",
        "per_condition": [condition_result()], "state_before": "armed", "state_after": "triggered",
        "evaluation_hash": "b" * 64, "state_version_before": 1, "state_version_after": 2,
        "monitor_state_before": "active", "monitor_state_after": "active",
    }


def test_v2_parses_condition_continuity():
    parsed = ConditionEvaluationV2.model_validate(evaluation())
    assert parsed.per_condition[0].confirmation_count == 2


@pytest.mark.parametrize("update", [
    {"raw_truth": "unknown"}, {"confirmation_count": 0}, {"last_bar_at": None},
    {"confirmed": False}, {"status": "error"},
])
def test_v2_rejects_false_confirmation(update):
    with pytest.raises(ValidationError):
        PerConditionResultV2.model_validate({**condition_result(), **update})


def test_v2_rejects_version_gap_and_future_watermark():
    body = evaluation()
    body["state_version_after"] = 3
    with pytest.raises(ValidationError, match="version_invalid"):
        ConditionEvaluationV2.model_validate(body)
    body = evaluation()
    body["per_condition"][0]["last_bar_at"] = (ASOF + timedelta(seconds=1)).isoformat()
    with pytest.raises(ValidationError, match="future_bar"):
        ConditionEvaluationV2.model_validate(body)


def test_all_original_v1_definitions_are_unchanged():
    root = Path(__file__).resolve().parents[2]
    current = json.loads((root / "contracts/schemas/decision_product_v1.schema.yaml")
                         .read_text(encoding="utf-8"))
    original = {key: value for key, value in current["$defs"].items()
                if not key.endswith("_v2") and not key.startswith("decision_ui_")}
    digest = hashlib.sha256(json.dumps(original, sort_keys=True, separators=(",", ":"))
                            .encode()).hexdigest()
    assert digest == "cb48dea0ab4616f08a5220b928d11995680c5e82b30f78cd581e3f8b6c020bd7"

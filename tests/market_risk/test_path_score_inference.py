"""Controlled mathematical examples and private version plumbing, not market evidence."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from packages.kernel.decision_hub_kernel.decision.path_forecast import (
    LEGACY_PARAMETERS,
    fit_path_forecast,
    predict_path_forecast,
)
from packages.kernel.decision_hub_kernel.decision.path_forecast_score import (
    score_path_forecasts,
)
from packages.kernel.decision_hub_kernel.decision.path_score_inference import (
    PROTOCOL,
    PROTOCOL_HASH,
    MatchedEventBlock,
    PairedBrierLoss,
    paired_day_bootstrap,
)
from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash, spot_scope
from packages.kernel.decision_hub_kernel.ports.path_forecast import (
    FrozenPathForecastModel,
    PathForecastParameters,
    PathForecastParametersV1,
    parse_path_parameters,
)

FIRST = datetime(2026, 1, 1, tzinfo=UTC)
BTC, ETH = spot_scope("BTC-USDT"), spot_scope("ETH-USDT")
AS_OF = FIRST + timedelta(days=102)


def pair(day, scope=BTC.scope_id, *, delta=Decimal("0.25")):
    cutoff = FIRST + timedelta(days=day)
    return PairedBrierLoss(
        scope,
        cutoff,
        Decimal("0.5") + delta / 2,
        Decimal("0.5") - delta / 2,
        cutoff - timedelta(seconds=1),
        cutoff,
        cutoff + timedelta(seconds=5),
        cutoff + timedelta(days=1, minutes=2),
        cutoff + timedelta(days=1, minutes=1),
    )


def events(n=30):
    return tuple(
        MatchedEventBlock(
            f"controlled-event-{day}",
            FIRST + timedelta(days=day),
            FIRST + timedelta(days=day + 1, minutes=2),
            f"fixture://controlled-event/{day}",
            canonical_hash(["controlled-event", day]),
            (BTC.scope_id, ETH.scope_id),
        )
        for day in range(n)
    )


EVENTS = events()


def infer(pairs, *, blocks=EVENTS, expected=None):
    return paired_day_bootstrap(
        pairs,
        expected_pairs=expected or tuple((p.scope_id, p.cutoff_at) for p in pairs),
        event_blocks=blocks,
        as_of=AS_OF,
    )


def test_fixed_positive_negative_intervals_reproducible_and_never_promote():
    assert canonical_hash(PROTOCOL) == PROTOCOL_HASH
    positive = tuple(pair(day) for day in range(100))
    result = infer(positive)
    assert result == infer(positive)
    assert result["status"] == "available"
    assert Decimal(result["ci"]["lower"]) == Decimal(".25")
    assert Decimal(result["ci"]["upper"]) == Decimal(".25")
    negative = infer(tuple(pair(day, delta=Decimal("-.25")) for day in range(100)))
    assert Decimal(negative["ci"]["upper"]) == Decimal("-.25")
    assert not result["promotion_allowed"]
    assert result["ci"]["replicates"] == 2000 and result["ci"]["confidence"] == "0.95"
    variable = tuple(
        pair(day, delta=Decimal(".25") if day < 50 else Decimal("-.25")) for day in range(100)
    )
    interval = infer(variable)["ci"]
    assert Decimal(interval["lower"]) < 0 < Decimal(interval["upper"])
    assert Decimal(interval["point_estimate"]) == 0


def test_whole_day_correlation_not_two_asset_independent_samples():
    short = tuple(pair(day, scope.scope_id) for day in range(50) for scope in (BTC, ETH))
    result = infer(short)
    assert result["complete_pairs"] == 100 and result["independent_day_blocks"] == 50
    assert result["ci"] is None and result["status"] == "insufficient"
    opposite = tuple(
        pair(day, scope.scope_id, delta=Decimal(".25") if scope == BTC else Decimal("-.25"))
        for day in range(100)
        for scope in (BTC, ETH)
    )
    result = infer(opposite)
    assert result["independent_day_blocks"] == 100 and result["complete_pairs"] == 200
    assert Decimal(result["ci"]["lower"]) == Decimal(result["ci"]["upper"]) == 0


def test_clock_pair_census_and_event_original_counterexamples():
    valid = tuple(pair(day) for day in range(100))
    expected = tuple((p.scope_id, p.cutoff_at) for p in valid)
    incomplete = infer(valid[:-1], expected=expected)
    assert incomplete["ci"] is None
    assert "path_ci_complete_pair_census_missing" in incomplete["reasons"]
    for change in (
        {"input_known_at": FIRST + timedelta(microseconds=1)},
        {"baseline_published_at": FIRST + timedelta(minutes=1, microseconds=1)},
        {"label_known_at": AS_OF + timedelta(microseconds=1)},
        {"label_known_at": FIRST + timedelta(days=1)},
        {"window_end_at": FIRST + timedelta(days=1, minutes=2)},
        {"baseline_loss": Decimal("NaN")},
    ):
        with pytest.raises(ValueError):
            infer((replace(valid[0], **change), *valid[1:]))
    with pytest.raises(ValueError, match="pair_identity_invalid"):
        infer((*valid, valid[0]), expected=expected)
    assert infer(valid, blocks=None)["ci"] is None
    assert infer(valid, blocks=events(29))["event_blocks"] == 29
    same = events()
    assert infer(valid, blocks=(*same, same[0]))["event_blocks"] == 30
    with pytest.raises(ValueError, match="counted_twice"):
        infer(valid, blocks=(*same, replace(same[0], block_id="another-id")))
    with pytest.raises(ValueError, match="binding_invalid"):
        infer(valid, blocks=(replace(same[0], known_at=AS_OF + timedelta(microseconds=1)),))


def test_old_parameter_and_model_shape_hash_and_score_stay_v1():
    raw = PathForecastParametersV1().model_dump(mode="json")
    assert parse_path_parameters(raw).model_dump(mode="json") == raw
    assert "ci_seed" not in raw and raw["ci_protocol"] == "not_registered"
    model = fit_path_forecast(BTC, (), training_cutoff_at=FIRST, parameters=LEGACY_PARAMETERS)
    roundtrip = FrozenPathForecastModel.model_validate_json(model.model_dump_json())
    assert roundtrip == model and "ci_seed" not in roundtrip.parameters.model_dump(mode="json")
    forecast = predict_path_forecast(model, cutoff_at=FIRST, published_at=FIRST, features=None)
    assert forecast.parameter_hash == canonical_hash(raw)
    old = score_path_forecasts((), (), expected_slots=(), as_of=AS_OF, parameters=LEGACY_PARAMETERS)
    new = score_path_forecasts((), (), expected_slots=(), as_of=AS_OF)
    assert old["schema_version"] == "path-shadow-score.v1" and "inference" not in old
    assert "alarm_originals" not in old and old["ci_status"] == "not_registered"
    assert new["schema_version"] == "path-shadow-score.v2"
    assert new["ci_status"] == "insufficient" and new["alarm_originals"] == []
    assert PathForecastParameters().ci_protocol == PROTOCOL["version"]

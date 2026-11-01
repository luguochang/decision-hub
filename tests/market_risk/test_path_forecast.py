"""Isolated numerical fixtures; no market predictive performance is asserted."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext

import pytest
from pydantic import ValidationError

from packages.kernel.decision_hub_kernel.application.market_features import MarketFeatureSnapshot
from packages.kernel.decision_hub_kernel.decision.path_forecast import (
    empirical_cdf,
    fit_path_forecast,
    independent_day_blocks,
    predict_path_forecast,
    qualification_summary,
    training_window_from_candles,
)
from packages.kernel.decision_hub_kernel.decision.path_risk import PathRiskResult
from packages.kernel.decision_hub_kernel.ports.market_path import (
    MarketCandle,
    canonical_hash,
    spot_scope,
)
from packages.kernel.decision_hub_kernel.ports.path_forecast import PathTrainingWindow

BTC, ETH = spot_scope("BTC-USDT"), spot_scope("ETH-USDT")
CUTOFF = datetime(2026, 7, 1, tzinfo=UTC)


def feature(cutoff, *, rv="1", scope=BTC, known=None):
    return MarketFeatureSnapshot(
        scope=scope,
        as_of=cutoff,
        latest_close_at=cutoff,
        data_as_of=cutoff,
        known_at=known or cutoff,
        realized_vol_30m=Decimal(rv),
        status="fresh",
        metric_statuses={"realized_vol_30m": "verified"},
        semantic_hash="a" * 64,
        manifest_hash="b" * 64,
    )


def window(index, *, rv=None, scope=BTC, cutoff=None, known=None):
    # Explicit synthetic completed label summaries test the estimator, not source authentication.
    cutoff = cutoff or CUTOFF - timedelta(days=62 - index)
    start, end = cutoff + timedelta(minutes=1), cutoff + timedelta(days=1, minutes=1)
    path = PathRiskResult(
        scope=scope,
        window_start_at=start,
        window_end_at=end,
        as_of=end,
        anchor_at=start,
        anchor_price=Decimal(100),
        anchor_status="verified",
        window_status="complete",
        observed_count=1440,
        expected_count=1440,
        pending_count=0,
        missing_open_at=(),
        missing_ranges=(),
        quality="observed",
        mae_long=Decimal(".01") if index < 30 else Decimal(".02"),
        mae_short=Decimal(".06") if index % 10 == 0 else Decimal(".01"),
        semantic_hash="a" * 64,
        manifest_hash="b" * 64,
    )
    item = PathTrainingWindow(
        input_cutoff_at=cutoff,
        path=path,
        label_known_at=known or end,
        label_ref=f"fixture://path-forecast/{scope.instrument}/{index}",
        features=feature(cutoff, rv=rv or ("1" if index < 30 else "3"), scope=scope),
        input_hash="0" * 64,
    )
    return item.model_copy(
        update={"input_hash": canonical_hash(item.model_dump(mode="json", exclude={"input_hash"}))}
    )


def predict(model, *, rv="2", version="BASE_EMPIRICAL_V1", published=None):
    return predict_path_forecast(
        model,
        cutoff_at=CUTOFF,
        published_at=published or CUTOFF,
        features=feature(CUTOFF, rv=rv),
        model_version=version,
    )


def test_hand_counted_frequencies_full_cdf_and_frozen_conditional_median():
    rows = tuple(window(i) for i in range(60))
    model = fit_path_forecast(BTC, rows, training_cutoff_at=CUTOFF)
    assert model.baseline.n == 60 and model.training_vol_median == 2
    assert (model.conditional_low.n, model.conditional_high.n) == (30, 30)
    result = predict(model)
    assert result.frequencies[0].k == 30
    assert result.frequencies[0].probability == Decimal("30.5") / Decimal(61)
    assert result.frequencies[2].k == 6
    assert result.frequencies[2].probability == Decimal("6.5") / Decimal(61)
    assert empirical_cdf(model.baseline, "long", Decimal(".01")) == Decimal(".5")
    assert model.baseline.quantile_upper_bound is None
    low = predict(model, version="COND_VOL_V1", rv="2")
    high = predict(model, version="COND_VOL_V1", rv="100")
    assert low.selected_state == "low" and low.frequencies[0].n == 30
    assert high.selected_state == "high" and high.frequencies[0].n == 30
    assert model.training_vol_median == 2  # Target/holdout RV does not refit the split.
    assert not low.promotion_allowed and low.calibration_status == "experimental_uncalibrated"
    assert fit_path_forecast(BTC, tuple(reversed(rows)), training_cutoff_at=CUTOFF) == model
    with localcontext() as context:
        context.prec = 3
        # Caller Decimal settings cannot change frozen probabilities.
        assert predict(model) == result


def test_equal_median_ties_stay_low_and_empty_high_is_unknown():
    model = fit_path_forecast(
        BTC, tuple(window(i, rv="1") for i in range(60)), training_cutoff_at=CUTOFF
    )
    assert model.parameters.tie_policy == "rv_le_median_low_gt_median_high"
    assert (model.conditional_low.n, model.conditional_high.n) == (60, 0)
    low, high = (
        predict(model, version="COND_VOL_V1", rv="1"),
        predict(model, version="COND_VOL_V1", rv="1.0000000001"),
    )
    assert low.frequencies[0].probability is not None
    assert high.calibration_status == "insufficient"
    assert all(item.probability is None for item in high.frequencies)


@pytest.mark.parametrize(
    "delay,known_offset,usable",
    [
        (0, 0, True),
        (180, 0, True),
        (181, 0, False),
        (-1, 0, False),
        (60, 1, False),
    ],
)
def test_first_prereg_feature_delay_policy_uses_actual_known_clock(delay, known_offset, usable):
    model = fit_path_forecast(BTC, tuple(window(i) for i in range(60)), training_cutoff_at=CUTOFF)
    snapshot = feature(CUTOFF).model_copy(
        update={
            "latest_close_at": CUTOFF - timedelta(seconds=delay),
            "known_at": CUTOFF + timedelta(microseconds=known_offset),
        }
    )
    result = predict_path_forecast(
        model,
        cutoff_at=CUTOFF,
        published_at=CUTOFF,
        features=snapshot,
        model_version="COND_VOL_V1",
    )
    assert (result.frequencies[0].probability is not None) == usable
    assert result.feature_known_at == snapshot.known_at


def test_future_known_one_microsecond_immature_and_90_day_exclusions_are_preserved():
    rows = tuple(window(i) for i in range(59)) + (
        window(59, known=CUTOFF + timedelta(microseconds=1)),
        window(60, cutoff=CUTOFF),
        window(61, cutoff=CUTOFF - timedelta(days=91)),
    )
    model = fit_path_forecast(BTC, rows, training_cutoff_at=CUTOFF)
    assert model.baseline.n == 59
    reasons = [reason for _, values in model.excluded for reason in values]
    assert "label_not_known_at_training_cutoff" in reasons
    assert "label_not_mature" in reasons
    assert "outside_registered_90_day_history" in reasons
    assert all(item.probability is None for item in predict(model).frequencies)
    with pytest.raises((ValueError, ValidationError), match="timezone"):
        fit_path_forecast(BTC, (), training_cutoff_at=CUTOFF.replace(tzinfo=None))


def test_no_cross_asset_pooling_overlap_or_duplicate_independent_blocks():
    btc, eth = window(1), window(1, scope=ETH)
    assert independent_day_blocks((btc, eth)) == (btc.input_cutoff_at.date().isoformat(),)
    report = qualification_summary((btc, eth), event_block_ids=("same-shock", "same-shock"))
    assert report.independent_day_blocks == 1 and report.independent_event_blocks == 1
    assert not report.promotion_allowed and report.predictive_gain is None
    with pytest.raises(ValueError, match="scope_mismatch"):
        fit_path_forecast(BTC, (eth,), training_cutoff_at=CUTOFF)
    with pytest.raises(ValueError, match="duplicate_or_overlapping"):
        fit_path_forecast(BTC, (btc, btc), training_cutoff_at=CUTOFF)
    with pytest.raises(ValueError, match="not_registered_daily"):
        fit_path_forecast(BTC, (), training_cutoff_at=CUTOFF + timedelta(hours=1))
    with pytest.raises(ValueError, match="exceeds_90"):
        fit_path_forecast(BTC, (btc,) * 91, training_cutoff_at=CUTOFF)


def test_late_forecast_keeps_fixed_anchor_and_manifest_tamper_rejected():
    model = fit_path_forecast(BTC, tuple(window(i) for i in range(60)), training_cutoff_at=CUTOFF)
    late = predict(model, published=CUTOFF + timedelta(minutes=1, microseconds=1))
    assert late.publication_status == "late"
    assert late.window_start_at == CUTOFF + timedelta(minutes=1)
    assert late.window_end_at == late.window_start_at + timedelta(days=1)
    assert late.anchor_price is None and all(item.probability is None for item in late.frequencies)
    with pytest.raises(ValueError, match="model_manifest_changed"):
        predict(model.model_copy(update={"training_vol_median": Decimal("0")}))
    damaged = window(1).model_copy(update={"label_known_at": CUTOFF})
    with pytest.raises(ValueError, match="training_input_hash_changed"):
        fit_path_forecast(BTC, (damaged,), training_cutoff_at=CUTOFF)
    with pytest.raises(ValueError, match="feature_scope"):
        predict_path_forecast(
            model, cutoff_at=CUTOFF, published_at=CUTOFF, features=feature(CUTOFF, scope=ETH)
        )


def test_original_candle_recompute_complete_path_and_future_feature_receipt():
    cutoff = CUTOFF - timedelta(days=2)
    start, end = cutoff + timedelta(minutes=1), cutoff + timedelta(days=1, minutes=1)

    def bar(open_at, *, high="101", low="99", known=None):
        return MarketCandle(
            scope_id=BTC.scope_id,
            venue="okx",
            instrument="BTC-USDT",
            open_at=open_at,
            close_at=open_at + timedelta(minutes=1),
            open=Decimal(100),
            high=Decimal(high),
            low=Decimal(low),
            close=Decimal(100),
            received_at=known or open_at + timedelta(minutes=1),
            accepted_at=known or open_at + timedelta(minutes=1),
            capture_ref="fixture://minute-path",
            capture_hash="a" * 64,
        )

    path = tuple(
        bar(start + timedelta(minutes=i), low="95" if i == 8 else "99") for i in range(1440)
    )
    features = tuple(bar(cutoff - timedelta(minutes=31 - i)) for i in range(31))
    row = training_window_from_candles(
        BTC,
        input_cutoff_at=cutoff,
        path_candles=path,
        feature_candles=features,
        label_known_at=end,
        label_ref="fixture://recomputed-label",
    )
    assert row.path.mae_long == Decimal(".05") and row.path.window_status == "complete"
    assert row.features.realized_vol_30m == 0
    model = fit_path_forecast(BTC, (row,), training_cutoff_at=CUTOFF)
    assert model.baseline.n == 1
    assert all(item.probability is None for item in predict(model).frequencies)
    late = tuple(
        item.model_copy(update={"accepted_at": cutoff + timedelta(microseconds=1)})
        for item in features
    )
    late_row = training_window_from_candles(
        BTC,
        input_cutoff_at=cutoff,
        path_candles=path,
        feature_candles=late,
        label_known_at=end,
        label_ref="fixture://late-feature",
    )
    late_model = fit_path_forecast(BTC, (late_row,), training_cutoff_at=CUTOFF)
    assert late_model.baseline.n == 1 and late_model.candidate_training_n == 0

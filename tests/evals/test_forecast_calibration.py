from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from packages.evals.forecast_calibration import (
    ForecastCalibrationPolicy,
    ForecastCalibrationSample,
    calibrate_probability,
    evaluate_forecast_calibration,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _strong_samples(count: int = 180) -> list[ForecastCalibrationSample]:
    samples: list[ForecastCalibrationSample] = []
    regimes = ("risk_on", "risk_off", "range")
    for index in range(count):
        outcome = index % 5 != 0
        probability = 0.82 if outcome else 0.18
        realized = 1.0 if outcome else -1.0
        samples.append(
            ForecastCalibrationSample(
                sample_id=f"sample-{index:04d}",
                as_of=NOW + timedelta(days=index),
                raw_probability=probability,
                outcome=outcome,
                realized_return_pct=realized,
                trading_cost_pct=0.05,
                regime=regimes[index % len(regimes)],
            )
        )
    return samples


def test_strong_walk_forward_signal_passes_and_can_calibrate() -> None:
    report = evaluate_forecast_calibration(
        _strong_samples(),
        strategy_version="btc-direction.v1",
        horizon="24h",
        evaluated_at=NOW + timedelta(days=200),
    )

    assert report.passed is True
    assert report.fold_count == 5
    assert report.accuracy >= 0.90
    assert report.accuracy_lower_bound >= 0.50
    assert report.brier_score < report.baseline_brier_score
    assert report.net_return_pct > 0
    assert sum(item.count for item in report.reliability_bins) == report.evaluated_sample_count
    assert calibrate_probability(0.8, report) > 0.5


def test_weak_signal_fails_and_cannot_be_used_as_calibration_receipt() -> None:
    samples = [
        sample.model_copy(
            update={
                "raw_probability": 0.8 if index % 2 == 0 else 0.2,
                "outcome": index % 3 == 0,
                "realized_return_pct": 1.0 if index % 3 == 0 else -1.0,
            }
        )
        for index, sample in enumerate(_strong_samples())
    ]
    report = evaluate_forecast_calibration(
        samples,
        strategy_version="btc-direction.weak",
        horizon="24h",
        evaluated_at=NOW + timedelta(days=200),
    )

    assert report.passed is False
    assert "brier_not_better_than_base_rate" in report.failure_reasons
    with pytest.raises(ValueError, match="report_not_passed"):
        calibrate_probability(0.8, report)


def test_minimum_samples_and_regime_dominance_are_enforced() -> None:
    samples = [
        sample.model_copy(update={"regime": "single"}) for sample in _strong_samples(60)
    ]
    report = evaluate_forecast_calibration(
        samples,
        strategy_version="btc-direction.small",
        horizon="72h",
        evaluated_at=NOW + timedelta(days=100),
    )

    assert report.passed is False
    assert "sample_count_below_minimum" in report.failure_reasons
    assert "single_regime_dominates" in report.failure_reasons


def test_duplicate_sample_id_and_non_strict_time_are_rejected() -> None:
    duplicate = _strong_samples(30)
    duplicate[1] = duplicate[1].model_copy(update={"sample_id": duplicate[0].sample_id})
    with pytest.raises(ValueError, match="sample_id_duplicate"):
        evaluate_forecast_calibration(
            duplicate,
            strategy_version="btc-direction.v1",
            horizon="24h",
            evaluated_at=NOW,
            policy=ForecastCalibrationPolicy(min_samples=20, n_splits=3),
        )

    same_time = _strong_samples(30)
    same_time[1] = same_time[1].model_copy(update={"as_of": same_time[0].as_of})
    with pytest.raises(ValueError, match="time_not_strict"):
        evaluate_forecast_calibration(
            same_time,
            strategy_version="btc-direction.v1",
            horizon="24h",
            evaluated_at=NOW,
            policy=ForecastCalibrationPolicy(min_samples=20, n_splits=3),
        )

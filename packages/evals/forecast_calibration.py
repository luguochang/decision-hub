from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field


class ForecastCalibrationSample(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sample_id: str = Field(min_length=1)
    as_of: datetime
    outcome_at: datetime | None = None
    raw_probability: float = Field(ge=0, le=1)
    outcome: bool
    realized_return_pct: float
    trading_cost_pct: float = Field(default=0, ge=0)
    regime: str = Field(min_length=1)


class ForecastCalibrationPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    min_samples: int = Field(default=100, ge=20)
    n_splits: int = Field(default=5, ge=3, le=20)
    gap_samples: int = Field(default=1, ge=0)
    min_accuracy: float = Field(default=0.60, ge=0.5, le=1)
    min_accuracy_lower_bound: float = Field(default=0.50, ge=0, le=1)
    min_coverage_ratio: float = Field(default=0.60, ge=0, le=1)
    min_net_return_pct: float = 0.0
    max_drawdown_pct: float = Field(default=20.0, ge=0)
    max_regime_share: float = Field(default=0.70, gt=0, le=1)


class ForecastReliabilityBin(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    lower_bound: float = Field(ge=0, le=1)
    upper_bound: float = Field(ge=0, le=1)
    count: int = Field(ge=0)
    mean_probability: float = Field(ge=0, le=1)
    outcome_rate: float = Field(ge=0, le=1)


class ForecastCalibrationReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "forecast-calibration-report.v1"
    strategy_version: str = Field(min_length=1)
    horizon: str = Field(min_length=1)
    dataset_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    sample_count: int = Field(ge=0)
    evaluated_sample_count: int = Field(ge=0)
    fold_count: int = Field(ge=0)
    gap_samples: int = Field(ge=0)
    accuracy: float = Field(ge=0, le=1)
    accuracy_lower_bound: float = Field(ge=0, le=1)
    brier_score: float = Field(ge=0, le=1)
    baseline_brier_score: float = Field(ge=0, le=1)
    log_loss: float = Field(ge=0)
    net_return_pct: float
    max_drawdown_pct: float = Field(ge=0)
    coverage_ratio: float = Field(ge=0, le=1)
    max_regime_share: float = Field(ge=0, le=1)
    calibration_slope: float
    calibration_intercept: float
    reliability_bins: list[ForecastReliabilityBin]
    passed: bool
    failure_reasons: list[str]
    evaluated_at: datetime


def evaluate_forecast_calibration(
    samples: list[ForecastCalibrationSample],
    *,
    strategy_version: str,
    horizon: str,
    evaluated_at: datetime,
    policy: ForecastCalibrationPolicy | None = None,
) -> ForecastCalibrationReport:
    effective_policy = policy or ForecastCalibrationPolicy()
    ordered = sorted(samples, key=lambda item: (item.as_of, item.sample_id))
    if len({item.sample_id for item in ordered}) != len(ordered):
        raise ValueError("forecast_calibration_sample_id_duplicate")
    if any(left.as_of >= right.as_of for left, right in zip(ordered, ordered[1:], strict=False)):
        raise ValueError("forecast_calibration_time_not_strict")
    dataset_hash = _dataset_hash(ordered)
    splits = _time_splits(
        ordered,
        horizon=horizon,
        n_splits=effective_policy.n_splits,
        gap=effective_policy.gap_samples,
    )
    predictions: list[float] = []
    labels: list[float] = []
    baseline_predictions: list[float] = []
    net_returns: list[float] = []
    for train_indices, test_indices in splits:
        train = [ordered[index] for index in train_indices]
        test = [ordered[index] for index in test_indices]
        slope, intercept = _fit_platt(train)
        baseline = sum(item.outcome for item in train) / len(train)
        for sample in test:
            probability = _calibrated_probability(
                sample.raw_probability,
                slope=slope,
                intercept=intercept,
            )
            predictions.append(probability)
            label = 1.0 if sample.outcome else 0.0
            labels.append(label)
            baseline_predictions.append(baseline)
            signed_return = (
                sample.realized_return_pct
                if probability >= 0.5
                else -sample.realized_return_pct
            )
            net_returns.append(signed_return - sample.trading_cost_pct)

    evaluated_count = len(predictions)
    accuracy = (
        sum(
            (probability >= 0.5) == bool(label)
            for probability, label in zip(predictions, labels, strict=True)
        )
        / evaluated_count
        if evaluated_count
        else 0.0
    )
    brier = _mean(
        [
            (probability - label) ** 2
            for probability, label in zip(predictions, labels, strict=True)
        ]
    )
    baseline_brier = _mean(
        [
            (probability - label) ** 2
            for probability, label in zip(baseline_predictions, labels, strict=True)
        ]
    )
    log_loss = _mean(
        [
            -(label * math.log(_clip(probability)) + (1 - label) * math.log(_clip(1 - probability)))
            for probability, label in zip(predictions, labels, strict=True)
        ]
    )
    lower_bound = _wilson_lower_bound(round(accuracy * evaluated_count), evaluated_count)
    net_return = sum(net_returns)
    max_drawdown = _max_drawdown(net_returns)
    coverage = evaluated_count / len(ordered) if ordered else 0.0
    regime_counts: dict[str, int] = {}
    for sample in ordered:
        regime_counts[sample.regime] = regime_counts.get(sample.regime, 0) + 1
    max_regime_share = max(regime_counts.values(), default=0) / len(ordered) if ordered else 0.0
    final_slope, final_intercept = _fit_platt(ordered) if ordered else (1.0, 0.0)
    reasons: list[str] = []
    checks = (
        (len(ordered) >= effective_policy.min_samples, "sample_count_below_minimum"),
        (len(splits) >= effective_policy.n_splits, "time_split_count_below_minimum"),
        (accuracy >= effective_policy.min_accuracy, "accuracy_below_threshold"),
        (
            lower_bound >= effective_policy.min_accuracy_lower_bound,
            "accuracy_confidence_bound_below_threshold",
        ),
        (brier < baseline_brier, "brier_not_better_than_base_rate"),
        (net_return > effective_policy.min_net_return_pct, "net_return_not_positive"),
        (max_drawdown <= effective_policy.max_drawdown_pct, "drawdown_above_limit"),
        (coverage >= effective_policy.min_coverage_ratio, "coverage_below_threshold"),
        (max_regime_share <= effective_policy.max_regime_share, "single_regime_dominates"),
    )
    reasons.extend(reason for passed, reason in checks if not passed)
    return ForecastCalibrationReport(
        strategy_version=strategy_version,
        horizon=horizon,
        dataset_hash=dataset_hash,
        sample_count=len(ordered),
        evaluated_sample_count=evaluated_count,
        fold_count=len(splits),
        gap_samples=effective_policy.gap_samples,
        accuracy=accuracy,
        accuracy_lower_bound=lower_bound,
        brier_score=brier,
        baseline_brier_score=baseline_brier,
        log_loss=log_loss,
        net_return_pct=net_return,
        max_drawdown_pct=max_drawdown,
        coverage_ratio=coverage,
        max_regime_share=max_regime_share,
        calibration_slope=final_slope,
        calibration_intercept=final_intercept,
        reliability_bins=_reliability_bins(predictions, labels),
        passed=not reasons,
        failure_reasons=reasons,
        evaluated_at=evaluated_at,
    )


def calibrate_probability(raw_probability: float, report: ForecastCalibrationReport) -> float:
    if not report.passed:
        raise ValueError("forecast_calibration_report_not_passed")
    return _calibrated_probability(
        raw_probability,
        slope=report.calibration_slope,
        intercept=report.calibration_intercept,
    )


def _time_splits(
    samples: list[ForecastCalibrationSample],
    *,
    horizon: str,
    n_splits: int,
    gap: int,
) -> list[tuple[range, range]]:
    sample_count = len(samples)
    test_size = sample_count // (n_splits + 1)
    if test_size < 1:
        return []
    splits: list[tuple[range, range]] = []
    for fold in range(1, n_splits + 1):
        test_start = fold * test_size
        test_end = min(test_start + test_size, sample_count)
        train_end = test_start - gap
        test_as_of = samples[test_start].as_of
        while train_end > 0 and _outcome_at(samples[train_end - 1], horizon) > test_as_of:
            train_end -= 1
        if train_end < 10 or test_start >= test_end:
            continue
        splits.append((range(0, train_end), range(test_start, test_end)))
    return splits


def _outcome_at(sample: ForecastCalibrationSample, horizon: str) -> datetime:
    if sample.outcome_at is not None:
        return sample.outcome_at
    durations = {
        "30m": timedelta(minutes=30),
        "24h": timedelta(hours=24),
        "72h": timedelta(hours=72),
        "7d": timedelta(days=7),
    }
    try:
        return sample.as_of + durations[horizon]
    except KeyError as exc:
        raise ValueError("forecast_calibration_horizon_unsupported") from exc


def _fit_platt(samples: list[ForecastCalibrationSample]) -> tuple[float, float]:
    if not samples:
        return 1.0, 0.0
    slope = 1.0
    intercept = 0.0
    learning_rate = 0.03
    for _ in range(800):
        gradient_slope = 0.0
        gradient_intercept = 0.0
        for sample in samples:
            feature = _logit(sample.raw_probability)
            prediction = _sigmoid(slope * feature + intercept)
            error = prediction - (1.0 if sample.outcome else 0.0)
            gradient_slope += error * feature
            gradient_intercept += error
        scale = 1.0 / len(samples)
        slope -= learning_rate * gradient_slope * scale
        intercept -= learning_rate * gradient_intercept * scale
    return slope, intercept


def _calibrated_probability(raw: float, *, slope: float, intercept: float) -> float:
    return _sigmoid(slope * _logit(raw) + intercept)


def _dataset_hash(samples: list[ForecastCalibrationSample]) -> str:
    payload = [sample.model_dump(mode="json") for sample in samples]
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _clip(value: float) -> float:
    return min(max(value, 1e-12), 1 - 1e-12)


def _logit(value: float) -> float:
    probability = _clip(value)
    return math.log(probability / (1 - probability))


def _sigmoid(value: float) -> float:
    if value >= 0:
        factor = math.exp(-value)
        return 1 / (1 + factor)
    factor = math.exp(value)
    return factor / (1 + factor)


def _wilson_lower_bound(successes: int, total: int, z: float = 1.96) -> float:
    if total == 0:
        return 0.0
    rate = successes / total
    denominator = 1 + z**2 / total
    center = rate + z**2 / (2 * total)
    margin = z * math.sqrt((rate * (1 - rate) + z**2 / (4 * total)) / total)
    return max(0.0, (center - margin) / denominator)


def _max_drawdown(returns: list[float]) -> float:
    equity = 0.0
    peak = 0.0
    drawdown = 0.0
    for value in returns:
        equity += value
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return drawdown


def _reliability_bins(
    probabilities: list[float], labels: list[float], *, bin_count: int = 5
) -> list[ForecastReliabilityBin]:
    bins: list[ForecastReliabilityBin] = []
    for index in range(bin_count):
        lower = index / bin_count
        upper = (index + 1) / bin_count
        values = [
            (probability, label)
            for probability, label in zip(probabilities, labels, strict=True)
            if lower <= probability <= upper
            and (index == bin_count - 1 or probability < upper)
        ]
        if not values:
            continue
        bins.append(
            ForecastReliabilityBin(
                lower_bound=lower,
                upper_bound=upper,
                count=len(values),
                mean_probability=_mean([item[0] for item in values]),
                outcome_rate=_mean([item[1] for item in values]),
            )
        )
    return bins

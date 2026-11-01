"""Own-action probability calibration with actual label-known walk-forward boundaries.

The caller supplies authenticated immutable originals. This pure evaluator does
not authenticate a database, change an AI action, or grant execution permission.
Legacy forecast_calibration.v1 and its report schema remain unchanged.
"""

from __future__ import annotations

import hashlib
import math
import marshal
from pathlib import Path
from types import CodeType
from typing import TYPE_CHECKING, Any

from packages.evals.forecast_calibration import (
    ForecastCalibrationReport,
    _calibrated_probability,
    _clip,
    _dataset_hash,
    _fit_platt,
    _max_drawdown,
    _mean,
    _reliability_bins,
    _wilson_lower_bound,
    calibrate_probability,
)

if TYPE_CHECKING:
    from datetime import datetime

    from packages.kernel.decision_hub_kernel.application.calibration_inputs import (
        FrozenCalibrationInputs,
    )

MATH_VERSION = "own-action-retain-or-abstain.v2"


def _normalized_code(code: CodeType) -> CodeType:
    return code.replace(co_filename="<sealed-calibration-source>", co_consts=tuple(
        _normalized_code(value) if isinstance(value, CodeType) else value for value in code.co_consts
    ))


def _code_hash(function: Any) -> str:
    return hashlib.sha256(marshal.dumps(_normalized_code(function.__code__))).hexdigest()


def _require_loaded() -> None:
    for name, (function, code) in _LOADED.items():
        current = globals().get(name)
        if current is not function or getattr(current, "__code__", None) is not code:
            raise ValueError("calibration_math_implementation_unavailable")
    for path, expected in _LOADED_FILES.items():
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError("calibration_math_source_changed")


def math_identity() -> dict[str, Any]:
    _require_loaded()
    root = Path(__file__).resolve().parents[2]
    return {
        "math_version": MATH_VERSION,
        "code_files": {path.relative_to(root).as_posix(): digest for path, digest in _LOADED_FILES.items()},
        "loaded_abi": {name: _code_hash(function) for name, (function, _code) in _LOADED.items()},
    }


def evaluate_authenticated_forecast_calibration(
    inputs: FrozenCalibrationInputs, *, evaluated_at: datetime,
) -> ForecastCalibrationReport:
    _require_loaded()
    if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
        raise ValueError("calibration_math_clock_timezone_required")
    if inputs.reasons:
        raise ValueError("calibration_authenticated_inputs_incomplete")
    ordered = sorted(inputs.samples, key=lambda item: (item.sample.as_of, item.outlook_id))
    samples = [item.sample for item in ordered]
    if (len({item.sample_id for item in samples}) != len(samples)
            or any(left.as_of >= right.as_of for left, right in zip(samples, samples[1:]))):
        raise ValueError("calibration_math_time_or_identity_invalid")
    if any(item.label_known_at > evaluated_at for item in ordered):
        raise ValueError("calibration_math_future_label")
    policy = inputs.plan.policy.thresholds
    test_size = len(ordered) // (policy.n_splits + 1)
    splits: list[tuple[tuple[int, ...], range]] = []
    if test_size:
        for fold in range(1, policy.n_splits + 1):
            start = fold * test_size
            end = min(start + test_size, len(ordered))
            train_end = start - policy.gap_samples
            # Check EVERY train label, not merely the last prediction's label.
            # Variable horizons/retries may make label-known order non-monotone.
            test_as_of = ordered[start].sample.as_of
            eligible = [i for i in range(max(0, train_end)) if ordered[i].label_known_at <= test_as_of]
            if len(eligible) >= 10 and start < end:
                # Store exact selected train indices; later-known labels are not backfilled.
                splits.append((tuple(eligible), range(start, end)))
    predictions: list[float] = []
    labels: list[float] = []
    baselines: list[float] = []
    returns: list[float] = []
    threshold = inputs.plan.policy.retain_threshold
    for train_indices, test_indices in splits:
        train = [samples[index] for index in train_indices]
        slope, intercept = _fit_platt(train)
        baseline = sum(item.outcome for item in train) / len(train)
        for index in test_indices:
            sample = samples[index]
            value = _calibrated_probability(sample.raw_probability, slope=slope, intercept=intercept)
            predictions.append(value)
            labels.append(float(sample.outcome))
            baselines.append(baseline)
            # A low correctness probability abstains; it never reverses the AI direction.
            returns.append(sample.realized_return_pct - sample.trading_cost_pct if value >= threshold else 0.0)
    count = len(predictions)
    accuracy = sum((value >= 0.5) == bool(label) for value, label in zip(predictions, labels)) / count if count else 0.0
    brier = _mean([(value - label) ** 2 for value, label in zip(predictions, labels)])
    baseline_brier = _mean([(value - label) ** 2 for value, label in zip(baselines, labels)])
    loss = _mean([-(label * math.log(_clip(value)) + (1 - label) * math.log(_clip(1 - value)))
                  for value, label in zip(predictions, labels)])
    lower = _wilson_lower_bound(round(accuracy * count), count)
    net_return, drawdown = sum(returns), _max_drawdown(returns)
    coverage = count / len(samples) if samples else 0.0
    regimes: dict[str, int] = {}
    for sample in samples:
        regimes[sample.regime] = regimes.get(sample.regime, 0) + 1
    regime_share = max(regimes.values(), default=0) / len(samples) if samples else 0.0
    slope, intercept = _fit_platt(samples) if samples else (1.0, 0.0)
    checks = (
        (len(samples) >= policy.min_samples, "sample_count_below_minimum"),
        (len(splits) >= policy.n_splits, "time_split_count_below_minimum"),
        (accuracy >= policy.min_accuracy, "accuracy_below_threshold"),
        (lower >= policy.min_accuracy_lower_bound, "accuracy_confidence_bound_below_threshold"),
        (brier < baseline_brier, "brier_not_better_than_base_rate"),
        (net_return > policy.min_net_return_pct, "net_return_not_positive"),
        (drawdown <= policy.max_drawdown_pct, "drawdown_above_limit"),
        (coverage >= policy.min_coverage_ratio, "coverage_below_threshold"),
        (regime_share <= policy.max_regime_share, "single_regime_dominates"),
        (inputs.plan.policy.purpose == "prospective_research_calibration", "observational_only_not_activation_eligible"),
    )
    reasons = [reason for passed, reason in checks if not passed]
    return ForecastCalibrationReport(
        strategy_version=inputs.plan.scope.strategy_version, horizon=inputs.plan.scope.horizon,
        dataset_hash=_dataset_hash(samples), sample_count=len(samples), evaluated_sample_count=count,
        fold_count=len(splits), gap_samples=policy.gap_samples, accuracy=accuracy,
        accuracy_lower_bound=lower, brier_score=brier, baseline_brier_score=baseline_brier,
        log_loss=loss, net_return_pct=net_return, max_drawdown_pct=drawdown,
        coverage_ratio=coverage, max_regime_share=regime_share,
        calibration_slope=slope, calibration_intercept=intercept,
        reliability_bins=_reliability_bins(predictions, labels), passed=not reasons,
        failure_reasons=reasons, evaluated_at=evaluated_at,
    )


def calibrated_scalar(raw_probability: float, report: ForecastCalibrationReport) -> float:
    _require_loaded()
    if (type(raw_probability) not in {int, float} or not math.isfinite(raw_probability)
            or not 0 <= raw_probability <= 1):
        raise ValueError("calibration_raw_probability_invalid")
    return calibrate_probability(raw_probability, report)


_NAMES = (
    "_normalized_code", "_code_hash", "_require_loaded", "math_identity", "evaluate_authenticated_forecast_calibration",
    "calibrated_scalar", "_calibrated_probability", "_clip", "_dataset_hash", "_fit_platt",
    "_max_drawdown", "_mean", "_reliability_bins", "_wilson_lower_bound", "calibrate_probability",
)
_LOADED = {name: (globals()[name], globals()[name].__code__) for name in _NAMES}
_MATH_SOURCE = Path(__file__).resolve()
_LEGACY_SOURCE = Path(_fit_platt.__code__.co_filename).resolve()
_LOADED_FILES = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in (_MATH_SOURCE, _LEGACY_SOURCE)}

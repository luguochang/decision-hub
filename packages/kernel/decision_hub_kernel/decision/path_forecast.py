"""Fixed empirical path frequencies and one frozen conditional-volatility candidate.

This module computes no calibration, confidence interval or model promotion. Source
authentication and durable publication/label receipts belong to the owning service.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from statistics import median

from packages.kernel.decision_hub_kernel.application.market_features import (
    MarketFeatureSnapshot,
    build_market_features,
)
from packages.kernel.decision_hub_kernel.decision.path_risk import compute_path_risk
from packages.kernel.decision_hub_kernel.ports.market_path import (
    MarketCandle,
    MarketPathScope,
    aware_utc,
    canonical_hash,
    spot_scope,
)
from packages.kernel.decision_hub_kernel.ports.path_forecast import (
    EmpiricalDistribution,
    FrozenPathForecastModel,
    PathEventFrequency,
    PathForecastParameters,
    PathForecastParametersV1,
    PathQualification,
    PathTrainingWindow,
    RiskPathForecast,
)

PARAMETERS = PathForecastParameters()
LEGACY_PARAMETERS = PathForecastParametersV1()
THRESHOLDS = (Decimal("0.02"), Decimal("0.05"))


def _daily_cutoff(value: datetime) -> datetime:
    value = aware_utc(value)
    if (value.hour, value.minute, value.second, value.microsecond) != (0, 0, 0, 0):
        raise ValueError("path_forecast_cutoff_not_registered_daily_utc")
    return value


def _scope(value: MarketPathScope) -> None:
    if value != spot_scope(value.instrument):
        raise ValueError("path_forecast_scope_not_registered")


def _window_body(window: PathTrainingWindow) -> dict:
    return window.model_dump(mode="json", exclude={"input_hash"})


def verify_training_window(window: PathTrainingWindow) -> None:
    # Reparse even a model_copy input; hashes detect mutation, never confer source authority.
    PathTrainingWindow.model_validate(window.model_dump(mode="json"))
    if canonical_hash(_window_body(window)) != window.input_hash:
        raise ValueError("path_forecast_training_input_hash_changed")


def training_window_from_candles(
    scope: MarketPathScope,
    *,
    input_cutoff_at: datetime,
    path_candles: tuple[MarketCandle, ...],
    feature_candles: tuple[MarketCandle, ...],
    label_known_at: datetime,
    label_ref: str,
) -> PathTrainingWindow:
    """Recompute the label/30m feature from originals; do not backdate label knowledge."""
    _scope(scope)
    cutoff = _daily_cutoff(input_cutoff_at)
    known = aware_utc(label_known_at)
    if any(bar.known_at > known for bar in path_candles):
        raise ValueError("path_forecast_label_precedes_original_knowledge")
    start = cutoff + timedelta(minutes=1)
    path = compute_path_risk(scope, path_candles, start, start + timedelta(days=1), as_of=known)
    features = build_market_features(scope, feature_candles, cutoff)
    body = {
        "schema_version": "path-training-window.v1",
        "input_cutoff_at": cutoff,
        "path": path,
        "features": features,
        "label_known_at": known,
        "label_ref": label_ref,
        "input_hash": "0" * 64,
    }
    window = PathTrainingWindow(**body)
    return window.model_copy(update={"input_hash": canonical_hash(_window_body(window))})


def _feature_usable(feature: MarketFeatureSnapshot | None, scope, cutoff) -> bool:
    return bool(
        feature is not None
        and feature.scope == scope
        and feature.as_of == cutoff
        and feature.known_at is not None
        and feature.known_at <= cutoff
        and feature.latest_close_at is not None
        and 0
        <= (cutoff - feature.latest_close_at).total_seconds()
        <= PARAMETERS.maximum_feature_delay_seconds
        and feature.status == "fresh"
        and feature.metric_statuses.get("realized_vol_30m") == "verified"
        and feature.realized_vol_30m is not None
        and feature.realized_vol_30m.is_finite()
        and feature.realized_vol_30m >= 0
    )


def _exclusions(window: PathTrainingWindow, scope, cutoff, earliest) -> tuple[str, ...]:
    path = window.path
    _daily_cutoff(window.input_cutoff_at)
    if path.scope != scope:
        raise ValueError("path_forecast_training_scope_mismatch")
    start = window.input_cutoff_at + timedelta(minutes=1)
    if (path.window_start_at, path.window_end_at, path.anchor_at) != (
        start,
        start + timedelta(days=1),
        start,
    ):
        raise ValueError("path_forecast_training_window_not_registered")
    reasons = []
    if window.input_cutoff_at < earliest:
        reasons.append("outside_registered_90_day_history")
    if path.window_end_at > cutoff:
        reasons.append("label_not_mature")
    if window.label_known_at > cutoff or path.as_of > cutoff:
        reasons.append("label_not_known_at_training_cutoff")
    if window.label_known_at < path.as_of:
        raise ValueError("path_forecast_label_known_time_invalid")
    if (
        path.window_status != "complete"
        or path.quality != "observed"
        or path.anchor_status != "verified"
        or path.anchor_price is None
        or path.observed_count != 1440
        or path.expected_count != 1440
        or path.pending_count
        or path.missing_open_at
        or path.missing_ranges
        or path.mae_long is None
        or path.mae_short is None
    ):
        reasons.append("complete_24h_label_unavailable")
    elif any(not number.is_finite() or number < 0 for number in (path.mae_long, path.mae_short)):
        raise ValueError("path_forecast_mae_invalid")
    return tuple(reasons)


def _distribution(rows: tuple[PathTrainingWindow, ...]) -> EmpiricalDistribution:
    return EmpiricalDistribution(
        n=len(rows),
        long_mae_sorted=tuple(sorted(row.path.mae_long for row in rows)),
        short_mae_sorted=tuple(sorted(row.path.mae_short for row in rows)),
    )


def fit_path_forecast(
    scope: MarketPathScope,
    windows: tuple[PathTrainingWindow, ...],
    *,
    training_cutoff_at: datetime,
    parameters: PathForecastParameters | PathForecastParametersV1 = PARAMETERS,
) -> FrozenPathForecastModel:
    """Fit once to a fixed 90-day readset; no holdout or future window enters the median."""
    _scope(scope)
    if parameters not in (PARAMETERS, LEGACY_PARAMETERS):
        raise ValueError("path_forecast_parameters_changed")
    cutoff = _daily_cutoff(training_cutoff_at)
    earliest = cutoff - timedelta(days=PARAMETERS.history_days)
    if len(windows) > PARAMETERS.maximum_input_windows:
        raise ValueError("path_forecast_input_readset_exceeds_90_windows")
    accepted, excluded, seen = [], [], set()
    for row in sorted(windows, key=lambda item: (item.input_cutoff_at, item.label_ref)):
        verify_training_window(row)
        if row.input_cutoff_at in seen:
            raise ValueError("path_forecast_duplicate_or_overlapping_window")
        seen.add(row.input_cutoff_at)
        reasons = _exclusions(row, scope, cutoff, earliest)
        if reasons:
            excluded.append((row.label_ref, reasons))
        else:
            accepted.append(row)
    rows = tuple(accepted)
    vol_rows = tuple(
        row for row in rows if _feature_usable(row.features, scope, row.input_cutoff_at)
    )
    with localcontext() as context:
        context.prec, context.rounding = PARAMETERS.decimal_precision, ROUND_HALF_EVEN
        boundary = median([row.features.realized_vol_30m for row in vol_rows]) if vol_rows else None
    low = tuple(row for row in vol_rows if row.features.realized_vol_30m <= boundary)
    high = tuple(row for row in vol_rows if row.features.realized_vol_30m > boundary)
    model = FrozenPathForecastModel(
        scope=scope,
        training_cutoff_at=cutoff,
        history_start_at=earliest,
        parameters=parameters,
        accepted=rows,
        excluded=tuple(excluded),
        baseline=_distribution(rows),
        conditional_low=_distribution(low),
        conditional_high=_distribution(high),
        training_vol_median=boundary,
        candidate_training_n=len(vol_rows),
        independent_day_blocks=len(independent_day_blocks(rows)),
        model_hash="0" * 64,
    )
    return model.model_copy(
        update={"model_hash": canonical_hash(model.model_dump(mode="json", exclude={"model_hash"}))}
    )


def _verify_model(model: FrozenPathForecastModel) -> None:
    FrozenPathForecastModel.model_validate(model.model_dump(mode="json"))
    if (
        model.parameters not in (PARAMETERS, LEGACY_PARAMETERS)
        or canonical_hash(model.model_dump(mode="json", exclude={"model_hash"})) != model.model_hash
    ):
        raise ValueError("path_forecast_model_manifest_changed")


def empirical_cdf(distribution: EmpiricalDistribution, direction: str, value: Decimal) -> Decimal:
    """The unsmoothed empirical CDF; it is not a confidence or quantile bound."""
    if direction not in {"long", "short"} or not value.is_finite():
        raise ValueError("path_forecast_cdf_input_invalid")
    values = getattr(distribution, direction + "_mae_sorted")
    if not values:
        raise ValueError("path_forecast_cdf_empty")
    with localcontext() as context:
        context.prec, context.rounding = PARAMETERS.decimal_precision, ROUND_HALF_EVEN
        return Decimal(sum(item <= value for item in values)) / Decimal(len(values))


def _frequency(k: int, n: int) -> Decimal:
    with localcontext() as context:
        context.prec, context.rounding = PARAMETERS.decimal_precision, ROUND_HALF_EVEN
        return (Decimal(k) + Decimal("0.5")) / Decimal(n + 1)


def predict_path_forecast(
    model: FrozenPathForecastModel,
    *,
    cutoff_at: datetime,
    published_at: datetime,
    features: MarketFeatureSnapshot | None,
    model_version: str = "BASE_EMPIRICAL_V1",
) -> RiskPathForecast:
    _verify_model(model)
    cutoff, published = _daily_cutoff(cutoff_at), aware_utc(published_at)
    if cutoff < model.training_cutoff_at or published < cutoff:
        raise ValueError("path_forecast_clock_invalid")
    if model_version not in {PARAMETERS.baseline, PARAMETERS.candidate}:
        raise ValueError("path_forecast_model_not_registered")
    if features is not None and (features.scope != model.scope or features.as_of != cutoff):
        raise ValueError("path_forecast_feature_scope_or_cutoff_mismatch")
    start = cutoff + timedelta(minutes=1)
    late = published > start
    reasons, state = [], None
    distribution = model.baseline
    sufficient = distribution.n >= PARAMETERS.minimum_total_windows
    if not sufficient:
        reasons.append("fewer_than_60_complete_nonoverlapping_windows")
    if model_version == PARAMETERS.candidate:
        if not _feature_usable(features, model.scope, cutoff) or model.training_vol_median is None:
            distribution, sufficient = (
                EmpiricalDistribution(n=0, long_mae_sorted=(), short_mae_sorted=()),
                False,
            )
            reasons.append("cutoff_30m_realized_volatility_unavailable")
        else:
            state = "low" if features.realized_vol_30m <= model.training_vol_median else "high"
            distribution = model.conditional_low if state == "low" else model.conditional_high
            sufficient = (
                model.candidate_training_n >= PARAMETERS.minimum_total_windows
                and distribution.n >= PARAMETERS.minimum_group_windows
            )
            if not sufficient:
                reasons.append("conditional_training_or_selected_group_insufficient")
    if late:
        sufficient = False
        reasons.append("late_publication_ledger_only_fixed_window_not_moved")
    status = "experimental_uncalibrated" if sufficient else "insufficient"
    frequencies = []
    for direction in ("long", "short"):
        values = getattr(distribution, direction + "_mae_sorted")
        for threshold in THRESHOLDS:
            k, n = sum(value >= threshold for value in values), len(values)
            frequencies.append(
                PathEventFrequency(
                    direction=direction,
                    threshold=threshold,
                    n=n,
                    k=k,
                    probability=_frequency(k, n) if sufficient else None,
                    status=status,
                )
            )
    forecast = RiskPathForecast(
        scope=model.scope,
        model_version=model_version,
        model_hash=model.model_hash,
        parameter_hash=canonical_hash(model.parameters.model_dump(mode="json")),
        feature_hash=canonical_hash(features.model_dump(mode="json")) if features else None,
        feature_latest_close_at=features.latest_close_at if features else None,
        feature_known_at=features.known_at if features else None,
        feature_delay_seconds=int((cutoff - features.latest_close_at).total_seconds())
        if features and features.latest_close_at and features.latest_close_at <= cutoff
        else None,
        cutoff_at=cutoff,
        published_at=published,
        publication_status="late" if late else "on_time",
        window_start_at=start,
        window_end_at=start + timedelta(days=1),
        selected_state=state,
        frequencies=tuple(frequencies),
        empirical_distribution=distribution if sufficient else None,
        calibration_status=status,
        reasons=tuple(reasons)
        + (
            "experimental_rate_not_calibrated_probability_or_confidence",
            "source_original_authentication_required_by_durable_owner",
            "f5_power_and_ci_protocol_not_qualified",
        ),
        payload_hash="0" * 64,
    )
    return forecast.model_copy(
        update={
            "payload_hash": canonical_hash(
                forecast.model_dump(mode="json", exclude={"payload_hash"})
            )
        }
    )


def verify_path_forecast(forecast: RiskPathForecast) -> None:
    RiskPathForecast.model_validate(forecast.model_dump(mode="json"))
    if canonical_hash(forecast.model_dump(mode="json", exclude={"payload_hash"})) != (
        forecast.payload_hash
    ):
        raise ValueError("path_forecast_payload_changed")


def independent_day_blocks(windows: tuple[PathTrainingWindow, ...]) -> tuple[str, ...]:
    """BTC and ETH on one UTC forecast day contribute one correlated day block."""
    return tuple(sorted({aware_utc(row.input_cutoff_at).date().isoformat() for row in windows}))


def qualification_summary(
    windows: tuple[PathTrainingWindow, ...], *, event_block_ids: tuple[str, ...] | None = None
) -> PathQualification:
    days = len(independent_day_blocks(windows))
    events = len(set(event_block_ids)) if event_block_ids is not None else None
    reasons = ["training_summary_is_not_paired_holdout_inference"]
    if days < PARAMETERS.minimum_time_blocks:
        reasons.append("fewer_than_100_independent_day_blocks")
    if events is None:
        reasons.append("authenticated_matching_event_blocks_unavailable")
    elif events < PARAMETERS.minimum_event_blocks:
        reasons.append("fewer_than_30_independent_event_blocks")
    return PathQualification(
        independent_day_blocks=days,
        independent_event_blocks=events,
        ci_status="not_evaluated",
        reasons=tuple(reasons),
    )

"""Bounded contracts for the registered 24h path experiment, not trading authority."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal, Protocol

from pydantic import Field, field_validator, model_validator

from packages.kernel.decision_hub_kernel.application.market_features import MarketFeatureSnapshot
from packages.kernel.decision_hub_kernel.decision.path_risk import PathRiskResult
from packages.kernel.decision_hub_kernel.ports.market_path import (
    FrozenContract,
    MarketPathScope,
    aware_utc,
)


class PathForecastParametersV1(FrozenContract):
    protocol_version: Literal["path-shadow-24h.v1"] = "path-shadow-24h.v1"
    baseline: Literal["BASE_EMPIRICAL_V1"] = "BASE_EMPIRICAL_V1"
    candidate: Literal["COND_VOL_V1"] = "COND_VOL_V1"
    candidate_partition_version: Literal["median-low-inclusive.v1"] = "median-low-inclusive.v1"
    tie_policy: Literal["rv_le_median_low_gt_median_high"] = "rv_le_median_low_gt_median_high"
    history_days: Literal[90] = 90
    maximum_input_windows: Literal[90] = 90
    minimum_total_windows: Literal[60] = 60
    minimum_group_windows: Literal[30] = 30
    minimum_time_blocks: Literal[100] = 100
    minimum_event_blocks: Literal[30] = 30
    primary_threshold: Literal["0.02"] = "0.02"
    sensitivity_threshold: Literal["0.05"] = "0.05"
    smoothing: Literal["(k+0.5)/(n+1)"] = "(k+0.5)/(n+1)"
    decimal_precision: Literal[28] = 28
    decimal_rounding: Literal["ROUND_HALF_EVEN"] = "ROUND_HALF_EVEN"
    cutoff_utc: Literal["00:00:00"] = "00:00:00"
    window_start_utc: Literal["00:01:00"] = "00:01:00"
    horizon_seconds: Literal[86400] = 86400
    shadow_days: Literal[28] = 28
    feature_policy: Literal["known-closed-31-delay180.v1"] = "known-closed-31-delay180.v1"
    maximum_feature_delay_seconds: Literal[180] = 180
    label_grace_seconds: Literal[86400] = 86400
    anchor_policy: Literal["scheduled_bar_open"] = "scheduled_bar_open"
    quantile_protocol: Literal["not_registered"] = "not_registered"
    ci_protocol: Literal["not_registered"] = "not_registered"
    matching_protocol: Literal["matching.v1"] = "matching.v1"
    promotion_allowed: Literal[False] = False


class PathForecastParameters(PathForecastParametersV1):
    """New prospective protocol; legacy V1 retains its exact old JSON shape."""

    protocol_version: Literal["path-shadow-24h.v2"] = "path-shadow-24h.v2"
    ci_protocol: Literal["utc-day-paired-percentile-bootstrap.v2"] = (
        "utc-day-paired-percentile-bootstrap.v2"
    )
    ci_confidence: Literal["0.95"] = "0.95"
    ci_seed: Literal[271828] = 271828
    ci_replicates: Literal[2000] = 2000
    ci_percentile: Literal["nearest_rank_ceil"] = "nearest_rank_ceil"
    ci_block: Literal["UTC-day-all-assets-together"] = "UTC-day-all-assets-together"
    ci_estimator: Literal["equal_day_mean_baseline_minus_candidate_brier"] = (
        "equal_day_mean_baseline_minus_candidate_brier"
    )


def parse_path_parameters(payload: dict) -> PathForecastParameters | PathForecastParametersV1:
    """Explicit stored version; no injected V2 fields on a historical V1 row."""
    version = payload.get("protocol_version")
    if version == "path-shadow-24h.v1":
        return PathForecastParametersV1.model_validate(payload)
    if version == "path-shadow-24h.v2":
        return PathForecastParameters.model_validate(payload)
    raise ValueError("path_forecast_protocol_version_unsupported")


class PathTrainingWindow(FrozenContract):
    schema_version: Literal["path-training-window.v1"] = "path-training-window.v1"
    input_cutoff_at: datetime
    path: PathRiskResult
    features: MarketFeatureSnapshot | None = None
    label_known_at: datetime
    label_ref: str = Field(min_length=1)
    input_hash: str = Field(pattern=r"^[a-f0-9]{64}$")

    @field_validator("input_cutoff_at", "label_known_at")
    @classmethod
    def clocks(cls, value: datetime) -> datetime:
        return aware_utc(value)

    @model_validator(mode="after")
    def source_clocks(self) -> PathTrainingWindow:
        for value in (
            self.path.window_start_at,
            self.path.window_end_at,
            self.path.as_of,
            self.path.anchor_at,
        ):
            aware_utc(value)
        if self.features is not None:
            for value in (
                self.features.as_of,
                self.features.known_at,
                self.features.latest_close_at,
            ):
                if value is not None:
                    aware_utc(value)
        return self


class EmpiricalDistribution(FrozenContract):
    n: int = Field(ge=0, le=90)
    long_mae_sorted: tuple[Decimal, ...]
    short_mae_sorted: tuple[Decimal, ...]
    # These are all empirical support points. No unregistered quantile is selected.
    quantile_upper_bound: None = None

    @model_validator(mode="after")
    def supports(self) -> EmpiricalDistribution:
        for values in (self.long_mae_sorted, self.short_mae_sorted):
            if (
                len(values) != self.n
                or tuple(sorted(values)) != values
                or any(not item.is_finite() or item < 0 for item in values)
            ):
                raise ValueError("path_forecast_empirical_distribution_invalid")
        return self


class FrozenPathForecastModel(FrozenContract):
    schema_version: Literal["frozen-path-forecast-model.v1"] = "frozen-path-forecast-model.v1"
    scope: MarketPathScope
    training_cutoff_at: datetime
    history_start_at: datetime
    parameters: PathForecastParameters | PathForecastParametersV1 = Field(
        discriminator="protocol_version"
    )
    accepted: tuple[PathTrainingWindow, ...]
    excluded: tuple[tuple[str, tuple[str, ...]], ...]
    baseline: EmpiricalDistribution
    conditional_low: EmpiricalDistribution
    conditional_high: EmpiricalDistribution
    training_vol_median: Decimal | None
    candidate_training_n: int = Field(ge=0, le=90)
    independent_day_blocks: int = Field(ge=0, le=90)
    source_authentication: Literal["caller_must_authenticate_originals"] = (
        "caller_must_authenticate_originals"
    )
    model_hash: str = Field(pattern=r"^[a-f0-9]{64}$")

    @field_validator("training_cutoff_at", "history_start_at")
    @classmethod
    def clocks(cls, value: datetime) -> datetime:
        return aware_utc(value)


class PathEventFrequency(FrozenContract):
    direction: Literal["long", "short"]
    threshold: Decimal
    n: int = Field(ge=0, le=90)
    k: int = Field(ge=0, le=90)
    probability: Decimal | None
    status: Literal["experimental_uncalibrated", "insufficient"]

    @model_validator(mode="after")
    def valid_frequency(self) -> PathEventFrequency:
        if self.k > self.n or (
            self.probability is not None
            and (not self.probability.is_finite() or not 0 <= self.probability <= 1)
        ):
            raise ValueError("path_forecast_frequency_invalid")
        if (self.probability is None) != (self.status == "insufficient"):
            raise ValueError("path_forecast_frequency_status_invalid")
        return self


class RiskPathForecast(FrozenContract):
    schema_version: Literal["risk-path-forecast.v1"] = "risk-path-forecast.v1"
    scope: MarketPathScope
    model_version: Literal["BASE_EMPIRICAL_V1", "COND_VOL_V1"]
    model_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    parameter_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    feature_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    feature_latest_close_at: datetime | None = None
    feature_known_at: datetime | None = None
    feature_delay_seconds: int | None = Field(default=None, ge=0)
    cutoff_at: datetime
    published_at: datetime
    publication_status: Literal["on_time", "late"]
    window_start_at: datetime
    window_end_at: datetime
    anchor_policy: Literal["scheduled_bar_open"] = "scheduled_bar_open"
    anchor_price: None = None
    selected_state: Literal["low", "high"] | None
    frequencies: tuple[PathEventFrequency, ...]
    empirical_distribution: EmpiricalDistribution | None
    calibration_status: Literal["experimental_uncalibrated", "insufficient"]
    promotion_allowed: Literal[False] = False
    reasons: tuple[str, ...]
    payload_hash: str = Field(pattern=r"^[a-f0-9]{64}$")

    @field_validator(
        "cutoff_at",
        "published_at",
        "window_start_at",
        "window_end_at",
        "feature_latest_close_at",
        "feature_known_at",
    )
    @classmethod
    def clocks(cls, value: datetime | None) -> datetime | None:
        return aware_utc(value) if value is not None else None

    @model_validator(mode="after")
    def fixed_window(self) -> RiskPathForecast:
        if self.cutoff_at != self.cutoff_at.replace(hour=0, minute=0, second=0, microsecond=0):
            raise ValueError("path_forecast_daily_cutoff_required")
        if (
            self.window_start_at != self.cutoff_at + timedelta(minutes=1)
            or (self.window_end_at != self.window_start_at + timedelta(days=1))
            or self.published_at < self.cutoff_at
        ):
            raise ValueError("path_forecast_fixed_window_invalid")
        if self.publication_status != (
            "late" if self.published_at > self.window_start_at else "on_time"
        ):
            raise ValueError("path_forecast_publication_status_invalid")
        if len(self.frequencies) != 4 or {
            (item.direction, item.threshold) for item in self.frequencies
        } != {
            (direction, Decimal(threshold))
            for direction in ("long", "short")
            for threshold in ("0.02", "0.05")
        }:
            raise ValueError("path_forecast_targets_invalid")
        return self


class PathCandleReference(FrozenContract):
    open_at: datetime
    revision_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    capture_ref: str = Field(min_length=1)
    capture_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    row_index: int = Field(ge=0)
    received_at: datetime
    known_at: datetime

    @field_validator("open_at", "received_at", "known_at")
    @classmethod
    def clocks(cls, value: datetime) -> datetime:
        return aware_utc(value)


class PathFeatureManifest(FrozenContract):
    schema_version: Literal["path-feature-manifest.v1"] = "path-feature-manifest.v1"
    scope: MarketPathScope
    cutoff_at: datetime
    captured_at: datetime
    features: MarketFeatureSnapshot
    feature_delay_seconds: int | None = Field(default=None, ge=0)
    originals: tuple[PathCandleReference, ...]
    payload_hash: str = Field(pattern=r"^[a-f0-9]{64}$")

    @field_validator("cutoff_at", "captured_at")
    @classmethod
    def clocks(cls, value: datetime) -> datetime:
        return aware_utc(value)


class PathObservationReceipt(FrozenContract):
    schema_version: Literal["path-observation-receipt.v1"] = "path-observation-receipt.v1"
    scope: MarketPathScope
    input_cutoff_at: datetime
    path: PathRiskResult
    label_known_at: datetime
    originals: tuple[PathCandleReference, ...]
    calculator_version: Literal["path-risk-1m.v1"] = "path-risk-1m.v1"
    payload_hash: str = Field(pattern=r"^[a-f0-9]{64}$")

    @field_validator("input_cutoff_at", "label_known_at")
    @classmethod
    def clocks(cls, value: datetime) -> datetime:
        return aware_utc(value)

    @model_validator(mode="after")
    def fixed_window(self) -> PathObservationReceipt:
        start = self.input_cutoff_at + timedelta(minutes=1)
        if self.path.scope != self.scope or (
            self.path.window_start_at,
            self.path.window_end_at,
            self.path.anchor_at,
        ) != (start, start + timedelta(days=1), start):
            raise ValueError("path_observation_scope_or_window_invalid")
        if aware_utc(self.path.as_of) > self.label_known_at or any(
            ref.known_at > self.path.as_of for ref in self.originals
        ):
            raise ValueError("path_observation_knowledge_invalid")
        return self


class PathQualification(FrozenContract):
    independent_day_blocks: int
    independent_event_blocks: int | None
    minimum_time_blocks: Literal[100] = 100
    minimum_event_blocks: Literal[30] = 30
    ci_status: Literal["not_registered", "not_evaluated"] = "not_evaluated"
    predictive_gain: None = None
    promotion_allowed: Literal[False] = False
    reasons: tuple[str, ...]


class PathForecastHistoryPort(Protocol):
    """The owner authenticates original paths/receipts before passing these inputs."""

    def training_windows(
        self, *, scope: MarketPathScope, known_by: datetime
    ) -> tuple[PathTrainingWindow, ...]: ...

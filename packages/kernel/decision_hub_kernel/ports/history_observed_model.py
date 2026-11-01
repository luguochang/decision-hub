"""Actual authenticated source admission and prospective-use successor contracts."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field, model_validator

from .history_observed_training import HistoryObservedContract, fixed_cutoffs, verify_payload
from .market_path import MarketPathScope
from .path_forecast import EmpiricalDistribution


class HistoryObservedSourceAdmissionV1(HistoryObservedContract):
    schema_version: Literal["history-observed-source-admission.v1"] = (
        "history-observed-source-admission.v1"
    )
    registration_id: str
    protocol_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    dataset_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    scope: MarketPathScope
    registered_at: datetime
    data_information_asof: datetime
    dataset_sealed_at: datetime
    authenticated_at: datetime
    daily_cutoffs: tuple[datetime, ...]
    source_manifest_hashes: tuple[str, ...]
    readset_original_ref: str
    readset_original_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    implementation_files: tuple[tuple[str, str], ...]
    selected_capture_count: int = Field(ge=1)
    source_admission: Literal["verified_originals_at_fit"] = "verified_originals_at_fit"

    @model_validator(mode="after")
    def actual_admission(self) -> HistoryObservedSourceAdmissionV1:
        if (
            not self.registered_at
            <= self.data_information_asof
            <= self.dataset_sealed_at
            <= self.authenticated_at
        ):
            raise ValueError("history_observed_admission_clock_invalid")
        if (
            self.daily_cutoffs != fixed_cutoffs(self.registered_at)
            or len(self.source_manifest_hashes) != 90
        ):
            raise ValueError("history_observed_admission_fixed_source_windows_required")
        if not self.implementation_files or len({p for p, _ in self.implementation_files}) != len(
            self.implementation_files
        ):
            raise ValueError("history_observed_admission_implementation_invalid")
        return self


class HistoryObservedFitValuesV1(HistoryObservedContract):
    schema_version: Literal["history-observed-fit-values.v1"] = "history-observed-fit-values.v1"
    admission: HistoryObservedSourceAdmissionV1
    derived_at: datetime
    fit_started_at: datetime
    fit_completed_at: datetime
    baseline: EmpiricalDistribution
    conditional_low: EmpiricalDistribution
    conditional_high: EmpiricalDistribution
    training_vol_median: Decimal
    baseline_method: Literal["BASE_EMPIRICAL_HISTORY_OBS_V1"] = "BASE_EMPIRICAL_HISTORY_OBS_V1"
    candidate_method: Literal["COND_VOL_HISTORY_OBS_V1"] = "COND_VOL_HISTORY_OBS_V1"

    @model_validator(mode="after")
    def fit_completed(self) -> HistoryObservedFitValuesV1:
        verify_payload(self.admission)
        if (
            not self.admission.dataset_sealed_at
            <= self.admission.authenticated_at
            <= self.fit_started_at
            <= self.fit_completed_at
            or self.derived_at > self.admission.dataset_sealed_at
        ):
            raise ValueError("history_observed_fit_actual_clock_invalid")
        if (
            self.baseline.n != self.conditional_low.n + self.conditional_high.n
            or self.baseline.n < 60
            or min(self.conditional_low.n, self.conditional_high.n) < 30
            or not self.training_vol_median.is_finite()
            or self.training_vol_median < 0
        ):
            raise ValueError("history_observed_fit_support_gate_invalid")
        return self


class FrozenHistoryObservedPathModelV2(HistoryObservedContract):
    schema_version: Literal["frozen-history-observed-path-model.v2"] = (
        "frozen-history-observed-path-model.v2"
    )
    fit: HistoryObservedFitValuesV1
    model_known_at: datetime
    execution_eligible_for_prospective_use: Literal[True] = True

    @model_validator(mode="after")
    def model_saved(self) -> FrozenHistoryObservedPathModelV2:
        verify_payload(self.fit)
        if self.model_known_at < self.fit.fit_completed_at:
            raise ValueError("history_observed_model_save_before_fit")
        return self


class HistoryObservedProspectiveForecastV1(HistoryObservedContract):
    schema_version: Literal["history-observed-prospective-forecast.v1"] = (
        "history-observed-prospective-forecast.v1"
    )
    registration_id: str
    scope: MarketPathScope
    cutoff_at: datetime
    model_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    model_known_at: datetime
    model_authenticated_at: datetime
    feature_known_at: datetime
    latest_close_at: datetime
    feature_original_ref: str
    feature_original_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    realized_vol_30m: Decimal
    selected_group: Literal["low", "high"]
    threshold: Literal["0.02", "0.05"]
    baseline_n: int = Field(ge=60, le=90)
    conditional_n: int = Field(ge=30, le=90)
    baseline_long_probability: Decimal
    baseline_short_probability: Decimal
    conditional_long_probability: Decimal
    conditional_short_probability: Decimal
    produced_at: datetime

    @model_validator(mode="after")
    def future_only(self) -> HistoryObservedProspectiveForecastV1:
        if not (
            self.model_known_at <= self.cutoff_at <= self.model_authenticated_at <= self.produced_at
            and self.feature_known_at <= self.cutoff_at
            and self.latest_close_at <= self.cutoff_at
            and (self.cutoff_at - self.latest_close_at).total_seconds() <= 180
            and self.realized_vol_30m.is_finite()
            and self.realized_vol_30m >= 0
        ):
            raise ValueError("history_observed_prospective_clock_or_feature_invalid")
        for value in (
            self.baseline_long_probability,
            self.baseline_short_probability,
            self.conditional_long_probability,
            self.conditional_short_probability,
        ):
            if not value.is_finite() or not 0 <= value <= 1:
                raise ValueError("history_observed_probability_invalid")
        return self

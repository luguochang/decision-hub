"""Independent history-at-fit contracts; never an old PIT forecast or trial."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from statistics import median
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .market_path import FrozenContract, MarketPathScope, aware_utc, canonical_hash, spot_scope
from .path_forecast import EmpiricalDistribution


def fixed_cutoffs(registered_at: datetime) -> tuple[datetime, ...]:
    latest = (aware_utc(registered_at) - timedelta(days=1, minutes=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return tuple(latest - timedelta(days=offset) for offset in range(89, -1, -1))


class HistoryObservedContract(FrozenContract):
    payload_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    training_mode: Literal["history_observed_at_fit"] = "history_observed_at_fit"
    historical_pit_verified: Literal[False] = False
    promotion_allowed: Literal[False] = False

    @field_validator("*", mode="after")
    @classmethod
    def clocks(cls, value: object) -> object:
        return aware_utc(value) if isinstance(value, datetime) else value


def seal[T: HistoryObservedContract](contract: type[T], **values: object) -> T:
    candidate = contract.model_validate({**values, "payload_hash": "0" * 64})
    payload = candidate.model_dump(mode="json")
    payload["payload_hash"] = canonical_hash(
        {k: v for k, v in payload.items() if k != "payload_hash"}
    )
    return contract.model_validate(payload)


def verify_payload[T: HistoryObservedContract](value: T) -> T:
    # Revalidate nested model_copy mutations as JSON, not trusted model instances.
    checked = type(value).model_validate_json(value.model_dump_json())
    if (
        canonical_hash(checked.model_dump(mode="json", exclude={"payload_hash"}))
        != checked.payload_hash
    ):
        raise ValueError("history_observed_payload_changed")
    return checked


class HistoryObservedTrainingProtocolV1(HistoryObservedContract):
    schema_version: Literal["history-observed-training-protocol.v1"] = (
        "history-observed-training-protocol.v1"
    )
    registration_id: str = Field(min_length=1, max_length=80)
    request_id: str = Field(min_length=1, max_length=128)
    registered_at: datetime
    source_proof_recorded_at: datetime
    scopes: tuple[MarketPathScope, ...]
    daily_cutoffs: tuple[datetime, ...]
    implementation_files: tuple[tuple[str, str], ...]
    maximum_windows: Literal[90] = 90
    minimum_complete_days: Literal[60] = 60
    minimum_group_days: Literal[30] = 30
    requested_limit: Literal[300] = 300
    daily_page_cap: Literal[6] = 6
    per_page_retry_cap: Literal[1] = 1
    per_job_attempt_cap: Literal[12] = 12
    http_attempt_accounting: Literal["all_http_attempts"] = "all_http_attempts"
    total_attempt_cap: Literal[1200] = 1200
    per_scope_attempt_cap: Literal[600] = 600
    acquisition_deadline_seconds: Literal[21600] = 21600
    minimum_request_spacing_seconds: Literal[1] = 1
    source_admission: Literal["not_evaluated"] = "not_evaluated"
    median_ties: Literal["low_le_median_high_gt_median"] = "low_le_median_high_gt_median"
    decimal_precision: Literal[28] = 28
    decimal_rounding: Literal["ROUND_HALF_EVEN"] = "ROUND_HALF_EVEN"

    @field_validator("daily_cutoffs", mode="after")
    @classmethod
    def utc_cutoffs(cls, value: tuple[datetime, ...]) -> tuple[datetime, ...]:
        return tuple(aware_utc(cutoff) for cutoff in value)

    @model_validator(mode="after")
    def fixed_registration(self) -> HistoryObservedTrainingProtocolV1:
        if self.daily_cutoffs != fixed_cutoffs(self.registered_at):
            raise ValueError("history_observed_fixed_90_UTC_days_required")
        if self.scopes != (spot_scope("BTC-USDT"), spot_scope("ETH-USDT")):
            raise ValueError("history_observed_exact_two_scopes_required")
        if self.source_proof_recorded_at != self.registered_at or not self.implementation_files:
            raise ValueError("history_observed_registration_source_clock_invalid")
        paths = [path for path, _ in self.implementation_files]
        if len(set(paths)) != len(paths) or any(
            not re_hash(digest) for _, digest in self.implementation_files
        ):
            raise ValueError("history_observed_implementation_manifest_invalid")
        return self


def re_hash(value: str) -> bool:
    return len(value) == 64 and all(letter in "0123456789abcdef" for letter in value)


class HistoryObservedFeatureV1(HistoryObservedContract):
    schema_version: Literal["history-observed-feature.v1"] = "history-observed-feature.v1"
    scope: MarketPathScope
    market_cutoff_at: datetime
    data_information_asof: datetime
    derived_at: datetime
    latest_close_at: datetime | None
    source_known_at: datetime | None
    complete_close_count: int = Field(ge=0, le=31)
    realized_vol_30m: Decimal | None
    source_manifest_ref: str = Field(min_length=1)
    source_manifest_hash: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def feature_clocks(self) -> HistoryObservedFeatureV1:
        if not self.market_cutoff_at <= self.data_information_asof <= self.derived_at:
            raise ValueError("history_observed_feature_clock_invalid")
        if self.source_known_at is not None and self.source_known_at > self.data_information_asof:
            raise ValueError("history_observed_feature_future_knowledge")
        if self.latest_close_at is not None and self.latest_close_at > self.market_cutoff_at:
            raise ValueError("history_observed_feature_future_market_bar")
        if self.realized_vol_30m is not None and (
            self.complete_close_count != 31
            or self.latest_close_at is None
            or (self.market_cutoff_at - self.latest_close_at).total_seconds() > 180
            or not self.realized_vol_30m.is_finite()
            or self.realized_vol_30m < 0
        ):
            raise ValueError("history_observed_feature_false_complete")
        return self


class HistoryObservedLabelV1(FrozenContract):
    """Bounded label summary; minute originals live in the partition manifest."""

    scope: MarketPathScope
    as_of: datetime
    window_start_at: datetime
    window_end_at: datetime
    anchor_at: datetime
    anchor_status: Literal["verified", "unknown"]
    anchor_price: Decimal | None
    window_status: Literal["complete", "partial", "unknown", "pending"]
    quality: Literal["observed", "observed_lower_bound", "unknown"]
    observed_count: int = Field(ge=0, le=1440)
    expected_count: Literal[1440] = 1440
    pending_count: int = Field(ge=0, le=1440)
    missing_count: int = Field(ge=0, le=1440)
    mae_long: Decimal | None
    mae_short: Decimal | None
    semantic_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    manifest_hash: str = Field(pattern=r"^[a-f0-9]{64}$")

    @field_validator("as_of", "window_start_at", "window_end_at", "anchor_at")
    @classmethod
    def clocks(cls, value: datetime) -> datetime:
        return aware_utc(value)


class HistoryObservedTrainingWindowV1(HistoryObservedContract):
    schema_version: Literal["history-observed-training-window.v1"] = (
        "history-observed-training-window.v1"
    )
    scope: MarketPathScope
    registered_at: datetime
    market_cutoff_at: datetime
    data_information_asof: datetime
    label_known_at: datetime
    path: HistoryObservedLabelV1
    feature: HistoryObservedFeatureV1
    source_manifest_ref: str = Field(min_length=1)
    source_manifest_hash: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def window_clocks(self) -> HistoryObservedTrainingWindowV1:
        start = self.market_cutoff_at + timedelta(minutes=1)
        end = start + timedelta(days=1)
        if self.market_cutoff_at != self.market_cutoff_at.replace(
            hour=0, minute=0, second=0, microsecond=0
        ):
            raise ValueError("history_observed_UTC_daily_cutoff_required")
        if not end <= self.registered_at <= self.data_information_asof <= self.label_known_at:
            raise ValueError("history_observed_label_clock_invalid")
        for clock in (
            self.path.as_of,
            self.path.window_start_at,
            self.path.window_end_at,
            self.path.anchor_at,
        ):
            aware_utc(clock)
        if (
            self.path.scope,
            self.path.as_of,
            self.path.window_start_at,
            self.path.window_end_at,
            self.path.anchor_at,
            self.path.expected_count,
        ) != (self.scope, self.data_information_asof, start, end, start, 1440):
            raise ValueError("history_observed_exact_label_window_required")
        if (
            self.feature.scope,
            self.feature.market_cutoff_at,
            self.feature.data_information_asof,
            self.feature.derived_at,
            self.feature.source_manifest_ref,
            self.feature.source_manifest_hash,
        ) != (
            self.scope,
            self.market_cutoff_at,
            self.data_information_asof,
            self.label_known_at,
            self.source_manifest_ref,
            self.source_manifest_hash,
        ):
            raise ValueError("history_observed_feature_binding_invalid")
        verify_payload(self.feature)
        return self


def label_complete(window: HistoryObservedTrainingWindowV1) -> bool:
    path = window.path
    return (
        path.window_status == "complete"
        and path.anchor_status == "verified"
        and path.anchor_price is not None
        and path.anchor_price.is_finite()
        and path.anchor_price > 0
        and path.observed_count == 1440
        and path.pending_count == 0
        and path.missing_count == 0
        and path.quality == "observed"
        and all(
            value is not None and value.is_finite() and value >= 0
            for value in (path.mae_long, path.mae_short)
        )
    )


class HistoryObservedDatasetManifestV1(HistoryObservedContract):
    schema_version: Literal["history-observed-dataset.v1"] = "history-observed-dataset.v1"
    registration_id: str
    protocol_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    scope: MarketPathScope
    registered_at: datetime
    data_information_asof: datetime
    derived_at: datetime
    sealed_at: datetime
    windows: tuple[HistoryObservedTrainingWindowV1, ...]

    @model_validator(mode="after")
    def fixed_dataset(self) -> HistoryObservedDatasetManifestV1:
        if (
            not self.registered_at
            <= self.data_information_asof
            <= self.derived_at
            <= self.sealed_at
        ):
            raise ValueError("history_observed_dataset_clock_invalid")
        if self.data_information_asof > self.registered_at + timedelta(hours=6):
            raise ValueError("history_observed_acquisition_deadline_exceeded")
        if tuple(item.market_cutoff_at for item in self.windows) != fixed_cutoffs(
            self.registered_at
        ):
            raise ValueError("history_observed_dataset_fixed_denominator_required")
        for item in self.windows:
            verify_payload(item)
            if (item.scope, item.registered_at, item.data_information_asof) != (
                self.scope,
                self.registered_at,
                self.data_information_asof,
            ) or item.label_known_at > self.derived_at:
                raise ValueError("history_observed_dataset_window_binding_invalid")
        return self


class HistoryTrainingReadinessV1(HistoryObservedContract):
    schema_version: Literal["history-observed-readiness.v1"] = "history-observed-readiness.v1"
    dataset_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    scope: MarketPathScope
    complete_label_days: int = Field(ge=0, le=90)
    joint_days: int = Field(ge=0, le=90)
    low_days: int = Field(ge=0, le=90)
    high_days: int = Field(ge=0, le=90)
    rv_median: Decimal | None
    excluded: tuple[tuple[datetime, tuple[str, ...]], ...]
    baseline_count_gate_met: bool
    candidate_count_gate_met: bool
    ready_for_real_fit: Literal[False] = False
    source_admission: Literal["not_evaluated"] = "not_evaluated"

    @model_validator(mode="after")
    def count_gate(self) -> HistoryTrainingReadinessV1:
        if (
            self.low_days + self.high_days != self.joint_days
            or self.joint_days > self.complete_label_days
            or len(self.excluded) + self.joint_days != 90
            or self.baseline_count_gate_met != (self.complete_label_days >= 60)
            or self.candidate_count_gate_met
            != (self.joint_days >= 60 and min(self.low_days, self.high_days) >= 30)
            or (self.rv_median is None) != (self.joint_days == 0)
            or (
                self.rv_median is not None
                and (not self.rv_median.is_finite() or self.rv_median < 0)
            )
        ):
            raise ValueError("history_observed_readiness_count_invalid")
        return self


def readiness(dataset: HistoryObservedDatasetManifestV1) -> HistoryTrainingReadinessV1:
    verify_payload(dataset)
    labels = [item for item in dataset.windows if label_complete(item)]
    joint = [item for item in labels if item.feature.realized_vol_30m is not None]
    values = [item.feature.realized_vol_30m for item in joint]
    numeric = [item for item in values if item is not None]
    with localcontext() as context:
        context.prec = 28
        context.rounding = ROUND_HALF_EVEN
        middle = Decimal(median(numeric)) if numeric else None
    low = sum(item <= middle for item in numeric) if middle is not None else 0
    excluded = tuple(
        (
            item.market_cutoff_at,
            tuple(
                reason
                for reason, failed in (
                    ("complete_1440_label_unavailable", not label_complete(item)),
                    ("complete_31_feature_unavailable", item.feature.realized_vol_30m is None),
                )
                if failed
            ),
        )
        for item in dataset.windows
        if item not in joint
    )
    return seal(
        HistoryTrainingReadinessV1,
        dataset_hash=dataset.payload_hash,
        scope=dataset.scope,
        complete_label_days=len(labels),
        joint_days=len(joint),
        low_days=low,
        high_days=len(joint) - low,
        rv_median=middle,
        excluded=excluded,
        baseline_count_gate_met=len(labels) >= 60,
        candidate_count_gate_met=len(joint) >= 60 and min(low, len(joint) - low) >= 30,
    )


class FrozenHistoryObservedPathModelV1(HistoryObservedContract):
    schema_version: Literal["frozen-history-observed-path-model.v1"] = (
        "frozen-history-observed-path-model.v1"
    )
    registration_id: str
    dataset_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    scope: MarketPathScope
    registered_at: datetime
    data_information_asof: datetime
    derived_at: datetime
    dataset_sealed_at: datetime
    fit_started_at: datetime
    fit_completed_at: datetime
    model_known_at: datetime
    baseline: EmpiricalDistribution
    conditional_low: EmpiricalDistribution
    conditional_high: EmpiricalDistribution
    training_vol_median: Decimal
    ready_for_prospective_use: Literal[False] = False
    baseline_method: Literal["BASE_EMPIRICAL_HISTORY_OBS_V1"] = "BASE_EMPIRICAL_HISTORY_OBS_V1"
    candidate_method: Literal["COND_VOL_HISTORY_OBS_V1"] = "COND_VOL_HISTORY_OBS_V1"

    @model_validator(mode="after")
    def fit_chain(self) -> FrozenHistoryObservedPathModelV1:
        chain = (
            self.registered_at,
            self.data_information_asof,
            self.derived_at,
            self.dataset_sealed_at,
            self.fit_started_at,
            self.fit_completed_at,
            self.model_known_at,
        )
        if tuple(sorted(chain)) != chain:
            raise ValueError("history_observed_fit_clock_invalid")
        if (
            self.baseline.n != self.conditional_low.n + self.conditional_high.n
            or self.baseline.n < 60
            or min(self.conditional_low.n, self.conditional_high.n) < 30
            or not self.training_vol_median.is_finite()
            or self.training_vol_median < 0
        ):
            raise ValueError("history_observed_model_count_gate_invalid")
        return self


def verify_model_cutoff(
    model: FrozenHistoryObservedPathModelV1,
    dataset: HistoryObservedDatasetManifestV1,
    cutoff_at: datetime,
    now: datetime,
) -> None:
    verify_payload(model)
    verify_payload(dataset)
    cutoff, clock = aware_utc(cutoff_at), aware_utc(now)
    if not model.model_known_at <= cutoff <= clock or (
        model.registration_id,
        model.dataset_hash,
        model.scope,
        model.registered_at,
        model.data_information_asof,
        model.derived_at,
        model.dataset_sealed_at,
    ) != (
        dataset.registration_id,
        dataset.payload_hash,
        dataset.scope,
        dataset.registered_at,
        dataset.data_information_asof,
        dataset.derived_at,
        dataset.sealed_at,
    ):
        raise ValueError("history_observed_model_future_or_binding_invalid")
    count_gate = readiness(dataset)
    if not count_gate.candidate_count_gate_met or model.training_vol_median != count_gate.rv_median:
        raise ValueError("history_observed_model_partition_changed")
    joint = [
        item
        for item in dataset.windows
        if label_complete(item) and item.feature.realized_vol_30m is not None
    ]
    for support, windows in (
        (model.baseline, joint),
        (
            model.conditional_low,
            [
                item
                for item in joint
                if item.feature.realized_vol_30m is not None
                and item.feature.realized_vol_30m <= model.training_vol_median
            ],
        ),
        (
            model.conditional_high,
            [
                item
                for item in joint
                if item.feature.realized_vol_30m is not None
                and item.feature.realized_vol_30m > model.training_vol_median
            ],
        ),
    ):
        if (
            support.n != len(windows)
            or support.long_mae_sorted
            != tuple(
                sorted(item.path.mae_long for item in windows if item.path.mae_long is not None)
            )
            or support.short_mae_sorted
            != tuple(
                sorted(item.path.mae_short for item in windows if item.path.mae_short is not None)
            )
        ):
            raise ValueError("history_observed_model_support_changed")

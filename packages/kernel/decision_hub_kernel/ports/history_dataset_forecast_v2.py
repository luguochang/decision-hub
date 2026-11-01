"""Independent V2 future risk frequencies; never a legacy registration forecast."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .history_observed_training import HistoryObservedContract
from .market_path import FrozenContract, MarketCandle, spot_scope

LOADED_SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


class HistoryDatasetRiskFrequencyV2(FrozenContract):
    threshold: Literal["0.02", "0.05"]
    baseline_n: int = Field(ge=60, le=90)
    conditional_n: int = Field(ge=30, le=90)
    baseline_long_probability: Decimal
    baseline_short_probability: Decimal
    conditional_long_probability: Decimal
    conditional_short_probability: Decimal

    @model_validator(mode="after")
    def probabilities(self):
        if any(
            not value.is_finite() or not 0 <= value <= 1
            for value in (
                self.baseline_long_probability,
                self.baseline_short_probability,
                self.conditional_long_probability,
                self.conditional_short_probability,
            )
        ):
            raise ValueError("history_v2_forecast_probability_invalid")
        return self


class HistoryDatasetProspectiveForecastV2(HistoryObservedContract):
    schema_version: Literal["history-dataset-prospective-forecast.v2"] = (
        "history-dataset-prospective-forecast.v2"
    )
    forecast_id: str
    owner_claim: str = Field(min_length=1, max_length=128)
    request_id: str = Field(min_length=1, max_length=128)
    scope_id: str
    model_id: str
    model_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    model_known_at: datetime
    model_authenticated_at: datetime
    model_original_ref: str
    model_original_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    cutoff_at: datetime
    latest_close_at: datetime
    feature_known_at: datetime
    selected_closes: tuple[MarketCandle, ...] = Field(min_length=31, max_length=31)
    feature_original_ref: str
    feature_original_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    original_readset: tuple[tuple[Literal["raw", "archive"], str, str], ...]
    realized_vol_30m: Decimal
    selected_group: Literal["low", "high"]
    frequencies: tuple[HistoryDatasetRiskFrequencyV2, ...] = Field(min_length=2, max_length=2)
    source_files: tuple[tuple[str, str], ...]
    source_original_ref: str
    source_original_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    produced_at: datetime
    recorded_at: datetime
    horizon_seconds: Literal[86400] = 86400
    target: Literal["24h_path_mae"] = "24h_path_mae"
    fixture_mode: bool
    owner_authenticated: Literal[False] = False
    financial_qualified: Literal[False] = False
    automatic_retry: Literal[False] = False

    @model_validator(mode="after")
    def bindings(self):
        if self.scope_id not in {spot_scope(asset).scope_id for asset in ("BTC-USDT", "ETH-USDT")}:
            raise ValueError("history_v2_forecast_scope_invalid")
        closes = self.selected_closes
        if not (
            self.model_known_at
            <= self.cutoff_at
            <= self.model_authenticated_at
            <= self.produced_at
            <= self.recorded_at
            and self.feature_known_at <= self.cutoff_at
            and self.latest_close_at <= self.cutoff_at
            and (self.cutoff_at - self.latest_close_at).total_seconds() <= 180
            and self.cutoff_at.timestamp() % 60 == 0
            and self.realized_vol_30m.is_finite()
            and self.realized_vol_30m >= 0
        ):
            raise ValueError("history_v2_forecast_actual_clock_invalid")
        if (
            closes[-1].close_at != self.latest_close_at
            or max(bar.known_at for bar in closes) != self.feature_known_at
            or any(bar.scope_id != self.scope_id or bar.close_at > self.cutoff_at for bar in closes)
            or any(
                bar.known_at > self.cutoff_at or not bar.capture_ref or not bar.capture_hash
                for bar in closes
            )
            or any(
                right.close_at - left.close_at != timedelta(minutes=1)
                for left, right in zip(closes, closes[1:], strict=False)
            )
            or tuple(item.threshold for item in self.frequencies) != ("0.02", "0.05")
        ):
            raise ValueError("history_v2_forecast_complete_feature_or_targets_invalid")
        if not self.source_files or len(dict(self.source_files)) != len(self.source_files):
            raise ValueError("history_v2_forecast_source_identity_invalid")
        return self

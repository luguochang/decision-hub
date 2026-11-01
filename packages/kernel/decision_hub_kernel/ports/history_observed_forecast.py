"""Independent immutable prospective records; no old trial or trading authority."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .history_observed_model import HistoryObservedProspectiveForecastV1
from .history_observed_training import HistoryObservedContract, verify_payload
from .market_path import FrozenContract, MarketCandle, MarketPathScope, aware_utc, spot_scope

LOADED_SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


class HistoryForecastIdentityV1(FrozenContract):
    operator_owner_claim: str = Field(min_length=1, max_length=128)
    request_id: str = Field(min_length=1, max_length=128)
    registration_id: str = Field(min_length=1, max_length=80)
    scope: MarketPathScope
    expected_model_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_model_known_at: datetime
    cutoff_at: datetime
    threshold: Literal["0.02", "0.05"]

    @field_validator("expected_model_known_at", "cutoff_at", mode="after")
    @classmethod
    def clocks(cls, value: datetime) -> datetime:
        return aware_utc(value)

    @model_validator(mode="after")
    def fixed_identity(self) -> HistoryForecastIdentityV1:
        if self.scope not in (spot_scope("BTC-USDT"), spot_scope("ETH-USDT")):
            raise ValueError("history_forecast_exact_spot_scope_required")
        if self.expected_model_known_at > self.cutoff_at or self.cutoff_at.timestamp() % 60:
            raise ValueError("history_forecast_P_T_identity_invalid")
        return self


class RecordedHistoryForecastV1(HistoryObservedContract):
    schema_version: Literal["recorded-history-observed-forecast.v1"] = (
        "recorded-history-observed-forecast.v1"
    )
    forecast_id: str = Field(min_length=1, max_length=80)
    identity: HistoryForecastIdentityV1
    protocol_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    model_original_ref: str
    model_original_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    original_model_sources: tuple[tuple[str, str], ...]
    consumer_implementation_files: tuple[tuple[str, str], ...]
    consumer_source_original_ref: str
    consumer_source_original_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    selected_closes: tuple[MarketCandle, ...] = Field(min_length=31, max_length=31)
    original_readset: tuple[tuple[Literal["archive", "raw"], str, str], ...] = Field(
        min_length=1,
        max_length=2048,
    )
    forecast: HistoryObservedProspectiveForecastV1
    horizon_seconds: Literal[86400] = 86400
    target: Literal["24h_path_mae"] = "24h_path_mae"
    recorded_at: datetime
    owner_authenticated: Literal[False] = False
    authority_scope: Literal["local_process_filesystem_access"] = "local_process_filesystem_access"
    financial_qualified: Literal[False] = False

    @model_validator(mode="after")
    def exact_binding(self) -> RecordedHistoryForecastV1:
        forecast = verify_payload(self.forecast)
        identity = self.identity
        if (
            forecast.registration_id,
            forecast.scope,
            forecast.model_hash,
            forecast.model_known_at,
            forecast.cutoff_at,
            forecast.threshold,
        ) != (
            identity.registration_id,
            identity.scope,
            identity.expected_model_hash,
            identity.expected_model_known_at,
            identity.cutoff_at,
            identity.threshold,
        ) or self.recorded_at < forecast.produced_at:
            raise ValueError("history_forecast_record_identity_or_clock_invalid")
        closes = self.selected_closes
        if (
            closes[-1].close_at != forecast.latest_close_at
            or max(bar.known_at for bar in closes) != forecast.feature_known_at
            or any(bar.scope_id != identity.scope.scope_id for bar in closes)
            or any(bar.known_at > identity.cutoff_at for bar in closes)
            or any(
                right.close_at - left.close_at != timedelta(minutes=1)
                for left, right in zip(closes, closes[1:], strict=False)
            )
            or any(not bar.capture_ref or not bar.capture_hash for bar in closes)
        ):
            raise ValueError("history_forecast_record_31_close_binding_invalid")
        for sources in (self.original_model_sources, self.consumer_implementation_files):
            if not sources or len({path for path, _ in sources}) != len(sources):
                raise ValueError("history_forecast_record_source_identity_invalid")
            if any(
                len(digest) != 64 or set(digest) - set("0123456789abcdef") for _, digest in sources
            ):
                raise ValueError("history_forecast_record_source_hash_invalid")
        return self

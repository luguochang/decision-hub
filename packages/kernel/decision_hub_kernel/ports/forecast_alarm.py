"""Experimental forecast warnings and immutable PC receipt contracts."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .market_path import FrozenContract, aware_utc

ALARM_POLICY = "shadow-forecast-ui-receipts.v1"
MATCHING_VERSION = "matching.v1"
MINIMUM_FREQUENCY = Decimal("0.2")
MINIMUM_LEAD_SECONDS = 300
EVENT_CLUSTER_SECONDS = 1800
AlarmDirection = Literal["long", "short"]
AlarmMethod = Literal["BASE_EMPIRICAL_V1", "COND_VOL_V1"]


class ForecastAlarmV1(FrozenContract):
    schema_version: Literal["forecast-alarm.v1"] = "forecast-alarm.v1"
    alarm_policy: Literal["shadow-forecast-ui-receipts.v1"] = ALARM_POLICY
    alarm_id: str
    trial_id: str
    prediction_id: str
    prediction_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    method: AlarmMethod
    scope_id: str
    cutoff_at: datetime
    published_at: datetime
    window_start_at: datetime
    window_end_at: datetime
    direction: AlarmDirection
    threshold: Decimal
    frequency: Decimal
    known_at: datetime
    experimental: Literal[True] = True
    probability_qualified: Literal[False] = False
    trading_authority: Literal[False] = False
    payload_hash: str = Field(pattern=r"^[a-f0-9]{64}$")

    @field_validator("cutoff_at", "published_at", "window_start_at", "window_end_at", "known_at")
    @classmethod
    def clocks(cls, value: datetime) -> datetime:
        return aware_utc(value)

    @model_validator(mode="after")
    def registered_gate(self) -> ForecastAlarmV1:
        if (
            self.threshold not in (Decimal(".02"), Decimal(".05"))
            or not self.frequency.is_finite()
            or not MINIMUM_FREQUENCY <= self.frequency <= 1
            or not self.cutoff_at <= self.published_at <= self.known_at <= self.window_start_at
            or not self.window_start_at < self.window_end_at
            or self.window_start_at != self.cutoff_at + timedelta(minutes=1)
            or self.window_end_at != self.window_start_at + timedelta(days=1)
        ):
            raise ValueError("forecast_alarm_gate_invalid")
        return self


class ForecastAlarmReceiptV1(FrozenContract):
    schema_version: Literal["forecast-alarm-receipt.v1"] = "forecast-alarm-receipt.v1"
    receipt_id: str
    alarm_id: str
    alarm_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    receipt_kind: Literal["displayed", "visible", "acknowledged"]
    owner_id: str = Field(min_length=1, max_length=128)
    request_id: str = Field(min_length=1, max_length=128)
    known_at: datetime
    parent_receipt_id: str | None = None
    parent_receipt_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    surface: Literal["pc"] = "pc"
    visible_duration_ms: int | None = Field(default=None, ge=1000)
    evidence_source: Literal["pc_browser_reported"] = "pc_browser_reported"
    payload_hash: str = Field(pattern=r"^[a-f0-9]{64}$")

    @field_validator("known_at")
    @classmethod
    def clock(cls, value: datetime) -> datetime:
        return aware_utc(value)

    @model_validator(mode="after")
    def parent_required(self) -> ForecastAlarmReceiptV1:
        if (
            self.receipt_kind == "displayed"
            and self.parent_receipt_id is not None
            or self.receipt_kind != "displayed"
            and self.parent_receipt_id is None
            or (self.parent_receipt_id is None) != (self.parent_receipt_hash is None)
            or self.receipt_kind == "visible"
            and self.visible_duration_ms is None
        ):
            raise ValueError("forecast_alarm_receipt_parent_invalid")
        return self


class ForecastAlarmViewV1(FrozenContract):
    alarm: ForecastAlarmV1
    display_status: Literal["not_recorded", "displayed"]
    exposure_status: Literal["not_recorded", "visible"]
    acknowledged: bool


class ForecastAlarmListV1(FrozenContract):
    schema_version: Literal["forecast-alarm-list.v1"] = "forecast-alarm-list.v1"
    scope_id: str
    as_of: datetime
    items: tuple[ForecastAlarmViewV1, ...]
    experimental: Literal[True] = True
    default_collapsed: Literal[True] = True
    probability_qualified: Literal[False] = False
    trading_authority: Literal[False] = False


class AlarmOutcomeV1(FrozenContract):
    """Caller authenticates the mature label original before constructing this."""

    scope_id: str
    cutoff_at: datetime
    window_start_at: datetime
    window_end_at: datetime
    direction: AlarmDirection
    threshold: Decimal
    event_presence: bool | None
    firstness: Literal["verified", "unknown", "not_applicable"]
    interval_start_at: datetime | None
    interval_end_at: datetime | None
    label_known_at: datetime
    original_ref: str
    original_hash: str = Field(pattern=r"^[a-f0-9]{64}$")

    @field_validator(
        "cutoff_at",
        "window_start_at",
        "window_end_at",
        "label_known_at",
        "interval_start_at",
        "interval_end_at",
    )
    @classmethod
    def clocks(cls, value: datetime | None) -> datetime | None:
        return aware_utc(value) if value is not None else None

    @model_validator(mode="after")
    def first_crossing_interval(self) -> AlarmOutcomeV1:
        if self.window_start_at != self.cutoff_at + timedelta(
            minutes=1
        ) or self.window_end_at != self.window_start_at + timedelta(days=1):
            raise ValueError("forecast_alarm_label_window_invalid")
        if self.event_presence is True and self.firstness == "verified":
            if (
                self.interval_start_at is None
                or self.interval_end_at is None
                or (self.interval_end_at - self.interval_start_at).total_seconds() != 60
                or not self.window_start_at
                <= self.interval_start_at
                < self.interval_end_at
                <= self.window_end_at
            ):
                raise ValueError("forecast_alarm_first_crossing_interval_invalid")
        return self

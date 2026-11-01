"""Immutable calendar-owned CPI results, separate from research execution rights."""
from decimal import Decimal
from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from .bls_cpi import BLS_SERIES
from .cpi_pre_event import CpiContextSource
from .event_scenario import Band
from .market_path import FrozenContract, canonical_hash

METRICS = ("headline_mom", "core_mom", "headline_yoy", "core_yoy")


class EventRealizedMetric(FrozenContract):
    metric_id: Literal["headline_mom", "core_mom", "headline_yoy", "core_yoy"]
    reference_period: str = Field(pattern=r"^20\d{2}-(0[1-9]|1[0-2])$")
    series_id: str
    seasonal_adjustment: Literal["seasonally_adjusted", "not_seasonally_adjusted"]
    unit: Literal["percent"] = "percent"
    value: Decimal | None
    missing_reasons: tuple[str, ...] = ()
    semantic_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    source: CpiContextSource

    @model_validator(mode="after")
    def identity(self):
        series, adjustment, _ = BLS_SERIES[self.metric_id]
        if ((self.series_id, self.seasonal_adjustment) != (series, adjustment)
            or (self.value is not None and not self.value.is_finite())
            or (self.value is None) != bool(self.missing_reasons)
            or any(not r.startswith("actual_") for r in self.missing_reasons)):
            raise ValueError("event_realized_metric_identity_invalid")
        return self


class EventRealizationSnapshot(FrozenContract):
    schema_version: Literal["event-realization.v1"] = "event-realization.v1"
    owner_kind: Literal["calendar_event"] = "calendar_event"
    realization_id: str
    event_key: str
    forecast_id: str
    owner_forecast_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    reference_period: str = Field(pattern=r"^20\d{2}-(0[1-9]|1[0-2])$")
    scheduled_at: AwareDatetime
    as_of: AwareDatetime
    known_at: AwareDatetime
    recorded_at: AwareDatetime
    schedule_source: CpiContextSource
    baseline_kind: Literal["prior_month"] = "prior_month"
    target_definition: Literal["index_derived_continuous_percent_change"] = "index_derived_continuous_percent_change"
    rate_rounding_policy: Literal["no_rate_rounding_before_band_assignment"] = "no_rate_rounding_before_band_assignment"
    headline_baseline: Decimal
    core_baseline: Decimal
    tolerance_pp: Decimal
    metrics: tuple[EventRealizedMetric, ...]
    headline_band: Band
    core_band: Band
    scenario_id: str
    input_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    revision_parent_ref: str | None = None
    financial_qualified: Literal[False] = False
    action_execution_authorized: Literal[False] = False
    payload_hash: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def identity(self):
        if (tuple(m.metric_id for m in self.metrics) != METRICS
            or any(m.value is None for m in self.metrics[:2])
            or any(m.reference_period != self.reference_period for m in self.metrics)
            or not self.scheduled_at <= self.known_at <= self.as_of <= self.recorded_at
            or self.known_at != max(m.source.known_at for m in self.metrics)
            or any(m.source.known_at < self.scheduled_at for m in self.metrics)
            or self.schedule_source.known_at >= self.scheduled_at
            or not all(v.is_finite() for v in (self.headline_baseline, self.core_baseline, self.tolerance_pp))
            or self.tolerance_pp <= 0):
            raise ValueError("event_realization_identity_or_clock_invalid")
        def band(value, baseline):
            delta = value - baseline
            return "cooler" if delta < -self.tolerance_pp else "hotter" if delta > self.tolerance_pp else "near"
        if (self.headline_band != band(self.metrics[0].value, self.headline_baseline)
            or self.core_band != band(self.metrics[1].value, self.core_baseline)
            or self.scenario_id != f"cpi:headline_{self.headline_band}:core_{self.core_band}"):
            raise ValueError("event_realization_unique_scenario_invalid")
        expected = canonical_hash({"forecast_hash": self.owner_forecast_hash,
            "actuals": [(m.metric_id, m.semantic_hash) for m in self.metrics],
            "revision_parent_ref": self.revision_parent_ref})
        if self.input_hash != expected or self.realization_id != "event-realization:" + expected:
            raise ValueError("event_realization_input_identity_invalid")
        return self


def seal_event_realization(body):
    draft = EventRealizationSnapshot.model_validate(body | {"payload_hash": "0" * 64})
    canonical = draft.model_dump(mode="json", exclude={"payload_hash"})
    return EventRealizationSnapshot.model_validate(canonical | {"payload_hash": canonical_hash(canonical)})

"""BLS index-derived actual/prior payloads; never release clocks or consensus."""
from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from .market_path import FrozenContract, canonical_hash

BLS_URL = "https://api.bls.gov/publicAPI/v1/timeseries/data/"
BLS_SOURCE_ID = "bls-public-api"
BLS_VERSION = "bls-cpi-index-derived.v1"
BLS_SERIES = {
    "headline_mom": ("CUSR0000SA0", "seasonally_adjusted", 1),
    "core_mom": ("CUSR0000SA0L1E", "seasonally_adjusted", 1),
    "headline_yoy": ("CUUR0000SA0", "not_seasonally_adjusted", 12),
    "core_yoy": ("CUUR0000SA0L1E", "not_seasonally_adjusted", 12),
}


def period_offset(period: str, months: int) -> str:
    if not re.fullmatch(r"20\d{2}-(0[1-9]|1[0-2])", period):
        raise ValueError("bls_reference_period_invalid")
    year, month = map(int, period.split("-"))
    value = year * 12 + month - 1 + months
    y, m = divmod(value, 12)
    return f"{y:04d}-{m + 1:02d}"


class BlsCpiIndexPoint(FrozenContract):
    reference_period: str = Field(pattern=r"^20\d{2}-(0[1-9]|1[0-2])$")
    source_value: str | None
    footnotes: tuple[str, ...] = ()

    @property
    def level(self) -> Decimal | None:
        if self.source_value in {None, "-"}:
            return None
        try:
            value = Decimal(self.source_value)
        except InvalidOperation as error:
            raise ValueError("bls_index_value_invalid") from error
        if not value.is_finite() or value <= 0:
            raise ValueError("bls_index_value_invalid")
        return value

    @model_validator(mode="after")
    def valid_level(self):
        self.level
        return self


def _rate(current: BlsCpiIndexPoint, baseline: BlsCpiIndexPoint) -> Decimal | None:
    a, b = current.level, baseline.level
    return (a / b - 1) * 100 if a is not None and b is not None else None


class BlsCpiMetricPayload(FrozenContract):
    schema_version: Literal["bls-cpi-metric.v1"] = "bls-cpi-metric.v1"
    source_id: Literal["bls-public-api"] = BLS_SOURCE_ID
    metric_id: Literal["headline_mom", "core_mom", "headline_yoy", "core_yoy"]
    series_id: str
    reference_period: str = Field(pattern=r"^20\d{2}-(0[1-9]|1[0-2])$")
    seasonal_adjustment: Literal["seasonally_adjusted", "not_seasonally_adjusted"]
    unit: Literal["percent"] = "percent"
    value_kind: Literal["index_derived_actual_and_prior"] = "index_derived_actual_and_prior"
    formula_version: Literal["100*(current_index/basis_index-1).v1"] = "100*(current_index/basis_index-1).v1"
    current_index: BlsCpiIndexPoint
    basis_index: BlsCpiIndexPoint
    prior_current_index: BlsCpiIndexPoint
    prior_basis_index: BlsCpiIndexPoint
    actual_value: Decimal | None
    prior_value: Decimal | None
    missing_reasons: tuple[str, ...]
    raw_body_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    raw_body_ref: str
    capture_id: str = Field(min_length=1)
    attempt_id: str = Field(min_length=1)
    received_at: AwareDatetime
    request_startyear: int = Field(ge=2000, le=2099)
    request_endyear: int = Field(ge=2000, le=2099)
    public_precision: Literal["unknown"] = "unknown"
    public_at: None = None
    consensus: None = None

    @model_validator(mode="after")
    def derived_identity(self):
        series, adjustment, offset = BLS_SERIES[self.metric_id]
        expected_periods = (self.reference_period, period_offset(self.reference_period, -offset), period_offset(self.reference_period, -1), period_offset(self.reference_period, -1-offset))
        points = (self.current_index, self.basis_index, self.prior_current_index, self.prior_basis_index)
        missing = tuple(name + "_missing" for name, point in zip(("actual_target", "actual_basis", "prior_target", "prior_basis"), points, strict=True) if point.level is None)
        if (self.series_id != series or self.seasonal_adjustment != adjustment
            or tuple(p.reference_period for p in points) != expected_periods
            or self.actual_value != _rate(points[0], points[1])
            or self.prior_value != _rate(points[2], points[3])
            or self.missing_reasons != missing
            or self.raw_body_ref != "raw://sha256/" + self.raw_body_hash
            or not 1 <= self.request_endyear - self.request_startyear + 1 <= 3
            or not self.request_startyear <= int(self.reference_period[:4]) <= self.request_endyear):
            raise ValueError("bls_metric_identity_invalid")
        return self

    @property
    def semantic_hash(self) -> str:
        return canonical_hash(self.model_dump(mode="json", exclude={"raw_body_hash", "raw_body_ref", "capture_id", "attempt_id", "received_at", "request_startyear", "request_endyear"}))


def _parse_bls_cpi_index(body: bytes):
    """Pure raw-body indexing; no clock, provenance or authorization is cached."""
    if len(body) > 1024 * 1024:
        raise ValueError("bls_response_too_large")
    try:
        response = json.loads(body)
        series = response["Results"]["series"]
        if response["status"] != "REQUEST_SUCCEEDED" or not isinstance(series, list):
            raise ValueError("bls_api_unsuccessful")
        ids = [row["seriesID"] for row in series]
        if len(ids) != 4 or set(ids) != {value[0] for value in BLS_SERIES.values()}:
            raise ValueError("bls_series_set_invalid")
        indexed = {}
        for row in series:
            values = {}
            for point in row["data"]:
                year, period, raw = point["year"], point["period"], point["value"]
                if period == "M13":
                    continue  # Annual averages are not monthly baselines.
                if not isinstance(year, str) or not isinstance(period, str) or not isinstance(raw, str):
                    raise ValueError("bls_index_shape_invalid")
                if not re.fullmatch(r"20\d{2}", year) or not re.fullmatch(r"M(0[1-9]|1[0-2])", period):
                    raise ValueError("bls_index_period_invalid")
                key = year + "-" + period[1:]
                if key in values:
                    raise ValueError("bls_duplicate_period")
                notes = tuple(f"{f.get('code', '')}:{f.get('text', '')}" for f in point.get("footnotes", []) if f)
                values[key] = BlsCpiIndexPoint(reference_period=key, source_value=raw.strip(), footnotes=notes)
            indexed[row["seriesID"]] = values
    except (KeyError, TypeError, json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError("bls_response_shape_invalid") from error
    return indexed, hashlib.sha256(body).hexdigest()


def _metrics_from_index(indexed, digest, *, reference_period: str, provenance: dict):
    output = []
    for metric, (sid, adjustment, offset) in BLS_SERIES.items():
        periods = (reference_period, period_offset(reference_period, -offset), period_offset(reference_period, -1), period_offset(reference_period, -1-offset))
        points = tuple(indexed[sid].get(period, BlsCpiIndexPoint(reference_period=period, source_value=None)) for period in periods)
        output.append(BlsCpiMetricPayload(
            metric_id=metric, series_id=sid, reference_period=reference_period, seasonal_adjustment=adjustment,
            current_index=points[0], basis_index=points[1], prior_current_index=points[2], prior_basis_index=points[3],
            actual_value=_rate(points[0], points[1]), prior_value=_rate(points[2], points[3]),
            missing_reasons=tuple(name + "_missing" for name, point in zip(("actual_target", "actual_basis", "prior_target", "prior_basis"), points, strict=True) if point.level is None),
            raw_body_hash=digest, raw_body_ref="raw://sha256/"+digest,
            **provenance,
        ))
    return tuple(output)


def parse_bls_cpi_metrics(body: bytes, *, reference_period: str, provenance: dict) -> tuple[BlsCpiMetricPayload, ...]:
    """Explicit month, exact four series, no older common-month substitution."""
    period_offset(reference_period, 0)
    indexed, digest = _parse_bls_cpi_index(body)
    return _metrics_from_index(indexed, digest, reference_period=reference_period, provenance=provenance)


def parse_bls_cpi_metric_batches(
    body: bytes, *, reference_periods: tuple[str, ...], provenance: dict,
) -> tuple[tuple[BlsCpiMetricPayload, ...], ...]:
    """Index these actual bytes once; rebuild each period's original provenance.

    Output order matches the requested periods, including duplicates. The index
    is private to this call and discarded at return; no cross-request cache or
    verified DTO input is accepted. Native readers still enforce PIT and hashes.
    """
    for period in reference_periods:
        period_offset(period, 0)
    if not reference_periods:
        return ()
    indexed, digest = _parse_bls_cpi_index(body)
    return tuple(_metrics_from_index(indexed, digest, reference_period=period, provenance=provenance)
                 for period in reference_periods)

"""Cleveland Fed CPI nowcast originals: model estimates, not survey consensus."""
from __future__ import annotations

import hashlib
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from .market_path import FrozenContract, canonical_hash

NOWCAST_SOURCE_ID = "cleveland-cpi-nowcast"
NOWCAST_URL = "https://www.clevelandfed.org/our-research/indicators-and-data/inflation-nowcasting"
NOWCAST_VERSION = "cleveland-cpi-nowcast.v1"
NOWCAST_METRICS = {
    "headline_mom": ("Inflation, month-over-month percent change", "CPI", "seasonally_adjusted"),
    "core_mom": ("Inflation, month-over-month percent change", "Core CPI", "seasonally_adjusted"),
    "headline_yoy": ("Inflation, year-over-year percent change", "CPI", "not_seasonally_adjusted"),
    "core_yoy": ("Inflation, year-over-year percent change", "Core CPI", "not_seasonally_adjusted"),
}
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December")


class CpiNowcastMetricPayload(FrozenContract):
    schema_version: Literal["cpi-nowcast-metric.v1"] = "cpi-nowcast-metric.v1"
    source_id: Literal["cleveland-cpi-nowcast"] = NOWCAST_SOURCE_ID
    role: Literal["nowcast"] = "nowcast"
    metric_id: Literal["headline_mom", "core_mom", "headline_yoy", "core_yoy"]
    reference_period: str = Field(pattern=r"^20\d{2}-(0[1-9]|1[0-2])$")
    reference_month_label: str
    seasonal_adjustment: Literal["seasonally_adjusted", "not_seasonally_adjusted"]
    unit: Literal["percent"] = "percent"
    value: Decimal | None
    missing_reason: Literal["already_released"] | None = None
    source_update_label: str = Field(pattern=r"^(0?[1-9]|1[0-2])/(0?[1-9]|[12]\d|3[01])$")
    # The page has MM/DD only. Do not manufacture the year or an exact time.
    source_update_precision: Literal["month_day_only"] = "month_day_only"
    source_updated_at: None = None
    public_at: None = None
    source_methodology_notices: tuple[Literal["missing_2025_10_official_cpi_replaced_by_model_nowcast"], ...] = ()
    table_caption: str
    column_name: str
    raw_body_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    raw_body_ref: str
    capture_id: str = Field(min_length=1)
    attempt_id: str = Field(min_length=1)
    received_at: AwareDatetime
    consensus: None = None
    actual: None = None

    @model_validator(mode="after")
    def valid_identity(self):
        caption, column, adjustment = NOWCAST_METRICS[self.metric_id]
        year, month = map(int, self.reference_period.split("-"))
        update_month, update_day = map(int, self.source_update_label.split("/"))
        date(2000, update_month, update_day)  # Validate MM/DD without inventing source year.
        if (self.reference_month_label != f"{MONTHS[month-1]} {year}"
            or self.table_caption != caption or self.column_name != column
            or self.seasonal_adjustment != adjustment
            or self.raw_body_ref != "raw://sha256/" + self.raw_body_hash
            or (self.value is None) != (self.missing_reason == "already_released")
            or (self.value is not None and (not self.value.is_finite()))
            or len(set(self.source_methodology_notices)) != len(self.source_methodology_notices)):
            raise ValueError("cpi_nowcast_metric_identity_invalid")
        return self

    @property
    def semantic_hash(self) -> str:
        return canonical_hash(self.model_dump(mode="json", exclude={"raw_body_hash", "raw_body_ref", "capture_id", "attempt_id", "received_at"}))


class _Tables(HTMLParser):
    """Strict small table extractor; no scripts, browser execution or network."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables = []
        self.table = self.row = self.cell = None
        self.caption = False
        self.text = []

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            if self.table is not None:
                raise ValueError("cpi_nowcast_nested_table")
            self.table = {"caption": [], "rows": []}
        elif self.table is not None:
            if tag == "caption":
                self.caption = True
            elif tag == "tr":
                self.row = []
            elif tag in {"th", "td"} and self.row is not None:
                self.cell = []

    def handle_data(self, data):
        self.text.append(data)
        if self.table is not None:
            if self.caption:
                self.table["caption"].append(data)
            if self.cell is not None:
                self.cell.append(data)

    def handle_endtag(self, tag):
        if self.table is None:
            return
        if tag == "caption":
            self.caption = False
        elif tag in {"th", "td"} and self.cell is not None:
            self.row.append(" ".join("".join(self.cell).split()))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.table["rows"].append(self.row)
            self.row = self.cell = None
        elif tag == "table":
            self.table["caption"] = " ".join("".join(self.table["caption"]).split())
            self.tables.append(self.table)
            self.table = self.row = self.cell = None
            self.caption = False


def parse_cpi_nowcast_metrics(body: bytes, *, reference_period: str, provenance: dict) -> tuple[CpiNowcastMetricPayload, ...]:
    if not re.fullmatch(r"20\d{2}-(0[1-9]|1[0-2])", reference_period):
        raise ValueError("cpi_nowcast_reference_period_invalid")
    if len(body) > 2 * 1024 * 1024:
        raise ValueError("cpi_nowcast_response_too_large")
    parser = _Tables()
    try:
        parser.feed(body.decode("utf-8"))
        parser.close()
    except (UnicodeDecodeError, ValueError) as error:
        raise ValueError("cpi_nowcast_html_invalid") from error
    plain = " ".join(" ".join(parser.text).split())
    if ("If the cell is blank, it implies that the actual data corresponding to the month" not in plain
        or "seasonally adjusted, month-over-month inflation rates" not in plain
        or "based on nonseasonally adjusted data for CPI inflation and core CPI inflation" not in plain):
        raise ValueError("cpi_nowcast_measure_definition_missing")
    notices = ()
    if "latest available nowcast for October 2025 CPI in place of the BLS October 2025 CPI release" in plain:
        notices = ("missing_2025_10_official_cpi_replaced_by_model_nowcast",)
    year, month = map(int, reference_period.split("-"))
    label = f"{MONTHS[month-1]} {year}"
    digest = hashlib.sha256(body).hexdigest()
    expected_header = ["Month", "CPI", "Core CPI", "PCE", "Core PCE", "Updated"]
    selected = {}
    for caption in {spec[0] for spec in NOWCAST_METRICS.values()}:
        matches = [table for table in parser.tables if table["caption"] == caption]
        if len(matches) != 1 or not matches[0]["rows"] or matches[0]["rows"][0] != expected_header:
            raise ValueError("cpi_nowcast_table_shape_invalid")
        rows = [row for row in matches[0]["rows"][1:] if row and row[0] == label]
        if len(rows) != 1 or len(rows[0]) != len(expected_header):
            raise ValueError("cpi_nowcast_reference_period_unavailable_or_duplicate")
        selected[caption] = dict(zip(expected_header, rows[0], strict=True))
    if len({row["Updated"] for row in selected.values()}) != 1:
        raise ValueError("cpi_nowcast_cross_table_update_mismatch")
    output = []
    for metric, (caption, column, adjustment) in NOWCAST_METRICS.items():
        row = selected[caption]
        raw = row[column]
        try:
            value = Decimal(raw) if raw else None
        except InvalidOperation as error:
            raise ValueError("cpi_nowcast_value_invalid") from error
        output.append(CpiNowcastMetricPayload(
            metric_id=metric, reference_period=reference_period, reference_month_label=label,
            seasonal_adjustment=adjustment, value=value, missing_reason="already_released" if value is None else None,
            source_update_label=row["Updated"], source_methodology_notices=notices,
            table_caption=caption, column_name=column, raw_body_hash=digest, raw_body_ref="raw://sha256/"+digest,
            **provenance,
        ))
    return tuple(output)

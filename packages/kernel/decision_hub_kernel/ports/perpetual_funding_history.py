"""Exact settled funding-rate history evidence. No account cash or mark inference."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
    DecisionSourceRefV1,
)

from .action_quote import CaptureOriginal, decode_json
from .market_path import aware_utc, canonical_hash
from .okx_perpetual_original import IDENTITIES, _millis, _signed_text

VERSION = "okx-perp-funding-history.v1"
LIMIT = 10
COVERAGE_VERSION = "okx-perp-funding-coverage.v1"
COVERAGE_LIMIT = 400
TIME_URL = "https://www.okx.com/api/v5/public/time"


def funding_history_url(inst_id: str) -> str:
    if inst_id not in IDENTITIES:
        raise ValueError("perp_funding_explicit_swap_required")
    return f"https://www.okx.com/api/v5/public/funding-rate-history?instId={inst_id}&limit={LIMIT}"


def funding_coverage_url(inst_id: str, *, before_ms: int, after_ms: int) -> str:
    """A bounded OKX page: newer than ``before`` and older than ``after``."""
    if inst_id not in IDENTITIES:
        raise ValueError("perp_funding_explicit_swap_required")
    if type(before_ms) is not int or type(after_ms) is not int or before_ms >= after_ms:
        raise ValueError("perp_funding_coverage_bounds_invalid")
    return (
        "https://www.okx.com/api/v5/public/funding-rate-history"
        f"?instId={inst_id}&before={before_ms}&after={after_ms}&limit={COVERAGE_LIMIT}"
    )


@dataclass(frozen=True)
class SettledFundingRate:
    source_row_index: int
    settlement_at: datetime
    forecast_rate: str | None
    settled_rate: str | None
    method: str
    formula_type: str
    settlement_mark: None = None
    cashflow_usdt: None = None


@dataclass(frozen=True)
class FundingHistoryOriginal:
    instrument_id: str
    capture: CaptureOriginal
    settlements: tuple[SettledFundingRate, ...]
    gaps: tuple[str, ...]
    coverage_complete: bool = False

    @property
    def body(self) -> dict:
        return dict(
            schema_version=VERSION,
            instrument_id=self.instrument_id,
            capture_id=self.capture.capture_id,
            capture_metadata_hash=canonical_hash(self.capture.payload()),
            raw_body_hash=self.capture.body_hash,
            known_at=aware_utc(self.capture.recorded_at).isoformat(),
            rate_unit="fraction_per_source_settlement_not_annualized",
            settlements=[
                dict(
                    source_row_index=row.source_row_index,
                    settlement_at=row.settlement_at.isoformat(),
                    forecast_rate=row.forecast_rate,
                    settled_rate=row.settled_rate,
                    method=row.method,
                    formula_type=row.formula_type,
                    settlement_mark=None,
                    cashflow_usdt=None,
                )
                for row in self.settlements
            ],
            coverage_complete=False,
            gaps=list(self.gaps),
        )

    @property
    def reference(self) -> DecisionSourceRefV1:
        return DecisionSourceRefV1(
            ref="perp_funding_history:" + self.capture.capture_id, hash=canonical_hash(self.body)
        )


@dataclass(frozen=True)
class FundingCoveragePage:
    before_ms: int
    after_ms: int
    capture: CaptureOriginal
    settlements: tuple[SettledFundingRate, ...]


@dataclass(frozen=True)
class FundingCoverageOriginal:
    instrument_id: str
    provider_lower: datetime
    provider_upper: datetime
    server_before: CaptureOriginal
    server_before_at: datetime
    pages: tuple[FundingCoveragePage, ...]
    server_after: CaptureOriginal
    server_after_at: datetime
    settlements: tuple[SettledFundingRate, ...]
    coverage_complete: bool
    gaps: tuple[str, ...]

    @property
    def body(self) -> dict:
        return dict(
            schema_version=COVERAGE_VERSION,
            instrument_id=self.instrument_id,
            provider_lower=self.provider_lower.isoformat(),
            provider_upper=self.provider_upper.isoformat(),
            server_before=self.server_before.payload(),
            server_before_at=self.server_before_at.isoformat(),
            pages=[
                dict(
                    before_ms=page.before_ms,
                    after_ms=page.after_ms,
                    capture=page.capture.payload(),
                    settlements=[
                        dict(
                            source_row_index=row.source_row_index,
                            settlement_at=row.settlement_at.isoformat(),
                            forecast_rate=row.forecast_rate,
                            settled_rate=row.settled_rate,
                            method=row.method,
                            formula_type=row.formula_type,
                        )
                        for row in page.settlements
                    ],
                )
                for page in self.pages
            ],
            server_after=self.server_after.payload(),
            server_after_at=self.server_after_at.isoformat(),
            terminal_empty=bool(self.pages and not self.pages[-1].settlements),
            settlements=[row.settlement_at.isoformat() for row in self.settlements],
            coverage_complete=self.coverage_complete,
            gaps=list(self.gaps),
        )

    @property
    def reference(self) -> DecisionSourceRefV1:
        digest = canonical_hash(self.body)
        return DecisionSourceRefV1(ref="perp_funding_coverage:" + digest, hash=digest)


def _coverage_capture(
    capture: CaptureOriginal,
    raw: bytes,
    *,
    inst_id: str,
    expected_url: str,
    as_of: datetime,
) -> dict:
    if (
        capture.url != expected_url
        or capture.adapter_version != COVERAGE_VERSION
        or re.fullmatch(
            rf"{re.escape(COVERAGE_VERSION)}:{re.escape(inst_id)}:[a-f0-9]{{32}}",
            capture.context_id,
        )
        is None
        or capture.status_code != 200
        or capture.error_code is not None
    ):
        raise ValueError("perp_funding_coverage_capture_invalid")
    digest = hashlib.sha256(raw).hexdigest()
    if (
        capture.body_hash != digest
        or capture.body_ref != f"raw://sha256/{digest}"
        or capture.byte_count != len(raw)
        or len(raw) > 1_000_000
        or not aware_utc(capture.started_at)
        <= aware_utc(capture.completed_at)
        <= aware_utc(capture.recorded_at)
        <= aware_utc(as_of)
    ):
        raise ValueError("perp_funding_coverage_raw_or_clock_invalid")
    body = decode_json(raw)
    if body.get("code") != "0" or type(body.get("data")) is not list:
        raise ValueError("perp_funding_coverage_response_invalid")
    return body


def decode_funding_server_time(
    capture: CaptureOriginal, raw: bytes, *, inst_id: str, as_of: datetime
) -> datetime:
    body = _coverage_capture(
        capture, raw, inst_id=inst_id, expected_url=TIME_URL, as_of=as_of
    )
    if len(body["data"]) != 1 or type(body["data"][0]) is not dict:
        raise ValueError("perp_funding_server_time_invalid")
    value = body["data"][0].get("ts")
    if type(value) is not str or re.fullmatch(r"[1-9][0-9]{0,15}", value) is None:
        raise ValueError("perp_funding_server_time_invalid")
    return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=int(value))


def decode_funding_coverage_page(
    capture: CaptureOriginal,
    raw: bytes,
    *,
    inst_id: str,
    before_ms: int,
    after_ms: int,
    as_of: datetime,
) -> FundingCoveragePage:
    body = _coverage_capture(
        capture,
        raw,
        inst_id=inst_id,
        expected_url=funding_coverage_url(
            inst_id, before_ms=before_ms, after_ms=after_ms
        ),
        as_of=as_of,
    )
    if len(body["data"]) > COVERAGE_LIMIT:
        raise ValueError("perp_funding_coverage_page_limit_invalid")
    rows: list[SettledFundingRate] = []
    previous_ms = after_ms
    for index, row in enumerate(body["data"]):
        if (
            type(row) is not dict
            or row.get("instId") != inst_id
            or row.get("instType") != "SWAP"
        ):
            raise ValueError("perp_funding_coverage_instrument_invalid")
        at = _millis(row.get("fundingTime"))
        at_ms = int(at.timestamp() * 1000)
        if not before_ms < at_ms < after_ms or at_ms >= previous_ms:
            raise ValueError("perp_funding_coverage_order_or_bounds_invalid")
        previous_ms = at_ms
        method, formula = row.get("method"), row.get("formulaType")
        if method not in {"current_period", "next_period"} or formula not in {
            "noRate",
            "withRate",
        }:
            raise ValueError("perp_funding_history_mechanism_unknown")
        realized = (
            None if row.get("realizedRate") in {None, ""} else _signed_text(row["realizedRate"])
        )
        forecast = (
            None if row.get("fundingRate") in {None, ""} else _signed_text(row["fundingRate"])
        )
        rows.append(SettledFundingRate(index, at, forecast, realized, method, formula))
    return FundingCoveragePage(before_ms, after_ms, capture, tuple(rows))


def decode_funding_history(
    capture: CaptureOriginal, raw: bytes, *, inst_id: str, as_of: datetime
) -> FundingHistoryOriginal:
    if capture.url != funding_history_url(inst_id) or capture.adapter_version != VERSION:
        raise ValueError("perp_funding_history_endpoint_or_version_mismatch")
    if (
        re.fullmatch(
            rf"{re.escape(VERSION)}:{re.escape(inst_id)}:[a-f0-9]{{32}}", capture.context_id
        )
        is None
    ):
        raise ValueError("perp_funding_history_context_mismatch")
    if capture.status_code != 200 or capture.error_code is not None:
        raise ValueError("perp_funding_history_capture_unsuccessful")
    digest = hashlib.sha256(raw).hexdigest()
    if (
        capture.body_hash != digest
        or capture.body_ref != f"raw://sha256/{digest}"
        or capture.byte_count != len(raw)
        or len(raw) > 1_000_000
    ):
        raise ValueError("perp_funding_history_raw_hash_mismatch")
    if (
        not aware_utc(capture.started_at)
        <= aware_utc(capture.completed_at)
        <= aware_utc(capture.recorded_at)
        <= aware_utc(as_of)
    ):
        raise ValueError("perp_funding_history_clock_or_cutoff_invalid")
    response = decode_json(raw)
    if (
        response.get("code") != "0"
        or not isinstance(response.get("data"), list)
        or len(response["data"]) > LIMIT
    ):
        raise ValueError("perp_funding_history_response_invalid")
    rows, times = [], set()
    gaps = [
        "settlement_mark_unavailable",
        "account_position_at_settlement_unproven",
        "settlement_coverage_unproven",
    ]
    for index, row in enumerate(response["data"]):
        if (
            not isinstance(row, dict)
            or row.get("instId") != inst_id
            or row.get("instType") != "SWAP"
        ):
            raise ValueError("perp_funding_history_instrument_mismatch")
        at = _millis(row.get("fundingTime"))
        # Seconds misread as milliseconds fall in1970; never accept that as history.
        if at.year < 2000 or at > aware_utc(capture.completed_at) or at in times:
            raise ValueError("perp_funding_history_timestamp_or_duplicate_invalid")
        times.add(at)
        method, formula = row.get("method"), row.get("formulaType")
        if method not in {"current_period", "next_period"} or formula not in {"noRate", "withRate"}:
            raise ValueError("perp_funding_history_mechanism_unknown")
        realized = (
            None if row.get("realizedRate") in {None, ""} else _signed_text(row["realizedRate"])
        )
        forecast = (
            None if row.get("fundingRate") in {None, ""} else _signed_text(row["fundingRate"])
        )
        if realized is None and "realized_rate_unavailable" not in gaps:
            gaps.append("realized_rate_unavailable")
        rows.append(SettledFundingRate(index, at, forecast, realized, method, formula))
    if not rows:
        gaps.append("settlement_history_empty")
    return FundingHistoryOriginal(
        "okx:" + inst_id,
        capture,
        tuple(sorted(rows, key=lambda row: row.settlement_at)),
        tuple(gaps),
    )

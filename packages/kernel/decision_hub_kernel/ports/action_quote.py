"""Artifact-only strict quote contract and parser; never infer a fill."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qs, urlsplit

from packages.kernel.decision_hub_kernel.ports.market_path import (
    MarketPathScope,
    aware_utc,
    canonical_hash,
    spot_scope,
)

ADAPTER_VERSION = "okx-action-quote.v1"
MAX_QUOTE_AGE = timedelta(seconds=10)
WAIT_FOR_QUOTE = timedelta(seconds=60)


def text_at(value: datetime) -> str:
    return aware_utc(value).isoformat(timespec="microseconds")


def require_scope(scope: MarketPathScope) -> None:
    if scope != spot_scope(scope.instrument):
        raise ValueError("action_quote_scope_unsupported")


def capture_context(scope: MarketPathScope, identity: str) -> str:
    require_scope(scope)
    if not re.fullmatch(r"[a-f0-9]{32}", identity):
        raise ValueError("action_quote_context_invalid")
    return f"action-quote.v1:{scope.instrument}:{identity}"


def exact_price(value: object) -> Decimal:
    if (
        not isinstance(value, str)
        or len(value) > 80
        or not re.fullmatch(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?", value)
    ):
        raise ValueError("action_quote_original_decimal_string_required")
    price = Decimal(value)
    if not price.is_finite() or price <= 0:
        raise ValueError("action_quote_price_not_positive")
    return price


def normalized_price(value: str) -> str:
    # String manipulation avoids Decimal.normalize() rounding to ambient precision.
    return value.rstrip("0").rstrip(".") if "." in value else value


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("action_quote_duplicate_json_key")
        result[key] = value
    return result


def decode_json(raw: bytes | str) -> dict[str, Any]:
    result = json.loads(raw, object_pairs_hook=_unique_object)
    if not isinstance(result, dict):
        raise ValueError("action_quote_json_object_required")
    return result


@dataclass(frozen=True)
class CaptureOriginal:
    capture_id: str
    context_id: str
    url: str
    status_code: int | None
    body_ref: str | None
    body_hash: str | None
    byte_count: int
    adapter_version: str
    error_code: str | None
    started_at: datetime
    completed_at: datetime
    recorded_at: datetime

    def payload(self) -> dict[str, Any]:
        value = asdict(self)
        for key in ("started_at", "completed_at", "recorded_at"):
            value[key] = text_at(value[key])
        return value


@dataclass(frozen=True)
class ParsedQuote:
    scope: MarketPathScope
    capture: CaptureOriginal
    observed_at: datetime
    bid_text: str
    ask_text: str
    clock_receipt_json: str | None = None

    @property
    def clock_receipt(self) -> dict[str, Any] | None:
        return decode_json(self.clock_receipt_json) if self.clock_receipt_json else None

    def local_interval(self, *, clock_policy: str = "legacy") -> tuple[datetime, datetime]:
        if clock_policy == "legacy":
            if not timedelta(0) <= self.capture.completed_at - self.observed_at <= MAX_QUOTE_AGE:
                raise ValueError("action_quote_legacy_clock_incompatible")
            return self.observed_at, self.observed_at
        clock = self.clock_receipt
        if clock_policy != "bracket_v1" or clock is None:
            raise ValueError("action_quote_clock_policy_missing")
        lower = aware_utc(datetime.fromisoformat(clock["observed_local_lower"]))
        upper = aware_utc(datetime.fromisoformat(clock["observed_local_upper"]))
        return lower, upper

    @property
    def bid(self) -> Decimal:
        return Decimal(self.bid_text)

    @property
    def ask(self) -> Decimal:
        return Decimal(self.ask_text)

    @property
    def semantic_hash(self) -> str:
        return canonical_hash(
            {
                "scope_id": self.scope.scope_id,
                "observed_at": text_at(self.observed_at),
                "bid": normalized_price(self.bid_text),
                "ask": normalized_price(self.ask_text),
            }
        )

    def payload(self, *, admitted_at: datetime) -> dict[str, Any]:
        admitted = aware_utc(admitted_at)
        if admitted < self.capture.recorded_at:
            raise ValueError("action_quote_admission_before_capture")
        payload: dict[str, Any] = {
            "schema_version": "action-quote-original.v1",
            "scope": self.scope.model_dump(mode="json"),
            "price_basis": "bid_ask",
            "bid_text": self.bid_text,
            "ask_text": self.ask_text,
            "observed_at": text_at(self.observed_at),
            "received_at": text_at(self.capture.completed_at),
            "admitted_at": text_at(admitted),
            "semantic_hash": self.semantic_hash,
            "capture": self.capture.payload(),
        }
        clock = self.clock_receipt
        if clock is not None:
            if admitted < aware_utc(datetime.fromisoformat(clock["measurement_recorded_at"])):
                raise ValueError("action_quote_admission_before_clock_measurement")
            payload["schema_version"] = "action-quote-original.v2"
            payload["clock_receipt"] = clock
        return payload


def parse_okx_quote(
    scope: MarketPathScope,
    capture: CaptureOriginal,
    raw: bytes,
    *,
    clock_receipt_json: str | None = None,
) -> ParsedQuote:
    require_scope(scope)
    parts = urlsplit(capture.url)
    query = parse_qs(parts.query, keep_blank_values=True)
    if (
        parts.scheme != "https"
        or parts.hostname != "www.okx.com"
        or parts.port not in (None, 443)
        or parts.username
        or parts.password
        or parts.fragment
        or parts.path != "/api/v5/market/ticker"
        or query != {"instId": [scope.instrument]}
    ):
        raise ValueError("action_quote_endpoint_mismatch")
    context_version = "v2" if clock_receipt_json else "v1"
    if (
        capture.status_code != 200
        or capture.error_code is not None
        or capture.adapter_version
        != ("okx-action-quote.v2" if clock_receipt_json else ADAPTER_VERSION)
        or not re.fullmatch(
            rf"action-quote\.{context_version}:{re.escape(scope.instrument)}:[a-f0-9]{{32}}",
            capture.context_id,
        )
    ):
        raise ValueError("action_quote_capture_not_eligible")
    digest = hashlib.sha256(raw).hexdigest()
    if (
        capture.body_hash != digest
        or capture.body_ref != f"raw://sha256/{digest}"
        or capture.byte_count != len(raw)
    ):
        raise ValueError("action_quote_raw_hash_mismatch")
    started = aware_utc(capture.started_at)
    received = aware_utc(capture.completed_at)
    recorded = aware_utc(capture.recorded_at)
    if not started <= received <= recorded:
        raise ValueError("action_quote_capture_clock_order")
    response = decode_json(raw)
    data = response.get("data")
    if response.get("code") != "0":
        raise ValueError("action_quote_provider_rejected")
    if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
        raise ValueError("action_quote_unique_ticker_required")
    row = data[0]
    if row.get("instId") != scope.instrument or row.get("instType") != "SPOT":
        raise ValueError("action_quote_ticker_scope_mismatch")
    raw_ts = row.get("ts")
    if not isinstance(raw_ts, str) or not re.fullmatch(r"[1-9][0-9]{0,15}", raw_ts):
        raise ValueError("action_quote_provider_timestamp_required")
    try:
        observed = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=int(raw_ts))
    except OverflowError as error:
        raise ValueError("action_quote_provider_timestamp_invalid") from error
    if clock_receipt_json is None and not timedelta(0) <= received - observed <= MAX_QUOTE_AGE:
        raise ValueError("action_quote_future_or_stale")
    bid, ask = exact_price(row.get("bidPx")), exact_price(row.get("askPx"))
    if bid > ask:
        raise ValueError("action_quote_crossed_book")
    parsed = ParsedQuote(scope, capture, observed, row["bidPx"], row["askPx"], clock_receipt_json)
    if clock_receipt_json:
        lower, upper = parsed.local_interval(clock_policy="bracket_v1")
        if not lower <= upper <= received or received - lower > MAX_QUOTE_AGE:
            raise ValueError("action_quote_future_or_stale")
    return parsed

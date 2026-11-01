"""Nonreusable same-bracket clock evidence for perpetual top books.

Reuse the existing raw-original and monotonic/wall-clock proof primitives.
The books endpoint, 5-second age and adapter are explicitly versioned here;
the old spot ticker policy and its originals remain unchanged.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta

from .action_quote import CaptureOriginal, text_at
from .action_quote_clock import CLOCK_POLICY, _monotonic, _raw, _time, _us
from .market_path import canonical_hash
from .perpetual_quote import VERSION

TIME_URL = "https://www.okx.com/api/v5/public/time"
MEASUREMENT_URL = "internal://perpetual-book-clock/measurement"
MEASUREMENT_ADAPTER = "okx-perp-book-clock.v1"
POLICY = {
    **CLOCK_POLICY,
    "schema_version": "okx-perp-book-bracket-clock.v1",
    "endpoints": ["/api/v5/public/time", "/api/v5/market/books"],
    "quote_max_age_seconds": 5,
}
POLICY_HASH = canonical_hash(POLICY)


def _sample(capture: CaptureOriginal, raw: bytes, measurement: dict):
    body = _raw(capture, raw)
    if (
        capture.url != TIME_URL
        or capture.adapter_version != VERSION
        or capture.status_code != 200
        or capture.error_code is not None
        or body.get("code") != "0"
        or type(body.get("data")) is not list
        or len(body["data"]) != 1
        or type(body["data"][0]) is not dict
    ):
        raise ValueError("perp_clock_time_original_invalid")
    ts = body["data"][0].get("ts")
    if type(ts) is not str or re.fullmatch(r"[1-9][0-9]{0,15}", ts) is None:
        raise ValueError("perp_clock_provider_timestamp_required")
    provider = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=int(ts))
    _monotonic(measurement, max_ns=POLICY["max_time_rtt_ns"])
    if not (
        _time(measurement["started_at"])
        <= capture.started_at
        <= capture.completed_at
        <= _time(measurement["completed_at"])
    ):
        raise ValueError("perp_clock_time_measurement_not_bound")
    return (
        _us(provider - capture.completed_at) - POLICY["timestamp_quantization_us"],
        _us(provider - capture.started_at) + POLICY["timestamp_quantization_us"],
        provider,
    )


def reconstruct_book_clock(
    *,
    quote: CaptureOriginal,
    quote_raw: bytes,
    before: CaptureOriginal,
    before_raw: bytes,
    after: CaptureOriginal,
    after_raw: bytes,
    measurement: CaptureOriginal,
    measurement_raw: bytes,
) -> str:
    doc = _raw(measurement, measurement_raw)
    if (
        measurement.url != MEASUREMENT_URL
        or measurement.adapter_version != MEASUREMENT_ADAPTER
        or measurement.status_code is not None
        or measurement.error_code is not None
        or len({item.context_id for item in (quote, before, after, measurement)}) != 1
        or quote.adapter_version != VERSION
        or set(doc) != {"schema_version", "kind", "captures", "before", "after", "bracket"}
        or doc["schema_version"] != "perpetual-book-clock-measurement.v1"
        or doc["kind"] != "internal_clock_measurement"
        or doc["captures"] != [item.payload() for item in (before, quote, after)]
    ):
        raise ValueError("perp_clock_measurement_binding_invalid")
    if not before.completed_at <= quote.started_at <= quote.completed_at <= after.started_at:
        raise ValueError("perp_clock_bracket_order")
    if not after.completed_at <= measurement.started_at <= measurement.completed_at:
        raise ValueError("perp_clock_measurement_before_bracket")
    _monotonic(doc["bracket"], max_ns=POLICY["max_bracket_ns"])
    if not (
        _time(doc["bracket"]["started_at"]) <= before.started_at
        and after.completed_at <= _time(doc["bracket"]["completed_at"])
    ):
        raise ValueError("perp_clock_bracket_not_bound")
    lo1, hi1, server_before = _sample(before, before_raw, doc["before"])
    lo2, hi2, server_after = _sample(after, after_raw, doc["after"])
    if server_after < server_before:
        raise ValueError("perp_clock_provider_regressed")
    drift = POLICY["assumed_max_bracket_clock_drift_us"]
    lower, upper = min(lo1, lo2) - drift, max(hi1, hi2) + drift
    if (
        max(abs(lower), abs(upper)) > POLICY["max_abs_offset_us"]
        or upper - lower > POLICY["max_interval_width_us"]
    ):
        raise ValueError("perp_clock_uncertain")
    body = _raw(quote, quote_raw)
    ts = body.get("data", [{}])[0].get("ts")
    if type(ts) is not str or re.fullmatch(r"[1-9][0-9]{0,15}", ts) is None:
        raise ValueError("perp_clock_book_timestamp_required")
    observed = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=int(ts))
    if observed > server_after + timedelta(milliseconds=1):
        raise ValueError("perp_clock_book_after_bracket")
    local_lower = observed - timedelta(microseconds=upper)
    local_upper = min(observed - timedelta(microseconds=lower), quote.completed_at)
    if local_lower > local_upper:
        raise ValueError("perp_clock_interval_invalid")
    receipt = dict(
        schema_version="perpetual-book-clock.v1",
        policy=POLICY,
        policy_hash=POLICY_HASH,
        quote_capture_ref=quote.capture_id,
        quote_capture_hash=canonical_hash(quote.payload()),
        measurement_capture_ref=measurement.capture_id,
        measurement_capture_hash=canonical_hash(measurement.payload()),
        measurement_recorded_at=text_at(measurement.recorded_at),
        before={"capture": before.payload(), "measurement": doc["before"]},
        after={"capture": after.payload(), "measurement": doc["after"]},
        bracket=doc["bracket"],
        offset_lower_us=lower,
        offset_upper_us=upper,
        provider_observed_at=text_at(observed),
        observed_local_lower=text_at(local_lower),
        observed_local_upper=text_at(local_upper),
        established_at=text_at(after.completed_at),
        valid_until=text_at(local_lower + timedelta(seconds=5)),
    )
    digest = canonical_hash(receipt)
    return json.dumps(
        {**receipt, "id": "perpclock_" + digest, "payload_hash": digest},
        sort_keys=True,
        separators=(",", ":"),
    )

"""Same-bracket clock evidence. These bounds assume bounded endpoint clock drift."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from packages.kernel.decision_hub_kernel.ports.action_quote import (
    CaptureOriginal,
    decode_json,
    text_at,
)
from packages.kernel.decision_hub_kernel.ports.market_path import aware_utc, canonical_hash

CLOCK_POLICY_REF = "okx-bracket-clock.v1"
CLOCK_POLICY: dict[str, Any] = {
    "schema_version": CLOCK_POLICY_REF,
    "origin": "https://www.okx.com",
    "endpoints": ["/api/v5/public/time", "/api/v5/market/ticker"],
    "assumption": "same_origin_endpoints_share_clock_with_bounded_bracket_drift",
    "max_time_rtt_ns": 2_000_000_000,
    "max_bracket_ns": 14_000_000_000,
    "max_wall_monotonic_discrepancy_us": 25_000,
    "assumed_max_bracket_clock_drift_us": 25_000,
    "timestamp_quantization_us": 1_000,
    "max_abs_offset_us": 30_000_000,
    "max_interval_width_us": 2_100_000,
    "reusable": False,
    "offset_aggregation": "hull_of_before_after_bounds",
}
CLOCK_POLICY_HASH = canonical_hash(CLOCK_POLICY)
MEASUREMENT_ADAPTER = "okx-action-quote-clock.v1"
MEASUREMENT_URL = "internal://action-quote-clock/measurement"


def _us(value: timedelta) -> int:
    return (value.days * 86400 + value.seconds) * 1_000_000 + value.microseconds


def _time(value: str) -> datetime:
    return aware_utc(datetime.fromisoformat(value))


def _raw(capture: CaptureOriginal, raw: bytes) -> dict[str, Any]:
    digest = hashlib.sha256(raw).hexdigest()
    if (capture.body_hash, capture.body_ref, capture.byte_count) != (
        digest,
        f"raw://sha256/{digest}",
        len(raw),
    ):
        raise ValueError("action_quote_clock_original_mismatch")
    if not capture.started_at <= capture.completed_at <= capture.recorded_at:
        raise ValueError("action_quote_clock_capture_order")
    return decode_json(raw)


def _monotonic(sample: dict[str, Any], *, max_ns: int) -> None:
    if set(sample) != {"started_at", "completed_at", "elapsed_monotonic_ns"}:
        raise ValueError("action_quote_clock_measurement_shape")
    elapsed = sample["elapsed_monotonic_ns"]
    if type(elapsed) is not int or not 0 <= elapsed <= max_ns:
        raise ValueError("action_quote_clock_uncertain")
    wall = _us(_time(sample["completed_at"]) - _time(sample["started_at"]))
    if abs(wall * 1000 - elapsed) > 25_000_000:
        raise ValueError("action_quote_clock_wall_step")


def _sample(
    capture: CaptureOriginal, raw: bytes, measurement: dict[str, Any]
) -> tuple[int, int, datetime]:
    value = _raw(capture, raw)
    if (
        capture.url != "https://www.okx.com/api/v5/public/time"
        or capture.status_code != 200
        or capture.error_code is not None
        or capture.adapter_version != "okx-action-quote.v2"
        or value.get("code") != "0"
        or not isinstance(value.get("data"), list)
        or len(value["data"]) != 1
    ):
        raise ValueError("action_quote_clock_endpoint_or_response_invalid")
    timestamp = value["data"][0].get("ts")
    if not isinstance(timestamp, str) or not re.fullmatch(r"[1-9][0-9]{0,15}", timestamp):
        raise ValueError("action_quote_clock_provider_timestamp_required")
    provider = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=int(timestamp))
    _monotonic(measurement, max_ns=CLOCK_POLICY["max_time_rtt_ns"])
    if not (
        _time(measurement["started_at"])
        <= capture.started_at
        <= capture.completed_at
        <= _time(measurement["completed_at"])
    ):
        raise ValueError("action_quote_clock_measurement_not_bound")
    lo = _us(provider - capture.completed_at) - 1000
    hi = _us(provider - capture.started_at) + 1000
    return lo, hi, provider


def reconstruct_clock_receipt(
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
    """Pure verification; repository supplies persisted identities and original bytes."""
    doc = _raw(measurement, measurement_raw)
    if (
        measurement.url != MEASUREMENT_URL
        or measurement.adapter_version != MEASUREMENT_ADAPTER
        or measurement.status_code is not None
        or measurement.error_code is not None
        or len({x.context_id for x in (quote, before, after, measurement)}) != 1
        or not quote.context_id.startswith("action-quote.v2:")
        or quote.adapter_version != "okx-action-quote.v2"
        or set(doc) != {"schema_version", "kind", "captures", "before", "after", "bracket"}
        or doc["schema_version"] != "action-quote-clock-measurement.v1"
        or doc["kind"] != "internal_clock_measurement"
        or doc["captures"] != [x.payload() for x in (before, quote, after)]
    ):
        raise ValueError("action_quote_clock_measurement_binding_invalid")
    if not before.completed_at <= quote.started_at <= quote.completed_at <= after.started_at:
        raise ValueError("action_quote_clock_bracket_order")
    if not after.completed_at <= measurement.started_at <= measurement.completed_at:
        raise ValueError("action_quote_clock_measurement_before_bracket")
    _monotonic(doc["bracket"], max_ns=CLOCK_POLICY["max_bracket_ns"])
    if not (
        _time(doc["bracket"]["started_at"]) <= before.started_at
        and after.completed_at <= _time(doc["bracket"]["completed_at"])
    ):
        raise ValueError("action_quote_clock_bracket_not_bound")
    lo1, hi1, server_before = _sample(before, before_raw, doc["before"])
    lo2, hi2, server_after = _sample(after, after_raw, doc["after"])
    if server_after < server_before:
        raise ValueError("action_quote_clock_provider_regressed")
    lower, upper = min(lo1, lo2) - 25_000, max(hi1, hi2) + 25_000
    if max(abs(lower), abs(upper)) > 30_000_000 or upper - lower > 2_100_000:
        raise ValueError("action_quote_clock_uncertain")
    raw = _raw(quote, quote_raw)
    raw_ts = raw.get("data", [{}])[0].get("ts")
    if not isinstance(raw_ts, str) or not re.fullmatch(r"[1-9][0-9]{0,15}", raw_ts):
        raise ValueError("action_quote_provider_timestamp_required")
    observed = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=int(raw_ts))
    if observed > server_after + timedelta(milliseconds=1):
        raise ValueError("action_quote_clock_ticker_after_bracket")
    local_lower = observed - timedelta(microseconds=upper)
    local_upper = min(observed - timedelta(microseconds=lower), quote.completed_at)
    if local_lower > local_upper:
        raise ValueError("action_quote_clock_interval_invalid")
    body = {
        "schema_version": "action-quote-clock.v1",
        "policy_ref": CLOCK_POLICY_REF,
        "policy_hash": CLOCK_POLICY_HASH,
        "quote_capture_ref": quote.capture_id,
        "quote_capture_hash": canonical_hash(quote.payload()),
        "measurement_capture_ref": measurement.capture_id,
        "measurement_capture_hash": canonical_hash(measurement.payload()),
        "measurement_recorded_at": text_at(measurement.recorded_at),
        "before": {"capture": before.payload(), "measurement": doc["before"]},
        "after": {"capture": after.payload(), "measurement": doc["after"]},
        "bracket": doc["bracket"],
        "offset_lower_us": lower,
        "offset_upper_us": upper,
        "provider_observed_at": text_at(observed),
        "observed_local_lower": text_at(local_lower),
        "observed_local_upper": text_at(local_upper),
        "established_at": text_at(after.completed_at),
        "valid_until": text_at(local_lower + timedelta(seconds=10)),
    }
    digest = canonical_hash(body)
    return json.dumps(
        body | {"id": "clock_" + digest, "payload_hash": digest},
        sort_keys=True,
        separators=(",", ":"),
    )

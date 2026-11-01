"""Confirmed OKX 1H perpetual closes for the direction base rate; no orders, no model."""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlencode, urlsplit

from .action_quote import CaptureOriginal, decode_json
from .perpetual_close import INSTRUMENTS, _decimal

VERSION = "okx-perp-hourly.v1"
HOUR_MS = 3_600_000
PAGE_LIMIT = 100
MAX_PAGES = 25
WINDOW_DAYS = 90


def public_url(inst_id: str, after_ms: int) -> str:
    if (
        inst_id not in INSTRUMENTS
        or type(after_ms) is not int
        or after_ms <= 0
        or after_ms % HOUR_MS
    ):
        raise ValueError("perp_hourly_request_invalid")
    return "https://www.okx.com/api/v5/market/history-candles?" + urlencode(
        {"instId": inst_id, "bar": "1H", "after": str(after_ms), "limit": str(PAGE_LIMIT)}
    )


def request_cursor(url: str, inst_id: str) -> int:
    parsed = urlsplit(url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "www.okx.com"
        or parsed.path != "/api/v5/market/history-candles"
        or parsed.fragment
        or set(query) != {"instId", "bar", "after", "limit"}
        or query["instId"] != [inst_id]
        or query["bar"] != ["1H"]
        or query["limit"] != [str(PAGE_LIMIT)]
        or len(query["after"]) != 1
        or not re.fullmatch(r"[1-9][0-9]*", query["after"][0])
    ):
        raise ValueError("perp_hourly_request_invalid")
    after = int(query["after"][0])
    public_url(inst_id, after)
    return after


def parse_hourly_page(
    capture: CaptureOriginal, raw: bytes, *, inst_id: str, as_of: datetime, sequence: int
) -> tuple[int, tuple[tuple[datetime, str], ...]]:
    """Return (after_ms, ((close_at, close_price), ...)) newest first, all confirmed."""
    if (
        inst_id not in INSTRUMENTS
        or capture.adapter_version != VERSION
        or re.fullmatch(
            rf"{re.escape(VERSION)}:{re.escape(inst_id)}:[a-f0-9]{{32}}:{sequence}",
            capture.context_id,
        )
        is None
    ):
        raise ValueError("perp_hourly_capture_identity_invalid")
    after = request_cursor(capture.url, inst_id)
    if not capture.started_at <= capture.completed_at <= capture.recorded_at <= as_of:
        raise ValueError("perp_hourly_capture_clock_invalid")
    if (
        capture.status_code != 200
        or capture.error_code is not None
        or capture.body_hash != hashlib.sha256(raw).hexdigest()
        or capture.body_ref != "raw://sha256/" + str(capture.body_hash)
        or capture.byte_count != len(raw)
    ):
        raise ValueError("perp_hourly_capture_invalid")
    envelope = decode_json(raw)
    data = envelope.get("data")
    if envelope.get("code") != "0" or not isinstance(data, list) or len(data) > PAGE_LIMIT:
        raise ValueError("perp_hourly_response_invalid")
    rows = []
    previous = after
    for row in data:
        if (
            not isinstance(row, list)
            or len(row) != 9
            or not all(isinstance(item, str) for item in row)
            or not re.fullmatch(r"[1-9][0-9]*", row[0])
            or row[8] != "1"
        ):
            raise ValueError("perp_hourly_confirmed_row_required")
        stamp = int(row[0])
        if stamp % HOUR_MS or stamp >= previous:
            raise ValueError("perp_hourly_cursor_order_invalid")
        previous = stamp
        close_at = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=stamp + HOUR_MS)
        if close_at > capture.completed_at:
            raise ValueError("perp_hourly_future_bar")
        opened, high, low, closed = (_decimal(item, positive=True) for item in row[1:5])
        if not low <= min(opened, closed) <= max(opened, closed) <= high:
            raise ValueError("perp_hourly_ohlc_invalid")
        rows.append((close_at, row[4]))
    return after, tuple(rows)

"""Confirmed swap trade closes and the approved, reproducible target calculation.

OKX history-candles timestamps are bar OPEN times. Column 4 is close; no
derivative volume column is projected into a spot volume unit here.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from decimal import (
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    localcontext,
)
from urllib.parse import parse_qs, urlencode, urlsplit

from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
    DecisionSourceRefV1,
)

from .action_quote import CaptureOriginal, decode_json
from .exact_decimal_text import number
from .market_path import aware_utc, canonical_hash

VERSION = "okx-perp-close.v1"
INSTRUMENTS = ("BTC-USDT-SWAP", "ETH-USDT-SWAP")
MINUTE_MS = 60000
PRECISION = 50
MAX_PAGES = 6
POLICY = {
    "version": "perp-close-target-method.v1",
    "spec": "02:5.2",
    "price": "OKX confirmed trade 1m close; open_ms+60000=close_ms; USDT/base",
    "count": 1441,
    "returns": 1440,
    "sigma24_squared": "sum(ln(close[i]/close[i-1])**2)",
    "theta": "0.25*sqrt(sigma24_squared)*sqrt(horizon_seconds/86400)",
    "horizons_seconds": [14400, 86400, 259200],
    "decimal_precision": PRECISION,
    "decimal_Emin": -999999,
    "decimal_Emax": 999999,
    "decimal_clamp": 0,
    "decimal_traps": ["InvalidOperation", "DivisionByZero", "Overflow"],
    "rounding": "ROUND_HALF_EVEN",
    "published_fraction_quantum": "0.000000000000000001",
    "zero_threshold": "unavailable",
    "missing": "unavailable; no pad or older fallback",
    "paging": "after exclusive older open_ms; limit300; at most6; at most1 final newest refresh",
}


def json_body(value: object) -> dict:
    def encode(item):
        if isinstance(item, datetime):
            return aware_utc(item).isoformat(timespec="microseconds").replace("+00:00", "Z")
        raise TypeError("perp_close_non_json_value")

    return json.loads(json.dumps(value, default=encode))


def minute_end(value: datetime) -> datetime:
    return aware_utc(value).replace(second=0, microsecond=0)


def millis(value: datetime) -> int:
    value = aware_utc(value)
    delta = value - datetime(1970, 1, 1, tzinfo=UTC)
    return (delta.days * 86400 + delta.seconds) * 1000 + delta.microseconds // 1000


def public_url(inst_id: str, after_ms: int) -> str:
    if (
        inst_id not in INSTRUMENTS
        or type(after_ms) is not int
        or after_ms <= 0
        or after_ms % MINUTE_MS
    ):
        raise ValueError("perp_close_request_invalid")
    return "https://www.okx.com/api/v5/market/history-candles?" + urlencode(
        {
            "instId": inst_id,
            "bar": "1m",
            "after": str(after_ms),
            "limit": "300",
        }
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
        or query["bar"] != ["1m"]
        or query["limit"] != ["300"]
        or len(query["after"]) != 1
        or not re.fullmatch(r"[1-9][0-9]*", query["after"][0])
    ):
        raise ValueError("perp_close_request_invalid")
    after = int(query["after"][0])
    public_url(inst_id, after)
    return after


def _decimal(text: object, *, positive: bool) -> Decimal:
    if not isinstance(text, str) or not re.fullmatch(
        r"(?:0|[1-9][0-9]{0,31})(?:\.[0-9]{1,18})?", text
    ):
        raise ValueError("perp_close_decimal_invalid")
    value = Decimal(text)
    if value < 0 or (positive and value == 0):
        raise ValueError("perp_close_decimal_invalid")
    return value


@dataclass(frozen=True)
class PerpetualClose:
    open_at: datetime
    close_at: datetime
    price: str
    capture_id: str
    capture_hash: str
    capture_metadata_hash: str
    body_ref: str
    row_index: int
    received_at: datetime
    recorded_at: datetime


@dataclass(frozen=True)
class PerpetualClosePage:
    capture: CaptureOriginal
    acquisition_id: str
    kind: str
    sequence: int
    after_ms: int
    closes: tuple[PerpetualClose, ...]


def parse_page(
    capture: CaptureOriginal, raw: bytes, *, inst_id: str, as_of: datetime, selected_rows
) -> PerpetualClosePage:
    cutoff = aware_utc(as_of)
    match = re.fullmatch(
        rf"{re.escape(VERSION)}:{re.escape(inst_id)}:([a-f0-9]{{32}}):(base|refresh):([0-5])",
        capture.context_id,
    )
    if match is None or inst_id not in INSTRUMENTS or capture.adapter_version != VERSION:
        raise ValueError("perp_close_capture_identity_invalid")
    after = request_cursor(capture.url, inst_id)
    if not capture.started_at <= capture.completed_at <= capture.recorded_at:
        raise ValueError("perp_close_capture_clock_invalid")
    if aware_utc(capture.recorded_at) > cutoff:
        raise ValueError("perp_close_not_known_at_cutoff")
    if (
        capture.status_code != 200
        or capture.error_code is not None
        or capture.body_hash != hashlib.sha256(raw).hexdigest()
        or capture.body_ref != "raw://sha256/" + str(capture.body_hash)
        or capture.byte_count != len(raw)
    ):
        raise ValueError("perp_close_capture_invalid")
    envelope = decode_json(raw)
    data = envelope.get("data")
    if envelope.get("code") != "0" or not isinstance(data, list) or not 1 <= len(data) <= 300:
        raise ValueError("perp_close_response_invalid")
    # Reuse the existing bounded pure parse helper AFTER fresh bytes/hash/JSON checks.
    rows = selected_rows(raw, capture.body_hash, tuple(range(len(data))))
    closes = []
    previous = after
    metadata_hash = canonical_hash(capture.payload())
    for index in range(len(data)):
        row = rows[index]
        if row != data[index]:
            raise ValueError("perp_close_parse_cache_mismatch")
        if (
            not isinstance(row, list)
            or len(row) != 9
            or not all(isinstance(item, str) for item in row)
            or not re.fullmatch(r"[1-9][0-9]*", row[0])
            or row[8] != "1"
        ):
            raise ValueError("perp_close_confirmed_row_required")
        stamp = int(row[0])
        if stamp % MINUTE_MS or stamp >= previous:
            raise ValueError("perp_close_cursor_order_invalid")
        previous = stamp
        open_at = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=stamp)
        close_at = open_at + timedelta(minutes=1)
        if close_at > aware_utc(capture.completed_at):
            raise ValueError("perp_close_future_bar")
        opened, high, low, closed = (_decimal(item, positive=True) for item in row[1:5])
        if not low <= min(opened, closed) <= max(opened, closed) <= high:
            raise ValueError("perp_close_ohlc_invalid")
        for volume in row[5:8]:
            _decimal(volume, positive=False)
        closes.append(
            PerpetualClose(
                open_at,
                close_at,
                row[4],
                capture.capture_id,
                capture.body_hash,
                metadata_hash,
                capture.body_ref,
                index,
                capture.completed_at,
                capture.recorded_at,
            )
        )
    return PerpetualClosePage(capture, match[1], match[2], int(match[3]), after, tuple(closes))


@dataclass(frozen=True)
class PerpetualThreshold:
    horizon: str
    duration_seconds: int
    raw_fraction: str
    fraction: str


@dataclass(frozen=True)
class PerpetualCloseWindow:
    instrument_ref_ref: str
    instrument_ref_hash: str
    inst_id: str
    as_of: datetime
    requested_end_at: datetime
    end_at: datetime
    closes: tuple[PerpetualClose, ...]
    log_returns: tuple[str, ...]
    sigma24_squared: str
    sigma24: str
    thresholds: tuple[PerpetualThreshold, ...]
    capture_ids: tuple[str, ...]
    method_hash: str
    software_hash: str
    window_hash: str

    @property
    def instrument_ref(self) -> DecisionSourceRefV1:
        return DecisionSourceRefV1(ref=self.instrument_ref_ref, hash=self.instrument_ref_hash)

    @property
    def source_ref(self) -> DecisionSourceRefV1:
        return DecisionSourceRefV1(
            ref="perp_close_window:" + self.window_hash, hash=self.window_hash
        )

    @property
    def method_ref(self) -> DecisionSourceRefV1:
        return DecisionSourceRefV1(
            ref="perp_close_method:" + self.method_hash, hash=self.method_hash
        )


def assemble_window(
    *,
    instrument_ref: DecisionSourceRefV1,
    inst_id: str,
    pages: tuple[PerpetualClosePage, ...],
    as_of: datetime,
    software_hash: str,
) -> PerpetualCloseWindow:
    if not 5 <= len(pages) <= MAX_PAGES:
        raise ValueError("perp_close_page_count_invalid")
    end = minute_end(as_of)
    requested_end = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=pages[0].after_ms)
    acquisition = pages[0].acquisition_id
    previous = None
    refresh_seen = False
    by_open = {}
    for index, page in enumerate(pages):
        if page.sequence != index or page.acquisition_id != acquisition:
            raise ValueError("perp_close_page_identity_invalid")
        if previous and previous.capture.recorded_at > page.capture.started_at:
            raise ValueError("perp_close_capture_order_invalid")
        if page.kind == "refresh":
            if refresh_seen or index != len(pages) - 1 or index < 5 or requested_end >= end:
                raise ValueError("perp_close_refresh_invalid")
            refresh_seen = True
            if page.after_ms != millis(end):
                raise ValueError("perp_close_latest_boundary_missing")
        elif previous and page.after_ms != millis(previous.closes[-1].open_at):
            raise ValueError("perp_close_cursor_chain_invalid")
        if page.after_ms > millis(minute_end(page.capture.started_at)):
            raise ValueError("perp_close_future_cursor")
        for close in page.closes:
            prior = by_open.get(close.open_at)
            if prior is not None and Decimal(prior.price) != Decimal(close.price):
                raise ValueError("perp_close_conflicting_close")
            by_open.setdefault(close.open_at, close)
        previous = page
    if not refresh_seen and requested_end != end:
        raise ValueError("perp_close_latest_boundary_missing")
    required = tuple(end - timedelta(minutes=index) for index in range(1441, 0, -1))
    if any(stamp not in by_open for stamp in required):
        raise ValueError("perp_close_minute_missing")
    closes = tuple(by_open[stamp] for stamp in required)
    with localcontext(
        Context(
            prec=PRECISION,
            rounding=ROUND_HALF_EVEN,
            Emin=-999999,
            Emax=999999,
            capitals=1,
            clamp=0,
            traps=[InvalidOperation, DivisionByZero, Overflow],
        )
    ):
        prices = [Decimal(close.price) for close in closes]
        returns = tuple(
            (current / prior).ln() for prior, current in zip(prices, prices[1:], strict=False)
        )
        sigma_squared = sum((item * item for item in returns), Decimal(0))
        sigma = sigma_squared.sqrt()
        thresholds = []
        for horizon, seconds in (("4h", 14400), ("24h", 86400), ("72h", 259200)):
            raw = Decimal("0.25") * sigma * (Decimal(seconds) / Decimal(86400)).sqrt()
            fraction = raw.quantize(Decimal("1e-18"))
            if fraction <= 0:
                raise ValueError("perp_close_threshold_not_positive")
            thresholds.append(PerpetualThreshold(horizon, seconds, number(raw), number(fraction)))
    method_hash = canonical_hash({"policy": POLICY, "software_hash": software_hash})
    values = dict(
        instrument_ref_ref=instrument_ref.ref,
        instrument_ref_hash=instrument_ref.hash,
        inst_id=inst_id,
        as_of=aware_utc(as_of),
        requested_end_at=requested_end,
        end_at=end,
        closes=closes,
        log_returns=tuple(number(item) for item in returns),
        sigma24_squared=number(sigma_squared),
        sigma24=number(sigma),
        thresholds=tuple(thresholds),
        capture_ids=tuple(page.capture.capture_id for page in pages),
        method_hash=method_hash,
        software_hash=software_hash,
    )
    # Include complete capture metadata, including older rows omitted from the
    # selected window and original page cursors, in the immutable source hash.
    digest = canonical_hash(
        json_body(
            {
                "window": asdict(PerpetualCloseWindow(**values, window_hash="")),
                "pages": [asdict(page) for page in pages],
            }
        )
    )
    return PerpetualCloseWindow(**values, window_hash=digest)

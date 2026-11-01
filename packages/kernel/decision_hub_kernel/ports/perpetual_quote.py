"""Pure OKX top-book originals; derivatives level size is in contracts.

Official contract: https://app.okx.com/docs-v5/en/#order-book-trading-market-data-get-order-book
These are observed quotes, never exchange fills or mark risk prices.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from .action_quote import CaptureOriginal, decode_json, exact_price
from .market_path import aware_utc, canonical_hash
from .okx_perpetual_original import IDENTITIES

VERSION = "okx-perp-book.v1"


def public_url(inst_id: str) -> str:
    if inst_id not in IDENTITIES:
        raise ValueError("perp_book_instrument_unsupported")
    # OKX documents a default depth of one. Omitting optional sz also preserves
    # the exact URL through the existing capture safe-query allowlist.
    return f"https://www.okx.com/api/v5/market/books?instId={inst_id}"


@dataclass(frozen=True)
class PerpetualBook:
    instrument_id: str
    capture_id: str
    capture_hash: str
    observed_at: datetime
    received_at: datetime
    bid: str
    ask: str
    bid_contracts: str
    ask_contracts: str
    capacity_key: str
    observed_local_lower: datetime
    observed_local_upper: datetime
    clock_receipt_json: str | None = None


def _size(value: object) -> str:
    if (
        type(value) is not str
        or len(value) > 51
        or re.fullmatch(r"(?:0|[1-9][0-9]{0,31})(?:\.[0-9]{1,18})?", value) is None
    ):
        raise ValueError("perp_book_contract_size_required")
    return value


def decode_book(
    inst_id: str,
    capture: CaptureOriginal,
    raw: bytes,
    *,
    cutoff: datetime,
    clock_receipt_json: str | None = None,
):
    if (
        capture.url != public_url(inst_id)
        or capture.adapter_version != VERSION
        or re.fullmatch(
            rf"{re.escape(VERSION)}:{re.escape(inst_id)}:[a-f0-9]{{32}}", capture.context_id
        )
        is None
    ):
        raise ValueError("perp_book_capture_binding_mismatch")
    digest = hashlib.sha256(raw).hexdigest()
    if (
        capture.body_hash != digest
        or capture.body_ref != f"raw://sha256/{digest}"
        or len(raw) != capture.byte_count
    ):
        raise ValueError("perp_book_raw_hash_mismatch")
    if capture.status_code != 200 or capture.error_code is not None:
        raise ValueError("perp_book_capture_not_successful")
    cutoff = aware_utc(cutoff)
    started, received, recorded = map(
        aware_utc,
        (
            capture.started_at,
            capture.completed_at,
            capture.recorded_at,
        ),
    )
    if not started <= received <= recorded <= cutoff:
        raise ValueError("perp_book_capture_clock_order")
    body = decode_json(raw)
    if (
        body.get("code") != "0"
        or type(body.get("data")) is not list
        or len(body["data"]) != 1
        or type(body["data"][0]) is not dict
    ):
        raise ValueError("perp_book_unique_successful_row_required")
    row = body["data"][0]
    levels = []
    for side in ("bids", "asks"):
        if (
            type(row.get(side)) is not list
            or len(row[side]) != 1
            or type(row[side][0]) is not list
            or len(row[side][0]) != 4
        ):
            raise ValueError("perp_book_top_level_required")
        price, size, deprecated, count = row[side][0]
        exact_price(price)
        _size(size)
        if deprecated != "0" or type(count) is not str or not count.isdigit():
            raise ValueError("perp_book_level_format_invalid")
        levels.append((price, size))
    bid, ask = levels
    if Decimal(bid[0]) > Decimal(ask[0]):
        raise ValueError("perp_book_crossed")
    ts, seq = row.get("ts"), row.get("seqId")
    if type(ts) is not str or re.fullmatch(r"[1-9][0-9]{0,15}", ts) is None:
        raise ValueError("perp_book_timestamp_required")
    if type(seq) is not int or seq < 0:
        raise ValueError("perp_book_sequence_required")
    observed = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=int(ts))
    local_lower = local_upper = observed
    if clock_receipt_json is not None:
        from .perpetual_quote_clock import POLICY, POLICY_HASH

        receipt = decode_json(clock_receipt_json)
        digest = canonical_hash(
            {key: value for key, value in receipt.items() if key not in {"id", "payload_hash"}}
        )
        if (
            receipt.get("policy") != POLICY
            or receipt.get("policy_hash") != POLICY_HASH
            or receipt.get("payload_hash") != digest
            or receipt.get("id") != "perpclock_" + digest
            or receipt.get("quote_capture_ref") != capture.capture_id
            or receipt.get("quote_capture_hash") != canonical_hash(capture.payload())
            or aware_utc(datetime.fromisoformat(receipt["measurement_recorded_at"])) > cutoff
            or aware_utc(datetime.fromisoformat(receipt["provider_observed_at"])) != observed
        ):
            raise ValueError("perp_clock_receipt_binding_invalid")
        local_lower = aware_utc(datetime.fromisoformat(receipt["observed_local_lower"]))
        local_upper = aware_utc(datetime.fromisoformat(receipt["observed_local_upper"]))
    if not local_lower <= local_upper <= received or cutoff - local_lower > timedelta(seconds=5):
        raise ValueError("perp_quote_stale_or_future")
    # Re-capturing the same venue snapshot does not replenish its capacity.
    capacity = canonical_hash([inst_id, ts, seq])
    return PerpetualBook(
        "okx:" + inst_id,
        capture.capture_id,
        canonical_hash(capture.payload()),
        observed,
        received,
        bid[0],
        ask[0],
        bid[1],
        ask[1],
        capacity,
        local_lower,
        local_upper,
        clock_receipt_json,
    )

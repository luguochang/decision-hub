"""Exact horizon-bounded truth on original confirmed swap capture pages."""

import hashlib
import re
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from ..ports.action_quote import CaptureOriginal, decode_json
from ..ports.market_path import aware_utc, canonical_hash
from ..ports.perpetual_close import (
    INSTRUMENTS,
    MINUTE_MS,
    PerpetualClose,
    PerpetualClosePage,
    _decimal,  # pyright: ignore[reportPrivateUsage]
    millis,
    request_cursor,
)
from ..ports.perpetual_forecast_scoring import expected_closes, horizon_policy

VERSION = "okx-perp-forecast-truth.v1"


class TruthCollectionError(ValueError):
    def __init__(self, reason: str, capture_ids: tuple[str, ...]):
        super().__init__(reason)
        self.capture_ids = capture_ids


def _capture_rows(
    capture: CaptureOriginal, raw: bytes, inst_id: str, as_of: datetime
) -> tuple[list[Any], int, str, int]:
    # The old v1 codec's six-page identity remains unchanged; this codec owns 0..14.
    match = re.fullmatch(
        rf"{re.escape(VERSION)}:{re.escape(inst_id)}:([a-f0-9]{{32}}):base:(0|[1-9]|1[0-4])",
        capture.context_id,
    )
    if match is None or inst_id not in INSTRUMENTS or capture.adapter_version != VERSION:
        raise ValueError("perpetual_scoring_capture_identity_invalid")
    cursor = request_cursor(capture.url, inst_id)
    if not capture.started_at <= capture.completed_at <= capture.recorded_at:
        raise ValueError("perpetual_scoring_capture_clock_invalid")
    if aware_utc(capture.recorded_at) > aware_utc(as_of):
        raise ValueError("perpetual_scoring_not_known_at_cutoff")
    if (
        capture.status_code != 200
        or capture.error_code is not None
        or capture.body_hash != hashlib.sha256(raw).hexdigest()
        or capture.body_ref != "raw://sha256/" + str(capture.body_hash)
        or capture.byte_count != len(raw)
    ):
        raise ValueError("perpetual_scoring_capture_invalid")
    envelope = decode_json(raw)
    data = envelope.get("data")
    if envelope.get("code") != "0" or not isinstance(data, list) or not 1 <= len(data) <= 300:
        raise ValueError("perpetual_scoring_response_invalid")
    return data, cursor, match[1], int(match[2])


def _close(
    row: Any, previous: int, capture: CaptureOriginal, index: int, metadata_hash: str
) -> PerpetualClose:
    if capture.body_hash is None or capture.body_ref is None:
        raise ValueError("perpetual_scoring_capture_body_required")
    if (
        not isinstance(row, list)
        or len(row) != 9
        or not all(isinstance(item, str) for item in row)
        or not re.fullmatch(r"[1-9][0-9]*", row[0])
        or row[8] != "1"
    ):
        raise ValueError("perpetual_scoring_confirmed_row_required")
    stamp = int(row[0])
    if stamp % MINUTE_MS or stamp >= previous:
        raise ValueError("perpetual_scoring_cursor_order_invalid")
    opened_at = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=stamp)
    close_at = opened_at + timedelta(minutes=1)
    if close_at > aware_utc(capture.completed_at):
        raise ValueError("perpetual_scoring_future_bar")
    opened, high, low, closed = (_decimal(item, positive=True) for item in row[1:5])
    if not low <= min(opened, closed) <= max(opened, closed) <= high:
        raise ValueError("perpetual_scoring_ohlc_invalid")
    for volume in row[5:8]:
        _decimal(volume, positive=False)
    return PerpetualClose(
        opened_at,
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


def parse_truth_page(
    capture: CaptureOriginal,
    raw: bytes,
    *,
    inst_id: str,
    as_of: datetime,
    selected_rows: Callable[..., Any],
) -> PerpetualClosePage:
    data, cursor, acquisition, sequence = _capture_rows(capture, raw, inst_id, as_of)
    rows = selected_rows(raw, capture.body_hash, tuple(range(len(data))))
    closes, previous = [], cursor
    metadata_hash = canonical_hash(capture.payload())
    for index in range(len(data)):
        row = rows[index]
        if row != data[index]:
            raise ValueError("perpetual_scoring_parse_cache_mismatch")
        close = _close(row, previous, capture, index, metadata_hash)
        closes.append(close)
        previous = millis(close.open_at)
    return PerpetualClosePage(capture, acquisition, "base", sequence, cursor, tuple(closes))


def derive_truth(
    pages: Sequence[PerpetualClosePage],
    *,
    instrument_id: str,
    start: datetime,
    end: datetime,
    horizon: str,
    recorded_at: datetime,
) -> tuple[PerpetualClose, ...]:
    """No missing-minute padding or latest-data substitution can create a label."""
    at = aware_utc(recorded_at)
    expected = expected_closes(start, end, horizon)
    if instrument_id not in INSTRUMENTS or end > at:
        raise ValueError("perpetual_scoring_truth_not_mature_or_scope_invalid")
    if not pages or len(pages) > horizon_policy(horizon)["max_pages"]:
        raise ValueError("perpetual_scoring_truth_page_count_invalid")
    cursor, capture_ids, closes = millis(end), set(), {}
    previous = None
    for sequence, page in enumerate(pages):
        if (
            page.kind != "base"
            or page.sequence != sequence
            or page.acquisition_id != pages[0].acquisition_id
            or page.capture.capture_id in capture_ids
            or page.after_ms != cursor
            or request_cursor(page.capture.url, instrument_id) != cursor
            or page.capture.recorded_at > at
            or previous is not None
            and previous.capture.recorded_at > page.capture.started_at
        ):
            raise ValueError("perpetual_scoring_truth_cursor_or_PIT_invalid")
        capture_ids.add(page.capture.capture_id)
        for close in page.closes:
            if not start <= close.close_at <= end:
                continue
            if close.recorded_at > at or close.close_at in closes:
                raise ValueError("perpetual_scoring_truth_duplicate_or_future_minute")
            closes[close.close_at] = close
        cursor, previous = millis(page.closes[-1].open_at), page
    if set(closes) != set(expected):
        raise ValueError("perpetual_scoring_truth_exact_minute_missing")
    return tuple(closes[stamp] for stamp in expected)


async def collect_truth_pages(
    *,
    instrument_id: str,
    start: datetime,
    end: datetime,
    horizon: str,
    capture_page: Callable[[int, int], Awaitable[PerpetualClosePage]],
    clock: Callable[[], datetime],
) -> tuple[str, ...]:
    expected_closes(start, end, horizon)
    if end > aware_utc(clock()):
        raise ValueError("perpetual_scoring_target_not_mature")
    cursor, pages = millis(end), []
    for sequence in range(horizon_policy(horizon)["max_pages"]):
        try:
            page = await capture_page(cursor, sequence)
            pages.append(page)
            if page.after_ms != cursor or request_cursor(page.capture.url, instrument_id) != cursor:
                raise ValueError("perpetual_scoring_truth_cursor_or_PIT_invalid")
            if page.closes[-1].close_at <= start:
                derive_truth(
                    pages,
                    instrument_id=instrument_id,
                    start=start,
                    end=end,
                    horizon=horizon,
                    recorded_at=clock(),
                )
                return tuple(item.capture.capture_id for item in pages)
            cursor = millis(page.closes[-1].open_at)
        except (ValueError, OSError, httpx.HTTPError) as error:
            prior = tuple(item.capture.capture_id for item in pages)
            partial = getattr(error, "capture_ids", ())
            reason = str(error) if isinstance(error, ValueError) else type(error).__name__
            raise TruthCollectionError(reason, tuple(dict.fromkeys((*prior, *partial)))) from error
    raise TruthCollectionError(
        "perpetual_scoring_truth_exact_minute_missing",
        tuple(item.capture.capture_id for item in pages),
    )

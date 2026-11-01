"""Hourly history originals -> frozen direction base rate for one perpetual admission.

Kept apart from perpetual_close_source: those files are hashed into every frozen
close window, so this optional, non-blocking input must not change them. The
admission stores only capture IDs and as_of; every read recomputes from the raw
archive and must reproduce the frozen result exactly.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx

from packages.provider_adapters.research.raw_capture import (
    CaptureBatch,
    CapturingAsyncClient,
    capture_scope,
)

from ..persistence.action_quote_repository import _capture
from ..persistence.db import RawCaptureRecord, utcnow
from ..ports.market_path import aware_utc
from ..ports.perpetual_base_rate import direction_base_rate
from ..ports.perpetual_hourly_history import (
    HOUR_MS,
    MAX_PAGES,
    VERSION,
    WINDOW_DAYS,
    parse_hourly_page,
    public_url,
)

SCHEMA = "perpetual-direction-base-rate-original.v1"
COMPARISON_DAYS = 30
COLLECTION_SECONDS = 60


@dataclass(frozen=True)
class CollectedHourlyHistory:
    capture_ids: tuple[str, ...]
    as_of: datetime


def _hour_floor(value: datetime) -> datetime:
    return aware_utc(value).replace(minute=0, second=0, microsecond=0)


def _millis(value: datetime) -> int:
    return int((aware_utc(value) - datetime(1970, 1, 1, tzinfo=UTC)) / timedelta(milliseconds=1))


async def collect_hourly_history(source, *, inst_id: str) -> CollectedHourlyHistory:
    """Public 1H candles covering WINDOW_DAYS plus the longest horizon; raises on failure."""

    async def collect():
        end = _hour_floor(utcnow())
        oldest_needed = _millis(end - timedelta(days=WINDOW_DAYS)) - HOUR_MS
        after = _millis(end)
        public_url(inst_id, after)
        acquisition, ids = uuid4().hex, []
        async with CapturingAsyncClient(timeout=15, follow_redirects=False) as client:
            for sequence in range(MAX_PAGES):
                batch = CaptureBatch(
                    f"{VERSION}:{inst_id}:{acquisition}:{sequence}",
                    VERSION,
                    source.archive,
                    source.lineage.record_capture,
                )
                with capture_scope(batch):
                    response = await client.get(public_url(inst_id, after))
                response.raise_for_status()
                if len(batch.captures) != 1:
                    raise ValueError("perp_hourly_one_capture_required")
                entry = batch.captures[0]
                _, rows = parse_hourly_page(
                    _capture(RawCaptureRecord(**entry)),
                    response.content,
                    inst_id=inst_id,
                    as_of=utcnow(),
                    sequence=sequence,
                )
                ids.append(entry["capture_id"])
                if not rows:
                    break
                after = _millis(rows[-1][0]) - HOUR_MS
                if after <= oldest_needed:
                    break
        return CollectedHourlyHistory(tuple(ids), utcnow())

    return await asyncio.wait_for(collect(), COLLECTION_SECONDS)


async def collect_hourly_history_or_none(source, *, inst_id: str):
    """Base rate is optional research input: a failed collection never blocks admission."""
    try:
        return await collect_hourly_history(source, inst_id=inst_id)
    except (httpx.HTTPError, ValueError, OSError, TimeoutError):
        return None


def _closes_in_session(session, archive, *, inst_id, capture_ids, as_of):
    closes, previous_oldest = [], None
    for sequence, capture_id in enumerate(capture_ids):
        row = session.get(RawCaptureRecord, capture_id, populate_existing=True)
        if row is None or row.body_ref is None:
            raise ValueError("perp_hourly_capture_missing")
        capture = _capture(row)
        after, rows = parse_hourly_page(
            capture, archive.read(row.body_ref), inst_id=inst_id, as_of=as_of,
            sequence=sequence,
        )
        if sequence == 0:
            # The cursor is the hour floor taken just before the first request.
            started = _millis(_hour_floor(capture.started_at))
            if not started - HOUR_MS <= after <= started:
                raise ValueError("perp_hourly_first_cursor_invalid")
        elif previous_oldest is None or after != previous_oldest:
            raise ValueError("perp_hourly_cursor_chain_invalid")
        previous_oldest = _millis(rows[-1][0]) - HOUR_MS if rows else None
        closes.extend(rows)
    return closes


def read_direction_base_rate_in_session(
    session, archive, *, inst_id, capture_ids, as_of, threshold_fraction, horizon_seconds
) -> dict:
    """Deterministic for the same originals; invalid originals give an explicit unavailable."""
    cutoff = aware_utc(as_of)
    body = dict(
        schema_version=SCHEMA,
        inst_id=inst_id,
        capture_ids=list(capture_ids),
        as_of=cutoff.isoformat().replace("+00:00", "Z"),
        threshold_fraction=threshold_fraction,
        horizon_seconds=horizon_seconds,
        purpose="historical_frequency_prior_only",
        conditional_information=False,
    )
    if not capture_ids:
        return dict(body, status="unavailable", reason="hourly_history_not_collected")
    try:
        closes = _closes_in_session(
            session, archive, inst_id=inst_id, capture_ids=tuple(capture_ids), as_of=cutoff
        )
    except ValueError as error:
        return dict(body, status="unavailable", reason=str(error))
    windows = {
        f"window_{days}d": direction_base_rate(
            closes=closes,
            threshold_fraction=threshold_fraction,
            horizon_seconds=horizon_seconds,
            as_of=cutoff,
            window_days=days,
        )
        for days in (WINDOW_DAYS, COMPARISON_DAYS)
    }
    primary = windows[f"window_{WINDOW_DAYS}d"]
    return dict(body, status=primary["status"], reason=primary["reason"], **windows)

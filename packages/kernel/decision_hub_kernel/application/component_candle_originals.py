"""Borrowed-snapshot native candle and actual capture authentication for component review."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select

from ..persistence.db import RawCaptureRecord, as_utc
from ..persistence.risk_records import MarketCandleRecord
from ..ports.component_protocol import ComponentOriginalRef
from ..ports.market_path import MarketCandle, aware_utc, canonical_hash, spot_scope
from .market_path_store import MarketPathStore


def read_candle_original_in_session(
    session,
    *,
    reference: ComponentOriginalRef,
    archive,
    expected_scope_id: str,
    cutoff_at: datetime,
) -> MarketCandle:
    """No new Session, IO transport, mutation or implicit timezone repair of external DTOs."""
    if not reference.original_ref.startswith("market_candle:"):
        raise ValueError("component_candle_reference_namespace_invalid")
    row = session.get(
        MarketCandleRecord,
        reference.original_ref.removeprefix("market_candle:"),
        populate_existing=True,
    )
    if row is None:
        raise ValueError("component_candle_original_missing")
    raw_dto = json.loads(row.candle_json)
    if canonical_hash(raw_dto) != reference.original_hash:
        raise ValueError("component_candle_payload_changed")
    candle = MarketCandle.model_validate(raw_dto)
    scope, cutoff = spot_scope(candle.instrument, venue=candle.venue), aware_utc(cutoff_at)
    revision = canonical_hash(candle.semantic_payload())
    if (
        scope.scope_id != expected_scope_id
        or candle.scope_id != scope.scope_id
        or candle.known_at > cutoff
        or candle.close_at > cutoff
        or row.candle_id != "candle_" + canonical_hash([candle.scope_id, revision])[:48]
        or revision != row.revision_hash
        or (row.scope_id, row.venue, row.instrument, row.price_type, row.interval_seconds)
        != (
            candle.scope_id,
            candle.venue,
            candle.instrument,
            candle.price_type,
            candle.interval_seconds,
        )
        or tuple(map(as_utc, (row.open_at, row.close_at, row.received_at, row.known_at)))
        != (candle.open_at, candle.close_at, candle.received_at, candle.known_at)
        or (row.capture_ref, row.capture_hash) != (candle.capture_ref, candle.capture_hash)
    ):
        raise ValueError("component_candle_scope_or_time_invalid")
    if candle.capture_ref is None or candle.capture_hash is None or candle.row_index is None:
        raise ValueError("component_candle_capture_missing")
    body = archive.read(candle.capture_ref)
    if len(body) > 1024 * 1024 or hashlib.sha256(body).hexdigest() != candle.capture_hash:
        raise ValueError("component_candle_raw_changed")
    payload = json.loads(body)
    if (
        not isinstance(payload, dict)
        or str(payload.get("code")) != "0"
        or not isinstance(payload.get("data"), list)
    ):
        raise ValueError("component_candle_raw_shape_invalid")
    captures = session.scalars(
        select(RawCaptureRecord)
        .where(
            RawCaptureRecord.body_ref == candle.capture_ref,
            RawCaptureRecord.body_hash == candle.capture_hash,
        )
        .limit(129)
    ).all()
    if len(captures) > 128 or not any(
        record.status_code == 200
        and record.error_code is None
        and as_utc(record.completed_at) == candle.received_at
        and MarketPathStore._url_matches(record.url, scope, candle)
        for record in captures
    ):
        raise ValueError("component_candle_native_capture_invalid")
    try:
        item = payload["data"][candle.row_index]
        if (
            not isinstance(item, list)
            or len(item) != 9
            or str(item[8]) != "1"
            or int(item[0]) != int(candle.open_at.timestamp() * 1000)
        ):
            raise ValueError("component_candle_raw_row_invalid")
        for name, index in (
            ("open", 1),
            ("high", 2),
            ("low", 3),
            ("close", 4),
            ("volume_base", 5),
            ("volume_quote", 7),
        ):
            if getattr(candle, name) != Decimal(str(item[index])):
                raise ValueError("component_candle_raw_value_invalid")
    except (IndexError, TypeError, ArithmeticError) as exc:
        raise ValueError("component_candle_raw_row_invalid") from exc
    return candle

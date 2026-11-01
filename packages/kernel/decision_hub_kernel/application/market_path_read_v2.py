"""Independent V2 summary reader with fresh originals and call-local metadata index.

The frozen V1 Store and historical/report readers remain unchanged. This reader
selects scalar basis columns, then validates every selected DTO and original.
Only capture metadata lookup is indexed; no verified bytes, DTOs or permissions
are retained as qualification across calls.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from urllib.parse import parse_qs, urlsplit

from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session

from ..persistence.db import Database, RawCaptureRecord, as_utc
from ..persistence.risk_records import MarketCandleRecord
from ..ports.market_path import (
    MarketCandle,
    MarketPathScope,
    aware_utc,
    canonical_hash,
    scope_matches,
)
from ..ports.market_path_read_v2 import MarketOriginalBytesReadPort
from .market_original_parse import MarketOriginalParse


class MarketPathReaderV2:
    def __init__(self, database: Database, archive: MarketOriginalBytesReadPort) -> None:
        self.database = database
        self.archive = archive
        # Existing bounded pure parsing hints still check fresh input SHA on every call.
        self._original_parse = MarketOriginalParse()

    @property
    def version(self) -> Literal["market-path-read.v2"]:
        return "market-path-read.v2"

    @staticmethod
    def _capture_after(url: str, scope: MarketPathScope) -> int | None:
        parts = urlsplit(url)
        query = parse_qs(parts.query, keep_blank_values=True)
        if (
            parts.scheme != "https"
            or parts.hostname != "www.okx.com"
            or parts.path != "/api/v5/market/history-candles"
            or parts.username
            or parts.port not in (None, 443)
            or parts.fragment
            or set(query) != {"instId", "bar", "after", "limit"}
            or query["instId"] != [scope.instrument]
            or query["bar"] != ["1m"]
            or query["limit"] != ["300"]
            or len(query["after"]) != 1
        ):
            return None
        try:
            after = int(query["after"][0])
        except ValueError:
            return None
        if (
            scope.venue != "okx"
            or scope.instrument_kind != "spot"
            or scope.price_type != "trade"
            or after % 60_000 != 0
        ):
            return None
        return after

    def _verify_originals(
        self, session: Session, scope: MarketPathScope, bars: tuple[MarketCandle, ...]
    ) -> None:
        identities = tuple(dict.fromkeys(
            (bar.capture_ref, bar.capture_hash)
            for bar in bars if bar.capture_ref and bar.capture_hash
        ))
        indices: dict[str, list[int]] = {}
        for bar in bars:
            if bar.capture_ref and bar.row_index is not None:
                indices.setdefault(bar.capture_ref, []).append(bar.row_index)

        # Preserve the original candidate order and existential match. Eligibility
        # and receipt UTC are indexed once; URL parsing remains lazy so a valid
        # earlier candidate still short-circuits a malformed later candidate.
        capture_urls: dict[tuple[str, str, datetime], list[str]] = {}
        for offset in range(0, len(identities), 256):
            records = session.execute(select(
                RawCaptureRecord.body_ref,
                RawCaptureRecord.body_hash,
                RawCaptureRecord.status_code,
                RawCaptureRecord.error_code,
                RawCaptureRecord.completed_at,
                RawCaptureRecord.url,
            ).where(tuple_(RawCaptureRecord.body_ref, RawCaptureRecord.body_hash).in_(
                identities[offset:offset + 256]
            ))).all()
            for record in records:
                body_ref, body_hash, status_code, error_code, completed_at, url = record
                assert body_ref is not None and body_hash is not None
                if status_code == 200 and error_code is None:
                    completed_at = as_utc(completed_at)
                    capture_urls.setdefault((body_ref, body_hash, completed_at), []).append(url)

        parsed_urls: dict[str, int | None] = {}
        originals: dict[str, tuple[str, dict[int, Any]]] = {}

        def url_matches(url: str, bar: MarketCandle) -> bool:
            if url not in parsed_urls:
                parsed_urls[url] = self._capture_after(url, scope)
            after = parsed_urls[url]
            return after is not None and after > int(bar.open_at.timestamp() * 1000)

        for bar in bars:
            if not scope_matches(scope, bar):
                raise ValueError("market_store_scope_mismatch")
            if not bar.capture_ref or not bar.capture_hash or bar.row_index is None:
                raise ValueError("market_store_original_missing")
            if bar.capture_ref not in originals:
                body = self.archive.read(bar.capture_ref)
                try:
                    rows = self._original_parse.selected_rows(
                        body, bar.capture_hash, tuple(dict.fromkeys(indices[bar.capture_ref]))
                    )
                except (IndexError, TypeError, ArithmeticError) as exc:
                    raise ValueError("market_store_row_invalid") from exc
                originals[bar.capture_ref] = (bar.capture_hash, rows)
            original_hash, original_rows = originals[bar.capture_ref]
            if original_hash != bar.capture_hash:
                raise ValueError("market_store_original_hash_mismatch")
            if not any(url_matches(url, bar) for url in capture_urls.get(
                (bar.capture_ref, bar.capture_hash, bar.received_at), ()
            )):
                raise ValueError("market_store_capture_scope_or_clock_mismatch")
            try:
                raw = original_rows[bar.row_index]
                if not isinstance(raw, list) or len(raw) != 9 or str(raw[8]) != "1":
                    raise ValueError("market_store_row_invalid")
                if int(raw[0]) != int(bar.open_at.timestamp() * 1000):
                    raise ValueError("market_store_row_clock_mismatch")
                pairs = (
                    ("open", 1), ("high", 2), ("low", 3), ("close", 4),
                    ("volume_base", 5), ("volume_quote", 7),
                )
                if any(getattr(bar, name) != Decimal(str(raw[index])) for name, index in pairs):
                    raise ValueError("market_store_row_value_mismatch")
            except (IndexError, TypeError, ArithmeticError) as exc:
                raise ValueError("market_store_row_invalid") from exc

    def load(
        self,
        scope: MarketPathScope,
        *,
        start_at: datetime,
        end_at: datetime,
        as_of: datetime,
    ) -> tuple[MarketCandle, ...]:
        start, end, clock = aware_utc(start_at), aware_utc(end_at), aware_utc(as_of)
        if not start < end <= clock:
            raise ValueError("market_store_window_invalid")
        with self.database.session() as session, session.no_autoflush:
            connection = session.connection()
            if connection.dialect.name == "sqlite":
                native = connection.connection.driver_connection
                if native is None:
                    raise ValueError("market_store_physical_snapshot_required")
                if not native.in_transaction:
                    connection.exec_driver_sql("BEGIN")
                if not native.in_transaction:
                    raise ValueError("market_store_physical_snapshot_required")
            rows = session.execute(select(
                MarketCandleRecord.candle_id,
                MarketCandleRecord.venue,
                MarketCandleRecord.instrument,
                MarketCandleRecord.price_type,
                MarketCandleRecord.interval_seconds,
                MarketCandleRecord.open_at,
                MarketCandleRecord.close_at,
                MarketCandleRecord.received_at,
                MarketCandleRecord.known_at,
                MarketCandleRecord.revision_hash,
                MarketCandleRecord.candle_json,
                MarketCandleRecord.capture_ref,
                MarketCandleRecord.capture_hash,
            ).where(
                MarketCandleRecord.scope_id == scope.scope_id,
                MarketCandleRecord.open_at >= start,
                MarketCandleRecord.close_at <= end,
                MarketCandleRecord.known_at <= clock,
            ).order_by(MarketCandleRecord.open_at, MarketCandleRecord.known_at)).all()
            selected: dict[datetime, Any] = {}
            for row in rows:
                open_at = row[5]
                previous = selected.get(open_at)
                if previous is not None and as_utc(previous[8]) == as_utc(row[8]):
                    if previous[9] != row[9]:
                        raise ValueError("market_store_conflicting_revision_time")
                selected[open_at] = row
            bars = []
            for row in selected.values():
                (
                    candle_id, venue, instrument, price_type, interval_seconds,
                    open_at, close_at, received_at, known_at, revision_hash,
                    candle_json, capture_ref, capture_hash,
                ) = row
                bar = MarketCandle.model_validate_json(candle_json)
                revision = canonical_hash(bar.semantic_payload())
                if (
                    not scope_matches(scope, bar)
                    or venue != bar.venue
                    or instrument != bar.instrument
                    or price_type != bar.price_type
                    or interval_seconds != bar.interval_seconds
                    or revision != revision_hash
                    or candle_id != "candle_" + canonical_hash([bar.scope_id, revision])[:48]
                    or bar.open_at != as_utc(open_at)
                    or bar.close_at != as_utc(close_at)
                    or bar.received_at != as_utc(received_at)
                    or bar.known_at != as_utc(known_at)
                    or bar.capture_ref != capture_ref
                    or bar.capture_hash != capture_hash
                ):
                    raise ValueError("market_store_basis_corrupt")
                bars.append(bar)
            result = tuple(bars)
            self._verify_originals(session, scope, result)
            return result

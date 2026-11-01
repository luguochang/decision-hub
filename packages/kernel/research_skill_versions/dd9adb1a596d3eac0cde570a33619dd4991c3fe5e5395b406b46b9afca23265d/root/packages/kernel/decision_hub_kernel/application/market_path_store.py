"""Persist canonical market bars and original receipt clocks without rewriting history."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol, TypedDict
from urllib.parse import parse_qs, urlsplit

from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session

from ..persistence.db import Database, RawCaptureRecord, as_utc, utcnow
from ..persistence.risk_records import MarketCandleRecord
from ..ports.market_path import (
    MarketCandle,
    MarketPathScope,
    MarketPricePath,
    aware_utc,
    canonical_hash,
    path_hashes,
    scope_matches,
)
from .market_original_parse import MarketOriginalParse
from .risk_monitor import canonical_json


class MarketRawArchive(Protocol):
    def read(self, ref: str) -> bytes: ...


class _OriginalRows(TypedDict):
    hash: str
    rows: dict[int, Any]
    captures: list[RawCaptureRecord]


class MarketPathStore:
    def __init__(
        self,
        database: Database,
        archive: MarketRawArchive,
        *,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.database = database
        self.archive = archive
        self.clock = clock
        self._original_parse = MarketOriginalParse()

    @staticmethod
    def _capture_after(url: str, scope: MarketPathScope) -> int | None:
        """Parse a capture URL; its per-bar time boundary is checked separately."""
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

    @staticmethod
    def _url_matches(url: str, scope: MarketPathScope, bar: MarketCandle) -> bool:
        after = MarketPathStore._capture_after(url, scope)
        return after is not None and after > int(bar.open_at.timestamp() * 1000)

    def _verify_originals(self, scope: MarketPathScope, bars: tuple[MarketCandle, ...]) -> None:
        with self.database.session() as session, session.no_autoflush:
            self._verify_originals_in_session(session, scope, bars)

    def _verify_originals_in_session(
        self,
        session: Session,
        scope: MarketPathScope,
        bars: tuple[MarketCandle, ...],
        *,
        on_original: Callable[[str, str], None] | None = None,
    ) -> None:
        originals: dict[str, _OriginalRows] = {}
        # Only pure URL parsing is shared inside this fresh verification. Capture
        # metadata, bar identity, after/open boundary and raw values remain per bar.
        parsed_urls: dict[str, int | None] = {}

        def url_matches(url: str, bar: MarketCandle) -> bool:
            if url not in parsed_urls:
                parsed_urls[url] = self._capture_after(url, scope)
            after = parsed_urls[url]
            return after is not None and after > int(bar.open_at.timestamp() * 1000)

        identities = tuple(
            dict.fromkeys(
                (bar.capture_ref, bar.capture_hash)
                for bar in bars
                if bar.capture_ref and bar.capture_hash
            )
        )
        indices: dict[str, list[int]] = {}
        for bar in bars:
            if bar.capture_ref and bar.row_index is not None:
                indices.setdefault(bar.capture_ref, []).append(bar.row_index)
        connection = session.connection()
        if connection.dialect.name == "sqlite":
            native = connection.connection.driver_connection
            if native is None:
                raise ValueError("market_store_physical_snapshot_required")
            if not native.in_transaction:
                connection.exec_driver_sql("BEGIN")
            if not native.in_transaction:
                raise ValueError("market_store_physical_snapshot_required")
        captures: dict[tuple[str, str], list[RawCaptureRecord]] = {}
        for offset in range(0, len(identities), 256):
            records = session.scalars(
                select(RawCaptureRecord).where(
                    tuple_(RawCaptureRecord.body_ref, RawCaptureRecord.body_hash).in_(
                        identities[offset : offset + 256]
                    )
                )
            ).all()
            for record in records:
                assert record.body_ref is not None and record.body_hash is not None
                captures.setdefault((record.body_ref, record.body_hash), []).append(record)
        for bar in bars:
            if not scope_matches(scope, bar):
                raise ValueError("market_store_scope_mismatch")
            if not bar.capture_ref or not bar.capture_hash or bar.row_index is None:
                raise ValueError("market_store_original_missing")
            if bar.capture_ref not in originals:
                body = self.archive.read(bar.capture_ref)
                try:
                    selected_rows = self._original_parse.selected_rows(
                        body, bar.capture_hash, tuple(dict.fromkeys(indices[bar.capture_ref]))
                    )
                except (IndexError, TypeError, ArithmeticError) as exc:
                    raise ValueError("market_store_row_invalid") from exc
                if on_original is not None:
                    on_original(bar.capture_ref, bar.capture_hash)
                originals[bar.capture_ref] = {
                    "hash": bar.capture_hash,
                    "rows": selected_rows,
                    "captures": captures.get((bar.capture_ref, bar.capture_hash), []),
                }
            original = originals[bar.capture_ref]
            if original["hash"] != bar.capture_hash:
                raise ValueError("market_store_original_hash_mismatch")
            if not any(
                item.status_code == 200
                and item.error_code is None
                and as_utc(item.completed_at) == bar.received_at
                and url_matches(item.url, bar)
                for item in original["captures"]
            ):
                raise ValueError("market_store_capture_scope_or_clock_mismatch")
            try:
                raw = original["rows"][bar.row_index]
                if not isinstance(raw, list) or len(raw) != 9 or str(raw[8]) != "1":
                    raise ValueError("market_store_row_invalid")
                if int(raw[0]) != int(bar.open_at.timestamp() * 1000):
                    raise ValueError("market_store_row_clock_mismatch")
                pairs = (
                    ("open", 1),
                    ("high", 2),
                    ("low", 3),
                    ("close", 4),
                    ("volume_base", 5),
                    ("volume_quote", 7),
                )
                if any(getattr(bar, name) != Decimal(str(raw[index])) for name, index in pairs):
                    raise ValueError("market_store_row_value_mismatch")
            except (IndexError, TypeError, ArithmeticError) as exc:
                raise ValueError("market_store_row_invalid") from exc

    def ingest(self, path: MarketPricePath) -> int:
        now = aware_utc(self.clock())
        scope = MarketPathScope.model_validate(path.scope.model_dump())
        bars = tuple(MarketCandle.model_validate(bar.model_dump()) for bar in path.candles)
        start, end = aware_utc(path.window_start_at), aware_utc(path.window_end_at)
        if not start < end <= now or (end - start).total_seconds() > 86400:
            raise ValueError("market_store_window_invalid")
        if any(bar.received_at > now for bar in bars):
            raise ValueError("market_store_future_receipt")
        if any(not start <= bar.open_at < end for bar in bars) or len(
            {bar.open_at for bar in bars}
        ) != len(bars):
            raise ValueError("market_store_window_bars_invalid")
        if path_hashes(scope, bars) != (path.semantic_hash, path.manifest_hash):
            raise ValueError("market_store_path_hash_mismatch")
        self._verify_originals(scope, bars)
        added = 0
        # The accepted clock follows original verification, never precedes it.
        accepted_at = aware_utc(self.clock())
        with self.database.session() as session:
            for bar in bars:
                revision = canonical_hash(bar.semantic_payload())
                identity = "candle_" + canonical_hash([bar.scope_id, revision])[:48]
                if session.get(MarketCandleRecord, identity) is not None:
                    continue
                # Verification of the original is mandatory before this boundary.
                assert bar.capture_ref is not None and bar.capture_hash is not None
                known_at = max(accepted_at, bar.received_at)
                payload = bar.model_dump(mode="json")
                payload["accepted_at"] = accepted_at.isoformat()
                session.add(
                    MarketCandleRecord(
                        candle_id=identity,
                        scope_id=bar.scope_id,
                        venue=bar.venue,
                        instrument=bar.instrument,
                        price_type=bar.price_type,
                        interval_seconds=bar.interval_seconds,
                        open_at=bar.open_at,
                        close_at=bar.close_at,
                        received_at=bar.received_at,
                        known_at=known_at,
                        revision_hash=revision,
                        candle_json=canonical_json(payload),
                        capture_ref=bar.capture_ref,
                        capture_hash=bar.capture_hash,
                    )
                )
                added += 1
        return added

    def load(
        self,
        scope: MarketPathScope,
        *,
        start_at: datetime,
        end_at: datetime,
        as_of: datetime,
    ) -> tuple[MarketCandle, ...]:
        with self.database.session() as session, session.no_autoflush:
            return self.load_in_session(
                session,
                scope,
                start_at=start_at,
                end_at=end_at,
                as_of=as_of,
            )

    def load_in_session(
        self,
        session: Session,
        scope: MarketPathScope,
        *,
        start_at: datetime,
        end_at: datetime,
        as_of: datetime,
        on_original: Callable[[str, str], None] | None = None,
    ) -> tuple[MarketCandle, ...]:
        """Authenticate bars and capture rows in the caller's physical snapshot.

        The callback records already-verified immutable raw references for a
        consuming writer's byte recheck; it grants no source permission.
        """
        start, end, clock = aware_utc(start_at), aware_utc(end_at), aware_utc(as_of)
        if not start < end <= clock:
            raise ValueError("market_store_window_invalid")
        connection = session.connection()
        if connection.dialect.name == "sqlite":
            native = connection.connection.driver_connection
            if native is None:
                raise ValueError("market_store_physical_snapshot_required")
            if not native.in_transaction:
                connection.exec_driver_sql("BEGIN")
        rows = (
            session.query(MarketCandleRecord)
            .filter(
                MarketCandleRecord.scope_id == scope.scope_id,
                MarketCandleRecord.open_at >= start,
                MarketCandleRecord.close_at <= end,
                MarketCandleRecord.known_at <= clock,
            )
            .order_by(MarketCandleRecord.open_at, MarketCandleRecord.known_at)
            .all()
        )
        selected: dict[datetime, MarketCandleRecord] = {}
        for row in rows:
            previous = selected.get(row.open_at)
            if previous is not None and as_utc(previous.known_at) == as_utc(row.known_at):
                if previous.revision_hash != row.revision_hash:
                    raise ValueError("market_store_conflicting_revision_time")
            selected[row.open_at] = row
        bars = []
        for row in selected.values():
            bar = MarketCandle.model_validate_json(row.candle_json)
            revision = canonical_hash(bar.semantic_payload())
            if (
                not scope_matches(scope, bar)
                or row.venue != bar.venue
                or row.instrument != bar.instrument
                or row.price_type != bar.price_type
                or row.interval_seconds != bar.interval_seconds
                or revision != row.revision_hash
                or row.candle_id != "candle_" + canonical_hash([bar.scope_id, revision])[:48]
                or bar.open_at != as_utc(row.open_at)
                or bar.close_at != as_utc(row.close_at)
                or bar.received_at != as_utc(row.received_at)
                or bar.known_at != as_utc(row.known_at)
                or bar.capture_ref != row.capture_ref
                or bar.capture_hash != row.capture_hash
            ):
                raise ValueError("market_store_basis_corrupt")
            bars.append(bar)
        bars = tuple(bars)
        self._verify_originals_in_session(session, scope, bars, on_original=on_original)
        return bars


def archive_feature(body: str, root: Path) -> tuple[str, str]:
    """Immutable JSON receipt; the supplied manifest remains inside its body."""
    payload = body.encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    path = root / digest[:2] / (digest + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as stream:
            stream.write(payload)
    except FileExistsError:
        if path.read_bytes() != payload:
            raise ValueError("market_feature_archive_corrupt") from None
    return str(path.resolve()), digest

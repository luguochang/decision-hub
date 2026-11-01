"""Request-scoped full raw-capture candidates in one physical RO snapshot."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType, SimpleNamespace

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..persistence.db import RawCaptureRecord, as_utc
from ..persistence.risk_records import MarketCandleRecord
from ..ports.market_path import MarketCandle, MarketPathScope, canonical_hash, scope_matches
from . import history_forecast_transfer39 as transfer
from .history_forecast_dispatch39 import HistoryForecastDispatch39Service
from .history_observed_dataset import (
    capture_payload,
    capture_url_matches,
    encoded,
    physical_snapshot,
    require_snapshot,
    row_fingerprint,
    snapshot_identity,
)

LOADED_SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
SOURCE_FILE = "packages/kernel/decision_hub_kernel/application/history_outcome_originals_v2.py"


@dataclass(frozen=True, slots=True)
class CaptureCacheV2:
    refs: tuple[str, ...]
    captures: tuple[tuple, ...]
    schemas: tuple
    sources: tuple
    context: tuple
    boundary: tuple
    issuer: object
    thread_id: int
    authentication: bytes


def _capture_pool(session, refs, check):
    rows = []
    for offset in range(0, len(refs), transfer.BATCH):
        check()
        chunk = refs[offset : offset + transfer.BATCH]
        rows.extend(
            tuple(row)
            for row in session.connection()
            .exec_driver_sql(
                f"SELECT {transfer.PROJECTIONS['raw_captures']} FROM raw_captures "
                f"WHERE body_ref IN ({','.join('?' for _ in chunk)})",
                tuple(chunk),
            )
            .all()
        )
    return tuple(sorted(rows, key=lambda row: row[0]))


class HistoryOutcomeCaptureAuthorityV2:
    def __init__(self, forecasts: HistoryForecastDispatch39Service):
        if type(forecasts) is not HistoryForecastDispatch39Service:
            raise ValueError("history_outcome_exact_forecast_dispatch_required")
        self.forecasts = forecasts
        self.key = secrets.token_bytes(32)
        self._issued = self._leaves = None
        self._pool, self._raw, self._files = {}, {}, {}
        self._windows = ()
        self._active = True

    def _loaded(self):
        if (
            hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != LOADED_SOURCE_SHA256
            or hashlib.sha256(
                (self.forecasts.originals.source_root / SOURCE_FILE).read_bytes()
            ).hexdigest()
            != LOADED_SOURCE_SHA256
            or any(
                getattr(cls, name) is not function or function.__code__ is not code
                for cls, name, function, code in LOADED_METHODS
            )
            or any(
                globals()[name] is not function or function.__code__ is not code
                for name, function, code in LOADED_FUNCTIONS
            )
            or any(globals()[name] is not value for name, value in LOADED_CLASSES)
            or transfer is not LOADED_TRANSFER
            or any(
                getattr(transfer, name) is not function or function.__code__ is not code
                for name, function, code in LOADED_TRANSFER_FUNCTIONS
            )
        ):
            raise ValueError("history_outcome_originals_loaded_source_or_callable_changed")

    def _sources(self, expected=None):
        self._loaded()
        sources = self.forecasts._sources()
        if expected is not None and not transfer.native_equal39(sources, expected):
            raise ValueError("history_outcome_originals_source_changed")
        return sources

    def _context(self, session):
        service = self.forecasts
        return (
            id(self),
            id(service),
            id(service.writer),
            id(service.originals),
            transfer.database_identity(session),
            service.owner_claim,
            tuple(sorted(service.grants.items())),
            str(service.originals.data_directory),
            str(service.originals.source_root),
            service.originals.fixture_mode,
        )

    @staticmethod
    def _members(cache):
        return (
            cache.refs,
            cache.captures,
            cache.schemas,
            cache.sources,
            cache.context,
            tuple(id(value) for value in cache.boundary[:4]),
            cache.boundary[4:],
            id(cache.issuer),
            cache.thread_id,
        )

    def issue(self, session, capture_refs, *, checkpoint, boundary=None):
        if (
            self._issued is not None
            or not self._active
            or type(capture_refs) is not tuple
            or len(set(capture_refs)) != len(capture_refs)
            or any(type(ref) is not str or not ref for ref in capture_refs)
            or not callable(checkpoint)
        ):
            raise ValueError("history_outcome_capture_cache_issue_invalid")
        self.check = checkpoint
        checkpoint()
        boundary = boundary or snapshot_identity(session)
        if session.connection().exec_driver_sql("PRAGMA query_only").scalar_one() != 1:
            raise ValueError("history_outcome_capture_cache_readonly_required")
        sources = self._sources()
        schemas = transfer.capture_schemas39(session, ("raw_captures", "market_candles"))
        refs = tuple(sorted(capture_refs))
        captures = _capture_pool(session, refs, checkpoint)
        cache = CaptureCacheV2(
            refs,
            captures,
            schemas,
            sources,
            self._context(session),
            boundary,
            self,
            threading.get_ident(),
            b"",
        )
        leaves = self._members(cache)
        self._manifest = hashlib.sha256(encoded(leaves)).hexdigest()
        authentication = hmac.digest(self.key, self._manifest.encode(), "sha256")
        cache = CaptureCacheV2(
            refs,
            captures,
            schemas,
            sources,
            cache.context,
            boundary,
            self,
            cache.thread_id,
            authentication,
        )
        self._issued, self._leaves = cache, leaves
        groups = {}
        for row in captures:
            groups.setdefault((row[5], row[4]), []).append(row)
        self._pool = MappingProxyType({key: tuple(rows) for key, rows in groups.items()})
        self._pool_identity = self._pool
        # Own and all producer bytes participate in the complete exit SHA pass.
        for path, digest in (*sources, (SOURCE_FILE, LOADED_SOURCE_SHA256)):
            target = str((self.forecasts.originals.source_root / path).resolve(strict=True))
            actual, size = self._fingerprint(target)
            if actual != digest:
                raise ValueError("history_outcome_originals_source_changed")
            self._files[target] = digest, size
        self.authenticate(session, cache)
        return cache

    def authenticate(self, session, cache):
        self.check()
        if (
            not self._active
            or type(cache) is not CaptureCacheV2
            or cache is not self._issued
            or cache.issuer is not self
            or cache.thread_id != threading.get_ident()
            or cache.boundary[0] is not session
            or not transfer.native_equal39(self._members(cache), self._leaves)
            or not transfer.native_equal39(cache.context, self._context(session))
            or self._pool is not self._pool_identity
            or not hmac.compare_digest(
                cache.authentication, hmac.digest(self.key, self._manifest.encode(), "sha256")
            )
        ):
            raise ValueError("history_outcome_capture_cache_changed")
        self._loaded()
        transfer.verify_schemas39(session, cache.schemas)
        require_snapshot(session, cache.boundary, before_write=True)

    def _fingerprint(self, path):
        digest, size = hashlib.sha256(), 0
        with Path(path).open("rb") as handle:
            while block := handle.read(1024 * 1024):
                self.check()
                digest.update(block)
                size += len(block)
        self.check()
        return digest.hexdigest(), size

    def read_raw(self, ref):
        self.check()
        if ref not in self._issued.refs:
            raise ValueError("history_outcome_capture_ref_not_in_complete_pool")
        if ref not in self._raw:
            body = self.forecasts.originals.raw.read(ref)
            digest = hashlib.sha256(body).hexdigest()
            target = str(
                (self.forecasts.originals.raw.root / digest[:2] / digest).resolve(strict=True)
            )
            actual = self._fingerprint(target)
            if actual != (digest, len(body)):
                raise ValueError("history_observed_raw_hash_changed")
            self._raw[ref] = body
            self._files[target] = actual
        return self._raw[ref]

    def capture_records(self, identity):
        result = []
        for row in self._pool.get(identity, ()):
            record = dict(zip(transfer.PROJECTIONS["raw_captures"].split(","), row, strict=True))
            for name in ("started_at", "completed_at", "recorded_at"):
                record[name] = as_utc(datetime.fromisoformat(record[name]))
            result.append(SimpleNamespace(**record))
        return result

    def verify(self, session, cache):
        self.authenticate(session, cache)
        self._sources(cache.sources)
        if not transfer.native_equal39(
            _capture_pool(session, cache.refs, self.check), cache.captures
        ):
            raise ValueError("history_outcome_capture_selection_closure_changed")
        for scope, cutoff, known, expected in self._windows:
            self.check()
            actual = _window_rows(session, scope, cutoff, known)
            if not transfer.native_equal39(actual, expected):
                raise ValueError("history_outcome_window_selection_closure_changed")
        for path, expected in self._files.items():
            if self._fingerprint(path) != expected:
                raise ValueError("history39_original_bytes_changed")
        self.authenticate(session, cache)

    def close(self, session, cache):
        try:
            self.verify(session, cache)
            require_snapshot(session, cache.boundary)
        finally:
            self.abort()

    def abort(self):
        self._active = False
        self.key = b""
        self._raw.clear()


def _window_rows(session, scope, cutoff, known):
    def stamp(value):
        return value.strftime("%Y-%m-%d %H:%M:%S.%f")

    return tuple(
        tuple(row)
        for row in session.connection()
        .exec_driver_sql(
            f"SELECT {transfer.PROJECTIONS['market_candles']} FROM market_candles "
            "WHERE scope_id=? AND open_at>=? AND close_at<=? AND known_at<=? "
            "ORDER BY open_at,known_at",
            (
                scope.scope_id,
                stamp(cutoff - timedelta(minutes=34)),
                stamp(cutoff + timedelta(days=1, minutes=1)),
                stamp(known),
            ),
        )
        .all()
    )


class _ReaderV2:
    def __init__(self, cache):
        self.cache = cache
        self.raw_reader = cache.issuer.read_raw

    def load(
        self, session: Session, scope: MarketPathScope, cutoff: datetime, known: datetime
    ) -> tuple[tuple[MarketCandle, ...], dict[str, object]]:
        boundary = snapshot_identity(session)
        start, end = cutoff - timedelta(minutes=34), cutoff + timedelta(days=1, minutes=1)
        rows = session.scalars(
            select(MarketCandleRecord)
            .where(
                MarketCandleRecord.scope_id == scope.scope_id,
                MarketCandleRecord.open_at >= start,
                MarketCandleRecord.close_at <= end,
                MarketCandleRecord.known_at <= known,
            )
            .order_by(MarketCandleRecord.open_at, MarketCandleRecord.known_at)
            .execution_options(populate_existing=True)
        ).all()
        chosen: dict[datetime, MarketCandleRecord] = {}
        for row in rows:
            old = chosen.get(row.open_at)
            if (
                old is not None
                and old.known_at == row.known_at
                and old.revision_hash != row.revision_hash
            ):
                raise ValueError("history_observed_conflicting_revision_clock")
            chosen[row.open_at] = row
        bars: list[MarketCandle] = []
        originals: dict[tuple[str, str], tuple[object, list[RawCaptureRecord]]] = {}
        body_lengths: dict[tuple[str, str], int] = {}
        used_captures: dict[str, dict[str, object]] = {}
        for row in chosen.values():
            self.cache.issuer.check()
            bar = MarketCandle.model_validate_json(row.candle_json)
            revision = canonical_hash(bar.semantic_payload())
            if (
                not scope_matches(scope, bar)
                or revision != row.revision_hash
                or row.candle_id != "candle_" + canonical_hash([bar.scope_id, revision])[:48]
                or (
                    row.venue,
                    row.instrument,
                    row.price_type,
                    row.interval_seconds,
                    as_utc(row.open_at),
                    as_utc(row.close_at),
                    as_utc(row.received_at),
                    as_utc(row.known_at),
                    row.capture_ref,
                    row.capture_hash,
                )
                != (
                    bar.venue,
                    bar.instrument,
                    bar.price_type,
                    bar.interval_seconds,
                    bar.open_at,
                    bar.close_at,
                    bar.received_at,
                    bar.known_at,
                    bar.capture_ref,
                    bar.capture_hash,
                )
                or bar.known_at > known
                or (bar.accepted_at is not None and bar.accepted_at < bar.received_at)
                or not bar.capture_ref
                or not bar.capture_hash
                or bar.row_index is None
            ):
                raise ValueError("history_observed_candle_identity_or_clock_changed")
            identity = (bar.capture_ref, bar.capture_hash)
            if identity not in originals:
                body = self.raw_reader(bar.capture_ref)
                if hashlib.sha256(body).hexdigest() != bar.capture_hash:
                    raise ValueError("history_observed_raw_hash_changed")
                raw = json.loads(body)
                if (
                    not isinstance(raw, dict)
                    or str(raw.get("code")) != "0"
                    or not isinstance(raw.get("data"), list)
                ):
                    raise ValueError("history_observed_raw_shape_invalid")
                raw_rows = raw["data"]
                if len(raw_rows) > 300:
                    raise ValueError("history_observed_raw_page_limit_invalid")
                captures = self.cache.issuer.capture_records(identity)
                originals[identity] = (raw_rows, captures)
                body_lengths[identity] = len(body)
            raw_rows, captures = originals[identity]
            if not isinstance(raw_rows, list) or bar.row_index >= len(raw_rows):
                raise ValueError("history_observed_raw_row_missing")
            raw_row = raw_rows[bar.row_index]
            if (
                not isinstance(raw_row, list)
                or len(raw_row) != 9
                or str(raw_row[8]) != "1"
                or int(raw_row[0]) != int(bar.open_at.timestamp() * 1000)
                or any(
                    getattr(bar, name) != Decimal(str(raw_row[index]))
                    for name, index in (
                        ("open", 1),
                        ("high", 2),
                        ("low", 3),
                        ("close", 4),
                        ("volume_base", 5),
                        ("volume_quote", 7),
                    )
                )
            ):
                raise ValueError("history_observed_raw_row_value_changed")
            matching = [
                capture
                for capture in captures
                if (
                    capture.status_code == 200
                    and capture.error_code is None
                    and capture.byte_count == body_lengths[identity]
                    and as_utc(capture.started_at)
                    <= as_utc(capture.completed_at)
                    <= as_utc(capture.recorded_at)
                    <= known
                    and as_utc(capture.completed_at) == bar.received_at
                    and capture_url_matches(capture.url, scope, bar)
                )
            ]
            if not matching:
                raise ValueError("history_observed_capture_scope_or_knowledge_invalid")
            capture = sorted(matching, key=lambda value: value.capture_id)[0]
            used_captures[capture.capture_id] = capture_payload(capture)
            bars.append(bar)
        # Re-read whole bytes at the exit: parsing a retained value never replaces SHA freshness.
        for ref, digest in originals:
            if hashlib.sha256(self.raw_reader(ref)).hexdigest() != digest:
                raise ValueError("history_observed_raw_changed_at_exit")
        physical_snapshot(session)
        require_snapshot(session, boundary)
        return tuple(bars), {
            "schema_version": "history-observed-selected-source.v1",
            "scope": scope.model_dump(mode="json"),
            "market_cutoff_at": cutoff.isoformat(),
            "data_information_asof": known.isoformat(),
            "bars": [bar.model_dump(mode="json") for bar in bars],
            "selected_row_fingerprints": [
                (row.candle_id, row_fingerprint(row)) for row in chosen.values()
            ],
            "retained_capture_originals": [used_captures[key] for key in sorted(used_captures)],
            "acquisition_captures": [],
            "source_admission": "not_evaluated",
        }


def load_window(session, scope, cutoff, known, capture_cache):
    if (
        type(capture_cache) is not CaptureCacheV2
        or type(capture_cache.issuer) is not HistoryOutcomeCaptureAuthorityV2
        or type(scope) is not MarketPathScope
    ):
        raise ValueError("history_outcome_exact_capture_capability_required")
    issuer = capture_cache.issuer
    issuer.authenticate(session, capture_cache)
    expected = _window_rows(session, scope, cutoff, known)
    result = _ReaderV2(capture_cache).load(session, scope, cutoff, known)
    issuer._windows = (*issuer._windows, (scope, cutoff, known, expected))
    issuer.authenticate(session, capture_cache)
    return result


LOADED_METHODS = tuple(
    (cls, name, getattr(cls, name), getattr(cls, name).__code__)
    for cls in (HistoryOutcomeCaptureAuthorityV2, _ReaderV2)
    for name in cls.__dict__
    if callable(getattr(cls, name)) and hasattr(getattr(cls, name), "__code__")
)
LOADED_FUNCTIONS = tuple(
    (name, globals()[name], globals()[name].__code__)
    for name in (
        "_capture_pool",
        "_window_rows",
        "load_window",
        "capture_payload",
        "capture_url_matches",
        "encoded",
        "physical_snapshot",
        "require_snapshot",
        "row_fingerprint",
        "snapshot_identity",
        "as_utc",
        "canonical_hash",
        "scope_matches",
    )
)
LOADED_CLASSES = tuple(
    (name, globals()[name])
    for name in (
        "HistoryOutcomeCaptureAuthorityV2",
        "CaptureCacheV2",
        "_ReaderV2",
        "HistoryForecastDispatch39Service",
        "MarketCandle",
        "MarketPathScope",
        "MarketCandleRecord",
    )
)
LOADED_TRANSFER = transfer
LOADED_TRANSFER_FUNCTIONS = tuple(
    (name, getattr(transfer, name), getattr(transfer, name).__code__)
    for name in ("native_equal39", "database_identity", "capture_schemas39", "verify_schemas39")
)

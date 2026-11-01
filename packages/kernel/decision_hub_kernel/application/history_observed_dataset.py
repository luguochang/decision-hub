"""Fresh originals in a caller-owned physical snapshot; no network or implicit DB."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import secrets
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from ..decision.path_risk import compute_path_risk
from ..persistence.db import RawCaptureRecord, as_utc, utcnow
from ..persistence.risk_records import MarketCandleRecord
from ..ports.history_observed_training import (
    HistoryObservedDatasetManifestV1,
    HistoryObservedFeatureV1,
    HistoryObservedLabelV1,
    HistoryObservedTrainingProtocolV1,
    HistoryObservedTrainingWindowV1,
    seal,
    verify_payload,
)
from ..ports.market_path import (
    MarketCandle,
    MarketPathScope,
    aware_utc,
    canonical_hash,
    scope_matches,
)


def physical_snapshot(session: Session) -> None:
    if session.new or session.dirty or session.deleted or not session.in_transaction():
        raise ValueError("history_observed_clean_caller_transaction_required")
    connection = session.connection()
    native = connection.connection.driver_connection
    if connection.dialect.name != "sqlite" or native is None or not native.in_transaction:
        raise ValueError("history_observed_physical_SQLite_transaction_required")


def _snapshot_fields(session: Session) -> tuple[object, object, object, object, int, int]:
    physical_snapshot(session)
    connection = session.connection()
    native = connection.connection.driver_connection
    if native is None:
        raise ValueError("history_observed_physical_SQLite_transaction_required")
    return (
        session,
        session.get_transaction(),
        connection,
        native,
        int(native.total_changes),
        threading.get_ident(),
    )


def snapshot_identity(session: Session) -> tuple[object, object, object, object, int, int, str]:
    fields = _snapshot_fields(session)
    marker = "hobs_snapshot_" + secrets.token_hex(16)
    session.connection().exec_driver_sql(f"SAVEPOINT {marker}")
    return (*fields, marker)


def require_snapshot(
    session: Session, boundary: tuple[object, ...], *, before_write: bool = False
) -> None:
    if _snapshot_fields(session) != boundary[:6]:
        raise ValueError("history_observed_snapshot_boundary_changed")
    # COMMIT/ROLLBACK by raw DBAPI can preserve the SA transaction object. A
    # private savepoint disappears with the physical transaction and proves it
    # was not replaced. SAVEPOINT/RELEASE write no rows or acquire a writer lock.
    try:
        session.connection().exec_driver_sql(f"RELEASE SAVEPOINT {boundary[6]}")
    except DBAPIError as exc:
        raise ValueError("history_observed_physical_snapshot_replaced") from exc
    if _snapshot_fields(session) != boundary[:6]:
        raise ValueError("history_observed_snapshot_boundary_changed")
    if before_write:
        # This caller will check again after its one declared flush. Renew the
        # verified marker in the same physical transaction for that exit check.
        session.connection().exec_driver_sql(f"SAVEPOINT {boundary[6]}")


def encoded(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )


def row_fingerprint(row: MarketCandleRecord) -> str:
    return canonical_hash(
        {
            "id": row.candle_id,
            "scope": row.scope_id,
            "venue": row.venue,
            "instrument": row.instrument,
            "price_type": row.price_type,
            "interval": row.interval_seconds,
            "open": as_utc(row.open_at).isoformat(),
            "close": as_utc(row.close_at).isoformat(),
            "received": as_utc(row.received_at).isoformat(),
            "known": as_utc(row.known_at).isoformat(),
            "revision": row.revision_hash,
            "candle_json": row.candle_json,
            "capture_ref": row.capture_ref,
            "capture_hash": row.capture_hash,
        }
    )


def capture_payload(capture: RawCaptureRecord) -> dict[str, object]:
    return {
        "capture_id": capture.capture_id,
        "context_id": capture.context_id,
        "url": capture.url,
        "body_ref": capture.body_ref,
        "body_hash": capture.body_hash,
        "byte_count": capture.byte_count,
        "adapter_version": capture.adapter_version,
        "status_code": capture.status_code,
        "error_code": capture.error_code,
        "media_type": capture.media_type,
        "started_at": as_utc(capture.started_at).isoformat(),
        "completed_at": as_utc(capture.completed_at).isoformat(),
        "recorded_at": as_utc(capture.recorded_at).isoformat(),
    }


@dataclass(frozen=True)
class HistoryObservedPreparation:
    """Unpersisted process-local input; carries no execution or fit permission."""

    dataset: HistoryObservedDatasetManifestV1
    producer: object
    thread_id: int
    payload_hash: str
    _auth: bytes = field(repr=False)

    def authenticated_by(self, expected: bytes) -> bool:
        return hmac.compare_digest(self._auth, expected)


def capture_url_matches(url: str, scope: MarketPathScope, bar: MarketCandle) -> bool:
    parts = urlsplit(url)
    query = parse_qs(parts.query, keep_blank_values=True)
    if (
        parts.scheme != "https"
        or parts.hostname != "www.okx.com"
        or parts.path != "/api/v5/market/history-candles"
        or parts.username is not None
        or parts.password is not None
        or parts.port not in (None, 443)
        or parts.fragment
        or set(query) != {"instId", "bar", "after", "limit"}
        or query["instId"] != [scope.instrument]
        or query["bar"] != ["1m"]
        or query["limit"] != ["300"]
        or len(query["after"]) != 1
    ):
        return False
    try:
        after = int(query["after"][0])
    except ValueError:
        return False
    return after % 60_000 == 0 and after > int(bar.open_at.timestamp() * 1000)


class HistoryObservedArchive:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def reference(self, value: object) -> tuple[str, str]:
        body = encoded(value)
        digest = hashlib.sha256(body).hexdigest()
        target = self.root / digest[:2] / f"{digest}.json"
        return str(target), digest

    def put(self, value: object) -> tuple[str, str]:
        body = encoded(value)
        ref, digest = self.reference(value)
        target = Path(ref)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with target.open("xb") as handle:
                handle.write(body)
        except FileExistsError:
            if target.read_bytes() != body:
                raise ValueError("history_observed_archive_collision") from None
        return str(target), digest

    def read(self, ref: str, digest: str) -> bytes:
        target = Path(ref).resolve()
        if not target.is_relative_to(self.root):
            raise ValueError("history_observed_archive_ref_outside_root")
        body = target.read_bytes()
        if hashlib.sha256(body).hexdigest() != digest:
            raise ValueError("history_observed_archive_hash_changed")
        return body


class HistoryObservedOriginals:
    def __init__(self, raw_reader: Callable[[str], bytes]) -> None:
        self.raw_reader = raw_reader

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
                captures = list(
                    session.scalars(
                        select(RawCaptureRecord)
                        .where(
                            RawCaptureRecord.body_ref == bar.capture_ref,
                            RawCaptureRecord.body_hash == bar.capture_hash,
                        )
                        .execution_options(populate_existing=True)
                    )
                )
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


class HistoryObservedDatasetBuilder:
    def __init__(
        self,
        originals: HistoryObservedOriginals,
        archive: HistoryObservedArchive,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.originals, self.archive, self.clock = originals, archive, clock
        self._preparation_key = secrets.token_bytes(32)

    def _preparation_auth(self, payload_hash: str, thread_id: int) -> bytes:
        """Internal issuer integrity; never persisted or accepted as fit permission."""
        return hmac.digest(
            self._preparation_key, encoded([payload_hash, thread_id, id(self)]), "sha256"
        )

    def prepare_verified(
        self,
        session: Session,
        protocol: HistoryObservedTrainingProtocolV1,
        dataset: HistoryObservedDatasetManifestV1,
    ) -> HistoryObservedPreparation:
        """Mint only after this snapshot freshly authenticates the stored exact dataset."""
        boundary = snapshot_identity(session)
        self.verify(session, protocol, dataset)
        require_snapshot(session, boundary)
        thread = threading.get_ident()
        return HistoryObservedPreparation(
            dataset,
            self,
            thread,
            dataset.payload_hash,
            self._preparation_auth(dataset.payload_hash, thread),
        )

    def window(
        self,
        session: Session,
        protocol: HistoryObservedTrainingProtocolV1,
        scope: MarketPathScope,
        cutoff: datetime,
        known: datetime,
        derived: datetime | None = None,
        *,
        persist: bool = True,
    ) -> HistoryObservedTrainingWindowV1:
        bars, source = self.originals.load(session, scope, cutoff, known)
        ref, digest = self.archive.put(source) if persist else self.archive.reference(source)
        feature_bars = [bar for bar in bars if bar.close_at <= cutoff]
        latest = feature_bars[-1].close_at if feature_bars else None
        lookup = {bar.close_at: bar for bar in feature_bars}
        times = (
            tuple(latest - timedelta(minutes=offset) for offset in range(30, -1, -1))
            if latest
            else ()
        )
        selected = [lookup[value] for value in times if value in lookup]
        rv = None
        with localcontext() as context:
            context.prec = 28
            context.rounding = ROUND_HALF_EVEN
            if (
                len(selected) == 31
                and latest is not None
                and (cutoff - latest).total_seconds() <= 180
            ):
                returns = [
                    math.log(float(right.close / left.close))
                    for left, right in zip(selected, selected[1:], strict=False)
                ]
                rv = Decimal(str(math.sqrt(sum(value * value for value in returns))))
            calculated = compute_path_risk(
                scope,
                bars,
                cutoff + timedelta(minutes=1),
                cutoff + timedelta(days=1, minutes=1),
                as_of=known,
            )
        path = HistoryObservedLabelV1.model_validate(
            {
                **{
                    name: getattr(calculated, name)
                    for name in HistoryObservedLabelV1.model_fields
                    if name != "missing_count"
                },
                "missing_count": len(calculated.missing_open_at),
            }
        )
        created = aware_utc(self.clock()) if derived is None else derived
        feature = seal(
            HistoryObservedFeatureV1,
            scope=scope,
            market_cutoff_at=cutoff,
            data_information_asof=known,
            derived_at=created,
            latest_close_at=latest,
            source_known_at=max((bar.known_at for bar in selected), default=None),
            complete_close_count=len(selected),
            realized_vol_30m=rv,
            source_manifest_ref=ref,
            source_manifest_hash=digest,
        )
        return seal(
            HistoryObservedTrainingWindowV1,
            scope=scope,
            registered_at=protocol.registered_at,
            market_cutoff_at=cutoff,
            data_information_asof=known,
            label_known_at=created,
            path=path,
            feature=feature,
            source_manifest_ref=ref,
            source_manifest_hash=digest,
        )

    def build(
        self,
        session: Session,
        protocol: HistoryObservedTrainingProtocolV1,
        scope: MarketPathScope,
        known: datetime,
    ) -> HistoryObservedPreparation:
        boundary = snapshot_identity(session)
        verify_payload(protocol)
        known, now = aware_utc(known), aware_utc(self.clock())
        if scope not in protocol.scopes or not protocol.registered_at <= known <= now:
            raise ValueError("history_observed_dataset_scope_or_future_cutoff")
        windows = tuple(
            self.window(session, protocol, scope, cutoff, known)
            for cutoff in protocol.daily_cutoffs
        )
        derived = aware_utc(self.clock())
        value = seal(
            HistoryObservedDatasetManifestV1,
            registration_id=protocol.registration_id,
            protocol_hash=protocol.payload_hash,
            scope=scope,
            registered_at=protocol.registered_at,
            data_information_asof=known,
            derived_at=derived,
            sealed_at=aware_utc(self.clock()),
            windows=windows,
        )
        require_snapshot(session, boundary)
        thread_id = threading.get_ident()
        return HistoryObservedPreparation(
            value,
            self,
            thread_id,
            value.payload_hash,
            self._preparation_auth(value.payload_hash, thread_id),
        )

    def confirm(self, session: Session, prepared: HistoryObservedPreparation) -> None:
        """Writer rechecks only selected metadata/readset and whole SHA, never 90d maths."""
        boundary = snapshot_identity(session)
        dataset = verify_payload(prepared.dataset)
        if (
            prepared.producer is not self
            or prepared.thread_id != threading.get_ident()
            or prepared.payload_hash != dataset.payload_hash
            or not prepared.authenticated_by(
                self._preparation_auth(dataset.payload_hash, prepared.thread_id)
            )
        ):
            raise ValueError("history_observed_preparation_identity_changed")
        expected: dict[str, str] = {}
        captures: dict[str, dict[str, object]] = {}
        raw_refs: dict[str, str] = {}
        for window in dataset.windows:
            source = json.loads(
                self.archive.read(window.source_manifest_ref, window.source_manifest_hash)
            )
            for identity, digest in source["selected_row_fingerprints"]:
                if identity in expected and expected[identity] != digest:
                    raise ValueError("history_observed_readset_conflicting_row")
                expected[identity] = digest
            for capture in source["retained_capture_originals"]:
                captures[capture["capture_id"]] = capture
                raw_refs[capture["body_ref"]] = capture["body_hash"]
        selected: dict[datetime, MarketCandleRecord] = {}
        rows = session.scalars(
            select(MarketCandleRecord)
            .where(
                MarketCandleRecord.scope_id == dataset.scope.scope_id,
                MarketCandleRecord.open_at
                >= dataset.windows[0].market_cutoff_at - timedelta(minutes=34),
                MarketCandleRecord.close_at
                <= dataset.windows[-1].market_cutoff_at + timedelta(days=1, minutes=1),
                MarketCandleRecord.known_at <= dataset.data_information_asof,
            )
            .order_by(MarketCandleRecord.open_at, MarketCandleRecord.known_at)
            .execution_options(populate_existing=True)
        )
        for row in rows:
            old = selected.get(row.open_at)
            if (
                old is not None
                and old.known_at == row.known_at
                and old.revision_hash != row.revision_hash
            ):
                raise ValueError("history_observed_conflicting_revision_clock")
            selected[row.open_at] = row
        if {row.candle_id: row_fingerprint(row) for row in selected.values()} != expected:
            raise ValueError("history_observed_selected_readset_changed")
        for identity, expected_capture in captures.items():
            actual = session.get(RawCaptureRecord, identity, populate_existing=True)
            if actual is None or capture_payload(actual) != expected_capture:
                raise ValueError("history_observed_capture_readset_changed")
        for ref, digest in raw_refs.items():
            if hashlib.sha256(self.originals.raw_reader(ref)).hexdigest() != digest:
                raise ValueError("history_observed_raw_changed_at_writer")
        require_snapshot(session, boundary)

    def verify(
        self,
        session: Session,
        protocol: HistoryObservedTrainingProtocolV1,
        dataset: HistoryObservedDatasetManifestV1,
    ) -> None:
        boundary = snapshot_identity(session)
        verify_payload(dataset)
        if (
            (dataset.registration_id, dataset.protocol_hash, dataset.registered_at)
            != (protocol.registration_id, protocol.payload_hash, protocol.registered_at)
            or dataset.scope not in protocol.scopes
            or dataset.sealed_at > aware_utc(self.clock())
        ):
            raise ValueError("history_observed_dataset_registration_or_future_seal")
        for window in dataset.windows:
            original = self.archive.read(window.source_manifest_ref, window.source_manifest_hash)
            rebuilt = self.window(
                session,
                protocol,
                dataset.scope,
                window.market_cutoff_at,
                dataset.data_information_asof,
                window.label_known_at,
                persist=False,
            )
            if (
                hashlib.sha256(original).hexdigest() != rebuilt.source_manifest_hash
                or rebuilt.source_manifest_ref != window.source_manifest_ref
                or rebuilt != window
            ):
                raise ValueError("history_observed_selected_originals_changed")
        require_snapshot(session, boundary)

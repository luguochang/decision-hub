"""Explicit fixed-window plan and cross-registration original readset producer."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import secrets
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.provider_adapters.research.raw_capture import RawBodyArchive

from ..persistence.db import as_utc
from ..persistence.history_dataset_v2_records import HistoryDatasetV2OriginalRecord as Original
from ..persistence.history_dataset_v2_records import HistoryMissingV2AttemptRecord as Attempt
from ..persistence.history_dataset_v2_records import HistoryMissingV2JobRecord as Job
from ..ports.history_dataset_v2 import HistoryDatasetOriginalV2, history_v2_id
from ..ports.history_observed_training import (
    HistoryObservedTrainingProtocolV1,
    seal,
    verify_payload,
)
from ..ports.market_path import MarketCandle, aware_utc, canonical_hash, spot_scope
from .history_observed_dataset import (
    HistoryObservedArchive,
    encoded,
    require_snapshot,
    snapshot_identity,
)
from .history_observed_ledger import SOURCE_FILES

LOADED_SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
NEW_FILES = (
    "packages/kernel/decision_hub_kernel/ports/history_dataset_v2.py",
    "packages/kernel/decision_hub_kernel/persistence/history_dataset_v2_records.py",
    "packages/kernel/decision_hub_kernel/application/history_dataset_v2.py",
    "packages/kernel/decision_hub_kernel/application/history_missing_acquisition_v2.py",
    "packages/provider_adapters/market/history_missing_transport_v2.py",
    "apps/hub_worker/history_dataset_v2_cli.py",
)
LEGACY_TABLES = {
    "market_history_training_records": "record_id",
    "market_history_acquisition_jobs": "job_id",
    "raw_captures": "capture_id",
    "market_candles": "candle_id",
    "market_history_v2_originals": "original_id",
    "market_history_v2_attempts": "attempt_id",
    "market_history_v2_jobs": "job_id",
}
ROW_PROJECTIONS = {
    "market_history_v2_jobs": "job_id,epoch_id,plan_id,scope_id,start_at,end_at,max_pages"
}


@dataclass(frozen=True)
class HistoryDatasetV2Prepared:
    value: HistoryDatasetOriginalV2
    source_body: bytes
    files: tuple[tuple[str, str], ...]
    rows: tuple[tuple[str, str, str], ...]
    issuer: object
    thread_id: int
    authentication: bytes


class HistoryDatasetV2Service:
    def __init__(
        self,
        commands: Any,
        data_directory: Path,
        *,
        source_root: Path | None = None,
        clock: Any = None,
        fixture_mode: bool = False,
    ) -> None:
        self.commands = commands
        self.data_directory = data_directory.resolve(strict=True)
        self.source_root = (source_root or Path(__file__).resolve().parents[4]).resolve(strict=True)
        self.archive = HistoryObservedArchive(self.data_directory / "history-dataset-v2")
        self.raw = RawBodyArchive(self.data_directory / "raw-captures")
        if clock is not None and not fixture_mode:
            raise ValueError("history_v2_injected_clock_requires_private_fixture")
        self.clock = clock or (lambda: datetime.now(UTC))
        self.fixture_mode = fixture_mode
        self._key = secrets.token_bytes(32)

    def now(self) -> datetime:
        return aware_utc(self.clock())

    def require_sources(
        self, expected: tuple[tuple[str, str], ...] | None = None
    ) -> tuple[tuple[str, str], ...]:
        if hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != LOADED_SOURCE_SHA256:
            raise ValueError("history_v2_loaded_source_changed")
        values = tuple(
            (name, hashlib.sha256((self.source_root / name).read_bytes()).hexdigest())
            for name in (
                *SOURCE_FILES,
                *NEW_FILES,
                "packages/kernel/decision_hub_kernel/application/history_observed_commands.py",
            )
        )
        if expected is not None and values != expected:
            raise ValueError("history_v2_source_changed")
        return values

    def _source_body(self, sources: tuple[tuple[str, str], ...]) -> bytes:
        return encoded(
            {
                "schema_version": "history-v2-source-original.v1",
                "files": [
                    {
                        "path": name,
                        "sha256": digest,
                        "bytes_base64": base64.b64encode(
                            (self.source_root / name).read_bytes()
                        ).decode(),
                    }
                    for name, digest in sources
                ],
            }
        )

    @staticmethod
    def _row(session: Session, table: str, key: str) -> dict[str, Any]:
        if table not in LEGACY_TABLES:
            raise ValueError("history_v2_table_not_allowed")
        row = (
            session.connection()
            .exec_driver_sql(
                f"SELECT {ROW_PROJECTIONS.get(table, '*')} FROM {table} "
                f"WHERE {LEGACY_TABLES[table]}=? LIMIT 2",
                (key,),
            )
            .mappings()
            .all()
        )
        if len(row) != 1:
            raise ValueError("history_v2_original_row_missing")
        return dict(row[0])

    @staticmethod
    def _dt(value: Any) -> datetime:
        parsed = datetime.fromisoformat(str(value))
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else aware_utc(parsed)

    def _raw_body(self, ref: str, digest: str) -> bytes:
        if ref != f"raw://sha256/{digest}":
            raise ValueError("history_v2_raw_ref_identity_invalid")
        body = self.raw.read(ref)
        if not 0 < len(body) <= 1_000_000 or hashlib.sha256(body).hexdigest() != digest:
            raise ValueError("history_v2_raw_original_changed")
        return body

    def _pairs(
        self,
        body: bytes,
        scope_id: str,
        start: datetime,
        end: datetime,
        cursor: str,
        received: datetime,
    ) -> list[list[Any]]:
        scope = next(
            (
                value
                for value in (spot_scope("BTC-USDT"), spot_scope("ETH-USDT"))
                if value.scope_id == scope_id
            ),
            None,
        )
        if scope is None:
            raise ValueError("history_v2_scope_invalid")
        payload = json.loads(body)
        rows = payload.get("data") if isinstance(payload, dict) else None
        if (
            not isinstance(payload, dict)
            or str(payload.get("code")) != "0"
            or not isinstance(rows, list)
            or not 1 <= len(rows) <= 300
        ):
            raise ValueError("history_v2_raw_page_shape_invalid")
        result = []
        for index, row in enumerate(rows):
            if not isinstance(row, list) or len(row) != 9:
                raise ValueError("history_v2_raw_row_shape_invalid")
            stamp = int(row[0])
            if stamp % 60_000 or stamp >= int(cursor):
                raise ValueError("history_v2_raw_cursor_invalid")
            opened = datetime.fromtimestamp(stamp / 1000, UTC)
            if not start <= opened < end:
                continue
            if str(row[8]) != "1":
                raise ValueError("history_v2_raw_bar_unclosed")
            bar = MarketCandle(
                scope_id=scope_id,
                venue=scope.venue,
                instrument=scope.instrument,
                price_type=scope.price_type,
                open_at=opened,
                close_at=opened + timedelta(minutes=1),
                open=Decimal(str(row[1])),
                high=Decimal(str(row[2])),
                low=Decimal(str(row[3])),
                close=Decimal(str(row[4])),
                volume_base=Decimal(str(row[5])),
                volume_quote=Decimal(str(row[7])),
                received_at=received,
                row_index=index,
            )
            result.append([stamp, canonical_hash(bar.semantic_payload())])
        return sorted(result)

    @staticmethod
    def _required(cutoffs: tuple[datetime, ...]) -> set[int]:
        result: set[int] = set()
        for value in cutoffs:
            stamp = int(value.timestamp() * 1000)
            result.update(stamp - index * 60_000 for index in range(1, 32))
            result.update(stamp + index * 60_000 for index in range(1, 1441))
        return result

    @staticmethod
    def _intervals(scope: str, missing: set[int]) -> list[dict[str, Any]]:
        ordered = sorted(missing)
        groups: list[list[int]] = []
        for stamp in ordered:
            if not groups or stamp != groups[-1][-1] + 60_000:
                groups.append([stamp])
            else:
                groups[-1].append(stamp)
        return [
            {
                "scope_id": scope,
                "start_at": datetime.fromtimestamp(group[0] / 1000, UTC).isoformat(),
                "end_at": datetime.fromtimestamp((group[-1] + 60_000) / 1000, UTC).isoformat(),
                "cursor": str(group[-1] + 60_000),
                "missing_minutes": len(group),
                "max_pages": math.ceil(len(group) / 300) + 2,
            }
            for group in groups
        ]

    def prepare_original(
        self,
        session: Session,
        *,
        kind: str,
        owner_claim: str,
        request_id: str,
        plan_id: str,
        known_asof: datetime,
        cutoffs: tuple[datetime, ...],
        provenance: dict[str, Any],
        files: tuple[tuple[str, str], ...] = (),
        rows: tuple[tuple[str, str, str], ...] = (),
    ) -> HistoryDatasetV2Prepared:
        boundary = snapshot_identity(session)
        sources = self.require_sources()
        source_body = self._source_body(sources)
        source_ref, source_hash = self.archive.reference(json.loads(source_body))
        now = self.now()
        value = seal(
            HistoryDatasetOriginalV2,
            kind=kind,
            original_id=history_v2_id(kind, owner_claim, request_id),
            owner_claim=owner_claim,
            request_id=request_id,
            plan_id=plan_id,
            known_asof=known_asof,
            produced_at=now,
            recorded_at=now,
            cutoffs=cutoffs,
            scope_ids=(spot_scope("BTC-USDT").scope_id, spot_scope("ETH-USDT").scope_id),
            source_files=sources,
            source_original_ref=source_ref,
            source_original_hash=source_hash,
            provenance=provenance,
        )
        prepared = HistoryDatasetV2Prepared(
            value, source_body, files, rows, self, threading.get_ident(), b""
        )
        result = HistoryDatasetV2Prepared(
            value, source_body, files, rows, self, prepared.thread_id, self._auth(prepared)
        )
        self._confirm(result)
        require_snapshot(session, boundary)
        return result

    def _auth(self, prepared: HistoryDatasetV2Prepared) -> bytes:
        return hmac.digest(
            self._key,
            encoded(
                {
                    "value": prepared.value.model_dump(mode="json"),
                    "source_sha": hashlib.sha256(prepared.source_body).hexdigest(),
                    "files": prepared.files,
                    "rows": prepared.rows,
                    "thread": prepared.thread_id,
                    "issuer": id(self),
                }
            ),
            "sha256",
        )

    def _confirm(self, prepared: HistoryDatasetV2Prepared, session: Session | None = None) -> None:
        if (
            prepared.issuer is not self
            or prepared.thread_id != threading.get_ident()
            or not hmac.compare_digest(prepared.authentication, self._auth(prepared))
        ):
            raise ValueError("history_v2_preparation_changed")
        self.require_sources(prepared.value.source_files)
        for path, digest in prepared.files:
            if hashlib.sha256(Path(path).read_bytes()).hexdigest() != digest:
                raise ValueError("history_v2_full_original_changed")
        if session is not None:
            self._confirm_rows(session, prepared.rows)

    def _confirm_rows(self, session: Session, values: tuple[tuple[str, str, str], ...]) -> None:
        groups: dict[str, dict[str, str]] = {}
        for table, key, fingerprint in values:
            if table not in LEGACY_TABLES:
                raise ValueError("history_v2_table_not_allowed")
            groups.setdefault(table, {})[key] = fingerprint
        for table, expected in groups.items():
            keys = list(expected)
            found = 0
            for offset in range(0, len(keys), 700):
                batch = keys[offset : offset + 700]
                rows = (
                    session.connection()
                    .exec_driver_sql(
                        f"SELECT {ROW_PROJECTIONS.get(table, '*')} FROM {table} "
                        f"WHERE {LEGACY_TABLES[table]} IN "
                        f"({','.join('?' for _ in batch)})",
                        tuple(batch),
                    )
                    .mappings()
                    .all()
                )
                found += len(rows)
                if any(
                    canonical_hash(dict(row)) != expected[row[LEGACY_TABLES[table]]] for row in rows
                ):
                    raise ValueError("history_v2_original_metadata_changed")
            if found != len(expected):
                raise ValueError("history_v2_original_metadata_missing")

    def record_original(
        self, session: Session, prepared: HistoryDatasetV2Prepared
    ) -> HistoryDatasetOriginalV2:
        boundary = snapshot_identity(session)
        self._confirm(prepared, session)
        existing = session.get(Original, prepared.value.original_id, populate_existing=True)
        if existing is not None:
            first = self.read_original(session, existing.original_id, prepared.value.owner_claim)
            if (
                first.kind,
                first.request_id,
                first.plan_id,
                first.known_asof,
                first.provenance,
            ) != (
                prepared.value.kind,
                prepared.value.request_id,
                prepared.value.plan_id,
                prepared.value.known_asof,
                prepared.value.provenance,
            ):
                raise ValueError("history_v2_request_conflict")
            require_snapshot(session, boundary)
            return first
        value = seal(
            HistoryDatasetOriginalV2,
            **{**prepared.value.model_dump(exclude={"payload_hash"}), "recorded_at": self.now()},
        )
        if self.archive.put(json.loads(prepared.source_body)) != (
            value.source_original_ref,
            value.source_original_hash,
        ):
            raise ValueError("history_v2_source_original_identity_changed")
        ref, digest = self.archive.put(value.model_dump(mode="json"))
        self._confirm(prepared, session)
        require_snapshot(session, boundary, before_write=True)
        session.add(
            Original(
                original_id=value.original_id,
                kind=value.kind,
                owner_claim=value.owner_claim,
                request_id=value.request_id,
                plan_id=value.plan_id,
                payload_json=encoded(value.model_dump(mode="json")).decode(),
                payload_hash=value.payload_hash,
                original_ref=ref,
                original_hash=digest,
                recorded_at=value.recorded_at,
            )
        )
        session.flush()
        self._confirm(prepared, session)
        require_snapshot(session, (*boundary[:4], boundary[4] + 1, *boundary[5:]))
        return value

    def read_original(
        self, session: Session, original_id: str, owner_claim: str
    ) -> HistoryDatasetOriginalV2:
        boundary = snapshot_identity(session)
        self.require_sources()
        row = session.get(Original, original_id, populate_existing=True)
        if row is None or row.owner_claim != owner_claim:
            raise ValueError("history_v2_original_missing_or_owner_conflict")
        body = self.archive.read(row.original_ref, row.original_hash)
        value = verify_payload(HistoryDatasetOriginalV2.model_validate_json(body))
        if (
            row.original_id,
            row.kind,
            row.owner_claim,
            row.request_id,
            row.plan_id,
            row.payload_hash,
            row.payload_json.encode(),
            as_utc(row.recorded_at),
        ) != (
            value.original_id,
            value.kind,
            value.owner_claim,
            value.request_id,
            value.plan_id,
            value.payload_hash,
            body,
            value.recorded_at,
        ) or value.recorded_at > self.now():
            raise ValueError("history_v2_saved_original_identity_changed")
        source = json.loads(
            self.archive.read(value.source_original_ref, value.source_original_hash)
        )
        entries = tuple((item["path"], item["sha256"]) for item in source["files"])
        if entries != value.source_files or any(
            hashlib.sha256(base64.b64decode(item["bytes_base64"], validate=True)).hexdigest()
            != item["sha256"]
            for item in source["files"]
        ):
            raise ValueError("history_v2_saved_source_original_changed")
        # The supported immutable rule version is interpreted by this reader;
        # retained producer bytes are evidence, never executed or updated.
        for path, digest in value.provenance.get("file_readset", ()):
            if hashlib.sha256(Path(path).read_bytes()).hexdigest() != digest:
                raise ValueError("history_v2_saved_readset_original_changed")
        self._confirm_rows(session, tuple(value.provenance.get("row_readset", ())))
        self.require_sources()
        if self.archive.read(row.original_ref, row.original_hash) != body:
            raise ValueError("history_v2_saved_original_changed_at_exit")
        require_snapshot(session, boundary)
        return value

    def prepare_plan(
        self,
        session: Session,
        *,
        census_path: Path,
        census_hash: str,
        owner_claim: str,
        request_id: str,
    ) -> HistoryDatasetV2Prepared:
        boundary = snapshot_identity(session)
        body = census_path.read_bytes()
        if hashlib.sha256(body).hexdigest() != census_hash:
            raise ValueError("history_v2_census_original_changed")
        census = json.loads(body)
        if (
            census["schema_version"] != "history-checkpoint-raw-union-census.v1"
            or not census["whole_original_exit_proof"]
            or census["SQL_changes"]
            or census["query_only"] != 1
        ):
            raise ValueError("history_v2_census_not_verified")
        known = self._dt(census["known_asof"])
        if known > self.now() or len(census["protocols"]) != 2:
            raise ValueError("history_v2_census_clock_or_registration_invalid")
        files: dict[str, str] = {str(census_path.resolve()): census_hash}
        rowset: dict[tuple[str, str], str] = {}
        protocol_ids = tuple(sorted(census["protocols"]))
        cutoffs = tuple(
            self._dt(value) for value in census["protocols"][protocol_ids[0]]["fixed_UTC_cutoffs"]
        )
        sources = dict(self.require_sources())
        for rid in protocol_ids:
            row = self._row(session, "market_history_training_records", rid)
            rowset["market_history_training_records", rid] = canonical_hash(row)
            raw = Path(row["original_ref"]).read_bytes()
            if (
                hashlib.sha256(raw).hexdigest() != row["original_hash"]
                or raw != row["payload_json"].encode()
            ):
                raise ValueError("history_v2_protocol_original_changed")
            protocol = verify_payload(HistoryObservedTrainingProtocolV1.model_validate_json(raw))
            if (
                protocol.registration_id != rid
                or protocol.payload_hash != census["protocols"][rid]["protocol_hash"]
                or protocol.daily_cutoffs != cutoffs
                or any(sources[name] != digest for name, digest in protocol.implementation_files)
            ):
                raise ValueError("history_v2_protocol_source_or_window_changed")
            files[row["original_ref"]] = row["original_hash"]
        jobs = {item["job_id"]: item for item in census["jobs"]}
        pages, parsed_jobs, coverage = (
            [],
            {},
            {scope.scope_id: {} for scope in (spot_scope("BTC-USDT"), spot_scope("ETH-USDT"))},
        )
        for proof in census["pages"]:
            job_id = proof["job_id"]
            if proof["verification"] != "verified" or proof["registration_id"] not in protocol_ids:
                raise ValueError("history_v2_page_witness_not_verified")
            if job_id not in parsed_jobs:
                job = self._row(session, "market_history_acquisition_jobs", job_id)
                if canonical_hash(job) != jobs[job_id]["job_metadata_sha256"]:
                    raise ValueError("history_v2_checkpoint_metadata_changed")
                rowset["market_history_acquisition_jobs", job_id] = canonical_hash(job)
                checkpoint = json.loads(job["checkpoint_json"])
                if (
                    canonical_hash(
                        {key: value for key, value in checkpoint.items() if key != "payload_hash"}
                    )
                    != checkpoint["payload_hash"]
                ):
                    raise ValueError("history_v2_checkpoint_seal_changed")
                parsed_jobs[job_id] = checkpoint
            page = next(
                (
                    value
                    for value in parsed_jobs[job_id]["pages"]
                    if canonical_hash(value) == proof["checkpoint_page_hash"]
                ),
                None,
            )
            if page is None:
                raise ValueError("history_v2_checkpoint_page_missing")
            capture = self._row(session, "raw_captures", proof["capture_id"])
            rowset["raw_captures", proof["capture_id"]] = canonical_hash(capture)
            if (
                (
                    capture["context_id"],
                    capture["body_ref"],
                    capture["body_hash"],
                    capture["byte_count"],
                    capture["status_code"],
                    capture["adapter_version"],
                    capture["error_code"],
                )
                != (
                    job_id,
                    proof["capture_ref"],
                    proof["body_sha256"],
                    proof["byte_count"],
                    200,
                    "okx-closed-spot-path.v1",
                    None,
                )
                or self._dt(capture["completed_at"]) != self._dt(page["received_at"])
                or not self._dt(capture["started_at"])
                <= self._dt(capture["completed_at"])
                <= self._dt(capture["recorded_at"])
                <= known
            ):
                raise ValueError("history_v2_capture_witness_changed")
            raw = self._raw_body(page["capture_ref"], page["capture_hash"])
            day = self._dt(proof["cutoff_at"])
            start = (
                day - timedelta(minutes=31)
                if page["phase"] == "feature"
                else day + timedelta(minutes=1)
            )
            end = day if page["phase"] == "feature" else day + timedelta(days=1, minutes=1)
            pairs = self._pairs(
                raw, proof["scope_id"], start, end, page["cursor"], self._dt(page["received_at"])
            )
            if pairs != page["rows"]:
                raise ValueError("history_v2_raw_semantic_witness_changed")
            digest = page["capture_hash"]
            files[str(self.data_directory / "raw-captures" / digest[:2] / digest)] = digest
            witness = {
                "origin": "registered_v1",
                "registration_id": proof["registration_id"],
                "job_id": job_id,
                "scope_id": proof["scope_id"],
                "start_at": start.isoformat(),
                "end_at": end.isoformat(),
                "page": page,
                "capture": capture,
                "producer_sources": census["current_implementation_files"],
            }
            pages.append(witness)
            for stamp, revision in pairs:
                target = coverage[proof["scope_id"]]
                if stamp in target and target[stamp] != revision:
                    raise ValueError("history_v2_cross_registration_revision_conflict")
                target[stamp] = revision
        required = self._required(cutoffs)
        intervals = [
            item
            for scope_id, values in coverage.items()
            for item in self._intervals(scope_id, required - values.keys())
        ]
        plan_id = history_v2_id("plan", owner_claim, request_id)
        provenance = {
            "census_hash": census_hash,
            "base_registration_ids": protocol_ids,
            "original_witnesses": pages,
            "missing_intervals": intervals,
            "file_readset": sorted(files.items()),
            "row_readset": [
                (table, key, fingerprint) for (table, key), fingerprint in sorted(rowset.items())
            ],
            "fixture_mode": self.fixture_mode,
            "global_first_known_verified": False,
            "legacy_fit_authorized": False,
        }
        prepared = self.prepare_original(
            session,
            kind="plan",
            owner_claim=owner_claim,
            request_id=request_id,
            plan_id=plan_id,
            known_asof=known,
            cutoffs=cutoffs,
            provenance=provenance,
            files=tuple(sorted(files.items())),
            rows=tuple(provenance["row_readset"]),
        )
        require_snapshot(session, boundary)
        return prepared

    def prepare_freeze(
        self,
        session: Session,
        *,
        owner_claim: str,
        request_id: str,
        plan_id: str,
        epoch_ids: tuple[str, ...] = (),
    ) -> HistoryDatasetV2Prepared:
        boundary = snapshot_identity(session)
        if len(epoch_ids) > 32 or len(set(epoch_ids)) != len(epoch_ids):
            raise ValueError("history_v2_freeze_epoch_set_invalid")
        plan = self.read_original(session, plan_id, owner_claim)
        if plan.kind != "plan" or plan.provenance["fixture_mode"] != self.fixture_mode:
            raise ValueError("history_v2_plan_or_fixture_identity_invalid")
        known = self.now()
        witnesses = list(plan.provenance["original_witnesses"])
        files = dict(plan.provenance["file_readset"])
        rowset = {
            (table, key): fingerprint for table, key, fingerprint in plan.provenance["row_readset"]
        }

        def original_readset(value):
            row = self._row(session, "market_history_v2_originals", value.original_id)
            rowset["market_history_v2_originals", value.original_id] = canonical_hash(row)
            files[row["original_ref"]] = row["original_hash"]
            files[value.source_original_ref] = value.source_original_hash

        original_readset(plan)
        epoch_hashes = []
        for epoch_id in epoch_ids:
            epoch = self.read_original(session, epoch_id, owner_claim)
            if (
                epoch.kind != "epoch"
                or epoch.plan_id != plan_id
                or epoch.provenance["plan_hash"] != plan.payload_hash
                or epoch.provenance["fixture_mode"] != self.fixture_mode
            ):
                raise ValueError("history_v2_epoch_plan_identity_invalid")
            epoch_hashes.append((epoch_id, epoch.payload_hash))
            original_readset(epoch)
            for attempt in session.scalars(
                select(Attempt).where(Attempt.epoch_id == epoch_id, Attempt.state == "captured")
            ):
                if attempt.fixture_mode != self.fixture_mode or attempt.result_json is None:
                    raise ValueError("history_v2_attempt_fixture_or_result_missing")
                result = json.loads(attempt.result_json)
                if canonical_hash(result) != attempt.result_hash:
                    raise ValueError("history_v2_attempt_original_changed")
                witness = result.get("witness")
                if witness is None:
                    raise ValueError("history_v2_attempt_witness_missing")
                job = session.get(Job, attempt.job_id, populate_existing=True)
                interval = next(
                    (
                        item
                        for item in epoch.provenance["intervals"]
                        if "hgap_" + canonical_hash([epoch_id, item])[:48] == attempt.job_id
                    ),
                    None,
                )
                if (
                    job is None
                    or interval is None
                    or (
                        job.epoch_id != epoch_id
                        or job.plan_id != plan_id
                        or job.scope_id != interval["scope_id"]
                        or self._dt(job.start_at) != self._dt(interval["start_at"])
                        or self._dt(job.end_at) != self._dt(interval["end_at"])
                        or job.max_pages != interval["max_pages"]
                        or witness["origin"] != "missing_v2"
                        or witness["epoch_id"] != epoch_id
                        or witness["attempt_id"] != attempt.attempt_id
                        or result["attempt_id"] != attempt.attempt_id
                        or witness["job_id"] != attempt.job_id
                        or witness["scope_id"] != job.scope_id
                        or self._dt(witness["start_at"]) != self._dt(job.start_at)
                        or self._dt(witness["end_at"]) != self._dt(job.end_at)
                        or witness["page"]["phase"] != "gap"
                        or witness["page"]["cursor"] != attempt.cursor
                        or witness["capture"]["capture_id"] != attempt.capture_id
                        or witness["capture"]["context_id"] != attempt.attempt_id
                        or witness["capture"]["adapter_version"] != "history-missing-capture.v2"
                        or canonical_hash(witness["producer_sources"])
                        != canonical_hash(epoch.source_files)
                        or witness["capture"] != result["capture"]
                        or witness["page"]["rows"] != result["rows"]
                        or witness["page"]["next_cursor"] != result["next_cursor"]
                        or result["error_code"] is not None
                        or result["fixture_mode"] != self.fixture_mode
                    )
                ):
                    raise ValueError("history_v2_attempt_epoch_capture_witness_changed")
                witnesses.append(witness)
                rowset["market_history_v2_jobs", attempt.job_id] = canonical_hash(
                    self._row(session, "market_history_v2_jobs", attempt.job_id)
                )
                rowset["market_history_v2_attempts", attempt.attempt_id] = canonical_hash(
                    self._row(session, "market_history_v2_attempts", attempt.attempt_id)
                )
        by_minute: dict[tuple[str, int], list[dict[str, Any]]] = {}
        for index, witness in enumerate(witnesses):
            page, capture = witness["page"], witness["capture"]
            actual = self._row(session, "raw_captures", capture["capture_id"])
            rowset["raw_captures", capture["capture_id"]] = canonical_hash(actual)
            date_fields = {"started_at", "completed_at", "recorded_at"}
            same_capture = set(actual) == set(capture) and all(
                (
                    self._dt(actual[key]) == self._dt(capture[key])
                    if key in date_fields
                    else actual[key] == capture[key]
                )
                for key in actual
            )
            if not same_capture or not self._dt(capture["recorded_at"]) <= known:
                raise ValueError("history_v2_freeze_capture_identity_changed")
            raw = self._raw_body(page["capture_ref"], page["capture_hash"])
            if (
                capture["body_ref"] != page["capture_ref"]
                or capture["body_hash"] != page["capture_hash"]
                or capture["byte_count"] != len(raw)
                or capture["status_code"] != 200
                or capture["error_code"] is not None
                or self._dt(capture["completed_at"]) != self._dt(page["received_at"])
                or not self._dt(capture["started_at"])
                <= self._dt(capture["completed_at"])
                <= self._dt(capture["recorded_at"])
                <= known
            ):
                raise ValueError("history_v2_freeze_capture_raw_clock_changed")
            start, end = self._dt(witness["start_at"]), self._dt(witness["end_at"])
            pairs = self._pairs(
                raw, witness["scope_id"], start, end, page["cursor"], self._dt(page["received_at"])
            )
            if pairs != page["rows"]:
                raise ValueError("history_v2_freeze_raw_semantics_changed")
            digest = page["capture_hash"]
            files[str(self.data_directory / "raw-captures" / digest[:2] / digest)] = digest
            for stamp, revision in pairs:
                by_minute.setdefault((witness["scope_id"], stamp), []).append(
                    {
                        "revision": revision,
                        "witness_index": index,
                        "capture": capture,
                    }
                )
        selected: dict[str, dict[int, dict[str, Any]]] = {
            scope_id: {} for scope_id in plan.scope_ids
        }
        required = self._required(plan.cutoffs)
        candidates: dict[str, list[tuple[int, str, list[dict[str, Any]]]]] = {
            scope_id: [] for scope_id in plan.scope_ids
        }
        for scope_id in plan.scope_ids:
            for stamp in sorted(required):
                options = by_minute.get((scope_id, stamp), [])
                if not options:
                    continue
                revisions = {item["revision"] for item in options}
                if len(revisions) != 1:
                    raise ValueError("history_v2_freeze_revision_conflict")
                revision = next(iter(revisions))
                candidates[scope_id].append((stamp, revision, options))
            values = candidates[scope_id]
            for offset in range(0, len(values), 700):
                batch = values[offset : offset + 700]
                keys = [
                    "candle_" + canonical_hash([scope_id, revision])[:48]
                    for _, revision, _ in batch
                ]
                stored = (
                    session.connection()
                    .exec_driver_sql(
                        "SELECT * FROM market_candles WHERE candle_id IN "
                        f"({','.join('?' for _ in keys)})",
                        tuple(keys),
                    )
                    .mappings()
                    .all()
                )
                rows = {row["candle_id"]: dict(row) for row in stored}
                for (stamp, revision, options), key in zip(batch, keys, strict=True):
                    row = rows.get(key)
                    if row is None:
                        continue
                    bar = MarketCandle.model_validate_json(row["candle_json"])
                    if (
                        bar.scope_id,
                        int(bar.open_at.timestamp() * 1000),
                        canonical_hash(bar.semantic_payload()),
                        bar.known_at,
                        bar.capture_ref,
                        bar.capture_hash,
                    ) != (
                        scope_id,
                        stamp,
                        revision,
                        self._dt(row["known_at"]),
                        row["capture_ref"],
                        row["capture_hash"],
                    ) or bar.known_at > known:
                        raise ValueError("history_v2_first_known_candle_identity_changed")
                    origins = [
                        option
                        for option in options
                        if option["capture"]["body_ref"] == bar.capture_ref
                        and option["capture"]["body_hash"] == bar.capture_hash
                        and self._dt(option["capture"]["completed_at"]) == bar.received_at
                        and self._dt(option["capture"]["recorded_at"]) <= bar.known_at
                    ]
                    if not origins:
                        continue
                    origin = origins[0]
                    rowset["market_candles", key] = canonical_hash(row)
                    selected[scope_id][stamp] = {
                        "candle_id": key,
                        "semantic_revision": revision,
                        "first_known_at": bar.known_at.isoformat(),
                        "first_received_at": bar.received_at.isoformat(),
                        "first_accepted_at": bar.accepted_at.isoformat()
                        if bar.accepted_at
                        else None,
                        "witness_index": origin["witness_index"],
                    }
        windows = []
        for scope_id in plan.scope_ids:
            for day in plan.cutoffs:
                stamp = int(day.timestamp() * 1000)
                feature = [stamp - index * 60_000 for index in range(31, 0, -1)]
                label = [stamp + index * 60_000 for index in range(1, 1441)]
                missing = [value for value in feature + label if value not in selected[scope_id]]
                windows.append(
                    {
                        "scope_id": scope_id,
                        "cutoff_at": day.isoformat(),
                        "status": "excluded" if missing else "complete",
                        "missing_minutes": len(missing),
                        "reason": "missing_or_unowned_first_known_original" if missing else None,
                    }
                )
        provenance = {
            "plan_hash": plan.payload_hash,
            "epoch_originals": epoch_hashes,
            "original_witnesses": witnesses,
            "selected_rows": {
                scope_id: [[stamp, value] for stamp, value in sorted(rows.items())]
                for scope_id, rows in selected.items()
            },
            "windows": windows,
            "file_readset": sorted(files.items()),
            "row_readset": [
                (table, key, fingerprint) for (table, key), fingerprint in sorted(rowset.items())
            ],
            "fixture_mode": self.fixture_mode,
            "global_first_known_verified": True,
            "legacy_fit_authorized": False,
            "ready_for_real_fit": False,
        }
        prepared = self.prepare_original(
            session,
            kind="dataset",
            owner_claim=owner_claim,
            request_id=request_id,
            plan_id=plan_id,
            known_asof=known,
            cutoffs=plan.cutoffs,
            provenance=provenance,
            files=tuple(sorted(files.items())),
            rows=tuple(provenance["row_readset"]),
        )
        require_snapshot(session, boundary)
        return prepared

"""Bounded complete model evidence freeze; original logical references survive."""

from __future__ import annotations

import codecs
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import stat
import sys
import threading
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from ..ports.history_dataset_model_v2 import HistoryDatasetFitModelV2
from ..ports.history_observed_training import verify_payload
from . import history_forecast_transfer39 as transfer
from .history_forecast_dispatch39 import HistoryForecastDispatch39Service
from .history_observed_dataset import encoded, require_snapshot, snapshot_identity

LOADED_SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
MAX_BYTES = 512 * 1024 * 1024
PACK_MAX = MAX_BYTES
V4_NATIVE_MAX = 768 * 1024 * 1024
V4_UNPACK_MAX = 1024 * 1024 * 1024
V4_STAGING_MAX = 2 * 1024 * 1024 * 1024
MAX_ROWS = 300000
MEMORY_CHUNK = 1024 * 1024
NATIVE_BATCH_BYTES = 16 * 1024 * 1024
FILES41 = (
    "packages/kernel/decision_hub_kernel/ports/history_model_qualification_v3.py",
    "packages/kernel/decision_hub_kernel/persistence/history_model_qualification_v3_records.py",
    "packages/kernel/decision_hub_kernel/application/history_model_freeze_v3.py",
    "packages/kernel/decision_hub_kernel/application/history_model_qualification_v3.py",
    "packages/kernel/decision_hub_kernel/application/history_qualified_forecast_v3.py",
    "packages/kernel/decision_hub_kernel/application/history_forecast_dispatch41.py",
    "apps/hub_api/history_product_bootstrap41.py",
    "apps/hub_api/history_product_routes41.py",
    "apps/hub_worker/history_hourly41.py",
    "apps/hub_worker/history_qualification_cli41.py",
    "migrations/versions/0090_history_model_qualification_v3.py",
    "packages/kernel/decision_hub_kernel/application/history_outcome_originals_v2.py",
)


class HistoryModelQualificationBoundV3Error(ValueError):
    automatic_retry = False


def sources41(forecasts, expected=None):
    base = forecasts._sources()
    sources = (
        *base,
        *(
            (
                path,
                hashlib.sha256((forecasts.originals.source_root / path).read_bytes()).hexdigest(),
            )
            for path in FILES41
        ),
    )
    if len(dict(sources)) != len(sources) or (expected is not None and sources != expected):
        raise ValueError("history_qualification_producer_source_changed")
    if dict(sources)[FILES41[2]] != LOADED_SOURCE_SHA256:
        raise ValueError("history_qualification_loaded_freeze_source_changed")
    for path, digest in sources:
        module = sys.modules.get(path.removesuffix(".py").replace("/", "."))
        if (
            module is not None
            and hasattr(module, "LOADED_SOURCE_SHA256")
            and (module.LOADED_SOURCE_SHA256 != digest)
        ):
            raise ValueError("history_qualification_loaded_dependency_source_changed")
    return sources


def _fingerprint(path, check):
    digest, size = hashlib.sha256(), 0
    with Path(path).open("rb") as handle:
        while block := handle.read(MEMORY_CHUNK):
            check()
            digest.update(block)
            size += len(block)
    check()
    return digest.hexdigest(), size


def _transport_limits46():
    return {
        "schema_version": "history-model-transport-limits.v4",
        "native_bytes_max": V4_NATIVE_MAX,
        "unpacked_bytes_max": V4_UNPACK_MAX,
        "staging_bytes_max": V4_STAGING_MAX,
        "packed_bytes_max": PACK_MAX,
        "rows_max": MAX_ROWS,
        "native_batch_bytes_max": NATIVE_BATCH_BYTES,
        "chunk_bytes": MEMORY_CHUNK,
        "compression": "ZIP_DEFLATED",
        "compression_level": 1,
        "staging_scope": "this_prepare_native_image_sidecars_and_pack",
        "unpack_scope": "all_unique_bundle_members_including_native_image",
    }


def _staging_limit46(staging, check, *, native_bytes=0, packed_bytes=0):
    """Per newly-created preparation, not the source store or old preparations."""
    check()
    size = 0
    allowed = {
        "native.sqlite3", "native.sqlite3-journal", "native.sqlite3-wal",
        "native.sqlite3-shm", "bundle.zip",
    }
    for path in staging.iterdir():
        if path.name not in allowed or not path.is_file() or path.is_symlink():
            raise ValueError("history_qualification_staging_member46_invalid")
        observed = path.stat().st_size
        if path.name == "native.sqlite3":
            observed = max(observed, native_bytes)
        elif path.name == "bundle.zip":
            observed = max(observed, packed_bytes)
        size += observed
    if size > V4_STAGING_MAX:
        raise HistoryModelQualificationBoundV3Error(
            "history_qualification_staging_bound46_reached"
        )
    return size


def _cleanup_staging46(staging, root):
    target, owner = staging.resolve(), root.resolve()
    if (
        target.parent != owner or not staging.name.startswith("prepare46_")
        or len(staging.name) != len("prepare46_") + 32 or staging.is_symlink()
        or any(letter not in "0123456789abcdef" for letter in staging.name[10:])
    ):
        raise ValueError("history_qualification_staging_cleanup46_identity_invalid")
    # Only files owned by this invocation; no recursive cleanup or old stages.
    for name in (
        "native.sqlite3", "native.sqlite3-journal", "native.sqlite3-wal",
        "native.sqlite3-shm", "bundle.zip",
    ):
        path = target / name
        if path.exists() or path.is_symlink():
            path.unlink()
    target.rmdir()


class _BoundedPackWriter46:
    def __init__(self, handle, staging, check):
        self.handle, self.staging, self.check = handle, staging, check
        self.peak = 0

    def write(self, body):
        self.check()
        end = max(self.peak, self.handle.tell() + len(body))
        if end > PACK_MAX:
            raise HistoryModelQualificationBoundV3Error(
                "history_qualification_packed_bound46_reached"
            )
        _staging_limit46(self.staging, self.check, packed_bytes=end)
        count = self.handle.write(body)
        if count != len(body):
            raise ValueError("history_qualification_pack_write46_incomplete")
        self.peak = end
        return count

    def seek(self, *arguments):
        return self.handle.seek(*arguments)

    def tell(self):
        return self.handle.tell()

    def flush(self):
        return self.handle.flush()


def _stream_member46(bundle, member, expected_hash, expected_size, maximum, check, *, output=None):
    """Actual decoded bytes, not ZIP metadata alone, enforce every read boundary."""
    info = bundle.getinfo(member)
    if (
        type(expected_size) is not int or expected_size < 0 or expected_size > maximum
        or info.file_size != expected_size
    ):
        raise HistoryModelQualificationBoundV3Error(
            "history_qualification_member_unpacked_bound46_reached"
        )
    count, hashing, blocks = 0, hashlib.sha256(), []
    with bundle.open(info) as source:
        while block := source.read(MEMORY_CHUNK):
            check()
            count += len(block)
            if count > expected_size or count > maximum:
                raise HistoryModelQualificationBoundV3Error(
                    "history_qualification_member_unpacked_bound46_reached"
                )
            hashing.update(block)
            if output is None:
                blocks.append(block)
            elif output.write(block) != len(block):
                raise ValueError("history_qualification_unpack_write46_incomplete")
    if (hashing.hexdigest(), count) != (expected_hash, expected_size):
        raise ValueError("history_qualification_member_original46_changed")
    check()
    return b"".join(blocks) if output is None else count


def _copy_native_snapshot45(
    session, schemas, row_keys, snapshot, check, *, native_maximum=None, staging_check=None,
):
    """Copy complete native rows without another whole large TEXT/BLOB value.

    The issued model proof already owns its full immutable projected values.
    This bounds additional Python row materialization, not total process memory
    or SQLite's internal buffers. Large cells retain their exact storage class
    and bytes through incremental blobs on the existing physical RO snapshot.
    """
    connection = session.connection()
    native = connection.connection.driver_connection
    boundary = snapshot_identity(session)
    if type(native) is not sqlite3.Connection or (
        connection.exec_driver_sql("PRAGMA query_only").scalar_one() != 1
        or connection.exec_driver_sql("PRAGMA encoding").scalar_one() != "UTF-8"
        or not native.in_transaction
    ):
        raise ValueError("history_qualification_native_stream_utf8_readonly_required")
    changed = native.total_changes
    target = sqlite3.connect(snapshot)
    maximum = MAX_BYTES if native_maximum is None else native_maximum
    if type(maximum) is not int or maximum <= 0 or maximum > V4_NATIVE_MAX:
        target.close()
        raise ValueError("history_qualification_native_limit46_invalid")
    if native_maximum is not None:
        page_size = target.execute("PRAGMA page_size").fetchone()[0]
        target.execute(f"PRAGMA max_page_count={max(1, maximum // page_size)}")

    def limit():
        check()
        allocated = (
            target.execute("PRAGMA page_count").fetchone()[0]
            * target.execute("PRAGMA page_size").fetchone()[0]
        )
        if allocated > maximum:
            raise HistoryModelQualificationBoundV3Error(
                "history_qualification_retention_bound_reached" if native_maximum is None
                else "history_qualification_native_image_bound46_reached"
            )
        if staging_check is not None:
            staging_check(allocated)

    def small_rows(table, columns, keys):
        if not keys:
            return
        rows = tuple(
            tuple(row)
            for row in connection.exec_driver_sql(
                f'SELECT * FROM "{table}" WHERE "{columns[0]}" IN '
                f"({','.join('?' for _ in keys)})", tuple(keys)
            ).all()
        )
        if len(rows) != len(keys):
            raise ValueError("history_qualification_selected_native_row_missing")
        if sum(
            len(cell.encode("utf-8")) if type(cell) is str
            else len(cell) if type(cell) is bytes else 8
            for row in rows for cell in row
        ) > NATIVE_BATCH_BYTES:
            raise HistoryModelQualificationBoundV3Error(
                "history_qualification_native_batch_memory_bound_reached"
            )
        target.executemany(
            f'INSERT INTO "{table}" VALUES ({",".join("?" for _ in columns)})', rows
        )
        projected_indexes = tuple(
            columns.index(name) for name in transfer.PROJECTIONS[table].split(",")
        )
        for row in rows:
            if not transfer.native_equal39(
                tuple(row[index] for index in projected_indexes), row_keys[table][row[0]]
            ):
                raise ValueError("history_qualification_selected_native_row_changed")
        stored = tuple(
            tuple(row) for row in target.execute(
                f'SELECT * FROM "{table}" WHERE "{columns[0]}" IN '
                f"({','.join('?' for _ in keys)})", tuple(keys)
            ).fetchall()
        )
        if not transfer.native_equal39(tuple(sorted(stored)), tuple(sorted(rows))):
            raise ValueError("history_qualification_frozen_native_type_changed")
        limit()

    def large_row(table, columns, metadata):
        key, source_rowid, *cells = metadata
        types, sizes = cells[::2], cells[1::2]
        streamed = {
            index for index, kind in enumerate(types)
            if index != 0 and kind in ("text", "blob")
        }
        row = tuple(connection.exec_driver_sql(
            "SELECT " + ",".join(
                "NULL" if index in streamed else f'"{name}"'
                for index, name in enumerate(columns)
            ) + f' FROM "{table}" WHERE "{columns[0]}"=?', (key,)
        ).one())
        if sum(
            len(cell.encode("utf-8")) if type(cell) is str
            else len(cell) if type(cell) is bytes else 8 for cell in row
        ) > NATIVE_BATCH_BYTES:
            raise HistoryModelQualificationBoundV3Error(
                "history_qualification_native_batch_memory_bound_reached"
            )
        parameters, expressions = [], []
        for index, value in enumerate(row):
            if index in streamed:
                expressions.append(
                    "CAST(zeroblob(?) AS TEXT)" if types[index] == "text" else "zeroblob(?)"
                )
                parameters.append(sizes[index])
            else:
                expressions.append("?")
                parameters.append(value)
        target_rowid = target.execute(
            f'INSERT INTO "{table}" VALUES ({",".join(expressions)})', tuple(parameters)
        ).lastrowid
        projection = dict(zip(transfer.PROJECTIONS[table].split(","), row_keys[table][key]))
        for index, name in enumerate(columns):
            if index not in streamed:
                if name in projection and not transfer.native_equal39(row[index], projection[name]):
                    raise ValueError("history_qualification_selected_native_row_changed")
                continue
            expected = projection.get(name)
            if name in projection and type(expected) is not (str if types[index] == "text" else bytes):
                raise ValueError("history_qualification_selected_native_row_changed")
            decoder = codecs.getincrementaldecoder("utf-8")() if types[index] == "text" else None
            position = 0
            with (
                native.blobopen(table, name, source_rowid, readonly=True) as source,
                target.blobopen(table, name, target_rowid) as destination,
            ):
                if len(source) != sizes[index] or len(destination) != sizes[index]:
                    raise ValueError("history_qualification_selected_native_row_changed")
                while position < sizes[index]:
                    check()
                    block = source.read(min(MEMORY_CHUNK, sizes[index] - position))
                    if not block:
                        raise ValueError("history_qualification_selected_native_row_changed")
                    destination.write(block)
                    position += len(block)
            position = text_position = 0
            with (
                native.blobopen(table, name, source_rowid, readonly=True) as source,
                target.blobopen(table, name, target_rowid, readonly=True) as destination,
            ):
                while position < sizes[index]:
                    check()
                    block = source.read(min(MEMORY_CHUNK, sizes[index] - position))
                    copied = destination.read(len(block))
                    if not block or copied != block:
                        raise ValueError("history_qualification_frozen_native_type_changed")
                    value = decoder.decode(block, final=position + len(block) == sizes[index]) if decoder else block
                    if name in projection:
                        start = text_position if decoder else position
                        if expected[start : start + len(value)] != value:
                            raise ValueError("history_qualification_selected_native_row_changed")
                    text_position += len(value)
                    position += len(block)
                if name in projection and len(expected) != (text_position if decoder else position):
                    raise ValueError("history_qualification_selected_native_row_changed")
            actual = target.execute(
                f'SELECT typeof("{name}"),length(CAST("{name}" AS BLOB)) '
                f'FROM "{table}" WHERE _rowid_=?', (target_rowid,)
            ).fetchone()
            if actual != (types[index], sizes[index]):
                raise ValueError("history_qualification_frozen_native_type_changed")
        small_columns = tuple(index for index in range(len(columns)) if index not in streamed)
        stored = target.execute(
            "SELECT " + ",".join(f'"{columns[index]}"' for index in small_columns)
            + f' FROM "{table}" WHERE _rowid_=?', (target_rowid,)
        ).fetchone()
        if not transfer.native_equal39(tuple(stored), tuple(row[index] for index in small_columns)):
            raise ValueError("history_qualification_frozen_native_type_changed")
        limit()

    try:
        for table, schema in schemas:
            check()
            columns = tuple(row[1] for row in schema)
            if "_rowid_" in columns:
                raise ValueError("history_qualification_native_rowid_shadowed")
            target.execute(
                f'CREATE TABLE "{table}" ('
                + ",".join(f'"{name}"' + (" PRIMARY KEY" if index == 0 else "")
                           for index, name in enumerate(columns)) + ")"
            )
            keys = tuple(sorted(row_keys.get(table, {})))
            fields = ",".join(
                f'typeof("{name}"),length(CAST("{name}" AS BLOB))' for name in columns
            )
            for offset in range(0, len(keys), transfer.BATCH):
                check()
                batch = keys[offset : offset + transfer.BATCH]
                metadata = tuple(tuple(row) for row in connection.exec_driver_sql(
                    f'SELECT "{columns[0]}",_rowid_,{fields} FROM "{table}" '
                    f'WHERE "{columns[0]}" IN ({",".join("?" for _ in batch)})', batch
                ).all())
                if len(metadata) != len(batch) or {row[0] for row in metadata} != set(batch):
                    raise ValueError("history_qualification_selected_native_row_missing")
                pending, pending_size = [], 0
                for row in metadata:
                    cells = row[2:]
                    size = sum(length if kind in ("text", "blob") else 8
                               for kind, length in zip(cells[::2], cells[1::2]))
                    if size > maximum:
                        raise HistoryModelQualificationBoundV3Error(
                            "history_qualification_retention_bound_reached" if native_maximum is None
                            else "history_qualification_native_row_bound46_reached"
                        )
                    if size > NATIVE_BATCH_BYTES:
                        small_rows(table, columns, pending)
                        pending, pending_size = [], 0
                        large_row(table, columns, row)
                    else:
                        if pending_size + size > NATIVE_BATCH_BYTES:
                            small_rows(table, columns, pending)
                            pending, pending_size = [], 0
                        pending.append(row[0])
                        pending_size += size
                small_rows(table, columns, pending)
        target.commit()
        if native.total_changes != changed or connection.exec_driver_sql("PRAGMA query_only").scalar_one() != 1:
            raise ValueError("history_qualification_native_stream_source_changed")
        require_snapshot(session, boundary)
    except sqlite3.DatabaseError as error:
        if native_maximum is not None and getattr(error, "sqlite_errorcode", None) == sqlite3.SQLITE_FULL:
            raise HistoryModelQualificationBoundV3Error(
                "history_qualification_native_allocation_bound46_reached"
            ) from error
        raise
    finally:
        target.close()


@dataclass(frozen=True, slots=True)
class FrozenModelPreparationV3:
    request_id: str
    models: tuple[bytes, ...]
    bundle_ref: str
    bundle_hash: str
    bundle_bytes: int
    manifest_hash: str
    verified_at: str
    sources: tuple
    context: tuple
    issuer: object
    thread_id: int
    authentication: bytes


class HistoryModelFreezeV3Publisher:
    def __init__(self, forecasts: HistoryForecastDispatch39Service):
        if type(forecasts) is not HistoryForecastDispatch39Service:
            raise ValueError("history_qualification_exact_forecast_dispatch_required")
        self.forecasts = forecasts
        self.root = (
            forecasts.originals.data_directory / "history-model-qualification-v3"
        ).resolve()
        self.key = secrets.token_bytes(32)
        self._issued = self._leaves = None

    def _context(self, session):
        return (
            transfer.database_identity(session),
            id(self),
            id(self.forecasts),
            self.forecasts.owner_claim,
            tuple(sorted(self.forecasts.grants.items())),
            str(self.forecasts.originals.data_directory),
            str(self.forecasts.originals.source_root),
            self.forecasts.originals.fixture_mode,
        )

    @staticmethod
    def _members(value):
        return (
            value.request_id,
            value.models,
            value.bundle_ref,
            value.bundle_hash,
            value.bundle_bytes,
            value.manifest_hash,
            value.verified_at,
            value.sources,
            value.context,
            id(value.issuer),
            value.thread_id,
        )

    def _loaded(self):
        if (
            hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != LOADED_SOURCE_SHA256
            or any(
                getattr(cls, name) is not function or function.__code__ is not code
                for cls, name, function, code in LOADED_METHODS
            )
            or any(
                globals()[name] is not function or function.__code__ is not code
                for name, function, code in LOADED_FUNCTIONS
            )
        ):
            raise ValueError("history_qualification_loaded_freeze_callable_changed")

    def _issue_model(self, session, model_id):
        authority = transfer.TransferAuthority39(self.forecasts.writer)
        proof, model = authority.issue(session, model_id)
        return authority, proof, model

    def prepare(self, session, *, model_ids, request_id, budget_seconds=180.0, checkpoint=None):
        started = perf_counter()
        if (
            type(model_ids) is not tuple
            or not 1 <= len(model_ids) <= 2
            or len(set(model_ids)) != len(model_ids)
            or not set(model_ids) <= set(self.forecasts.grants.values())
            or type(request_id) is not str
            or not 1 <= len(request_id) <= 128
            or type(budget_seconds) not in (float, int)
            or not 0 < budget_seconds <= 180
            or (checkpoint is not None and not callable(checkpoint))
        ):
            raise ValueError("history_qualification_freeze_request_invalid")
        deadline = started + budget_seconds

        def check():
            if perf_counter() >= deadline:
                raise HistoryModelQualificationBoundV3Error(
                    "history_qualification_freeze_budget_reached"
                )
            if checkpoint is not None:
                checkpoint()
            if perf_counter() >= deadline:
                raise HistoryModelQualificationBoundV3Error(
                    "history_qualification_freeze_budget_reached"
                )

        self._loaded()
        boundary = snapshot_identity(session)
        if session.connection().exec_driver_sql("PRAGMA query_only").scalar_one() != 1:
            raise ValueError("history_qualification_physical_readonly_required")
        sources = sources41(self.forecasts)
        schemas = transfer.capture_schemas39(session, tuple(transfer.PROJECTIONS))
        entries, row_keys, files = [], {}, {}
        for model_id in model_ids:
            check()
            authority, proof, model = self._issue_model(session, model_id)
            check()
            if (
                type(authority) is not transfer.TransferAuthority39
                or type(proof) is not transfer.ModelProof39
                or proof.issuer is not authority
                or authority.service is not self.forecasts.writer
                or model.model_id != model_id
                or model.owner_claim != self.forecasts.owner_claim
                or encoded(model.model_dump(mode="json")) != proof.model_bytes
            ):
                raise ValueError("history_qualification_exact_model_proof_changed")
            authority.authenticate(session, proof)
            entries.append((authority, proof, model))
            for group in proof.rows:
                expected = row_keys.setdefault(group.table, {})
                for row in group.rows:
                    if row[0] in expected and not transfer.native_equal39(expected[row[0]], row):
                        raise ValueError("history_qualification_native_readset_conflict")
                    expected[row[0]] = row
            for path, digest, size in proof.files:
                if path in files and files[path] != (digest, size):
                    raise ValueError("history_qualification_file_readset_conflict")
                files[path] = digest, size
        for path, digest in sources:
            target = str((self.forecasts.originals.source_root / path).resolve(strict=True))
            actual = _fingerprint(target, check)
            if actual[0] != digest:
                raise ValueError("history_qualification_producer_source_changed")
            if target in files and files[target] != actual:
                raise ValueError("history_qualification_file_readset_conflict")
            files[target] = actual
        row_count = sum(len(rows) for rows in row_keys.values())
        if row_count > MAX_ROWS:
            raise HistoryModelQualificationBoundV3Error(
                "history_qualification_retention_bound_reached"
            )
        if sum(size for _, size in set(files.values())) > V4_UNPACK_MAX:
            raise HistoryModelQualificationBoundV3Error(
                "history_qualification_originals_unpacked_bound46_reached"
            )
        destination, pack_hash, pack_size, manifest_hash = self._freeze_bundle46(
            session, schemas, row_keys, files, entries, sources, request_id, check, boundary,
        )
        check()
        verified = self.forecasts.originals.now().isoformat()
        value = FrozenModelPreparationV3(
            request_id,
            tuple(proof.model_bytes for _, proof, _ in entries),
            str(destination),
            pack_hash,
            pack_size,
            manifest_hash,
            verified,
            sources,
            self._context(session),
            self,
            threading.get_ident(),
            b"",
        )
        leaves = self._members(value)
        value = FrozenModelPreparationV3(
            request_id,
            value.models,
            value.bundle_ref,
            value.bundle_hash,
            value.bundle_bytes,
            value.manifest_hash,
            verified,
            sources,
            value.context,
            self,
            value.thread_id,
            hmac.digest(self.key, repr(leaves).encode(), "sha256"),
        )
        self._issued, self._leaves = value, leaves
        return value

    def _freeze_bundle46(
        self, session, schemas, row_keys, files, entries, sources, request_id, check, boundary,
    ):
        self.root.mkdir(parents=True, exist_ok=True)
        staging = self.root / ("prepare46_" + secrets.token_hex(16))
        staging.mkdir()
        snapshot = staging / "native.sqlite3"
        try:
            return self._write_bundle46(
                session, schemas, row_keys, files, entries, sources, request_id,
                check, boundary, staging, snapshot,
            )
        finally:
            _cleanup_staging46(staging, self.root)

    def _write_bundle46(
        self, session, schemas, row_keys, files, entries, sources, request_id,
        check, boundary, staging, snapshot,
    ):
        _copy_native_snapshot45(
            session, schemas, row_keys, snapshot, check, native_maximum=V4_NATIVE_MAX,
            staging_check=lambda size: _staging_limit46(staging, check, native_bytes=size),
        )
        snapshot_digest, snapshot_size = _fingerprint(snapshot, check)
        file_manifest = [
            (path, digest, size, "originals/" + digest)
            for path, (digest, size) in sorted(files.items())
        ]
        raw_aliases = []
        candle_columns = transfer.PROJECTIONS["market_candles"].split(",")
        for row in row_keys.get("market_candles", {}).values():
            ref, digest = (
                row[candle_columns.index("capture_ref")],
                row[candle_columns.index("capture_hash")],
            )
            raw_aliases.append((ref, digest, "originals/" + digest))
        manifest = {
            "schema_version": "history-model-frozen-bundle.v4",
            "transport_limits": _transport_limits46(),
            "owner_claim": self.forecasts.owner_claim,
            "request_id": request_id,
            "fixture_mode": self.forecasts.originals.fixture_mode,
            "schemas": schemas,
            "row_counts": {table: len(rows) for table, rows in row_keys.items()},
            "row_keys": {table: sorted(rows) for table, rows in row_keys.items()},
            "native_format": "sqlite-native-unaffinitized.v1",
            "native_snapshot": ("native.sqlite3", snapshot_digest, snapshot_size),
            "files": file_manifest,
            "raw_aliases": sorted(set(raw_aliases)),
            "source_files": sources,
            "models": [
                (
                    model.model_id,
                    model.payload_hash,
                    "models/" + model.model_id + ".json",
                    next(
                        group.rows[0][8]
                        for group in proof.rows
                        if group.table == "market_history_v2_models"
                    ),
                    next(
                        group.rows[0][9]
                        for group in proof.rows
                        if group.table == "market_history_v2_models"
                    ),
                )
                for _, proof, model in entries
            ],
            "financial_qualified": False,
        }
        manifest_body = encoded(manifest)
        manifest_hash = hashlib.sha256(manifest_body).hexdigest()
        pack = staging / "bundle.zip"
        retained_bytes = len(manifest_body) + snapshot_size + sum(
            size for _, size in set(files.values())
        ) + sum(len(proof.model_bytes) for _, proof, _ in entries)
        if retained_bytes > V4_UNPACK_MAX:
            raise HistoryModelQualificationBoundV3Error(
                "history_qualification_bundle_unpacked_bound46_reached"
            )
        with pack.open("w+b") as handle, zipfile.ZipFile(
            _BoundedPackWriter46(handle, staging, check), "w",
            compression=zipfile.ZIP_DEFLATED, compresslevel=1, allowZip64=True,
        ) as bundle:
            bundle.writestr("manifest.json", manifest_body)
            retained_actual = len(manifest_body)
            copied = set()
            for path, digest, size, member in (
                *file_manifest,
                (str(snapshot), snapshot_digest, snapshot_size, "native.sqlite3"),
            ):
                if member in copied:
                    continue
                copied.add(member)
                count, hashing = 0, hashlib.sha256()
                with (
                    Path(path).open("rb") as original,
                    bundle.open(member, "w", force_zip64=True) as target,
                ):
                    while block := original.read(MEMORY_CHUNK):
                        check()
                        hashing.update(block)
                        count += len(block)
                        retained_actual += len(block)
                        if count > size:
                            raise ValueError("history_qualification_original_changed_while_copying")
                        if retained_actual > V4_UNPACK_MAX:
                            raise HistoryModelQualificationBoundV3Error(
                                "history_qualification_bundle_unpacked_bound46_reached"
                            )
                        target.write(block)
                if (hashing.hexdigest(), count) != (digest, size):
                    raise ValueError("history_qualification_original_changed_while_copying")
            for _, proof, model in entries:
                check()
                body = proof.model_bytes
                retained_actual += len(body)
                if retained_actual > V4_UNPACK_MAX:
                    raise HistoryModelQualificationBoundV3Error(
                        "history_qualification_bundle_unpacked_bound46_reached"
                    )
                bundle.writestr("models/" + model.model_id + ".json", body)
            if retained_actual != retained_bytes:
                raise ValueError("history_qualification_bundle_unpacked_count46_changed")
        # All live originals and complete native/schema readsets remain required
        # on this snapshot's exit, after exact copying has finished.
        for authority, proof, _ in entries:
            authority.authenticate(session, proof)
            transfer.verify_rows(session, proof.rows)
        transfer.verify_schemas39(session, schemas)
        for path, expected in files.items():
            if _fingerprint(path, check) != expected:
                raise ValueError("history_qualification_live_original_changed_at_exit")
        sources41(self.forecasts, sources)
        require_snapshot(session, boundary)
        pack_hash, pack_size = _fingerprint(pack, check)
        if pack_size > PACK_MAX:
            raise HistoryModelQualificationBoundV3Error(
                "history_qualification_packed_bound46_reached"
            )
        _staging_limit46(staging, check)
        destination = self.root / "bundles" / pack_hash[:2] / (pack_hash + ".zip")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with pack.open("r+b") as original:
            os.fsync(original.fileno())
        # Same-volume atomic no-overwrite link of the already fsynced complete pack.
        try:
            os.link(pack, destination)
        except FileExistsError:
            if _fingerprint(destination, check) != (pack_hash, pack_size):
                raise ValueError("history_qualification_bundle_collision") from None
        if _fingerprint(destination, check) != (pack_hash, pack_size):
            raise ValueError("history_qualification_bundle_copy_changed")
        check()
        return destination, pack_hash, pack_size, manifest_hash

    def authenticate(self, session, prepared):
        self._loaded()
        if (
            type(prepared) is not FrozenModelPreparationV3
            or prepared is not self._issued
            or prepared.issuer is not self
            or prepared.thread_id != threading.get_ident()
            or not transfer.native_equal39(self._members(prepared), self._leaves)
            or not transfer.native_equal39(prepared.context, self._context(session))
            or not hmac.compare_digest(
                prepared.authentication,
                hmac.digest(self.key, repr(self._leaves).encode(), "sha256"),
            )
        ):
            raise ValueError("history_qualification_freeze_preparation_changed")
        sources41(self.forecasts, prepared.sources)


class FrozenModelBundleV3:
    """Explicit V3/V4 transport; all original logical references remain unchanged."""

    def __init__(self, root, reference, digest, manifest_hash, *, checkpoint):
        self.root = Path(root).resolve(strict=True)
        self.path = Path(reference).resolve(strict=True)
        self.digest, self.check = digest, checkpoint
        if not self.path.is_relative_to(self.root):
            raise ValueError("history_qualification_bundle_outside_retained_root")
        self.verify()
        with zipfile.ZipFile(self.path) as bundle:
            info = bundle.infolist()
            names = [item.filename for item in info]
            if (
                len(set(names)) != len(names)
                or sum(item.file_size for item in info) > V4_UNPACK_MAX
            ):
                raise ValueError("history_qualification_bundle_members_invalid")
            manifest_info = bundle.getinfo("manifest.json")
            body = _stream_member46(
                bundle, "manifest.json", manifest_hash, manifest_info.file_size,
                MAX_BYTES, self.check,
            )
        self.manifest = json.loads(body)
        self._manifest_bytes = body
        self._version = self.manifest.get("schema_version")
        if (
            self._version not in ("history-model-frozen-bundle.v3", "history-model-frozen-bundle.v4")
            or self.manifest.get("financial_qualified") is not False
        ):
            raise ValueError("history_qualification_bundle_version_invalid")
        if self._version == "history-model-frozen-bundle.v3":
            # Original V3 logical limits remain strict regardless of compression.
            if "transport_limits" in self.manifest or sum(item.file_size for item in info) > MAX_BYTES:
                raise ValueError("history_qualification_bundle_members_invalid")
            self._native_max, self._unpack_max = MAX_BYTES, MAX_BYTES
        else:
            if (
                encoded(self.manifest.get("transport_limits")) != encoded(_transport_limits46())
                or set(self.manifest) != {
                    "schema_version", "transport_limits", "owner_claim", "request_id", "fixture_mode",
                    "schemas", "row_counts", "row_keys", "native_format", "native_snapshot",
                    "files", "raw_aliases", "source_files", "models", "financial_qualified",
                }
                or any(
                    item.compress_type != zipfile.ZIP_DEFLATED or item.flag_bits != 0
                    or item.comment or item.is_dir()
                    or stat.S_IFMT(item.external_attr >> 16) not in (0, stat.S_IFREG)
                    for item in info
                )
            ):
                raise ValueError("history_qualification_transport46_contract_invalid")
            with zipfile.ZipFile(self.path) as bundle:
                if bundle.comment or min(item.header_offset for item in info) != 0:
                    raise ValueError("history_qualification_transport46_contract_invalid")
            with self.path.open("rb") as handle:
                handle.seek(-22, os.SEEK_END)
                ending = handle.read(22)
            if ending[:4] != b"PK\x05\x06" or ending[-2:] != b"\x00\x00":
                raise ValueError("history_qualification_transport46_contract_invalid")
            self._native_max, self._unpack_max = V4_NATIVE_MAX, V4_UNPACK_MAX
        native_member, native_digest, native_size = self.manifest["native_snapshot"]
        if (
            native_member != "native.sqlite3" or type(native_size) is not int
            or not 0 < native_size <= self._native_max or type(native_digest) is not str
            or len(native_digest) != 64 or any(letter not in "0123456789abcdef" for letter in native_digest)
        ):
            raise ValueError("history_qualification_native_member46_invalid")
        self.mapping = {}
        self._member_entries = {"manifest.json": (manifest_hash, len(body)),
                                "native.sqlite3": (native_digest, native_size)}
        for logical, expected, size, member in self.manifest["files"]:
            if logical in self.mapping and self.mapping[logical] != (expected, size, member):
                raise ValueError("history_qualification_mapping_conflict")
            if (
                type(expected) is not str or len(expected) != 64
                or any(letter not in "0123456789abcdef" for letter in expected)
                or member != "originals/" + expected or type(size) is not int or size < 0
                or member in self._member_entries and self._member_entries[member] != (expected, size)
            ):
                raise ValueError("history_qualification_member_original46_invalid")
            self._member_entries[member] = expected, size
            self.mapping[logical] = expected, size, member
        for logical, expected, member in self.manifest["raw_aliases"]:
            match = next((row for row in self.manifest["files"] if row[1] == expected), None)
            if match is None:
                raise ValueError("history_qualification_raw_alias_unretained")
            mapping = expected, match[2], member
            if logical in self.mapping and self.mapping[logical] != mapping:
                raise ValueError("history_qualification_mapping_conflict")
            self.mapping[logical] = mapping
        expected_names = {
            "manifest.json",
            "native.sqlite3",
            *(row[3] for row in self.manifest["files"]),
            *(row[2] for row in self.manifest["models"]),
        }
        if (
            set(names) != expected_names
            or self.manifest.get("native_format") != "sqlite-native-unaffinitized.v1"
            or len(self.manifest["models"]) not in (1, 2)
            or len({row[0] for row in self.manifest["models"]}) != len(self.manifest["models"])
        ):
            raise ValueError("history_qualification_complete_bundle_manifest_invalid")
        for identifier, _payload_hash, member, _ref, original_hash in self.manifest["models"]:
            if (
                type(identifier) is not str or not identifier.startswith("hfit2_")
                or len(identifier) != 54 or any(letter not in "0123456789abcdef" for letter in identifier[6:])
                or member != "models/" + identifier + ".json"
            ):
                raise ValueError("history_qualification_model_member46_invalid")
            selected_info = next(item for item in info if item.filename == member)
            self._member_entries[member] = original_hash, selected_info.file_size
        if (
            any(item.file_size != self._member_entries[item.filename][1] for item in info)
            or sum(size for _, size in self._member_entries.values()) > self._unpack_max
            or any(type(count) is not int or count < 0 for count in self.manifest["row_counts"].values())
            or sum(self.manifest["row_counts"].values()) > MAX_ROWS
        ):
            raise ValueError("history_qualification_bundle_members_invalid")
        self._mapping_leaves = tuple(sorted(self.mapping.items()))
        self._member_leaves = tuple(sorted(self._member_entries.items()))
        self._resource_leaves = (self._version, self._native_max, self._unpack_max)

    def _guard(self):
        if (
            encoded(self.manifest) != self._manifest_bytes
            or tuple(sorted(self.mapping.items())) != self._mapping_leaves
            or tuple(sorted(self._member_entries.items())) != self._member_leaves
            or (self._version, self._native_max, self._unpack_max) != self._resource_leaves
        ):
            raise ValueError("history_qualification_bundle_mapping_changed")

    def verify(self):
        if hasattr(self, "_manifest_bytes"):
            self._guard()
        if self.path.stat().st_size > PACK_MAX:
            raise HistoryModelQualificationBoundV3Error("history_qualification_packed_bound46_reached")
        if _fingerprint(self.path, self.check)[0] != self.digest:
            raise ValueError("history_qualification_full_bundle_hash_changed")

    def read(self, logical, digest=None):
        self._guard()
        self.check()
        expected, size, member = self.mapping[logical]
        if digest is not None and digest != expected:
            raise ValueError("history_qualification_logical_ref_hash_changed")
        with zipfile.ZipFile(self.path) as bundle:
            body = _stream_member46(bundle, member, expected, size, self._unpack_max, self.check)
        self.check()
        return body

    def model(self, model_id):
        self._guard()
        selected = [row for row in self.manifest["models"] if row[0] == model_id]
        if len(selected) != 1:
            raise ValueError("history_qualification_exact_model_not_retained")
        with zipfile.ZipFile(self.path) as bundle:
            expected, size = self._member_entries[selected[0][2]]
            value = verify_payload(
                HistoryDatasetFitModelV2.model_validate_json(_stream_member46(
                    bundle, selected[0][2], expected, size, self._unpack_max, self.check,
                ))
            )
        if value.model_id != model_id or value.payload_hash != selected[0][1]:
            raise ValueError("history_qualification_retained_model_changed")
        self.check()
        return value

    @contextmanager
    def snapshot_session(self):
        self._guard()
        member, digest, size = self.manifest["native_snapshot"]
        target = self.root / "snapshots" / (digest + ".sqlite3")
        if not target.resolve().is_relative_to(self.root) or target.is_symlink():
            raise ValueError("history_qualification_native_cache46_outside_root")
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.parent / ("unpack46_" + secrets.token_hex(16) + ".partial")
        created = False
        try:
            if not target.exists():
                with partial.open("xb") as output, zipfile.ZipFile(self.path) as bundle:
                    created = True
                    _stream_member46(bundle, member, digest, size, self._native_max, self.check, output=output)
                    output.flush()
                    os.fsync(output.fileno())
                if _fingerprint(partial, self.check) != (digest, size):
                    raise ValueError("history_qualification_frozen_native_snapshot_changed")
                self.verify()
                # Hard-link publication is atomic and never replaces another cache.
                try:
                    os.link(partial, target)
                except FileExistsError:
                    pass
        finally:
            if created and partial.exists():
                partial.unlink()
        if target.stat().st_size != size or _fingerprint(target, self.check) != (digest, size):
            raise ValueError("history_qualification_frozen_native_snapshot_changed")
        engine = create_engine(
            "sqlite+pysqlite:///file:" + target.as_posix() + "?mode=ro&immutable=1&uri=true"
        )
        try:
            with engine.connect() as connection:
                connection.exec_driver_sql("PRAGMA query_only=ON")
                connection.exec_driver_sql("BEGIN")
                with Session(bind=connection) as session:
                    session.connection()
                    boundary = snapshot_identity(session)
                    yield session
                    require_snapshot(session, boundary)
        finally:
            engine.dispose()
        if _fingerprint(target, self.check) != (digest, size):
            raise ValueError("history_qualification_frozen_native_snapshot_changed")
        self.verify()


LOADED_METHODS = tuple(
    (cls, name, getattr(cls, name), getattr(cls, name).__code__)
    for cls in (HistoryModelFreezeV3Publisher, FrozenModelBundleV3, _BoundedPackWriter46)
    for name in cls.__dict__
    if callable(getattr(cls, name)) and hasattr(getattr(cls, name), "__code__")
)
LOADED_FUNCTIONS = tuple(
    (name, globals()[name], globals()[name].__code__)
    for name in (
        "sources41",
        "_fingerprint",
        "_copy_native_snapshot45",
        "_transport_limits46",
        "_staging_limit46",
        "_cleanup_staging46",
        "_stream_member46",
        "encoded",
        "require_snapshot",
        "snapshot_identity",
        "verify_payload",
    )
)

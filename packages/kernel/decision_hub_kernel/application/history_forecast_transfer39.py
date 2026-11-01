"""Request-local, native readset transfer from a fully authenticated RO model.

The proof is an internal capability: its exact issued object, immutable members,
thread, database, grants, HMAC and loaded methods must all remain bound. It is
never reconstructed from HTTP or an archive and never reused by another request.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from types import MappingProxyType

from ..ports.history_dataset_v2 import HistoryDatasetOriginalV2
from ..ports.history_observed_training import verify_payload
from ..ports.market_path import canonical_hash
from .history_observed_dataset import encoded, require_snapshot, snapshot_identity

LOADED_SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
BATCH = 700
PROJECTIONS = MappingProxyType(
    {
        "market_history_training_records": (
            "record_id,registration_id,request_id,kind,scope_id,payload_json,payload_hash,"
            "original_ref,original_hash,known_at,recorded_at"
        ),
        "market_history_acquisition_jobs": (
            "job_id,registration_id,scope_id,cutoff_at,deadline_at,state,attempts,lease_owner,"
            "lease_generation,lease_expires_at,next_attempt_at,last_attempt_at,checkpoint_json,"
            "error_code,completed_at,protocol_hash,recorded_at"
        ),
        "raw_captures": (
            "capture_id,context_id,url,status_code,body_hash,body_ref,byte_count,media_type,"
            "adapter_version,error_code,started_at,completed_at,recorded_at"
        ),
        "market_candles": (
            "candle_id,scope_id,venue,instrument,price_type,interval_seconds,open_at,close_at,"
            "received_at,known_at,revision_hash,candle_json,capture_ref,capture_hash"
        ),
        "market_history_v2_originals": (
            "original_id,kind,owner_claim,request_id,plan_id,payload_json,payload_hash,"
            "original_ref,original_hash,recorded_at"
        ),
        "market_history_v2_attempts": (
            "attempt_id,epoch_id,job_id,generation,cursor,reserved_at,state,capture_id,"
            "result_json,result_hash,fixture_mode"
        ),
        "market_history_v2_jobs": "job_id,epoch_id,plan_id,scope_id,start_at,end_at,max_pages",
        "market_history_v2_models": (
            "model_id,owner_claim,request_id,scope_id,plan_id,dataset_id,payload_json,payload_hash,"
            "original_ref,original_hash,known_at,recorded_at"
        ),
    }
)
KEYS = MappingProxyType({table: columns.split(",")[0] for table, columns in PROJECTIONS.items()})
FULL_SCHEMA_COLUMNS = MappingProxyType(
    {
        **{table: tuple(columns.split(",")) for table, columns in PROJECTIONS.items()},
        "market_history_v2_jobs": (
            "job_id",
            "epoch_id",
            "plan_id",
            "scope_id",
            "start_at",
            "end_at",
            "cursor",
            "max_pages",
            "page_count",
            "attempts",
            "retry_used",
            "generation",
            "state",
            "lease_owner",
            "lease_expires_at",
            "next_attempt_at",
            "error_code",
        ),
    }
)


@dataclass(frozen=True, slots=True)
class NativeRows39:
    table: str
    rows: tuple[tuple, ...]


@dataclass(frozen=True, slots=True)
class ModelProof39:
    model_bytes: bytes
    rows: tuple[NativeRows39, ...]
    schemas: tuple[tuple[str, tuple[tuple, ...]], ...]
    files: tuple[tuple[str, str, int], ...]
    sources: tuple[tuple[str, str], ...]
    context: tuple
    deep_verified_at: str
    issuer: object
    thread_id: int
    manifest_hash: str
    authentication: bytes


def database_identity(session):
    rows = session.connection().exec_driver_sql("PRAGMA database_list").all()
    main = [tuple(row) for row in rows if row[1] == "main"]
    if len(main) != 1 or not main[0][2]:
        raise ValueError("history39_named_physical_database_required")
    return str(Path(main[0][2]).resolve(strict=True))


def native_equal39(left, right):
    """Exact SQLite native types/values; integer 1 is not REAL 1.0.

    Full historical strings are compared directly without JSON reconstruction.
    Float hex additionally preserves the signed-zero distinction of old JSON.
    """
    if type(left) is not type(right):
        return False
    if left is right:
        return True
    if type(left) is tuple:
        if len(left) != len(right):
            return False
        return all(native_equal39(a, b) for a, b in zip(left, right, strict=True))
    if type(left) is float:
        return left.hex() == right.hex()
    if type(left) not in (str, int, bytes, bool, type(None)):
        raise ValueError("history39_unsupported_native_scalar")
    return left == right


def capture_schemas39(session, tables):
    schemas = []
    for table in sorted(set(tables)):
        if table not in PROJECTIONS:
            raise ValueError("history39_native_projection_invalid")
        columns = tuple(
            tuple(row)
            for row in session.connection().exec_driver_sql(f"PRAGMA table_xinfo({table})").all()
        )
        # table_xinfo includes generated/hidden columns omitted by table_info;
        # legacy SELECT* cannot silently lose a newly added metadata column.
        names = tuple(row[1] for row in columns)
        if (
            len(names) != len(FULL_SCHEMA_COLUMNS[table])
            or set(names) != set(FULL_SCHEMA_COLUMNS[table])
            or any(row[6] != 0 for row in columns)
            or not set(PROJECTIONS[table].split(",")) <= set(names)
        ):
            raise ValueError("history39_complete_metadata_schema_changed")
        schemas.append((table, columns))
    return tuple(schemas)


def verify_schemas39(session, expected):
    actual = capture_schemas39(session, tuple(table for table, _ in expected))
    if not native_equal39(actual, expected):
        raise ValueError("history39_complete_metadata_schema_changed")


def file_fingerprint(path):
    """Whole original bytes, never stat/mtime/header identity."""
    size = 0
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
            size += len(block)
    return digest.hexdigest(), size


def capture_files(values):
    expected = {}
    for path, digest in values:
        path = str(Path(path).resolve(strict=True))
        if path in expected and expected[path] != digest:
            raise ValueError("history39_conflicting_original_readset")
        expected[path] = digest
    result = []
    for path, digest in sorted(expected.items()):
        actual, size = file_fingerprint(path)
        if actual != digest:
            raise ValueError("history39_original_bytes_changed")
        result.append((path, digest, size))
    return tuple(result)


def verify_files(values):
    for path, digest, size in values:
        if file_fingerprint(path) != (digest, size):
            raise ValueError("history39_original_bytes_changed")


def fetch_rows(session, table, keys):
    if table not in PROJECTIONS or len(set(keys)) != len(keys):
        raise ValueError("history39_native_projection_invalid")
    result = []
    for offset in range(0, len(keys), BATCH):
        batch = keys[offset : offset + BATCH]
        result.extend(
            tuple(row)
            for row in session.connection()
            .exec_driver_sql(
                f"SELECT {PROJECTIONS[table]} FROM {table} WHERE {KEYS[table]} IN "
                f"({','.join('?' for _ in batch)})",
                tuple(batch),
            )
            .all()
        )
    return tuple(sorted(result, key=lambda row: row[0]))


def capture_rows(session, manifest, extra):
    groups = {}
    for table, key, digest in manifest:
        if table not in PROJECTIONS:
            raise ValueError("history39_native_projection_invalid")
        prior = groups.setdefault(table, {}).setdefault(key, digest)
        if prior != digest:
            raise ValueError("history39_conflicting_metadata_readset")
    for table, key in extra:
        groups.setdefault(table, {}).setdefault(key, None)
    frozen = []
    for table, expected in sorted(groups.items()):
        rows = fetch_rows(session, table, tuple(expected))
        if len(rows) != len(expected):
            raise ValueError("history39_original_metadata_missing")
        columns = PROJECTIONS[table].split(",")
        for row in rows:
            digest = expected[row[0]]
            if (
                digest is not None
                and canonical_hash(dict(zip(columns, row, strict=True))) != digest
            ):
                raise ValueError("history39_original_metadata_changed")
        frozen.append(NativeRows39(table, rows))
    return tuple(frozen)


def verify_rows(session, groups):
    for group in groups:
        if not native_equal39(
            fetch_rows(session, group.table, tuple(row[0] for row in group.rows)), group.rows
        ):
            raise ValueError("history39_original_metadata_changed")


def feature_candidates(session, scope, cutoff):
    # Exact original reader.load predicate/order; every eligible revision is
    # retained, including nonselected candidates, to exclude new winners.
    def stamp(value):
        return value.strftime("%Y-%m-%d %H:%M:%S.%f")

    return tuple(
        tuple(row)
        for row in session.connection()
        .exec_driver_sql(
            f"SELECT {PROJECTIONS['market_candles']} FROM market_candles "
            "WHERE scope_id=? AND open_at>=? AND close_at<=? AND known_at<=? "
            "ORDER BY open_at,known_at",
            (
                scope,
                stamp(cutoff - timedelta(minutes=34)),
                stamp(cutoff + timedelta(days=1, minutes=1)),
                stamp(cutoff),
            ),
        )
        .all()
    )


class TransferAuthority39:
    def __init__(self, service):
        self.service = service
        self.key = secrets.token_bytes(32)
        self._issued = None
        self._members = None

    def context(self, session):
        service = self.service
        return (
            database_identity(session),
            str(service.originals.data_directory),
            str(service.originals.source_root),
            service.owner_claim,
            tuple(sorted(service.grants.items())),
            service.originals.fixture_mode,
            id(service),
            id(service.models),
            id(service.commands),
            id(service.reader),
        )

    @staticmethod
    def members(proof):
        return (
            proof.model_bytes,
            tuple((group.table, group.rows) for group in proof.rows),
            proof.schemas,
            proof.files,
            proof.sources,
            proof.context,
            proof.deep_verified_at,
            proof.issuer,
            proof.thread_id,
            proof.manifest_hash,
        )

    def issue(self, session, model_id):
        boundary = snapshot_identity(session)
        if session.connection().exec_driver_sql("PRAGMA query_only").scalar_one() != 1:
            raise ValueError("history39_fresh_readonly_proof_required")
        service = self.service
        sources = service._sources()
        # Bind the full current schema before deep authentication. All fixed
        # reader tables exist in this deployed V2 schema; no schema discovery.
        schemas = capture_schemas39(session, tuple(PROJECTIONS))
        model = service.models.read(session, model_id, service.owner_claim)
        deep_verified = service.originals.now()
        dataset_body = service.originals.archive.read(
            model.dataset_original_ref, model.dataset_original_hash
        )
        dataset = verify_payload(HistoryDatasetOriginalV2.model_validate_json(dataset_body))
        rows = capture_rows(
            session,
            dataset.provenance["row_readset"],
            (
                ("market_history_v2_models", model.model_id),
                ("market_history_v2_originals", model.dataset_id),
            ),
        )
        model_row = next(
            group.rows[0] for group in rows if group.table == "market_history_v2_models"
        )
        files = capture_files(
            (
                *dataset.provenance["file_readset"],
                (model_row[8], model_row[9]),
                (model.source_original_ref, model.source_original_hash),
                (model.dataset_original_ref, model.dataset_original_hash),
                (dataset.source_original_ref, dataset.source_original_hash),
                *((str(service.originals.source_root / path), digest) for path, digest in sources),
            )
        )
        context = self.context(session)
        body = encoded(
            [
                model.model_dump(mode="json"),
                [(group.table, group.rows) for group in rows],
                schemas,
                files,
                sources,
                context,
                deep_verified.isoformat(),
                threading.get_ident(),
            ]
        )
        manifest = hashlib.sha256(body).hexdigest()
        proof = ModelProof39(
            encoded(model.model_dump(mode="json")),
            rows,
            schemas,
            files,
            sources,
            context,
            deep_verified.isoformat(),
            self,
            threading.get_ident(),
            manifest,
            hmac.digest(self.key, manifest.encode(), "sha256"),
        )
        self._issued, self._members = proof, self.members(proof)
        service._sources(sources)
        verify_schemas39(session, schemas)
        verify_files(files)
        require_snapshot(session, boundary)
        return proof, model

    def authenticate(self, session, proof):
        if (
            type(proof) is not ModelProof39
            or proof is not self._issued
            or proof.issuer is not self
            or proof.thread_id != threading.get_ident()
            or not native_equal39(self.members(proof), self._members)
            or not native_equal39(proof.context, self.context(session))
            or not hmac.compare_digest(
                proof.authentication, hmac.digest(self.key, proof.manifest_hash.encode(), "sha256")
            )
        ):
            raise ValueError("history39_model_proof_changed")
        self.service._sources(proof.sources)
        if any(
            getattr(TransferAuthority39, name) is not function or function.__code__ is not code
            for name, function, code in LOADED_METHODS
        ):
            raise ValueError("history39_loaded_transfer_callable_changed")
        verify_schemas39(session, proof.schemas)
        if any(
            globals()[name] is not function or function.__code__ is not code
            for name, function, code in LOADED_FUNCTIONS
        ):
            raise ValueError("history39_loaded_transfer_callable_changed")

    def verify(self, session, proof):
        self.authenticate(session, proof)
        verify_rows(session, proof.rows)
        verify_files(proof.files)
        verify_schemas39(session, proof.schemas)


LOADED_METHODS = tuple(
    (name, getattr(TransferAuthority39, name), getattr(TransferAuthority39, name).__code__)
    for name in ("context", "members", "issue", "authenticate", "verify")
)
LOADED_FUNCTIONS = tuple(
    (name, globals()[name], globals()[name].__code__)
    for name in (
        "database_identity",
        "native_equal39",
        "capture_schemas39",
        "verify_schemas39",
        "file_fingerprint",
        "capture_files",
        "verify_files",
        "fetch_rows",
        "capture_rows",
        "verify_rows",
        "feature_candidates",
    )
)

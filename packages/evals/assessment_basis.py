"""Invalidate derived assessments against their actual original read dependencies.

Only the background builder records SQL produced by the real assessor. Readers
borrow a clean caller snapshot and replay those reads with a SQLite authorizer;
this receipt grants neither publication nor execution permission.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.metadata
import json
import math
import re
import sqlite3
import sys
from collections.abc import Callable, Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import event, select
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts.binding_contract_base import (
    shape_validation_scope,
)
from packages.evals.analytical_assessment import (
    AnalyticalAssessment,
    assess_outlook_in_session,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    AnalyticalOutlookRecord,
    Base,
    Database,
    RunRecord,
    RunRoleOutputRecord,
    as_utc,
)
from packages.provider_adapters.research.raw_capture import RawBodyArchive

# The loaded verifier ABI is part of both the manifest and the replay gate.
# A pre-release worker may see new disk bytes while still executing its old
# imports. Its v1 receipts must not survive the citation-v2 deployment.
SCHEMA = "analytical-assessment-basis.v3"
LOADED_VERIFIER_VERSION = "analytical-original-assessor.loaded.v1"
_ROOT = Path(__file__).resolve().parents[2]
_AUTHORIZE_KEY = "assessment_replay_authorizer_owned"
MAX_QUERIES = 1024
MAX_BASIS_BYTES = 2 * 1024 * 1024
MAX_ROW_BYTES = 64 * 1024 * 1024
MAX_ROWS = 65536
MAX_SOURCE_FILES = 8192
MAX_SOURCE_FILE_BYTES = 16 * 1024 * 1024
MAX_SOURCE_TOTAL_BYTES = 256 * 1024 * 1024
# Unique authenticated transport content, not cumulative reads across copies.
MAX_IMMUTABLE_BINDING_TOTAL_BYTES = 256 * 1024 * 1024
_SOURCE_STREAM_CHUNK_BYTES = 64 * 1024
_SOURCE_DIRS = (
    "packages/evals",
    "packages/contracts_py",
    "packages/kernel",
    "packages/provider_adapters",
    "packages/query_views",
    "packages/runtime_adapters",
    "packages/orchestration",
    "contracts/schemas",
    "packs",
)
_SOURCE_SUFFIXES = frozenset({".py", ".json", ".yaml", ".yml"})
_IGNORED_SOURCE_PARTS = frozenset({"__pycache__", "node_modules", "tests", "data", ".git"})
_READ_TABLES = frozenset(
    {
        "runs",
        "studies",
        "snapshots",
        "research_run_scopes",
        "run_role_outputs",
        "dsh_session_links",
        "dsh_session_prompts",
        "research_results",
        "research_evidence",
        "research_facts",
        "evidence_parsed_links",
        "parsed_versions",
        "fact_versions",
        "run_input_refs",
        "artifacts",
        "gate_decisions",
        "publication_decisions",
        "analytical_outlooks",
        "analytical_observations",
        "analytical_observation_attempts",
        "raw_captures",
        "data_jobs",
        "forecasts",
        "strategy_calibration_jobs",
        "strategy_calibration_productions",
        "strategy_probability_activations",
    }
)
_SAFE_FUNCTIONS = frozenset(
    {
        "count",
        "min",
        "max",
        "coalesce",
        "ifnull",
        "length",
        "typeof",
        "like",
    }
)
_RAW_REF = re.compile(r"raw://sha256/([a-f0-9]{64})\Z")


class AssessmentJob(Protocol):
    outlook_id: str
    status: str
    next_attempt_at: datetime
    evaluated_at: datetime | None
    verifier_version: str
    basis_json: str | None
    basis_hash: str | None
    assessment_json: str | None
    assessment_hash: str | None


def _canonical(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )


def _sha(value: str | bytes) -> str:
    return hashlib.sha256(value.encode("utf-8") if isinstance(value, str) else value).hexdigest()


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("assessment_basis_clock_timezone_required")
    return value.astimezone(UTC)


def _retained_payload_path(relative: Path) -> bool:
    """Only two installed immutable-input layouts; metadata remains in the ABI."""
    parts = relative.parts
    if len(parts) <= 5 or parts[:2] != ("packages", "kernel") or parts[4] != "root":
        return False
    if parts[2] == "research_recipe_versions":
        valid = re.fullmatch(r"(?:[a-f0-9]{8}|[a-f0-9]{64})", parts[3]) is not None
        index = _ROOT.joinpath(*parts[:4], "release.json")
    elif parts[2] == "market_component_versions":
        valid = re.fullmatch(r"[0-9]{8}T[0-9]{9}Z", parts[3]) is not None
        index = _ROOT.joinpath(*parts[:4], "index.json")
    else:
        return False
    return valid and index.is_file()


def _manifest_source_paths(
    directory: Path, *, checkpoint: Callable[[], None] | None,
) -> list[Path]:
    """Skip only subtrees whose every descendant was already excluded."""
    relative = directory.relative_to(_ROOT)
    if (any(part in _IGNORED_SOURCE_PARTS for part in relative.parts)
            or _retained_payload_path(relative / "__manifest_payload__")):
        return []
    paths: list[Path] = []
    for current, directories, filenames in directory.walk(top_down=True):
        if checkpoint is not None:
            checkpoint()
        directories[:] = [
            name for name in directories if name not in _IGNORED_SOURCE_PARTS
            and not _retained_payload_path(
                (current / name).relative_to(_ROOT) / "__manifest_payload__"
            )
        ]
        paths.extend(current / name for name in filenames)
    return sorted(paths)


def _immutable_component_binding(
    relative: Path, path: Path, *, checkpoint: Callable[[], None] | None,
) -> tuple[str, int] | None:
    """Authenticate every path before deduplicating its transport content."""
    parts = relative.parts
    if (
        len(parts) != 5
        or parts[:3] != ("packages", "kernel", "market_component_versions")
        or parts[4] != "source-binding.json"
        or re.fullmatch(r"[0-9]{8}T[0-9]{9}Z", parts[3]) is None
    ):
        return None
    index_path = _ROOT.joinpath(*parts[:4], "index.json")
    with index_path.open("rb") as stream:
        raw = stream.read(MAX_SOURCE_FILE_BYTES + 1)
    if checkpoint is not None:
        checkpoint()
    if len(raw) > MAX_SOURCE_FILE_BYTES:
        raise ValueError("assessment_basis_source_limit")
    try:
        index = json.loads(raw)
    except (ValueError, UnicodeError) as error:
        raise ValueError("assessment_basis_binding_index_invalid") from error
    if (
        not isinstance(index, dict)
        or index.get("schema_version") not in {
            "market-component-retained-release.v1", "market-component-retained-release.v2"
        }
    ):
        raise ValueError("assessment_basis_binding_index_invalid")
    digest, size = hashlib.sha256(), 0
    with path.open("rb") as stream:
        while True:
            if checkpoint is not None:
                checkpoint()
            block = stream.read(min(_SOURCE_STREAM_CHUNK_BYTES, MAX_SOURCE_FILE_BYTES - size + 1))
            if not block:
                break
            size += len(block)
            if size > MAX_SOURCE_FILE_BYTES:
                raise ValueError("assessment_basis_source_limit")
            digest.update(block)
    actual = digest.hexdigest()
    if index.get("source_binding_sha256") != actual:
        raise ValueError("assessment_basis_binding_index_invalid")
    return actual, size


def _method_source_identity_in_session(session: Session, outlook_id: str) -> dict[str, Any]:
    """Recheck exact selected archive bytes; never memo an authenticated verdict.

    Called within the real caller's SELECT-only physical snapshot. The current
    manifest covers live code and all archive metadata, while this exact Run
    chooses immutable old input bytes using the existing registered reader.
    """
    from packages.kernel.decision_hub_kernel.application import (
        research_graph_history_versions as history,
    )
    from packages.kernel.decision_hub_kernel.application.research_graph_dispatch import (
        read_graph_recipe_selection_for_run_in_session,
    )

    connection = session.connection()
    native = connection.connection.driver_connection
    transaction = session.get_transaction()
    changes = native.total_changes if isinstance(native, sqlite3.Connection) else None
    if (changes is None or not native.in_transaction
            or session.new or session.dirty or session.deleted):
        raise ValueError("assessment_basis_method_snapshot_required")
    with session.no_autoflush:
        outlook = session.get(AnalyticalOutlookRecord, outlook_id, populate_existing=True)
        if outlook is None:
            raise ValueError("assessment_basis_method_outlook_missing")
        if outlook.method_family == "ai_synthesis" and session.get(
            RunRecord, outlook.run_id, populate_existing=True
        ) is None:
            raise ValueError("assessment_basis_method_run_missing")
        rows = list(session.scalars(select(RunRoleOutputRecord).where(
            RunRoleOutputRecord.run_id == outlook.run_id,
            RunRoleOutputRecord.role == "graph_recipe_selection",
        ).execution_options(populate_existing=True)))
        if len(rows) > 1:
            raise ValueError("research_graph_recipe_selection_ambiguous")
        identity: dict[str, Any] = {
            "schema_version": "assessment-method-source-identity.v1",
            "outlook_id": outlook_id, "run_id": outlook.run_id,
            "method_family": outlook.method_family,
            "selection_payload_hash": rows[0].payload_hash if rows else None,
            "mode": "legacy_or_non_ai", "archive": None,
        }
        if rows:
            row = rows[0]
            if _sha(row.payload_json) != row.payload_hash:
                raise ValueError("research_history_recipe_original_changed")
            registration = history._original_recipe_registration(session, outlook.run_id)
            if registration is None:
                # This real typed reader admits only its current exact source;
                # missing old registration cannot masquerade as current.
                selected = read_graph_recipe_selection_for_run_in_session(session, outlook.run_id)
                if selected is None:
                    raise ValueError("assessment_basis_method_registration_missing")
                identity["mode"] = "current_selection"
            else:
                index, _payloads, _root = history._verified_release_uncached(registration)
                registered = history._REGISTRY_BINDING
                identity.update(mode="registered_archive", archive={
                    "directory": registration.directory,
                    "graph_hash": registration.graph_hash,
                    "index_hash": registration.index_hash,
                    "selector_hash": registration.selector_hash,
                    "all_files_hash": _sha(_canonical(index["all_files"])),
                    "registry_hash": registered.registry_hash if registered else None,
                    "release_receipt_hash": registered.receipt_hash if registered else None,
                })
    if (session.connection() is not connection or session.get_transaction() is not transaction
            or not native.in_transaction or native.total_changes != changes
            or session.new or session.dirty or session.deleted):
        raise ValueError("assessment_basis_method_snapshot_changed")
    return identity


def assessment_manifest_hash(
    *, memo: dict[Any, Any] | None = None,
    checkpoint: Callable[[], None] | None = None,
) -> str:
    """Hash actual implementation/schema/resource bytes; memo is one request only."""
    if checkpoint is not None:
        checkpoint()
    key = ("assessment-manifest.v1",)
    if memo is not None and key in memo:
        return str(memo[key])
    files: dict[str, str] = {}
    total = 0
    binding_total = 0
    binding_hashes: set[str] = set()
    for relative in _SOURCE_DIRS:
        if checkpoint is not None:
            checkpoint()
        directory = _ROOT / relative
        if not directory.is_dir():
            raise ValueError("assessment_basis_source_directory_missing")
        for path in _manifest_source_paths(directory, checkpoint=checkpoint):
            if checkpoint is not None:
                checkpoint()
            if not path.is_file() or path.suffix.lower() not in _SOURCE_SUFFIXES:
                continue
            if any(
                part in _IGNORED_SOURCE_PARTS
                for part in path.relative_to(_ROOT).parts
            ):
                continue
            if _retained_payload_path(path.relative_to(_ROOT)):
                continue
            if len(files) >= MAX_SOURCE_FILES:
                raise ValueError("assessment_basis_source_limit")
            binding = _immutable_component_binding(
                path.relative_to(_ROOT), path, checkpoint=checkpoint,
            )
            if binding is not None:
                digest, size = binding
                if digest not in binding_hashes:
                    binding_total += size
                    if binding_total > MAX_IMMUTABLE_BINDING_TOTAL_BYTES:
                        raise ValueError("assessment_basis_immutable_binding_limit")
                    binding_hashes.add(digest)
            else:
                with path.open("rb") as stream:
                    body = stream.read(MAX_SOURCE_FILE_BYTES + 1)
                if checkpoint is not None:
                    checkpoint()
                if len(body) > MAX_SOURCE_FILE_BYTES:
                    raise ValueError("assessment_basis_source_limit")
                total += len(body)
                if total > MAX_SOURCE_TOTAL_BYTES:
                    raise ValueError("assessment_basis_source_limit")
                digest = _sha(body)
            files[path.relative_to(_ROOT).as_posix()] = digest
    gateway = _ROOT / "packages/workbench_adapters/durable_research_gateway.py"
    if checkpoint is not None:
        checkpoint()
    body = gateway.read_bytes()
    if checkpoint is not None:
        checkpoint()
    if not body or len(body) > 16 * 1024 * 1024:
        raise ValueError("assessment_basis_gateway_source_limit")
    files[gateway.relative_to(_ROOT).as_posix()] = _sha(body)
    versions = {
        name: importlib.metadata.version(name)
        for name in (
            "pydantic",
            "pydantic-core",
            "jsonschema",
            "referencing",
            "SQLAlchemy",
            "PyYAML",
        )
    }
    result = _sha(
        _canonical(
            {
                "schema_version": SCHEMA,
                "files": files,
                "dependencies": versions,
                "python": sys.version,
                "sqlite": sqlite3.sqlite_version,
            }
        )
    )
    if memo is not None:
        memo[key] = result
    return result


# Freeze at import, before any job can discover or claim this verifier. A later
# file release cannot relabel the still-loaded reader with its new disk hash.
_LOADED_MANIFEST_HASH = assessment_manifest_hash()


def loaded_assessment_manifest_hash(
    *, memo: dict[Any, Any] | None = None,
    checkpoint: Callable[[], None] | None = None,
) -> str:
    """Return this process's imported verifier only while its files still match."""
    current = assessment_manifest_hash(memo=memo, checkpoint=checkpoint)
    if current != _LOADED_MANIFEST_HASH:
        raise ValueError("assessment_loaded_verifier_source_changed")
    return _LOADED_MANIFEST_HASH


def _loaded_verifier(manifest: str) -> dict[str, str]:
    return {"schema_version": LOADED_VERIFIER_VERSION, "manifest_hash": manifest}


def _scalar(value: Any) -> list[Any]:
    kind = type(value)
    if value is None:
        return ["null", None]
    if kind is str:
        value.encode("utf-8", errors="strict")
        return ["str", value]
    if kind is bool:
        return ["bool", value]
    if kind is int and -(2**63) <= value < 2**63:
        return ["int", value]
    if kind is float and math.isfinite(value):
        return ["float", value.hex()]
    if kind is bytes:
        return ["bytes", base64.b64encode(value).decode("ascii")]
    raise ValueError("assessment_basis_sql_native_type_required")


def _unscalar(value: Any) -> Any:
    if type(value) is not list or len(value) != 2 or type(value[0]) is not str:
        raise ValueError("assessment_basis_parameter_invalid")
    tag, body = value
    if tag == "null" and body is None:
        return None
    if tag == "str" and type(body) is str:
        return body
    if tag == "bool" and type(body) is bool:
        return body
    if tag == "int" and type(body) is int and -(2**63) <= body < 2**63:
        return body
    if tag == "float" and type(body) is str:
        result = float.fromhex(body)
        if math.isfinite(result) and result.hex() == body:
            return result
    if tag == "bytes" and type(body) is str:
        result = base64.b64decode(body, validate=True)
        if base64.b64encode(result).decode("ascii") == body:
            return result
    raise ValueError("assessment_basis_parameter_invalid")


def _parameters(value: Any) -> dict[str, Any]:
    if type(value) is tuple or type(value) is list:
        return {"kind": "positional", "values": [_scalar(item) for item in value]}
    if type(value) is dict and all(type(key) is str for key in value):
        return {"kind": "named", "values": {key: _scalar(value[key]) for key in sorted(value)}}
    raise ValueError("assessment_basis_parameter_invalid")


def _unparameters(value: Any) -> tuple[Any, ...] | dict[str, Any]:
    if type(value) is not dict or set(value) != {"kind", "values"}:
        raise ValueError("assessment_basis_parameter_invalid")
    if value["kind"] == "positional" and type(value["values"]) is list:
        return tuple(_unscalar(item) for item in value["values"])
    if value["kind"] == "named" and type(value["values"]) is dict:
        if all(type(key) is str for key in value["values"]):
            return {key: _unscalar(item) for key, item in value["values"].items()}
    raise ValueError("assessment_basis_parameter_invalid")


def _select(statement: Any) -> str:
    if type(statement) is not str or not re.match(r"(?is)^\s*SELECT\b", statement):
        raise ValueError("assessment_basis_select_only")
    if any(token in statement for token in (";", "--", "/*", "*/", "\x00")):
        raise ValueError("assessment_basis_select_only")
    if len(statement.encode("utf-8")) > MAX_BASIS_BYTES:
        raise ValueError("assessment_basis_query_limit")
    return statement


def _native(connection: Connection) -> sqlite3.Connection:
    native = connection.connection.driver_connection
    if connection.dialect.name != "sqlite" or not isinstance(native, sqlite3.Connection):
        raise ValueError("assessment_basis_sqlite_required")
    if not native.in_transaction or connection.info.get(_AUTHORIZE_KEY) is not True:
        raise ValueError("assessment_basis_owned_snapshot_required")
    return native


@contextmanager
def _readonly(connection: Connection) -> Generator[sqlite3.Connection, None, None]:
    """Caller explicitly owns the callback; sqlite has no get_authorizer API."""
    native = _native(connection)
    known = _READ_TABLES & frozenset(Base.metadata.tables)
    # Never allow a connection-local UDF to impersonate a safe built-in.
    functions = native.execute("PRAGMA function_list").fetchall()
    overridden = {str(row[0]).lower() for row in functions if row[1] == 0}
    safe = {str(row[0]).lower() for row in functions if row[1] == 1} & _SAFE_FUNCTIONS
    safe -= overridden

    def authorize(
        action: int, first: str | None, second: str | None, database: str | None, origin: str | None
    ) -> int:
        if action == sqlite3.SQLITE_SELECT:
            return sqlite3.SQLITE_OK
        if (
            action == sqlite3.SQLITE_READ
            and database == "main"
            and first in known
            and origin is None
        ):
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_FUNCTION and (second or "").lower() in safe:
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    native.set_authorizer(authorize)
    try:
        yield native
    finally:
        native.set_authorizer(None)


def _future_time(value: Any, assessed_at: datetime) -> datetime | None:
    if type(value) is not str or not re.fullmatch(
        r"\d{4}-\d\d-\d\d[ T]\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d)?",
        value,
    ):
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    parsed = as_utc(parsed)
    return parsed if parsed > assessed_at else None


def _query_fingerprint(
    native: sqlite3.Connection, statement: str, parameters: Any, assessed_at: datetime,
    *, checkpoint: Callable[[], None] | None = None,
) -> dict[str, Any]:
    if checkpoint is not None:
        checkpoint()
    cursor = native.execute(_select(statement), _unparameters(parameters))
    try:
        columns = [item[0] for item in cursor.description or ()]
        digest = hashlib.sha256(_canonical(columns).encode("utf-8"))
        count, size = 0, 0
        future: datetime | None = None
        while rows := cursor.fetchmany(128):
            if checkpoint is not None:
                checkpoint()
            for row in rows:
                encoded = _canonical([_scalar(value) for value in row]).encode("utf-8")
                count += 1
                size += len(encoded)
                if count > MAX_ROWS or size > MAX_ROW_BYTES:
                    raise ValueError("assessment_basis_rows_limit")
                digest.update(b"\0" + encoded)
                for value in row:
                    boundary = _future_time(value, assessed_at)
                    if boundary is not None and (future is None or boundary < future):
                        future = boundary
        digest.update(f"\0rows:{count}".encode())
        return {
            "columns": columns,
            "row_count": count,
            "row_byte_count": size,
            "row_hash": digest.hexdigest(),
            "next_time_boundary_at": future.isoformat() if future is not None else None,
        }
    finally:
        cursor.close()


def _archive_record(
    archive: RawBodyArchive, reference: str
) -> tuple[dict[str, Any], bytes | None, Exception | None]:
    try:
        body = archive.read(reference)
        if type(body) is not bytes:
            raise ValueError("assessment_basis_archive_bytes_required")
        return (
            {"ref": reference, "status": "present", "sha256": _sha(body), "error_code": None},
            body,
            None,
        )
    except (ValueError, OSError) as error:
        raw_hash: str | None = None
        status = "missing" if isinstance(error, FileNotFoundError) else "unverifiable"
        match = _RAW_REF.fullmatch(reference)
        if match and type(archive) is RawBodyArchive:
            digest = match[1]
            try:
                raw_hash = _sha((archive.root / digest[:2] / digest).read_bytes())
                status = "corrupt"
            except FileNotFoundError:
                status = "missing"
            except OSError:
                status = "unverifiable"
        code = str(error)
        if not code or len(code) > 100 or not code.replace("_", "").isalnum():
            code = type(error).__name__
        return (
            {"ref": reference, "status": status, "sha256": raw_hash, "error_code": code},
            None,
            error,
        )


class _ArchiveRecorder(RawBodyArchive):
    def __init__(self, archive: RawBodyArchive):
        super().__init__(archive.root)
        self.archive = archive
        self.records: dict[str, dict[str, Any]] = {}

    def read(self, ref: str) -> bytes:
        reference = ref
        record, body, error = _archive_record(self.archive, reference)
        previous = self.records.get(reference)
        if previous is not None and previous != record:
            raise ValueError("assessment_basis_archive_changed_during_build")
        self.records[reference] = record
        if error is not None:
            raise error
        assert body is not None
        return body



def _daily_runtime_hash(assessment: AnalyticalAssessment) -> str | None:
    # Decimal context/loaded callable changes are not disk changes. This hash
    # is re-evaluated per build/replay, never reused from SQL/archive memo.
    if (assessment.method_scope != "daily_rule_loaded_implementation"
            or assessment.method_status != "verified"):
        return None
    from packages.kernel.decision_hub_kernel.application.daily_execution_binding import (
        stable_daily_recipe,
    )
    from packages.provider_adapters.historical.research import loaded_daily_implementation

    identity = loaded_daily_implementation().identity()
    if stable_daily_recipe(identity) != assessment.stable_recipe_hash:
        raise ValueError("assessment_basis_daily_loaded_identity_changed")
    return _sha(_canonical(identity.model_dump(mode="json")))


def build_assessment_basis(
    database: Database, outlook_id: str, *, assessed_at: datetime, archive: RawBodyArchive | None,
    source_memo: dict[Any, Any] | None = None,
    checkpoint: Callable[[], None] | None = None,
) -> tuple[AnalyticalAssessment, dict[str, Any]]:
    """Run the real assessor once in a fresh physical read snapshot; never write."""
    now = _aware(assessed_at)
    manifest = loaded_assessment_manifest_hash(memo=source_memo, checkpoint=checkpoint)
    queries: dict[str, dict[str, Any]] = {}
    recorder = _ArchiveRecorder(archive) if archive is not None else None
    with database.session() as session:
        connection = session.connection()
        native = connection.connection.driver_connection
        if connection.dialect.name != "sqlite" or not isinstance(native, sqlite3.Connection):
            raise ValueError("assessment_basis_sqlite_required")
        if native.in_transaction or session.identity_map or session.new or session.dirty:
            raise ValueError("assessment_basis_fresh_session_required")
        connection.exec_driver_sql("BEGIN")
        connection.info[_AUTHORIZE_KEY] = True

        def capture(
            _connection: Any,
            _cursor: Any,
            statement: str,
            parameters: Any,
            _context: Any,
            executemany: bool,
        ) -> None:
            if executemany:
                raise ValueError("assessment_basis_select_only")
            item = {"statement": _select(statement), "parameters": _parameters(parameters)}
            key = _canonical(item)
            if key in queries:
                return
            queries[key] = item
            if len(queries) > MAX_QUERIES or sum(len(key.encode("utf-8")) for key in queries) > (
                MAX_BASIS_BYTES
            ):
                raise ValueError("assessment_basis_query_limit")

        try:
            with _readonly(connection) as guarded, session.no_autoflush, shape_validation_scope():
                event.listen(connection, "before_cursor_execute", capture)
                try:
                    method_source_identity = _method_source_identity_in_session(session, outlook_id)
                    if checkpoint is not None:
                        checkpoint()
                    assessment = assess_outlook_in_session(
                        session,
                        outlook_id,
                        assessed_at=now,
                        archive=recorder,
                    )
                    if method_source_identity != _method_source_identity_in_session(
                        session, outlook_id
                    ):
                        raise ValueError("assessment_basis_method_source_changed_during_build")
                finally:
                    event.remove(connection, "before_cursor_execute", capture)
                daily_runtime_hash = _daily_runtime_hash(assessment)
                if checkpoint is not None:
                    checkpoint()
                for item in queries.values():
                    item.update(
                        _query_fingerprint(
                            guarded, item["statement"], item["parameters"], now,
                            checkpoint=checkpoint,
                        )
                    )
                    if (
                        sum(
                            query["row_byte_count"]
                            for query in queries.values()
                            if "row_byte_count" in query
                        )
                        > MAX_ROW_BYTES
                    ):
                        raise ValueError("assessment_basis_rows_limit")
        finally:
            connection.info.pop(_AUTHORIZE_KEY, None)
    if checkpoint is not None:
        checkpoint()
    if (manifest != loaded_assessment_manifest_hash(
            memo=source_memo, checkpoint=checkpoint)
            or daily_runtime_hash != _daily_runtime_hash(assessment)):
        raise ValueError("assessment_basis_implementation_changed_during_build")
    boundaries = [
        item["next_time_boundary_at"]
        for item in queries.values()
        if item["next_time_boundary_at"] is not None
    ]
    basis = {
        "schema_version": SCHEMA,
        "outlook_id": outlook_id,
        "assessed_at": now.isoformat(),
        "manifest_hash": manifest,
        "loaded_verifier": _loaded_verifier(manifest),
        "method_source_identity": method_source_identity,
        "assessment_hash": _sha(_canonical(assessment.model_dump(mode="json"))),
        "read_queries": list(queries.values()),
        "daily_runtime_identity_hash": daily_runtime_hash,
        "archive_enabled": archive is not None,
        "archive_reads": list(recorder.records.values()) if recorder is not None else [],
        "next_time_boundary_at": min(boundaries) if boundaries else None,
    }
    if not queries or len(_canonical(basis).encode("utf-8")) > MAX_BASIS_BYTES:
        raise ValueError("assessment_basis_query_limit")
    return assessment, basis



def build_assessment_basis_in_session(
    session: Session, outlook_id: str, *, assessed_at: datetime, archive: RawBodyArchive | None,
    source_memo: dict[Any, Any] | None = None,
    checkpoint: Callable[[], None] | None = None,
) -> tuple[AnalyticalAssessment, dict[str, Any]]:
    """Capture actual reads inside an already-owned physical read snapshot.

    This function installs no authorizer, opens no connection/transaction, and
    never flushes, commits, rolls back, or changes the caller's connection info.
    Its caller must own the SELECT-only boundary (e.g. prepare_originals).
    """
    now = _aware(assessed_at)
    manifest = loaded_assessment_manifest_hash(memo=source_memo, checkpoint=checkpoint)
    connection = session.connection()
    native = connection.connection.driver_connection
    if (connection.dialect.name != "sqlite" or not isinstance(native, sqlite3.Connection)
            or not native.in_transaction or session.new or session.dirty or session.deleted):
        raise ValueError("assessment_basis_borrowed_snapshot_required")
    transaction = connection.get_transaction()
    nested = connection.get_nested_transaction()
    changes = native.total_changes
    queries: dict[str, dict[str, Any]] = {}
    recorder = _ArchiveRecorder(archive) if archive is not None else None

    def capture(_connection: Any, _cursor: Any, statement: str, parameters: Any,
                _context: Any, executemany: bool) -> None:
        if executemany:
            raise ValueError("assessment_basis_select_only")
        item = {"statement": _select(statement), "parameters": _parameters(parameters)}
        key = _canonical(item)
        queries[key] = item
        if len(queries) > MAX_QUERIES or sum(len(key.encode("utf-8")) for key in queries) > MAX_BASIS_BYTES:
            raise ValueError("assessment_basis_query_limit")

    event.listen(connection, "before_cursor_execute", capture)
    try:
        with session.no_autoflush, shape_validation_scope():
            method_source_identity = _method_source_identity_in_session(session, outlook_id)
            if checkpoint is not None:
                checkpoint()
            assessment = assess_outlook_in_session(session, outlook_id, assessed_at=now, archive=recorder)
            if method_source_identity != _method_source_identity_in_session(session, outlook_id):
                raise ValueError("assessment_basis_method_source_changed_during_build")
        daily_runtime_hash = _daily_runtime_hash(assessment)
        if checkpoint is not None:
            checkpoint()
    finally:
        event.remove(connection, "before_cursor_execute", capture)
    for item in queries.values():
        item.update(_query_fingerprint(
            native, item["statement"], item["parameters"], now, checkpoint=checkpoint,
        ))
    if checkpoint is not None:
        checkpoint()
    if (session.new or session.dirty or session.deleted or not native.in_transaction
            or native.total_changes != changes or connection.get_transaction() is not transaction
            or connection.get_nested_transaction() is not nested
            or manifest != loaded_assessment_manifest_hash(
                memo=source_memo, checkpoint=checkpoint,
            )
            or daily_runtime_hash != _daily_runtime_hash(assessment)):
        raise ValueError("assessment_basis_borrowed_snapshot_changed")
    if sum(item["row_byte_count"] for item in queries.values()) > MAX_ROW_BYTES:
        raise ValueError("assessment_basis_rows_limit")
    boundaries = [item["next_time_boundary_at"] for item in queries.values()
                  if item["next_time_boundary_at"] is not None]
    basis = {
        "schema_version": SCHEMA, "outlook_id": outlook_id, "assessed_at": now.isoformat(),
        "manifest_hash": manifest, "loaded_verifier": _loaded_verifier(manifest),
        "method_source_identity": method_source_identity,
        "assessment_hash": _sha(_canonical(assessment.model_dump(mode="json"))),
        "read_queries": list(queries.values()), "daily_runtime_identity_hash": daily_runtime_hash,
        "archive_enabled": archive is not None,
        "archive_reads": list(recorder.records.values()) if recorder is not None else [],
        "next_time_boundary_at": min(boundaries) if boundaries else None,
    }
    if not queries or len(_canonical(basis).encode("utf-8")) > MAX_BASIS_BYTES:
        raise ValueError("assessment_basis_query_limit")
    return assessment, basis


def verify_assessment_basis_in_session(
    session: Session, basis: dict[str, Any], *, archive: RawBodyArchive,
) -> None:
    """Re-read exact captured queries/archives in the caller's physical snapshot.

    Used at a production's original capture time, not as today's assessment of
    a mutable observation. The immutable production keeps that original label.
    """
    native = session.connection().connection.driver_connection
    if not isinstance(native, sqlite3.Connection) or not native.in_transaction:
        raise ValueError("assessment_basis_borrowed_snapshot_required")
    if basis.get("schema_version") != SCHEMA or basis.get("manifest_hash") != loaded_assessment_manifest_hash():
        raise ValueError("assessment_basis_implementation_unavailable")
    if basis.get("method_source_identity") != _method_source_identity_in_session(
        session, basis["outlook_id"]
    ):
        raise ValueError("assessment_basis_method_source_changed")
    now = _aware(datetime.fromisoformat(basis["assessed_at"]))
    queries = basis.get("read_queries")
    if type(queries) is not list or not queries or len(queries) > MAX_QUERIES:
        raise ValueError("assessment_basis_query_limit")
    for item in queries:
        expected = {key: item[key] for key in ("columns", "row_count", "row_byte_count", "row_hash", "next_time_boundary_at")}
        if _query_fingerprint(native, item["statement"], item["parameters"], now) != expected:
            raise ValueError("calibration_original_readset_changed")
    for item in basis["archive_reads"]:
        record, _body, error = _archive_record(archive, item["ref"])
        if error is not None or record != item:
            raise ValueError("calibration_original_archive_changed")


def read_valid_assessment(
    session: Session,
    job: AssessmentJob,
    *,
    now: datetime,
    archive: RawBodyArchive | None,
    memo: dict[Any, Any] | None = None,
) -> AnalyticalAssessment | None:
    """Return a derived view only while every captured original read still matches.

    The caller owns a clean explicit SQLite read snapshot and marks its Connection
    info ``assessment_replay_authorizer_owned=True``. No verified verdict is memoed;
    only exact read-query/archive fingerprints may be shared within that snapshot.
    """
    current = _aware(now)
    try:
        due = as_utc(job.next_attempt_at)
        if (
            job.status != "completed"
            or not job.basis_json
            or not job.basis_hash
            or not job.assessment_json
            or not job.assessment_hash
            or job.evaluated_at is None
            or due <= current
        ):
            return None
        if (
            _sha(job.basis_json) != job.basis_hash
            or _sha(job.assessment_json) != job.assessment_hash
        ):
            return None
        if len(job.basis_json.encode("utf-8")) > MAX_BASIS_BYTES:
            return None
        basis = json.loads(job.basis_json)
        if type(basis) is not dict or basis.get("schema_version") != SCHEMA:
            return None
        if _canonical(basis) != job.basis_json:
            return None
        assessment = AnalyticalAssessment.model_validate_json(job.assessment_json)
        assessed = _aware(datetime.fromisoformat(basis["assessed_at"]))
        if (
            basis["outlook_id"] != job.outlook_id
            or assessment.outlook_id != job.outlook_id
            or assessment.assessed_at != assessed
            or assessed > current
            or as_utc(job.evaluated_at) != assessed
            or basis["assessment_hash"] != job.assessment_hash
            or _canonical(assessment.model_dump(mode="json")) != job.assessment_json
            or basis["manifest_hash"] != job.verifier_version
            or basis["manifest_hash"] != loaded_assessment_manifest_hash(memo=memo)
            or basis.get("loaded_verifier") != _loaded_verifier(basis["manifest_hash"])
            or basis["archive_enabled"] is not (archive is not None)
            or basis["daily_runtime_identity_hash"] != _daily_runtime_hash(assessment)
        ):
            return None
        boundary = basis.get("next_time_boundary_at")
        if boundary is not None and _aware(datetime.fromisoformat(boundary)) <= current:
            return None
        queries, archives = basis["read_queries"], basis["archive_reads"]
        if (
            type(queries) is not list
            or not 0 < len(queries) <= MAX_QUERIES
            or type(archives) is not list
            or len(archives) > MAX_QUERIES
            or session.new
            or session.dirty
            or session.deleted
        ):
            return None
        connection = session.connection()
        transaction = session.get_transaction()
        with _readonly(connection) as native:
            seen: set[str] = set()
            row_bytes = 0
            for item in queries:
                if type(item) is not dict or set(item) != {
                    "statement",
                    "parameters",
                    "columns",
                    "row_count",
                    "row_byte_count",
                    "row_hash",
                    "next_time_boundary_at",
                }:
                    return None
                statement = _select(item["statement"])
                identity = _canonical([statement, item["parameters"]])
                if identity in seen:
                    return None
                seen.add(identity)
                key = ("assessment-query.v1", connection, transaction, identity, assessed)
                fingerprint = memo.get(key) if memo is not None else None
                if fingerprint is None:
                    fingerprint = _query_fingerprint(
                        native, statement, item["parameters"], assessed
                    )
                    if memo is not None:
                        memo[key] = fingerprint
                if any(item[name] != fingerprint[name] for name in fingerprint):
                    return None
                row_bytes += fingerprint["row_byte_count"]
                if row_bytes > MAX_ROW_BYTES:
                    return None
            if basis.get("method_source_identity") != _method_source_identity_in_session(
                session, job.outlook_id
            ):
                return None
        for item in archives:
            if type(item) is not dict or set(item) != {"ref", "status", "sha256", "error_code"}:
                return None
            if archive is None or item["status"] == "unverifiable":
                return None
            key = ("assessment-archive.v1", archive, item["ref"])
            record = memo.get(key) if memo is not None else None
            if record is None:
                record, _body, _error = _archive_record(archive, item["ref"])
                if memo is not None:
                    memo[key] = record
            if record != item:
                return None
        return assessment
    except (ValueError, TypeError, KeyError, OSError, sqlite3.Error, AttributeError):
        return None

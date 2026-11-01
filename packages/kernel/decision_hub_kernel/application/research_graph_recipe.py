"""Factory-only Graph recipe selection; no upgrade of an existing Task."""

from __future__ import annotations

import ast
import hashlib
import json
import math
import os
import sqlite3
import sys
import threading
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from importlib.metadata import version
from pathlib import Path
from typing import Literal, cast
from weakref import WeakKeyDictionary

from pydantic import AwareDatetime, BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session, SessionTransaction

from packages.kernel.decision_hub_kernel.application.research_binding import (
    PACK_ROLE,
    TASK_ROLE,
    FrozenResearchBundle,
    read_research_bundle_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_publication_binding import (
    observation_json,
    observation_sha,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    DshSessionLinkRecord,
    RunRecord,
    RunRoleOutputRecord,
    as_utc,
)
from packages.kernel.decision_hub_kernel.ports.research_observation import (
    ObservedExecutionRef,
    Sha,
)

# Deliberately outside research_graph_%: a selection is not an admitted round.
SELECTION_ROLE = "graph_recipe_selection"
SELECTION_SCHEMA = "research-graph-recipe-selection.v2"
RECIPE_VERSION_V2 = "research-graph-projection.v2"
_ROOT = Path(__file__).resolve().parents[4]
_FINITE_METHOD_SOURCE_REF = (
    "docs/design/decision-product-control-2026-10-17/02_DECISION_METHOD_AND_EVALUATION.md"
)


_GRAPH_RECIPE_ROOTS = (
    "packages.kernel.decision_hub_kernel.application.research_graph_binding",
    "packages.kernel.decision_hub_kernel.application.research_graph_recipe",
    "packages.kernel.decision_hub_kernel.application.research_graph_projection_v2",
    "packages.kernel.decision_hub_kernel.decision.citation_membership_v2",
    "packages.kernel.decision_hub_kernel.decision.action_comparison",
    "packages.kernel.decision_hub_kernel.decision.perpetual_publication",
)


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class GraphRecipeDependencyV2(_Closed):
    path: str
    payload_hash: Sha


class GraphRecipeManifestV2(_Closed):
    schema_version: Literal["research-graph-recipe-manifest.v2"] = (
        "research-graph-recipe-manifest.v2"
    )
    source_files: list[GraphRecipeDependencyV2]
    schema_files: list[GraphRecipeDependencyV2]
    runtime_versions: dict[str, str]


class GraphRecipeSelectionV2(_Closed):
    schema_version: Literal["research-graph-recipe-selection.v2"] = SELECTION_SCHEMA
    run_id: str
    recipe_version: Literal["research-graph-projection.v2"] = RECIPE_VERSION_V2
    original_task_ref: ObservedExecutionRef
    original_pack_ref: ObservedExecutionRef
    manifest: GraphRecipeManifestV2
    dependency_hash: Sha
    recorded_at: AwareDatetime


@dataclass(frozen=True)
class FrozenGraphRecipeSelection:
    reference: ObservedExecutionRef
    receipt: GraphRecipeSelectionV2


@dataclass
class _CreationPermit:
    session: Session
    transaction: SessionTransaction
    driver: sqlite3.Connection
    run_id: str
    used: bool = False


_CREATION_PERMITS: ContextVar[tuple[_CreationPermit, ...]] = ContextVar(
    "graph_recipe_task_creation_permits", default=()
)


@contextmanager
def graph_recipe_task_creation_scope(session: Session, run_id: str) -> Generator[None]:
    """Prove absence before creating the Task in this exact caller writer.

    This nonserializable, one-use permit is not an API supplied claim. Existing
    Tasks cannot enter this scope, even when a caller knows their old timestamp.
    """
    connection = session.connection()
    driver = connection.connection.driver_connection
    transaction = session.get_transaction()
    if (
        connection.dialect.name != "sqlite"
        or not isinstance(driver, sqlite3.Connection)
        or not driver.in_transaction
        or transaction is None
    ):
        raise ValueError("research_graph_recipe_writer_required")
    if (
        session.scalar(
            select(RunRoleOutputRecord.output_id).where(
                RunRoleOutputRecord.run_id == run_id,
                RunRoleOutputRecord.role.in_([TASK_ROLE, PACK_ROLE, SELECTION_ROLE]),
            )
        )
        is not None
        or session.get(DshSessionLinkRecord, run_id, populate_existing=True) is not None
    ):
        raise ValueError("research_graph_recipe_existing_task_not_upgradeable")
    permit = _CreationPermit(session, transaction, driver, run_id)
    token = _CREATION_PERMITS.set((*_CREATION_PERMITS.get(), permit))
    try:
        yield
    finally:
        permit.used = True
        _CREATION_PERMITS.reset(token)


def _module_file(name: str) -> Path | None:
    if not name.startswith("packages."):
        return None
    pieces = name.split(".")
    while len(pieces) >= 2:
        path = _ROOT.joinpath(*pieces)
        for candidate in (path.with_suffix(".py"), path / "__init__.py"):
            if candidate.is_file():
                return candidate
        pieces.pop()
    return None


@dataclass(frozen=True)
class _ImportNamesMemo:
    payload_hash: str
    names: tuple[str, ...]


_IMPORT_NAMES_LOCK = threading.RLock()
_IMPORT_NAMES_MAX_ENTRIES = 1024
_IMPORT_NAMES_MAX_BYTES = 4 * 1024 * 1024
_import_names_memo: dict[str, _ImportNamesMemo] = {}
_import_names_bytes = 0


def _parse_import_names(relative: str, payload: bytes) -> tuple[str, ...]:
    if relative == _FINITE_METHOD_SOURCE_REF:
        return ()  # One declared method input is Markdown, never Python code.
    package_parts = relative.removesuffix(".py").split("/")[:-1]
    names: list[str] = []
    for node in ast.walk(ast.parse(payload, filename=relative)):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                prefix = package_parts[: len(package_parts) - node.level + 1]
                module = ".".join([*prefix, *(node.module or "").split(".")]).rstrip(".")
            else:
                module = node.module or ""
            names.extend([module, *(f"{module}.{alias.name}" for alias in node.names)])
    return tuple(names)


def _import_names(relative: str, payload: bytes, payload_hash: str) -> tuple[str, ...]:
    """Reuse only syntax-derived names; raw bytes/SHA and resolution stay fresh.

    Unknown or changed loaded helpers retain the original parsing path. No cache
    entry represents a source, schema, installation or execution permission.
    """
    global _import_names_bytes
    try:
        helpers = _memo_helpers()
    except Exception:
        helpers = None
    if helpers is None or not _same_memo_helpers(helpers, _LOADED_MEMO_HELPERS):
        return _parse_import_names(relative, payload)
    with _IMPORT_NAMES_LOCK:
        cached = _import_names_memo.get(relative)
    if cached is not None and cached.payload_hash == payload_hash:
        return cached.names
    names = _parse_import_names(relative, payload)
    # Preserve parse failures and never turn cache capacity into a proof gate.
    size = len(relative.encode("utf-8")) + 64 + sum(len(name.encode("utf-8")) for name in names)
    with _IMPORT_NAMES_LOCK:
        old = _import_names_memo.get(relative)
        old_size = 0 if old is None else (
            len(relative.encode("utf-8")) + 64
            + sum(len(name.encode("utf-8")) for name in old.names)
        )
        if (
            (old is not None or len(_import_names_memo) < _IMPORT_NAMES_MAX_ENTRIES)
            and _import_names_bytes - old_size + size <= _IMPORT_NAMES_MAX_BYTES
        ):
            _import_names_memo[relative] = _ImportNamesMemo(payload_hash, names)
            _import_names_bytes += size - old_size
    return names


def _build_graph_recipe_manifest_v2() -> GraphRecipeManifestV2:
    """Capture repository imports and schema bytes, freezing external runtime versions."""
    initial = (  # Read as a literal by the archive without executing the producer.
        "packages.kernel.decision_hub_kernel.application.research_graph_binding",
        "packages.kernel.decision_hub_kernel.application.research_graph_recipe",
        "packages.kernel.decision_hub_kernel.application.research_graph_projection_v2",
        "packages.kernel.decision_hub_kernel.decision.citation_membership_v2",
        "packages.kernel.decision_hub_kernel.decision.action_comparison",
        "packages.kernel.decision_hub_kernel.decision.perpetual_publication",
    )
    roots = initial if _GRAPH_RECIPE_ROOTS == initial else _GRAPH_RECIPE_ROOTS
    pending = [path for name in roots if (path := _module_file(name)) is not None]
    seen: dict[str, GraphRecipeDependencyV2] = {}
    while pending:
        path = pending.pop()
        relative = path.relative_to(_ROOT).as_posix()
        if relative in seen:
            continue
        payload = path.read_bytes()
        payload_hash = hashlib.sha256(payload).hexdigest()
        seen[relative] = GraphRecipeDependencyV2(path=relative, payload_hash=payload_hash)
        for name in _import_names(relative, payload, payload_hash):
            dependency = _module_file(name)
            if dependency is not None:
                pending.append(dependency)
        if len(seen) > 1024:
            raise ValueError("research_graph_recipe_dependency_limit")
    if "packages/kernel/decision_hub_kernel/application/perpetual_close_source.py" in seen:
        source = _ROOT / _FINITE_METHOD_SOURCE_REF
        seen[_FINITE_METHOD_SOURCE_REF] = GraphRecipeDependencyV2(
            path=_FINITE_METHOD_SOURCE_REF,
            payload_hash=hashlib.sha256(source.read_bytes()).hexdigest()
        )
    schemas = [
        GraphRecipeDependencyV2(
            path=path.relative_to(_ROOT).as_posix(),
            payload_hash=hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in sorted((_ROOT / "contracts" / "schemas").rglob("*"))
        if path.is_file() and path.suffix in {".json", ".yaml", ".yml"}
    ]
    return GraphRecipeManifestV2(
        source_files=[seen[key] for key in sorted(seen)],
        schema_files=schemas,
        runtime_versions={
            **{
                name: version(name)
                for name in (
                    "pydantic",
                    "pydantic-core",
                    "jsonschema",
                    "referencing",
                    "SQLAlchemy",
                    "PyYAML",
                )
            },
            "python": sys.version,
            "sqlite": sqlite3.sqlite_version,
        },
    )


# This is only a pure repository-manifest construction memo. All DB originals,
# permission/fence checks, loaded-source guards and retained resources are read
# independently by their original consumers on every request.
_MEMO_MAX_BYTES = 1_000_000
_MEMO_METADATA_RESERVE = 16384
_MEMO_LOCK = threading.RLock()
_MemoHelpers = tuple[tuple[object, object | None, str, str], ...]


@dataclass(frozen=True)
class _ManifestMemo:
    fingerprint: str
    helpers: _MemoHelpers
    payload: bytes


_manifest_memo: _ManifestMemo | None = None


def _memo_metadata(value: object) -> str | None:
    def primitive(item: object) -> bool:
        if item is None or type(item) in {bool, int, str}:
            return True
        if type(item) is float:
            return math.isfinite(item)
        if type(item) in {tuple, list}:
            return all(primitive(child) for child in cast(tuple[object, ...] | list[object], item))
        if type(item) is dict:
            return all(
                type(key) is str and primitive(child)
                for key, child in cast(dict[object, object], item).items()
            )
        return False

    return json.dumps(value, sort_keys=True, separators=(",", ":")) if primitive(value) else None


def _memo_helpers() -> _MemoHelpers | None:
    if type(_GRAPH_RECIPE_ROOTS) is not tuple or any(
        type(name) is not str for name in _GRAPH_RECIPE_ROOTS
    ):
        return None
    tokens: list[tuple[object, object | None, str, str]] = []
    for value in (
        _GRAPH_RECIPE_ROOTS,
        _build_graph_recipe_manifest_v2,
        _import_names,
        _parse_import_names,
        _module_file,
        _manifest_fingerprint,
        _source_resolution,
        _schema_read_set,
        _package_python_inventory,
        _manifest_runtime_versions,
        _memo_helpers,
        _memo_metadata,
        _same_memo_helpers,
        ast.parse,
        ast.Import,
        ast.ImportFrom,
        ast.walk,
        hashlib.sha256,
        math.isfinite,
        version,
        observation_json,
        GraphRecipeManifestV2,
        GraphRecipeDependencyV2,
        Path.read_bytes,
        Path.is_file,
        Path.rglob,
        Path.joinpath,
        Path.with_suffix,
        Path.resolve,
        Path.is_symlink,
        Path.is_junction,
        os.scandir,
    ):
        if getattr(value, "__closure__", None) is not None:
            return None
        defaults = _memo_metadata(getattr(value, "__defaults__", None))
        keywords = _memo_metadata(getattr(value, "__kwdefaults__", None))
        if defaults is None or keywords is None:
            return None
        tokens.append((value, getattr(value, "__code__", None), defaults, keywords))
    return tuple(tokens)


def _same_memo_helpers(left: _MemoHelpers | None, right: _MemoHelpers | None) -> bool:
    return (
        left is not None
        and right is not None
        and len(left) == len(right)
        and all(
            first[0] is second[0] and first[1] is second[1] and first[2:] == second[2:]
            for first, second in zip(left, right, strict=True)
        )
    )


def _manifest_runtime_versions() -> dict[str, str]:
    return {
        **{
            name: version(name)
            for name in (
                "pydantic",
                "pydantic-core",
                "jsonschema",
                "referencing",
                "SQLAlchemy",
                "PyYAML",
            )
        },
        "python": sys.version,
        "sqlite": sqlite3.sqlite_version,
    }


def _package_python_inventory(root: Path) -> tuple[str, ...] | None:
    # Every directory still resolves strictly, including aliases and reparse
    # points. String prefixes only avoid constructing Paths for ordinary files.
    pending: list[tuple[str, str, frozenset[Path]]] = [
        (str(root / "packages"), "packages", frozenset())
    ]
    names: list[str] = []
    directories = 0
    entries_seen = 0
    while pending:
        directory_name, prefix, ancestors = pending.pop()
        directory = Path(directory_name)
        real = directory.resolve(strict=True)
        if real in ancestors or directories >= 4096:
            return None
        directories += 1
        names.append(f"directory:{prefix}->{real}")
        next_ancestors = ancestors | {real}
        with os.scandir(directory) as entries:
            for entry in entries:
                entries_seen += 1
                if entries_seen > 65536:
                    return None
                if entry.is_dir(follow_symlinks=True):
                    if directories + len(pending) >= 4096:
                        return None
                    pending.append((entry.path, f"{prefix}/{entry.name}", next_ancestors))
                elif entry.is_file(follow_symlinks=True):
                    # Match Path.suffix exactly, including '..py' and excluding
                    # '.py' or trailing dots; splitext ignores leading-dot runs.
                    dot = entry.name.rfind(".")
                    if 0 < dot < len(entry.name) - 1 and entry.name[dot:].casefold() == ".py":
                        names.append(f"{prefix}/{entry.name}")
    return tuple(sorted(names))


def _manifest_fingerprint(manifest: GraphRecipeManifestV2) -> str | None:
    root = _ROOT.resolve(strict=True)
    names: set[str] = set(_GRAPH_RECIPE_ROOTS)
    sources: list[tuple[str, str]] = []
    for item in manifest.source_files:
        path = root / item.path
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != item.payload_hash:
            return None
        sources.append((item.path, str(path.resolve(strict=True))))
        names.update(_import_names(item.path, payload, item.payload_hash))
    resolutions = tuple(_source_resolution(name) for name in sorted(names))
    selected = {relative for _, relative, _ in resolutions if relative is not None}
    if "packages/kernel/decision_hub_kernel/application/perpetual_close_source.py" in selected:
        selected.add(_FINITE_METHOD_SOURCE_REF)
    # A new resolution between construction and eligibility cannot seed a
    # memo for an incomplete closure. All original source guards stay separate.
    if selected != {item.path for item in manifest.source_files}:
        return None
    schemas = _schema_read_set()
    runtime = _manifest_runtime_versions()
    if (
        [(name, digest) for name, digest, _ in schemas]
        != [(item.path, item.payload_hash) for item in manifest.schema_files]
        or runtime != manifest.runtime_versions
    ):
        return None
    return observation_sha(
        {
            "root": str(root),
            "initial_roots": _GRAPH_RECIPE_ROOTS,
            "source_resolutions": resolutions,
            "source_paths": sources,
            "source_files": [item.model_dump(mode="json") for item in manifest.source_files],
            "schema_files": schemas,
            "runtime_versions": runtime,
        }
    )


@dataclass(frozen=True, eq=False)
class PreparedGraphRecipeManifest:
    """Issued pure-source read set; no Run, permission or validation verdict."""

    root: Path
    payload: bytes
    source_paths: tuple[tuple[str, str], ...]
    resolutions: tuple[tuple[str, str | None, str | None], ...]
    schemas: tuple[tuple[str, str, str], ...]
    helpers: _MemoHelpers


@dataclass(frozen=True)
class _PreparedManifestScope:
    session: Session
    transaction: SessionTransaction
    driver: sqlite3.Connection
    prepared: PreparedGraphRecipeManifest


_PREPARED_MANIFESTS: WeakKeyDictionary[PreparedGraphRecipeManifest, str] = WeakKeyDictionary()
_PREPARED_MANIFEST_LOCK = threading.RLock()
_PREPARED_MANIFEST_SCOPE: ContextVar[_PreparedManifestScope | None] = ContextVar(
    "graph_recipe_prepared_source_scope", default=None,
)


def _source_resolution(name: str) -> tuple[str, str | None, str | None]:
    path = _module_file(name)
    return (name, path.relative_to(_ROOT).as_posix(), str(path.resolve(strict=True))) if (
        path is not None
    ) else (name, None, None)


def _schema_read_set() -> tuple[tuple[str, str, str], ...]:
    return tuple(
        (path.relative_to(_ROOT).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest(),
         str(path.resolve(strict=True)))
        for path in sorted((_ROOT / "contracts" / "schemas").rglob("*"))
        if path.is_file() and path.suffix in {".json", ".yaml", ".yml"}
    )


def _prepared_manifest_signature(value: PreparedGraphRecipeManifest) -> str:
    return observation_sha({
        "root": str(value.root), "payload": hashlib.sha256(value.payload).hexdigest(),
        "source_paths": value.source_paths, "resolutions": value.resolutions,
        "schemas": value.schemas,
    })


def _prepared_manifest_helpers() -> _MemoHelpers | None:
    inherited = _memo_helpers()
    if inherited is None:
        return None
    wrapped_attribute = "__wrapped__"
    own = (
        prepare_graph_recipe_manifest_v2, _source_resolution, _schema_read_set,
        _prepared_manifest_signature, _prepared_manifest_helpers,
        _check_prepared_manifest, _check_manifest_scope, _scoped_manifest,
        graph_recipe_prepared_manifest_scope,
        getattr(graph_recipe_prepared_manifest_scope, wrapped_attribute),
        PreparedGraphRecipeManifest, PreparedGraphRecipeManifest.__init__,
        _PreparedManifestScope, _PreparedManifestScope.__init__, graph_recipe_manifest_v2,
    )
    return (*inherited, *(
        (function, getattr(function, "__code__", None),
         _memo_metadata(getattr(function, "__defaults__", None)) or "unavailable",
         _memo_metadata(getattr(function, "__kwdefaults__", None)) or "unavailable")
        for function in own
    ))


def _check_prepared_manifest(value: PreparedGraphRecipeManifest) -> GraphRecipeManifestV2:
    if (
        not _same_memo_helpers(_prepared_manifest_helpers(), value.helpers)
        or not _same_memo_helpers(value.helpers, _LOADED_PREPARED_HELPERS)
        or _ROOT.resolve(strict=True) != value.root
    ):
        raise ValueError("research_graph_recipe_prepared_implementation_changed")
    manifest = GraphRecipeManifestV2.model_validate_json(value.payload)
    if len(value.source_paths) != len(manifest.source_files):
        raise ValueError("research_graph_recipe_prepared_source_changed")
    for item, (relative, resolved) in zip(manifest.source_files, value.source_paths, strict=True):
        path = _ROOT / relative
        if (
            item.path != relative or str(path.resolve(strict=True)) != resolved
            or hashlib.sha256(path.read_bytes()).hexdigest() != item.payload_hash
        ):
            raise ValueError("research_graph_recipe_prepared_source_changed")
    # Resolution is an input too: a previously absent, higher-priority module
    # can change a closure without modifying any retained source bytes.
    if tuple(_source_resolution(row[0]) for row in value.resolutions) != value.resolutions:
        raise ValueError("research_graph_recipe_prepared_resolution_changed")
    if (
        _schema_read_set() != value.schemas
        or [(name, digest) for name, digest, _ in value.schemas]
        != [(item.path, item.payload_hash) for item in manifest.schema_files]
        or _manifest_runtime_versions() != manifest.runtime_versions
    ):
        raise ValueError("research_graph_recipe_prepared_schema_runtime_changed")
    return manifest


def prepare_graph_recipe_manifest_v2() -> PreparedGraphRecipeManifest:
    """Read/parse the full original closure before a caller takes its writer."""
    helpers = _prepared_manifest_helpers()
    if helpers is None or not _same_memo_helpers(helpers, _LOADED_PREPARED_HELPERS):
        raise ValueError("research_graph_recipe_prepared_implementation_changed")
    manifest = _build_graph_recipe_manifest_v2()
    names = {
        "packages.kernel.decision_hub_kernel.application.research_graph_binding",
        "packages.kernel.decision_hub_kernel.application.research_graph_recipe",
        "packages.kernel.decision_hub_kernel.application.research_graph_projection_v2",
        "packages.kernel.decision_hub_kernel.decision.citation_membership_v2",
    }
    paths: list[tuple[str, str]] = []
    for item in manifest.source_files:
        path = _ROOT / item.path
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != item.payload_hash:
            raise ValueError("research_graph_recipe_preparation_source_changed")
        paths.append((item.path, str(path.resolve(strict=True))))
        names.update(name for name in _import_names(item.path, payload, item.payload_hash)
                     if name.startswith("packages."))
    if len(names) > 32768:
        raise ValueError("research_graph_recipe_preparation_resolution_limit")
    value = PreparedGraphRecipeManifest(
        _ROOT.resolve(strict=True), observation_json(manifest.model_dump(mode="json")).encode(),
        tuple(paths), tuple(_source_resolution(name) for name in sorted(names)),
        _schema_read_set(), helpers,
    )
    _check_prepared_manifest(value)
    with _PREPARED_MANIFEST_LOCK:
        _PREPARED_MANIFESTS[value] = _prepared_manifest_signature(value)
    return value


def _check_manifest_scope(scope: _PreparedManifestScope) -> None:
    connection = scope.session.connection()
    if (
        scope.session.get_transaction() is not scope.transaction
        or connection.connection.driver_connection is not scope.driver
        or not scope.driver.in_transaction
    ):
        raise ValueError("research_graph_recipe_prepared_writer_changed")


@contextmanager

def graph_recipe_prepared_manifest_scope(
    session: Session, prepared: PreparedGraphRecipeManifest,
) -> Generator[None]:
    """One physical writer rechecks the pure read set before and after use."""
    with _PREPARED_MANIFEST_LOCK:
        signature = _PREPARED_MANIFESTS.pop(prepared, None)
    if signature is None or signature != _prepared_manifest_signature(prepared):
        raise ValueError("research_graph_recipe_preparation_not_native")
    connection = session.connection()
    transaction = session.get_transaction()
    driver = connection.connection.driver_connection
    if (
        transaction is None or connection.dialect.name != "sqlite"
        or not isinstance(driver, sqlite3.Connection) or not driver.in_transaction
        or _PREPARED_MANIFEST_SCOPE.get() is not None
    ):
        raise ValueError("research_graph_recipe_prepared_writer_required")
    _check_prepared_manifest(prepared)
    scope = _PreparedManifestScope(session, transaction, driver, prepared)
    token = _PREPARED_MANIFEST_SCOPE.set(scope)
    try:
        yield
        _check_manifest_scope(scope)
        _check_prepared_manifest(prepared)
    finally:
        _PREPARED_MANIFEST_SCOPE.reset(token)


def _scoped_manifest() -> GraphRecipeManifestV2 | None:
    scope = _PREPARED_MANIFEST_SCOPE.get()
    if scope is None:
        return None
    _check_manifest_scope(scope)
    # A fresh typed clone cannot mutate the retained immutable source payload.
    return GraphRecipeManifestV2.model_validate_json(scope.prepared.payload)


def graph_recipe_manifest_v2() -> GraphRecipeManifestV2:
    """Reuse pure construction only after two fresh complete input checks."""
    scoped = _scoped_manifest()
    if scoped is not None:
        return scoped
    global _manifest_memo
    try:
        helpers = _memo_helpers()
    except Exception:
        helpers = None
    if helpers is None or not _same_memo_helpers(helpers, _LOADED_MEMO_HELPERS):
        return _build_graph_recipe_manifest_v2()
    with _MEMO_LOCK:
        memo = _manifest_memo
    if memo is not None and _same_memo_helpers(memo.helpers, helpers):
        try:
            # A fresh typed clone cannot mutate the bytes retained by the memo.
            clone = GraphRecipeManifestV2.model_validate_json(memo.payload)
            before = _manifest_fingerprint(clone)
            if before == memo.fingerprint and _same_memo_helpers(_memo_helpers(), helpers):
                after = _manifest_fingerprint(clone)
                if after == before and _same_memo_helpers(_memo_helpers(), helpers):
                    return clone
        except Exception:
            # A memo probe is never a new rejection. The original builder below
            # retains its own parse/read/runtime exception and output semantics.
            pass
    manifest = _build_graph_recipe_manifest_v2()
    try:
        payload = observation_json(manifest.model_dump(mode="json")).encode("utf-8")
        if len(payload) + _MEMO_METADATA_RESERVE <= _MEMO_MAX_BYTES:
            fingerprint = _manifest_fingerprint(manifest)
            if fingerprint is not None and _same_memo_helpers(_memo_helpers(), helpers):
                entry = _ManifestMemo(fingerprint, helpers, payload)
                with _MEMO_LOCK:
                    _manifest_memo = entry
    except Exception:
        # Serialization and eligibility cannot replace a valid builder output.
        pass
    return manifest


# Unknown or changed loaded helpers use the complete original builder every
# time. No new implementation is inferred from mutable runtime callable state.
_LOADED_MEMO_HELPERS = _memo_helpers()


def _ref(row: RunRoleOutputRecord) -> ObservedExecutionRef:
    recorded_at = as_utc(cast(datetime | None, row.created_at))
    if (
        recorded_at is None
        or hashlib.sha256(row.payload_json.encode()).hexdigest() != row.payload_hash
    ):
        raise ValueError("research_graph_recipe_original_row_invalid")
    return ObservedExecutionRef(
        output_id=row.output_id,
        role=row.role,
        schema_version=row.schema_version,
        payload_hash=row.payload_hash,
        recorded_at=recorded_at,
    )


def _original_rows(
    session: Session, run_id: str
) -> tuple[RunRoleOutputRecord, RunRoleOutputRecord]:
    bundle = read_research_bundle_in_session(session, run_id)
    if not isinstance(bundle, FrozenResearchBundle):
        raise ValueError("research_graph_recipe_prepared_bundle_required")
    rows = list(
        session.scalars(
            select(RunRoleOutputRecord)
            .where(
                RunRoleOutputRecord.run_id == run_id,
                RunRoleOutputRecord.role.in_([TASK_ROLE, PACK_ROLE]),
            )
            .execution_options(populate_existing=True)
        )
    )
    by_role = {row.role: row for row in rows}
    if len(rows) != 2 or set(by_role) != {TASK_ROLE, PACK_ROLE}:
        raise ValueError("research_graph_recipe_original_inputs_missing")
    return by_role[TASK_ROLE], by_role[PACK_ROLE]


def read_graph_recipe_selection_in_session(
    session: Session, run_id: str
) -> FrozenGraphRecipeSelection | None:
    rows = list(
        session.scalars(
            select(RunRoleOutputRecord)
            .where(RunRoleOutputRecord.run_id == run_id, RunRoleOutputRecord.role == SELECTION_ROLE)
            .execution_options(populate_existing=True)
        )
    )
    if not rows:
        return None
    if len(rows) != 1:
        raise ValueError("research_graph_recipe_selection_ambiguous")
    row = rows[0]
    reference = _ref(row)
    if row.schema_version != SELECTION_SCHEMA:
        raise ValueError("research_graph_recipe_schema_unsupported")
    value = GraphRecipeSelectionV2.model_validate_json(row.payload_json)
    task, pack = _original_rows(session, run_id)
    if (
        value.run_id != run_id
        or value.original_task_ref != _ref(task)
        or value.original_pack_ref != _ref(pack)
        or value.recorded_at != reference.recorded_at
        or value.recorded_at != value.original_task_ref.recorded_at
        or value.recorded_at != value.original_pack_ref.recorded_at
        or value.dependency_hash != observation_sha(value.manifest.model_dump(mode="json"))
        or observation_json(value.model_dump(mode="json")) != row.payload_json
        or row.output_id
        != "graph_recipe_selection_" + observation_sha(value.model_dump(mode="json"))
    ):
        raise ValueError("research_graph_recipe_selection_changed")
    # The selection is frozen before the first execution, not merely before
    # an admission or later remapping generation.
    execution_rows = (
        session.execute(
            select(RunRoleOutputRecord.created_at).where(
                RunRoleOutputRecord.run_id == run_id,
                RunRoleOutputRecord.role.like("dsh_execution_request:%"),
            )
        )
        .scalars()
        .all()
    )
    execution_clocks = [as_utc(cast(datetime | None, created)) for created in execution_rows]
    if any(created is None or created < value.recorded_at for created in execution_clocks):
        raise ValueError("research_graph_recipe_selection_after_dispatch")
    # A valid saved declaration does not prove today's implementation can
    # replay it. Keep this fail-closed outcome distinct from changed originals.
    if value.manifest != graph_recipe_manifest_v2():
        raise ValueError("research_graph_recipe_current_implementation_unavailable")
    return FrozenGraphRecipeSelection(reference, value)


def freeze_graph_recipe_selection_in_session(
    session: Session, *, run_id: str, recorded_at: datetime
) -> FrozenGraphRecipeSelection:
    connection = session.connection()
    driver = connection.connection.driver_connection
    if (
        connection.dialect.name != "sqlite"
        or not isinstance(driver, sqlite3.Connection)
        or not driver.in_transaction
    ):
        raise ValueError("research_graph_recipe_writer_required")
    if recorded_at.tzinfo is None or recorded_at.utcoffset() is None:
        raise ValueError("research_graph_recipe_clock_timezone_required")
    if existing := read_graph_recipe_selection_in_session(session, run_id):
        if existing.receipt.recorded_at != recorded_at:
            raise ValueError("research_graph_recipe_original_conflict")
        return existing
    permits = [
        permit
        for permit in _CREATION_PERMITS.get()
        if permit.session is session
        and permit.transaction is session.get_transaction()
        and permit.driver is driver
        and permit.run_id == run_id
        and not permit.used
    ]
    if len(permits) != 1:
        raise ValueError("research_graph_recipe_new_task_scope_required")
    run = session.get(RunRecord, run_id, populate_existing=True)
    task, pack = _original_rows(session, run_id)
    if (
        run is None
        or run.status not in {"admitted", "dispatched", "running"}
        or run.artifact_id is not None
        or as_utc(task.created_at) != recorded_at
        or as_utc(pack.created_at) != recorded_at
        or session.get(DshSessionLinkRecord, run_id, populate_existing=True) is not None
        or session.scalar(
            select(RunRoleOutputRecord.output_id).where(
                RunRoleOutputRecord.run_id == run_id,
                (RunRoleOutputRecord.role.like("dsh_execution_request:%"))
                | (RunRoleOutputRecord.role.like("research_graph_%")),
            )
        )
        is not None
    ):
        raise ValueError("research_graph_recipe_existing_task_not_upgradeable")
    manifest = graph_recipe_manifest_v2()
    value = GraphRecipeSelectionV2(
        run_id=run_id,
        original_task_ref=_ref(task),
        original_pack_ref=_ref(pack),
        manifest=manifest,
        dependency_hash=observation_sha(manifest.model_dump(mode="json")),
        recorded_at=recorded_at,
    )
    encoded = observation_json(value.model_dump(mode="json"))
    row = RunRoleOutputRecord(
        output_id="graph_recipe_selection_" + observation_sha(value.model_dump(mode="json")),
        run_id=run_id,
        role=SELECTION_ROLE,
        schema_version=SELECTION_SCHEMA,
        payload_json=encoded,
        payload_hash=hashlib.sha256(encoded.encode()).hexdigest(),
        created_at=recorded_at,
    )
    session.add(row)
    session.flush()
    permits[0].used = True
    return FrozenGraphRecipeSelection(_ref(row), value)


_LOADED_PREPARED_HELPERS = _prepared_manifest_helpers()

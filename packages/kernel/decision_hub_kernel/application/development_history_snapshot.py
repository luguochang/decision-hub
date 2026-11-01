"""Finite exact-source snapshots for explicitly bootstrapped development readers."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path, PurePosixPath
from typing import Any, Protocol
from uuid import uuid4

from .research_graph_recipe import GraphRecipeManifestV2, graph_recipe_manifest_v2

_FORMAT = "development-history-snapshot.v1"
_SHA = re.compile(r"[a-f0-9]{64}\Z")
_MAX_FILE = 2 * 1024 * 1024
_MAX_TOTAL = 32 * 1024 * 1024
_MAX_FILES = 2048
_SHARED = (
    "packages/kernel/decision_hub_kernel/persistence/db.py",
    "packages/kernel/decision_hub_kernel/application/research_context_selection_versions.py",
)


class DevelopmentRuntimePaths(Protocol):
    @property
    def root(self) -> Path: ...

    @property
    def data_dir(self) -> Path: ...


def _canonical(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    )


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def native_history_path(path: Path) -> Path:
    """Adapt validated absolute logical paths for IO, never stored identities."""
    if os.name != "nt":
        return path
    text = str(path)
    prefix = "\\\\?\\"
    if (not path.is_absolute() or text.startswith((prefix, "\\\\.\\"))
            or ".." in path.parts):
        raise ValueError("development_history_path_invalid")
    if text.startswith("\\\\"):
        return Path(prefix + "UNC\\" + text[2:])
    if re.fullmatch(r"[A-Za-z]:\\.*", text) is None:
        raise ValueError("development_history_path_invalid")
    return Path(prefix + text)


def _plain(path: Path, *, allow_source_hardlinks: bool = False) -> None:
    for item in (*reversed(path.parents), path):
        try:
            observed = native_history_path(item).lstat()
        except FileNotFoundError:
            continue
        if (stat.S_ISLNK(observed.st_mode) or getattr(observed, "st_file_attributes", 0) & 0x400
                or stat.S_ISREG(observed.st_mode) and observed.st_nlink > 1
                and not (allow_source_hardlinks and item == path)):
            raise ValueError("development_history_path_invalid")
        if item != path and not stat.S_ISDIR(observed.st_mode):
            raise ValueError("development_history_path_invalid")


def _relative(value: str) -> str:
    path = PurePosixPath(value)
    if (value in {"", "."} or path.is_absolute() or "\\" in value or ":" in value
            or any(ord(char) < 32 for char in value)
            or any(part in {".", ".."} or part.endswith((".", " ")) for part in path.parts)
            or path.as_posix() != value):
        raise ValueError("development_history_path_invalid")
    return value


def _layout(repository_root: Path, development_root: Path) -> Path:
    repository, development = repository_root.absolute(), development_root.absolute()
    if (repository != repository_root or development != development_root
            or development.parent != repository / "data/development"
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,47}", development.name) is None):
        raise ValueError("development_history_path_invalid")
    _plain(repository)
    _plain(development)
    store = development / "runtime/history-snapshots"
    _plain(store)
    return store


def _files(manifest: GraphRecipeManifestV2) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in (*manifest.source_files, *manifest.schema_files):
        path = _relative(item.path)
        if path in result or _SHA.fullmatch(item.payload_hash) is None:
            raise ValueError("development_history_snapshot_invalid")
        result[path] = item.payload_hash
    if not result or len(result) > _MAX_FILES or any(path not in result for path in _SHARED):
        raise ValueError("development_history_snapshot_invalid")
    return result


def _read(path: Path, *, allow_source_hardlinks: bool = False) -> bytes:
    _plain(path, allow_source_hardlinks=allow_source_hardlinks)
    native = native_history_path(path)
    try:
        if not native.exists():
            raise ValueError("development_history_snapshot_unavailable")
        if not native.is_file() or native.stat().st_size > _MAX_FILE:
            raise ValueError("development_history_snapshot_invalid")
        raw = native.read_bytes()
    except OSError as error:
        raise ValueError("development_history_snapshot_unavailable") from error
    if len(raw) > _MAX_FILE:
        raise ValueError("development_history_snapshot_invalid")
    return raw


def _payloads(
    root: Path, files: dict[str, str], *, allow_source_hardlinks: bool = False,
) -> dict[str, bytes]:
    payloads: dict[str, bytes] = {}
    total = 0
    for path, expected in files.items():
        raw = _read(root / path, allow_source_hardlinks=allow_source_hardlinks)
        total += len(raw)
        if _sha(raw) != expected or total > _MAX_TOTAL:
            raise ValueError("development_history_snapshot_invalid")
        payloads[path] = raw
    return payloads


def _runtime(manifest: GraphRecipeManifestV2) -> None:
    import sqlite3
    import sys

    expected_names = {
        "pydantic", "pydantic-core", "jsonschema", "referencing", "SQLAlchemy", "PyYAML",
        "python", "sqlite",
    }
    if set(manifest.runtime_versions) != expected_names:
        raise ValueError("development_history_snapshot_invalid")
    for name, expected in manifest.runtime_versions.items():
        actual = (
            sys.version if name == "python"
            else sqlite3.sqlite_version if name == "sqlite" else version(name)
        )
        if actual != expected:
            raise ValueError("development_history_snapshot_invalid")


@dataclass(frozen=True)
class DevelopmentHistorySnapshot:
    repository_root: Path
    development_root: Path
    root: Path
    dependency_hash: str
    manifest_json: str
    index_sha256: str


def _inventory(root: Path, expected: set[str]) -> None:
    actual: set[str] = set()
    native_root = native_history_path(root)
    for directory, directories, files in os.walk(native_root, followlinks=False):
        base = Path(directory)
        logical_base = root / base.relative_to(native_root)
        _plain(logical_base)
        for name in directories:
            _plain(logical_base / name)
        for name in files:
            path = base / name
            _plain(logical_base / name)
            actual.add(path.relative_to(native_root).as_posix())
            if (len(actual) > _MAX_FILES
                    or path.relative_to(native_root).as_posix() not in expected):
                raise ValueError("development_history_snapshot_invalid")
    if actual != expected:
        raise ValueError("development_history_snapshot_invalid")


def _index(manifest: GraphRecipeManifestV2, payloads: dict[str, bytes]) -> dict[str, Any]:
    body = manifest.model_dump(mode="json")
    return {
        "schema_version": _FORMAT,
        "dependency_hash": _sha(_canonical(body).encode()),
        "manifest": body,
        "private_original_orm": True,
        "files": [
            {"path": path, "payload_hash": _sha(raw), "bytes": len(raw)}
            for path, raw in sorted(payloads.items())
        ],
        "shared_original_hashes": {path: _sha(payloads[path]) for path in _SHARED},
    }


def read_development_history_snapshot(
    store: DevelopmentHistorySnapshot, manifest_json: str, dependency_hash: str,
) -> tuple[DevelopmentHistorySnapshot, dict[str, bytes]]:
    """Reopen only the saved manifest's fixed identity child; never capture on read."""
    base = _layout(store.repository_root, store.development_root)
    if _SHA.fullmatch(dependency_hash) is None:
        raise ValueError("development_history_snapshot_invalid")
    manifest = GraphRecipeManifestV2.model_validate_json(manifest_json)
    canonical = _canonical(manifest.model_dump(mode="json"))
    if canonical != manifest_json or _sha(canonical.encode()) != dependency_hash:
        raise ValueError("development_history_snapshot_invalid")
    _runtime(manifest)
    directory = base / dependency_hash
    root = directory / "root"
    files = _files(manifest)
    raw_index = _read(directory / "index.json")
    try:
        index = json.loads(raw_index)
    except (ValueError, UnicodeError) as error:
        raise ValueError("development_history_snapshot_invalid") from error
    payloads = _payloads(root, files)
    _inventory(root, set(files))
    if {path.name for path in native_history_path(directory).iterdir()} != {"root", "index.json"}:
        raise ValueError("development_history_snapshot_invalid")
    if (raw_index != _canonical(_index(manifest, payloads)).encode()
            or index != _index(manifest, payloads)):
        raise ValueError("development_history_snapshot_invalid")
    for path in _SHARED:
        if _sha(_read(store.repository_root / path, allow_source_hardlinks=True)) != files[path]:
            raise ValueError("development_history_snapshot_invalid")
    return DevelopmentHistorySnapshot(
        store.repository_root, store.development_root, root, dependency_hash,
        canonical, _sha(raw_index),
    ), payloads


def capture_current_development_snapshot(
    repository_root: Path, development: DevelopmentRuntimePaths,
) -> DevelopmentHistorySnapshot:
    """File-only bootstrap: exact current closure, immutable hash-named child."""
    store = _layout(repository_root, development.root)
    if development.data_dir != development.root / "data":
        raise ValueError("development_history_path_invalid")
    manifest = graph_recipe_manifest_v2()
    _runtime(manifest)
    manifest_json = _canonical(manifest.model_dump(mode="json"))
    identity = _sha(manifest_json.encode())
    descriptor = DevelopmentHistorySnapshot(
        repository_root, development.root, store / identity / "root", identity, manifest_json, "",
    )
    files = _files(manifest)
    payloads = _payloads(repository_root, files, allow_source_hardlinks=True)
    index = _canonical(_index(manifest, payloads)).encode()
    if native_history_path(store / identity).exists():
        existing, _ = read_development_history_snapshot(descriptor, manifest_json, identity)
        if (_payloads(repository_root, files, allow_source_hardlinks=True) != payloads
                or graph_recipe_manifest_v2() != manifest):
            raise ValueError("development_history_snapshot_invalid")
        return existing
    native_history_path(store).mkdir(mode=0o700, parents=True, exist_ok=True)
    _plain(store)
    pending = store / (".pending-" + identity + "-" + uuid4().hex)
    native_history_path(pending).mkdir(mode=0o700)
    for path, raw in payloads.items():
        target = pending / "root" / path
        native_history_path(target.parent).mkdir(parents=True, exist_ok=True)
        _plain(target)
        with native_history_path(target).open("xb") as stream:
            stream.write(raw)
    with native_history_path(pending / "index.json").open("xb") as stream:
        stream.write(index)
    if (_payloads(repository_root, files, allow_source_hardlinks=True) != payloads
            or graph_recipe_manifest_v2() != manifest):
        raise ValueError("development_history_snapshot_invalid")
    if _payloads(pending / "root", files) != payloads:
        raise ValueError("development_history_snapshot_invalid")
    _inventory(pending / "root", set(files))
    try:
        native_history_path(pending).rename(native_history_path(store / identity))
    except FileExistsError:
        # Another bootstrap won. Never repair or overwrite its identity directory.
        pass
    result, _ = read_development_history_snapshot(descriptor, manifest_json, identity)
    return result

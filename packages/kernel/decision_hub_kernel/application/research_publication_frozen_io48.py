"""Explicit server runtime and frozen-consumption IO for the next host only.

No Path substitution, source loading from material, or root-data fallback.
An audit hook is inactive outside its owned consumption scope. Every accepted
file is exact, entry/exit checked, and unknown numeric descriptors are denied.
This uninstalled candidate is not an authority or a publication proof.
"""
from __future__ import annotations

from contextlib import contextmanager
import ast
import asyncio
import sqlite3
import threading
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import sys
from urllib.parse import unquote, urlsplit


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def key(path):
    spelling = os.fspath(path)
    if spelling.startswith("\\\\?\\"):
        spelling = spelling[4:]
    return os.path.normcase(os.path.abspath(spelling))


@dataclass(frozen=True)
class InterpreterAlias:
    logical_root: Path
    resolved_root: Path
    executable_sha256: str
    exact_version: str

    def require_unchanged(self):
        alias, target = self.logical_root.absolute(), self.resolved_root.absolute()
        if (alias.parent != target.parent or alias == target
                or not alias.is_junction() or alias.is_symlink()
                or alias.resolve(strict=True) != target
                or key(sys._base_executable) != key(alias / "python.exe")
                or sys.version != self.exact_version):
            raise ValueError("frozen_runtime_exact_interpreter_alias_changed")
        self.require_real(alias.parent)
        self.require_real(target)
        if digest((target / "python.exe").read_bytes()) != self.executable_sha256:
            raise ValueError("frozen_runtime_exact_interpreter_executable_changed")

    def require_real(self, path, *, allow_absent=False):
        path = Path(path).absolute()
        current = Path(path.anchor)
        for part in path.parts[1:]:
            current /= part
            try:
                status = current.lstat()
            except FileNotFoundError:
                if allow_absent:
                    return
                raise
            if current.is_symlink() or getattr(status, "st_file_attributes", 0) & 0x400:
                raise ValueError("frozen_runtime_unexpected_link")

    def require_path(self, path, *, allow_absent=False):
        path = Path(path).absolute()
        try:
            relative = path.relative_to(self.logical_root.absolute())
        except ValueError:
            self.require_real(path,allow_absent=allow_absent)
            return path
        self.require_unchanged()
        actual = self.resolved_root.joinpath(relative)
        self.require_real(actual,allow_absent=allow_absent)
        return actual


_CURRENT_IO = ContextVar("publication_frozen_consumption_io48", default=None)
_HOOK_INSTALLED = False

def _dispatch_audit(event,args):
    current=_CURRENT_IO.get()
    if current is not None:
        current._audit(event,args)



_NORMAL_IDENTITY_OWNERS = (
    ("packages/kernel/decision_hub_kernel/application/research_context_selection.py",
     "contracts/schemas/research_context_selection_v3.schema.yaml"),
    ("packages/kernel/decision_hub_kernel/application/research_context_selection_v4.py",
     "contracts/schemas/research_context_selection_v4.schema.yaml"),
)


def _normal_identity_references(raw, owner_ref, schema_ref):
    from pathlib import PurePosixPath
    tree = ast.parse(raw, filename=owner_ref)
    definitions = [node for node in tree.body if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "_IDENTITY_PATHS"
                for target in node.targets)]
    if len(definitions) != 1:
        raise ValueError("frozen_io_identity_declaration_unsupported")
    try:
        refs = ast.literal_eval(definitions[0].value)
    except (ValueError, TypeError):
        raise ValueError("frozen_io_identity_declaration_unsupported") from None
    if (type(refs) is not tuple or len(refs) != 14
            or any(type(ref) is not str for ref in refs) or len(set(refs)) != 14
            or schema_ref not in refs):
        raise ValueError("frozen_io_identity_declaration_unsupported")
    for ref in refs:
        path = PurePosixPath(ref)
        if (path.as_posix() != ref or path.is_absolute()
                or any(part in {"", ".", ".."} for part in ref.split("/"))
                or any(char in ref for char in "\\:\x00")
                or not (ref.endswith(".py") or ref == schema_ref)):
            raise ValueError("frozen_io_identity_declaration_unsupported")
    return refs


def _require_normal_identity_scope(authority, closure, lease):
    from .research_publication_resources import OriginalPublicationResourceAuthority
    from ..persistence import research_source_snapshot as normal_factory
    from .research_graph_binding import _ReadSourceGuard
    try:
        try:
            task = asyncio.current_task()
        except RuntimeError:
            task = None
        valid = (type(authority) is OriginalPublicationResourceAuthority
            and type(lease) is normal_factory.PublicationSourceReadScope
            and lease.guard_type is _ReadSourceGuard
            and normal_factory.active_source_snapshot(_ReadSourceGuard) is lease
            and lease.active and lease.root == authority.root
            and lease.run_id == closure.run_id and threading.get_ident() == lease.thread
            and task is lease.task
            and isinstance(lease.native, sqlite3.Connection) and lease.native.text_factory is str
            and lease.session.connection() is lease.connection and not lease.connection.closed
            and lease.connection.connection.driver_connection is lease.native
            and lease.session.get_transaction() is lease.transaction
            and lease.session.get_nested_transaction() is lease.nested
            and lease.native.total_changes == lease.changes and lease.native.in_transaction
            and not (lease.session.new or lease.session.dirty or lease.session.deleted)
            and lease.collector.session is lease.session
            and lease.collector.connection is lease.connection
            and lease.collector.native is lease.native and lease.collector.owner_thread == lease.thread
            and lease.collector._entered and not lease.collector._finished
            and lease.native.execute("PRAGMA query_only").fetchone() == (1,))
        if valid:
            lease.collector._require_boundary()  # Original pure native boundary; no Source read.
    except (sqlite3.Error, AttributeError, RuntimeError):
        valid = False
    if not valid:
        raise ValueError("frozen_io_normal_identity_scope_changed")



def _normal_identity_values(authority, closure, lease):
    _require_normal_identity_scope(authority, closure, lease)
    files = {file.relative_ref: file for file in lease.snapshot.files}
    declarations, schemas = [], []
    for owner_ref, schema_ref in _NORMAL_IDENTITY_OWNERS:
        raw = authority.read_normal_source(closure, "current-root", owner_ref)
        if authority.source_path(closure, "current-root", owner_ref) != authority.root / owner_ref:
            raise ValueError("frozen_io_normal_identity_source_mapping_changed")
        refs = _normal_identity_references(raw, owner_ref, schema_ref)
        owner_rows = []
        for ref in refs:
            entries = [item for item in closure.entries
                if item.namespace == "current-root" and item.logical_ref == ref]
            expected_purpose = "data_resource" if ref == schema_ref else "source_attestation"
            if (len(entries) != 1 or entries[0].physical_ref != ref
                    or entries[0].purpose != expected_purpose or ref not in files
                    or entries[0].sha256 != files[ref].sha256
                    or entries[0].bytes != files[ref].bytes):
                raise ValueError("frozen_io_normal_identity_description_changed")
            entry = entries[0]
            owner_rows.append((ref, entry.sha256, entry.bytes, entry.purpose))
            if ref == schema_ref:
                schemas.append((ref, entry.sha256, entry.bytes))
        declarations.append((owner_ref, digest(raw), len(raw), tuple(owner_rows)))
    return tuple(declarations), tuple(sorted(schemas))


@dataclass(frozen=True)
class NormalIdentityAttestations:
    """Declared normal implementation inputs; no caller path list or permission."""
    authority: object
    closure: object
    lease: object
    declarations: tuple
    schemas: tuple

    def require_complete(self):
        # Full original require_read is outside strict IO. Its guard itself
        # hashes selector schemas; calling it from an open hook would recurse.
        _require_normal_identity_scope(self.authority, self.closure, self.lease)
        self.lease.require_read(self.lease.session, self.closure.run_id)
        actual = _normal_identity_values(self.authority, self.closure, self.lease)
        if actual != (self.declarations, self.schemas):
            raise ValueError("frozen_io_normal_identity_declaration_changed")

    def require_active(self):
        # Original public factory validates its own issued active object.
        # Full byte/implementation entry and fresh exit stay in that factory.
        _require_normal_identity_scope(self.authority, self.closure, self.lease)
        for owner_ref, _schema_ref in _NORMAL_IDENTITY_OWNERS:
            raw = self.authority.read_normal_source(self.closure, "current-root", owner_ref)
            expected = next(row for row in self.declarations if row[0] == owner_ref)
            if digest(raw) != expected[1] or len(raw) != expected[2]:
                raise ValueError("frozen_io_normal_identity_source_changed")


def derive_normal_identity_attestations(authority, closure, normal_source_lease):
    """Public derivation from a genuine Root lease and issued normal closure."""
    _require_normal_identity_scope(authority, closure, normal_source_lease)
    normal_source_lease.require_read(normal_source_lease.session, closure.run_id)
    declarations, schemas = _normal_identity_values(authority, closure, normal_source_lease)
    result = NormalIdentityAttestations(authority, closure, normal_source_lease, declarations, schemas)
    result.require_complete()
    return result


class FrozenConsumptionIO:
    def __init__(self, *, root, sources, authorization, staged, runtime,
                 native_database, interpreter, normal_identity=None, native_connection=None):
        self.root = Path(root).absolute()
        self.native_database = key(native_database)
        self.interpreter = interpreter
        self.sources = self._exact(sources)
        self.authorization_files = self._exact(authorization)
        self.staged = self._exact(staged)
        self.runtime = self._exact(runtime)
        self.normal_identity = normal_identity
        self.identity_schemas = {}
        if normal_identity is not None:
            if type(normal_identity) is not NormalIdentityAttestations:
                raise ValueError("frozen_io_normal_identity_declaration_required")
            normal_identity.require_complete()
            if (normal_identity.authority.root != self.root
                    or native_connection is not normal_identity.lease.native):
                raise ValueError("frozen_io_normal_identity_root_changed")
            self.identity_schemas = self._exact({str(self.root / ref):
                {"sha256": sha, "bytes": size, "present": True}
                for ref, sha, size in normal_identity.schemas})
        if any(p == key(self.root) or p.startswith(key(self.root) + os.sep) for p in self.staged):
            raise ValueError("frozen_io_stage_must_be_owned_outside_root")
        self.active = False
        self.used = False
        self._authorization = ContextVar("frozen_publication_authorization_io47", default=frozenset())
        self.opens = []
        self.denied = []
        for path in self.sources:
            if not path.endswith(".py"):
                raise ValueError("frozen_io_normal_sources_must_be_python")
        if set(self.sources) & set(self.authorization_files):
            raise ValueError("frozen_io_source_authorization_roles_overlap")
        self._all = {**self.sources, **self.authorization_files, **self.staged, **self.runtime, **self.identity_schemas}
        if len(self._all) != sum(map(len, (self.sources, self.authorization_files, self.staged, self.runtime, self.identity_schemas))):
            raise ValueError("frozen_io_resource_roles_overlap")

    @staticmethod
    def _exact(values):
        result = {}
        for path, expected in values.items():
            normalized = key(path)
            if normalized in result or set(expected) != {"sha256", "bytes", "present"}:
                raise ValueError("frozen_io_exact_resource_descriptor_required")
            result[normalized] = dict(expected)
        return result

    def require_unchanged(self):
        if self.normal_identity is not None:
            self.normal_identity.require_active()
        self.interpreter.require_unchanged()
        with self.authorization(self.authorization_files):
            for path, expected in self._all.items():
                actual = self.interpreter.require_path(Path(path),allow_absent=(
                    path in self.runtime and expected["present"] is False))
                if actual.exists() is not expected["present"]:
                    raise ValueError("frozen_io_declared_presence_changed")
                if expected["present"]:
                    status = actual.lstat()
                    if actual.is_symlink() or getattr(status, "st_file_attributes", 0) & 0x400:
                        raise ValueError("frozen_io_final_file_link_forbidden")
                    if status.st_size != expected["bytes"]:
                        raise ValueError("frozen_io_declared_size_changed")
                    raw = actual.read_bytes()
                    if digest(raw) != expected["sha256"]:
                        raise ValueError("frozen_io_declared_whole_hash_changed")

    @contextmanager
    def authorization(self, exact_paths):
        allowed = frozenset(key(p) for p in exact_paths)
        if not allowed <= self.authorization_files.keys():
            raise ValueError("frozen_io_undeclared_authorization_resource")
        token = self._authorization.set(allowed)
        try:
            yield
        finally:
            self._authorization.reset(token)

    def read_authorization(self, path, original_reader):
        normalized = key(path)
        if normalized in self.sources:
            return original_reader()
        with self.authorization((normalized,)):
            raw = original_reader()
        expected = self.authorization_files[normalized]
        if len(raw) != expected["bytes"] or digest(raw) != expected["sha256"]:
            raise ValueError("frozen_io_authorization_whole_bytes_changed")
        return raw

    def _audit(self, event, args):
        if not self.active:
            return
        if event == "sqlite3.connect":
            raw = args[0]
            if type(raw) is not str:
                self._reject("unknown_sqlite_descriptor")
            if raw.startswith("file:"):
                parsed = urlsplit(raw)
                path = unquote(parsed.path)
                if os.name == "nt" and path.startswith("/"):
                    path = path[1:]
                if parsed.query != "mode=ro":
                    self._reject("native_database_must_be_readonly")
            else:
                self._reject("native_database_uri_required")
            if key(path) != self.native_database:
                self._reject("unknown_native_database")
        if event != "open":
            return
        path, mode, flags = args
        if isinstance(path, int):
            self._reject("unknown_numeric_descriptor")
        if not isinstance(path, (str, bytes, os.PathLike)):
            self._reject("unknown_file_descriptor")
        if (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
                isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)):
            self._reject("consumption_write_forbidden")
        normalized = key(os.fsdecode(path))
        if normalized in self.identity_schemas:
            self.normal_identity.require_active()
        allowed = (normalized in self.sources or normalized in self.staged
                   or normalized in self.runtime or normalized in self._authorization.get()
                   or normalized in self.identity_schemas)
        if not allowed:
            category = "root_data_fallback" if normalized.startswith(key(self.root) + os.sep) else "undeclared_file"
            self._reject(category, normalized)
        self.opens.append({"path": normalized, "role": "authorization" if normalized in self._authorization.get()
            else "normal_identity_schema" if normalized in self.identity_schemas
            else "normal_source" if normalized in self.sources else "frozen_stage" if normalized in self.staged
            else "runtime"})

    def _reject(self, category, path=None):
        self.denied.append({"category": category, "path": path})
        raise ValueError("frozen_io_" + category)

    def __enter__(self):
        if self.active or self.used:
            raise ValueError("frozen_io_single_consumption_scope_required")
        self.require_unchanged()
        global _HOOK_INSTALLED
        if _CURRENT_IO.get() is not None:
            raise ValueError("frozen_io_nested_consumption_scope_forbidden")
        if not _HOOK_INSTALLED:
            sys.addaudithook(_dispatch_audit)
            _HOOK_INSTALLED = True
        self._scope_token = _CURRENT_IO.set(self)
        self.used = True
        self.active = True
        return self

    def __exit__(self, kind, value, trace):
        self.active = False
        _CURRENT_IO.reset(self._scope_token)
        if kind is None:
            self.require_unchanged()
        return False

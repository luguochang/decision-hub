"""V2 publication-only byte lease. Old readers without a scope remain fresh.

Windows deny-write/delete handles hold the complete authenticated byte set.
Every reuse checks native read and source identity; uncached full-byte exit
precedes return. No permission, row result or source verdict crosses a read.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from ..ports.research_source_snapshot import (
    SOURCE_SNAPSHOT_READ_ABI as SOURCE_SNAPSHOT_READ_ABI,
)
from ..ports.research_source_snapshot import (
    PublicationSourceSnapshotV1,
)
from .callback_source_lease import CallbackSourceLease, _WindowsFiles

_ACTIVE: ContextVar[PublicationSourceReadScope | None] = ContextVar(
    "publication_source_snapshot_v2",
    default=None,
)
_ISSUED: set[PublicationSourceReadScope] = set()
_GUARD_METHODS = ("capture", "matches", "matches_identity", "matches_scope_identity")


def _changed():
    return ValueError("publication_source_snapshot_boundary_changed")


def _task():
    try:
        return asyncio.current_task()
    except RuntimeError:
        return None


class PublicationSourceReadScope:
    """Issued only by the installed factory's explicit current V2 branch."""

    def __init__(self, session, run_id, guard_type, collector, declared, checkpoint):
        if os.name != "nt":
            raise ValueError("publication_source_snapshot_native_platform_unsupported")
        self.session, self.run_id, self.collector = session, run_id, collector
        self.guard_type, self.guard = guard_type, guard_type.capture()
        if self.guard is None or not self.guard.matches():
            raise _changed()
        self.root = self.guard.root
        self.connection = session.connection()
        self.native = self.connection.connection.driver_connection
        self.transaction, self.nested = session.get_transaction(), session.get_nested_transaction()
        self.thread, self.task = threading.get_ident(), _task()
        self.changes = self.native.total_changes
        self.active = False
        self.native_files = _WindowsFiles()
        self.files, self.directories = [], []
        self.checkpoint = checkpoint
        self.reuse_count = 0
        self.guard_implementations = tuple(
            (
                getattr(guard_type, name),
                getattr(getattr(guard_type, name), "__func__", getattr(guard_type, name)).__code__,
            )
            for name in _GUARD_METHODS
        )
        self._require_boundary()
        values = dict(self.guard.files)
        for relative, digest in declared:
            if relative in values and values[relative] != digest:
                raise _changed()
            values[relative] = digest
        try:
            self._pin(values)
        except BaseException:
            self.close()
            raise

    def _require_boundary(self):
        try:
            valid = (
                isinstance(self.native, sqlite3.Connection)
                and self.native.in_transaction
                and self.native.total_changes == self.changes
                and self.native.text_factory is str
                and not self.connection.closed
                and self.session.connection() is self.connection
                and self.connection.connection.driver_connection is self.native
                and self.session.get_transaction() is self.transaction
                and self.session.get_nested_transaction() is self.nested
                and not (self.session.new or self.session.dirty or self.session.deleted)
                and threading.get_ident() == self.thread
                and _task() is self.task
                and self.collector.session is self.session
                and self.collector.connection is self.connection
                and self.collector.native is self.native
                and self.collector.owner_thread == self.thread
                and self.collector._entered
                and not self.collector._finished
                and self.native.execute("PRAGMA query_only").fetchone() == (1,)
            )
            self.collector._require_boundary()
        except (sqlite3.Error, AttributeError, RuntimeError):
            valid = False
        if not valid:
            raise _changed()

    def _pin(self, values):
        # Validate references/count before acquiring an OS handle.
        shape = [[relative, digest, 0] for relative, digest in sorted(values.items())]
        PublicationSourceSnapshotV1(
            files=[dict(relative_ref=r, sha256=d, bytes=n) for r, d, n in shape],
            source_set_sha256=hashlib.sha256(
                json.dumps(shape, ensure_ascii=False, separators=(",", ":")).encode()
            ).hexdigest(),
        )
        if self.root != self.root.resolve(strict=True):
            raise _changed()
        ancestors = {self.root, *self.root.parents}
        for relative in values:
            parent = (self.root / relative).parent
            while parent != self.root:
                parent.relative_to(self.root)
                ancestors.add(parent)
                parent = parent.parent
        for path in sorted(ancestors, key=lambda p: (len(p.parts), str(p))):
            self.checkpoint()
            CallbackSourceLease._reject_reparse(path)
            handle = self.native_files.open(path, directory=True)
            try:
                identity = self.native_files.identity(handle)
            except BaseException:
                self.native_files.close(handle)
                raise
            self.directories.append((path, handle, identity))
        shape = []
        total = 0
        for relative, expected in sorted(values.items()):
            self.checkpoint()
            path = self.root / relative
            CallbackSourceLease._reject_reparse(path)
            if path.stat().st_size > 2 * 1024 * 1024:
                raise ValueError("publication_source_snapshot_file_bound")
            handle = self.native_files.open(path)
            try:
                identity = self.native_files.identity(handle)
            except BaseException:
                self.native_files.close(handle)
                raise
            self.files.append((path, handle, identity))
            size = path.stat().st_size
            if size > 2 * 1024 * 1024 or total + size > 32 * 1024 * 1024:
                raise ValueError("publication_source_snapshot_byte_bound")
            digest, size = self.native_files.digest(handle, self.checkpoint)
            total += size
            if digest != expected or size > 2 * 1024 * 1024 or total > 32 * 1024 * 1024:
                raise _changed()
            shape.append([relative, digest, size])
        self.snapshot = PublicationSourceSnapshotV1(
            files=[dict(relative_ref=r, sha256=d, bytes=n) for r, d, n in shape],
            source_set_sha256=hashlib.sha256(
                json.dumps(shape, ensure_ascii=False, separators=(",", ":")).encode()
            ).hexdigest(),
        )
        self._require_identity()

    def _require_identity(self):
        self._require_boundary()
        if not self.guard.matches_identity():
            raise _changed()
        self._require_guard_implementation()
        for directory, entries in ((True, self.directories), (False, self.files)):
            for path, handle, expected in entries:
                CallbackSourceLease._reject_reparse(path)
                if self.native_files.identity(
                    handle
                ) != expected or expected.final_path != self.native_files.canonical(str(path)):
                    raise _changed()
                probe = self.native_files.open(path, directory=directory)
                try:
                    if self.native_files.identity(probe) != expected:
                        raise _changed()
                finally:
                    self.native_files.close(probe)

    def _require_guard_implementation(self):
        for name, (saved, code) in zip(_GUARD_METHODS, self.guard_implementations, strict=True):
            actual = getattr(self.guard_type, name)
            original = getattr(actual, "__func__", actual)
            if actual != saved or original.__code__ is not code:
                raise _changed()

    def _require_live_identity(self):
        self._require_boundary()
        self._require_guard_implementation()
        if (
            not self.guard.matches_scope_identity()
            or len(self.files) != len(self.snapshot.files)
            or not self.directories
        ):
            raise _changed()
        # Every held native identity stays live; immutable HANDLEs deny byte
        # writes/delete and ancestor retarget. Full fresh-path/inventory proof
        # runs at entry and uncached exit before any result can leave this read.
        for _path, handle, expected in (*self.directories, *self.files):
            if self.native_files.identity(handle) != expected:
                raise _changed()

    def require_read(self, session, run_id):
        if (
            not self.active
            or _ACTIVE.get() is not self
            or self not in _ISSUED
            or session is not self.session
            or type(run_id) is not str
            or run_id != self.run_id
        ):
            raise _changed()
        self._require_live_identity()

    def require_reader_source(self, path):
        self.require_read(self.session, self.run_id)
        relative = Path(path).resolve(strict=True).relative_to(self.root).as_posix()
        if not any(file.relative_ref == relative for file in self.snapshot.files):
            raise ValueError("publication_source_snapshot_reader_not_declared")

    def matches_guard(self, guard):
        if guard is not self.guard:
            raise _changed()
        self.require_read(self.session, self.run_id)
        self.reuse_count += 1
        return True

    def verify_full_exit(self):
        self._require_identity()
        # Context has already been reset: these are always the old fresh paths.
        fresh = self.guard_type.capture()
        if fresh is None or fresh != self.guard or not fresh.matches():
            raise _changed()
        for file in self.snapshot.files:
            raw = (self.root / file.relative_ref).read_bytes()
            if len(raw) != file.bytes or hashlib.sha256(raw).hexdigest() != file.sha256:
                raise _changed()
        self._require_identity()

    def close(self):
        if threading.get_ident() != self.thread:
            raise _changed()
        errors = []
        for _path, handle, _identity in reversed((*self.directories, *self.files)):
            try:
                self.native_files.close(handle)
            except Exception as error:
                errors.append(error)
        self.files.clear()
        self.directories.clear()
        self.active = False
        if errors:
            raise _changed() from errors[0]


def active_source_snapshot(guard_type):
    scope = _ACTIVE.get()
    if scope is None:
        return None
    if (
        type(scope) is not PublicationSourceReadScope
        or scope not in _ISSUED
        or scope.guard_type is not guard_type
    ):
        raise _changed()
    return scope


@contextmanager
def publication_source_snapshot_read_scope(
    session,
    *,
    run_id,
    guard_type,
    collector,
    declared_sources,
    expected_snapshot=None,
    checkpoint=lambda: None,
):
    if _ACTIVE.get() is not None:
        raise ValueError("publication_source_snapshot_nested_scope_forbidden")
    _require_scope_implementation()
    scope = PublicationSourceReadScope(
        session, run_id, guard_type, collector, declared_sources, checkpoint
    )
    try:
        if expected_snapshot is not None:
            expected = PublicationSourceSnapshotV1.model_validate(expected_snapshot)
            if scope.snapshot != expected:
                raise ValueError("publication_source_snapshot_frozen_set_changed")
        scope.active = True
        _ISSUED.add(scope)
        token = _ACTIVE.set(scope)
        try:
            yield scope
        finally:
            _ACTIVE.reset(token)
            _ISSUED.discard(scope)
            scope.active = False
            _require_scope_implementation()
            scope.verify_full_exit()
    finally:
        scope.close()


_SCOPE_FUNCTIONS = (
    PublicationSourceReadScope.__init__,
    PublicationSourceReadScope._require_boundary,
    PublicationSourceReadScope._pin,
    PublicationSourceReadScope._require_identity,
    PublicationSourceReadScope._require_guard_implementation,
    PublicationSourceReadScope._require_live_identity,
    PublicationSourceReadScope.require_read,
    PublicationSourceReadScope.require_reader_source,
    PublicationSourceReadScope.matches_guard,
    PublicationSourceReadScope.verify_full_exit,
    PublicationSourceReadScope.close,
    active_source_snapshot,
    publication_source_snapshot_read_scope.__wrapped__,
)
_SCOPE_IMPLEMENTATION = (
    PublicationSourceReadScope,
    _ACTIVE,
    _ISSUED,
    tuple((function, function.__code__) for function in _SCOPE_FUNCTIONS),
)


def _require_scope_implementation():
    current = (
        PublicationSourceReadScope.__init__,
        PublicationSourceReadScope._require_boundary,
        PublicationSourceReadScope._pin,
        PublicationSourceReadScope._require_identity,
        PublicationSourceReadScope._require_guard_implementation,
        PublicationSourceReadScope._require_live_identity,
        PublicationSourceReadScope.require_read,
        PublicationSourceReadScope.require_reader_source,
        PublicationSourceReadScope.matches_guard,
        PublicationSourceReadScope.verify_full_exit,
        PublicationSourceReadScope.close,
        active_source_snapshot,
        publication_source_snapshot_read_scope.__wrapped__,
    )
    cls, active, issued, functions = _SCOPE_IMPLEMENTATION
    if (
        PublicationSourceReadScope is not cls
        or _ACTIVE is not active
        or _ISSUED is not issued
        or any(
            actual is not saved or actual.__code__ is not code
            for actual, (saved, code) in zip(current, functions, strict=True)
        )
    ):
        raise ValueError("publication_source_snapshot_implementation_changed")

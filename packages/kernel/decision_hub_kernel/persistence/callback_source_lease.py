"""Per-callback immutable Windows source handles. Never an execution grant.

Complete source bytes are hashed outside SQLite's writer. Windows sharing rules
then refuse write/delete while this one worker owns the handles. Fresh path to
native file identity and the original source metadata are checked at every cut.
Other platforms keep the original full-hash path; there is no stat-only cache.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable


def _changed() -> ValueError:
    return ValueError("original_preparation_source_changed")


@dataclass(frozen=True)
class _Identity:
    volume: int
    file_id: bytes
    final_path: str


class _WindowsFiles:
    """Actual HANDLE identity, not timestamp/size as a content proof."""

    def __init__(self) -> None:
        from ctypes import wintypes

        class FileIdInfo(ctypes.Structure):
            _fields_ = [("volume", ctypes.c_ulonglong), ("file_id", ctypes.c_ubyte * 16)]

        self.Info = FileIdInfo
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.api.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                        wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        self.api.CreateFileW.restype = wintypes.HANDLE
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.api.CloseHandle.restype = wintypes.BOOL
        self.api.GetFileInformationByHandleEx.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
        self.api.GetFileInformationByHandleEx.restype = wintypes.BOOL
        self.api.GetFinalPathNameByHandleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
        self.api.GetFinalPathNameByHandleW.restype = wintypes.DWORD
        self.api.ReadFile.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
                                     ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
        self.api.ReadFile.restype = wintypes.BOOL
        self.Count = wintypes.DWORD

    @staticmethod
    def canonical(path: str) -> str:
        if path.startswith("\\\\?\\UNC\\"):
            path = "\\\\" + path[8:]
        elif path.startswith("\\\\?\\"):
            path = path[4:]
        return os.path.normcase(os.path.normpath(path))

    def open(self, path: Path, *, directory: bool = False) -> Any:
        # Directory handles deny rename/delete of ancestors, while allowing
        # ordinary child creation; lexical inventory checks detect additions.
        access = 0x80 if directory else 0x80000000  # FILE_READ_ATTRIBUTES / GENERIC_READ
        share = 0x3 if directory else 0x1  # directories R|W; source bytes R only
        flags = 0x02000000 if directory else 0x08000000  # BACKUP_SEMANTICS / SEQUENTIAL_SCAN
        spelling = str(path.absolute())
        if not spelling.startswith('\\\\?\\'):
            spelling = '\\\\?\\UNC\\' + spelling[2:] if spelling.startswith('\\\\') else '\\\\?\\' + spelling
        handle = self.api.CreateFileW(spelling, access, share, None, 3, flags, None)
        if handle in (None, ctypes.c_void_p(-1).value):
            raise _changed()
        return handle

    def close(self, handle: Any) -> None:
        if not self.api.CloseHandle(handle):
            raise _changed()

    def identity(self, handle: Any) -> _Identity:
        info = self.Info()
        if not self.api.GetFileInformationByHandleEx(handle, 18, ctypes.byref(info), ctypes.sizeof(info)):
            raise _changed()
        buffer = ctypes.create_unicode_buffer(32768)
        count = self.api.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
        if count == 0 or count >= len(buffer):
            raise _changed()
        return _Identity(info.volume, bytes(info.file_id), self.canonical(buffer.value))

    def digest(self, handle: Any, check: Callable[[], None]) -> tuple[str, int]:
        digest = hashlib.sha256()
        buffer = ctypes.create_string_buffer(1024 * 1024)
        total = 0
        while True:
            check()
            count = self.Count()
            if not self.api.ReadFile(handle, buffer, len(buffer), ctypes.byref(count), None):
                raise _changed()
            if not count.value:
                break
            digest.update(buffer.raw[:count.value])
            total += count.value
        check()
        return digest.hexdigest(), total


_GRAPH_ARCHIVE_PREFIXES = (
    "packages/kernel/research_recipe_versions/",
    "packages/kernel/market_component_versions/",
)


class CallbackSourceLease:
    def __init__(
        self,
        checkpoint: Callable[[], None] | None = None,
        *,
        defer_unselected_recipe_archives: bool = False,
    ) -> None:
        if type(defer_unselected_recipe_archives) is not bool:
            raise _changed()
        self.owner = threading.get_ident()
        self.checkpoint = checkpoint or (lambda: None)
        self.defer_unselected_recipe_archives = defer_unselected_recipe_archives
        self.native = _WindowsFiles()
        self.active = True
        self._source: Any = None
        self._files: list[tuple[Path, Any, _Identity]] = []
        self._directories: list[tuple[Path, Any, _Identity]] = []
        self._expected: dict[str, str] = {}
        self._deferred_recipe_archives: dict[str, str] = {}
        self._bindings: list[tuple[Any, Any, Path, Path]] = []
        self._extended_run_id: str | None = None
        self._sealed = False
        self.hashed_bytes = 0
        self.hash_observations: list[tuple[str, int, float]] = []
        self.identity_checks = 0

    def _check(self) -> None:
        if not self.active or threading.get_ident() != self.owner:
            raise _changed()
        self.checkpoint()

    def pin(self, source: Any) -> _LeasedSourceGuard:
        from .original_preparation import _ConsumerSourceGuard

        self._check()
        if self._source is not None or type(source) is not _ConsumerSourceGuard:
            raise _changed()
        root = source.root
        if root != root.resolve(strict=True):
            raise _changed()
        self._source = source
        values: dict[str, str] = {}
        for relative, digest in (*source.graph.files, *source.files):
            self._validate_declaration(relative, digest)
            if relative in values and values[relative] != digest:
                raise _changed()
            if (self.defer_unselected_recipe_archives
                    and relative.startswith(_GRAPH_ARCHIVE_PREFIXES)):
                previous = self._deferred_recipe_archives.get(relative)
                if previous is not None and previous != digest:
                    raise _changed()
                self._deferred_recipe_archives[relative] = digest
                continue
            values[relative] = digest
        self._add_files(values)
        if not self.matches():
            raise _changed()
        return _LeasedSourceGuard(self, source)

    def _add_files(self, values: dict[str, str]) -> None:
        """Add authenticated full declarations while the RO owner is outside writer."""
        self._check()
        if self._source is None or self._sealed:
            raise _changed()
        root = self._source.root
        additions = {}
        for relative, digest in values.items():
            self._validate_declaration(relative, digest)
            deferred = self._deferred_recipe_archives.get(relative)
            if deferred is not None and deferred != digest:
                raise _changed()
            if relative in self._expected:
                if self._expected[relative] != digest:
                    raise _changed()
            else:
                additions[relative] = digest
        # Include lexical ancestors above the repository as well: a moved
        # parent must not retarget the still-live source spelling mid-call.
        directories = {root, *root.parents}
        for relative in additions:
            parent = (root / relative).parent
            while parent != root:
                parent.relative_to(root)
                directories.add(parent)
                parent = parent.parent
        held_directories = {item[0] for item in self._directories}
        for path in sorted(directories - held_directories, key=lambda p: (len(p.parts), str(p))):
            self._check()
            self._reject_reparse(path)
            handle = self.native.open(path, directory=True)
            try:
                identity = self.native.identity(handle)
            except Exception:
                self.native.close(handle)
                raise
            self._directories.append((path, handle, identity))
            if identity.final_path != self.native.canonical(str(path)):
                raise _changed()
        for relative, expected in sorted(additions.items()):
            self._check()
            path = root / relative
            self._reject_reparse(path)
            handle = self.native.open(path)
            try:
                identity = self.native.identity(handle)
            except Exception:
                self.native.close(handle)
                raise
            self._files.append((path, handle, identity))
            if identity.final_path != self.native.canonical(str(path)):
                raise _changed()
            started = time.monotonic()
            actual, size = self.native.digest(handle, self._check)
            self.hash_observations.append((relative, size, time.monotonic() - started))
            self.hashed_bytes += size
            if actual != expected:
                raise _changed()
            self._expected[relative] = expected
            self._deferred_recipe_archives.pop(relative, None)

    @staticmethod
    def _validate_declaration(relative: Any, digest: Any) -> None:
        if type(relative) is not str:
            raise _changed()
        parts = PurePosixPath(relative).parts
        if (not parts or PurePosixPath(relative).as_posix() != relative
                or relative.startswith("/") or re.search(r'[\\\x00-\x1f<>:"|?*]', relative)
                or any(part in {".", ".."} for part in parts)):
            raise _changed()
        if type(digest) is not str or re.fullmatch('[a-f0-9]{64}', digest) is None:
            raise _changed()

    def extend_authenticated_selection(self, session: Any, run_id: str) -> None:
        """Called only after the unchanged full callback authentication in same RO.

        The original strict reader selects the archive from native seven-field
        identity. Its uncached validator supplies the complete declared closure;
        no saved type, permission or cross-request authority is reused here.
        """
        from ..application import research_graph_history_versions as history

        self._check()
        if (self._source is None or self._sealed or self._extended_run_id is not None
                or type(run_id) is not str or not run_id
                or history._CURRENT_ROOT != self._source.root):
            raise _changed()
        registration = history._original_recipe_registration(session, run_id)
        binding = history._REGISTRY_BINDING
        values: dict[str, str] = {}
        if binding is not None:
            values.update(binding.transport_hashes)
            values[binding.receipt_path.relative_to(self._source.root).as_posix()] = binding.receipt_hash
            values[binding.registry_path] = binding.registry_hash
        # Preserve None as an authenticated registry state too. A registry
        # installed after this cut cannot silently change the selected loader.
        self._bindings.append((history, binding, history._CURRENT_ROOT, history._RESOURCE_ROOT))
        if registration is not None:
            index, _payloads, _root = history._verified_release(registration)
            prefix = 'packages/kernel/research_recipe_versions/' + registration.directory + '/'
            values[prefix + 'release.json'] = registration.index_hash
            stored = set(index['stored_files'])
            values.update({prefix + 'root/' + path if path in stored else path: digest
                           for path, digest in index['all_files'].items()})
            # The strict V3 selector reads every same-graph candidate index
            # before exact installation routing. Keep those original identities
            # immutable too; unrelated capsule payloads are not consumed.
            for candidate in history._active_registrations():
                if candidate.graph_hash == registration.graph_hash:
                    values['packages/kernel/research_recipe_versions/' + candidate.directory + '/release.json'] = candidate.index_hash
        else:
            # A current unarchived exact installation retains its strict reader.
            # Its native saved manifest and original receipt/pointer are locked
            # as bytes only after that reader has authenticated all of them.
            rows = session.connection().exec_driver_sql(
                "SELECT payload_json FROM run_role_outputs WHERE run_id=? "
                "AND role='graph_recipe_selection' LIMIT 2", (run_id,),
            ).all()
            if len(rows) > 1:
                raise _changed()
            if rows:
                selected = json.loads(rows[0][0])
                for item in selected['manifest']['source_files'] + selected['manifest']['schema_files']:
                    values[item['path']] = item['payload_hash']
                if selected['schema_version'] == 'research-graph-recipe-selection.v3':
                    for key in ('installation_receipt', 'installed_pointer'):
                        ref = selected[key]
                        values[ref['path']] = ref['sha256']
        self._check()
        self._add_files(values)
        if not self.matches():
            raise _changed()
        self._extended_run_id = run_id

    def seal_authenticated_selection(self, run_id: str) -> None:
        """Freeze one fully authenticated callback before its short writer.

        Entry and selection extension already performed the complete recursive
        inventory and byte checks. Every consumed byte is now held by a native
        HANDLE that refuses write/delete/rename. The sealed writer therefore
        rechecks the live implementation identity, the exact selected registry
        binding and every held path identity without walking unrelated retained
        version payloads again. This state is local to this callback and is
        discarded with its handles; it is never an authorization cache.
        """
        self._check()
        if (self._source is None or self._sealed
                or self._extended_run_id != run_id):
            raise _changed()
        # One last complete check closes the gap before the bounded identity
        # fence becomes active.
        if not self.matches():
            raise _changed()
        # Only research-recipe selection is promoted by the authenticated
        # extension above. An unresolved component cannot enter the bounded
        # sealed fence, which excludes retained version payload inventories.
        if any(path.startswith("packages/kernel/market_component_versions/")
               for path in self._deferred_recipe_archives):
            raise _changed()
        self._sealed = True

    @staticmethod
    def _reject_reparse(path: Path) -> None:
        try:
            spelling = str(path.absolute())
            if not spelling.startswith('\\\\?\\'):
                spelling = '\\\\?\\UNC\\' + spelling[2:] if spelling.startswith('\\\\') else '\\\\?\\' + spelling
            native_path = Path(spelling)
            stat = native_path.lstat()
            if native_path.is_symlink() or getattr(stat, "st_file_attributes", 0) & 0x400:
                raise _changed()
        except OSError as error:
            raise _changed() from error

    def _matches_bindings_and_handles(self) -> bool:
        if any(module._REGISTRY_BINDING is not binding
               or module._CURRENT_ROOT != root or module._RESOURCE_ROOT != resource
               for module, binding, root, resource in self._bindings):
            return False
        directories = {d[0] for d in self._directories}
        for path, handle, expected in (*self._directories, *self._files):
            self._check()
            self._reject_reparse(path)
            # Check both still-live held HANDLE and a fresh lexical-path open.
            if self.native.identity(handle) != expected:
                return False
            probe = self.native.open(path, directory=path in directories)
            try:
                if self.native.identity(probe) != expected:
                    return False
            finally:
                self.native.close(probe)
            self.identity_checks += 1
        self._check()
        return True

    def _matches_sealed_source_identity(self) -> bool:
        source = self._source
        if source is None or self._extended_run_id is None:
            return False
        graph_scope = getattr(source.graph, "matches_scope_identity", None)
        if not callable(graph_scope) or not graph_scope():
            return False
        # Current consumer modules remain a small, fresh lexical inventory.
        # Retained version payloads are excluded here because the exact selected
        # capsule and registry resources were added to the held HANDLE set by
        # extend_authenticated_selection before sealing.
        kernel = source.root / "packages/kernel/decision_hub_kernel"
        inventory = tuple(sorted(
            path.relative_to(source.root).as_posix()
            for folder in ("application", "decision", "persistence")
            for path in (kernel / folder).glob("*.py")
        ))
        return inventory == tuple(path for path, _digest in source.files)

    def matches_sealed(self) -> bool:
        """Bounded post-authentication fence for the callback's short writer."""
        self._check()
        if not self._sealed or not self._matches_sealed_source_identity():
            return False
        return self._matches_bindings_and_handles()

    def matches(self) -> bool:
        self._check()
        if self._source is None:
            return False
        if self._sealed:
            return self.matches_sealed()
        if not self._source.matches_identity():
            return False
        return self._matches_bindings_and_handles()

    def close(self) -> None:
        # Only the validating worker may close; event-loop cancellation merely
        # marks the checkpoint. Handles remain held until that worker exits.
        if threading.get_ident() != self.owner:
            raise _changed()
        errors = []
        for _path, handle, _identity in reversed((*self._directories, *self._files)):
            try:
                self.native.close(handle)
            except Exception as error:
                errors.append(error)
        self._files.clear()
        self._directories.clear()
        self._expected.clear()
        self._deferred_recipe_archives.clear()
        self._bindings.clear()
        self._extended_run_id = None
        self._sealed = False
        self.active = False
        if errors:
            raise _changed() from errors[0]


@dataclass(frozen=True)
class _LeasedSourceGuard:
    lease: CallbackSourceLease
    original: Any

    def matches(self) -> bool:
        return self.lease.matches()


@contextmanager
def callback_source_lease_scope(
    checkpoint: Callable[[], None] | None = None,
    *,
    defer_unselected_recipe_archives: bool = False,
):
    if os.name != "nt":
        yield None
        return
    lease = CallbackSourceLease(
        checkpoint,
        defer_unselected_recipe_archives=defer_unselected_recipe_archives,
    )
    try:
        yield lease
    finally:
        lease.close()

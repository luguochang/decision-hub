"""Owned frozen-data roots for an explicit future retained-reader descriptor.

No historical code execution, global FS patch, path rewriting or Root data
fallback. Stage .py files are fresh normal registered source attestations; all
other files come only from authenticated material. The current runtime has no
descriptor admission and remains unsupported. This is a business integration
primitive, not complete actual old39 consumption proof.
"""
from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
import os
from pathlib import Path, PurePosixPath
import shutil
import tempfile
from types import MappingProxyType

from .research_publication_resources import (
    OriginalPublicationResourceAuthority, FrozenPublicationResourceReader,
    ResourceBounds, sha,
)


def _relative(name):
    if (type(name) is not str or "\\" in name or ":" in name
            or not name or PurePosixPath(name).is_absolute()
            or any(part in {"", ".", ".."} for part in name.split("/"))):
        raise ValueError("publication_stage_resource_reference_invalid")
    return name.split("/")


def _owned_parent(path):
    path = path.absolute()
    current = Path(path.anchor)
    for part in path.parts[1:]:
        if part in {".", ".."}:
            raise ValueError("publication_stage_parent_escape")
        current /= part
        status = current.lstat()
        if current.is_symlink() or getattr(status, "st_file_attributes", 0) & 0x400:
            raise ValueError("publication_stage_parent_link_forbidden")
    if not path.is_dir():
        raise ValueError("publication_stage_owned_parent_required")
    return path


@dataclass(frozen=True)
class StagedPublicationResources:
    logical_repository_root: str
    current_root: Path
    selected_root: Path
    selected_directory: str
    selected_index_sha256: str
    closure_sha256: str
    selected_loader_payloads: object


class OwnedPublicationResourceStage(AbstractContextManager):
    """Fresh exclusively-owned copies; never hardlinks from mutable Root."""
    def __init__(self, *, parent: Path, authority: OriginalPublicationResourceAuthority,
                 resources: FrozenPublicationResourceReader, bounds: ResourceBounds):
        if (not isinstance(authority, OriginalPublicationResourceAuthority)
                or not isinstance(resources, FrozenPublicationResourceReader)
                or authority._issued.get(resources.closure.nonce) is not resources.closure):
            raise ValueError("publication_stage_issuer_identity_required")
        self.parent, self.authority, self.resources = _owned_parent(parent), authority, resources
        self.bounds = bounds
        self.path = None
        self.files = {}
        self.codes = {}
        self.description = None

    def __enter__(self):
        if self.path is not None:
            raise ValueError("publication_stage_single_owned_scope_required")
        if len(self.resources.closure.entries) > self.bounds.max_resources:
            raise ValueError("publication_stage_resource_count_bound")
        self.path = Path(tempfile.mkdtemp(prefix="pf-", dir=self.parent))
        roots = {"current-root": self.path / "current", "selected-installation": self.path / "selected"}
        selected_payloads, total = {}, 0
        try:
            for item in self.resources.closure.entries:
                if item.namespace not in roots:
                    raise ValueError("publication_stage_namespace_invalid")
                key = (item.namespace, item.logical_ref)
                if key in self.files:
                    raise ValueError("publication_stage_duplicate_logical_reference")
                if item.purpose == "source_attestation":
                    expected, size = self.resources.attest_source(*key)
                    raw = self.authority.read_normal_source(self.resources.closure, *key)
                    self.codes[key] = raw
                    if expected != item.sha256 or size != item.bytes:
                        raise ValueError("publication_stage_source_attestation_changed")
                elif item.purpose == "data_resource":
                    raw = self.resources.read_data(*key)
                else:
                    raise ValueError("publication_stage_resource_purpose_invalid")
                if len(raw) != item.bytes or sha(raw) != item.sha256:
                    raise ValueError("publication_stage_original_bytes_changed")
                total += len(raw)
                if len(raw) > self.bounds.max_resource_bytes or total > self.bounds.max_total_bytes:
                    raise ValueError("publication_stage_byte_bound")
                target = roots[item.namespace].joinpath(*_relative(item.logical_ref))
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("xb") as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                self.files[key] = (target, item.sha256, item.bytes)
                if item.namespace == "selected-installation" and item.logical_ref != "release.json":
                    # .py in this map came exclusively from installed normal
                    # Root bytes above. No source from material may be compiled.
                    selected_payloads[item.logical_ref] = raw
            closure = self.resources.closure
            self.description = StagedPublicationResources(closure.repository_root,
                roots["current-root"], (roots["current-root"] if closure.mode == "current-installation" else roots["selected-installation"]), closure.selected_directory,
                closure.selected_index_sha256, closure.closure_sha256,
                MappingProxyType(selected_payloads))
            self.require_unchanged()
            return self.description
        except BaseException:
            self._cleanup()
            raise

    def require_unchanged(self):
        if self.path is None or self.description is None:
            raise ValueError("publication_stage_owned_scope_required")
        for key, (path, expected, size) in self.files.items():
            current = self.path
            for piece in path.relative_to(self.path).parts:
                current /= piece
                status = current.lstat()
                if current.is_symlink() or getattr(status, "st_file_attributes", 0) & 0x400:
                    raise ValueError("publication_stage_file_link_forbidden")
            status = path.stat()
            if getattr(status, "st_nlink", 1) != 1:
                raise ValueError("publication_stage_file_hardlink_forbidden")
            raw = path.read_bytes()
            if len(raw) != size or sha(raw) != expected:
                raise ValueError("publication_stage_file_bytes_changed")
            if key in self.codes:
                fresh = self.authority.read_normal_source(self.resources.closure, *key)
                if fresh != self.codes[key]:
                    raise ValueError("publication_stage_normal_source_changed")

    def _cleanup(self):
        if self.path is not None:
            # The target is an exclusively created child of the checked
            # parent. Do not traverse or remove supplied external paths.
            if self.path.parent != self.parent or not self.path.name.startswith("pf-"):
                raise ValueError("publication_stage_cleanup_target_changed")
            _owned_parent(self.parent)
            status = self.path.lstat()
            if (self.path.is_symlink() or getattr(status, "st_file_attributes", 0) & 0x400
                    or self.path.resolve(strict=True).parent != self.parent.resolve(strict=True)):
                raise ValueError("publication_stage_cleanup_resolved_target_changed")
            shutil.rmtree(self.path)
            self.path = None

    def __exit__(self, kind, value, traceback):
        try:
            if kind is None:
                self.require_unchanged()
        finally:
            self._cleanup()
        return False

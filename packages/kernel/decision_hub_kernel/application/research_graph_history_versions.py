"""Registered original Graph/Selection/Publication readers; no historical execution grant.

The retained reader executes its original source and schema closure. Every read
checks resources and runtime again. Only plain serialized results escape its
private namespace; no old service or Frozen object is returned to a caller.
"""

from __future__ import annotations

import asyncio
import builtins
import hashlib
import importlib
import importlib.metadata
import importlib.util
import json
import os
import re
import sqlite3
import sys
import threading
from collections.abc import Callable, Generator, Mapping
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from types import MappingProxyType, ModuleType
from typing import Any, Protocol
from uuid import uuid4

from sqlalchemy import event, select
from sqlalchemy.orm import Session

from .product_release_provenance import (
    _loaded_codec as _require_loaded_provenance_codec,
)
from .product_release_provenance import (
    resolve_product_receipt,
)

_CURRENT_ROOT = Path(__file__).resolve().parents[4]
_RESOURCE_ROOT = Path(__file__).resolve().parents[2] / "research_recipe_versions"
_SHARED_MODULES = frozenset(
    {
        "packages.kernel.decision_hub_kernel.persistence.db",
        "packages.kernel.decision_hub_kernel.application.research_context_selection_versions",
    }
)
_BINDING = "packages.kernel.decision_hub_kernel.application.research_graph_binding"
_RECIPE = "packages.kernel.decision_hub_kernel.application.research_graph_recipe"
_RECIPE_V3 = "packages.kernel.decision_hub_kernel.application.research_graph_recipe_v3"
_SELECTOR = "packages.kernel.decision_hub_kernel.application.research_context_selection"
_COMMIT = "packages.kernel.decision_hub_kernel.application.commit"
# Recovered exact originals are a terminal read port, never an execution fallback.
_TERMINAL_READ_RECIPES = frozenset({
    "e26b9e0b0677878c511897580659a748bcce47862b8035d5a89ec1ce4647617d",
})
# These original helper names are part of the frozen policy identity hash.
# Restore only their logical names after execution; imports and typed objects
# remain in this release's private alias namespace.
_POLICY_LOGICAL_MODULE_NAMES = frozenset(
    {
        "packages.kernel.decision_hub_kernel.decision.gate",
        "packages.kernel.decision_hub_kernel.decision.horizon_contract",
        "packages.kernel.decision_hub_kernel.decision.sufficiency",
        "packages.kernel.decision_hub_kernel.application.research_evidence",
        "packages.kernel.decision_hub_kernel.decision.research_loop",
        "packages.kernel.decision_hub_kernel.application.research_probability_binding",
    }
)
_SHA = re.compile(r"[a-f0-9]{64}")
_LOCK = threading.RLock()
_READ_STACK: ContextVar[tuple[tuple[int, str, str, str], ...]] = ContextVar(
    "retained_graph_selection_reads", default=()
)
_V3_READ_SCOPE: ContextVar[tuple[Any, ...] | None] = ContextVar("retained_v3_installation_read", default=None)


@dataclass(frozen=True)
class RetainedGraphRead:
    receipt_json: str
    projection_json: str


@dataclass(frozen=True)
class RetainedPublicationRead:
    artifact_id: str
    gate_json: str


@dataclass(frozen=True)
class RetainedRecipeRead:
    reference_json: str
    receipt_json: str


@dataclass(frozen=True)
class RetainedPublicationQualityRead:
    """Serialized originals; no retained type, reusable proof or permission."""

    view_json: str
    artifact_id: str
    gate_json: str
    selected_observation_ref_json: str | None = None


# This terminal storage protocol mirrors these exact original consumers. A
# different archived implementation falls back to its original strict reader.
# These hashes select semantics, never substitute for the archive verification.
_QUALITY_STORAGE_CONSUMERS = MappingProxyType({
    "packages/kernel/decision_hub_kernel/application/commit.py":
        frozenset({"112ab7edaa1312d604f3efda9bb68b4b2d8da0ff7b04dcf10fa389ac07573c67"}),
    "packages/kernel/decision_hub_kernel/application/research_policy_checker.py":
        frozenset({
            "fee1fcee31717849a2585c22cd94271fabdaf00f2832233633ef0b0039cbb0ed",
            # Complete audit originals from the already published 32 consumer.
            # The storage mirror invokes its original trajectory helper and
            # authentication, retaining the original whole-byte limit.
            "f6ef852de14e438ed5f6a064ceac50a874dda0d3341529887ca13d037e03c443",
        }),
})


@dataclass(frozen=True)
class _Registration:
    directory: str
    index_hash: str
    graph_hash: str
    selector_hash: str
    private_original_orm: bool = False


_REGISTRATIONS = (
    _Registration(
        "0de4b8b0",
        "856c76d4feed9dcd2125077d6e8633dc4de2c371cf181be0eb7f9508fcb7fed2",
        "0de4b8b096c038698c023377bc5789ab1cb054596c64ca5af56fab1a95a28296",
        "12a0c4de6dea002add21ae7255d6f5ba2af15de8d0b9c6ac390b562096ce37c0",
    ),
    _Registration(
        "268fa500",
        "bde8ee86e0eec3c0263cfbb4b3977fb4b9f91c23a6ede9a854d5cb22a65e0966",
        "268fa50028e05401b5f2ad1ea97a1cdebf01798041457d24af59cccc623384fb",
        "748ea7bc11efd2fe37cd64fb9307848dec5fe0c07f86b680be4f8fbf67d9e1b1",
        private_original_orm=True,
    ),
    _Registration(
        "a58938f2",
        "aa189c1c7e3764bcbfb27a1e7d0d4281de2e66066c496e4404286ff47ce74166",
        "a58938f2ba22d8faf8276bc3b01e1849e2295635facba5d199d7cba001a9bec5",
        "cb35215c8154c6081173b0d956bed3005bb9aa109099ae4bc5f89c14d2be9ff2",
        private_original_orm=True,
    ),
)


@dataclass(frozen=True)
class _RegistryBinding:
    receipt_path: Path
    receipt_hash: str
    registry_path: str
    registry_hash: str
    source_hashes: Mapping[str, str]
    transport_hashes: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


_REGISTRY_BINDING: _RegistryBinding | None = None
_REGISTRY_SCHEMA = "research-retained-recipe-registry.v1"
_REGISTRY_SCHEMA_V2 = "research-retained-recipe-registry.v2"
_REGISTRY_PREFIX = "packages/kernel/research_recipe_versions/registries/"


FROZEN_PUBLICATION_HOST_CONTRACT = "publication-frozen-host.v1"
_FROZEN_DESCRIPTOR = globals().get("_FROZEN_DESCRIPTOR")


# Public normal metadata ownership is separate from execution/publication grants.
_FROZEN_HISTORY_OWNERS80: dict[object, Any] = {}
_FROZEN_HISTORY_READ80: ContextVar[Any] = ContextVar("normal_frozen_history_owner80", default=None)
_NORMAL_HISTORY_NAME80 = "packages.kernel.decision_hub_kernel.application.research_graph_history_versions"
_LOADED_METADATA_OWNER_SOURCE_SHA80 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _owner_changed80() -> ValueError:
    return ValueError("publication_frozen_history_owner_changed")


def _original_functions80(module: ModuleType) -> tuple[tuple[str, Any, Any], ...]:
    names = ("_managed_read", "_replay", "consume_retained_publication_quality_in_session",
             "read_frozen_registered_original_in_session80",
             "consume_frozen_registered_quality_in_session80", "_loaded_release",
             "_verified_release", "_verified_release_uncached", "_read_only",
             "_verified_release_read_scope", "_private_v3_read_scope",
             "publication_retained_source_snapshot_read_scope", "_PrivateRelease.__init__",
             "_PrivateRelease.load", "_VerifiedReleaseRead.boundary_matches")
    result = []
    for name in names:
        function = module
        for part in name.split("."):
            function = getattr(function, part)
        wrapped = getattr(function, "__wrapped__", function)
        result.append((name, function, (function.__code__, wrapped, wrapped.__code__)))
    return tuple(result)


def _owner_structure80(descriptor, stage, host):
    return (descriptor.authority, descriptor.closure, descriptor.resources,
            descriptor.logical_root, descriptor.mode, descriptor.scope_nonce, descriptor.selected_root,
            stage.description, stage.path, host.__name__, host.__file__, host.__package__,
            host._REGISTRY_BINDING, host._VERIFIED_RELEASE_READ, host._V3_READ_SCOPE)


def _code_identity80(function):
    wrapped = getattr(function, "__wrapped__", function)
    closure = getattr(function, "__closure__", None)
    return (function, function.__code__, wrapped, wrapped.__code__,
            tuple(cell.cell_contents for cell in closure) if closure is not None else None)


def _descriptor_routes80(descriptor):
    return tuple((name, _code_identity80(getattr(type(descriptor), name))) for name in (
        "current_authorization_read", "selected_bytes", "current_bytes", "shared_original", "release_bytes"))


def _io_roles80(io):
    return tuple((name, getattr(io, name), tuple(sorted(
        (path, value["sha256"], value["bytes"], value["present"])
        for path, value in getattr(io, name).items())))
        for name in ("sources", "authorization_files", "staged", "runtime", "identity_schemas", "_all"))


@dataclass(frozen=True)
class _FrozenHistoryOwnerState80:
    authority: Any
    closure: Any
    resources: Any
    stage: Any
    descriptor: Any
    host: ModuleType
    binding: Any
    source_sha256: str
    functions: tuple[tuple[str, Any, Any], ...]
    host_context: Any
    loader: Any = None
    capture_json: str | None = None
    io: Any = None
    structure: tuple[Any, ...] = ()
    io_roles: tuple[Any, ...] = ()
    io_routing: tuple[Any, ...] = ()
    descriptor_routes: tuple[Any, ...] = ()
    loader_builder: tuple[Any, ...] = ()


@dataclass(frozen=True)
class _FrozenHistoryReadState80:
    owner: Any
    session: Session
    run_id: str
    collector: Any
    leases: tuple[Any, ...]
    factories: tuple[Any, ...]
    factory_methods: tuple[tuple[Any, Any], ...]
    snapshots: tuple[Any, ...]
    held: tuple[tuple[Any, Any, tuple[Any, ...], tuple[Any, ...]], ...]
    private: Any
    current_binding: Any
    retained_binding: Any
    verified_json: str
    verified_payloads: Any
    verified_root: Path
    lease_identity: tuple[tuple[Any, ...], ...]


class _PublicationFrozenHostOwner80:
    """Opaque normal factory product; only the three issued Source scopes activate it."""
    @property
    def descriptor(self):
        return self._prepared().descriptor

    @property
    def host(self):
        return self._prepared().host

    def _prepared(self):
        _require_owner_implementation80()
        state = _FROZEN_HISTORY_OWNERS80.get(self)
        if (type(state) is not _FrozenHistoryOwnerState80
                or __name__ != _NORMAL_HISTORY_NAME80
                or sys.modules.get(__name__) is not sys.modules.get(_NORMAL_HISTORY_NAME80)
                or _REGISTRY_BINDING is not state.binding
                or state.descriptor.frozen_history_owner80 is not self
                or state.descriptor.authority is not state.authority
                or state.descriptor.closure is not state.closure
                or state.descriptor.resources is not state.resources
                or state.resources.closure is not state.closure
                or state.host.__dict__.get("_FROZEN_DESCRIPTOR") is not state.descriptor
                or sys.modules.get(state.host.__name__) is not state.host
                or state.host._FROZEN_HISTORY_READ80 is not state.host_context
                or state.host_context is _FROZEN_HISTORY_READ80
                or state.host_context.get() is not None
                or state.stage.authority is not state.authority
                or state.stage.resources is not state.resources
                or state.stage.description is None
                or state.stage.description.closure_sha256 != state.closure.closure_sha256
                or state.stage.description is not state.structure[7]
                or _owner_structure80(state.descriptor, state.stage, state.host) != state.structure):
            raise _owner_changed80()
        if _descriptor_routes80(state.descriptor) != state.descriptor_routes:
            raise _owner_changed80()
        if state.loader_builder:
            from .research_publication_runtime48 import require_current_loader_type_implementation80
            if _code_identity80(require_current_loader_type_implementation80()) != state.loader_builder:
                raise _owner_changed80()
        # Public authority method authenticates the issued object and normal binding.
        state.authority.authorization_sources(state.closure)
        state.host._require_owner_implementation80()
        for name, function, code in state.functions:
            actual = state.host
            for part in name.split("."):
                actual = getattr(actual, part, None)
            wrapped = getattr(actual, "__wrapped__", actual)
            if (actual is not function or getattr(actual, "__code__", None) is not code[0]
                    or wrapped is not code[1] or getattr(wrapped, "__code__", None) is not code[2]):
                raise _owner_changed80()
        if state.loader is not None and state.descriptor.loader is not state.loader:
            raise _owner_changed80()
        if state.descriptor.io is not state.io:
            raise _owner_changed80()
        if state.io is not None:
            if (state.io.interpreter is not state.io_routing[2]
                    or state.io.normal_identity is not state.io_routing[3]
                    or state.io._authorization is not state.io_routing[4]):
                raise _owner_changed80()
            if (state.io.root, state.io.native_database, state.io.interpreter,
                    state.io.normal_identity, state.io._authorization) != state.io_routing:
                raise _owner_changed80()
            for name, container, values in state.io_roles:
                actual = getattr(state.io, name)
                if actual is not container or tuple(sorted((path, value["sha256"], value["bytes"], value["present"])
                        for path, value in actual.items())) != values:
                    raise _owner_changed80()
        return state

    def create_current_loader(self, *, capture):
        from .research_publication_runtime48 import require_current_loader_type_implementation80
        state = self._prepared()
        _current_loader_type = require_current_loader_type_implementation80()
        if (state.loader is not None or _FROZEN_HISTORY_READ80.get() is not None
                or type(capture.get("source_snapshot_v2")) is not dict
                or type(capture.get("retained_source_snapshot_v2")) is not dict):
            raise _owner_changed80()
        payloads = {item.logical_ref: state.authority.read_normal_source(
            state.closure, item.namespace, item.logical_ref)
            for item in state.closure.entries
            if item.namespace == "current-root" and item.purpose == "source_attestation"}
        registration = state.host._Registration(
            "frozen_current_" + state.descriptor.scope_nonce, "0" * 64, "0" * 64, "0" * 64)
        loader_type = _current_loader_type(state.host, state.descriptor,
                                          current_policy_source_snapshot=True)
        loader = loader_type(registration, payloads, state.stage.description.current_root, v3=True)
        loader.shared_modules = frozenset()
        state.descriptor.loader = loader
        from dataclasses import replace
        _FROZEN_HISTORY_OWNERS80[self] = replace(state, loader=loader,
            capture_json=_canonical({"source_snapshot_v2": capture["source_snapshot_v2"],
                                     "retained_source_snapshot_v2": capture["retained_source_snapshot_v2"]}),
            loader_builder=_code_identity80(_current_loader_type))
        self._prepared()
        return loader

    def bind_io(self, io):
        from dataclasses import replace
        from .research_publication_frozen_io48 import FrozenConsumptionIO, key
        state = self._prepared()
        if (state.io is not None or state.loader is None
                or type(io) is not FrozenConsumptionIO or io.active or io.used
                or io.root != state.authority.root or io.normal_identity is None
                or io.normal_identity.authority is not state.authority
                or io.normal_identity.closure is not state.closure):
            raise _owner_changed80()
        expected_sources = {key(state.authority.source_path(state.closure, item.namespace, item.logical_ref)):
            {"sha256": item.sha256, "bytes": item.bytes, "present": True}
            for item in state.closure.entries if item.purpose == "source_attestation"}
        expected_authorization = {}
        for ref, digest in state.authority.authorization_sources(state.closure):
            entries = [item for item in state.closure.entries if item.namespace == "current-root"
                       and item.logical_ref == ref and item.physical_ref == ref and item.purpose == "data_resource"]
            if len(entries) != 1 or entries[0].sha256 != digest:
                raise _owner_changed80()
            expected_authorization[key(state.authority.root / ref)] = {
                "sha256": digest, "bytes": entries[0].bytes, "present": True}
        expected_staged = {}
        for item in state.closure.entries:
            pair = (item.namespace, item.logical_ref)
            root = state.stage.description.current_root if item.namespace == "current-root" else state.stage.description.selected_root
            path = root / item.logical_ref
            if state.stage.files.get(pair) != (path, item.sha256, item.bytes):
                raise _owner_changed80()
            expected_staged[key(path)] = {"sha256": item.sha256, "bytes": item.bytes, "present": True}
        expected_schemas = {key(state.authority.root / ref): {"sha256": digest, "bytes": size, "present": True}
                            for ref, digest, size in io.normal_identity.schemas}
        if (io.sources != expected_sources or io.authorization_files != expected_authorization
                or io.staged != expected_staged or io.identity_schemas != expected_schemas
                or set(state.stage.files) != {(item.namespace, item.logical_ref) for item in state.closure.entries}):
            raise _owner_changed80()
        expected_all = {**expected_sources, **expected_authorization, **expected_staged,
                        **io.runtime, **expected_schemas}
        if io._all != expected_all:
            raise _owner_changed80()
        state.descriptor.io = io
        _FROZEN_HISTORY_OWNERS80[self] = replace(state, io=io, io_roles=_io_roles80(io),
            io_routing=(io.root, io.native_database, io.interpreter, io.normal_identity, io._authorization))
        self._prepared()

    @contextmanager
    def shared_original_read_scope(self, session, *, run_id, collector,
                                   root_lease, current_private_lease, retained_private_lease):
        from ..persistence import research_source_snapshot as root_factory
        from .research_graph_binding import _ReadSourceGuard
        state = self._prepared()
        if (state.loader is None or state.capture_json is None
                or state.closure.mode != "retained-installation"
                or run_id != state.closure.run_id or _FROZEN_HISTORY_READ80.get() is not None):
            raise _owner_changed80()
        prefix = "packages.kernel.decision_hub_kernel."
        source_name = prefix + "persistence.research_source_snapshot"
        binding_name = prefix + "application.research_graph_binding"
        private = state.host._LOADED.get(state.closure.selected_directory)
        if private is None:
            raise _owner_changed80()
        current_factory = state.loader.modules.get(source_name)
        current_binding = state.loader.modules.get(binding_name)
        retained_factory = private.modules.get(source_name)
        retained_binding = private.modules.get(binding_name)
        if None in (current_factory, current_binding, retained_factory, retained_binding):
            raise _owner_changed80()
        verified = state.host._VERIFIED_RELEASE_READ.get()
        if (verified is None or verified.session is not session or verified.run_id != run_id
                or verified.registration != private.registration):
            raise _owner_changed80()
        leases = (root_lease, current_private_lease, retained_private_lease)
        factories = (root_factory, current_factory, retained_factory)
        guards = (_ReadSourceGuard, current_binding._ReadSourceGuard, retained_binding._ReadSourceGuard)
        declaration = json.loads(state.capture_json)
        expected = (declaration["source_snapshot_v2"], declaration["source_snapshot_v2"],
                    declaration["retained_source_snapshot_v2"])
        for factory, lease, guard, snapshot in zip(factories, leases, guards, expected, strict=True):
            if (type(lease) is not factory.PublicationSourceReadScope
                    or lease.guard_type is not guard
                    or factory.active_source_snapshot(guard) is not lease
                    or lease.snapshot.model_dump(mode="json") != snapshot):
                raise _owner_changed80()
        scope = _FrozenHistoryReadState80(self, session, run_id, collector, leases, factories,
            tuple((factory.active_source_snapshot, factory.active_source_snapshot.__code__)
                  for factory in factories), tuple(lease.snapshot for lease in leases),
            tuple((lease.files, lease.directories, tuple(lease.files), tuple(lease.directories))
                  for lease in leases), private, current_binding, retained_binding,
            verified.index_json, verified.payloads, verified.root,
            tuple((lease.guard_type, lease.guard, lease.root, lease.native_files,
                   lease.guard_implementations) for lease in leases))
        entry_context = _FROZEN_HISTORY_READ80
        token = entry_context.set(scope)
        try:
            self.require_active()
            with state.host._private_v3_read_scope(
                    session, private, run_id, reuse_shape=True), retained_binding.publication_final_read_scope(session):
                yield self
                self.require_active()
            self.require_active()
        finally:
            entry_context.reset(token)

    def require_active(self):
        state = self._prepared()
        from .research_publication_frozen_io48 import _CURRENT_IO
        scope = _FROZEN_HISTORY_READ80.get()
        if (type(scope) is not _FrozenHistoryReadState80 or scope.owner is not self
                or scope.run_id != state.closure.run_id or state.io is None
                or not state.io.active or _CURRENT_IO.get() is not state.io
                or state.io.normal_identity.lease is not scope.leases[0]):
            raise _owner_changed80()
        session, collector = scope.session, scope.collector
        prefix = "packages.kernel.decision_hub_kernel."
        source_name = prefix + "persistence.research_source_snapshot"
        binding_name = prefix + "application.research_graph_binding"
        verified = state.host._VERIFIED_RELEASE_READ.get()
        v3_scope = state.host._V3_READ_SCOPE.get()
        if (state.host._LOADED.get(state.closure.selected_directory) is not scope.private
                or state.loader.modules.get(source_name) is not scope.factories[1]
                or state.loader.modules.get(binding_name) is not scope.current_binding
                or scope.private.modules.get(source_name) is not scope.factories[2]
                or scope.private.modules.get(binding_name) is not scope.retained_binding
                or verified is None or verified.session is not session or verified.run_id != scope.run_id
                or verified.registration != scope.private.registration
                or verified.index_json != scope.verified_json or verified.payloads != scope.verified_payloads
                or verified.root != scope.verified_root
                or not verified.boundary_matches(verified.registration)
                or v3_scope is None or v3_scope[0] is not scope.private or v3_scope[1] is not session
                or v3_scope[2] != scope.run_id or v3_scope[3] is not session.get_transaction()
                or v3_scope[4] is not session.get_nested_transaction()
                or v3_scope[5] is not collector.connection or v3_scope[6] is not collector.native
                or v3_scope[7] != collector.native.total_changes):
            raise _owner_changed80()
        for factory, lease, method, snapshot, held, identity in zip(
                scope.factories, scope.leases, scope.factory_methods,
                scope.snapshots, scope.held, scope.lease_identity, strict=True):
            actual = factory.active_source_snapshot
            if (actual is not method[0] or actual.__code__ is not method[1]
                    or type(lease) is not factory.PublicationSourceReadScope
                    or actual(lease.guard_type) is not lease or not lease.active
                    or lease.guard_type is not identity[0] or lease.guard is not identity[1]
                    or lease.root != identity[2] or lease.native_files is not identity[3]
                    or lease.guard_implementations is not identity[4]
                    or lease.session is not session or lease.run_id != scope.run_id
                    or lease.collector is not collector
                    or lease.snapshot is not snapshot
                    or lease.files is not held[0] or lease.directories is not held[1]
                    or tuple(lease.files) != held[2] or tuple(lease.directories) != held[3]
                    or threading.get_ident() != lease.thread or _current_async_task() is not lease.task
                    or not isinstance(lease.native, sqlite3.Connection)
                    or not lease.native.in_transaction or lease.native.text_factory is not str
                    or lease.native.total_changes != lease.changes
                    or session.connection() is not lease.connection or lease.connection.closed
                    or lease.connection.connection.driver_connection is not lease.native
                    or session.get_transaction() is not lease.transaction
                    or session.get_nested_transaction() is not lease.nested
                    or session.new or session.dirty or session.deleted
                    or collector.session is not session or collector.connection is not lease.connection
                    or collector.native is not lease.native or collector.owner_thread != lease.thread
                    or not collector._entered or collector._finished):
                raise _owner_changed80()
        collector._require_boundary()
        return state

    def require_host(self, host):
        state = self.require_active()
        if host is not state.host or host._FROZEN_HISTORY_READ80.get() is not None:
            raise _owner_changed80()
        return state

    def read_current_metadata(self, relative, original_reader):
        state = self.require_active()
        relative = _valid_relative(relative)
        if relative.endswith(".py"):
            raise _owner_changed80()
        return state.descriptor.current_authorization_read(relative, original_reader)

    def replay(self, session, **arguments):
        state = self.require_active()
        keys = {"run_id", "reference_json", "kind", "clock", "selector_identity", "declared_json",
                "expected_lease_owner", "candidate_json", "gate_json"}
        kind = arguments.get("kind")
        if (session is not _FROZEN_HISTORY_READ80.get().session
                or set(arguments) != keys or arguments["run_id"] != state.closure.run_id
                or kind not in {"recipe_v3", "selection", "round", "final", "publication"}):
            raise _owner_changed80()
        result = state.host.read_frozen_registered_original_in_session80(session, **arguments)
        self.require_active()
        if result is None:
            if kind == "recipe_v3":
                raise ValueError("publication_frozen_selected_recipe_missing")
            return None
        if kind == "selection":
            if type(result) is not str:
                raise _owner_changed80()
            return result
        if kind == "recipe_v3":
            host_type, root_type, names = state.host.RetainedRecipeRead, RetainedRecipeRead, ("reference_json", "receipt_json")
        elif kind in {"round", "final"}:
            host_type, root_type, names = state.host.RetainedGraphRead, RetainedGraphRead, ("receipt_json", "projection_json")
        else:
            host_type, root_type, names = state.host.RetainedPublicationRead, RetainedPublicationRead, ("artifact_id", "gate_json")
        if type(result) is not host_type:
            raise _owner_changed80()
        fields = {name: getattr(result, name) for name in names}
        if any(type(value) is not str for value in fields.values()):
            raise _owner_changed80()
        return root_type(**fields)

    def consume_quality(self, session, run_id, *, as_of, consume):
        state = self.require_active()
        if session is not _FROZEN_HISTORY_READ80.get().session or run_id != state.closure.run_id:
            raise _owner_changed80()
        def adapt(original):
            self.require_host(state.host)
            if type(original) is not state.host.RetainedPublicationQualityRead:
                raise _owner_changed80()
            fields = {name: getattr(original, name) for name in (
                "view_json", "artifact_id", "gate_json", "selected_observation_ref_json")}
            if any(type(value) is not str and not (
                    name == "selected_observation_ref_json" and value is None)
                    for name, value in fields.items()):
                raise _owner_changed80()
            result = consume(RetainedPublicationQualityRead(**fields))
            self.require_host(state.host)
            return result
        result = state.host.consume_frozen_registered_quality_in_session80(
            session, run_id, as_of=as_of, consume=adapt)
        self.require_active()
        return result

    def close(self):
        failure = None
        try:
            _require_owner_implementation80()
        except BaseException as error:
            failure = error
        # Revocation/cleanup must survive a loaded-method/Context identity F.
        registry = _OWNER_IMPLEMENTATION80[0][3]
        context = _OWNER_IMPLEMENTATION80[0][4]
        state = registry.get(self)
        if state is None:
            if failure is not None:
                raise failure
            return
        if context.get() is not None:
            raise _owner_changed80()
        registry.pop(self)
        try:
            for private in tuple(state.host._LOADED.values()):
                try:
                    private.discard()
                except BaseException as error:
                    if failure is None:
                        failure = error
        finally:
            try:
                if state.loader is not None:
                    try:
                        state.loader.discard()
                    except BaseException as error:
                        if failure is None:
                            failure = error
            finally:
                if sys.modules.get(state.host.__name__) is state.host:
                    sys.modules.pop(state.host.__name__)
        if failure is not None:
            raise failure


def create_publication_frozen_host_owner(authority, closure, resources, stage):
    """Normal Source-derived host; no caller descriptor/module or execution claim."""
    from .research_publication_resources import OriginalPublicationResourceAuthority, FrozenPublicationResourceReader
    from .research_publication_resource_stage import OwnedPublicationResourceStage
    from .research_publication_runtime48 import FrozenDescriptor, require_current_loader_type_implementation80
    _require_owner_implementation80()
    if (__name__ != _NORMAL_HISTORY_NAME80 or _FROZEN_DESCRIPTOR is not None
            or _FROZEN_HISTORY_READ80.get() is not None
            or type(authority) is not OriginalPublicationResourceAuthority
            or type(resources) is not FrozenPublicationResourceReader
            or type(stage) is not OwnedPublicationResourceStage
            or resources.closure is not closure or stage.authority is not authority
            or stage.resources is not resources or stage.description is None
            or stage.description.closure_sha256 != closure.closure_sha256
            or closure.mode != "retained-installation"):
        raise _owner_changed80()
    authority.authorization_sources(closure)
    ref = _NORMAL_HISTORY_NAME80.replace(".", "/") + ".py"
    raw = authority.read_normal_source(closure, "current-root", ref)
    if authority.source_path(closure, "current-root", ref) != _CURRENT_ROOT / ref:
        # Source-authenticated historical mappings keep their complete old host.
        # None is only an Owner-contract qualification, never a read/permission P.
        return None
    if _hash(raw) != _LOADED_METADATA_OWNER_SOURCE_SHA80:
        raise _owner_changed80()
    descriptor = FrozenDescriptor(authority, closure, resources)
    descriptor.selected_root = stage.description.selected_root
    try:
        descriptor.bind_authorization_diagnostic_sources78(stage)
    except BaseException:
        pass  # Original bounded diagnostic remains non-authoritative.
    owner = _PublicationFrozenHostOwner80()
    descriptor.frozen_history_owner80 = owner
    alias = "_decision_hub_frozen_host_" + descriptor.scope_nonce
    if alias in sys.modules:
        raise _owner_changed80()
    host = ModuleType(alias); host.__file__ = str(authority.root / ref)
    host.__package__ = "packages.kernel.decision_hub_kernel.application"
    host.__dict__["_FROZEN_DESCRIPTOR"] = descriptor
    host.__dict__["_FROZEN_ROOT_METADATA_ISSUER80"] = (
        sys.modules[__name__], require_publication_frozen_host_owner80,
        require_publication_frozen_host_owner80.__code__)
    sys.modules[alias] = host
    try:
        exec(compile(raw, host.__file__, "exec"), host.__dict__)
        host.configure_retained_recipe_registry_from_validated_release(
            receipt_path=authority.root / closure.receipt_ref, receipt_sha256=closure.receipt_sha256)
        _FROZEN_HISTORY_OWNERS80[owner] = _FrozenHistoryOwnerState80(
            authority, closure, resources, stage, descriptor, host, _REGISTRY_BINDING,
            _hash(raw), _original_functions80(host), host._FROZEN_HISTORY_READ80,
            structure=_owner_structure80(descriptor, stage, host),
            descriptor_routes=_descriptor_routes80(descriptor),
            loader_builder=_code_identity80(require_current_loader_type_implementation80()))
        owner._prepared()
        return owner
    except BaseException:
        _FROZEN_HISTORY_OWNERS80.pop(owner, None)
        sys.modules.pop(alias, None)
        raise


def _active_frozen_history_owner80():
    scope = _FROZEN_HISTORY_READ80.get()
    if scope is None:
        return None
    if __name__ != _NORMAL_HISTORY_NAME80 or type(scope) is not _FrozenHistoryReadState80:
        raise _owner_changed80()
    scope.owner.require_active()
    return scope.owner


def read_frozen_registered_original_in_session80(session: Session, **arguments):
    """Exact Host original dispatch; its own delegation Context stays default None."""
    descriptor = _FROZEN_DESCRIPTOR
    if descriptor is None or _FROZEN_HISTORY_READ80.get() is not None:
        raise _owner_changed80()
    owner = _require_root_metadata_issuer80(descriptor, session, arguments.get("run_id"))
    state = owner.require_host(sys.modules[__name__])
    if (set(arguments) != {"run_id", "reference_json", "kind", "clock", "selector_identity",
                          "declared_json", "expected_lease_owner", "candidate_json", "gate_json"}
            or arguments["run_id"] != state.closure.run_id
            or arguments["kind"] not in {"recipe_v3", "selection", "round", "final", "publication"}):
        raise _owner_changed80()
    result = _replay(session, **arguments)
    owner.require_host(sys.modules[__name__])
    return result


def _owner_implementation_values80():
    cls = _PublicationFrozenHostOwner80
    functions = (create_publication_frozen_host_owner, _active_frozen_history_owner80,
        _owner_changed80, _original_functions80, _owner_structure80, _io_roles80,
        _code_identity80, _descriptor_routes80, _require_root_metadata_issuer80,
        require_publication_frozen_host_owner80,
        _owner_implementation_values80, _require_owner_implementation80,
        cls.descriptor.fget, cls.host.fget, cls._prepared, cls.create_current_loader,
        cls.bind_io, cls.shared_original_read_scope, cls.require_active, cls.require_host,
        cls.read_current_metadata, cls.replay, cls.consume_quality, cls.close,
        read_frozen_registered_original_in_session80, consume_frozen_registered_quality_in_session80)
    return ((cls, _FrozenHistoryOwnerState80, _FrozenHistoryReadState80,
             _FROZEN_HISTORY_OWNERS80, _FROZEN_HISTORY_READ80),
            tuple(_code_identity80(fn) for fn in functions))


def _require_owner_implementation80():
    objects, functions = _OWNER_IMPLEMENTATION80
    current_objects, current_functions = _owner_implementation_values80()
    if (any(actual is not expected for actual, expected in zip(current_objects, objects, strict=True))
            or any(any(a is not b for a, b in zip(actual[:4], expected[:4], strict=True))
                   or actual[4] != expected[4]
                   for actual, expected in zip(current_functions, functions, strict=True))):
        raise _owner_changed80()


def consume_frozen_registered_quality_in_session80(session: Session, run_id: str, *, as_of, consume):
    """Callback remains inside the unchanged Host whole-original entry/exit fence."""
    descriptor = _FROZEN_DESCRIPTOR
    if descriptor is None or _FROZEN_HISTORY_READ80.get() is not None:
        raise _owner_changed80()
    owner = _require_root_metadata_issuer80(descriptor, session, run_id)
    result = consume_retained_publication_quality_in_session(
        session, run_id, as_of=as_of, consume=consume)
    owner.require_host(sys.modules[__name__])
    return result


def require_publication_frozen_host_owner80(descriptor, host, session, run_id):
    """Normal ROOT issuer validation; a descriptor method shape grants nothing."""
    _require_owner_implementation80()
    owner = getattr(descriptor, "frozen_history_owner80", None)
    scope = _FROZEN_HISTORY_READ80.get()
    if (__name__ != _NORMAL_HISTORY_NAME80 or type(owner) is not _PublicationFrozenHostOwner80
            or owner not in _FROZEN_HISTORY_OWNERS80 or type(scope) is not _FrozenHistoryReadState80
            or scope.owner is not owner or scope.session is not session or scope.run_id != run_id):
        raise _owner_changed80()
    state = owner.require_host(host)
    if state.descriptor is not descriptor or state.closure.run_id != run_id:
        raise _owner_changed80()
    return owner


def _require_root_metadata_issuer80(descriptor, session, run_id):
    record = globals().get("_FROZEN_ROOT_METADATA_ISSUER80")
    if type(record) is not tuple or len(record) != 3:
        raise _owner_changed80()
    module, issuer, code = record
    if (sys.modules.get(_NORMAL_HISTORY_NAME80) is not module
            or getattr(module, "require_publication_frozen_host_owner80", None) is not issuer
            or issuer.__code__ is not code or _FROZEN_HISTORY_READ80.get() is not None):
        raise _owner_changed80()
    return issuer(descriptor, sys.modules[__name__], session, run_id)


_OWNER_IMPLEMENTATION80 = _owner_implementation_values80()


def _managed_read(relative: str) -> bytes:
    if not (type(relative) is str and relative.endswith(".py")):
        owner = _active_frozen_history_owner80()
        if owner is not None:
            return owner.read_current_metadata(relative, lambda: _managed_current_authorization_read(relative))
    if _FROZEN_DESCRIPTOR is not None:
        return _FROZEN_DESCRIPTOR.current_authorization_read(relative, lambda: _managed_current_authorization_read(relative))
    return _managed_current_authorization_read(relative)

def _managed_current_authorization_read(relative: str) -> bytes:
    relative = _valid_relative(relative)
    current = _CURRENT_ROOT
    for part in PurePosixPath(relative).parts:
        current = current / part
        spelling = str(current.absolute())
        if os.name == "nt" and not spelling.startswith("\\\\?\\"):
            spelling = "\\\\?\\" + spelling
        actual = Path(spelling)
        status = actual.lstat()
        if actual.is_symlink() or getattr(status, "st_file_attributes", 0) & 0x400:
            raise _unavailable()
    if not _long_path(current).is_file() or status.st_size > 2 * 1024 * 1024:
        raise _unavailable()
    from .research_recipe_archive import _read  # pyright: ignore[reportPrivateUsage]
    return _read(_long_path(current))


def configure_retained_recipe_registry_from_validated_release(
    *, receipt_path: Path, receipt_sha256: str
) -> None:
    """Internal trusted release wiring only; no API/env/caller authority.

    The caller must supply the exact receipt already accepted by the normal
    product build guard. This verifies its archive subset again and pins it
    for this process. Unconfigured processes retain only the legacy readers.
    """
    if _active_frozen_history_owner80() is not None:
        raise ValueError("publication_frozen_shared_registry_rebind_forbidden")
    global _REGISTRY_BINDING
    try:
        if _SHA.fullmatch(receipt_sha256) is None:
            raise _unavailable()
        receipt_relative = receipt_path.absolute().relative_to(_CURRENT_ROOT.absolute()).as_posix()
        raw = _managed_read(receipt_relative)
        if _hash(raw) != receipt_sha256:
            raise _unavailable()
        resolved = resolve_product_receipt(
            raw, expected_root=_CURRENT_ROOT, read_resource=_managed_read,
            expected_sha256=receipt_sha256,
        )
        receipt = resolved.value
        evidence = receipt["evidence"]["retained_recipe_archives_v1"]
        if set(evidence) != {"registry"} or set(evidence["registry"]) != {"path", "sha256"}:
            raise _unavailable()
        reference = evidence["registry"]
        relative, digest = reference["path"], reference["sha256"]
        if (type(digest) is not str or _SHA.fullmatch(digest) is None
                or relative != _REGISTRY_PREFIX + digest + ".json"
                or receipt["sources"].get(relative) != digest):
            raise _unavailable()
        sources = receipt["sources"]
        if (type(sources) is not dict or not sources
                or any(type(value) is not str or _SHA.fullmatch(value) is None
                       for value in sources.values())):
            raise _unavailable()
        binding = _RegistryBinding(
            _CURRENT_ROOT / receipt_relative, receipt_sha256, relative, digest,
            MappingProxyType(dict(sources)),
            MappingProxyType(dict(resolved.transport)),
        )
        _registry_registrations(binding)
        with _LOCK:
            if _REGISTRY_BINDING is not None and _REGISTRY_BINDING != binding:
                raise ValueError("research_history_registry_process_rebind_forbidden")
            _REGISTRY_BINDING = binding
    except (KeyError, TypeError, OSError, json.JSONDecodeError) as error:
        raise _unavailable() from error


def _registry_registrations(
    binding: _RegistryBinding, *, force_full: bool = False
) -> tuple[_Registration, ...]:
    raw_receipt = _managed_read(binding.receipt_path.relative_to(_CURRENT_ROOT).as_posix())
    raw = _managed_read(binding.registry_path)
    if _hash(raw_receipt) != binding.receipt_hash or _hash(raw) != binding.registry_hash:
        raise _unavailable()
    scope = _VERIFIED_RELEASE_READ.get()
    if (
        not force_full and scope is not None and scope.marker is not None
        and scope.boundary_matches(scope.registration)
        and bool(scope.registry_snapshot)
        and scope.registry_snapshot[0][0] is binding
    ):
        # Only immutable parsing is reused. Fresh complete transport bytes,
        # managed path/open witnesses and loaded codec identity are mandatory.
        _require_loaded_provenance_codec()
        receipt_root = json.loads(raw_receipt)["repository_root"]
        if (not Path(receipt_root).is_absolute()
                or Path(receipt_root).resolve() != _CURRENT_ROOT.resolve()):
            raise _unavailable()
        for path, digest in binding.transport_hashes.items():
            if _hash(_managed_read(path)) != digest:
                raise _unavailable()
        return scope.registry_snapshot[0][1]
    resolved = resolve_product_receipt(
        raw_receipt, expected_root=_CURRENT_ROOT, read_resource=_managed_read,
        expected_sha256=binding.receipt_hash,
    )
    if (resolved.value["sources"] != binding.source_hashes
            or resolved.transport != binding.transport_hashes):
        raise _unavailable()
    value = json.loads(raw)
    if (type(value) is not dict or set(value) != {"schema_version", "registrations"}
            or value["schema_version"] not in {_REGISTRY_SCHEMA, _REGISTRY_SCHEMA_V2}
            or type(value["registrations"]) is not list or len(value["registrations"]) > 256
            or raw != (_canonical(value) + "\n").encode()):
        raise _unavailable()
    found: list[_Registration] = []
    builtin_hashes = {item.graph_hash for item in _REGISTRATIONS}
    hashes = set(builtin_hashes)
    directories = {item.directory for item in _REGISTRATIONS}
    v2 = value["schema_version"] == _REGISTRY_SCHEMA_V2
    for row in value["registrations"]:
        if (type(row) is not dict
                or set(row) != {"directory", "index_hash", "graph_hash", "selector_hash",
                                "private_original_orm"}
                or any(type(row[key]) is not str or _SHA.fullmatch(row[key]) is None
                       for key in ("directory", "graph_hash", "index_hash", "selector_hash"))
                or (not v2 and row["directory"] != row["graph_hash"])
                or row["private_original_orm"] is not True
                or row["directory"] in directories
                or (row["graph_hash"] in hashes
                    and (not v2 or row["graph_hash"] in builtin_hashes))):
            raise _unavailable()
        relative = "packages/kernel/research_recipe_versions/" + row["directory"] + "/release.json"
        if binding.source_hashes.get(relative) != row["index_hash"]:
            raise _unavailable()
        hashes.add(row["graph_hash"])
        directories.add(row["directory"])
        found.append(_Registration(**row))
    if found != sorted(found, key=lambda item: (item.graph_hash, item.directory)):
        raise _unavailable()
    result = tuple(found)
    if (
        not force_full and scope is not None and scope.marker is not None
        and scope.boundary_matches(scope.registration)
    ):
        scope.registry_snapshot[:] = [(binding, result)]
    return result


def _active_registrations() -> tuple[_Registration, ...]:
    if _REGISTRY_BINDING is None:
        return _REGISTRATIONS
    try:
        return (*_REGISTRATIONS, *_registry_registrations(_REGISTRY_BINDING))
    except (KeyError, TypeError, OSError, json.JSONDecodeError) as error:
        raise _unavailable() from error


def _guard_registered_archive(
    registration: _Registration, index: Mapping[str, Any],
) -> Mapping[str, bytes] | None:
    if registration in _REGISTRATIONS:
        return
    binding = _REGISTRY_BINDING
    if (binding is None
            or registration not in _registry_registrations(binding, force_full=True)):
        raise _unavailable()
    stored = index["stored_files"]
    payloads: dict[str, bytes] = {}
    total = (
        len((_canonical(index) + "\n").encode())
        if index["schema_version"] == "research-retained-recipe-release.v3" else 0
    )
    for name, digest in index["all_files"].items():
        relative = ("packages/kernel/research_recipe_versions/" + registration.directory
                     + "/root/" + name) if name in stored else name
        if binding.source_hashes.get(relative) != digest:
            raise _unavailable()
        payload = (_FROZEN_DESCRIPTOR.selected_bytes(name) if _FROZEN_DESCRIPTOR is not None else _managed_read(relative))
        total += len(payload)
        if len(payload) > 2 * 1024 * 1024 or total > 32 * 1024 * 1024 or _hash(payload) != digest:
            raise _unavailable()
        payloads[name] = payload
    # Only this uncached invocation may reuse its just-read immutable bytes.
    # The outer read scope independently calls this guard again on exit.
    return MappingProxyType(payloads)


def _long_path(path: Path) -> Path:
    value = str(path.resolve())
    if os.name == "nt" and not value.startswith("\\\\?\\"):
        value = "\\\\?\\" + value
    return Path(value)


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _v3_installation_directory(graph_hash: str, receipt: Mapping[str, Any], pointer: Mapping[str, Any]) -> str:
    """The exact namespaced key shared with the file-only archive producer."""
    if type(graph_hash) is not str or _SHA.fullmatch(graph_hash) is None:
        raise _unavailable()
    for reference in (receipt, pointer):
        if (type(reference) is not dict or set(reference) != {"path", "sha256"}
                or type(reference["sha256"]) is not str
                or _SHA.fullmatch(reference["sha256"]) is None):
            raise _unavailable()
        _valid_relative(reference["path"])
    return _hash(_canonical({
        "schema_version": "retained-v3-installation-key.v1", "graph_hash": graph_hash,
        "installation_receipt": receipt, "installed_pointer": pointer,
    }).encode())


def _unavailable() -> ValueError:
    return ValueError("research_history_release_unavailable")


def _valid_relative(value: object) -> str:
    if type(value) is not str:
        raise _unavailable()
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "\\" in value or ":" in value:
        raise _unavailable()
    if not value or path.as_posix() != value:
        raise _unavailable()
    return value


def _runtime_matches(expected: Mapping[str, str]) -> bool:
    current = {"python": sys.version, "sqlite": sqlite3.sqlite_version}
    try:
        current.update(
            {name: importlib.metadata.version(name) for name in expected if name not in current}
        )
    except importlib.metadata.PackageNotFoundError:
        return False
    return current == dict(expected)


def _verified_release_uncached(registration: _Registration) -> tuple[dict[str, Any], dict[str, bytes], Path]:
    directory = _long_path(_RESOURCE_ROOT / registration.directory)
    try:
        archive_relative = (_RESOURCE_ROOT / registration.directory).absolute().relative_to(
            _CURRENT_ROOT.absolute()
        ).as_posix()
        raw = (_FROZEN_DESCRIPTOR.release_bytes(registration) if _FROZEN_DESCRIPTOR is not None else _managed_read(archive_relative + "/release.json"))
        if _hash(raw) != registration.index_hash:
            raise _unavailable()
        index = json.loads(raw)
        if (index.get("schema_version") == "research-retained-recipe-release.v3"
                and raw != (_canonical(index) + "\n").encode()):
            raise _unavailable()
        manifest = index["graph_recipe_manifest"]
        selector = index["selector_identity"]
        files = index["all_files"]
        stored = set(index["stored_files"])
        auxiliary = index["shared_auxiliary_files"]
        declared = {
            item["path"]: item["payload_hash"]
            for item in manifest["source_files"] + manifest["schema_files"]
        }
        v3 = manifest["schema_version"] == "research-graph-recipe-manifest.v3"
        if v3:
            from .research_recipe_archive import _v3_stored_files
            declared = _v3_stored_files(index)
        guarded_payloads = _guard_registered_archive(registration, index)
        allowed = {"research-retained-recipe-release.v2", "research-retained-recipe-release.v3"} if v3 else {"research-retained-recipe-release.v1"}
        if (
            index["schema_version"] not in allowed
            or manifest["schema_version"] not in {"research-graph-recipe-manifest.v2", "research-graph-recipe-manifest.v3"}
            or index["graph_recipe_dependency_hash"] != registration.graph_hash
            or _hash(_canonical(manifest).encode()) != registration.graph_hash
            or selector["implementation_hash"] != registration.selector_hash
            or _hash(
                _canonical(
                    {
                        "files": index["selector_identity_files"],
                        "codec": "canonical_context_selection.v3",
                    }
                ).encode()
            )
            != registration.selector_hash
            or (
                registration.private_original_orm
                and index.get("orm_mode") != "private_original_orm.v1"
            )
            or stored != set(declared)
            or set(files) != stored | set(auxiliary)
            or stored & set(auxiliary)
            or not _runtime_matches(manifest["runtime_versions"])
        ):
            raise _unavailable()
        payloads: dict[str, bytes] = {}
        total = len(raw) if index["schema_version"] == "research-retained-recipe-release.v3" else 0
        for name, expected in files.items():
            relative = _valid_relative(name)
            if type(expected) is not str or _SHA.fullmatch(expected) is None:
                raise _unavailable()
            location = archive_relative + "/root/" + relative if relative in stored else relative
            payload = (
                _managed_read(location)
                if guarded_payloads is None else guarded_payloads[relative]
            )
            total += len(payload)
            if len(payload) > 2 * 1024 * 1024 or total > 32 * 1024 * 1024:
                raise _unavailable()
            if _hash(payload) != expected:
                raise _unavailable()
            if relative in declared and declared[relative] != expected:
                raise _unavailable()
            payloads[relative] = payload
        for name, expected in index["selector_identity_files"].items():
            if files.get(name) != expected:
                raise _unavailable()
        if v3:
            from .research_recipe_archive import _validate_retained_v3_installation
            _validate_retained_v3_installation(index, payloads, expected_root=_CURRENT_ROOT)
        if registration.directory != registration.graph_hash and registration not in _REGISTRATIONS:
            if not v3 or registration.directory != _v3_installation_directory(
                registration.graph_hash, index["installation_binding"]["receipt"],
                index["installation_binding"]["pointer"],
            ):
                raise _unavailable()
        for module_name in _SHARED_MODULES:
            if (
                registration.private_original_orm
                and module_name == "packages.kernel.decision_hub_kernel.persistence.db"
            ):
                # This reviewed release executes its exact archived ORM mappings.
                # The current module never substitutes for a frozen dependency.
                continue
            relative = module_name.replace(".", "/") + ".py"
            if _hash(_managed_read(relative)) != files[relative]:
                raise _unavailable()
        return index, payloads, (_FROZEN_DESCRIPTOR.selected_root if _FROZEN_DESCRIPTOR is not None else directory / "root")
    except (KeyError, TypeError, OSError, json.JSONDecodeError) as error:
        raise _unavailable() from error


@dataclass(frozen=True)
class _RetainedInstallationRead:
    body: str
    verify: Callable[[Mapping[str, Any], Mapping[str, bytes], Path], str]


def _current_async_task() -> object | None:
    try:
        return asyncio.current_task()
    except RuntimeError:
        return None


def _installation_from_description(module: ModuleType, body: str) -> Any:
    value = json.loads(body)
    manifest = module.GraphRecipeManifestV3.model_validate_json(_canonical(value["manifest"]))
    return module._InstalledBindingV3(
        module.GraphRecipeResourceV3.model_validate(value["receipt"]),
        module.GraphRecipeResourceV3.model_validate(value["pointer"]),
        value["dependency_hash"], manifest.registration, value["plugin_build_hash"], manifest,
    )


@dataclass(frozen=True)
class _VerifiedReleaseRead:
    session: Session
    run_id: str
    registration: _Registration
    current_root: Path
    resource_root: Path
    thread_id: int
    transaction: Any
    nested: Any
    connection: Any
    native: sqlite3.Connection
    changes: int
    index_json: str
    payloads: Mapping[str, bytes]
    root: Path
    task: object | None
    marker: str | None
    installations: dict[ModuleType, _RetainedInstallationRead] = field(default_factory=dict)
    registry_snapshot: list[tuple[_RegistryBinding, tuple[_Registration, ...]]] = field(
        default_factory=list
    )

    def boundary_matches(self, registration: _Registration) -> bool:
        private_scope = _V3_READ_SCOPE.get()
        return (
            registration == self.registration
            and _CURRENT_ROOT == self.current_root
            and _RESOURCE_ROOT == self.resource_root
            and threading.get_ident() == self.thread_id
            and _current_async_task() is self.task
            and self.session.get_transaction() is self.transaction
            and self.session.get_nested_transaction() is self.nested
            and self.session.connection() is self.connection
            and self.connection.connection.driver_connection is self.native
            and self.native.in_transaction
            and self.native.total_changes == self.changes
            and not (self.session.new or self.session.dirty or self.session.deleted)
            and (private_scope is None or (
                private_scope[1] is self.session and private_scope[2] == self.run_id
            ))
        )

    def matches(self, registration: _Registration) -> bool:
        return (
            self.boundary_matches(registration)
            and _original_recipe_registration(self.session, self.run_id) == registration
        )


_VERIFIED_RELEASE_READ: ContextVar[_VerifiedReleaseRead | None] = ContextVar(
    "retained_quality_release_read", default=None
)


def _verified_release(registration: _Registration) -> tuple[dict[str, Any], Mapping[str, bytes], Path]:
    scope = _VERIFIED_RELEASE_READ.get()
    if scope is not None and scope.matches(registration):
        # Parse a fresh plain index; only immutable original bytes are reused.
        return json.loads(scope.index_json), scope.payloads, scope.root
    return _verified_release_uncached(registration)


@contextmanager
def _verified_release_read_scope(
    session: Session,
    run_id: str,
    registration: _Registration,
    verified: tuple[dict[str, Any], dict[str, bytes], Path],
) -> Generator[None]:
    """Pure archive bytes and installation descriptions; full fresh exit checks."""
    index, payloads, root = verified
    outer = _VERIFIED_RELEASE_READ.get()
    if (outer is not None and outer.marker is not None
            and outer.session is session and outer.run_id == run_id
            and outer.registration == registration and outer.matches(registration)
            and root == outer.root and _canonical(index) == outer.index_json
            and payloads == outer.payloads):
        # This lexical read remains inside the same complete outer fence.
        # The caller cannot return or begin frozen replay until its uncached
        # source/material/installation exit verification has completed.
        yield
        if (not outer.matches(registration) or root != outer.root
                or _canonical(index) != outer.index_json or payloads != outer.payloads):
            raise ValueError("research_history_read_boundary_changed")
        return
    connection = session.connection()
    native = connection.connection.driver_connection
    if not isinstance(native, sqlite3.Connection) or not native.in_transaction:
        raise ValueError("research_history_v3_physical_snapshot_required")
    # This marker covers only new installation-description reuse. A stricter
    # authorizer retains the original full installation reader without changing
    # its authorizer or claiming that ORM/native identities prove continuity.
    marker: str | None = "retained_installation_" + uuid4().hex
    try:
        native.execute(f"SAVEPOINT {marker}")
    except sqlite3.DatabaseError as error:
        if getattr(error, "sqlite_errorcode", None) != sqlite3.SQLITE_AUTH:
            raise
        marker = None
    scope = _VerifiedReleaseRead(
        session, run_id, registration, _CURRENT_ROOT, _RESOURCE_ROOT, threading.get_ident(),
        session.get_transaction(), session.get_nested_transaction(), connection,
        native, native.total_changes, _canonical(index), MappingProxyType(dict(payloads)), root,
        _current_async_task(), marker,
    )
    token = _VERIFIED_RELEASE_READ.set(scope)
    released = False
    try:
        if not scope.matches(registration):
            raise ValueError("research_history_read_boundary_changed")
        yield
        _VERIFIED_RELEASE_READ.reset(token)
        token = _VERIFIED_RELEASE_READ.set(None)
        final_index, final_payloads, final_root = _verified_release_uncached(registration)
        if (
            not scope.matches(registration)
            or final_root != scope.root
            or _canonical(final_index) != scope.index_json
            or final_payloads != scope.payloads
        ):
            raise ValueError("research_history_publication_original_changed")
        # Every memoized description is freshly rebuilt from the uncached exit
        # bytes and original manifest builder; no memo participates in this step.
        for installation in scope.installations.values():
            if installation.verify(final_index, final_payloads, final_root) != installation.body:
                raise ValueError("research_history_v3_installation_original_changed")
            if not scope.matches(registration):
                raise ValueError("research_history_read_boundary_changed")
        if marker is not None:
            try:
                native.execute(f"RELEASE SAVEPOINT {marker}")
            except sqlite3.Error as error:
                raise ValueError("research_history_read_boundary_changed") from error
            released = True
    finally:
        scope.installations.clear()
        scope.registry_snapshot.clear()
        _VERIFIED_RELEASE_READ.reset(token)
        if marker is not None and not released:
            try:
                native.execute(f"RELEASE SAVEPOINT {marker}")
            except sqlite3.Error:
                pass


class PrivateNamespaceDescriptor(Protocol):
    @property
    def directory(self) -> str: ...

    @property
    def private_original_orm(self) -> bool: ...


@dataclass(frozen=True)
class _ImportNamespaceIdentity:
    directory: str
    private_original_orm: bool = True


class _PrivateRelease:
    def __init__(
        self, registration: PrivateNamespaceDescriptor, payloads: Mapping[str, bytes], root: Path, *, v3: bool = False
    ) -> None:
        self.prefix = "_decision_hub_graph_history_" + registration.directory + ("_" + _FROZEN_DESCRIPTOR.scope_nonce if _FROZEN_DESCRIPTOR is not None else "")
        self.root = root
        self.modules: dict[str, ModuleType] = {}
        self.paths: dict[str, str] = {}
        self.payloads = dict(payloads)
        self._registration = registration
        self.is_v3 = v3
        self.v3_installation_bridge: tuple[Any, ...] | None = None
        self.shape_scope_implementation: tuple[ModuleType, tuple[Any, ...]] | None = None
        self.shared_modules = _SHARED_MODULES - (
            {"packages.kernel.decision_hub_kernel.persistence.db"}
            if registration.private_original_orm
            else set()
        )
        for relative in payloads:
            if not relative.endswith(".py") or "/context_selector_versions/" in relative:
                continue
            name = relative.removesuffix(".py").replace("/", ".")
            if name.endswith(".__init__"):
                name = name.removesuffix(".__init__")
            self.paths[name] = relative

    @property
    def registration(self) -> _Registration:
        # Import mechanics alone confer no formal registration identity.
        if not isinstance(self._registration, _Registration):
            raise _unavailable()
        return self._registration

    def _import(
        self,
        name: str,
        globals: dict[str, Any] | None = None,
        locals: dict[str, Any] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> Any:
        if level:
            package = (globals or {}).get("_history_original_package")
            if not isinstance(package, str):
                raise _unavailable()
            name = importlib.util.resolve_name("." * level + name, package)
        if name == "packages" or name.startswith("packages."):
            module = self.load(name)
            for attribute in fromlist:
                if attribute != "*" and not hasattr(module, attribute):
                    candidate = name + "." + attribute
                    if candidate in self.paths:
                        self.load(candidate)
            return module if fromlist else self.load("packages")
        if self.is_v3 and (name in {"apps", "tools"} or name.startswith(("apps.", "tools."))):
            # Repo consumers are never satisfied by a current-process fallback.
            raise _unavailable()
        if level:
            raise _unavailable()
        return builtins.__import__(name, globals, locals, fromlist, 0)

    def load(self, name: str) -> ModuleType:
        if name in self.shared_modules:
            module = (_FROZEN_DESCRIPTOR.shared_original(name) if _FROZEN_DESCRIPTOR is not None else importlib.import_module(name))
            parent, _, child = name.rpartition(".")
            setattr(self.load(parent), child, module)
            return module
        if name in self.modules:
            return self.modules[name]
        relative = self.paths.get(name)
        if relative is None and not any(key.startswith(name + ".") for key in self.paths):
            raise _unavailable()
        parent, _, child = name.rpartition(".")
        parent_module = self.load(parent) if parent else None
        if name in self.modules:
            return self.modules[name]
        alias = self.prefix + "." + name
        if alias in sys.modules:
            raise _unavailable()
        module = ModuleType(alias)
        self.modules[name] = module
        sys.modules[alias] = module
        if parent_module is not None:
            setattr(parent_module, child, module)
        is_package = relative is None or relative.endswith("/__init__.py")
        package = name if is_package else parent
        module.__package__ = self.prefix + "." + package
        module.__dict__["_history_original_package"] = package
        if (_FROZEN_DESCRIPTOR is not None and _FROZEN_DESCRIPTOR.mode == "current-installation"
                and name == "packages.kernel.decision_hub_kernel.application.research_graph_recipe_v3"):
            module.__dict__["_FROZEN_PUBLICATION_CURRENT"] = _FROZEN_DESCRIPTOR
        module.__dict__["__builtins__"] = {**vars(builtins), "__import__": self._import}
        if is_package:
            module.__path__ = []
        if relative is not None:
            module.__file__ = str(self.root / relative)
            exec(compile(self.payloads[relative], module.__file__, "exec"), module.__dict__)
            if relative == (
                "packages/contracts_py/decision_hub_contracts/binding_contract_base.py"
            ) and _hash(self.payloads[relative]) == (
                "eb2013b8fe90cd6495b64557a00a211c6410cd20c3bd75d68ded0c7843ef40f3"
            ):
                # Capture the original private namespace immediately after exec,
                # before callers can replace its scope, helpers or memo limits.
                self.shape_scope_implementation = (
                    module,
                    tuple(
                        (key, value, getattr(value, "__wrapped__", value),
                         getattr(value, "__code__", None),
                         getattr(getattr(value, "__wrapped__", value), "__code__", None))
                        for key, value in vars(module).items() if not key.startswith("__")
                    ),
                )
        if name in _POLICY_LOGICAL_MODULE_NAMES:
            # Classes/functions were defined with the private alias __module__.
            # The original checker hashes module names as well as source bytes.
            module.__name__ = name
        return module

    def discard(self) -> None:
        for name, module in self.modules.items():
            alias = self.prefix + "." + name
            if sys.modules.get(alias) is module:
                sys.modules.pop(alias)
        self.modules.clear()


@contextmanager
def original_publication_namespace(
    directory: str, payloads: Mapping[str, bytes], root: Path,
) -> Generator[Callable[[str], ModuleType], None, None]:
    """Import-only lexical bridge; the caller authenticates its DEV originals."""
    with _LOCK:
        methods = (_PrivateRelease.__init__, _PrivateRelease.load, _PrivateRelease._import, _PrivateRelease.discard)
        implementation = tuple((method, method.__code__) for method in methods)
        private = _PrivateRelease(_ImportNamespaceIdentity(directory), payloads, root)
        try:
            yield private.load
        finally:
            private.discard()
            if any(name.startswith(private.prefix + ".") for name in sys.modules):
                raise ValueError("development_history_read_cleanup_failed")
            current = (_PrivateRelease.__init__, _PrivateRelease.load, _PrivateRelease._import, _PrivateRelease.discard)
            if implementation != tuple((method, method.__code__) for method in current):
                raise ValueError("development_history_read_boundary_changed")


@contextmanager
def original_publication_read_scope(session: Session) -> Generator[None]:
    """Reuse the existing physical SELECT/no-flush guard without release identity."""
    with _read_only(session):
        yield


def original_publication_result(artifact_id: str, gate_json: str) -> RetainedPublicationRead:
    """Reuse the existing serialized publication result boundary, not authority."""
    _publication_gate_body(gate_json)
    return RetainedPublicationRead(artifact_id, gate_json)


_LOADED: dict[str, _PrivateRelease] = {}


def _loaded_release(
    registration: _Registration, payloads: Mapping[str, bytes], root: Path, *, v3: bool = False
) -> _PrivateRelease:
    with _LOCK:
        if registration.directory not in _LOADED:
            private = _PrivateRelease(registration, payloads, root, v3=v3)
            try:
                private.load(_BINDING)
                private.load(_SELECTOR)
            except BaseException:
                private.discard()
                raise
            _LOADED[registration.directory] = private
    return _LOADED[registration.directory]


def _configure_private_v3_installation(private: _PrivateRelease) -> None:
    """Storage bridge for original V3 installation, only in a protected read.

    The original reader still validates Task/Pack/selection/dispatch clocks.
    Its physical installation verifier is replaced by this explicit archive
    verifier because the unchanged receipt names the original repository root.
    No writer, current receipt, pointer creation or execution grant is exposed.
    """
    module = private.load(_RECIPE_V3)
    if private.v3_installation_bridge is not None:
        expected, code, original = private.v3_installation_bridge
        if module._installation is not expected or expected.__code__ is not code:
            raise ValueError("research_history_v3_installation_bridge_changed")
        return
    original = module._installation
    manifest_builder = module.graph_recipe_manifest_v3
    manifest_code = manifest_builder.__code__

    def installation_description(
        index: Mapping[str, Any], payloads: Mapping[str, bytes], root: Path,
    ) -> str:
        if (private.root != root or private.payloads != payloads
                or private.modules.get(_RECIPE_V3) is not module
                or module._LOADED_SOURCE_SHA != _hash(payloads[module.SOURCE_REF])
                or module._installation is not original_installation
                or original_installation.__code__ is not installation_code
                or module.graph_recipe_manifest_v3 is not manifest_builder
                or manifest_builder.__code__ is not manifest_code):
            raise ValueError("research_history_v3_loaded_source_changed")
        from .research_recipe_archive import _validate_retained_v3_installation
        receipt, _pointer = _validate_retained_v3_installation(
            index, payloads, expected_root=_CURRENT_ROOT,
        )
        manifest = manifest_builder()
        if manifest.model_dump(mode="json") != index["graph_recipe_manifest"]:
            raise ValueError("research_history_v3_manifest_changed")
        binding = index["installation_binding"]
        return _canonical({
            "receipt": binding["receipt"], "pointer": binding["pointer"],
            "dependency_hash": index["graph_recipe_dependency_hash"],
            "plugin_build_hash": receipt["plugin_build_hash"],
            "manifest": manifest.model_dump(mode="json"),
        })

    def original_installation() -> Any:
        scope = _V3_READ_SCOPE.get()
        if scope is None or scope[0] is not private:
            raise PermissionError("research_history_v3_read_scope_required")
        _, session, run_id, transaction, nested, connection, native, changes = scope
        if (session.get_transaction() is not transaction
                or session.get_nested_transaction() is not nested
                or session.connection() is not connection
                or connection.connection.driver_connection is not native
                or not native.in_transaction or native.total_changes != changes
                or session.new or session.dirty or session.deleted
                or _original_recipe_registration(session, run_id) != private.registration
                or private.modules.get(_RECIPE_V3) is not module
                or module._installation is not original_installation
                or original_installation.__code__ is not installation_code
                or module.graph_recipe_manifest_v3 is not manifest_builder
                or manifest_builder.__code__ is not manifest_code):
            raise ValueError("research_history_v3_read_boundary_changed")
        index, payloads, root = _verified_release(private.registration)
        if (private.root != root or private.payloads != payloads
                or module._LOADED_SOURCE_SHA != _hash(payloads[module.SOURCE_REF])):
            raise ValueError("research_history_v3_loaded_source_changed")
        verified = _VERIFIED_RELEASE_READ.get()
        if (verified is not None and verified.marker is not None
                and verified.boundary_matches(private.registration)):
            saved = verified.installations.get(module)
            if saved is None:
                body = installation_description(index, payloads, root)
                saved = _RetainedInstallationRead(body, installation_description)
                verified.installations[module] = saved
            return _installation_from_description(module, saved.body)
        return _installation_from_description(
            module, installation_description(index, payloads, root),
        )

    installation_code = original_installation.__code__
    module._installation = original_installation
    private.v3_installation_bridge = (original_installation, original_installation.__code__, original)


@contextmanager
def _private_shape_read_scope(
    private: _PrivateRelease, *, enabled: bool
) -> Generator[None]:
    """Enter only the exact original's bounded pure shape-success scope."""
    path = "packages/contracts_py/decision_hub_contracts/binding_contract_base.py"
    payload = private.payloads.get(path)
    if not enabled or payload is None or _hash(payload) != (
        "eb2013b8fe90cd6495b64557a00a211c6410cd20c3bd75d68ded0c7843ef40f3"
    ):
        yield
        return
    logical = path.removesuffix(".py").replace("/", ".")
    module = private.load(logical)
    saved = private.shape_scope_implementation

    def require_original() -> None:
        if saved is None or saved[0] is not module or private.modules.get(logical) is not module:
            raise ValueError("research_history_original_shape_scope_changed")
        for name, value, wrapped, code, wrapped_code in saved[1]:
            actual = getattr(module, name, None)
            effective = getattr(actual, "__wrapped__", actual)
            if (actual is not value or effective is not wrapped
                    or getattr(actual, "__code__", None) is not code
                    or getattr(effective, "__code__", None) is not wrapped_code):
                raise ValueError("research_history_original_shape_scope_changed")
        scope = module.shape_validation_scope
        if (scope.__closure__ is None or len(scope.__closure__) != 1
                or scope.__closure__[0].cell_contents is not scope.__wrapped__):
            raise ValueError("research_history_original_shape_scope_changed")

    require_original()
    try:
        with module.shape_validation_scope():
            yield
    finally:
        require_original()


@contextmanager
def _private_v3_read_scope(
    session: Session, private: _PrivateRelease, run_id: str, *,
    reuse_provider_usage: bool = False,
    reuse_shape: bool = False,
) -> Generator[None]:
    connection = session.connection()
    native = connection.connection.driver_connection
    if not isinstance(native, sqlite3.Connection) or not native.in_transaction:
        raise ValueError("research_history_v3_physical_snapshot_required")
    _configure_private_v3_installation(private)
    token = _V3_READ_SCOPE.set((private, session, run_id, session.get_transaction(),
                              session.get_nested_transaction(), connection, native, native.total_changes))
    try:
        # The current namespace's ContextVar does not enter a retained module.
        # Only this exact original supplies the separately verified read scope.
        # Older originals keep their full uncached reader and original gates.
        usage_path = "packages/kernel/decision_hub_kernel/application/research_provider_usage.py"
        usage_payload = private.payloads.get(usage_path)
        with _private_shape_read_scope(private, enabled=reuse_provider_usage or reuse_shape):
            if reuse_provider_usage and usage_payload is not None and _hash(usage_payload) in (
                "96e3eb95d201785d3e0b839f60b60c7ae008bef8855b0392e95679a8412e34bd",
                "96e3e5e88ff68da7fcbf71317b384da9bfcd711a2b53560328052a3f45b056bd",
            ):
                usage = private.load(usage_path.removesuffix(".py").replace("/", "."))
                with usage.provider_usage_read_scope(session):
                    yield
            else:
                yield
    finally:
        _V3_READ_SCOPE.reset(token)


@contextmanager
def publication_retained_source_snapshot_read_scope(
    session: Session, *, run_id: str, collector: Any, current_source_lease: Any,
    expected_snapshot: Any = None, checkpoint: Callable[[], None] = lambda: None,
) -> Generator[Any]:
    """A separate original private V2 byte lease; no current permit is shared.

    Only an already verified same-read retained installation is eligible.
    Other original readers stay strict; a declared private V2 never falls back.
    """
    if _active_frozen_history_owner80() is not None:
        raise ValueError("publication_frozen_shared_retained_factory_forbidden")
    current_source_lease.require_read(session, run_id)
    if collector.session is not session:
        raise ValueError("research_history_publication_source_collector_changed")
    collector._require_boundary()
    registration = _original_recipe_registration(session, run_id)
    verified = _VERIFIED_RELEASE_READ.get()
    if (registration is None or verified is None
            or verified.session is not session or verified.run_id != run_id
            or not verified.matches(registration)):
        if expected_snapshot is not None:
            raise ValueError("research_history_publication_source_scope_unavailable")
        yield None
        return
    index, payloads, root = _verified_release(registration)
    source_scope_ref = "packages/kernel/decision_hub_kernel/persistence/research_source_snapshot.py"
    source_port_ref = "packages/kernel/decision_hub_kernel/ports/research_source_snapshot.py"
    binding_ref = _BINDING.replace(".", "/") + ".py"
    abi_sources = {
        source_scope_ref: "63dfed4b79a70b476ff2c4b157095f620e15e7a10e85d94fe402e3a4866fc2be",
        source_port_ref: "4aafac922c5a0faadce1073976c9b424eb28dba6c03712b0940ecba76eb30049",
        binding_ref: "b9067d636a87afe6f9872475756aec6436ad8e3e960b198879e560ad6928ed4d",
    }
    supported = (index["graph_recipe_manifest"]["schema_version"] == "research-graph-recipe-manifest.v3"
        and set(index["stored_files"]) == set(index["all_files"])
        and all(index["all_files"].get(ref) == digest
            and ref in payloads and _hash(payloads[ref]) == digest
            for ref, digest in abi_sources.items()))
    if not supported:
        if expected_snapshot is not None:
            raise ValueError("research_history_publication_source_abi_unsupported")
        yield None
        return
    private = _loaded_release(registration, payloads, root, v3=True)
    with _private_v3_read_scope(session, private, run_id):
        factory = private.load(source_scope_ref.removesuffix(".py").replace("/", "."))
        port = private.load(source_port_ref.removesuffix(".py").replace("/", "."))
        binding = private.load(_BINDING)
        if (factory.SOURCE_SNAPSHOT_READ_ABI != "publication-source-snapshot-read.v2"
                or port.SOURCE_SNAPSHOT_READ_ABI != factory.SOURCE_SNAPSHOT_READ_ABI
                or private.root != root or private.payloads != payloads):
            raise ValueError("research_history_publication_source_original_changed")
        declared = tuple(sorted(index["all_files"].items()))
        with factory.publication_source_snapshot_read_scope(
            session, run_id=run_id, guard_type=binding._ReadSourceGuard,
            collector=collector, declared_sources=declared,
            expected_snapshot=expected_snapshot, checkpoint=checkpoint,
        ) as private_source_lease:
            private_source_lease.require_read(session, run_id)
            private_source_lease.require_reader_source(binding.__file__)
            if (private_source_lease.root != root
                    or tuple((file.relative_ref, file.sha256) for file in private_source_lease.snapshot.files) != declared):
                raise ValueError("research_history_publication_source_set_changed")
            with _private_v3_read_scope(
                    session, private, run_id, reuse_shape=True), binding.publication_final_read_scope(session):
                yield private_source_lease
                private_source_lease.require_read(session, run_id)
                current_source_lease.require_read(session, run_id)
        if (_original_recipe_registration(session, run_id) != registration
                or _LOADED.get(registration.directory) is not private
                or private.root != root or private.payloads != payloads
                or not verified.matches(registration)):
            raise ValueError("research_history_publication_source_original_changed")
    current_source_lease.require_read(session, run_id)


@contextmanager
def _read_only(session: Session) -> Generator[None]:
    """Borrow one physical read snapshot, beginning it only when absent.

    A clean SQLAlchemy implicit transaction may lack SQLite's real BEGIN.
    The caller keeps ownership: this helper never commits or rolls it back.
    """
    if session.new or session.dirty or session.deleted:
        raise ValueError("research_history_dirty_session")
    connection = session.connection()
    native = connection.connection.driver_connection
    if not isinstance(native, sqlite3.Connection):
        raise ValueError("research_history_sqlite_required")
    transaction, nested = session.get_transaction(), session.get_nested_transaction()
    if not native.in_transaction:
        if nested is not None:
            raise ValueError("research_history_read_boundary_changed")
        connection.exec_driver_sql("BEGIN")
    if not native.in_transaction or transaction is None:
        raise ValueError("research_history_physical_snapshot_required")
    changes = native.total_changes

    def require_select(
        _connection: Any,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        if re.match(r"\s*SELECT\b", statement, flags=re.IGNORECASE) is None:
            raise ValueError("research_history_write_forbidden")

    def no_write(*_args: Any) -> None:
        raise ValueError("research_history_write_forbidden")

    event.listen(connection, "before_cursor_execute", require_select)
    event.listen(session, "before_flush", no_write)
    event.listen(session, "before_commit", no_write)
    try:
        with session.no_autoflush:
            yield
        if (
            not native.in_transaction
            or session.get_transaction() is not transaction
            or session.get_nested_transaction() is not nested
            or session.connection() is not connection
            or connection.connection.driver_connection is not native
            or native.total_changes != changes
            or session.new
            or session.dirty
            or session.deleted
        ):
            raise ValueError("research_history_read_boundary_changed")
    finally:
        event.remove(session, "before_commit", no_write)
        event.remove(session, "before_flush", no_write)
        event.remove(connection, "before_cursor_execute", require_select)


def _original_recipe_registration(session: Session, run_id: str) -> _Registration | None:
    # Native SQL returns all seven original SQLite fields without ORM datetime
    # conversion or refresh of pending caller changes. No write or new snapshot.
    rows = session.connection().exec_driver_sql(
        "SELECT output_id,run_id,role,schema_version,payload_json,payload_hash,created_at "
        "FROM run_role_outputs WHERE run_id=? AND role='graph_recipe_selection' LIMIT 2",
        (run_id,),
    ).all()
    if not rows:
        return None
    if len(rows) != 1:
        raise ValueError("research_history_recipe_ambiguous")
    output_id, saved_run, role, schema, encoded, expected, created = rows[0]
    versions = {
        "research-graph-recipe-selection.v2": (
            "research-graph-projection.v2", "research-graph-recipe-manifest.v2",
        ),
        "research-graph-recipe-selection.v3": (
            "research-graph-projection.v3", "research-graph-recipe-manifest.v3",
        ),
    }
    if (any(type(item) is not str for item in rows[0]) or schema not in versions
            or _SHA.fullmatch(expected) is None):
        raise ValueError("research_history_recipe_original_changed")
    try:
        raw = encoded.encode("utf-8")
        value = json.loads(encoded)
    except (TypeError, ValueError):
        raise ValueError("research_history_recipe_original_changed") from None
    if (len(raw) > 1024 * 1024 or type(value) is not dict
            or _hash(raw) != expected or _canonical(value).encode() != raw
            or value.get("schema_version") != schema or value.get("run_id") != saved_run
            or saved_run != run_id or role != "graph_recipe_selection"
            or value.get("recipe_version") != versions[schema][0]
            or output_id != "graph_recipe_selection_" + expected
            or type(value.get("manifest")) is not dict
            or value["manifest"].get("schema_version") != versions[schema][1]
            or value.get("dependency_hash") != _hash(_canonical(value["manifest"]).encode())):
        raise ValueError("research_history_recipe_original_changed")
    candidates = tuple(item for item in _active_registrations()
                       if value["dependency_hash"] == item.graph_hash)
    if schema == "research-graph-recipe-selection.v2":
        candidates = tuple(item for item in candidates
                           if item in _REGISTRATIONS or item.directory == item.graph_hash)
        if len(candidates) > 1:
            raise ValueError("research_history_recipe_ambiguous")
        return candidates[0] if candidates else None
    receipt, pointer = value.get("installation_receipt"), value.get("installed_pointer")
    _v3_installation_directory(value["dependency_hash"], receipt, pointer)
    matched = []
    for registration in candidates:
        name = "packages/kernel/research_recipe_versions/" + registration.directory + "/release.json"
        index_raw = _managed_read(name)
        if (len(index_raw) > 2 * 1024 * 1024 or _hash(index_raw) != registration.index_hash
                or (registration not in _REGISTRATIONS and (
                    _REGISTRY_BINDING is None
                    or _REGISTRY_BINDING.source_hashes.get(name) != registration.index_hash))):
            raise _unavailable()
        index = json.loads(index_raw)
        if (type(index) is not dict or index.get("schema_version") not in {
                "research-retained-recipe-release.v2", "research-retained-recipe-release.v3",
            }
                or (index["schema_version"] == "research-retained-recipe-release.v3"
                    and (_canonical(index) + "\n").encode() != index_raw)
                or index.get("graph_recipe_dependency_hash") != registration.graph_hash
                or index.get("graph_recipe_manifest") != value["manifest"]):
            raise _unavailable()
        binding = index.get("installation_binding")
        if type(binding) is not dict:
            raise _unavailable()
        if registration.directory != registration.graph_hash and registration.directory != _v3_installation_directory(
            registration.graph_hash, binding.get("receipt"), binding.get("pointer"),
        ):
            raise _unavailable()
        if binding.get("receipt") == receipt and binding.get("pointer") == pointer:
            matched.append(registration)
    if len(matched) > 1:
        raise ValueError("research_history_recipe_ambiguous")
    # An unregistered current exact installation keeps its existing strict
    # current reader. A registered archive never stands in for another install.
    return matched[0] if matched else None


def read_retained_skill_method_in_session(
    session: Session, *, run_id: str, service: Any,
) -> dict[str, Any] | None:
    """Exact Run method originals, including failed/cancelled research.

    This is an independent read port, not the publication replay gate. It
    confers neither terminal publication authority nor old execution rights.
    Only plain JSON leaves the original private namespace.
    """
    with _read_only(session):
        registration = _original_recipe_registration(session, run_id)
        if registration is None:
            return None
        index, payloads, root = _verified_release(registration)
        if index["graph_recipe_manifest"]["schema_version"] != "research-graph-recipe-manifest.v3":
            raise ValueError("skill_history_original_protocol_unavailable")
        with _verified_release_read_scope(session, run_id, registration, (index, payloads, root)):
            private = _loaded_release(registration, payloads, root, v3=True)
            with _private_v3_read_scope(session, private, run_id, reuse_shape=True):
                recipe = private.load(_RECIPE_V3)
                original = recipe.read_graph_recipe_selection_v3_in_session(session, run_id)
                if (original is None
                        or original.receipt.manifest.model_dump(mode="json") != index["graph_recipe_manifest"]
                        or original.receipt.dependency_hash != registration.graph_hash):
                    raise ValueError("research_history_recipe_replay_changed")
                from .skill_method_history import read_retained_method_originals
                result = read_retained_method_originals(
                    session, run_id=run_id, service=service, private=private, index=index,
                    payloads=payloads, root=_CURRENT_ROOT, read=_managed_read,
                )
                result = json.loads(_canonical(result))
            final_index, final_payloads, final_root = _verified_release(registration)
            if (_original_recipe_registration(session, run_id) != registration
                    or final_index != index or final_payloads != payloads or final_root != root
                    or _LOADED.get(registration.directory) is not private):
                raise ValueError("skill_history_original_changed")
        return result


def _legacy_publication_protocol_in_session(
    session: Session,
    *,
    run_id: str,
    index: Mapping[str, Any],
    payloads: Mapping[str, bytes],
    reference: Mapping[str, Any],
    declared_json: str | None,
) -> None:
    """Authenticate the sole old v1 protocol path, not an archived consumer.

    A capsule predating both Commit and the executor cannot prove either was
    executed. Its authenticated registry-only binding and closed v1 original
    may continue through the current strict protocol validator. The Graph
    still uses its registered original reader; no publication/lease is minted.
    """
    from packages.kernel.decision_hub_kernel.application.research_policy_checker import (
        _binding_originals,
    )
    from packages.kernel.decision_hub_kernel.persistence.db import (
        RunRecord,
        RunRoleOutputRecord,
        as_utc,
    )

    commit_path = _COMMIT.replace(".", "/") + ".py"
    checker_path = "packages/kernel/decision_hub_kernel/application/research_policy_checker.py"
    registry_path = "packages/kernel/decision_hub_kernel/decision/research_policy_registry.py"
    registry_raw = payloads.get(registry_path)
    if (commit_path in index["all_files"] or checker_path in index["all_files"]
            or registry_raw is None):
        raise ValueError("research_history_publication_consumer_unavailable")
    run_table, source_table = RunRecord.__table__, RunRoleOutputRecord.__table__
    run = session.execute(
        select(run_table).where(run_table.c.run_id == run_id)
    ).mappings().one_or_none()
    if (run is None or run["status"] not in {"completed", "degraded"}
            or type(run["artifact_id"]) is not str or not run["artifact_id"]
            or run["finished_at"] is None):
        raise PermissionError("research_history_publication_not_terminal")
    rows = session.execute(
        select(source_table).where(source_table.c.run_id == run_id,
            source_table.c.role == "research_publication_source").limit(2)
    ).mappings().all()
    if len(rows) != 1:
        raise ValueError("research_history_publication_original_invalid")
    source = rows[0]
    raw = source["payload_json"]
    if type(raw) is not str or len(raw.encode("utf-8")) > 2 * 1024 * 1024:
        raise ValueError("research_history_publication_original_invalid")
    body = json.loads(raw)
    required = {
        "schema_version", "run_id", "event_id", "graph_final_ref",
        "graph_result_hash", "declared_result_hash", "result_schema_version",
        "result_payload_hash", "artifact_id", "artifact_hash", "review_binding",
        "calibration_binding", "gate", "recorded_at", "result_created_at",
        "candidate", "candidate_gate", "publication_owner",
    }
    # Exact 18-field v1: phase/kernel fields never enter this compatibility path.
    if (type(body) is not dict or set(body) != required
            or body["schema_version"] != "research-publication-source.v1"
            or source["schema_version"] != "research-publication-source.v1"):
        raise ValueError("research_history_publication_consumer_unavailable")
    created_at, finished_at = as_utc(source["created_at"]), as_utc(run["finished_at"])
    digest = _hash(raw.encode("utf-8"))
    if (source["payload_hash"] != digest
            or source["output_id"] != "publication_" + digest[:40]
            or created_at is None or finished_at is None
            or body["recorded_at"] != created_at.isoformat()
            or body["recorded_at"] != finished_at.isoformat()
            or body["run_id"] != run_id or body["event_id"] != run["event_id"]
            or body["artifact_id"] != run["artifact_id"]
            or body["graph_final_ref"] != dict(reference)
            or declared_json is None
            or body["declared_result_hash"] != _hash(_canonical(json.loads(declared_json)).encode())):
        raise ValueError("research_history_publication_original_invalid")
    prepared, bundle, _identity, binding = _binding_originals(session, run_id)
    policy = prepared.method.definition.policy_binding
    if (binding is not None or policy.checker_activation != "prepared"
            or policy.checker_version != "research-policy-checker-registry.v1"
            or policy.checker_code_hash != bundle.compiled.compiled.code_identity.registry_source_sha256
            or policy.checker_code_hash != _hash(registry_raw)):
        raise ValueError("research_history_publication_consumer_unavailable")


def _terminal_read_original_in_session(
    session: Session, private: _PrivateRelease, run_id: str,
) -> dict[str, Any]:
    """Read the exact archived Run mapping without obtaining lease permission."""
    table = private.load(
        "packages.kernel.decision_hub_kernel.persistence.db"
    ).RunRecord.__table__
    row = session.execute(
        select(table).where(table.c.run_id == run_id)
    ).mappings().one_or_none()
    if (
        row is None
        or row["status"] not in {"completed", "degraded"}
        or type(row["artifact_id"]) is not str
        or not row["artifact_id"]
        or row["finished_at"] is None
    ):
        raise PermissionError("research_history_recovered_reader_not_terminal")
    return dict(row)


def _replay(
    session: Session,
    *,
    run_id: str,
    reference_json: str,
    kind: str,
    clock: Callable[[], datetime] | None = None,
    selector_identity: Mapping[str, object] | None = None,
    declared_json: str | None = None,
    expected_lease_owner: str | None = None,
    candidate_json: str | None = None,
    gate_json: str | None = None,
) -> RetainedGraphRead | RetainedPublicationRead | RetainedRecipeRead | str | None:
    owner = _active_frozen_history_owner80()
    if owner is not None:
        return owner.replay(session, run_id=run_id, reference_json=reference_json, kind=kind,
            clock=clock, selector_identity=selector_identity, declared_json=declared_json,
            expected_lease_owner=expected_lease_owner, candidate_json=candidate_json, gate_json=gate_json)
    # Dispatch is not a new gate for the current reader or its caller writer.
    # Use raw columns to avoid refreshing/erasing pending ORM modifications.
    with session.no_autoflush:
        registration = _original_recipe_registration(session, run_id)
    if registration is None:
        return None
    with _read_only(session):
        # A supplied owner compares the original saved publication owner only.
        # Terminal publication replay never renews current execution permission.
        if _original_recipe_registration(session, run_id) != registration:
            raise ValueError("research_history_recipe_original_changed")
        index, payloads, root = _verified_release(registration)
        if kind == "selection" and dict(selector_identity or {}) != index["selector_identity"]:
            return None
        private = _loaded_release(registration, payloads, root, v3=index["graph_recipe_manifest"]["schema_version"] == "research-graph-recipe-manifest.v3")
        terminal_original = (
            _terminal_read_original_in_session(session, private, run_id)
            if registration.graph_hash in _TERMINAL_READ_RECIPES or index["graph_recipe_manifest"]["schema_version"] == "research-graph-recipe-manifest.v3" else None
        )
        is_v3 = index["graph_recipe_manifest"]["schema_version"] == "research-graph-recipe-manifest.v3"
        scope = (
            _private_v3_read_scope(
                session, private, run_id, reuse_shape=kind in {"round", "final", "publication"},
            )
            if is_v3 else nullcontext()
        )
        from .perpetual_retained_resource import perpetual_method_resource_read_scope
        with scope, perpetual_method_resource_read_scope(
            sys.modules[__name__], session, private, run_id, reference_json, kind
        ):
            # Execute the original manifest builder/reader, not a registered hash
            # substituted for an actual dependency closure authentication.
            v3 = index["graph_recipe_manifest"]["schema_version"] == "research-graph-recipe-manifest.v3"
            recipe = private.load(_RECIPE_V3 if v3 else _RECIPE)
            original_recipe = (recipe.read_graph_recipe_selection_v3_in_session(session, run_id) if v3
                               else recipe.read_graph_recipe_selection_in_session(session, run_id))
            if (
                original_recipe is None
                or original_recipe.receipt.manifest.model_dump(mode="json")
                != index["graph_recipe_manifest"]
                or original_recipe.receipt.dependency_hash != registration.graph_hash
            ):
                raise ValueError("research_history_recipe_replay_changed")
            if kind == "recipe_v3":
                if not v3:
                    return None
                _verified_release(registration)
                if (_original_recipe_registration(session, run_id) != registration
                        or _terminal_read_original_in_session(session, private, run_id) != terminal_original):
                    raise ValueError("research_history_v3_original_changed")
                return RetainedRecipeRead(
                    recipe.observation_json(original_recipe.reference.model_dump(mode="json")),
                    recipe.observation_json(original_recipe.receipt.model_dump(mode="json")),
                )
            reference = json.loads(reference_json)
            output_id = reference.get("output_id", reference.get("row", {}).get("output_id"))
            if type(output_id) is not str:
                raise ValueError("research_history_reference_invalid")
            key = (id(session), run_id, kind, output_id)
            stack = _READ_STACK.get()
            if key in stack:
                raise ValueError("research_history_original_dependency_cycle")
            token = _READ_STACK.set((*stack, key))
            try:
                publication_run = None
                if kind == "publication":
                    if reference.get("row", {}).get("schema_version") == "research-perpetual-graph-final.v1" and any(
                        name not in private.paths for name in (
                            _COMMIT,
                            "packages.kernel.decision_hub_kernel.application.perpetual_research_report",
                        )
                    ):
                        # V5 needs its complete original semantic consumer. The
                        # legacy storage-only fallback is not a perpetual reader.
                        raise ValueError("research_history_perpetual_publication_reader_missing")
                    if _COMMIT not in private.paths:
                        _legacy_publication_protocol_in_session(
                            session, run_id=run_id, index=index, payloads=payloads,
                            reference=reference, declared_json=declared_json,
                        )
                        _verified_release(registration)
                        if _original_recipe_registration(session, run_id) != registration:
                            raise ValueError("research_history_publication_original_changed")
                        if terminal_original is not None and (
                            _terminal_read_original_in_session(session, private, run_id)
                            != terminal_original
                        ):
                            raise ValueError("research_history_recovered_run_changed")
                        # None means current legacy protocol validation, never a
                        # claimed execution of an absent original Commit/checker.
                        return None
                    module = private.load(_COMMIT)
                    run_table = module.RunRecord.__table__
                    row = session.execute(
                        select(run_table).where(run_table.c.run_id == run_id)
                    ).mappings().one_or_none()
                    publication_run = dict(row) if row is not None else None
                    if (
                        publication_run is None
                        or publication_run["status"] not in {"completed", "degraded"}
                        or type(publication_run["artifact_id"]) is not str
                        or not publication_run["artifact_id"]
                        or publication_run["finished_at"] is None
                    ):
                        raise PermissionError("research_history_publication_not_terminal")
                    if declared_json is None:
                        raise ValueError("research_history_publication_declared_invalid")
                    binding = private.load(_BINDING)
                    run = session.get(module.RunRecord, run_id, populate_existing=True)
                    if run is None:
                        raise ValueError("research_history_publication_run_missing")
                    candidate = None if candidate_json is None else json.loads(candidate_json)
                    if candidate is not None and not isinstance(candidate, dict):
                        raise ValueError("research_history_publication_candidate_invalid")
                    supplied_gate = None
                    if gate_json is not None:
                        supplied = _publication_gate_body(gate_json)
                        supplied_gate = module.GateResult(
                            status=module.GateStatus(supplied["status"]),
                            decisions=supplied["decisions"],
                        )
                    artifact_id, original_gate = module._read_publication_source_in_session(
                        session,
                        run=run,
                        reference=binding.GraphFinalProjectionRef.model_validate_json(reference_json),
                        declared=module.ResearchSessionResult.model_validate_json(declared_json),
                        expected_lease_owner=expected_lease_owner,
                        candidate=candidate,
                        gate=supplied_gate,
                    )
                    if artifact_id != publication_run["artifact_id"]:
                        raise ValueError("research_history_publication_artifact_changed")
                    original_gate_json = _canonical({
                        "status": original_gate.status.value,
                        "decisions": original_gate.decisions,
                    })
                    _publication_gate_body(original_gate_json)
                    result = RetainedPublicationRead(artifact_id, original_gate_json)
                elif kind == "selection":
                    module = private.load(_SELECTOR)
                    service = module.ContextSelectionService.__new__(module.ContextSelectionService)
                    service.clock = clock
                    result = service.read_in_session(
                        session,
                        run_id=run_id,
                        reference=module.ContextSelectionReference.model_validate_json(reference_json),
                        require_current_permission=False,
                        expected_lease_owner=None,
                    ).payload_json
                else:
                    module = private.load(_BINDING)
                    reader = (
                        module.read_graph_round_in_session
                        if kind == "round"
                        else module.read_graph_final_in_session
                    )
                    model = (
                        module.GraphRoundAdmissionRef
                        if kind == "round"
                        else module.GraphFinalProjectionRef
                    )
                    frozen = reader(
                        session,
                        run_id=run_id,
                        reference=model.model_validate_json(reference_json),
                        expected_lease_owner=None,
                        require_current_permission=False,
                    )
                    projection = (
                        frozen.state if kind == "round" else frozen.result.model_dump(mode="json")
                    )
                    result = RetainedGraphRead(
                        module.observation_json(frozen.receipt.model_dump(mode="json")),
                        module.observation_json(projection),
                    )
                _verified_release(registration)
                if terminal_original is not None and (
                    _terminal_read_original_in_session(session, private, run_id)
                    != terminal_original
                ):
                    raise ValueError("research_history_recovered_run_changed")
                if kind == "publication":
                    row = session.execute(
                        select(run_table).where(run_table.c.run_id == run_id)
                    ).mappings().one_or_none()
                    if (
                        row is None
                        or dict(row) != publication_run
                        or _original_recipe_registration(session, run_id) != registration
                    ):
                        raise ValueError("research_history_publication_original_changed")
                return result
            finally:
                _READ_STACK.reset(token)



@dataclass(frozen=True)
class _RetainedOriginalIdentity:
    registration: _Registration
    private: _PrivateRelease
    module: ModuleType
    signatures: tuple[bytes, ...]
    ancestry: tuple[tuple[str, int], ...]
    targets: tuple[tuple[Any, str, Any, Any], ...]
    modules: tuple[tuple[str, ModuleType, str | None], ...]

    def matches(self, session: Session, run_id: str) -> bool:
        _require_retained_original_identity_implementation()
        if (_original_recipe_registration(session, run_id) != self.registration
                or _LOADED.get(self.registration.directory) is not self.private
                or self.private.modules.get(_BINDING) is not self.module):
            return False
        _, payloads, _ = _verified_release(self.registration)
        if self.private.payloads != payloads:
            return False
        for logical, module, file in self.modules:
            if self.private.modules.get(logical) is not module or getattr(module, '__file__', None) != file:
                return False
        for owner, name, function, code in self.targets:
            current = getattr(owner, name, None)
            effective = getattr(current, '__wrapped__', current)
            if current is not function or getattr(effective, '__code__', None) is not code:
                return False
        return (self.module._original_contract_signatures() == self.signatures
                and self.module._original_ancestry() == self.ancestry)


def capture_retained_original_identity(session: Session, run_id: str) -> tuple[bool, _RetainedOriginalIdentity | None]:
    """Only an already authenticated private reader's live identity; never load one."""
    _require_retained_original_identity_implementation()
    if _active_frozen_history_owner80() is not None:
        raise ValueError("publication_frozen_shared_identity_writer_unsupported")
    registration = _original_recipe_registration(session, run_id)
    if registration is None:
        return False, None
    with _LOCK:
        private = _LOADED.get(registration.directory)
        module = private.modules.get(_BINDING) if private is not None else None
        if module is None:
            return True, None
        _verified_release(registration)
        signature_function = getattr(module, '_original_contract_signatures', None)
        ancestry_function = getattr(module, '_original_ancestry', None)
        if not callable(signature_function) or not callable(ancestry_function):
            return True, None
        signatures = signature_function()
        if signatures is None:
            return True, None
        # Capture the actual functions, validators and formatters in this private
        # namespace. Wrappers are kept by identity with their real wrapped code.
        targets = []
        modules = []
        for logical, owner in tuple(private.modules.items()):
            if logical not in {_BINDING,
                    'packages.contracts_py.decision_hub_contracts.binding_contract_base',
                    'packages.contracts_py.decision_hub_contracts.policy_contract_base'}:
                continue
            modules.append((logical, owner, getattr(owner, '__file__', None)))
            for name, function in tuple(vars(owner).items()):
                wrapped = getattr(function, '__wrapped__', function)
                code = getattr(wrapped, '__code__', None)
                if code is not None:
                    targets.append((owner, name, function, code))
        value = _RetainedOriginalIdentity(registration, private, module, signatures,
                                         ancestry_function(), tuple(targets), tuple(modules))
        return (True, value) if value.matches(session, run_id) else (True, None)



_RETAINED_ORIGINAL_IDENTITY_IMPLEMENTATION = (
    capture_retained_original_identity, capture_retained_original_identity.__code__,
    _RetainedOriginalIdentity, _RetainedOriginalIdentity.matches, _RetainedOriginalIdentity.matches.__code__,
)


def _require_retained_original_identity_implementation() -> None:
    factory, factory_code, identity_type, matcher, matcher_code = _RETAINED_ORIGINAL_IDENTITY_IMPLEMENTATION
    if (capture_retained_original_identity is not factory
            or capture_retained_original_identity.__code__ is not factory_code
            or _RetainedOriginalIdentity is not identity_type
            or _RetainedOriginalIdentity.matches is not matcher
            or _RetainedOriginalIdentity.matches.__code__ is not matcher_code):
        raise ValueError('research_history_original_identity_implementation_changed')


def replay_retained_context_selection_in_session(
    session: Session,
    *,
    selector_identity: Mapping[str, object],
    run_id: str,
    reference_json: str,
    clock: Callable[[], datetime],
) -> str | None:
    value = _replay(
        session,
        run_id=run_id,
        reference_json=reference_json,
        kind="selection",
        clock=clock,
        selector_identity=selector_identity,
    )
    if value is not None and not isinstance(value, str):
        raise ValueError("research_history_selection_replay_type_invalid")
    return value


def replay_retained_graph_round_in_session(
    session: Session,
    *,
    run_id: str,
    reference_json: str,
) -> RetainedGraphRead | None:
    value = _replay(session, run_id=run_id, reference_json=reference_json, kind="round")
    if value is not None and not isinstance(value, RetainedGraphRead):
        raise ValueError("research_history_graph_replay_type_invalid")
    return value


def replay_retained_graph_final_in_session(
    session: Session,
    *,
    run_id: str,
    reference_json: str,
) -> RetainedGraphRead | None:
    value = _replay(session, run_id=run_id, reference_json=reference_json, kind="final")
    if value is not None and not isinstance(value, RetainedGraphRead):
        raise ValueError("research_history_graph_replay_type_invalid")
    return value


def _publication_gate_body(payload_json: str) -> dict[str, Any]:
    value = json.loads(payload_json)
    if (
        not isinstance(value, dict)
        or set(value) != {"status", "decisions"}
        or type(value["status"]) is not str
        or not isinstance(value["decisions"], list)
        or any(
            not isinstance(decision, dict)
            or any(type(key) is not str or type(item) is not str for key, item in decision.items())
            for decision in value["decisions"]
        )
    ):
        raise ValueError("research_history_publication_gate_invalid")
    return value


def replay_retained_publication_in_session(
    session: Session,
    *,
    run_id: str,
    reference_json: str,
    declared_json: str,
    expected_lease_owner: str | None,
    candidate_json: str | None = None,
    gate_json: str | None = None,
) -> RetainedPublicationRead | None:
    """Run a registered original publication consumer, never its writer.

    None preserves the strict current reader for an unregistered recipe or
    the explicitly authenticated old v1 protocol without a consumer capsule.
    This latter path does not claim an original Commit/checker was executed.
    A registered recipe is usable only for a completed/degraded Run with its
    original Artifact. A supplied owner is checked against the original saved
    publication owner; it is not a renewed lease or permission.
    """
    value = _replay(
        session,
        run_id=run_id,
        reference_json=reference_json,
        kind="publication",
        declared_json=declared_json,
        expected_lease_owner=expected_lease_owner,
        candidate_json=candidate_json,
        gate_json=gate_json,
    )
    if value is None:
        from .development_history_reader import replay_development_publication_in_session

        value = replay_development_publication_in_session(
            session, run_id=run_id, reference_json=reference_json,
            declared_json=declared_json, expected_lease_owner=expected_lease_owner,
            candidate_json=candidate_json, gate_json=gate_json,
        )
    if value is not None and not isinstance(value, RetainedPublicationRead):
        raise ValueError("research_history_publication_replay_type_invalid")
    return value


def replay_retained_graph_recipe_v3_in_session(session: Session, run_id: str) -> RetainedRecipeRead | None:
    """Serialized original selection for a registered terminal V3; no new Task."""
    value = _replay(session, run_id=run_id, reference_json="{}", kind="recipe_v3")
    if value is not None and not isinstance(value, RetainedRecipeRead):
        raise ValueError("research_history_v3_recipe_replay_type_invalid")
    return value


@contextmanager
def _retained_policy_materials(
    session: Session,
    *,
    checker: ModuleType,
    run_id: str,
    original: Any,
) -> Generator[Any]:
    """Original material readers, borrowing our fresh full Graph in this snapshot.

    This is the storage-only equivalent of the pinned _policy_materials body.
    It does not patch that function or accept a caller-supplied Graph proof.
    The outer owner authenticates a full Graph, protects the SQLite boundary,
    and holds the original source guard through both phase recomputations.
    """
    checker._require_loaded_callables()
    with checker._local_original_read(session) as boundary:
        prepared, bundle, identity, binding = checker._binding_originals(session, run_id)
        round_receipt, rules, snapshots, pit, trajectory, probability = (None,) * 6
        if binding is not None:
            round_receipt = checker._read_round_receipt(
                session,
                original.receipt.last_admission_ref.row,
                run_id,
            )
            rules = bundle.compiled.compiled.compiled_rules
            if (
                tuple(rule.definition.rule_id for rule in rules)
                != checker.research_policy_registry.REGISTERED_RULE_IDS
            ):
                raise ValueError("research_policy_canonical_rule_set_invalid")
            trigger = session.get(
                checker.SnapshotRecord, bundle.request.trigger_snapshot_id, populate_existing=True
            )
            decision = session.get(
                checker.SnapshotRecord,
                original.receipt.decision_snapshot_id,
                populate_existing=True,
            )
            if (
                trigger is None
                or decision is None
                or trigger.event_id != bundle.request.event_id
                or decision.run_id != run_id
                or decision.snapshot_hash != original.receipt.decision_snapshot_hash
            ):
                raise ValueError("research_policy_snapshot_original_mismatch")
            snapshots = (
                trigger.snapshot_id,
                trigger.snapshot_hash,
                decision.snapshot_id,
                decision.snapshot_hash,
            )
            pit = checker._pit_originals(
                session, bundle, original, round_receipt, trigger, decision
            )
            trajectory = checker._trajectory_originals(
                session, bundle, prepared, original, round_receipt, identity
            )
            probability = (
                checker.research_probability_binding.read_probability_materials_in_session(
                    session, bundle
                )
            )
        if not boundary.unchanged():
            raise ValueError("research_policy_snapshot_changed")
        # The callback must remain live until authentication finishes. The
        # enclosing contextmanager below therefore owns this generator scope.
        yield checker._PolicyMaterials(
            prepared,
            bundle,
            identity,
            binding,
            original,
            round_receipt,
            rules,
            snapshots,
            pit,
            boundary.unchanged,
            trajectory,
            probability,
        )
        checker._require_loaded_callables()
        if not boundary.unchanged():
            raise ValueError("research_policy_snapshot_changed")


def _retained_publication_originals(
    session: Session,
    *,
    private: _PrivateRelease,
    run: Any,
    source: Any,
    original: Any,
    reference: Any,
    selection: Any,
    sidecar: Any,
) -> tuple[str, Any]:
    """Pinned terminal publication protocol, using the full Graph read above.

    All saved publication/result/artifact/review/decision/policy/value/forecast
    proofs are re-read. Existing original pure functions recompute the two
    policy phases. No old function is replaced and no new signature is made.
    """
    c = private.load(_COMMIT)
    checker = private.load(
        "packages.kernel.decision_hub_kernel.application.research_policy_checker"
    )
    review_module = private.load("packages.kernel.decision_hub_kernel.application.research_review")
    reviews = list(
        session.scalars(
            select(c.RunRoleOutputRecord)
            .where(
                c.RunRoleOutputRecord.run_id == run.run_id,
                c.RunRoleOutputRecord.role == "independent_reviewer",
            )
            .execution_options(populate_existing=True)
        )
    )
    if len(reviews) != 1:
        raise ValueError("research_quality_publication_review_original_missing")
    declared = original.result.model_copy(
        update={"reviews": [review_module._decode_review_row(reviews[0])]}
    )
    digest = hashlib.sha256(source.payload_json.encode("utf-8")).hexdigest()
    body = json.loads(source.payload_json)
    required = {
        "schema_version",
        "run_id",
        "event_id",
        "graph_final_ref",
        "graph_result_hash",
        "declared_result_hash",
        "result_schema_version",
        "result_payload_hash",
        "artifact_id",
        "artifact_hash",
        "review_binding",
        "calibration_binding",
        "gate",
        "recorded_at",
        "result_created_at",
        "candidate",
        "candidate_gate",
        "publication_owner",
        "base_schema_version",
        "quality_binding",
    }
    base_schema = body.get("base_schema_version") if isinstance(body, dict) else None
    if base_schema in {c.PUBLICATION_SOURCE_SCHEMA_V2, c.PUBLICATION_SOURCE_SCHEMA_V3}:
        required.add("policy_checks")
    if base_schema == c.PUBLICATION_SOURCE_SCHEMA_V3:
        required.update({"kernel_values", "kernel_forecasts"})
    recorded = c.as_utc(source.created_at)
    finished = c.as_utc(run.finished_at)
    if (
        not isinstance(body, dict)
        or set(body) != required
        or body["schema_version"] != c.PUBLICATION_SOURCE_SCHEMA_V4
        or base_schema
        not in {
            c.PUBLICATION_SOURCE_SCHEMA,
            c.PUBLICATION_SOURCE_SCHEMA_V2,
            c.PUBLICATION_SOURCE_SCHEMA_V3,
        }
        or source.schema_version != body["schema_version"]
        or source.payload_hash != digest
        or source.output_id != f"publication_{digest[:40]}"
        or recorded is None
        or finished is None
        or body["recorded_at"] != recorded.isoformat()
        or body["recorded_at"] != finished.isoformat()
        or body["run_id"] != run.run_id
        or body["event_id"] != run.event_id
        or body["artifact_id"] != run.artifact_id
        or body["graph_final_ref"] != reference.model_dump(mode="json")
        or body["declared_result_hash"] != c._canonical_hash(declared.model_dump(mode="json"))
    ):
        raise ValueError("research_publication_source_invalid")
    expected_quality = {
        "quality_sidecar_id": original.receipt.quality_sidecar_id,
        "quality_context_hash": sidecar.quality_context_hash,
        "quality_decision_hash": sidecar.quality_decision.decision_hash,
        "source_result_hash": sidecar.source_result_hash,
        "projected_result_hash": original.receipt.projected_result_hash,
        "publication_mode": sidecar.quality_decision.publication_mode,
    }
    scope_module = private.load(
        "packages.kernel.decision_hub_kernel.application.research_run_scope"
    )
    frozen = scope_module.read_run_spot_admission_in_session(session, run.run_id)
    if frozen is not None:
        admission_model = private.load(
            "packages.kernel.decision_hub_kernel.persistence.run_spot_admission"
        ).RunSpotAdmissionRecord
        admission = session.get(admission_model, run.run_id, populate_existing=True)
        if admission is None:
            raise ValueError("research_quality_publication_scope_missing")
        expected_quality.update(
            run_spot_admission_hash=admission.payload_hash,
            admission_proof_hash=frozen.admission_proof_hash,
            path_manifest_hash=frozen.path.manifest_hash,
        )
    if body["quality_binding"] != expected_quality:
        raise ValueError("research_quality_publication_binding_changed")
    if body["graph_result_hash"] != c._canonical_hash(original.result.model_dump(mode="json")):
        raise ValueError("research_publication_source_original_mismatch")
    review = c.verify_graph_review_in_session(
        session,
        run_id=run.run_id,
        original=original.result,
        declared=declared,
    )
    row = session.get(c.ResearchResultRecord, run.run_id, populate_existing=True)
    artifact = session.get(c.ArtifactRecord, run.artifact_id, populate_existing=True)
    result_created = c.as_utc(row.created_at) if row is not None else None
    if (
        row is None
        or artifact is None
        or artifact.run_id != run.run_id
        or row.schema_version != body["result_schema_version"]
        or row.payload_hash != body["result_payload_hash"]
        or row.payload_hash != c._canonical_hash(json.loads(row.payload_json))
        or result_created is None
        or body["result_created_at"] != result_created.isoformat()
        or c._canonical_hash(c._artifact_body(artifact)) != body["artifact_hash"]
        or list(review) != body["review_binding"]
    ):
        raise ValueError("research_publication_source_original_mismatch")
    persisted = c.ResearchSessionResult.model_validate_json(row.payload_json)
    candidate = body["candidate"]
    if not isinstance(candidate, dict) or type(candidate.get("research_complete")) is not bool:
        raise ValueError("research_publication_source_candidate_invalid")
    projected = c._project_research_candidate(persisted, candidate["research_complete"])
    artifact_payload = {
        key: projected[key]
        for key in (
            "facts",
            "inferences",
            "counter_thesis",
            "uncertainty",
            "transmission_chain",
            "citations",
        )
    }
    if (
        candidate != projected
        or artifact.headline != projected["headline"]
        or artifact.summary != projected["summary"]
        or json.loads(artifact.payload_json) != artifact_payload
    ):
        raise ValueError("research_publication_source_candidate_invalid")
    decisions = session.scalars(
        select(c.GateDecisionRecord)
        .where(
            c.GateDecisionRecord.artifact_id == artifact.artifact_id,
        )
        .order_by(c.GateDecisionRecord.id)
        .execution_options(populate_existing=True)
    ).all()
    gate = c.GateResult(
        status=c.GateStatus(artifact.gate_status),
        decisions=[
            {
                "rule_id": d.rule_id,
                "status": d.status,
                "reason_code": d.reason_code,
                "input_hash": d.input_hash,
            }
            for d in decisions
        ],
    )
    if body["gate"] != {"status": gate.status.value, "decisions": gate.decisions}:
        raise ValueError("research_publication_source_gate_invalid")
    publication = session.get(
        c.PublicationDecisionRecord, artifact.artifact_id, populate_existing=True
    )
    if publication is None:
        raise ValueError("research_publication_decision_missing")
    decided = c.as_utc(publication.decided_at)
    terminal = (
        c.RunStatus.completed.value
        if gate.status == c.GateStatus.publish
        else c.RunStatus.degraded.value
    )
    if (
        publication.run_id != run.run_id
        or publication.artifact_id != artifact.artifact_id
        or publication.status != gate.status.value
        or publication.result_hash != c.research_result_content_hash(persisted)
        or json.loads(publication.decisions_json) != gate.decisions
        or decided is None
        or decided != c.as_utc(artifact.created_at)
        or decided != recorded
        or decided != finished
        or run.status != terminal
    ):
        raise ValueError("research_publication_decision_original_mismatch")
    source_guard = checker._ReadSourceGuard.capture()
    if source_guard is None:
        raise ValueError("research_policy_checker_current_implementation_unavailable")
    with _retained_policy_materials(
        session, checker=checker, run_id=run.run_id, original=original
    ) as materials:
        receipts = checker._authenticate_from_materials(
            session,
            materials,
            run_id=run.run_id,
            graph_ref=reference,
            result=persisted,
            references=body.get("policy_checks"),
        )
        if receipts:
            c._verify_canonical_policy_gate(gate, receipts[-1])
            if c.as_utc(receipts[-1].assessed_at) != recorded:
                raise ValueError("research_policy_commit_time_mismatch")
        if not source_guard.matches():
            raise ValueError("research_policy_snapshot_changed")
    if base_schema == c.PUBLICATION_SOURCE_SCHEMA_V3:
        values = checker.research_probability_binding.publication_values_in_session(
            session,
            run_id=run.run_id,
            references=body["kernel_values"],
        )
        if (
            values.graph_final_ref != reference.model_dump(mode="json")
            or values.graph_result_hash != body["graph_result_hash"]
        ):
            raise ValueError("research_probability_publication_graph_invalid")
        c._verify_kernel_forecasts(
            session,
            artifact=artifact,
            result=persisted,
            gate=gate,
            values=values,
            references=body["kernel_forecasts"],
            candidate=candidate,
        )
    # selection is owned by this read, not reconstructed from sidecar/latest.
    if sidecar.recipe_selection_ref != selection.reference:
        raise ValueError("research_quality_publication_selection_mixed")
    return artifact.artifact_id, gate


def read_retained_publication_quality_in_session(
    session: Session,
    run_id: str,
    *,
    as_of: datetime,
) -> RetainedPublicationQualityRead | None:
    matched, value = consume_retained_publication_quality_in_session(
        session, run_id, as_of=as_of, consume=lambda original: original
    )
    return value if matched else None


def _quality_storage_consumer_sources_match(payloads: Mapping[str, bytes]) -> bool:
    return all(
        path in payloads and _hash(payloads[path]) in digests
        for path, digests in _QUALITY_STORAGE_CONSUMERS.items()
    )


def consume_retained_publication_quality_in_session[R](
    session: Session,
    run_id: str,
    *,
    as_of: datetime,
    consume: Callable[[RetainedPublicationQualityRead], R],
) -> tuple[bool, R | None]:
    """One fresh full original final plus all pinned terminal publication proofs.

    Only registered supported V3 originals use this storage protocol. None
    requests the existing strict reader; it never means verified/unavailable.
    The caller's clean snapshot is borrowed, never committed or rolled back.
    There is no cross-request cache and no execution or current owner grant.
    """
    _require_quality_storage_implementation()
    owner = _active_frozen_history_owner80()
    if owner is not None:
        return owner.consume_quality(session, run_id, as_of=as_of, consume=consume)
    with session.no_autoflush:
        registration = _original_recipe_registration(session, run_id)
    if registration is None:
        return False, None
    with _read_only(session):
        release_scope = _VERIFIED_RELEASE_READ.get()
        index, payloads, root = (
            _verified_release(registration)
            if (release_scope is not None and release_scope.session is session
                and release_scope.run_id == run_id)
            else _verified_release_uncached(registration)
        )
        if index["graph_recipe_manifest"]["schema_version"] != "research-graph-recipe-manifest.v3":
            return False, None
        if not _quality_storage_consumer_sources_match(payloads):
            return False, None
        with _verified_release_read_scope(session, run_id, registration, (index, payloads, root)):
            private = _loaded_release(registration, payloads, root, v3=True)
            terminal = _terminal_read_original_in_session(session, private, run_id)
            with _private_v3_read_scope(session, private, run_id, reuse_provider_usage=True):
                c = private.load(_COMMIT)
                run = session.get(c.RunRecord, run_id, populate_existing=True)
                if run is None:
                    raise ValueError("research_history_publication_run_missing")
                sources = list(
                    session.scalars(
                        select(c.RunRoleOutputRecord)
                        .where(
                            c.RunRoleOutputRecord.run_id == run_id,
                            c.RunRoleOutputRecord.role == c.PUBLICATION_SOURCE_ROLE,
                        )
                        .execution_options(populate_existing=True)
                    )
                )
                if len(sources) != 1:
                    raise ValueError("research_quality_publication_original_missing")
                source = sources[0]
                body = json.loads(source.payload_json)
                if source.schema_version != c.PUBLICATION_SOURCE_SCHEMA_V4:
                    return False, None
                at, published_at = c.as_utc(as_of), c.as_utc(source.created_at)
                if at is None or published_at is None or published_at > at:
                    return False, None
                binding = private.load(_BINDING)
                reference = binding.GraphFinalProjectionRef.model_validate_json(
                    _canonical(body.get("graph_final_ref")),
                )
                recipe = private.load(_RECIPE_V3)
                selection = recipe.read_graph_recipe_selection_v3_in_session(session, run_id)
                if (
                    selection is None
                    or selection.receipt.manifest.model_dump(mode="json")
                    != index["graph_recipe_manifest"]
                    or selection.receipt.dependency_hash != registration.graph_hash
                ):
                    raise ValueError("research_history_recipe_replay_changed")
                original = binding.read_graph_final_in_session(
                    session,
                    run_id=run_id,
                    reference=reference,
                    expected_lease_owner=None,
                    require_current_permission=False,
                )
                if not isinstance(original.receipt, binding.ResearchGraphFinalProjectionV3):
                    raise ValueError("research_quality_publication_graph_mismatch")
                quality = private.load(
                    "packages.kernel.decision_hub_kernel.application.research_quality_sidecar"
                )
                sidecar = quality.read_quality_sidecar_in_session(
                    session,
                    original.receipt.quality_sidecar_id,
                    selection=selection.receipt,
                )
                if sidecar.phase != "final" or sidecar.recorded_at > published_at:
                    raise ValueError("research_quality_publication_sidecar_clock_invalid")
                artifact_id, gate = _retained_publication_originals(
                    session,
                    private=private,
                    run=run,
                    source=source,
                    original=original,
                    reference=reference,
                    selection=selection,
                    sidecar=sidecar,
                )
                runtime = json.loads(sidecar.prepared_runtime_json)
                limits = runtime["prepared_budget"]
                view = {
                    "run_id": run_id,
                    "as_of": at.isoformat(),
                    "status": "available",
                    "gate_status": gate.status.value,
                    "published_at": published_at.isoformat(),
                    "publication_ref": {
                        "output_id": source.output_id,
                        "role": source.role,
                        "schema_version": source.schema_version,
                        "payload_hash": source.payload_hash,
                        "recorded_at": published_at.isoformat(),
                    },
                    "graph_final_ref": reference.row.model_dump(mode="json"),
                    "recipe_selection_ref": selection.reference.model_dump(mode="json"),
                    "quality_sidecar_id": original.receipt.quality_sidecar_id,
                    "decision": sidecar.quality_decision.model_dump(mode="json"),
                    "budget": {
                        key: limits[key]
                        for key in (
                            "max_tool_calls",
                            "max_subagents",
                            "max_evidence_rounds",
                            "max_estimated_cost_usd",
                        )
                    }
                    | {"deadline_at": runtime["deadline_at"]},
                    "unknown_receipt_codes": list(sidecar.unknown_receipt_codes),
                    "usage": runtime.get("authenticated_conversation_usage"),
                }
                _verified_release(registration)
                if (
                    _original_recipe_registration(session, run_id) != registration
                    or _terminal_read_original_in_session(session, private, run_id) != terminal
                ):
                    raise ValueError("research_history_publication_original_changed")
                _require_quality_storage_implementation()
                publication = RetainedPublicationQualityRead(
                    _canonical(view),
                    artifact_id,
                    _canonical({"status": gate.status.value, "decisions": gate.decisions}),
                    (
                        original.receipt.selected_synthesis_observation_ref.model_dump_json()
                        if original.receipt.selected_synthesis_observation_ref is not None
                        else None
                    ),
                )
                # Complete the caller's dependent read before the full archive,
                # physical snapshot and source-SHA exit checks. Never export a
                # retained object or keep a permission/verdict across requests.
                return True, consume(publication)


_QUALITY_STORAGE_IMPLEMENTATION = (
    (
        RetainedPublicationQualityRead, _VerifiedReleaseRead,
        _RetainedInstallationRead, _VERIFIED_RELEASE_READ,
    ),
    _QUALITY_STORAGE_CONSUMERS,
    tuple(
        (function, getattr(function, "__wrapped__", function).__code__)
        for function in (
            _retained_policy_materials,
            _retained_publication_originals,
            read_retained_publication_quality_in_session,
            consume_retained_publication_quality_in_session,
            _verified_release_uncached,
            _verified_release,
            _verified_release_read_scope,
            _current_async_task,
            _PrivateRelease.load,
            _private_shape_read_scope,
            _private_v3_read_scope,
            _installation_from_description,
            _quality_storage_consumer_sources_match,
            _registry_registrations,
            _active_registrations,
            _require_loaded_provenance_codec,
            _VerifiedReleaseRead.boundary_matches,
            _VerifiedReleaseRead.matches,
        )
    ),
)


def _require_quality_storage_implementation() -> None:
    dto, consumers, functions = _QUALITY_STORAGE_IMPLEMENTATION
    current = (
        _retained_policy_materials,
        _retained_publication_originals,
        read_retained_publication_quality_in_session,
        consume_retained_publication_quality_in_session,
        _verified_release_uncached,
        _verified_release,
        _verified_release_read_scope,
        _current_async_task,
        _PrivateRelease.load,
        _private_shape_read_scope,
        _private_v3_read_scope,
        _installation_from_description,
        _quality_storage_consumer_sources_match,
        _registry_registrations,
        _active_registrations,
        _require_loaded_provenance_codec,
        _VerifiedReleaseRead.boundary_matches,
        _VerifiedReleaseRead.matches,
    )
    if (
        (
            RetainedPublicationQualityRead, _VerifiedReleaseRead,
            _RetainedInstallationRead, _VERIFIED_RELEASE_READ,
        ) != dto
        or _QUALITY_STORAGE_CONSUMERS is not consumers
        or any(
            actual is not saved or getattr(actual, "__wrapped__", actual).__code__ is not code
            for actual, (saved, code) in zip(current, functions, strict=True)
        )
    ):
        raise ValueError("research_history_quality_storage_implementation_changed")

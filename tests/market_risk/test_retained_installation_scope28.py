"""Six private installation-scope controls, not a full quality/provider proof.

Actual archive producer, registry, uncached SHA verifier and installation codec
execute. The small closure's recipe module uses actual installed DTO classes and
the producer's fresh manifest algorithm as an explicit module/business-reader
seam; no native, permission, publication, full-final or quality result is minted.
No formal database, HTTP, provider, model or product build is used.
"""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import json
import os
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, cast

import pytest
from sqlalchemy import Table, create_engine
from sqlalchemy.orm import Session

from packages.kernel.decision_hub_kernel.application import (
    research_graph_history_versions as candidate,
)
from packages.kernel.decision_hub_kernel.application import research_graph_recipe_v3 as recipe
from packages.kernel.decision_hub_kernel.application import research_recipe_archive as archive
from packages.kernel.decision_hub_kernel.persistence.db import RunRecord, RunRoleOutputRecord
from tests.market_risk.test_release_provenance_retained_v3 import (
    files as provenance_files,
)
from tests.market_risk.test_release_provenance_retained_v3 import put, sha

# These controls deliberately exercise the private read/storage boundary.
# pyright: reportPrivateUsage=false

DEV = Path(__file__).resolve().parents[2]
OUT = DEV / "artifacts/market-risk-implementation-20261007/B2/retained-installation-scope28"
RELATIVE = "packages/kernel/decision_hub_kernel/application/research_graph_history_versions.py"
BEFORE_SHA = "8e025a794a46571aa6bc577f1446f6dca01cd7aafcb3f8b1dd729f059b12f6a0"
NOW = datetime(2026, 10, 9, 0, 0, tzinfo=UTC)
RUN = "controlled-installation28"


@pytest.fixture
def controlled(tmp_path: Path):
    root, _, original, _, _ = cast(Any, provenance_files).__wrapped__(tmp_path)
    # These two real shared files satisfy the unchanged archive verifier rather
    # than removing its shared-source checks for a smaller fixture.
    for name in candidate._SHARED_MODULES:
        relative = name.replace(".", "/") + ".py"
        raw = (DEV / relative).read_bytes()
        put(root, relative, raw)
        original["sources"][relative] = sha(raw)
    binding_ref = "packages/kernel/decision_hub_kernel/application/research_graph_binding.py"
    binding_raw = (
        (root / binding_ref).read_bytes()
        + b"from . import research_context_selection_versions\n"
    )
    put(root, binding_ref, binding_raw)
    original["sources"][binding_ref] = sha(binding_raw)
    manifest = archive._planned_manifest(
        root, {}, {"python": sys.version, "sqlite": sqlite3.sqlite_version}, version=3,
    )
    pointer = {
        "schema_version": "research-graph-recipe-installed.v3",
        "recipe_version": "research-graph-projection.v3", "lifecycle": "installed",
        "registration": manifest["registration"],
        "dependency_hash": sha(archive._canonical(manifest)[:-1]),
        "plugin_build_hash": original["plugin_build_hash"],
    }
    pointer_raw = archive._canonical(pointer)
    put(root, recipe.INSTALLED_POINTER_REF, pointer_raw)
    original["sources"][recipe.INSTALLED_POINTER_REF] = sha(pointer_raw)
    original["evidence"]["research_graph_recipe_v3"]["installed_pointer"]["sha256"] = sha(
        pointer_raw,
    )
    receipt_relative = "artifacts/prior/receipt.json"
    # A new controlled v1 receipt authenticates these exact fixture bytes; it is
    # never written over or treated as the formal original receipt.
    prior_raw = archive._canonical(original)
    put(root, receipt_relative, prior_raw)
    prepared = archive.prepare_retained_graph_recipe_v3_archive(
        repository_root=root, prior_receipt_path=root / receipt_relative,
        prior_receipt_sha256=sha(prior_raw), original_manifest=manifest,
        staging_root=root / "artifacts/stage28",
    )
    assert prepared.registry_ref is not None
    for name, digest in prepared.install_sources.items():
        raw = archive._read(archive._path(root / "artifacts/stage28", name))
        assert sha(raw) == digest
        put(root, name, raw)
    installed = {
        **original, "sources": {**original["sources"], **prepared.install_sources},
        "evidence": {**original["evidence"],
                     "retained_recipe_archives_v1": {"registry": dict(prepared.registry_ref)}},
    }
    installed_raw = archive._canonical(installed)
    installed_ref = "artifacts/current/receipt.json"
    put(root, installed_ref, installed_raw)
    before_raw = (OUT / "before" / RELATIVE).read_bytes()
    assert sha(before_raw) == BEFORE_SHA
    before: Any = ModuleType(cast(str, candidate.__package__) + "._installation_before28")
    before.__file__, before.__package__ = str(DEV / RELATIVE), candidate.__package__
    sys.modules[before.__name__] = before
    exec(compile(before_raw, before.__file__, "exec"), before.__dict__)
    patch = pytest.MonkeyPatch()
    states: dict[str, Any] = {}
    for label, module in (("before", before), ("candidate", candidate)):
        patch.setattr(module, "_CURRENT_ROOT", root)
        patch.setattr(module, "_RESOURCE_ROOT", root / "packages/kernel/research_recipe_versions")
        patch.setattr(module, "_REGISTRY_BINDING", None)
        module.configure_retained_recipe_registry_from_validated_release(
            receipt_path=root / installed_ref, receipt_sha256=sha(installed_raw),
        )
        registration = next(item for item in module._active_registrations()
                            if item.graph_hash == prepared.archived_graph_hash)
        index, payloads, private_root = module._verified_release_uncached(registration)
        private = module._PrivateRelease(registration, payloads, private_root, v3=True)
        logical: Any = ModuleType(private.prefix + "._controlled_installation28_" + label)
        logical.SOURCE_REF = recipe.SOURCE_REF
        logical._LOADED_SOURCE_SHA = sha(payloads[recipe.SOURCE_REF])
        logical.GraphRecipeResourceV3 = recipe.GraphRecipeResourceV3
        logical.GraphRecipeManifestV3 = recipe.GraphRecipeManifestV3
        logical._InstalledBindingV3 = recipe._InstalledBindingV3
        calls = {"manifest": 0}

        def fresh_manifest(
            *, calls: dict[str, int] = calls, private: Any = private,
            index: dict[str, Any] = index,
        ) -> recipe.GraphRecipeManifestV3:
            calls["manifest"] += 1
            body = archive._planned_manifest(
                private.root, {}, index["graph_recipe_manifest"]["runtime_versions"], version=3,
            )
            return recipe.GraphRecipeManifestV3.model_validate_json(json.dumps(body))

        logical.graph_recipe_manifest_v3 = fresh_manifest
        logical._installation = lambda: None
        private.modules[module._RECIPE_V3] = logical
        states[label] = SimpleNamespace(module=module, private=private, logical=logical,
                                        registration=registration, calls=calls)
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'private28.sqlite3'}")
    cast(Table, RunRecord.__table__).create(engine)
    cast(Table, RunRoleOutputRecord.__table__).create(engine)
    selection = candidate._canonical({
        "dependency_hash": states["candidate"].registration.graph_hash,
        "controlled_lookup_only": True,
    })
    with Session(engine) as session, session.begin():
        session.add(RunRecord(run_id=RUN, event_id="controlled28", status="degraded",
                              created_at=NOW, updated_at=NOW, finished_at=NOW))
        session.add(RunRoleOutputRecord(
            output_id="controlled-selection28", run_id=RUN, role="graph_recipe_selection",
            schema_version="research-graph-recipe-selection.v3", payload_json=selection,
            payload_hash=sha(selection.encode()), created_at=NOW,
        ))
    try:
        yield SimpleNamespace(states=states, engine=engine, root=root, now=NOW)
    finally:
        patch.undo()
        sys.modules.pop(before.__name__, None)
        engine.dispose()


@contextmanager
def protected(controlled: Any, label: str = "candidate", *, deny_marker: bool = False):
    state = controlled.states[label]
    module = state.module
    with Session(controlled.engine) as session:
        session.connection().exec_driver_sql("BEGIN")
        native = cast(sqlite3.Connection, session.connection().connection.driver_connection)
        changes = native.total_changes
        authorizer: Any = None
        if deny_marker:
            def deny_authorizer(action: int, *_args: Any) -> int:
                if action in {sqlite3.SQLITE_SAVEPOINT, sqlite3.SQLITE_INSERT,
                              sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE}:
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK
            authorizer = deny_authorizer
            native.set_authorizer(authorizer)
        try:
            verified = module._verified_release_uncached(state.registration)
            with module._read_only(session):
                with module._verified_release_read_scope(
                    session, RUN, state.registration, verified,
                ):
                    scope = module._VERIFIED_RELEASE_READ.get()
                    with module._private_v3_read_scope(session, state.private, RUN):
                        yield session, state, scope
            assert native.total_changes == changes
            assert module._VERIFIED_RELEASE_READ.get() is None
        finally:
            if authorizer is not None:
                native.set_authorizer(None)
            session.rollback()


def body(value: Any) -> str:
    return candidate._canonical({
        "receipt": value.receipt.model_dump(mode="json"),
        "pointer": value.pointer.model_dump(mode="json"),
        "dependency_hash": value.dependency_hash,
        "registration": value.registration.model_dump(mode="json"),
        "plugin_build_hash": value.plugin_build_hash,
        "manifest": value.manifest.model_dump(mode="json"),
    })


def test_p1_full_installed_dto_equivalence_and_detached_clones(controlled: Any):
    values = {}
    for label in ("before", "candidate"):
        with protected(controlled, label) as (_session, state, scope):
            returned = [state.logical._installation() for _ in range(4)]
            values[label] = [body(value) for value in returned]
            assert len(set(values[label])) == 1
            assert returned[0] is not returned[1]
            assert returned[0].manifest is not returned[1].manifest
            if label == "candidate":
                assert scope.marker is not None and len(scope.installations) == 1
                object.__setattr__(returned[0], "plugin_build_hash", "0" * 64)
                assert body(state.logical._installation()) == values[label][0]
        assert state.calls["manifest"] == (4 if label == "before" else 2)
    assert values["before"] == values["candidate"]
    assert controlled.now == NOW


def test_n1_fresh_archive_manifest_and_loaded_function_reject(controlled: Any):
    state = controlled.states["candidate"]
    module = state.module
    index, _payloads, _root = module._verified_release_uncached(state.registration)
    names = [recipe.SOURCE_REF, index["graph_recipe_manifest"]["schema_files"][0]["path"],
             index["installation_binding"]["receipt"]["path"], recipe.INSTALLED_POINTER_REF]
    for name in names:
        target = state.private.root / name
        raw, stamp = target.read_bytes(), target.stat()
        try:
            with pytest.raises(ValueError):
                with protected(controlled) as (_session, state, _scope):
                    state.logical._installation()
                    target.write_bytes(bytes([raw[0] ^ 1]) + raw[1:])
                    os.utime(target, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
                    state.logical._installation()  # hit never exempts the exit SHA
        finally:
            target.write_bytes(raw)
            os.utime(target, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
            assert hashlib.sha256(target.read_bytes()).digest() == hashlib.sha256(raw).digest()
    for attribute in ("graph_recipe_manifest_v3", "_installation", "_LOADED_SOURCE_SHA"):
        saved = getattr(state.logical, attribute)
        try:
            with pytest.raises(ValueError):
                with protected(controlled) as (_session, state, _scope):
                    original = state.logical._installation
                    original()
                    setattr(
                        state.logical, attribute,
                        "0" * 64 if isinstance(saved, str) else lambda: None,
                    )
                    original()
        finally:
            setattr(state.logical, attribute, saved)
    with pytest.raises(ValueError, match="manifest_changed"):
        with protected(controlled) as (_session, state, _scope):
            state.logical._installation()
            state.logical.graph_recipe_manifest_v3.__kwdefaults__["index"] = {
                **index, "graph_recipe_manifest": {**index["graph_recipe_manifest"],
                                                    "runtime_versions": {"python": "not-current"}},
            }


def test_n2_selection_original_hash_schema_and_changes_reject(controlled: Any):
    for damage in ("hash", "schema", "dependency"):
        with Session(controlled.engine) as writer, writer.begin():
            row = writer.get(RunRoleOutputRecord, "controlled-selection28")
            assert row is not None
            saved = row.payload_json, row.payload_hash, row.schema_version
            if damage == "hash":
                row.payload_hash = "0" * 64
            elif damage == "schema":
                row.schema_version = "wrong-schema"
            else:
                row.payload_json = '{"dependency_hash":"' + "0" * 64 + '"}'
                row.payload_hash = sha(row.payload_json.encode())
        try:
            with pytest.raises(ValueError):
                with protected(controlled) as (_session, state, _scope):
                    state.logical._installation()
        finally:
            with Session(controlled.engine) as writer, writer.begin():
                row = writer.get(RunRoleOutputRecord, "controlled-selection28")
                assert row is not None
                row.payload_json, row.payload_hash, row.schema_version = saved


def test_n3_raw_transaction_dirty_and_foreign_boundaries_reject(controlled: Any):
    with pytest.raises(ValueError, match="read_boundary_changed"):
        with protected(controlled) as (session, state, _scope):
            state.logical._installation()
            native = cast(sqlite3.Connection, session.connection().connection.driver_connection)
            changes = native.total_changes
            native.execute("COMMIT")
            native.execute("BEGIN")
            assert native.total_changes == changes and native.in_transaction
            state.logical._installation()
    with pytest.raises(ValueError, match="read_boundary_changed"):
        with protected(controlled) as (session, state, _scope):
            state.logical._installation()
            row = session.get(RunRoleOutputRecord, "controlled-selection28")
            assert row is not None
            row.payload_json += " "
            state.logical._installation()
    with protected(controlled) as (_session, state, scope), Session(controlled.engine) as other:
        state.logical._installation()
        other.connection().exec_driver_sql("BEGIN")
        other_native = other.connection().connection.driver_connection
        assert not replace(scope, native=other_native).boundary_matches(state.registration)
        assert not replace(scope, session=other).boundary_matches(state.registration)
        assert not replace(scope, nested=object()).boundary_matches(state.registration)
        copied = contextvars.copy_context()
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert not pool.submit(copied.run, scope.boundary_matches, state.registration).result()

        async def foreign_task() -> bool:
            return scope.boundary_matches(state.registration)

        assert not asyncio.run(foreign_task())
        assert len(scope.installations) == 1


def test_n4_strict_authorizer_keeps_original_full_path(controlled: Any):
    values = []
    for label in ("before", "candidate"):
        with protected(controlled, label, deny_marker=True) as (_session, state, scope):
            values.append([body(state.logical._installation()) for _ in range(3)])
            if label == "candidate":
                assert scope.marker is None and not scope.installations
        assert state.calls["manifest"] == 3
    assert values[0] == values[1]


def test_n5_exception_clears_scope_and_new_scope_reads_fresh(controlled: Any):
    scope = None
    with pytest.raises(RuntimeError, match="controlled-consumer-error"):
        with protected(controlled) as (_session, state, scope):
            state.logical._installation()
            raise RuntimeError("controlled-consumer-error")
    assert scope is not None and not scope.installations
    assert candidate._VERIFIED_RELEASE_READ.get() is None
    state = controlled.states["candidate"]
    state.calls["manifest"] = 0
    with protected(controlled) as (_session, state, next_scope):
        state.logical._installation()
        assert next_scope is not scope and len(next_scope.installations) == 1
    assert state.calls["manifest"] == 2

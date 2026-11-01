"""Actual Windows byte protection and pure native boundary controls; no DB."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from packages.kernel.decision_hub_kernel.application import (
    research_publication_runtime48 as runtime,
)
from packages.kernel.decision_hub_kernel.persistence import research_source_snapshot as scoped
from packages.kernel.decision_hub_kernel.ports.research_source_snapshot import (
    SOURCE_SNAPSHOT_READ_ABI,
    PublicationSourceSnapshotV1,
)


class NativeShape(sqlite3.Connection):
    """Uninitialized Python native type: deliberately no actual database."""

    def __init__(self):
        self.alive = True
        self.changes = 0
        self.readonly = True

    @property
    def in_transaction(self):
        return self.alive

    @property
    def total_changes(self):
        return self.changes

    @property
    def text_factory(self):
        return str

    def execute(self, statement):
        assert statement == "PRAGMA query_only"
        return SimpleNamespace(fetchone=lambda: (1 if self.readonly else 0,))


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    def no_database(*_args, **_kwargs):
        raise AssertionError("test must never open SQLite")

    monkeypatch.setattr(sqlite3, "connect", no_database)
    root = tmp_path.resolve()
    path = root / "source.py"
    path.write_bytes(b"# authenticated source\n")
    native = NativeShape()
    connection = SimpleNamespace(connection=SimpleNamespace(driver_connection=native), closed=False)
    state = dict(transaction=object(), identity=True, capture=0, full=0)
    session = SimpleNamespace(
        connection=lambda: connection,
        get_transaction=lambda: state["transaction"],
        get_nested_transaction=lambda: None,
        new=(),
        dirty=(),
        deleted=(),
    )
    collector = SimpleNamespace(
        session=session,
        connection=connection,
        native=native,
        owner_thread=threading.get_ident(),
        _entered=True,
        _finished=False,
        _require_boundary=lambda: None,
    )

    @dataclass(frozen=True)
    class Guard:
        root: Path
        files: tuple

        @classmethod
        def capture(cls):
            active = scoped.active_source_snapshot(cls)
            if active is not None:
                active.require_read(active.session, active.run_id)
                return active.guard
            state["capture"] += 1
            return cls(root, (("source.py", hashlib.sha256(path.read_bytes()).hexdigest()),))

        def matches_identity(self):
            return state["identity"] and sorted(file.name for file in root.iterdir()) == [
                "source.py"
            ]

        def matches_scope_identity(self):
            return state["identity"]

        def matches(self):
            active = scoped.active_source_snapshot(type(self))
            if active is not None:
                return active.matches_guard(self)
            state["full"] += 1
            return self.matches_identity() and all(
                hashlib.sha256((self.root / ref).read_bytes()).hexdigest() == digest
                for ref, digest in self.files
            )

    def scope(**kwargs):
        return scoped.publication_source_snapshot_read_scope(
            session,
            run_id="run-current-v2",
            guard_type=Guard,
            collector=collector,
            declared_sources=(),
            **kwargs,
        )

    return SimpleNamespace(
        root=root,
        path=path,
        state=state,
        session=session,
        native=native,
        connection=connection,
        collector=collector,
        guard_type=Guard,
        scope=scope,
    )


@pytest.mark.skipif(os.name != "nt", reason="real Windows handle sharing controls")
def test_reuse_is_same_native_scope_without_rehash_and_no_cross_request(fixture, monkeypatch):
    f = fixture
    with f.scope() as lease:
        assert f.state["capture"] == f.state["full"] == 1
        original_read = Path.read_bytes
        monkeypatch.setattr(
            Path,
            "read_bytes",
            lambda *_: (_ for _ in ()).throw(
                AssertionError("source full bytes unexpectedly rehashed in repeated read")
            ),
        )
        for _ in range(3):
            assert f.guard_type.capture() is lease.guard and lease.guard.matches()
            lease.require_read(f.session, "run-current-v2")
        monkeypatch.setattr(Path, "read_bytes", original_read)
        assert lease.snapshot.reader_abi == SOURCE_SNAPSHOT_READ_ABI
    assert f.state["capture"] == f.state["full"] == 2
    assert scoped.active_source_snapshot(f.guard_type) is None
    f.guard_type.capture().matches()
    assert f.state["capture"] == f.state["full"] == 3
    assert not lease.files and not lease.directories
    with pytest.raises(ValueError, match="boundary_changed"):
        lease.require_read(f.session, "run-current-v2")


@pytest.mark.skipif(os.name != "nt", reason="real Windows handle sharing controls")
def test_native_write_delete_parent_retarget_denied_until_exit(fixture):
    f = fixture
    with f.scope() as lease:
        for operation in (
            lambda: f.path.write_bytes(b"changed"),
            f.path.unlink,
            lambda: f.root.rename(f.root.with_name(f.root.name + "-moved")),
        ):
            with pytest.raises(PermissionError):
                operation()
        lease.require_read(f.session, "run-current-v2")
    f.path.write_bytes(b"after actual owner exit")


@pytest.mark.skipif(os.name != "nt", reason="real Windows handle sharing controls")
@pytest.mark.parametrize(
    "change", ["transaction", "native", "write", "permission", "collector", "identity"]
)
def test_native_transaction_permission_or_source_drift_rejects_and_closes(fixture, change):
    f = fixture
    with pytest.raises(ValueError, match="boundary_changed"):
        with f.scope() as lease:
            if change == "transaction":
                f.state["transaction"] = object()
            elif change == "native":
                f.connection.connection.driver_connection = NativeShape()
            elif change == "write":
                f.native.changes += 1
            elif change == "permission":
                f.native.readonly = False
            elif change == "collector":
                f.collector._finished = True
            else:
                f.state["identity"] = False
            lease.require_read(f.session, "run-current-v2")
    assert scoped.active_source_snapshot(f.guard_type) is None
    assert not lease.files and not lease.directories
    f.path.write_bytes(b"known reject has released native handles")


@pytest.mark.skipif(os.name != "nt", reason="real Windows handle sharing controls")
def test_exit_inventory_change_is_refused_without_consuming_guard(fixture):
    f = fixture
    with pytest.raises(ValueError, match="boundary_changed"):
        with f.scope() as lease:
            (f.root / "unselected.py").write_text("# unexpected new source")
    assert not lease.files and not lease.directories


@pytest.mark.skipif(os.name != "nt", reason="real Windows handle sharing controls")
def test_foreign_thread_same_context_and_other_run_cannot_reuse(fixture):
    from contextvars import copy_context

    f = fixture
    with f.scope() as lease:
        with pytest.raises(ValueError, match="boundary_changed"):
            lease.require_read(f.session, "other-run")
        context = copy_context()
        errors = []

        def reuse():
            try:
                context.run(lambda: f.guard_type.capture())
            except ValueError as error:
                errors.append(str(error))

        thread = threading.Thread(target=reuse)
        thread.start()
        thread.join(2)
        assert not thread.is_alive() and errors == ["publication_source_snapshot_boundary_changed"]
        lease.require_read(f.session, "run-current-v2")


@pytest.mark.skipif(os.name != "nt", reason="real Windows handle sharing controls")
def test_expected_frozen_bytes_identity_must_match(fixture):
    f = fixture
    with f.scope() as lease:
        expected = lease.snapshot.model_dump(mode="json")
    expected["source_set_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="set_hash_changed"):
        with f.scope(expected_snapshot=expected):
            raise AssertionError("malformed frozen DTO was accepted")
    assert scoped.active_source_snapshot(f.guard_type) is None
    f.path.write_bytes(b"no handle leak on malformed frozen DTO")


@pytest.mark.skipif(os.name != "nt", reason="actual native HANDLE cleanup fault control")
def test_native_file_identity_failure_closes_every_acquired_handle(fixture, monkeypatch):
    opened, closed, file_handles = [], [], set()
    native_type = scoped._WindowsFiles

    class FaultNative(native_type):
        def open(self, path, *, directory=False):
            handle = super().open(path, directory=directory)
            opened.append(handle)
            if not directory:
                file_handles.add(handle)
            return handle

        def identity(self, handle):
            if handle in file_handles:
                raise ValueError("controlled_native_identity_unavailable")
            return super().identity(handle)

        def close(self, handle):
            closed.append(handle)
            return super().close(handle)

    monkeypatch.setattr(scoped, "_WindowsFiles", FaultNative)
    with pytest.raises(ValueError, match="controlled_native_identity_unavailable"):
        with fixture.scope():
            raise AssertionError("native identity failure was ignored")
    assert opened and sorted(opened) == sorted(closed)
    fixture.path.write_text("all native handles closed before propagation")


@pytest.mark.parametrize("ref", ["../foreign.py", "C:source.py", "a//b.py", "/absolute.py"])
def test_source_snapshot_pure_dto_paths_fail_closed(ref):
    shape = [[ref, "a" * 64, 1]]
    sha = hashlib.sha256(json.dumps(shape, separators=(",", ":")).encode()).hexdigest()
    with pytest.raises(ValueError, match="path_invalid"):
        PublicationSourceSnapshotV1(
            files=[dict(relative_ref=ref, sha256="a" * 64, bytes=1)], source_set_sha256=sha
        )


def test_unsupported_retained_abi_is_not_granted_by_mutable_marker():
    closure = SimpleNamespace(nonce="nonce", mode="retained-installation")
    authority = SimpleNamespace(_issued={"nonce": closure})
    assert runtime._current_source_snapshot_abi(authority, closure) is None
    with pytest.raises(ValueError, match="issuer_identity_changed"):
        runtime._current_source_snapshot_abi(
            authority, SimpleNamespace(nonce="nonce", mode="current-installation")
        )


def test_known_retained_qualification_returns_before_any_full_closure(monkeypatch):
    monkeypatch.setattr(
        runtime.normal_history, "_original_recipe_registration", lambda *_: object()
    )
    preparer = object.__new__(runtime.ManagedFrozenPublicationPreparer)
    assert preparer.reader_abi_qualification(object(), run_id="original-unsupported") is None


@pytest.mark.parametrize("change", ["root", "schema", "selector", "runtime"])
def test_real_current_guard_live_schema_selector_runtime_bindings_are_fresh(
    monkeypatch, tmp_path, change
):
    import importlib.metadata
    import sys

    from packages.kernel.decision_hub_kernel.application import (
        research_context_selection as selector,
    )
    from packages.kernel.decision_hub_kernel.application import research_graph_binding as binding
    from packages.kernel.decision_hub_kernel.application import research_graph_recipe as recipe

    state = dict(
        root=tmp_path, schema=(b"fresh-schema",), selector={"ref": "original"}, runtime="1"
    )
    monkeypatch.setattr(recipe, "_ROOT", tmp_path)
    monkeypatch.setattr(binding, "_original_contract_signatures", lambda: state["schema"])
    monkeypatch.setattr(selector, "_selector_identity", lambda: state["selector"])
    monkeypatch.setattr(importlib.metadata, "version", lambda _: state["runtime"])
    guard = binding._ReadSourceGuard(
        tmp_path,
        (),
        (),
        tuple(
            sorted(dict(python=sys.version, sqlite=sqlite3.sqlite_version, controlled="1").items())
        ),
        binding.observation_json(state["selector"]),
        state["schema"],
    )
    assert guard.matches_scope_identity()
    if change == "root":
        monkeypatch.setattr(recipe, "_ROOT", tmp_path / "changed")
    elif change == "schema":
        state["schema"] = (b"changed-schema",)
    elif change == "selector":
        state["selector"] = {"ref": "changed"}
    else:
        state["runtime"] = "2"
    assert not guard.matches_scope_identity()


@pytest.mark.skipif(os.name != "nt", reason="real Windows handle sharing controls")
def test_same_dto_requires_new_lease_for_independent_frozen_native_boundary(fixture):
    f = fixture
    with f.scope() as normal:
        expected = normal.snapshot.model_dump(mode="json")
    native = NativeShape()
    connection = SimpleNamespace(connection=SimpleNamespace(driver_connection=native), closed=False)
    session = SimpleNamespace(
        connection=lambda: connection,
        get_transaction=lambda: f.state["transaction"],
        get_nested_transaction=lambda: None,
        new=(),
        dirty=(),
        deleted=(),
    )
    collector = SimpleNamespace(
        session=session,
        connection=connection,
        native=native,
        owner_thread=threading.get_ident(),
        _entered=True,
        _finished=False,
        _require_boundary=lambda: None,
    )
    with scoped.publication_source_snapshot_read_scope(
        session,
        run_id="run-current-v2",
        guard_type=f.guard_type,
        collector=collector,
        declared_sources=(),
        expected_snapshot=expected,
    ) as frozen:
        assert frozen is not normal and frozen.snapshot == normal.snapshot
        assert f.guard_type.capture() is frozen.guard and frozen.guard.matches()
        with pytest.raises(ValueError, match="boundary_changed"):
            frozen.require_read(f.session, "run-current-v2")
    assert f.state["capture"] == 4 and f.state["full"] == 4


def test_scope_like_object_without_native_issuer_cannot_bypass_source_guard(fixture):
    f = fixture
    token = scoped._ACTIVE.set(
        SimpleNamespace(guard_type=f.guard_type, guard=object(), require_read=lambda *_: None)
    )
    try:
        with pytest.raises(ValueError, match="boundary_changed"):
            f.guard_type.capture()
    finally:
        scoped._ACTIVE.reset(token)


@pytest.mark.skipif(os.name != "nt", reason="real Windows handle sharing controls")
def test_changed_scope_implementation_is_rejected_before_any_byte_lease(fixture, monkeypatch):
    monkeypatch.setattr(scoped.PublicationSourceReadScope, "matches_guard", lambda *_: True)
    with pytest.raises(ValueError, match="implementation_changed"):
        with fixture.scope():
            raise AssertionError("replacement implementation entered the reuse scope")
    fixture.path.write_text("no leaked HANDLE on implementation refusal")


@pytest.mark.skipif(os.name != "nt", reason="real Windows handle sharing controls")
def test_other_async_task_with_copied_context_cannot_borrow_scope(fixture):
    import asyncio

    f = fixture

    async def owner():
        with f.scope() as lease:

            async def foreign():
                with pytest.raises(ValueError, match="boundary_changed"):
                    lease.require_read(f.session, "run-current-v2")

            await asyncio.create_task(foreign())
            lease.require_read(f.session, "run-current-v2")

    asyncio.run(owner())


@pytest.mark.skipif(os.name != "nt", reason="real Windows full-scale native handles")
def test_full_scale_16000_inventory_2048_sources_repeated_scope_cost(tmp_path, monkeypatch):
    import time

    monkeypatch.setattr(
        sqlite3,
        "connect",
        lambda *_: (_ for _ in ()).throw(
            AssertionError("no DB in controlled source-scale measurement")
        ),
    )
    root = tmp_path.resolve()
    total_inventory, source_count = 16000, 2048
    for number in range(total_inventory):
        path = root / f"p{number // 256:02d}" / f"s{number:05d}.py"
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(b"#" * 1024 if number < source_count else b"#")
    refs = tuple(f"p{number // 256:02d}/s{number:05d}.py" for number in range(source_count))

    def inventory():
        return tuple(sorted(path.relative_to(root).as_posix() for path in root.rglob("*.py")))

    original_inventory = inventory()
    count = dict(capture=0, full=0)

    @dataclass(frozen=True)
    class ScaleGuard:
        root: Path
        files: tuple
        inventory: tuple

        @classmethod
        def capture(cls):
            active = scoped.active_source_snapshot(cls)
            if active is not None:
                active.require_read(active.session, active.run_id)
                return active.guard
            count["capture"] += 1
            return cls(
                root,
                tuple((ref, hashlib.sha256((root / ref).read_bytes()).hexdigest()) for ref in refs),
                inventory(),
            )

        def matches_identity(self):
            return inventory() == self.inventory

        def matches_scope_identity(self):
            return True

        def matches(self):
            active = scoped.active_source_snapshot(type(self))
            if active is not None:
                return active.matches_guard(self)
            count["full"] += 1
            return self.matches_identity() and all(
                hashlib.sha256((root / ref).read_bytes()).hexdigest() == digest
                for ref, digest in self.files
            )

    native = NativeShape()
    connection = SimpleNamespace(connection=SimpleNamespace(driver_connection=native), closed=False)
    transaction = object()
    session = SimpleNamespace(
        connection=lambda: connection,
        get_transaction=lambda: transaction,
        get_nested_transaction=lambda: None,
        new=(),
        dirty=(),
        deleted=(),
    )
    collector = SimpleNamespace(
        session=session,
        connection=connection,
        native=native,
        owner_thread=threading.get_ident(),
        _entered=True,
        _finished=False,
        _require_boundary=lambda: None,
    )
    started = time.monotonic()
    samples = []
    with scoped.publication_source_snapshot_read_scope(
        session,
        run_id="scale-native-v2",
        guard_type=ScaleGuard,
        collector=collector,
        declared_sources=(),
    ) as lease:
        entered = time.monotonic()
        for _ in range(60):
            before = time.monotonic()
            assert ScaleGuard.capture() is lease.guard and lease.guard.matches()
            samples.append(time.monotonic() - before)
        reused = time.monotonic()
        assert lease.reuse_count == 60 and count == dict(capture=1, full=1)
        assert len(lease.snapshot.files) == source_count
    ended = time.monotonic()
    evidence = dict(
        scope="controlled Windows source-scale fixture; not product performance",
        source_files=source_count,
        source_bytes=source_count * 1024,
        full_lexical_inventory=len(original_inventory),
        repeats=60,
        reuse_count=lease.reuse_count,
        entry_seconds=entered - started,
        reuse_seconds=reused - entered,
        individual_pair_seconds=samples,
        full_exit_seconds=ended - reused,
        total_scope_seconds=ended - started,
        full_capture_calls=count["capture"],
        full_matches=count["full"],
        db_open_count=0,
        model_count=0,
        real_120s_publication_qualified=False,
    )
    output = (
        Path(__file__).resolve().parents[2]
        / "artifacts/core-goal-20261011/source-snapshot-abi02/source-scale02.json"
    )
    output.write_text(json.dumps(evidence, indent=2) + "\n")
    assert count == dict(capture=2, full=2) and not lease.files and not lease.directories
    assert len(original_inventory) == total_inventory

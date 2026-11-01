"""Capture scope controls without opening SQLite or issuing report authority.

The collector, registration lookup and complete archive verification are explicit
test seams. The actual retained read scope and memo boundary execute. These
controls do not prove full native capture, frozen replay or product delivery.
"""

import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from packages.kernel.decision_hub_kernel.application import (
    research_publication_runtime48 as runtime,
)


class StopAfterCapture(RuntimeError):
    pass


class NativeShape(sqlite3.Connection):
    """Uninitialized native shape; no database connection is created."""

    def __init__(self):
        self.markers = []

    @property
    def in_transaction(self):
        return True

    @property
    def total_changes(self):
        return 0

    def execute(self, statement, parameters=()):
        assert not parameters
        if statement.startswith("SAVEPOINT "):
            self.markers.append(statement.split()[-1])
        else:
            assert statement.startswith("RELEASE SAVEPOINT ")
            assert self.markers.pop() == statement.split()[-1]


@pytest.fixture
def control(monkeypatch, tmp_path):
    history = runtime.normal_history
    selected = history._Registration("fixture-installation", "a" * 64, "b" * 64, "c" * 64)
    native = NativeShape()
    connection = SimpleNamespace(connection=SimpleNamespace(driver_connection=native))
    state = {
        "selected": selected, "full_reads": 0, "original_reads": 0,
        "transaction": object(), "mode": "retained-installation", "epoch": 0,
        "collected": 0, "collect_hook": lambda: None,
    }
    session = SimpleNamespace(
        connection=lambda: connection, get_transaction=lambda: state["transaction"],
        get_nested_transaction=lambda: None, new=(), dirty=(), deleted=(),
    )
    root = tmp_path / "root"
    stage = tmp_path / "stage"
    root.mkdir()
    stage.mkdir()
    binding = SimpleNamespace(receipt_path=root / "receipt.json", receipt_hash="d" * 64)
    monkeypatch.setattr(history, "_CURRENT_ROOT", root)
    monkeypatch.setattr(history, "_REGISTRY_BINDING", binding)
    monkeypatch.setattr(history, "_original_recipe_registration", lambda s, r: (
        state["selected"] if s is session and r == "fixture-run" else None
    ))

    def uncached(registration):
        assert registration == selected
        state["full_reads"] += 1
        return {"epoch": state["epoch"]}, {"source.py": b"fixture-original"}, root

    monkeypatch.setattr(history, "_verified_release_uncached", uncached)
    preparer = runtime.ManagedFrozenPublicationPreparer(root, stage)

    def resolve(s, run_id, **kwargs):
        assert s is session and run_id == "fixture-run"
        assert kwargs == {"receipt_ref": "receipt.json", "receipt_sha256": "d" * 64}
        closure = SimpleNamespace(
            mode=state["mode"], selected_directory=selected.directory,
            selected_index_sha256=selected.index_hash, nonce="fixture-nonce",
            source_files=lambda: ("source.py",), entries=(),
        )
        preparer.authority._issued[closure.nonce] = closure
        return closure

    def authorize(closure, sources):
        assert sources == {"fixture": "fully-collected"}
        assert history._VERIFIED_RELEASE_READ.get() is None
        raise StopAfterCapture

    monkeypatch.setattr(preparer.authority, "resolve", resolve)
    monkeypatch.setattr(preparer.authority, "authorize_frozen", authorize)

    def original(s, run_id, **kwargs):
        assert s is session and run_id == "fixture-run"
        assert kwargs["as_of"].tzinfo is UTC
        state["original_reads"] += 1
        if state["mode"] == "retained-installation":
            for _ in range(4):
                index, payloads, actual_root = history._verified_release(selected)
                assert payloads["source.py"] == b"fixture-original" and actual_root == root
                index["reader_mutation"] = True
                assert "reader_mutation" not in history._verified_release(selected)[0]
        else:
            assert history._VERIFIED_RELEASE_READ.get() is None
        return object()

    monkeypatch.setattr(runtime, "read_registered_original", original)
    # This fixture admits its artificial selected installation solely to keep
    # independently exercising the original retained-release memo boundary.
    # Production V2 rejects old retained ABI; the dedicated V2 controls test
    # that gate and the actual immutable-byte scope, not this legacy seam.
    monkeypatch.setattr(runtime, "_current_source_snapshot_abi",
        lambda *_: runtime.SOURCE_SNAPSHOT_READ_ABI)
    @contextmanager
    def source_scope(_session, **_kwargs):
        yield SimpleNamespace(snapshot=SimpleNamespace(model_dump=lambda **_: {}))
    monkeypatch.setattr(runtime, "publication_source_snapshot_read_scope", source_scope)
    monkeypatch.setattr(runtime, "read_registered_original_v2",
        lambda s, r, *, as_of, source_lease: original(s,r,as_of=as_of))

    class Collector:
        def __init__(self, s, **kwargs):
            assert s is session and kwargs["trace_slot_owned"] is True

        def __enter__(self):
            return self

        def __exit__(self, *args):
            assert history._VERIFIED_RELEASE_READ.get() is None

        def collect_sources(self, actual_root, files):
            assert actual_root == root and files == ("source.py",)
            if state["mode"] == "retained-installation":
                assert history._verified_release(selected)[1]["source.py"] == b"fixture-original"
            state["collected"] += 1
            state["collect_hook"]()

        def snapshot(self):
            return {"observed_queries_status": "complete", "unresolved": [],
                    "native_changes": 0, "sources": {"fixture": "fully-collected"}}

    # Controller02 constructs this explicit checkpoint subclass. The same
    # fixture seam must replace that constructor, without weakening scope,
    # selection/native continuity or entry/exit source assertions below.
    monkeypatch.setattr(runtime, "_ExecutionNativeCollector", Collector)

    def capture():
        preparer.capture_and_prepare(session, run_id="fixture-run", as_of=datetime.now(UTC))

    yield state, history, selected, capture
    assert history._VERIFIED_RELEASE_READ.get() is None
    assert native.markers == []
    assert not preparer.authority._issued and not list(stage.iterdir())


def test_immutable_reuse_ends_before_next_request(control):
    state, history, selected, capture = control
    for count in (2, 4):
        with pytest.raises(StopAfterCapture):
            capture()
        assert state["full_reads"] == count  # full entry and successful exit per request
        assert history._VERIFIED_RELEASE_READ.get() is None
    history._verified_release(selected)
    assert state["full_reads"] == 5
    assert state["original_reads"] == state["collected"] == 2


@pytest.mark.parametrize("selection", [None, "different-directory", "different-index"])
def test_retained_missing_or_wrong_selection_never_reads_original(control, selection):
    state, _, selected, capture = control
    if isinstance(selection, str):
        selection = runtime.normal_history._Registration(
            "other" if selection == "different-directory" else selected.directory,
            "e" * 64 if selection == "different-index" else selected.index_hash,
            selected.graph_hash, selected.selector_hash,
        )
    state["selected"] = selection
    with pytest.raises(ValueError, match="publication_capture_selected_installation_changed"):
        capture()
    assert state["full_reads"] == state["original_reads"] == state["collected"] == 0


@pytest.mark.parametrize("change", ["selection", "transaction", "original"])
def test_scope_requires_fresh_exit_after_source_collection(control, change):
    state, _, _, capture = control

    def change_identity():
        if change == "selection":
            state["selected"] = None
        elif change == "transaction":
            state["transaction"] = object()
        else:
            state["epoch"] += 1

    state["collect_hook"] = change_identity
    with pytest.raises(ValueError, match="research_history_publication_original_changed"):
        capture()
    assert state["full_reads"] == 2 and state["collected"] == 1


def test_unregistered_current_installation_keeps_original_path(control):
    state, _, _, capture = control
    state.update(mode="current-installation", selected=None)
    with pytest.raises(StopAfterCapture):
        capture()
    assert state["full_reads"] == 0
    assert state["original_reads"] == state["collected"] == 1


def test_current_authority_cannot_silently_gain_retained_selection(control):
    state, _, _, capture = control
    state["mode"] = "current-installation"
    with pytest.raises(ValueError, match="publication_capture_selected_installation_changed"):
        capture()
    assert state["original_reads"] == state["collected"] == 0

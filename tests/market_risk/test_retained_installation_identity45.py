"""Private installation identity controls; no formal DB, HTTP or execution grant.

The existing 04 fixture is explicitly a relocated controlled installation. Its
bundle seam is only used for selection controls. The separate real-39 native
publication control retains original stored bytes and hashes.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from packages.kernel.decision_hub_kernel.application import (
    research_graph_history_versions as history,
)
from packages.kernel.decision_hub_kernel.application import research_graph_recipe_v3 as current
from packages.kernel.decision_hub_kernel.application import research_recipe_archive as capture

import test_retained_v3_read as original_fixture


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def put(root: Path, relative: str, raw: bytes) -> None:
    path = capture._path(root, relative, existing=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)


@pytest.fixture
def retained(tmp_path, monkeypatch):
    # The original control predates subsequent Root upgrades. Recover each
    # exact old file from its installed frozen capsule instead of copying a
    # changed current source while claiming it is an old source.
    formal = original_fixture.FORMAL
    saved_raw = (formal / original_fixture.SAVED).read_bytes()
    saved = json.loads(saved_raw)
    selection = json.loads(next(row["payload_json"] for row in saved["roles"]
                                if row["role"] == "graph_recipe_selection"))
    capsule = formal / "packages/kernel/research_recipe_versions" / selection["dependency_hash"]
    index = json.loads((capsule / "release.json").read_bytes())
    assert index["graph_recipe_manifest"] == selection["manifest"]
    inventory = capture._io_path(tmp_path / "original-inventory")
    for name, digest in index["all_files"].items():
        path = capsule / "root" / name if name in index["stored_files"] else formal / name
        raw = capture._read(capture._io_path(path))
        assert sha(raw) == digest
        put(inventory, name, raw)
    put(inventory, original_fixture.SAVED, saved_raw)
    prior = (formal / original_fixture.OLD_RECEIPT).read_bytes()
    assert sha(prior) == original_fixture.OLD_RECEIPT_SHA
    put(inventory, original_fixture.OLD_RECEIPT, prior)
    monkeypatch.setattr(original_fixture, "FORMAL", inventory)
    yield from original_fixture.retained.__wrapped__(tmp_path, monkeypatch)


def replace_registry(root, registrations, sources, newer, monkeypatch):
    document = {"schema_version": "research-retained-recipe-registry.v2",
                "registrations": sorted(registrations,
                                        key=lambda row: (row["graph_hash"], row["directory"]))}
    raw = capture._canonical(document)
    ref = {"path": "packages/kernel/research_recipe_versions/registries/" + sha(raw) + ".json",
           "sha256": sha(raw)}
    put(root, ref["path"], raw)
    value = {**newer, "sources": {**sources, ref["path"]: ref["sha256"]},
             "evidence": {**newer["evidence"], "retained_recipe_archives_v1": {"registry": ref}}}
    raw = capture._canonical(value)
    path = root / "artifacts/runtime45/receipt.json"
    put(root, path.relative_to(root).as_posix(), raw)
    monkeypatch.setattr(history, "_REGISTRY_BINDING", None)
    history.configure_retained_recipe_registry_from_validated_release(
        receipt_path=path, receipt_sha256=sha(raw),
    )


@pytest.mark.parametrize("legacy_directory", [False, True])
def test_same_graph_exact_installations_use_distinct_private_readers(
    retained, monkeypatch, legacy_directory,
):
    root, prepared, index, first_private, db, run_id, selection = retained
    newer = json.loads((root / "artifacts/new-release/receipt.json").read_bytes())
    first_registry = json.loads((root / prepared.registry_ref["path"]).read_bytes())
    assert len(first_registry["registrations"]) == 1
    first_row = first_registry["registrations"][0]
    receipt = index["installation_binding"]["receipt"]
    pointer = index["installation_binding"]["pointer"]
    assert first_row["directory"] == capture.retained_v3_installation_directory(
        first_row["graph_hash"], receipt, pointer,
    ) == history._v3_installation_directory(first_row["graph_hash"], receipt, pointer)

    # A second exact installation keeps the complete same graph/source bytes,
    # but its receipt path is different. Repointing to the first is forbidden.
    second_receipt_name = "artifacts/second-controlled-installation/receipt.json"
    put(root, second_receipt_name, (root / receipt["path"]).read_bytes())
    second_stage = root / "artifacts/second-capture"
    second = capture.prepare_retained_graph_recipe_v3_archive(
        repository_root=root, prior_receipt_path=root / second_receipt_name,
        prior_receipt_sha256=receipt["sha256"],
        original_manifest=index["graph_recipe_manifest"], staging_root=second_stage,
    )
    for name in second.install_sources:
        put(root, name, capture._read(capture._path(second_stage, name)))
    second_registry = json.loads((root / second.registry_ref["path"]).read_bytes())
    second_row = second_registry["registrations"][0]
    assert second_row["graph_hash"] == first_row["graph_hash"]
    assert second_row["directory"] != first_row["directory"]
    sources = {**newer["sources"], **second.install_sources}
    if legacy_directory:
        # Original graph-directory capsules survive v1 -> v2 migration intact.
        old_prefix = "packages/kernel/research_recipe_versions/" + first_row["directory"] + "/"
        new_prefix = "packages/kernel/research_recipe_versions/" + first_row["graph_hash"] + "/"
        for name, digest in tuple(sources.items()):
            if name.startswith(old_prefix):
                destination = new_prefix + name.removeprefix(old_prefix)
                raw = (root / name).read_bytes()
                assert sha(raw) == digest
                put(root, destination, raw)
                sources[destination] = digest
        first_row = {**first_row, "directory": first_row["graph_hash"]}
        assert sha((root / (new_prefix + "release.json")).read_bytes()) == first_row["index_hash"]
    replace_registry(root, [first_row, second_row], sources, newer, monkeypatch)
    registrations = tuple(r for r in history._active_registrations()
                          if r.graph_hash == first_row["graph_hash"])
    assert len(registrations) == 2
    first = next(r for r in registrations if r.directory == first_row["directory"])
    second = next(r for r in registrations if r.directory == second_row["directory"])
    originals = {}
    for registration in registrations:
        exact_index, payloads, archive_root = history._verified_release(registration)
        private = history._loaded_release(registration, payloads, archive_root, v3=True)
        originals[registration.directory] = private
        assert private.registration == registration
        assert exact_index["installation_binding"]["receipt"]["sha256"] == receipt["sha256"]
        if registration == first:
            # Same explicit fixture bundle-row seam as retained_v3_read; this
            # selection control does not stand in for the real report control.
            monkeypatch.setattr(private.load(history._RECIPE), "_original_rows",
                                first_private.load(history._RECIPE)._original_rows)
    assert originals[first.directory] is not originals[second.directory]
    assert originals[first.directory].prefix != originals[second.directory].prefix
    assert history._LOADED[first.directory] is originals[first.directory]
    assert history._LOADED[second.directory] is originals[second.directory]
    # Fixture construction used a pooled connection; the read uses a new one
    # with zero lifetime changes, not that fixture writer's historical count.
    db.engine.dispose()
    with db.session() as session:
        session.connection().exec_driver_sql("BEGIN")
        session.connection().exec_driver_sql("PRAGMA query_only=ON")
        native = session.connection().connection.driver_connection
        before = native.total_changes
        assert history._original_recipe_registration(session, run_id) == first
        actual = current.read_graph_recipe_selection_v3_in_session(session, run_id)
        assert actual.receipt.model_dump(mode="json") == selection
        assert native.in_transaction and native.total_changes == before == 0
    db.engine.dispose()


@pytest.mark.parametrize("damage", ["native_schema", "manifest_schema", "output_id", "native_type"])
def test_malformed_native_selection_rejects_before_registry_lookup(tmp_path, monkeypatch, damage):
    manifest = {"schema_version": "research-graph-recipe-manifest.v3"}
    value = {"schema_version": "research-graph-recipe-selection.v3", "run_id": "private-run",
             "recipe_version": "research-graph-projection.v3", "manifest": manifest,
             "dependency_hash": sha(history._canonical(manifest).encode())}
    schema = value["schema_version"]
    if damage == "native_schema":
        schema = "research-graph-recipe-selection.v2"
    elif damage == "manifest_schema":
        value["manifest"]["schema_version"] = "research-graph-recipe-manifest.v2"
        value["dependency_hash"] = sha(history._canonical(value["manifest"]).encode())
    raw = history._canonical(value)
    digest = sha(raw.encode())
    output_id = "graph_recipe_selection_" + digest
    if damage == "output_id":
        output_id = "graph_recipe_selection_" + "0" * 64
    path = tmp_path / "only-private.sqlite3"
    with sqlite3.connect(path) as native:
        native.execute("CREATE TABLE run_role_outputs (output_id TEXT PRIMARY KEY, run_id TEXT, "
                       "role TEXT, schema_version TEXT, payload_json TEXT, payload_hash TEXT, created_at TEXT)")
        native.execute("INSERT INTO run_role_outputs VALUES (?,?,?,?,?,?,?)", (
            output_id, "private-run", "graph_recipe_selection", schema,
            raw.encode() if damage == "native_type" else raw, digest, "2026-10-10 00:00:00.000000",
        ))
    engine = create_engine("sqlite://", creator=lambda: sqlite3.connect(path.as_uri() + "?mode=ro", uri=True))
    monkeypatch.setattr(history, "_active_registrations",
                        lambda: pytest.fail("invalid native selection reached the registry"))
    try:
        with Session(engine) as session:
            session.connection().exec_driver_sql("BEGIN")
            session.connection().exec_driver_sql("PRAGMA query_only=ON")
            native = session.connection().connection.driver_connection
            with pytest.raises(ValueError, match="research_history_recipe_original_changed"):
                history._original_recipe_registration(session, "private-run")
            assert native.in_transaction and native.total_changes == 0
    finally:
        engine.dispose()


def test_same_graph_missing_exact_identity_never_selects_an_archive_and_duplicates_reject(
    tmp_path, monkeypatch,
):
    """Bounded selector protocol; real original consumer is verified separately."""
    manifest = {"schema_version": "research-graph-recipe-manifest.v3"}
    graph = sha(history._canonical(manifest).encode())
    receipt = {"path": "artifacts/original/receipt.json", "sha256": "1" * 64}
    other = {"path": "artifacts/other/receipt.json", "sha256": "1" * 64}
    pointer = {"path": "infra/original-pointer.json", "sha256": "2" * 64}
    value = {"schema_version": "research-graph-recipe-selection.v3", "run_id": "private-run",
             "recipe_version": "research-graph-projection.v3", "manifest": manifest,
             "dependency_hash": graph, "installation_receipt": receipt, "installed_pointer": pointer}
    raw = history._canonical(value)
    digest = sha(raw.encode())
    path = tmp_path / "only-private.sqlite3"
    with sqlite3.connect(path) as native:
        native.execute("CREATE TABLE run_role_outputs (output_id TEXT PRIMARY KEY, run_id TEXT, "
                       "role TEXT, schema_version TEXT, payload_json TEXT, payload_hash TEXT, created_at TEXT)")
        native.execute("INSERT INTO run_role_outputs VALUES (?,?,?,?,?,?,?)", (
            "graph_recipe_selection_" + digest, "private-run", "graph_recipe_selection",
            value["schema_version"], raw, digest, "2026-10-10 00:00:00.000000",
        ))
    engine = create_engine("sqlite://", creator=lambda: sqlite3.connect(path.as_uri() + "?mode=ro", uri=True))
    fixture_payloads = {}

    def registration(reference, legacy=False):
        index = {"schema_version": "research-retained-recipe-release.v3",
                 "graph_recipe_dependency_hash": graph, "graph_recipe_manifest": manifest,
                 "installation_binding": {"receipt": reference, "pointer": pointer}}
        encoded = capture._canonical(index)
        directory = graph if legacy else history._v3_installation_directory(graph, reference, pointer)
        name = "packages/kernel/research_recipe_versions/" + directory + "/release.json"
        fixture_payloads[name] = encoded
        return history._Registration(directory, sha(encoded), graph, "3" * 64, True)

    wrong = registration(other)
    exact = registration(receipt)
    duplicate = registration(receipt, legacy=True)
    monkeypatch.setattr(history, "_managed_read", fixture_payloads.__getitem__)
    binding = history._RegistryBinding(tmp_path / "unused-receipt", "4" * 64, "unused-registry", "5" * 64,
                                       {name: sha(body) for name, body in fixture_payloads.items()})
    monkeypatch.setattr(history, "_REGISTRY_BINDING", binding)
    try:
        with Session(engine) as session:
            session.connection().exec_driver_sql("BEGIN")
            session.connection().exec_driver_sql("PRAGMA query_only=ON")
            native = session.connection().connection.driver_connection
            monkeypatch.setattr(history, "_active_registrations", lambda: (wrong,))
            assert history._original_recipe_registration(session, "private-run") is None
            monkeypatch.setattr(history, "_active_registrations", lambda: (wrong, exact))
            assert history._original_recipe_registration(session, "private-run") == exact
            monkeypatch.setattr(history, "_active_registrations", lambda: (wrong, duplicate, exact))
            with pytest.raises(ValueError, match="research_history_recipe_ambiguous"):
                history._original_recipe_registration(session, "private-run")
            assert native.in_transaction and native.total_changes == 0
    finally:
        engine.dispose()

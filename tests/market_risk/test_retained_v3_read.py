"""Archive/install bridge controls; full formal report read remains Root's release gate.

The source fixture copies the actual frozen 04 closure to an isolated directory.
The selection test uses the existing separately-authenticated bundle-row seam;
it does not invent a native report or calibrate/qualify the old Run.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select

from packages.kernel.decision_hub_kernel.application import (
    research_graph_history_versions as history,
)
from packages.kernel.decision_hub_kernel.application import research_graph_recipe_v3 as current
from packages.kernel.decision_hub_kernel.application import research_recipe_archive as capture
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    RunRecord,
    RunRoleOutputRecord,
)

FORMAL = Path(__file__).resolve().parents[2].parent / "decision-hub"
OLD_RECEIPT = "artifacts/mr-release-b2b4-04/product-validated-builds.json"
OLD_RECEIPT_SHA = "9f7c24bfdd4a5907f51983bddc5d214005369403af7023b648008866885f01b0"
SAVED = ("artifacts/market-risk-implementation-20261007/B2/formal-real-dsh04/actual01/"
         "run_e6545db744fd46538ec870808461cd73/sqlite-originals.json")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def retained(tmp_path, monkeypatch):
    originals = json.loads((FORMAL / SAVED).read_text("utf-8-sig"))
    row = next(row for row in originals["roles"] if row["role"] == "graph_recipe_selection")
    selection = json.loads(row["payload_json"])
    manifest = selection["manifest"]
    root = capture._io_path(tmp_path / "repo")
    root.mkdir()
    names = [item["path"] for item in manifest["source_files"] + manifest["schema_files"]]
    names += [manifest["registration"]["path"], current.INSTALLED_POINTER_REF]
    for name in names:
        destination = root / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((FORMAL / name).read_bytes())
    # Controlled installation fixture at a new physical repository location;
    # every original dependency and pointer byte is copied unchanged. Only this
    # fixture's receipt root and exact selection reference are newly generated.
    original_receipt = json.loads((FORMAL / OLD_RECEIPT).read_bytes())
    assert sha((FORMAL / OLD_RECEIPT).read_bytes()) == OLD_RECEIPT_SHA
    original_receipt["repository_root"] = str(root)
    original_receipt["evidence"].pop("retained_recipe_archives_v1", None)
    receipt = capture._canonical(original_receipt)
    path = root / OLD_RECEIPT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(receipt)
    stage = root / "artifacts/archive-stage"
    prepared = capture.prepare_retained_graph_recipe_v3_archive(
        repository_root=root, prior_receipt_path=path, prior_receipt_sha256=sha(receipt),
        original_manifest=manifest, staging_root=stage,
    )
    for name in prepared.install_sources:
        destination = root / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((stage / name).read_bytes())
    newer = {**original_receipt,
             "sources": {**original_receipt["sources"], **prepared.install_sources},
             "evidence": {**original_receipt["evidence"],
                          "retained_recipe_archives_v1": {"registry": dict(prepared.registry_ref)}}}
    new_ref = "artifacts/new-release/receipt.json"
    new_raw = capture._canonical(newer)
    (root / new_ref).parent.mkdir(parents=True)
    (root / new_ref).write_bytes(new_raw)
    monkeypatch.setattr(history, "_CURRENT_ROOT", root)
    monkeypatch.setattr(history, "_RESOURCE_ROOT", root / "packages/kernel/research_recipe_versions")
    monkeypatch.setattr(history, "_REGISTRY_BINDING", None)
    monkeypatch.setattr(history, "_LOADED", {})
    history.configure_retained_recipe_registry_from_validated_release(
        receipt_path=root / new_ref, receipt_sha256=sha(new_raw),
    )
    registration = next(r for r in history._active_registrations() if r.graph_hash == prepared.archived_graph_hash)
    index, payloads, archive_root = history._verified_release(registration)
    private = history._loaded_release(registration, payloads, archive_root, v3=True)
    # Installation reference has to name this test's real receipt; recalculate
    # the canonical fixture selection identity instead of pretending it is the
    # formal original. Original formal bytes were never edited.
    selection["installation_receipt"]["sha256"] = sha(receipt)
    # _effective_hash has an explicit body; use the actual typed model fields.
    old_recipe = private.load(history._RECIPE_V3)
    provisional = old_recipe.GraphRecipeSelectionV3.model_construct(**{
        **selection,
        "original_task_ref": old_recipe.ObservedExecutionRef.model_validate_json(json.dumps(selection["original_task_ref"])),
        "original_pack_ref": old_recipe.ObservedExecutionRef.model_validate_json(json.dumps(selection["original_pack_ref"])),
        "manifest": old_recipe.GraphRecipeManifestV3.model_validate_json(json.dumps(manifest)),
        "installation_receipt": old_recipe.GraphRecipeResourceV3.model_validate(selection["installation_receipt"]),
        "installed_pointer": old_recipe.GraphRecipeResourceV3.model_validate(selection["installed_pointer"]),
        "quality_context": old_recipe.QualityProjectionContextV1.model_validate_json(json.dumps(selection["quality_context"])),
        "recorded_at": datetime.fromisoformat(selection["recorded_at"].replace("Z", "+00:00")),
    })
    selection["effective_hash"] = old_recipe._effective_hash(provisional)
    raw = old_recipe.observation_json(selection)
    row = {**row, "payload_json": raw, "payload_hash": sha(raw.encode()),
           "output_id": "graph_recipe_selection_" + sha(raw.encode())}
    db = Database(f"sqlite+pysqlite:///{tmp_path / 'only-private.sqlite3'}")
    db.create_all()
    run_id = selection["run_id"]
    with db.session() as session:
        session.add(RunRecord(run_id=run_id, event_id="controlled", status="degraded",
                              artifact_id="controlled-artifact", created_at=datetime.now(UTC),
                              updated_at=datetime.now(UTC), finished_at=datetime.now(UTC)))
        # Use actual role names from the original reference, without a guessed
        # bundle attribute or a new Factory/Graph execution.
        wanted = {selection["original_task_ref"]["role"], selection["original_pack_ref"]["role"]}
        roles = [r for r in originals["roles"] if r["role"] in wanted]
        for item in [*roles, row]:
            session.add(RunRoleOutputRecord(**{**item, "created_at": datetime.fromisoformat(item["created_at"])}))
    legacy = private.load(history._RECIPE)

    def authenticated_bundle_rows(session, run_id):
        table = private.load("packages.kernel.decision_hub_kernel.persistence.db").RunRoleOutputRecord
        rows = list(session.scalars(select(table).where(table.run_id == run_id, table.role.in_(wanted))))
        indexed = {r.role: r for r in rows}
        return indexed[selection["original_task_ref"]["role"]], indexed[selection["original_pack_ref"]["role"]]

    monkeypatch.setattr(legacy, "_original_rows", authenticated_bundle_rows)
    yield root, prepared, index, private, db, run_id, selection
    for module in history._LOADED.values():
        module.discard()
    db.engine.dispose()


def test_old_selection_uses_exact_old_installation_despite_changed_current_files(retained):
    root, prepared, index, private, db, run_id, expected = retained
    (root / current.INSTALLED_POINTER_REF).write_text("current pointer changed after capture")
    (root / "packages/runtime_adapters/dsh_runtime/profile.py").write_text("current prompt changed")
    with db.session() as session:
        session.connection().exec_driver_sql("BEGIN")
        native = session.connection().connection.driver_connection
        before = native.total_changes
        value = current.read_graph_recipe_selection_v3_in_session(session, run_id)
        assert value.receipt.model_dump(mode="json") == expected
        assert native.in_transaction and native.total_changes == before
        original = private.load(history._RECIPE_V3)
        assert original is not current
        assert original.GraphRecipeSelectionV3.__module__.startswith(private.prefix)
        with pytest.raises(PermissionError, match="read_scope_required"):
            original._installation()


def test_archive_original_damage_and_unvalidated_registry_are_rejected(retained):
    root, prepared, index, private, db, run_id, expected = retained
    original = private.root / current.INSTALLED_POINTER_REF
    raw = original.read_bytes()
    original.write_bytes(raw + b" ")
    with pytest.raises(ValueError):
        history._verified_release(private.registration)
    original.write_bytes(raw)
    registry = root / prepared.registry_ref["path"]
    registry.write_bytes(registry.read_bytes() + b" ")
    with pytest.raises(ValueError):
        history._active_registrations()


def test_active_or_dirty_run_cannot_use_terminal_bridge(retained):
    root, prepared, index, private, db, run_id, expected = retained
    with db.session() as session:
        run = session.get(RunRecord, run_id)
        run.status = "running"
    with db.session() as session:
        with pytest.raises(PermissionError, match="not_terminal"):
            current.read_graph_recipe_selection_v3_in_session(session, run_id)
    with db.session() as session:
        run = session.get(RunRecord, run_id)
        run.status = "degraded"
        with pytest.raises(ValueError, match="dirty_session"):
            current.read_graph_recipe_selection_v3_in_session(session, run_id)


def test_wrong_prior_root_and_manifest_fail_before_capture(retained, tmp_path):
    root, prepared, index, private, db, run_id, expected = retained
    bad = json.loads(json.dumps(index))
    bad["installation_binding"]["repository_root"] = str(tmp_path / "wrong")
    with pytest.raises(ValueError, match="installation_invalid"):
        capture._validate_retained_v3_installation(bad, private.payloads, expected_root=root)
    bad = json.loads(json.dumps(index))
    bad["graph_recipe_manifest"]["source_files"][0]["payload_hash"] = "0" * 64
    with pytest.raises(ValueError):
        capture._validate_retained_v3_installation(bad, private.payloads, expected_root=root)

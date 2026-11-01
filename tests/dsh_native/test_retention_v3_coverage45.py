"""Private native SQLite and file-only controls; no product DB, HTTP, build or model."""

from __future__ import annotations

import copy
import importlib.util
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from packages.kernel.decision_hub_kernel.application import research_recipe_archive as archive

ROOT = Path(__file__).resolve().parents[2]
BINDING = "packages/kernel/decision_hub_kernel/application/research_graph_binding.py"
RECIPE = "packages/kernel/decision_hub_kernel/application/research_graph_recipe_v3.py"
SELECTOR = "packages/kernel/decision_hub_kernel/application/research_context_selection.py"
RANKING = "packages/kernel/decision_hub_kernel/application/research_context_selector_v2.py"
REGISTRATION = "packages/kernel/research_recipe_versions/candidates/research-graph-projection.v3.json"
POINTER = "infra/dsh/research-graph-recipe-installed.v3.json"


def publisher():
    spec = importlib.util.spec_from_file_location("coverage45", ROOT / "infra/dsh/prepare-product-retention.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write(root: Path, name: str, raw: bytes) -> None:
    path = archive._io_path(root / name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)


def installed_fixture(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    files = {
        BINDING: b"from . import research_context_selection, research_context_selector_v2\n",
        RECIPE: b"VALUE = 'old recipe'\n",
        "packages/kernel/decision_hub_kernel/application/research_graph_projection_v3.py": b"VALUE = 'old projection'\n",
        SELECTOR: f"_IDENTITY_PATHS = {(BINDING, RECIPE, SELECTOR)!r}\n".encode(),
        RANKING: b"RANKING_RULES_REF = 'research-ranking.fixture.v1'\n",
        REGISTRATION: b'{"fixture":"registration"}\n',
        "contracts/schemas/control.json": b'{}\n',
        "packages/kernel/research_recipe_versions/candidates/schemas/quality-projection-context.v1.json": b'{}\n',
        "packages/kernel/research_recipe_versions/candidates/schemas/quality-decision.v1.json": b'{}\n',
    }
    for name, raw in files.items():
        write(root, name, raw)
    manifest = archive._planned_manifest(root, {}, {"python": "private-control"}, version=3)
    graph_hash = archive._sha(archive._canonical(manifest)[:-1])
    files[POINTER] = archive._canonical({
        "schema_version": "research-graph-recipe-installed.v3", "recipe_version": "research-graph-projection.v3",
        "lifecycle": "installed", "registration": manifest["registration"],
        "dependency_hash": graph_hash, "plugin_build_hash": "a" * 64,
    })
    write(root, POINTER, files[POINTER])
    receipt_name = "artifacts/original39.json"
    receipt = {"schema": "product-validated-builds.v1", "repository_root": str(root),
               "sources": {name: archive._sha(raw) for name, raw in files.items()},
               "artifacts": {"fixture-no-product-build": "b" * 64}, "plugin_build_hash": "a" * 64,
               "evidence": {"research_graph_recipe_v3": {
                   "installed_pointer": {"path": POINTER, "sha256": archive._sha(files[POINTER])}}}}
    receipt_raw = archive._canonical(receipt)
    write(root, receipt_name, receipt_raw)
    original = root / "artifacts/original-view"
    for name, raw in files.items():
        write(original, name, raw)
    kwargs = dict(repository_root=root, prior_receipt_path=root / receipt_name,
                  prior_receipt_sha256=archive._sha(receipt_raw), original_manifest=manifest,
                  staging_root=root / "artifacts/capture", original_source_roots=(original,))
    return root, original, files, receipt, kwargs


def capture_fixture(tmp_path: Path):
    module = publisher()
    root, original, files, receipt, kwargs = installed_fixture(tmp_path)
    prepared = archive.prepare_retained_graph_recipe_v3_archive(**kwargs)
    payloads = {name: archive._read(kwargs["staging_root"] / name) for name in prepared.install_sources}
    for name, raw in payloads.items():
        write(root, name, raw)
    registry = json.loads(payloads[prepared.registry_ref["path"]])
    directory = registry["registrations"][0]["directory"]
    index = json.loads(payloads[module.PREFIX + directory + "/release.json"])
    selection = {"schema_version": "research-graph-recipe-selection.v3", "run_id": "run-original",
                 "recipe_version": "research-graph-projection.v3", "manifest": kwargs["original_manifest"],
                 "dependency_hash": prepared.archived_graph_hash,
                 "installation_receipt": index["installation_binding"]["receipt"],
                 "installed_pointer": index["installation_binding"]["pointer"]}
    return module, root, registry, selection, payloads, receipt, kwargs, prepared


def database(root: Path, selection: dict, *, indexed: bool = True, schema: str | None = None,
             created: object = "2026-10-10T00:00:00+00:00") -> Path:
    path = root / "artifacts/private-control.sqlite3"
    encoded = archive._canonical(selection)[:-1].decode()
    digest = archive._sha(encoded.encode())
    with sqlite3.connect(path) as connection:
        constraint = ", UNIQUE(run_id,role)" if indexed else ""
        connection.execute("CREATE TABLE run_role_outputs(output_id TEXT,run_id TEXT,role TEXT,"
                           "schema_version TEXT,payload_json TEXT,payload_hash TEXT,created_at" + constraint + ")")
        if indexed:
            connection.execute("CREATE INDEX ix_run_role_outputs_run_id ON run_role_outputs(run_id)")
        connection.execute("INSERT INTO run_role_outputs VALUES(?,?,?,?,?,?,?)", (
            "graph_recipe_selection_" + digest, selection["run_id"], "graph_recipe_selection",
            schema or selection["schema_version"], encoded, digest, created))
    return path


def test_overlay_captures_exact_old_bytes_without_live_source_execution(tmp_path):
    root, original, files, receipt, kwargs = installed_fixture(tmp_path)
    write(root, BINDING, b"raise RuntimeError('must not execute current or original business code')\nfrom packages.live import only\n")
    write(root, "packages/live/only.py", b"LIVE = True\n")
    write(root, "contracts/schemas/current-only.json", b'{"current":true}\n')
    prepared = archive.prepare_retained_graph_recipe_v3_archive(**kwargs)
    registry = json.loads(archive._read(kwargs["staging_root"] / prepared.registry_ref["path"]))
    directory = registry["registrations"][0]["directory"]
    prefix = archive._BASE + directory + "/"
    index = json.loads(archive._read(kwargs["staging_root"] / (prefix + "release.json")))
    originals = {name: archive._read(kwargs["staging_root"] / (prefix + "root/" + name))
                 for name in index["stored_files"]}
    bound, pointer = archive._validate_retained_v3_installation(index, originals, expected_root=root)
    assert bound == receipt and originals[BINDING] == files[BINDING]
    assert index["installation_binding"]["repository_root"] == str(root)
    assert index["installation_binding"]["receipt"]["sha256"] == kwargs["prior_receipt_sha256"]
    assert pointer["dependency_hash"] == prepared.archived_graph_hash
    assert registry["schema_version"] == "research-retained-recipe-registry.v2"
    assert directory == archive.retained_v3_installation_directory(
        prepared.archived_graph_hash, index["installation_binding"]["receipt"], index["installation_binding"]["pointer"])
    assert "contracts/schemas/current-only.json" not in index["stored_files"]
    assert "packages/live/only.py" not in index["stored_files"]
    assert prepared.retained_authority_configured is False


def test_missing_old_overlay_file_rejects_even_when_live_file_matches(tmp_path):
    root, original, _, _, kwargs = installed_fixture(tmp_path)
    (original / BINDING).unlink()
    with pytest.raises(ValueError, match="research_recipe_archive_source_changed"):
        archive.prepare_retained_graph_recipe_v3_archive(**kwargs)
    assert not kwargs["staging_root"].exists()


@pytest.mark.parametrize("mutation", ["pointer", "declared", "scope"])
def test_overlay_hash_or_source_scope_rejects_before_publish(tmp_path, mutation):
    root, original, files, _, kwargs = installed_fixture(tmp_path)
    if mutation == "scope":
        kwargs["original_source_roots"] = (tmp_path,)
        error = "research_recipe_archive_original_source_scope_invalid"
    else:
        write(original, POINTER if mutation == "pointer" else BINDING, b"changed old original\n")
        error = "research_recipe_archive_source_changed"
    with pytest.raises(ValueError, match=error):
        archive.prepare_retained_graph_recipe_v3_archive(**kwargs)
    assert not kwargs["staging_root"].exists()


def test_selected_v3_gate_rejects_the_actual_missing_registration_failure(tmp_path):
    module, root, registry, selection, *_ = capture_fixture(tmp_path)
    path = database(root, selection)
    empty = {"schema_version": registry["schema_version"], "registrations": []}
    with pytest.raises(ValueError, match="retention_selected_v3_not_registered"):
        module.selected_v3_retention_proof(root, path, empty)
    proof = module.selected_v3_retention_proof(root, path, registry)
    assert proof["selection_count"] == 1 and proof["run_count"] == 1
    assert proof["physical_BEGIN"] and proof["total_changes"] == 0
    assert proof["indexed_run_enumeration"] and proof["indexed_run_role_lookup"]
    assert proof["rows"][0]["installation_receipt"] == selection["installation_receipt"]
    assert proof["publication_qualified"] is False


@pytest.mark.parametrize("field", ["installation_receipt", "installed_pointer"])
def test_same_graph_with_different_installation_still_rejects(tmp_path, field):
    module, root, registry, selection, *_ = capture_fixture(tmp_path)
    selection[field]["sha256"] = "0" * 64
    path = database(root, selection)
    with pytest.raises(ValueError, match="retention_selected_v3_installation_mismatch"):
        module.selected_v3_retention_proof(root, path, registry)


def test_native_raw_payload_change_and_native_type_change_reject(tmp_path):
    module, root, registry, selection, *_ = capture_fixture(tmp_path)
    path = database(root, selection)
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE run_role_outputs SET payload_json=payload_json || ' '")
    with pytest.raises(ValueError, match="retention_selection_original_invalid"):
        module.selected_v3_retention_proof(root, path, registry)
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE run_role_outputs SET created_at=NULL")
    with pytest.raises(ValueError, match="retention_selection_original_invalid"):
        module.selected_v3_retention_proof(root, path, registry)


def test_query_without_covering_index_rejects_before_observation(tmp_path):
    module, root, registry, selection, *_ = capture_fixture(tmp_path)
    with pytest.raises(ValueError, match="retention_selection_covering_index_required"):
        module.selected_v3_retention_proof(root, database(root, selection, indexed=False), registry)


def test_legacy_rows_and_zero_v3_have_explicit_empty_coverage(tmp_path):
    module, root, registry, selection, *_ = capture_fixture(tmp_path)
    manifest = copy.deepcopy(selection["manifest"])
    manifest["schema_version"] = "research-graph-recipe-manifest.v2"
    manifest.pop("registration")
    ref = {"output_id": "private-ref", "role": "private-control", "schema_version": "private-control.v2",
           "payload_hash": "a" * 64, "recorded_at": "2026-10-10T00:00:00+00:00"}
    legacy = {"schema_version": "research-graph-recipe-selection.v2", "run_id": selection["run_id"],
              "recipe_version": "research-graph-projection.v2", "manifest": manifest,
              "dependency_hash": module.digest(module.canonical(manifest)[:-1]),
              "original_task_ref": ref, "original_pack_ref": ref,
              "recorded_at": "2026-10-10T00:00:00+00:00"}
    path = database(root, legacy)
    proof = module.selected_v3_retention_proof(root, path, {"schema_version": registry["schema_version"], "registrations": []})
    assert proof["selection_count"] == 0 and proof["rows"] == []
    assert proof["rows_sha256"] == module.digest(module.canonical([]))
    assert proof["run_count"] == 1 and proof["total_changes"] == 0
    assert proof["native_selection_count"] == 1 and proof["validated_v2_count"] == 1


@pytest.mark.parametrize("native_schema", [None, "research-graph-recipe-selection.v2", "research-graph-recipe-selection.v999"])
def test_native_schema_null_unknown_or_v3_downgrade_rejects(tmp_path, native_schema):
    module, root, registry, selection, *_ = capture_fixture(tmp_path)
    path = database(root, selection)
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE run_role_outputs SET schema_version=?", (native_schema,))
    with pytest.raises(ValueError, match="retention_selection_original_invalid"):
        module.selected_v3_retention_proof(root, path, registry)


@pytest.mark.parametrize("mutation", ["payload_schema", "recipe", "manifest_schema", "rehashed_downgrade"])
def test_rehashed_native_and_payload_schema_recipe_manifest_inconsistency_rejects(tmp_path, mutation):
    module, root, registry, selection, *_ = capture_fixture(tmp_path)
    if mutation == "payload_schema":
        selection["schema_version"] = "research-graph-recipe-selection.v2"
    elif mutation == "recipe":
        selection["recipe_version"] = "research-graph-projection.v2"
    elif mutation == "manifest_schema":
        selection["manifest"]["schema_version"] = "research-graph-recipe-manifest.v2"
        selection["dependency_hash"] = module.digest(module.canonical(selection["manifest"])[:-1])
    else:
        selection["schema_version"] = "research-graph-recipe-selection.v2"
        selection["recipe_version"] = "research-graph-projection.v2"
        selection["manifest"]["schema_version"] = "research-graph-recipe-manifest.v2"
        selection["dependency_hash"] = module.digest(module.canonical(selection["manifest"])[:-1])
    path = database(root, selection, schema="research-graph-recipe-selection.v3" if mutation == "payload_schema" else None)
    with pytest.raises(ValueError, match="retention_selection_original_invalid"):
        module.selected_v3_retention_proof(root, path, registry)


def test_older_capture_authenticates_real_old_receipt_and_preserves_current_registry(tmp_path):
    module, root, registry, selection, payloads, old_receipt, kwargs, prepared = capture_fixture(tmp_path)
    inventory = {}
    for name in (module.PROVENANCE_CODEC, module.ARCHIVE_PRODUCER):
        raw = (ROOT / name).read_bytes()
        write(root, name, raw)
        inventory[name] = module.digest(raw)
    current_row = {"directory": "d" * 64, "graph_hash": "d" * 64, "index_hash": "e" * 64,
                   "selector_hash": "f" * 64, "private_original_orm": True}
    current_registry = {"schema_version": registry["schema_version"], "registrations": [current_row]}
    current_receipt = copy.deepcopy(old_receipt)
    current_receipt["plugin_build_hash"] = "2" * 64
    current_path = root / "artifacts/current44.json"
    current_path.write_bytes(module.canonical(current_receipt))
    plan_path = root / "artifacts/capture-plan.json"
    from dataclasses import asdict
    plan_path.write_bytes(module.canonical(asdict(prepared)))
    args = SimpleNamespace(captured_v3_preparation=plan_path,
                           captured_v3_preparation_sha256=module.digest(plan_path.read_bytes()),
                           captured_v3_source_root=kwargs["staging_root"],
                           prior_receipt_path=current_path, prior_receipt_sha256=module.digest(current_path.read_bytes()),
                           selection_database=database(root, selection))
    captured = module.load_v3_capture(root, args, current_receipt, current_registry, inventory)
    assert captured["older_authenticated_capture"]
    assert captured["capture_prior_receipt_sha256"] == kwargs["prior_receipt_sha256"]
    proof = module.selected_v3_retention_proof(root, args.selection_database, current_registry, captured)
    assert proof["selection_count"] == 1
    union = module.union_registries(current_registry, captured["registry"])
    assert {row["graph_hash"] for row in union["registrations"]} == {current_row["graph_hash"], prepared.archived_graph_hash}
    args.selection_database = None
    with pytest.raises(ValueError, match="retention_v3_older_capture_requires_selection_coverage"):
        module.load_v3_capture(root, args, current_receipt, current_registry, inventory)
    args.selection_database = root / "artifacts/private-control.sqlite3"
    kwargs["prior_receipt_path"].write_bytes(b"changed authentic raw receipt")
    with pytest.raises(ValueError, match="retention_v3_capture_prior_receipt_changed"):
        module.load_v3_capture(root, args, current_receipt, current_registry, inventory)


def test_older_registry_union_rejects_identity_conflict_and_unrelated_new_graph(tmp_path):
    module, _, registry, _, *_ = capture_fixture(tmp_path)
    capture_directory = registry["registrations"][0]["directory"]
    conflict = copy.deepcopy(registry)
    conflict["registrations"][0]["index_hash"] = "0" * 64
    with pytest.raises(ValueError, match="retention_v3_capture_prior_registry_changed"):
        module.compatible_v3_capture_registry(registry, conflict, capture_directory, older=True)
    extra = copy.deepcopy(registry)
    extra["registrations"].append({"directory": "f" * 64, "graph_hash": "f" * 64,
                                    "index_hash": "e" * 64, "selector_hash": "d" * 64,
                                    "private_original_orm": True})
    extra["registrations"].sort(key=lambda row: row["graph_hash"])
    with pytest.raises(ValueError, match="retention_v3_capture_prior_registry_changed"):
        module.compatible_v3_capture_registry(registry, extra, capture_directory, older=True)


def multiple_capture_fixture(tmp_path, *, old_first=False):
    from dataclasses import asdict
    module, root, registry, first_selection, first_payloads, receipt, first_kwargs, first = capture_fixture(tmp_path)
    second_files = {name: archive._read(first_kwargs["original_source_roots"][0] / name)
                    for name in receipt["sources"]}
    second_pointer = json.loads(second_files[POINTER])
    second_pointer["plugin_build_hash"] = "3" * 64
    second_files[POINTER] = module.canonical(second_pointer)
    second_receipt = copy.deepcopy(receipt)
    second_receipt["plugin_build_hash"] = "3" * 64
    second_receipt["sources"][POINTER] = module.digest(second_files[POINTER])
    second_receipt["evidence"]["research_graph_recipe_v3"]["installed_pointer"]["sha256"] = module.digest(second_files[POINTER])
    second_path = root / "artifacts/original38.json"
    second_path.write_bytes(module.canonical(second_receipt))
    second_original = root / "artifacts/second-original-view"
    for name, raw in second_files.items():
        write(second_original, name, raw)
    second_kwargs = {**first_kwargs, "prior_receipt_path": second_path,
                     "prior_receipt_sha256": module.digest(second_path.read_bytes()),
                     "original_source_roots": (second_original,), "staging_root": root / "artifacts/second-capture"}
    second = archive.prepare_retained_graph_recipe_v3_archive(**second_kwargs)
    assert first.archived_graph_hash == second.archived_graph_hash
    first_plan = asdict(first)
    first_stage = first_kwargs["staging_root"]
    if old_first:
        row = copy.deepcopy(registry["registrations"][0])
        old_prefix, new_prefix = module.PREFIX + row["directory"] + "/", module.PREFIX + row["graph_hash"] + "/"
        row["directory"] = row["graph_hash"]
        legacy_registry = {"schema_version": "research-retained-recipe-registry.v1", "registrations": [row]}
        legacy_raw = module.canonical(legacy_registry)
        legacy_sha = module.digest(legacy_raw)
        legacy_ref = {"path": module.PREFIX + "registries/" + legacy_sha + ".json", "sha256": legacy_sha}
        legacy_payloads = {new_prefix + name.removeprefix(old_prefix): raw for name, raw in first_payloads.items()
                           if name.startswith(old_prefix)}
        legacy_payloads[legacy_ref["path"]] = legacy_raw
        first_stage = root / "artifacts/legacy-capture"
        for name, raw in legacy_payloads.items():
            write(first_stage, name, raw)
        first_plan["registry_ref"] = legacy_ref
        first_plan["install_sources"] = {name: module.digest(raw) for name, raw in legacy_payloads.items()}
    first_plan_path, second_plan_path = root / "artifacts/first-plan.json", root / "artifacts/second-plan.json"
    first_plan_path.write_bytes(module.canonical(first_plan))
    second_plan_path.write_bytes(module.canonical(asdict(second)))
    wrapper = {"schema_version": "product-retained-v3-capture-set.v1", "captures": [
        {"preparation": {"path": first_plan_path.relative_to(root).as_posix(), "sha256": module.digest(first_plan_path.read_bytes())},
         "source_root": first_stage.relative_to(root).as_posix()},
        {"preparation": {"path": second_plan_path.relative_to(root).as_posix(), "sha256": module.digest(second_plan_path.read_bytes())},
         "source_root": second_kwargs["staging_root"].relative_to(root).as_posix()},
    ]}
    wrapper_path = root / "artifacts/wrapper.json"
    wrapper_path.write_bytes(module.canonical(wrapper))
    second_selection = {**first_selection, "run_id": "run-second",
                        "installation_receipt": {"path": second_path.relative_to(root).as_posix(), "sha256": second_kwargs["prior_receipt_sha256"]},
                        "installed_pointer": second_receipt["evidence"]["research_graph_recipe_v3"]["installed_pointer"]}
    path = database(root, first_selection)
    encoded = module.canonical(second_selection)[:-1].decode()
    sha = module.digest(encoded.encode())
    with sqlite3.connect(path) as connection:
        connection.execute("INSERT INTO run_role_outputs VALUES(?,?,?,?,?,?,?)", (
            "graph_recipe_selection_" + sha, second_selection["run_id"], "graph_recipe_selection",
            second_selection["schema_version"], encoded, sha, "2026-10-10T00:00:00+00:00"))
    inventory = {}
    for name in (module.PROVENANCE_CODEC, module.ARCHIVE_PRODUCER):
        raw = (ROOT / name).read_bytes()
        write(root, name, raw)
        inventory[name] = module.digest(raw)
    args = SimpleNamespace(captured_v3_preparation=wrapper_path,
                           captured_v3_preparation_sha256=module.digest(wrapper_path.read_bytes()),
                           captured_v3_source_root=root / "artifacts", prior_receipt_path=first_kwargs["prior_receipt_path"],
                           prior_receipt_sha256=first_kwargs["prior_receipt_sha256"], selection_database=path)
    empty = {"schema_version": "research-retained-recipe-registry.v1", "registrations": []}
    return module, root, args, receipt, empty, inventory, wrapper, second_selection


@pytest.mark.parametrize("old_first", [False, True])
def test_explicit_multiple_captures_preserve_two_installations_of_same_graph(tmp_path, old_first):
    module, root, args, receipt, empty, inventory, _, second = multiple_capture_fixture(tmp_path, old_first=old_first)
    captured = module.load_v3_capture(root, args, receipt, empty, inventory)
    assert len(captured["members"]) == 2
    rows = captured["registry"]["registrations"]
    assert captured["registry"]["schema_version"] == "research-retained-recipe-registry.v2"
    assert rows[0]["graph_hash"] == rows[1]["graph_hash"]
    assert rows[0]["directory"] != rows[1]["directory"]
    proof = module.selected_v3_retention_proof(root, args.selection_database, empty, captured)
    assert proof["selection_count"] == 2
    assert len({row["archive_directory"] for row in proof["rows"]}) == 2
    module.verify_v3_capture(root, captured)
    installed = {}
    union, ref, extras = module.merge_v3_capture(root, root / "artifacts/merged", empty, installed, captured, install_candidates=False)
    assert union == captured["registry"]
    assert extras[ref["path"]] == module.digest(module.canonical(union))


@pytest.mark.parametrize("mutation", ["member_hash", "member_drift", "wrapper_drift", "scope", "duplicate", "nested"])
def test_explicit_capture_set_rejects_tamper_scope_duplicate_and_nested_input(tmp_path, mutation):
    module, root, args, receipt, empty, inventory, wrapper, _ = multiple_capture_fixture(tmp_path)
    if mutation in {"member_drift", "wrapper_drift"}:
        captured = module.load_v3_capture(root, args, receipt, empty, inventory)
        path = args.captured_v3_preparation if mutation == "wrapper_drift" else root / wrapper["captures"][0]["preparation"]["path"]
        path.write_bytes(b"changed after full original read")
        with pytest.raises(ValueError, match="retention_v3_capture_changed_during_prepare"):
            module.verify_v3_capture(root, captured)
        return
    if mutation == "member_hash":
        wrapper["captures"][0]["preparation"]["sha256"] = "0" * 64
        error = "retention_v3_capture_preparation_changed"
    elif mutation == "scope":
        wrapper["captures"][0]["source_root"] = "packages"
        error = "retention_v3_capture_set_scope_invalid"
    elif mutation == "duplicate":
        wrapper["captures"].append(copy.deepcopy(wrapper["captures"][0]))
        error = "retention_v3_capture_set_scope_invalid"
    else:
        nested_path = root / wrapper["captures"][0]["preparation"]["path"]
        nested_path.write_bytes(module.canonical({"schema_version": "product-retained-v3-capture-set.v1", "captures": []}))
        wrapper["captures"][0]["preparation"]["sha256"] = module.digest(nested_path.read_bytes())
        error = "retention_v3_capture_set_nested_forbidden"
    args.captured_v3_preparation.write_bytes(module.canonical(wrapper))
    args.captured_v3_preparation_sha256 = module.digest(args.captured_v3_preparation.read_bytes())
    with pytest.raises(ValueError, match=error):
        module.load_v3_capture(root, args, receipt, empty, inventory)


def test_capture_identity_directory_cannot_be_relabelled_with_self_rehash(tmp_path):
    module, root, registry, selection, payloads, receipt, kwargs, prepared = capture_fixture(tmp_path)
    from dataclasses import asdict
    plan = asdict(prepared)
    row = registry["registrations"][0]
    old_prefix, new_prefix = module.PREFIX + row["directory"] + "/", module.PREFIX + "0" * 64 + "/"
    row["directory"] = "0" * 64
    changed_raw = module.canonical(registry)
    sha = module.digest(changed_raw)
    ref = {"path": module.PREFIX + "registries/" + sha + ".json", "sha256": sha}
    changed = {new_prefix + name.removeprefix(old_prefix): content for name, content in payloads.items() if name.startswith(old_prefix)}
    changed[ref["path"]] = changed_raw
    stage = root / "artifacts/relabeled"
    for name, content in changed.items():
        write(stage, name, content)
    plan["registry_ref"], plan["install_sources"] = ref, {name: module.digest(content) for name, content in changed.items()}
    path = root / "artifacts/relabeled-plan.json"
    path.write_bytes(module.canonical(plan))
    inventory = {}
    for name in (module.PROVENANCE_CODEC, module.ARCHIVE_PRODUCER):
        raw = (ROOT / name).read_bytes()
        write(root, name, raw)
        inventory[name] = module.digest(raw)
    args = SimpleNamespace(captured_v3_preparation=path, captured_v3_preparation_sha256=module.digest(path.read_bytes()),
                           captured_v3_source_root=stage, prior_receipt_path=kwargs["prior_receipt_path"],
                           prior_receipt_sha256=kwargs["prior_receipt_sha256"], selection_database=database(root, selection))
    with pytest.raises(ValueError, match="retention_v3_capture_directory_invalid"):
        module.load_v3_capture(root, args, receipt, {"schema_version": "research-retained-recipe-registry.v1", "registrations": []}, inventory)


def test_prior_registry_v2_roundtrip_reverifies_both_installations_and_rejects_relabel(tmp_path):
    module, root, args, receipt, empty, inventory, _, _ = multiple_capture_fixture(tmp_path, old_first=True)
    captured = module.load_v3_capture(root, args, receipt, empty, inventory)
    registry, ref, extras = module.merge_v3_capture(root, root / "artifacts/private-next-release", empty, {}, captured, install_candidates=True)
    next_receipt = copy.deepcopy(receipt)
    next_receipt["sources"].update(extras)
    next_receipt["evidence"]["retained_recipe_archives_v1"] = {"registry": ref}
    assert archive._registry_from_prior(root, next_receipt) == registry["registrations"]
    compound = next(row for row in registry["registrations"] if row["directory"] != row["graph_hash"])
    bad_registry = copy.deepcopy(registry)
    bad_row = next(row for row in bad_registry["registrations"] if row["directory"] == compound["directory"])
    bad_row["directory"] = "0" * 64
    old_prefix, new_prefix = module.PREFIX + compound["directory"] + "/", module.PREFIX + "0" * 64 + "/"
    for name, raw in captured["payloads"].items():
        if name.startswith(old_prefix):
            changed_name = new_prefix + name.removeprefix(old_prefix)
            write(root, changed_name, raw)
            next_receipt["sources"][changed_name] = module.digest(raw)
    bad_registry["registrations"].sort(key=lambda row: (row["graph_hash"], row["directory"]))
    bad_raw = module.canonical(bad_registry)
    bad_sha = module.digest(bad_raw)
    bad_name = module.PREFIX + "registries/" + bad_sha + ".json"
    write(root, bad_name, bad_raw)
    next_receipt["sources"][bad_name] = bad_sha
    next_receipt["evidence"]["retained_recipe_archives_v1"]["registry"] = {"path": bad_name, "sha256": bad_sha}
    with pytest.raises(ValueError, match="research_recipe_archive_v3_directory_invalid"):
        archive._registry_from_prior(root, next_receipt)


def test_prestart_current44_validator_authenticates_mixed_capture_set_without_source_promotion(tmp_path):
    module, root, args, receipt, empty, inventory, _, _ = multiple_capture_fixture(tmp_path, old_first=True)
    # An existing immutable source read only; never import/execute old business code.
    actual_root44 = (ROOT.parent / "decision-hub/artifacts/mr-release-b2b4-44/retention/original-view"
                     / module.ARCHIVE_PRODUCER)
    prior_validator = archive._read(actual_root44)
    assert module.digest(prior_validator) == "23bbdbcf8b7264ba3015b706acc59944c8d3f549398ce005e85d31ed1bf10feb"
    assert b"def retained_v3_installation_directory" not in prior_validator
    write(root, module.ARCHIVE_PRODUCER, prior_validator)
    inventory[module.ARCHIVE_PRODUCER] = module.digest(prior_validator)
    captured = module.load_v3_capture(root, args, receipt, empty, inventory)
    proof = module.selected_v3_retention_proof(root, args.selection_database, empty, captured)
    assert proof["selection_count"] == 2
    assert captured["producer_raw"] == prior_validator
    module.verify_v3_capture(root, captured)

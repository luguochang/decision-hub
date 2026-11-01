"""Constructed metadata/file controls, not real component financial qualification."""

import importlib.util
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from apps.hub_worker import market_component_bootstrap
from apps.hub_worker.market_component_versions import _runtime
from packages.kernel.decision_hub_kernel.ports.component_registry import ComponentManifest

TOOL = Path(__file__).resolve().parents[2] / "infra/dsh/plan-retained-component-catalog.py"
SPEC = importlib.util.spec_from_file_location("component_catalog_plan_control", TOOL)
assert SPEC and SPEC.loader
planner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(planner)


def manifest(version="installed_control"):
    return ComponentManifest(
        version_id=version,
        kind="model",
        component_ref="control.v1",
        scope_id="constructed:scope",
        input_contract="in.v1",
        output_contract="out.v1",
        code_hash="a" * 64,
        resource_hash="b" * 64,
        parameters={},
    ).model_dump(mode="json")


def archive(ref, versions, closure):
    return {
        "reference": {"index_ref": ref, "index_sha256": "1" * 64},
        "components": [{"manifest": manifest(v)} for v in versions],
        "closure_hash": closure,
    }


def test_original_order_and_complete_first_closure_are_preserved():
    first = archive("first", ["v1"], "old-original-closure")
    duplicate = archive("later", ["v1"], "different-later-closure")
    new = archive("new", ["v2"], "new-original-closure")
    plan = planner.first_provider_plan([first, duplicate, new], duplicate, {"v1", "v2"})
    assert plan["archives"] == [first["reference"], new["reference"], duplicate["reference"]]
    assert plan["first_providers"]["v1"]["closure_hash"] == "old-original-closure"
    assert plan["catalog_version_count"] == 2


def test_missing_reference_and_same_version_identity_conflict_close():
    first = archive("first", ["v1"], "closure")
    with pytest.raises(ValueError, match="bound_version_missing"):
        planner.first_provider_plan([first], first, {"unavailable-version"})
    changed = archive("later", ["v1"], "other-closure")
    changed["components"][0]["manifest"]["code_hash"] = "c" * 64
    with pytest.raises(ValueError, match="version_identity_conflict"):
        planner.first_provider_plan([first, changed], first, {"v1"})


@pytest.fixture
def originals(tmp_path, monkeypatch):
    root = tmp_path / "repository"
    root.mkdir()
    body = b"def control():\n    return 1\n"
    (root / "control.py").write_bytes(body)
    namespace = {}
    exec(compile(body, str(root / "control.py"), "exec"), namespace)
    leaf_hash = planner.digest(body)
    pins = {}
    refs = []
    preparations = []
    declaration = ComponentManifest.model_validate(manifest())
    for timestamp in ("20261008T000000001Z", "20261008T000000002Z"):
        prefix = f"packages/kernel/market_component_versions/{timestamp}"
        directory = root / prefix
        (directory / "root").mkdir(parents=True)
        (directory / "root/control.py").write_bytes(body)
        binding = {"control.py": leaf_hash}
        binding_raw = planner.canonical(binding)
        (directory / "source-binding.json").write_bytes(binding_raw)
        index = {
            "schema_version": "market-component-retained-release.v1",
            "runtime": _runtime(),
            "source_binding_mode": "managed_candidate_inventory",
            "source_binding_sha256": planner.digest(binding_raw),
            "files": binding,
            "components": [
                {
                    "manifest": declaration.model_dump(mode="json"),
                    "module": "control",
                    "function": "control",
                    "code_files": {"control.py": "control.py"},
                    "resource_files": {},
                }
            ],
        }
        index_raw = planner.canonical(index)
        (directory / "index.json").write_bytes(index_raw)
        ref = {"index_ref": prefix + "/index.json", "index_sha256": planner.digest(index_raw)}
        additions = {
            ref["index_ref"]: ref["index_sha256"],
            prefix + "/source-binding.json": planner.digest(binding_raw),
            prefix + "/root/control.py": leaf_hash,
        }
        pins.update(additions)
        refs.append(ref)
        preparations.append({**ref, "source_additions": additions})
    receipt = {
        "schema": "product-validated-builds.v1",
        "repository_root": str(root),
        "sources": pins,
        "evidence": {"retained_market_components_v1": {"archives": refs}},
    }
    prior = root / "prior.json"
    prior.write_bytes(planner.canonical(receipt))
    current = root / "current-preparation.json"
    current.write_bytes(planner.canonical(preparations[-1]))
    database = root / "private.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript("""
          CREATE TABLE market_component_versions(version_id,payload_json,payload_hash);
          CREATE TABLE market_component_pointers(version_id);
          CREATE TABLE market_component_reviews(version_id);
          CREATE TABLE market_component_shadows(version_id,payload_json);
          CREATE TABLE market_component_audits(audit_id,payload_json);
        """)
        connection.execute(
            "INSERT INTO market_component_versions VALUES(?,?,?)",
            (declaration.version_id, declaration.model_dump_json(), declaration.payload_hash),
        )
    monkeypatch.setattr(
        market_component_bootstrap,
        "declare_market_components",
        lambda: (
            SimpleNamespace(
                version_id=declaration.version_id,
                manifest=lambda: declaration,
                callback=namespace["control"],
                code_files={"control.py": root / "control.py"},
                resource_files={},
            ),
        ),
    )
    args = dict(
        root=root,
        prior_path=prior,
        prior_hash=planner.digest(prior.read_bytes()),
        database=database,
        current_path=current,
        current_hash=planner.digest(current.read_bytes()),
    )
    return args, refs, receipt


def test_complete_leaf_pins_and_current_preserved_readonly(originals):
    args, refs, _ = originals
    result = planner.produce(**args)
    assert result["archives"] == refs
    assert result["readonly_snapshot"]["total_changes"] == 0
    assert result["readonly_snapshot"]["rollback"]
    assert result["first_providers"]["installed_control"]["index_ref"] == refs[0]["index_ref"]


def test_tampered_selected_leaf_and_missing_direct_pin_reject(originals):
    args, refs, receipt = originals
    leaf = args["root"] / refs[0]["index_ref"].replace("index.json", "root/control.py")
    before = leaf.read_bytes()
    leaf.write_bytes(before.replace(b"1", b"2"))
    with pytest.raises(ValueError, match="original_changed"):
        planner.produce(**args)
    leaf.write_bytes(before)
    del receipt["sources"][leaf.relative_to(args["root"]).as_posix()]
    args["prior_path"].write_bytes(planner.canonical(receipt))
    args["prior_hash"] = planner.digest(args["prior_path"].read_bytes())
    with pytest.raises(ValueError, match="leaf_unbound"):
        planner.produce(**args)


def test_reordered_receipt_stale_hash_and_unknown_live_declaration_reject(originals, monkeypatch):
    args, _, receipt = originals
    original = args["prior_path"].read_bytes()
    receipt["evidence"]["retained_market_components_v1"]["archives"].reverse()
    args["prior_path"].write_bytes(planner.canonical(receipt))
    with pytest.raises(ValueError, match="release_unbound"):
        planner.produce(**args)
    args["prior_path"].write_bytes(original)
    monkeypatch.setattr(market_component_bootstrap, "declare_market_components", lambda: ())
    with pytest.raises(ValueError, match="current_declaration_mismatch"):
        planner.produce(**args)


def test_unknown_registered_or_consumed_version_closes(originals):
    args, _, _ = originals
    with sqlite3.connect(args["database"]) as connection:
        connection.execute(
            "INSERT INTO market_component_pointers VALUES(?)", ("missing-bound-version",)
        )
    with pytest.raises(ValueError, match="bound_version_missing"):
        planner.produce(**args)
    with sqlite3.connect(args["database"]) as connection:
        connection.execute("DELETE FROM market_component_pointers")
        connection.execute(
            "INSERT INTO market_component_audits VALUES(?,?)",
            (
                "cconsume_control",
                json.dumps(
                    {"state": "selected", "selection": {"status": "selected", "manifest": None}}
                ),
            ),
        )
    with pytest.raises(ValueError, match="consumption_identity_unknown"):
        planner.produce(**args)

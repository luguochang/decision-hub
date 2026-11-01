"""Bounded file-only capture/union tests; no product import, SQL, or build."""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]


def publisher():
    spec = importlib.util.spec_from_file_location(
        "product_capture_retention", ROOT / "infra/dsh/prepare-product-retention.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write(module, root, name, raw):
    module.publish(module.scoped(root, name), raw)
    return module.digest(raw)


def fixture(tmp_path):
    module = publisher()
    root = tmp_path / "repo"
    root.mkdir()
    capture_root = root / "artifacts/capture"
    producer_raw = (ROOT / module.ARCHIVE_PRODUCER).read_bytes()
    producer_hash = write(module, root, module.ARCHIVE_PRODUCER, producer_raw)
    registration_name = module.PREFIX + "candidates/research-graph-projection.v3.json"
    pointer_name = "infra/dsh/research-graph-recipe-installed.v3.json"
    prior_name = "artifacts/prior.json"
    originals = {
        "packages/example.py": b"# isolated codec fixture, no execution\n",
        "contracts/example.json": b'{"fixture":true}\n',
        registration_name: b'{"fixture":"registration"}\n',
    }
    manifest = {
        "schema_version": "research-graph-recipe-manifest.v3",
        "source_files": [{"path": "packages/example.py", "payload_hash": module.digest(originals["packages/example.py"])}],
        "schema_files": [{"path": "contracts/example.json", "payload_hash": module.digest(originals["contracts/example.json"])}],
        "registration": {"path": registration_name, "sha256": module.digest(originals[registration_name])},
        "runtime_versions": {"fixture": "not-native"},
    }
    graph_hash = module.digest(module.canonical(manifest)[:-1])
    originals[pointer_name] = module.canonical({
        "schema_version": "research-graph-recipe-installed.v3", "recipe_version": "research-graph-projection.v3",
        "lifecycle": "installed", "registration": manifest["registration"],
        "dependency_hash": graph_hash, "plugin_build_hash": "a" * 64,
    })
    prior = {
        "schema": "product-validated-builds.v1", "repository_root": str(root),
        "sources": {name: module.digest(raw) for name, raw in originals.items()},
        "artifacts": {"fixture-not-a-build": "b" * 64}, "plugin_build_hash": "a" * 64,
        "evidence": {"research_graph_recipe_v3": {
            "installed_pointer": {"path": pointer_name, "sha256": module.digest(originals[pointer_name])}}},
    }
    originals[prior_name] = module.canonical(prior)
    prior_hash = write(module, root, prior_name, originals[prior_name])
    index = {
        "schema_version": "research-retained-recipe-release.v2", "graph_recipe_dependency_hash": graph_hash,
        "graph_recipe_manifest": manifest, "selector_identity": {"implementation_hash": "c" * 64},
        "selector_identity_files": {}, "stored_files": sorted(originals), "shared_auxiliary_files": {},
        "all_files": {name: module.digest(originals[name]) for name in sorted(originals)},
        "orm_mode": "private_original_orm.v1", "installation_binding": {
            "receipt": {"path": prior_name, "sha256": prior_hash},
            "pointer": {"path": pointer_name, "sha256": module.digest(originals[pointer_name])},
            "repository_root": str(root)},
    }
    prefix = module.PREFIX + graph_hash + "/"
    index_raw = module.canonical(index)
    row = {"directory": graph_hash, "graph_hash": graph_hash, "index_hash": module.digest(index_raw),
           "selector_hash": "c" * 64, "private_original_orm": True}
    registry = {"schema_version": "research-retained-recipe-registry.v1", "registrations": [row]}
    registry_raw = module.canonical(registry)
    registry_hash = module.digest(registry_raw)
    registry_name = module.PREFIX + "registries/" + registry_hash + ".json"
    payloads = {prefix + "root/" + name: raw for name, raw in originals.items()}
    payloads[prefix + "release.json"] = index_raw
    payloads[registry_name] = registry_raw
    installs = {name: write(module, capture_root, name, raw) for name, raw in payloads.items()}
    plan = {"changed": True, "archived_graph_hash": graph_hash,
            "registry_ref": {"path": registry_name, "sha256": registry_hash},
            "install_sources": installs, "shared_source_guards": {},
            "prior_receipt_sha256": prior_hash, "retained_authority_configured": False}
    plan_path = root / "artifacts/prepare.json"
    plan_path.write_bytes(module.canonical(plan))
    args = SimpleNamespace(captured_v3_preparation=plan_path,
                           captured_v3_preparation_sha256=module.digest(plan_path.read_bytes()),
                           captured_v3_source_root=capture_root,
                           prior_receipt_path=root / prior_name, prior_receipt_sha256=prior_hash)
    old_registry = {"schema_version": registry["schema_version"], "registrations": []}
    return module, root, args, prior, old_registry, {module.ARCHIVE_PRODUCER: producer_hash}, plan


def test_capture_union_installs_all_resources_and_binds_new_registry_before_build(tmp_path):
    module, root, args, prior, old, inventory, plan = fixture(tmp_path)
    prior_bytes = args.prior_receipt_path.read_bytes()
    captured = module.load_v3_capture(root, args, prior, old, inventory)
    v2_row = {"directory": "d" * 64, "graph_hash": "d" * 64, "index_hash": "e" * 64,
              "selector_hash": "f" * 64, "private_original_orm": True}
    v2_registry = {"schema_version": old["schema_version"], "registrations": [v2_row]}
    installed = {}
    registry, ref, extras = module.merge_v3_capture(
        root, root / "artifacts/union", v2_registry, installed, captured, install_candidates=True
    )
    assert len(registry["registrations"]) == 2
    assert extras == installed
    assert set(plan["install_sources"]).issubset(extras)
    assert ref["sha256"] == module.digest(module.read(module.scoped(root, ref["path"])))
    assert json.loads(module.read(module.scoped(root, ref["path"]))) == registry
    assert all(module.digest(module.read(module.scoped(root, name))) == sha for name, sha in extras.items())
    module.verify_v3_capture(root, captured)
    assert args.prior_receipt_path.read_bytes() == prior_bytes
    legacy_args = SimpleNamespace(captured_v3_preparation=None, captured_v3_preparation_sha256=None,
                                  captured_v3_source_root=None)
    assert module.load_v3_capture(root, legacy_args, prior, old, inventory) is None


def test_same_graph_different_registration_rejects_before_install(tmp_path):
    module, root, args, prior, old, inventory, _ = fixture(tmp_path)
    captured = module.load_v3_capture(root, args, prior, old, inventory)
    bad = json.loads(json.dumps(captured["registry"]))
    bad["registrations"][0]["index_hash"] = "0" * 64
    destination = root / "artifacts/not-published"
    with pytest.raises(ValueError, match="retention_registry_union_conflict"):
        module.merge_v3_capture(root, destination, bad, {}, captured, install_candidates=True)
    assert not destination.exists()


def test_capture_rehash_cannot_rewrite_original_manifest_or_prior_binding(tmp_path):
    module, root, args, prior, old, inventory, plan = fixture(tmp_path)
    name = next(name for name in plan["install_sources"] if name.endswith("root/packages/example.py"))
    changed = b"# altered original, falsely self-rehashed capture\n"
    module.io_path(module.scoped(args.captured_v3_source_root, name)).write_bytes(changed)
    plan["install_sources"][name] = module.digest(changed)
    args.captured_v3_preparation.write_bytes(module.canonical(plan))
    args.captured_v3_preparation_sha256 = module.digest(args.captured_v3_preparation.read_bytes())
    with pytest.raises(ValueError, match="research_recipe_archive_v3_original_changed"):
        module.load_v3_capture(root, args, prior, old, inventory)


def test_final_capture_recheck_and_incomplete_arguments_fail_closed(tmp_path):
    module, root, args, prior, old, inventory, _ = fixture(tmp_path)
    captured = module.load_v3_capture(root, args, prior, old, inventory)
    args.captured_v3_preparation.write_bytes(b"changed after original read")
    with pytest.raises(ValueError, match="retention_v3_capture_changed_during_prepare"):
        module.verify_v3_capture(root, captured)
    args.captured_v3_preparation_sha256 = None
    with pytest.raises(ValueError, match="retention_v3_capture_arguments_incomplete"):
        module.load_v3_capture(root, args, prior, old, inventory)


@pytest.mark.parametrize("count,oversized", [(12000, False), (24000, True)])
def test_receipt_size_estimate_is_compressed_and_precedes_builds(count, oversized):
    powershell = shutil.which("pwsh") or shutil.which("powershell.exe")
    if powershell is None:
        pytest.skip("PowerShell size helper")
    source_path = ROOT / "infra/dsh/build-product.ps1"
    path = str(source_path).replace("'", "''")
    shared_path = str(ROOT / "infra/dsh/product-build-receipt.ps1").replace("'", "''")
    script = (
        f". '{shared_path}'; "
        "$tokens=$null; $errors=$null; "
        f"$ast=[System.Management.Automation.Language.Parser]::ParseFile('{path}',[ref]$tokens,[ref]$errors); "
        "if($errors.Count){throw 'builder_parse_failed'}; "
        "$fn=$ast.Find({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] "
        "-and $node.Name -ceq 'Get-ProductReceiptSizeEstimate'},$true); "
        "Invoke-Expression $fn.Extent.Text; $repoRoot='C:/isolated-receipt-fixture'; $launchId='fixture'; "
        f"$map=@{{}}; for($i=0;$i -lt {count};$i++){{$map['packages/archive/'+$i.ToString('D8')+'.py']='a'*64}}; "
        "$size=Get-ProductReceiptSizeEstimate -SourceMap $map -PriorArtifactMap @{} -PluginHash ('b'*64) "
        "-Registry @{path='fixture';sha256=('c'*64)} -Retention @{path='fixture';sha256=('d'*64)} "
        "-SpotInstallation $null -ComponentArchives @(); "
        f"if(($size -gt 2097152) -ne ${str(oversized).lower()}){{throw 'size_boundary_wrong'}}; 'size-boundary-pass'"
    )
    result = subprocess.run([powershell, "-NoProfile", "-NonInteractive", "-Command", script],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    source = source_path.read_text()
    assert source.index("$sourceBefore = Get-ProductBuildSourceMap") < source.index("$estimatedSize =")
    assert source.index("product_build_receipt_estimate_oversized") < source.index("$stage.ui_build_attempts = 1")

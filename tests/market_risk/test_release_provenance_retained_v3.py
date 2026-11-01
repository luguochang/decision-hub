"""File-only B capacity controls; fixtures grant no installed/product authority.

The fixture's minimal AST closure exercises the capture protocol, while the
existing retained-v3 read controls exercise the actual frozen business reader.
No HTTP, native/model execution or formal database access is used here.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from packages.kernel.decision_hub_kernel.application import product_release_provenance as codec
from packages.kernel.decision_hub_kernel.application import (
    research_graph_history_versions as history,
)
from packages.kernel.decision_hub_kernel.application import research_graph_recipe_v3 as current
from packages.kernel.decision_hub_kernel.application import research_recipe_archive as capture

# These controls deliberately call the private authentication boundaries.
# pyright: reportPrivateUsage=false

Files = tuple[Path, dict[str, Any], dict[str, Any], bytes, dict[str, bytes]]

DEV = Path(__file__).resolve().parents[2]
FORMAL = DEV.parent / "decision-hub"
APP = "packages/kernel/decision_hub_kernel/application/"
RECEIPT = "artifacts/prior/receipt.json"
REGISTRY = "packages/kernel/research_recipe_versions/registries/"


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def put(root: Path, name: str, raw: bytes) -> Path:
    path = capture._path(root, name, existing=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return path


def v2_receipt(root: Path, original: dict[str, Any]) -> tuple[bytes, dict[str, bytes]]:
    value, transport = codec.encode_historical_sources(
        original["sources"], read_resource=lambda name: capture._read(capture._path(root, name))
    )
    value.update({key: item for key, item in original.items() if key != "sources"})
    value["schema"] = "product-validated-builds.v2"
    for name, raw in transport.items():
        put(root, name, raw)
    return codec.canonical(value), transport


@pytest.fixture
def files(tmp_path: Path) -> Files:
    root = capture._io_path(tmp_path / "repo")
    root.mkdir()
    sources = {
        current.SOURCE_REF: (DEV / current.SOURCE_REF).read_bytes(),
        codec.SOURCE_REF: (DEV / codec.SOURCE_REF).read_bytes(),
        APP + "research_graph_binding.py": (
            b"from . import research_context_selection, research_context_selector_v2\n"
        ),
        APP + "research_graph_projection_v3.py": b"# bounded fixture projection\n",
        APP + "research_context_selection.py": (
            "_IDENTITY_PATHS = (\n"
            f"'{APP}research_graph_binding.py',\n"
            f"'{APP}research_context_selector_v2.py',\n"
            f"'{APP}research_context_selection.py')\n"
        ).encode(),
        APP + "research_context_selector_v2.py": b"RANKING_RULES_REF = 'fixture-ranking.v1'\n",
        "contracts/schemas/fixture.json": b"{}\n",
        current.REGISTRATION_REF: b"{}\n",
    }
    for name, raw in sources.items():
        put(root, name, raw)
    manifest = capture._planned_manifest(root, {}, {}, version=3)
    pointer = {
        "schema_version": "research-graph-recipe-installed.v3",
        "recipe_version": "research-graph-projection.v3", "lifecycle": "installed",
        "registration": manifest["registration"],
        "dependency_hash": sha(capture._canonical(manifest)[:-1]),
        "plugin_build_hash": "1" * 64,
    }
    sources[current.INSTALLED_POINTER_REF] = capture._canonical(pointer)
    put(root, current.INSTALLED_POINTER_REF, sources[current.INSTALLED_POINTER_REF])
    # Explicit synthetic legacy owner tests transport ownership without claiming
    # this minimal owner is an executable historical business capsule.
    owner = "packages/kernel/research_recipe_versions/" + "a" * 64 + "/release.json"
    sources[owner] = codec.canonical({
        "schema_version": "research-retained-recipe-release.v1",
        "graph_recipe_dependency_hash": "a" * 64,
    })
    put(root, owner, sources[owner])
    registry_raw = capture._canonical({
        "schema_version": "research-retained-recipe-registry.v1", "registrations": []
    })
    registry = REGISTRY + sha(registry_raw) + ".json"
    sources[registry] = registry_raw
    put(root, registry, registry_raw)
    original = {
        "schema": "product-validated-builds.v1", "repository_root": str(root),
        "sources": {name: sha(raw) for name, raw in sources.items()},
        "artifacts": {"fixture/artifact": "2" * 64}, "plugin_build_hash": "1" * 64,
        "evidence": {
            "research_graph_recipe_v3": {
                "installed_pointer": {"path": current.INSTALLED_POINTER_REF,
                                      "sha256": sha(sources[current.INSTALLED_POINTER_REF])}
            },
            "retained_recipe_archives_v1": {"registry": {"path": registry, "sha256": sha(registry_raw)}},
        },
    }
    raw, transport = v2_receipt(root, original)
    put(root, RECEIPT, raw)
    return root, manifest, original, raw, transport


def prepared(files: Files) -> tuple[capture.PreparedRecipeArchive, dict[str, Any], dict[str, bytes]]:
    root, manifest, _, raw, _ = files
    result = capture.prepare_retained_graph_recipe_v3_archive(
        repository_root=root, prior_receipt_path=root / RECEIPT,
        prior_receipt_sha256=sha(raw), original_manifest=manifest,
        staging_root=root / "artifacts/stage",
    )
    assert result.archived_graph_hash is not None
    base = "packages/kernel/research_recipe_versions/" + result.archived_graph_hash + "/"
    stage = root / "artifacts/stage"
    index = json.loads(capture._read(capture._path(stage, base + "release.json")))
    payloads = {name: capture._read(capture._path(stage, base + "root/" + name))
                for name in index["stored_files"]}
    return result, index, payloads


def test_actual_21b_v1_keeps_raw_identity_and_all_logical_sources() -> None:
    name = "artifacts/mr-release-b2b4-21b/product-validated-builds.json"
    path = FORMAL / name
    digest = "0ba4a22a23f7dcf47d346cfc4ed46f55718d4f9da0a10dacda4367369ca5fcf4"
    value, raw = capture._bound_receipt(FORMAL, path, digest)
    assert sha(raw) == digest and raw == path.read_bytes()
    assert value == json.loads(raw)
    assert value["schema"] == "product-validated-builds.v1"
    assert len(value["sources"]) == 9296 and len(value["artifacts"]) == 41


def test_v2_bound_receipt_preserves_actual_schema_raw_and_full_map(files: Files) -> None:
    root, _, original, raw, transport = files
    value, bound = capture._bound_receipt(root, root / RECEIPT, sha(raw))
    assert bound == raw and value["schema"] == "product-validated-builds.v2"
    assert value["sources"] == original["sources"]
    assert len(json.loads(bound)) == 7 and len(value) == 6
    assert len(transport) == 2


def test_v2_capture_is_closed_and_private_reader_ignores_current_files(files: Files) -> None:
    root, _, original, raw, transport = files
    result, index, payloads = prepared(files)
    assert index["schema_version"] == "research-retained-recipe-release.v3"
    assert index["installation_binding"]["source_map_resources"] == {
        name: sha(body) for name, body in transport.items()
    }
    assert payloads[RECEIPT] == raw and codec.SOURCE_REF in payloads
    for name, body in transport.items():
        assert payloads[name] == body
        put(root, name, b"damaged current transport")
    put(root, RECEIPT, b"damaged current receipt")
    put(root, current.INSTALLED_POINTER_REF, b"damaged current pointer")
    resolved, _ = capture._validate_retained_v3_installation(index, payloads, expected_root=root)
    assert resolved["sources"] == original["sources"]
    assert result.retained_authority_configured is False


@pytest.mark.parametrize("damage", ["missing", "binding_missing", "mutated", "extra", "old_schema"])
def test_closed_capsule_rejects_missing_tampered_and_fake_old_schema(files: Files, damage: str) -> None:
    root, _, _, _, transport = files
    _, index, payloads = prepared(files)
    name = next(iter(transport))
    if damage == "missing":
        del payloads[name]
    elif damage == "binding_missing":
        del index["installation_binding"]["source_map_resources"][name]
    elif damage == "mutated":
        payloads[name] += b" "
    elif damage == "extra":
        payloads["artifacts/unbound.json"] = b"{}"
    else:
        index["schema_version"] = "research-retained-recipe-release.v2"
        del index["installation_binding"]["source_map_resources"]
        for item in transport:
            del index["all_files"][item]
            index["stored_files"].remove(item)
            del payloads[item]
    with pytest.raises(ValueError):
        capture._validate_retained_v3_installation(index, payloads, expected_root=root)


def test_current_v3_revalidates_transport_even_with_same_mtime(files: Files, monkeypatch: pytest.MonkeyPatch) -> None:
    root, manifest, _, raw, transport = files
    model = current.GraphRecipeManifestV3.model_validate_json(json.dumps(manifest))
    monkeypatch.setattr(current, "_ROOT", root)
    monkeypatch.setattr(current, "graph_recipe_manifest_v3", lambda: model)
    ref = current.GraphRecipeResourceV3(path=RECEIPT, sha256=sha(raw))
    bound = current._validated_installation(ref)
    assert bound.receipt == ref
    name = next(iter(transport))
    path = capture._path(root, name)
    stamp = path.stat()
    body = path.read_bytes()
    path.write_bytes(body[:-1] + b" ")
    os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    with pytest.raises(ValueError):
        current._validated_installation(ref, expected=bound)


def test_history_registry_revalidates_transport_every_read(files: Files, monkeypatch: pytest.MonkeyPatch) -> None:
    root, _, _, raw, transport = files
    monkeypatch.setattr(history, "_CURRENT_ROOT", root)
    monkeypatch.setattr(history, "_REGISTRY_BINDING", None)
    history.configure_retained_recipe_registry_from_validated_release(
        receipt_path=root / RECEIPT, receipt_sha256=sha(raw)
    )
    binding = history._REGISTRY_BINDING
    assert binding is not None and history._registry_registrations(binding) == ()
    name = next(iter(transport))
    path = capture._path(root, name)
    stamp = path.stat()
    body = path.read_bytes()
    path.write_bytes(body[:-1] + b" ")
    os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    with pytest.raises(ValueError):
        history._registry_registrations(binding)


def test_caps_include_index_and_preserve_fixed_resource_and_closure_limits(files: Files, monkeypatch: pytest.MonkeyPatch) -> None:
    root, _, _, _, _ = files
    _, index, payloads = prepared(files)
    assert capture._MAX_BYTES == 2 * 1024 * 1024
    assert capture._MAX_TOTAL == 32 * 1024 * 1024
    total = len(capture._canonical(index)) + sum(map(len, payloads.values()))
    monkeypatch.setattr(capture, "_MAX_TOTAL", total)
    capture._validate_retained_v3_installation(index, payloads, expected_root=root)
    monkeypatch.setattr(capture, "_MAX_TOTAL", total - 1)
    with pytest.raises(ValueError, match="closure_limit"):
        capture._validate_retained_v3_installation(index, payloads, expected_root=root)
    monkeypatch.setattr(capture, "_MAX_TOTAL", 32 * 1024 * 1024)
    payloads[next(iter(payloads))] = b"x" * (capture._MAX_BYTES + 1)
    with pytest.raises(ValueError, match="closure_limit"):
        capture._validate_retained_v3_installation(index, payloads, expected_root=root)


def test_hash_rebound_owner_tamper_is_rejected(files: Files) -> None:
    root, _, _, raw, transport = files
    receipt = json.loads(raw)
    ref = receipt["historical_source_map"]
    index = json.loads(transport[ref["path"]])
    index["archive_owners"][0]["index_sha256"] = "0" * 64
    changed = codec.canonical(index)
    name = codec.TRANSPORT_PREFIX + "indexes/" + sha(changed) + ".json"
    put(root, name, changed)
    receipt["historical_source_map"] = {"path": name, "sha256": sha(changed), "byte_count": len(changed)}
    changed_receipt = codec.canonical(receipt)
    put(root, RECEIPT, changed_receipt)
    with pytest.raises(ValueError, match="owner_invalid"):
        capture._bound_receipt(root, root / RECEIPT, sha(changed_receipt))


def test_v1_capture_keeps_original_retained_v2_shape(files: Files) -> None:
    root, _, original, _, _ = files
    raw = capture._canonical(original)
    put(root, RECEIPT, raw)
    v1_files = (*files[:3], raw, {})
    _, index, payloads = prepared(v1_files)
    assert index["schema_version"] == "research-retained-recipe-release.v2"
    assert set(index["installation_binding"]) == {"receipt", "pointer", "repository_root"}
    assert payloads[RECEIPT] == raw
    assert not any(name.startswith(codec.TRANSPORT_PREFIX) for name in payloads)
    resolved, _ = capture._validate_retained_v3_installation(index, payloads, expected_root=root)
    assert resolved == original


def test_wrong_receipt_hash_and_repository_root_rejected(files: Files, tmp_path: Path) -> None:
    root, _, _, raw, _ = files
    with pytest.raises(ValueError, match="receipt_changed"):
        capture._bound_receipt(root, root / RECEIPT, "0" * 64)
    _, index, payloads = prepared(files)
    wrong = copy.deepcopy(index)
    wrong["installation_binding"]["repository_root"] = str(tmp_path / "other")
    with pytest.raises(ValueError, match="installation_invalid"):
        capture._validate_retained_v3_installation(wrong, payloads, expected_root=root)


def test_exact_21b_closure_stays_immutable_and_loads_private_original(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    saved = FORMAL / (
        "artifacts/market-risk-implementation-20261007/B2/"
        "worker-terminal21b-btc-diagnosis/recipe-selection-originals05.json"
    )
    saved_raw = saved.read_bytes()
    assert sha(saved_raw) == "bb3b2bfbc3bea7e2c79138e44bac6921afe019d5fa3c2ed1ea2c36cff5483419"
    row = json.loads(saved_raw)["items"][0]["originals"][0]
    assert sha(row["payload_json"].encode()) == row["payload_hash"]
    selection = json.loads(row["payload_json"])
    manifest = selection["manifest"]
    receipt_ref = selection["installation_receipt"]
    original_raw = (FORMAL / receipt_ref["path"]).read_bytes()
    assert sha(original_raw) == receipt_ref["sha256"]
    original = json.loads(original_raw)
    root = capture._io_path(tmp_path / "actual-21b-private")
    root.mkdir()
    for ref in manifest["source_files"] + manifest["schema_files"]:
        raw = (FORMAL / ref["path"]).read_bytes()
        assert sha(raw) == ref["payload_hash"]
        put(root, ref["path"], raw)
    for ref in (manifest["registration"], selection["installed_pointer"]):
        raw = (FORMAL / ref["path"]).read_bytes()
        assert sha(raw) == ref["sha256"]
        put(root, ref["path"], raw)
    # Relocation is a new fixture receipt, with a new actual hash. The original
    # formal receipt, selected identity and capsules are never modified.
    original["repository_root"] = str(root)
    original["evidence"].pop("retained_recipe_archives_v1", None)
    fixture_receipt = capture._canonical(original)
    put(root, receipt_ref["path"], fixture_receipt)
    stage = root / "artifacts/stage"
    result = capture.prepare_retained_graph_recipe_v3_archive(
        repository_root=root, prior_receipt_path=root / receipt_ref["path"],
        prior_receipt_sha256=sha(fixture_receipt), original_manifest=manifest,
        staging_root=stage,
    )
    assert result.archived_graph_hash == selection["dependency_hash"]
    assert result.registry_ref is not None
    for name, digest in result.install_sources.items():
        raw = capture._read(capture._path(stage, name))
        assert sha(raw) == digest
        put(root, name, raw)
    registry = json.loads(capture._read(capture._path(root, result.registry_ref["path"])))
    registration = history._Registration(**registry["registrations"][0])
    monkeypatch.setattr(history, "_CURRENT_ROOT", root)
    monkeypatch.setattr(history, "_RESOURCE_ROOT", root / "packages/kernel/research_recipe_versions")
    monkeypatch.setattr(history, "_REGISTRATIONS", (registration,))
    index, payloads, private_root = history._verified_release_uncached(registration)
    assert index["schema_version"] == "research-retained-recipe-release.v2"
    private = history._PrivateRelease(registration, payloads, private_root, v3=True)
    try:
        recipe = private.load(history._RECIPE_V3)
        assert recipe is not current
        assert recipe.__file__ == str(private_root / current.SOURCE_REF)
        history._configure_private_v3_installation(private)
        with pytest.raises(PermissionError, match="read_scope_required"):
            recipe._installation()
    finally:
        private.discard()
    damaged = capture._path(root, "packages/kernel/research_recipe_versions/" + registration.directory +
                            "/root/" + current.INSTALLED_POINTER_REF)
    damaged.write_bytes(damaged.read_bytes() + b" ")
    with pytest.raises(ValueError):
        history._verified_release_uncached(registration)


def test_actual_current_v3_closure_loads_original_v2_codec_without_current_transport(files: Files, monkeypatch: pytest.MonkeyPatch) -> None:
    root, _, original, _, _ = files
    capture._path(root, "contracts/schemas/fixture.json").unlink()
    manifest = current.graph_recipe_manifest_v3().model_dump(mode="json")
    selected = {}
    for ref in manifest["source_files"] + manifest["schema_files"]:
        raw = (DEV / ref["path"]).read_bytes()
        assert sha(raw) == ref["payload_hash"]
        put(root, ref["path"], raw)
        selected[ref["path"]] = sha(raw)
    ref = manifest["registration"]
    registration_raw = (DEV / ref["path"]).read_bytes()
    assert sha(registration_raw) == ref["sha256"]
    put(root, ref["path"], registration_raw)
    selected[ref["path"]] = ref["sha256"]
    pointer = {
        "schema_version": "research-graph-recipe-installed.v3",
        "recipe_version": "research-graph-projection.v3", "lifecycle": "installed",
        "registration": ref, "dependency_hash": sha(capture._canonical(manifest)[:-1]),
        "plugin_build_hash": original["plugin_build_hash"],
    }
    pointer_raw = capture._canonical(pointer)
    put(root, current.INSTALLED_POINTER_REF, pointer_raw)
    selected[current.INSTALLED_POINTER_REF] = sha(pointer_raw)
    selected.update({name: digest for name, digest in original["sources"].items()
                     if name.startswith("packages/kernel/research_recipe_versions/")
                     and name != ref["path"]})
    original["sources"] = selected
    original["evidence"]["research_graph_recipe_v3"]["installed_pointer"]["sha256"] = sha(pointer_raw)
    raw, transport = v2_receipt(root, original)
    put(root, RECEIPT, raw)
    result, index, payloads = prepared((root, manifest, original, raw, transport))
    assert index["schema_version"] == "research-retained-recipe-release.v3"
    for name, digest in result.install_sources.items():
        body = capture._read(capture._path(root / "artifacts/stage", name))
        assert sha(body) == digest
        put(root, name, body)
    assert result.registry_ref is not None
    registry = json.loads(capture._read(capture._path(root, result.registry_ref["path"])))
    registration = history._Registration(**registry["registrations"][0])
    monkeypatch.setattr(history, "_CURRENT_ROOT", root)
    monkeypatch.setattr(history, "_RESOURCE_ROOT", root / "packages/kernel/research_recipe_versions")
    monkeypatch.setattr(history, "_REGISTRATIONS", (registration,))
    verified, originals, private_root = history._verified_release_uncached(registration)
    assert verified == index and originals == payloads
    private = history._PrivateRelease(registration, originals, private_root, v3=True)
    try:
        recipe = private.load(history._RECIPE_V3)
        original_codec = private.load(codec.SOURCE_REF.removesuffix(".py").replace("/", "."))
        assert original_codec is not codec
        codec_file = original_codec.__file__
        assert isinstance(codec_file, str)
        assert Path(codec_file).resolve() == (private_root / codec.SOURCE_REF).resolve()
        for name in transport:
            capture._path(root, name).unlink()
        resolved = original_codec.resolve_product_receipt(
            raw, expected_root=root, read_resource=originals.__getitem__, expected_sha256=sha(raw)
        )
        assert resolved.raw == raw and resolved.value["schema"] == "product-validated-builds.v2"
        assert resolved.value["sources"] == selected
        history._configure_private_v3_installation(private)
        with pytest.raises(PermissionError, match="read_scope_required"):
            recipe._installation()
    finally:
        private.discard()
    # New release3 is canonical: a hash-rebound whitespace expansion must not
    # make the actual index bytes exceed the closure accounting view.
    prefix = "packages/kernel/research_recipe_versions/" + registration.directory + "/"
    path = capture._path(root, prefix + "release.json")
    changed = path.read_bytes() + b" "
    path.write_bytes(changed)
    rebound = replace(registration, index_hash=sha(changed))
    with pytest.raises(ValueError, match="release_unavailable"):
        history._verified_release_uncached(rebound)
    sources = dict(result.install_sources)
    sources[prefix + "release.json"] = sha(changed)
    with pytest.raises(ValueError, match="v3_index_invalid"):
        capture._existing_archive(root, sources, {
            "directory": rebound.directory, "index_hash": rebound.index_hash,
            "graph_hash": rebound.graph_hash, "selector_hash": rebound.selector_hash,
        })

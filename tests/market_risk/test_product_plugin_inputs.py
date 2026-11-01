"""Private synthetic releases exercise the real v2 guard; no installed authority."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import pytest

from packages.kernel.decision_hub_kernel.application import product_release_provenance as codec

ROOT = Path(__file__).resolve().parents[2]
DEPENDENCIES = Path(os.environ.get("Q044_DEPENDENCY_ROOT", ROOT))
PLUGIN = "extensions/dsh/decision-hub/"
CONTROLS = (
    PLUGIN + "package.json",
    PLUGIN + "cordis.patch.yml",
    PLUGIN + "scripts/generate-build-identity.mjs",
    PLUGIN + "scripts/build-client.mjs",
    PLUGIN + "vite.host.config.ts",
    PLUGIN + "tsconfig.json",
    "package.json",
    "pnpm-lock.yaml",
    "pnpm-workspace.yaml",
    "packages/contracts_ts/package.json",
    "infra/dsh/presets/decision-research/agent.cordis.yml",
    "infra/dsh/presets/decision-research/preset.yml",
    "infra/dsh/upstream.lock.json",
)
FIXED = (
    "apps/decision-desk/index.html",
    "apps/decision-desk/package.json",
    "apps/decision-desk/tsconfig.json",
    "apps/decision-desk/vite.config.ts",
    "infra/dsh/validated-product-builds-v2.ps1",
    "infra/dsh/product-build-validation.ps1",
)
READER = "infra/dsh/product-artifact-manifest.py"
IDENTITY = PLUGIN + "src/generated-build-identity.ts"
Inputs = list[str] | list[str | bool] | dict[str, str]


@dataclass(frozen=True)
class Repository:
    root: Path
    inputs: tuple[str, ...]
    receipt: Path


def _put(root: Path, name: str, raw: bytes) -> None:
    p = root / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(raw)


def _sources(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file()
        and not p.relative_to(root)
        .as_posix()
        .startswith(("artifacts/", "apps/decision-desk/dist/", PLUGIN + "lib/"))
    }


def _framing(inputs: Iterable[object], sources: dict[str, bytes]) -> str:
    h = hashlib.sha256()
    for name in inputs:
        if type(name) is str and name in sources:
            raw = sources[name]
            h.update((name + "\0" + str(len(raw)) + ":").encode())
            h.update(raw)
            h.update(b"\0")
    return h.hexdigest()


def _seal(
    repo: Repository, inputs: Inputs, *, stale: str | None = None, unbound: str | None = None
) -> str:
    root = repo.root
    plugin_hash = stale or _framing(inputs, _sources(root))
    _put(
        root,
        IDENTITY,
        (
            f"export const PLUGIN_BUILD_HASH = {json.dumps(plugin_hash)} as const;\n"
            f"export const PLUGIN_BUILD_INPUTS = {json.dumps(inputs)} as const;\n"
        ).encode(),
    )
    for name in ("index.js", "research-tool.js", "synthesis-tool.js", "client.js"):
        _put(root, PLUGIN + "lib/" + name, f"// synthetic:{plugin_hash}\n".encode())
    dist = "apps/decision-desk/dist/"
    emitted = {
        name: codec.sha256((root / (dist + name)).read_bytes())
        for name in ("index.html", "assets/app.js", ".vite/manifest.json")
    }
    emission = {"schema_version": "product-emitted-artifacts.v1", "files": emitted}
    _put(root, dist + ".vite/product-artifacts.json", codec.canonical(emission))
    sources = {name: codec.sha256(raw) for name, raw in _sources(root).items()}
    if unbound is not None:
        sources.pop(unbound)
    projection, payloads = codec.encode_historical_sources(
        sources,
        read_resource=lambda name: (root / name).read_bytes(),
    )
    for name, raw in payloads.items():
        _put(root, name, raw)
    artifacts = {
        p.relative_to(root).as_posix(): codec.sha256(p.read_bytes())
        for directory in (root / dist, root / (PLUGIN + "lib"))
        for p in directory.rglob("*")
        if p.is_file()
    }
    value = {
        "schema": "product-validated-builds.v2",
        "repository_root": str(root),
        **projection,
        "artifacts": artifacts,
        "plugin_build_hash": plugin_hash,
        "evidence": {},
    }
    _put(root, repo.receipt.relative_to(root).as_posix(), codec.canonical(value))
    return plugin_hash


def _repository(tmp_path: Path, *, actual: bool = False) -> Repository:
    assert Path(codec.__file__).resolve() == ROOT / codec.SOURCE_REF
    root = tmp_path / "synthetic-repository"
    for name in (*CONTROLS, *FIXED):
        _put(root, name, ("synthetic:" + name).encode())
    for directory in ("apps/decision-desk/src", "apps/decision-desk/public", "contracts/schemas"):
        (root / directory).mkdir(parents=True, exist_ok=True)
    if not actual:
        for name in (
            PLUGIN + "src/index.ts",
            PLUGIN + "src/nested/new.ts",
            "packages/contracts_ts/src/generated/types.ts",
        ):
            _put(root, name, ("synthetic:" + name).encode())
    if actual:
        for directory in (PLUGIN + "src", "packages/contracts_ts/src"):
            for p in (ROOT / directory).rglob("*"):
                if p.is_file() and p.relative_to(ROOT).as_posix() != IDENTITY:
                    _put(root, p.relative_to(ROOT).as_posix(), p.read_bytes())
        for name in CONTROLS:
            _put(root, name, (ROOT / name).read_bytes())
    _put(root, codec.SOURCE_REF, Path(codec.__file__).read_bytes())
    _put(root, READER, (DEPENDENCIES / READER).read_bytes())
    _put(
        root,
        "apps/decision-desk/dist/index.html",
        b'<script type="module" src="/assets/app.js"></script>',
    )
    _put(root, "apps/decision-desk/dist/assets/app.js", b"// synthetic app")
    manifest = {"index.html": {"file": "assets/app.js", "isEntry": True}}
    _put(root, "apps/decision-desk/dist/.vite/manifest.json", codec.canonical(manifest))
    names = {
        p.relative_to(root).as_posix()
        for directory in (PLUGIN + "src", "packages/contracts_ts/src")
        for p in (root / directory).rglob("*")
        if p.is_file()
    }
    repo = Repository(root, tuple(sorted(names | set(CONTROLS))), root / "artifacts/receipt.json")
    _seal(repo, list(repo.inputs))
    return repo


def test_complete_current_sources_and_controls_pass_real_v2_guard(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    result = codec.verify_product_receipt_v2(repo.root, repo.receipt)
    assert result["receipt_schema"] == "product-validated-builds.v2"
    assert result["plugin_build_hash"] == _framing(list(repo.inputs), _sources(repo.root))


def test_actual_source_tree_contract_uses_all_physical_inputs(tmp_path: Path) -> None:
    repo = _repository(tmp_path, actual=True)
    result = codec.verify_product_receipt_v2(repo.root, repo.receipt)
    assert result["plugin_build_hash"] == _framing(list(repo.inputs), _sources(repo.root))
    assert IDENTITY not in repo.inputs
    assert set(CONTROLS) <= set(repo.inputs)


@pytest.mark.parametrize(
    "name",
    [
        PLUGIN + "scripts/build-client.mjs",
        PLUGIN + "src/nested/new.ts",
        "packages/contracts_ts/src/generated/types.ts",
    ],
)
def test_repacked_declaration_cannot_omit_a_real_input(tmp_path: Path, name: str) -> None:
    repo = _repository(tmp_path)
    _seal(repo, [item for item in repo.inputs if item != name])
    with pytest.raises(ValueError, match="product_provenance_plugin_inputs_invalid"):
        codec.verify_product_receipt_v2(repo.root, repo.receipt)


@pytest.mark.parametrize(
    "damage", ["extra", "duplicate", "nonstring", "nonlist", "traversal", "absolute"]
)
def test_repacked_invalid_declarations_rejected(tmp_path: Path, damage: str) -> None:
    repo = _repository(tmp_path)
    declaration: Inputs = list(repo.inputs)
    if damage == "extra":
        _put(repo.root, "extra.txt", b"synthetic extra")
        declaration = [*repo.inputs, "extra.txt"]
    elif damage == "duplicate":
        declaration = [*repo.inputs, repo.inputs[0]]
    elif damage == "nonstring":
        declaration = [*repo.inputs, True]
    elif damage == "nonlist":
        declaration = {"input": repo.inputs[0]}
    elif damage == "absolute":
        declaration = [*repo.inputs, "C:/outside"]
    else:
        declaration = [*repo.inputs, "../outside"]
    _seal(repo, declaration)
    code = "identity_invalid" if damage == "nonlist" else "inputs_invalid"
    with pytest.raises(ValueError, match="product_provenance_plugin_" + code):
        codec.verify_product_receipt_v2(repo.root, repo.receipt)


def test_fresh_inventory_cannot_be_replaced_by_stale_declaration(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    _put(repo.root, PLUGIN + "src/late.ts", b"new physical input")
    _seal(repo, list(repo.inputs))
    with pytest.raises(ValueError, match="product_provenance_plugin_inputs_invalid"):
        codec.verify_product_receipt_v2(repo.root, repo.receipt)


def test_declaration_must_be_bound_in_source_map(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    _seal(repo, list(repo.inputs), unbound=PLUGIN + "scripts/build-client.mjs")
    with pytest.raises(ValueError, match="product_provenance_current_source_missing"):
        codec.verify_product_receipt_v2(repo.root, repo.receipt)


def test_repacked_leaf_hash_does_not_reauthorize_stale_plugin_hash(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    old = json.loads(repo.receipt.read_bytes())["plugin_build_hash"]
    _put(repo.root, PLUGIN + "src/index.ts", b"changed byte content")
    _seal(repo, list(repo.inputs), stale=old)
    with pytest.raises(ValueError, match="product_provenance_plugin_identity_changed"):
        codec.verify_product_receipt_v2(repo.root, repo.receipt)


def test_unrepacked_leaf_and_oversize_receipt_still_rejected(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    _put(repo.root, PLUGIN + "src/index.ts", b"changed byte content")
    with pytest.raises(ValueError, match="product_provenance_leaf_changed"):
        codec.verify_product_receipt_v2(repo.root, repo.receipt)
    repo.receipt.write_bytes(b" " * (codec.RECEIPT_LIMIT + 1))
    with pytest.raises(ValueError, match="product_provenance_limit"):
        codec.verify_product_receipt_v2(repo.root, repo.receipt)


def test_fully_repacked_legacy_subset_cannot_authorize_current_release(tmp_path: Path) -> None:
    repo = _repository(tmp_path, actual=True)
    legacy = {
        PLUGIN + "src/" + name
        for name in (
            "index.ts research-tool.ts query-catalog.ts synthesis-tool.ts "
            "client/index.js final-review-gate.ts reviewer-tool.ts frozen-review-prompt.ts"
        ).split()
    }
    legacy.update(CONTROLS[:2] + CONTROLS[-3:])
    legacy.update(
        p.relative_to(repo.root).as_posix()
        for p in (repo.root / (PLUGIN + "src/host")).rglob("*")
        if p.is_file()
    )
    assert legacy < set(repo.inputs)
    _seal(repo, sorted(legacy))
    with pytest.raises(ValueError, match="product_provenance_plugin_inputs_invalid"):
        codec.verify_product_receipt_v2(repo.root, repo.receipt)


def test_source_link_still_rejected_before_input_acceptance(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "linked.ts").write_bytes(b"synthetic outside source")
    link = repo.root / (PLUGIN + "src/link")
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(outside), str(link))
    else:
        link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="product_provenance_linked_path"):
        codec.verify_product_receipt_v2(repo.root, repo.receipt)

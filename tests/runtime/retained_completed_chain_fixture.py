"""Synthetic file topology only; never real build/market/normal qualification."""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

MAIN = Path("E:/project/study/codex/dsh/decision-hub")
ROOT = Path(__file__).resolve().parents[2]
FROZEN = ROOT / "tests/runtime/test_retained_v2_maintenance_prior.py"
sys.path.insert(0, str(MAIN))
spec = importlib.util.spec_from_file_location("frozen_singlehop_fixture", FROZEN)
assert spec is not None and spec.loader is not None
old = importlib.util.module_from_spec(spec)
spec.loader.exec_module(old)


def load() -> ModuleType:
    path = ROOT / "infra/dsh/prepare-product-retention.py"
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("bounded_candidate_prepare", path)
    assert spec is not None and spec.loader is not None
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def seal(node: SimpleNamespace) -> SimpleNamespace:
    ref = {
        "path": str(node.root / node.retention_name),
        "sha256": old.write(node.root, node.retention_name, old.raw(node.retention)),
    }
    node.prior["evidence"]["retention_preparation"] = ref
    node.execution["retention_preparation"] = ref
    digest = old.write(node.root, node.prior_name, old.raw(node.prior))
    node.execution["validated_receipt"]["sha256"] = digest
    old.write(
        node.root,
        str(Path(node.prior_name).parent / "build-execution.json"),
        old.raw(node.execution),
    )
    return SimpleNamespace(
        prior_receipt_path=node.root / node.prior_name,
        prior_receipt_sha256=digest,
        completed_maintenance_prior=True,
        repository_root=node.root,
    )


def make_chain(tmp_path: Path, hops: int = 2) -> list[SimpleNamespace]:
    first = old.fixture(tmp_path)
    seal(first)
    nodes = [first]
    for index in range(1, hops):
        parent = nodes[-1]
        parent_args = seal(parent)
        node = copy.deepcopy(parent)
        node.prior_name = f"artifacts/normal-{index}/product-validated-builds.json"
        node.retention_name = f"artifacts/normal-{index}/retention/retained-recipe-preparation.json"
        node.retention["prior_receipt"] = {
            "path": str(parent_args.prior_receipt_path),
            "sha256": parent_args.prior_receipt_sha256,
        }
        node.execution["validated_receipt"]["path"] = str(node.root / node.prior_name)
        seal(node)
        nodes.append(node)
    return nodes


def reseal(nodes: list[SimpleNamespace]) -> SimpleNamespace:
    for index, node in enumerate(nodes):
        if index:
            parent = seal(nodes[index - 1])
            node.retention["prior_receipt"] = {
                "path": str(parent.prior_receipt_path),
                "sha256": parent.prior_receipt_sha256,
            }
        seal(node)
    return seal(nodes[-1])


def invoke(module: ModuleType, nodes: list[SimpleNamespace]) -> Any:
    return module.authenticate_unstarted_maintenance_prior(
        nodes[0].root, seal(nodes[-1]), old.raw(nodes[0].pointer)
    )


def archive_fixture(nodes: list[SimpleNamespace]) -> dict[str, Any]:
    """An actual original archive validator can hash this complete synthetic v1 wire."""
    root = nodes[0].root
    name = "packages/synthetic_example.py"
    payload = b"# synthetic, never executed\n"
    leaf = old.digest(payload)
    manifest = {
        "schema_version": "research-graph-recipe-manifest.v2",
        "runtime_versions": {"synthetic": "not-native"},
        "source_files": [{"path": name, "payload_hash": leaf}],
        "schema_files": [],
    }
    graph = old.digest(old.raw(manifest)[:-1])
    prefix = "packages/kernel/research_recipe_versions/" + graph + "/"
    index = {
        "schema_version": "research-retained-recipe-release.v1",
        "graph_recipe_dependency_hash": graph,
        "graph_recipe_manifest": manifest,
        "selector_identity": {"implementation_hash": "c" * 64},
        "selector_identity_files": {},
        "stored_files": [name],
        "shared_auxiliary_files": {},
        "all_files": {name: leaf},
        "orm_mode": "private_original_orm.v1",
    }
    raw_index = old.raw(index)
    index_name = prefix + "release.json"
    stored_name = prefix + "root/" + name
    old.write(root, index_name, raw_index)
    old.write(root, stored_name, payload)
    row = {
        "directory": graph,
        "graph_hash": graph,
        "index_hash": old.digest(raw_index),
        "selector_hash": "c" * 64,
        "private_original_orm": True,
    }
    registry = {"schema_version": "research-retained-recipe-registry.v1", "registrations": [row]}
    regraw = old.raw(registry)
    regsha = old.digest(regraw)
    regname = "packages/kernel/research_recipe_versions/registries/" + regsha + ".json"
    old.write(root, regname, regraw)
    ref = {"path": regname, "sha256": regsha}
    additions = {index_name: old.digest(raw_index), stored_name: leaf, regname: regsha}
    runtime_path = root / nodes[0].pointer["receipt_path"]
    runtime = json.loads(runtime_path.read_bytes())
    runtime["sources"].update(additions)
    runtime["evidence"]["retained_recipe_archives_v1"]["registry"] = ref
    runtime_sha = old.write(root, nodes[0].pointer["receipt_path"], old.raw(runtime))
    for node in nodes:
        node.pointer["receipt_sha256"] = runtime_sha
        node.prior["sources"].update(additions)
        node.prior["evidence"]["retained_recipe_archives_v1"]["registry"] = ref
        node.retention["retained_recipe_archives_v1"]["registry"] = ref
        node.retention["prior_registry_validation"]["registry"] = ref
        node.execution["validated_receipt"]["source_count"] = len(node.prior["sources"])
    nodes[0].retention["prior_receipt"]["sha256"] = runtime_sha
    old.write(root, "data/product-local/installed-release-receipt.json", old.raw(nodes[0].pointer))
    reseal(nodes)
    return {"index": index_name, "stored": stored_name, "registry": regname, "row": row, "ref": ref}


def v2_transport(node: SimpleNamespace) -> str:
    from packages.kernel.decision_hub_kernel.application import product_release_provenance as codec

    def read_resource(name: str) -> bytes:
        return (node.root / name).read_bytes()

    fields, payloads = codec.encode_historical_sources(
        node.prior["sources"], read_resource=read_resource
    )
    for name, payload in payloads.items():
        old.write(node.root, name, payload)
    node.prior.update(fields)
    node.prior["schema"] = "product-validated-builds.v2"
    seal(node)
    return next(iter(payloads))

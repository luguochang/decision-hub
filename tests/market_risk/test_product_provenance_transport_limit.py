"""Pure bounded transport roundtrips; synthetic leaves grant no release authority."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from packages.kernel.decision_hub_kernel.application import product_release_provenance as codec


OWNER = "packages/kernel/research_recipe_versions/" + "a" * 64
LEAF = b"x"
LEAF_SHA = hashlib.sha256(LEAF).hexdigest()
OWNER_RAW = b'{"graph_recipe_dependency_hash":"' + b"a" * 64 + b'"}\n'
OWNER_SHA = hashlib.sha256(OWNER_RAW).hexdigest()


def _canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _sources(segments: int) -> dict[str, str]:
    prefix = OWNER + "/root/" + "/".join(["retained_dependency_package"] * segments)
    return {
        "src/current.py": LEAF_SHA,
        OWNER + "/release.json": OWNER_SHA,
        **{f"{prefix}/source_{i:05d}.py": LEAF_SHA for i in range(60_000)},
    }


def _read_leaf(name: str) -> bytes:
    return OWNER_RAW if name == OWNER + "/release.json" else LEAF


def _receipt(root: Path, projection: dict) -> bytes:
    return _canonical({
        "schema": "product-validated-builds.v2",
        "repository_root": str(root),
        "sources": projection["sources"],
        "historical_source_map": projection["historical_source_map"],
        "artifacts": {"dist/fixture.js": LEAF_SHA},
        "plugin_build_hash": "b" * 64,
        "evidence": {},
    })


def test_retained_transport_above_16_mib_roundtrips_without_losing_leaf_or_owner(tmp_path):
    sources = _sources(4)
    projection, payloads = codec.encode_historical_sources(sources, read_resource=_read_leaf)
    total = sum(map(len, payloads.values()))
    assert 16_777_216 < total < 33_554_432
    assert len(sources) == 60_002
    index = json.loads(payloads[projection["historical_source_map"]["path"]])
    assert len(index["shards"]) <= 64
    assert projection["historical_source_map"]["byte_count"] <= 1_048_576
    assert all(ref["byte_count"] <= 1_048_576 for ref in index["shards"])
    raw = _receipt(tmp_path, projection)
    resolved = codec.resolve_product_receipt(
        raw, expected_root=tmp_path, read_resource=payloads.__getitem__,
        expected_sha256=hashlib.sha256(raw).hexdigest(),
    )
    assert dict(resolved.value["sources"]) == sources
    assert dict(resolved.source_sizes) == {
        name: len(OWNER_RAW) if name == OWNER + "/release.json" else 1
        for name in sources if name != "src/current.py"
    }
    assert len(resolved.owners) == 1
    owner = resolved.owners[0]
    assert owner["archive_root"] == OWNER
    assert owner["index_sha256"] == OWNER_SHA
    assert owner["source_binding_mode"] == "legacy_graph_index"
    assert resolved.raw == raw
    shard = index["shards"][0]["path"]
    changed = dict(payloads)
    changed[shard] = changed[shard].replace(b'"byte_count":1', b'"byte_count":2', 1)
    assert changed[shard] != payloads[shard]
    with pytest.raises(ValueError, match="^product_provenance_resource_changed$"):
        codec.resolve_product_receipt(raw, expected_root=tmp_path, read_resource=changed.__getitem__)
    print(f"accepted_transport_bytes={total}; sources={len(sources)}; shards={len(index['shards'])}")


def test_transport_above_32_mib_is_rejected_by_encoder_and_decoder(tmp_path):
    sources = _sources(18)
    with pytest.raises(ValueError, match="^product_provenance_limit$"):
        codec.encode_historical_sources(sources, read_resource=_read_leaf)

    # Independently pack fixed 1,000-leaf shards: each is below 1 MiB, all 61
    # refs are below the 64-shard limit, and the full leaf count remains legal.
    historical = {name: digest for name, digest in sources.items() if name != "src/current.py"}
    entries = list(historical.items())
    resources = {}
    refs = []
    for start in range(0, len(entries), 1_000):
        leaves = {
            name: {"sha256": digest, "byte_count": len(_read_leaf(name))}
            for name, digest in entries[start:start + 1_000]
        }
        body = _canonical({"schema_version": "product-historical-source-shard.v1", "sources": leaves})
        assert len(body) <= 1_048_576
        digest = hashlib.sha256(body).hexdigest()
        path = f"artifacts/product-provenance/shards/{digest}.json"
        resources[path] = body
        refs.append({"path": path, "sha256": digest, "byte_count": len(body)})
    index = _canonical({
        "schema_version": "product-historical-source-map.v1",
        "source_count": len(historical),
        "leafset_sha256": hashlib.sha256(_canonical(historical)).hexdigest(),
        "shards": refs,
        "archive_owners": [{
            "archive_root": OWNER, "kind": "research_recipe",
            "index_path": OWNER + "/release.json", "index_sha256": OWNER_SHA,
            "index_byte_count": len(OWNER_RAW), "dependency_hash": "a" * 64,
            "source_binding_mode": "legacy_graph_index", "source_binding_path": None,
            "source_binding_sha256": None, "original_receipt_path": None,
            "original_receipt_sha256": None,
        }],
    })
    assert len(index) < 1_048_576 and len(refs) <= 64 and len(sources) <= 65_536
    digest = hashlib.sha256(index).hexdigest()
    path = f"artifacts/product-provenance/indexes/{digest}.json"
    resources[path] = index
    total = sum(map(len, resources.values()))
    assert total > 33_554_432
    raw = _receipt(tmp_path, {
        "sources": {"src/current.py": LEAF_SHA},
        "historical_source_map": {"path": path, "sha256": digest, "byte_count": len(index)},
    })
    with pytest.raises(ValueError, match="^product_provenance_limit$"):
        codec.resolve_product_receipt(raw, expected_root=tmp_path, read_resource=resources.__getitem__)
    print(f"rejected_transport_bytes={total}; sources={len(sources)}; shards={len(refs)}")

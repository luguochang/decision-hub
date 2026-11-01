"""Bounded product receipt transport; it grants no Run or execution permission.

The raw receipt identity is never rewritten. V2 only moves the historical part
of its logical source map into immutable, flat, content-addressed resources.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any

RECEIPT_SCHEMAS = frozenset({"product-validated-builds.v1", "product-validated-builds.v2"})
RECEIPT_LIMIT = 2_097_152
INDEX_LIMIT = 1_048_576
SHARD_LIMIT = 1_048_576
TRANSPORT_LIMIT = 33_554_432
SOURCE_LIMIT = 65_536
TRANSPORT_PREFIX = "artifacts/product-provenance/"
SOURCE_REF = "packages/kernel/decision_hub_kernel/application/product_release_provenance.py"
_LOADED_SOURCE_SHA = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
_SHA = re.compile(r"[a-f0-9]{64}")
_UNSAFE_PATH = re.compile(r'[\\\x00-\x1f<>:"|?*]')
_ARCHIVE = re.compile(
    r"^(packages/kernel/(?:research_recipe_versions/(?:[a-f0-9]{8}|[a-f0-9]{64})"
    r"|market_component_versions/[0-9]{8}T[0-9]{9}Z))/"
)
_KEYS = {"schema", "repository_root", "sources", "artifacts", "plugin_build_hash", "evidence"}
ReadResource = Callable[[str], bytes]


def _fail(code: str = "product_provenance_invalid") -> ValueError:
    return ValueError(code)


def canonical(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _fail("product_provenance_duplicate_key")
        result[key] = value
    return result


def _json(raw: bytes) -> dict[str, Any]:
    value = json.loads(raw, object_pairs_hook=_unique)
    if type(value) is not dict:
        raise _fail()
    return value


def relative_path(value: object) -> str:
    if (
        type(value) is not str
        or not value
        or value.startswith("/")
        or _UNSAFE_PATH.search(value) is not None
        or any(part in {"", ".", ".."} or part.endswith((".", " ")) for part in value.split("/"))
    ):
        raise _fail("product_provenance_path_invalid")
    return value


def _digest(value: object) -> str:
    if type(value) is not str or _SHA.fullmatch(value) is None:
        raise _fail("product_provenance_hash_invalid")
    return value


def _archive_directory(name: str) -> str:
    match = _ARCHIVE.match(name)
    if match is None:
        raise _fail("product_provenance_owner_invalid")
    return match[1]


def _integer(value: object, *, maximum: int, minimum: int = 0) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise _fail("product_provenance_limit")
    return value


def _map(value: object, *, nonempty: bool = True) -> dict[str, str]:
    if type(value) is not dict or nonempty and not value or len(value) > SOURCE_LIMIT:
        raise _fail("product_provenance_source_map_invalid")
    result: dict[str, str] = {}
    spellings: set[str] = set()
    for key, digest in value.items():
        key = relative_path(key)
        if key.casefold() in spellings:
            raise _fail("product_provenance_path_alias")
        spellings.add(key.casefold())
        result[key] = _digest(digest)
    return result


def repository_file(root: Path, relative: str) -> Path:
    relative = relative_path(relative)
    root = root.absolute()
    root_status = root.lstat()
    if root.is_symlink() or getattr(root_status, "st_file_attributes", 0) & 0x400:
        raise _fail("product_provenance_linked_path")
    current = root
    for part in relative.split("/"):
        current /= part
        status = current.lstat()
        if current.is_symlink() or getattr(status, "st_file_attributes", 0) & 0x400:
            raise _fail("product_provenance_linked_path")
    if not current.is_file() or not current.resolve(strict=True).is_relative_to(
        root.resolve(strict=True)
    ):
        raise _fail("product_provenance_path_invalid")
    return current


def read_repository_file(root: Path, relative: str, *, limit: int | None = None) -> bytes:
    path = repository_file(root, relative)
    before = path.stat()
    if limit is not None and before.st_size > limit:
        raise _fail("product_provenance_limit")
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mtime_ns,
        ):
            raise _fail("product_provenance_source_changed")
        raw = stream.read() if limit is None else stream.read(limit + 1)
        ended = os.fstat(stream.fileno())

    def stamp(s: os.stat_result) -> tuple[int, int, int, int, int]:
        return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns

    final_path = repository_file(root, relative)
    if (
        final_path != path
        or stamp(before) != stamp(final_path.stat())
        or stamp(opened) != stamp(ended)
        or limit is not None
        and len(raw) > limit
    ):
        raise _fail("product_provenance_source_changed")
    return raw


def _ref(value: object, kind: str, maximum: int) -> dict[str, Any]:
    if type(value) is not dict or set(value) != {"path", "sha256", "byte_count"}:
        raise _fail("product_provenance_reference_invalid")
    digest = _digest(value["sha256"])
    if value["path"] != TRANSPORT_PREFIX + kind + "/" + digest + ".json":
        raise _fail("product_provenance_reference_invalid")
    relative_path(value["path"])
    _integer(value["byte_count"], maximum=maximum, minimum=1)
    return dict(value)


def _resource(ref: Mapping[str, Any], read: ReadResource) -> bytes:
    raw = read(ref["path"])
    if len(raw) != ref["byte_count"] or sha256(raw) != ref["sha256"]:
        raise _fail("product_provenance_resource_changed")
    return raw


def _loaded_codec() -> None:
    if sha256(Path(__file__).read_bytes()) != _LOADED_SOURCE_SHA:
        raise _fail("product_provenance_loaded_codec_changed")


@dataclass(frozen=True)
class ResolvedProductReceipt:
    raw: bytes
    value: Mapping[str, Any]
    source_sizes: Mapping[str, int]
    transport: Mapping[str, str]
    owners: tuple[Mapping[str, Any], ...]

    @property
    def sha256(self) -> str:
        return sha256(self.raw)


def _owner(value: object, sources: Mapping[str, str]) -> dict[str, Any]:
    keys = {
        "archive_root",
        "kind",
        "index_path",
        "index_sha256",
        "index_byte_count",
        "dependency_hash",
        "source_binding_mode",
        "source_binding_path",
        "source_binding_sha256",
        "original_receipt_path",
        "original_receipt_sha256",
    }
    if type(value) is not dict or set(value) != keys:
        raise _fail("product_provenance_owner_invalid")
    root = relative_path(value["archive_root"])
    match = _ARCHIVE.match(root + "/")
    expected_index = "release.json" if value["kind"] == "research_recipe" else "index.json"
    if (
        match is None
        or match[1] != root
        or value["kind"] not in {"research_recipe", "market_component"}
        or ("/research_recipe_versions/" in root) != (value["kind"] == "research_recipe")
        or value["index_path"] != root + "/" + expected_index
        or sources.get(value["index_path"]) != _digest(value["index_sha256"])
    ):
        raise _fail("product_provenance_owner_invalid")
    _integer(value["index_byte_count"], maximum=RECEIPT_LIMIT, minimum=1)
    for key in ("dependency_hash", "source_binding_sha256", "original_receipt_sha256"):
        if value[key] is not None:
            _digest(value[key])
    for key in ("source_binding_path", "original_receipt_path"):
        if value[key] is not None:
            relative_path(value[key])
    if (value["source_binding_path"] is None) != (value["source_binding_sha256"] is None):
        raise _fail("product_provenance_owner_invalid")
    if (value["original_receipt_path"] is None) != (value["original_receipt_sha256"] is None):
        raise _fail("product_provenance_owner_invalid")
    if value["source_binding_mode"] not in {
        "legacy_graph_index",
        "validated_release",
        "validated_prior_release",
        "managed_candidate_inventory",
    }:
        raise _fail("product_provenance_owner_invalid")
    if (
        value["source_binding_path"] is not None
        and sources.get(value["source_binding_path"]) != value["source_binding_sha256"]
    ):
        raise _fail("product_provenance_owner_invalid")
    if (
        value["original_receipt_path"] is not None
        and sources.get(root + "/root/" + value["original_receipt_path"])
        != value["original_receipt_sha256"]
    ):
        raise _fail("product_provenance_owner_invalid")
    return dict(value)


def resolve_product_receipt(
    raw: bytes,
    *,
    expected_root: Path,
    read_resource: ReadResource,
    expected_sha256: str | None = None,
) -> ResolvedProductReceipt:
    """Resolve immutable transport, preserving actual raw/schema identity.

    This does not grant full installation validation: the standard verifier
    hashes all logical leaves, while original readers retain their own gates.
    """
    _loaded_codec()
    if (
        len(raw) > RECEIPT_LIMIT
        or expected_sha256 is not None
        and sha256(raw) != _digest(expected_sha256)
    ):
        raise _fail("product_provenance_receipt_changed")
    value = _json(raw)
    schema = value.get("schema")
    if schema not in RECEIPT_SCHEMAS or set(value) != (
        _KEYS if schema.endswith(".v1") else _KEYS | {"historical_source_map"}
    ):
        raise _fail("product_provenance_receipt_schema_invalid")
    if (
        type(value["repository_root"]) is not str
        or not Path(value["repository_root"]).is_absolute()
        or Path(value["repository_root"]).resolve() != expected_root.resolve()
        or type(value["evidence"]) is not dict
    ):
        raise _fail("product_provenance_receipt_root_invalid")
    _digest(value["plugin_build_hash"])
    inline = _map(value["sources"])
    _map(value["artifacts"])
    sizes: dict[str, int] = {}
    transport: dict[str, str] = {}
    owners: tuple[Mapping[str, Any], ...] = ()
    if schema == "product-validated-builds.v2":
        if any(_ARCHIVE.match(name) for name in inline):
            raise _fail("product_provenance_inline_archive_invalid")
        ref = _ref(value["historical_source_map"], "indexes", INDEX_LIMIT)
        index_raw = _resource(ref, read_resource)
        transport[ref["path"]] = ref["sha256"]
        index = _json(index_raw)
        if (
            set(index)
            != {"schema_version", "source_count", "leafset_sha256", "shards", "archive_owners"}
            or index["schema_version"] != "product-historical-source-map.v1"
            or index_raw != canonical(index)
            or type(index["shards"]) is not list
            or len(index["shards"]) > 64
        ):
            raise _fail("product_provenance_index_invalid")
        count = _integer(index["source_count"], maximum=SOURCE_LIMIT)
        historical: dict[str, str] = {}
        total = len(index_raw)
        for item in index["shards"]:
            shard_ref = _ref(item, "shards", SHARD_LIMIT)
            if shard_ref["path"] in transport:
                raise _fail("product_provenance_duplicate_shard")
            total += shard_ref["byte_count"]
            if total > TRANSPORT_LIMIT:
                raise _fail("product_provenance_limit")
            shard_raw = _resource(shard_ref, read_resource)
            shard = _json(shard_raw)
            if (
                set(shard) != {"schema_version", "sources"}
                or shard["schema_version"] != "product-historical-source-shard.v1"
                or shard_raw != canonical(shard)
                or type(shard["sources"]) is not dict
                or not shard["sources"]
            ):
                raise _fail("product_provenance_shard_invalid")
            for name, leaf in shard["sources"].items():
                name = relative_path(name)
                if (
                    name in historical
                    or _ARCHIVE.match(name) is None
                    or type(leaf) is not dict
                    or set(leaf) != {"sha256", "byte_count"}
                ):
                    raise _fail("product_provenance_leaf_invalid")
                historical[name] = _digest(leaf["sha256"])
                sizes[name] = _integer(leaf["byte_count"], maximum=2**40)
                if len(historical) > SOURCE_LIMIT:
                    raise _fail("product_provenance_limit")
            transport[shard_ref["path"]] = shard_ref["sha256"]
        historical = _map(historical, nonempty=False)
        if len(historical) != count or sha256(canonical(historical)) != _digest(
            index["leafset_sha256"]
        ):
            raise _fail("product_provenance_leafset_changed")
        if type(index["archive_owners"]) is not list or len(index["archive_owners"]) > 512:
            raise _fail("product_provenance_owner_invalid")
        owner_rows = [_owner(item, historical) for item in index["archive_owners"]]
        roots = [item["archive_root"] for item in owner_rows]
        if roots != sorted(set(roots)) or set(roots) != {
            _archive_directory(name) for name in historical
        }:
            raise _fail("product_provenance_owner_closure_invalid")
        owners = tuple(MappingProxyType(item) for item in owner_rows)
        inline = _map({**inline, **historical})
    view = {key: value[key] for key in _KEYS}
    view["sources"] = inline
    return ResolvedProductReceipt(
        raw, MappingProxyType(view), MappingProxyType(sizes), MappingProxyType(transport), owners
    )


def parse_product_receipt(
    raw: bytes, *, expected_root: Path, read_resource: ReadResource
) -> dict[str, Any]:
    """Internal six-field projection; actual schema stays v1/v2, raw stays raw."""
    return dict(
        resolve_product_receipt(raw, expected_root=expected_root, read_resource=read_resource).value
    )


def _make_owner(root: str, sources: Mapping[str, str], read: ReadResource) -> dict[str, Any]:
    graph = "/research_recipe_versions/" in root
    name = root + ("/release.json" if graph else "/index.json")
    raw = read(name)
    if sources.get(name) != sha256(raw) or len(raw) > RECEIPT_LIMIT:
        raise _fail("product_provenance_owner_changed")
    index = _json(raw)
    binding = index.get("installation_binding") if graph else None
    original_ref = binding.get("receipt") if type(binding) is dict else None
    binding_name = None if graph else root + "/source-binding.json"
    binding_hash = None if binding_name is None else sources.get(binding_name)
    result = {
        "archive_root": root,
        "kind": "research_recipe" if graph else "market_component",
        "index_path": name,
        "index_sha256": sha256(raw),
        "index_byte_count": len(raw),
        "dependency_hash": index.get("graph_recipe_dependency_hash") if graph else None,
        "source_binding_mode": (
            "validated_release" if binding is not None else "legacy_graph_index"
        )
        if graph
        else index.get("source_binding_mode"),
        "source_binding_path": binding_name,
        "source_binding_sha256": binding_hash,
        "original_receipt_path": original_ref.get("path") if type(original_ref) is dict else None,
        "original_receipt_sha256": original_ref.get("sha256")
        if type(original_ref) is dict
        else None,
    }
    return _owner(result, sources)


def encode_historical_sources(
    sources: Mapping[str, str], *, read_resource: ReadResource
) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Pure encoding; caller publishes resources only after full fresh reads."""
    _loaded_codec()
    logical = _map(dict(sources))
    historical = {name: digest for name, digest in logical.items() if _ARCHIVE.match(name)}
    inline = {name: digest for name, digest in logical.items() if name not in historical}
    payloads: dict[str, bytes] = {}
    refs: list[dict[str, Any]] = []
    chunk: dict[str, dict[str, Any]] = {}

    def finish() -> None:
        raw = canonical({"schema_version": "product-historical-source-shard.v1", "sources": chunk})
        if len(raw) > SHARD_LIMIT:
            raise _fail("product_provenance_limit")
        digest = sha256(raw)
        name = TRANSPORT_PREFIX + "shards/" + digest + ".json"
        payloads[name] = raw
        refs.append({"path": name, "sha256": digest, "byte_count": len(raw)})

    # Track exact serialized entry lengths; avoid repeatedly encoding full chunks.
    size = len(canonical({"schema_version": "product-historical-source-shard.v1", "sources": {}}))
    for name, digest in sorted(historical.items()):
        raw = read_resource(name)
        if sha256(raw) != digest:
            raise _fail("product_provenance_leaf_changed")
        leaf = {"sha256": digest, "byte_count": len(raw)}
        entry_size = len(canonical({name: leaf})) - 3 + (1 if chunk else 0)
        if chunk and size + entry_size > SHARD_LIMIT:
            finish()
            chunk = {}
            size = len(
                canonical({"schema_version": "product-historical-source-shard.v1", "sources": {}})
            )
            entry_size = len(canonical({name: leaf})) - 3
        chunk[name] = leaf
        size += entry_size
    if chunk:
        finish()
    roots = sorted({_archive_directory(name) for name in historical})
    index = {
        "schema_version": "product-historical-source-map.v1",
        "source_count": len(historical),
        "leafset_sha256": sha256(canonical(dict(sorted(historical.items())))),
        "shards": refs,
        "archive_owners": [_make_owner(root, historical, read_resource) for root in roots],
    }
    raw = canonical(index)
    if (
        len(raw) > INDEX_LIMIT
        or sum(map(len, payloads.values())) + len(raw) > TRANSPORT_LIMIT
        or len(refs) > 64
    ):
        raise _fail("product_provenance_limit")
    digest = sha256(raw)
    name = TRANSPORT_PREFIX + "indexes/" + digest + ".json"
    payloads[name] = raw
    return {
        "sources": dict(sorted(inline.items())),
        "historical_source_map": {"path": name, "sha256": digest, "byte_count": len(raw)},
    }, payloads


def publish_transport(root: Path, payloads: Mapping[str, bytes]) -> None:
    for name, raw in payloads.items():
        relative_path(name)
        if not name.startswith(TRANSPORT_PREFIX):
            raise _fail()
        path = root / name
        current = root.absolute()
        for part in PurePosixPath(name).parts[:-1]:
            current /= part
            current.mkdir(exist_ok=True)
            status = current.lstat()
            if current.is_symlink() or getattr(status, "st_file_attributes", 0) & 0x400:
                raise _fail("product_provenance_linked_path")
        try:
            with path.open("xb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError:
            if read_repository_file(root, name, limit=SHARD_LIMIT) != raw:
                raise _fail("product_provenance_immutable_conflict") from None
        if read_repository_file(root, name, limit=SHARD_LIMIT) != raw:
            raise _fail("product_provenance_resource_changed")


def _verify_archive_owners(
    parsed: ResolvedProductReceipt, *, root: Path, read: ReadResource
) -> None:
    """Require every declared archive leaf, not merely its map's digest."""
    sources = parsed.value["sources"]
    for owner in parsed.owners:
        prefix = owner["archive_root"] + "/"
        raw = read(owner["index_path"])
        if len(raw) != owner["index_byte_count"] or sha256(raw) != owner["index_sha256"]:
            raise _fail("product_provenance_owner_changed")
        index = _json(raw)
        required = {owner["index_path"]: owner["index_sha256"]}
        if owner["kind"] == "research_recipe":
            if index.get("schema_version") not in {
                "research-retained-recipe-release.v1",
                "research-retained-recipe-release.v2",
                "research-retained-recipe-release.v3",
            }:
                raise _fail("product_provenance_owner_schema_invalid")
            if index.get("graph_recipe_dependency_hash") != owner["dependency_hash"]:
                raise _fail("product_provenance_owner_changed")
            files = _map(index.get("all_files"))
            stored, auxiliary = index.get("stored_files"), index.get("shared_auxiliary_files")
            if (
                type(stored) is not list
                or any(type(name) is not str for name in stored)
                or len(stored) != len(set(stored))
                or type(auxiliary) is not dict
                or set(files) != set(stored) | set(auxiliary)
                or set(stored) & set(auxiliary)
            ):
                raise _fail("product_provenance_owner_closure_invalid")
            for name, digest in files.items():
                destination = prefix + "root/" + name if name in stored else name
                if sources.get(destination) != digest:
                    raise _fail("product_provenance_owner_leaf_missing")
                if name in stored:
                    required[destination] = digest
            binding = index.get("installation_binding")
            if binding is not None:
                if (
                    type(binding) is not dict
                    or binding.get("repository_root") != parsed.value["repository_root"]
                    or binding.get("receipt")
                    != {
                        "path": owner["original_receipt_path"],
                        "sha256": owner["original_receipt_sha256"],
                    }
                ):
                    raise _fail("product_provenance_owner_binding_invalid")
                receipt_ref = binding["receipt"]
                resources = binding.get("source_map_resources", {})
                if type(resources) is not dict:
                    raise _fail("product_provenance_owner_binding_invalid")
                original_raw = read(prefix + "root/" + relative_path(receipt_ref["path"]))
                if sha256(original_raw) != receipt_ref["sha256"]:
                    raise _fail("product_provenance_owner_binding_invalid")
                original = resolve_product_receipt(
                    original_raw,
                    expected_root=root,
                    read_resource=lambda name, prefix=prefix: read(prefix + "root/" + name),
                )
                if dict(original.transport) != resources or any(
                    files.get(name) != digest for name, digest in resources.items()
                ):
                    raise _fail("product_provenance_owner_binding_invalid")
                pointer = binding.get("pointer")
                if (
                    type(pointer) is not dict
                    or set(pointer) != {"path", "sha256"}
                    or original.value["sources"].get(pointer["path"]) != pointer["sha256"]
                    or files.get(pointer["path"]) != pointer["sha256"]
                ):
                    raise _fail("product_provenance_owner_binding_invalid")
        else:
            if index.get("schema_version") not in {
                "market-component-retained-release.v1",
                "market-component-retained-release.v2",
            }:
                raise _fail("product_provenance_owner_schema_invalid")
            files = _map(index.get("files"))
            binding_name = owner["source_binding_path"]
            if (
                binding_name != prefix + "source-binding.json"
                or index.get("source_binding_sha256") != owner["source_binding_sha256"]
            ):
                raise _fail("product_provenance_owner_binding_invalid")
            binding_raw = read(binding_name)
            if sha256(binding_raw) != owner["source_binding_sha256"]:
                raise _fail("product_provenance_owner_binding_invalid")
            required[binding_name] = owner["source_binding_sha256"]
            mode = owner["source_binding_mode"]
            resources = index.get("source_binding_resources", {})
            if type(resources) is not dict or mode != index.get("source_binding_mode"):
                raise _fail("product_provenance_owner_binding_invalid")
            if mode == "validated_prior_release":
                original = resolve_product_receipt(
                    binding_raw,
                    expected_root=root,
                    read_resource=lambda name, prefix=prefix: read(prefix + "root/" + name),
                )
                original_sources = original.value["sources"]
                if dict(original.transport) != resources:
                    raise _fail("product_provenance_owner_binding_invalid")
            elif mode == "managed_candidate_inventory":
                original_sources = _map(_json(binding_raw))
                if resources:
                    raise _fail("product_provenance_owner_binding_invalid")
            else:
                raise _fail("product_provenance_owner_binding_invalid")
            for name, digest in {**files, **_map(resources, nonempty=False)}.items():
                destination = prefix + "root/" + name
                if name in files and original_sources.get(name) != digest:
                    raise _fail("product_provenance_owner_leaf_missing")
                required[destination] = digest
        actual_group = {name: digest for name, digest in sources.items() if name.startswith(prefix)}
        if actual_group != required:
            raise _fail("product_provenance_owner_closure_invalid")


def _plugin_build_inputs(required: set[str]) -> set[str]:
    # Reuse the fresh, link-checked current inventory, never the declared inputs.
    names = {
        name for name in required
        if name.startswith(("extensions/dsh/decision-hub/src/", "packages/contracts_ts/src/"))
        and name != "extensions/dsh/decision-hub/src/generated-build-identity.ts"
    }
    names.update({
        "extensions/dsh/decision-hub/package.json",
        "extensions/dsh/decision-hub/cordis.patch.yml",
        "extensions/dsh/decision-hub/scripts/generate-build-identity.mjs",
        "extensions/dsh/decision-hub/scripts/build-client.mjs",
        "extensions/dsh/decision-hub/vite.host.config.ts",
        "extensions/dsh/decision-hub/tsconfig.json",
        "package.json",
        "pnpm-lock.yaml",
        "pnpm-workspace.yaml",
        "packages/contracts_ts/package.json",
        "infra/dsh/presets/decision-research/agent.cordis.yml",
        "infra/dsh/presets/decision-research/preset.yml",
        "infra/dsh/upstream.lock.json",
    })
    return names


def verify_product_receipt_v2(root: Path, receipt_path: Path) -> dict[str, Any]:
    """Full v2 standard guard. It is separate from the fixed v1 verifier."""
    relative = receipt_path.absolute().relative_to(root.absolute()).as_posix()

    def read(name: str) -> bytes:
        return read_repository_file(root, name)

    raw = read_repository_file(root, relative, limit=RECEIPT_LIMIT)
    parsed = resolve_product_receipt(raw, expected_root=root, read_resource=read)
    value = parsed.value
    if value["schema"] != "product-validated-builds.v2":
        raise _fail("product_provenance_v2_required")
    _verify_archive_owners(parsed, root=root, read=read)
    sources, artifacts = value["sources"], value["artifacts"]
    for mapping, artifact in ((sources, False), (artifacts, True)):
        for name, digest in mapping.items():
            if artifact and not name.startswith(
                ("apps/decision-desk/dist/", "extensions/dsh/decision-hub/lib/")
            ):
                raise _fail("product_provenance_artifact_scope_invalid")
            data = read(name)
            if (
                sha256(data) != digest
                or name in parsed.source_sizes
                and len(data) != parsed.source_sizes[name]
            ):
                raise _fail("product_provenance_leaf_changed")
    required = {
        "package.json",
        "pnpm-lock.yaml",
        "pnpm-workspace.yaml",
        "apps/decision-desk/index.html",
        "apps/decision-desk/package.json",
        "apps/decision-desk/tsconfig.json",
        "apps/decision-desk/vite.config.ts",
        "packages/contracts_ts/package.json",
        "extensions/dsh/decision-hub/package.json",
        "extensions/dsh/decision-hub/cordis.patch.yml",
        "extensions/dsh/decision-hub/tsconfig.json",
        "extensions/dsh/decision-hub/vite.host.config.ts",
        "infra/dsh/presets/decision-research/agent.cordis.yml",
        "infra/dsh/presets/decision-research/preset.yml",
        "infra/dsh/upstream.lock.json",
        SOURCE_REF,
        "infra/dsh/validated-product-builds-v2.ps1",
        "infra/dsh/product-build-validation.ps1",
        "infra/dsh/product-artifact-manifest.py",
    }
    for name in (
        "apps/decision-desk/src",
        "apps/decision-desk/public",
        "packages/contracts_ts/src",
        "contracts/schemas",
        "extensions/dsh/decision-hub/src",
        "extensions/dsh/decision-hub/scripts",
    ):
        directory = root / name
        status = directory.lstat()
        if (
            not directory.is_dir()
            or directory.is_symlink()
            or getattr(status, "st_file_attributes", 0) & 0x400
        ):
            raise _fail("product_provenance_linked_path")
        for path in directory.rglob("*"):
            status = path.lstat()
            if path.is_symlink() or getattr(status, "st_file_attributes", 0) & 0x400:
                raise _fail("product_provenance_linked_path")
            if path.is_file():
                required.add(path.relative_to(root).as_posix())
    if not required <= sources.keys():
        raise _fail("product_provenance_current_source_missing")
    required_artifacts = {
        "apps/decision-desk/dist/index.html",
        "extensions/dsh/decision-hub/lib/index.js",
        "extensions/dsh/decision-hub/lib/research-tool.js",
        "extensions/dsh/decision-hub/lib/synthesis-tool.js",
        "extensions/dsh/decision-hub/lib/client.js",
    }
    for path in (root / "extensions/dsh/decision-hub/lib").rglob("*"):
        if path.is_file():
            required_artifacts.add(path.relative_to(root).as_posix())
    html = read("apps/decision-desk/dist/index.html").decode("utf-8")
    for href in re.findall(r"""(?:src|href)\s*=\s*["']([^"']+)["']""", html):
        if re.match(r"^(?:[a-z]+:|//|#)", href):
            continue
        href = re.split(r"[?#]", href, maxsplit=1)[0].lstrip("/")
        if href:
            required_artifacts.add("apps/decision-desk/dist/" + href)
    if not required_artifacts <= artifacts.keys():
        raise _fail("product_provenance_current_artifact_missing")
    # Reuse the already authenticated current emitted/public/Host closure
    # producer, executing its exact bytes rather than importing mutable state.
    tool_ref = "infra/dsh/product-artifact-manifest.py"
    tool_raw = read(tool_ref)
    if sources.get(tool_ref) != sha256(tool_raw):
        raise _fail("product_provenance_artifact_reader_unbound")
    namespace: dict[str, Any] = {
        "__name__": "_product_provenance_artifact_reader",
        "__file__": str(root / tool_ref),
    }
    exec(compile(tool_raw, str(root / tool_ref), "exec"), namespace)
    public = {
        name: digest
        for name, digest in sources.items()
        if name.startswith("apps/decision-desk/public/")
    }
    if namespace["artifact_map"](root, public) != artifacts or read(tool_ref) != tool_raw:
        raise _fail("product_provenance_artifact_closure_changed")
    identity = read("extensions/dsh/decision-hub/src/generated-build-identity.ts").decode("utf-8")
    plugin_match = re.search(r'PLUGIN_BUILD_HASH\s*=\s*"([a-f0-9]{64})"', identity)
    inputs_match = re.search(r"PLUGIN_BUILD_INPUTS\s*=\s*(\[[^\r\n]*\])\s+as const;", identity)
    expected_inputs = _plugin_build_inputs(required)
    if (
        plugin_match is None
        or inputs_match is None
        or plugin_match[1] != value["plugin_build_hash"]
    ):
        raise _fail("product_provenance_plugin_identity_invalid")
    inputs = json.loads(inputs_match[1])
    if (
        type(inputs) is not list
        or any(type(name) is not str for name in inputs)
        or len(inputs) != len(set(inputs))
        or set(inputs) != set(expected_inputs)
    ):
        raise _fail("product_provenance_plugin_inputs_invalid")
    hasher = hashlib.sha256()
    for name in inputs:
        if name not in sources:
            raise _fail("product_provenance_plugin_input_unbound")
        body = read(name)
        hasher.update((name + "\0" + str(len(body)) + ":").encode("utf-8"))
        hasher.update(body)
        hasher.update(b"\0")
    if hasher.hexdigest() != value["plugin_build_hash"] or value["plugin_build_hash"] not in read(
        "extensions/dsh/decision-hub/lib/index.js"
    ).decode("utf-8"):
        raise _fail("product_provenance_plugin_identity_changed")
    for name, digest in parsed.transport.items():
        if sha256(read(name)) != digest:
            raise _fail("product_provenance_resource_changed")
    if read(relative) != raw:
        raise _fail("product_provenance_receipt_changed")
    return {
        "receipt_path": str(receipt_path.absolute()),
        "receipt_sha256": parsed.sha256,
        "receipt_schema": value["schema"],
        "plugin_build_hash": value["plugin_build_hash"],
        "source_count": len(sources),
        "artifact_count": len(artifacts),
        "transport_count": len(parsed.transport),
        "verifier_source_sha256": _LOADED_SOURCE_SHA,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", required=True, type=Path)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--resolve", type=Path)
    group.add_argument("--verify", type=Path)
    group.add_argument("--encode", action="store_true")
    args = parser.parse_args()
    root = args.repository_root.resolve(strict=True)

    def read(name: str) -> bytes:
        return read_repository_file(root, name)

    if args.encode:
        import sys

        sources = _json(sys.stdin.buffer.read(16_777_217))
        projection, payloads = encode_historical_sources(sources, read_resource=read)
        publish_transport(root, payloads)
        output = projection
    elif args.verify is not None:
        output = verify_product_receipt_v2(root, args.verify)
    else:
        path = args.resolve.absolute().relative_to(root).as_posix()
        parsed = resolve_product_receipt(
            read_repository_file(root, path, limit=RECEIPT_LIMIT),
            expected_root=root,
            read_resource=read,
        )
        output = dict(parsed.value)
    print(json.dumps(output, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()

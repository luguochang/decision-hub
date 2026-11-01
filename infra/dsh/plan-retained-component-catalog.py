"""Keep each installed version's first archive provider, without deleting originals.

Invoked only by the standard build's explicit opt-in. Reads a physical SQLite
snapshot and complete file originals; it creates no DB/registry/pointer writes.
Every kept leaf remains directly pinned in the ordinary release source map.
"""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import re
import sqlite3
import sys
from pathlib import Path


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def manifest_identity(value: dict) -> bytes:
    # The existing loader supplies the archived parent_version_id to current
    # declarations. Preserve all other manifest fields exactly.
    return canonical({key: item for key, item in value.items() if key != "parent_version_id"})


def first_provider_plan(archives: list[dict], current: dict, required: set[str]) -> dict:
    """Pure, order preserving reduction; never substitute a newer cover."""
    first: dict[str, dict] = {}
    identity: dict[str, bytes] = {}
    retained = []
    for position, archive in enumerate(archives):
        introduced = []
        for item in archive["components"]:
            manifest = item["manifest"]
            version = manifest["version_id"]
            same = manifest_identity(manifest)
            if version in identity and identity[version] != same:
                raise ValueError("component_catalog_version_identity_conflict")
            identity[version] = same
            if version not in first:
                introduced.append(version)
                first[version] = {
                    "index_ref": archive["reference"]["index_ref"],
                    "index_sha256": archive["reference"]["index_sha256"],
                    "closure_hash": archive["closure_hash"],
                    "position": position,
                }
        if introduced:
            retained.append(archive["reference"])
    current_ids = set()
    for item in current["components"]:
        manifest = item["manifest"]
        version = manifest["version_id"]
        if version in current_ids:
            raise ValueError("component_catalog_current_version_duplicate")
        current_ids.add(version)
        if version in identity and identity[version] != manifest_identity(manifest):
            raise ValueError("component_catalog_current_identity_conflict")
    if required - (set(first) | current_ids):
        raise ValueError("component_catalog_bound_version_missing")
    # Retain ALL catalog versions, including currently unapproved/unreferenced
    # versions. Bootstrap remains current-live first then original archive order.
    if current["reference"] not in retained:
        retained.append(current["reference"])
    return {
        "archives": retained,
        "first_providers": first,
        "catalog_version_count": len(first),
        "current_version_count": len(current_ids),
        "required_version_count": len(required),
    }


def read_required_versions(database: Path, known_manifests: dict[str, dict]) -> dict:
    connection = sqlite3.connect(database.resolve(strict=True).as_uri() + "?mode=ro", uri=True)
    connection.execute("PRAGMA query_only=ON")
    connection.execute("BEGIN")
    required = set()
    counts = {}
    try:
        for version, raw, expected in connection.execute(
            "SELECT version_id,payload_json,payload_hash FROM market_component_versions"
        ):
            manifest = json.loads(raw)
            if (
                manifest.get("version_id") != version
                or digest(canonical(manifest)) != expected
                or version not in known_manifests
                or manifest_identity(manifest) != manifest_identity(known_manifests[version])
            ):
                raise ValueError("component_catalog_registered_identity_unavailable")
            required.add(version)
            if manifest.get("parent_version_id"):
                required.add(manifest["parent_version_id"])
        counts["registered"] = len(required)
        for table in ("market_component_pointers", "market_component_reviews"):
            rows = connection.execute(f"SELECT DISTINCT version_id FROM {table}").fetchall()
            required.update(row[0] for row in rows)
            counts[table] = len(rows)
        for version, baseline in connection.execute(
            "SELECT version_id,json_extract(payload_json,'$.baseline_version_id') "
            "FROM market_component_shadows"
        ):
            required.add(version)
            if baseline:
                required.add(baseline)
        states = connection.execute(
            "SELECT json_extract(payload_json,'$.state'),"
            "json_extract(payload_json,'$.selection.status'),"
            "json_extract(payload_json,'$.selection.manifest.version_id'),count(*) "
            "FROM market_component_audits WHERE audit_id LIKE 'cconsume_%' GROUP BY 1,2,3"
        ).fetchall()
        for state, status, version, _ in states:
            if state == "selected" and status == "selected" and version:
                required.add(version)
            elif not (
                state == "baseline_not_registry_approved"
                and status == "unavailable"
                and version is None
            ):
                raise ValueError("component_catalog_consumption_identity_unknown")
        changes = connection.total_changes
    finally:
        connection.rollback()
        rolled_back = not connection.in_transaction
        connection.close()
    if changes != 0 or not rolled_back:
        raise ValueError("component_catalog_readonly_boundary_invalid")
    return {
        "version_ids": sorted(required),
        "counts": counts,
        "consumption_states": states,
        "mode": "ro",
        "query_only": True,
        "begin": True,
        "rollback": True,
        "total_changes": 0,
    }


def produce(
    root: Path,
    prior_path: Path,
    prior_hash: str,
    database: Path,
    current_path: Path,
    current_hash: str,
) -> dict:
    root = root.resolve(strict=True)
    sys.path.insert(0, str(root))
    from apps.hub_worker.market_component_bootstrap import declare_market_components
    from apps.hub_worker.market_component_versions import (
        _binding_sources,  # pyright: ignore[reportPrivateUsage]
        _json,
        _path,
        _PrivateComponentRelease,
        _receipt,
        _runtime,
    )
    from packages.kernel.decision_hub_kernel.ports.component_registry import ComponentManifest

    prior, _ = _receipt(root, prior_path, prior_hash)
    source_map = prior["sources"]
    references = prior.get("evidence", {}).get("retained_market_components_v1", {}).get("archives")
    if not isinstance(references, list) or len(references) > 128:
        raise ValueError("component_catalog_prior_evidence_invalid")

    def authenticate(reference: dict, pins: dict[str, str]) -> dict:
        if set(reference) != {"index_ref", "index_sha256"} or not re.fullmatch(
            r"packages/kernel/market_component_versions/[0-9]{8}T[0-9]{9}Z/index.json",
            reference["index_ref"],
        ):
            raise ValueError("component_catalog_index_scope_invalid")
        path = _path(root, reference["index_ref"])
        raw = path.read_bytes()
        if digest(raw) != reference["index_sha256"] or pins.get(reference["index_ref"]) != digest(
            raw
        ):
            raise ValueError("component_catalog_index_unbound")
        index = _json(raw)
        if (
            index.get("schema_version") not in {
                "market-component-retained-release.v1", "market-component-retained-release.v2"
            }
            or index.get("runtime") != _runtime()
        ):
            raise ValueError("component_catalog_archive_identity_invalid")
        binding_path = _path(path.parent, "source-binding.json")
        binding_raw = binding_path.read_bytes()
        if digest(binding_raw) != index.get("source_binding_sha256") or pins.get(
            binding_path.relative_to(root).as_posix()
        ) != digest(binding_raw):
            raise ValueError("component_catalog_binding_unbound")
        original_map, _ = _binding_sources(root, path.parent, index, pins)
        files = index["files"]
        if not isinstance(files, dict) or not files or len(files) > 4096:
            raise ValueError("component_catalog_files_invalid")
        for name, expected in files.items():
            leaf = _path(path.parent / "root", name)
            if (
                pins.get(leaf.relative_to(root).as_posix()) != expected
                or original_map.get(name) != expected
            ):
                raise ValueError("component_catalog_leaf_unbound")
        _PrivateComponentRelease(path.parent / "root", files, reference["index_sha256"]).verify()
        components = index["components"]
        manifests = [ComponentManifest.model_validate(item["manifest"]) for item in components]
        if len({item.version_id for item in manifests}) != len(manifests):
            raise ValueError("component_catalog_version_duplicate")
        return {
            "reference": reference,
            "components": components,
            "closure_hash": digest(canonical(files)),
        }

    archives = [authenticate(reference, source_map) for reference in references]
    preparation_raw = current_path.read_bytes()
    if digest(preparation_raw) != current_hash:
        raise ValueError("component_catalog_current_preparation_changed")
    preparation = _json(preparation_raw)
    current = authenticate(
        {key: preparation[key] for key in ("index_ref", "index_sha256")},
        preparation["source_additions"],
    )

    def live_declarations() -> list[dict]:
        output = []
        for item in declare_market_components():
            source = Path(inspect.getsourcefile(item.callback) or "").resolve(strict=True)
            code = {
                key: path.resolve(strict=True).relative_to(root).as_posix()
                for key, path in item.code_files.items()
            }
            resources = {
                key: path.resolve(strict=True).relative_to(root).as_posix()
                for key, path in item.resource_files.items()
            }
            if item.callback.__qualname__ != item.callback.__name__:
                raise ValueError("component_catalog_current_callable_invalid")
            output.append(
                {
                    "manifest": item.manifest().model_dump(mode="json"),
                    "module": source.relative_to(root)
                    .as_posix()
                    .removesuffix(".py")
                    .replace("/", "."),
                    "function": item.callback.__name__,
                    "code_files": code,
                    "resource_files": resources,
                }
            )
        return output

    live = live_declarations()
    if live != current["components"]:
        raise ValueError("component_catalog_current_declaration_mismatch")
    known = {
        item["manifest"]["version_id"]: item["manifest"]
        for archive in [*archives, current]
        for item in archive["components"]
    }
    snapshot = read_required_versions(database, known)
    plan = first_provider_plan(archives, current, set(snapshot["version_ids"]))
    dropped = [ref for ref in references if ref not in plan["archives"]]
    dropped_prefixes = [ref["index_ref"].rsplit("/", 1)[0] + "/" for ref in dropped]
    dropped_sources = {
        name: expected
        for name, expected in source_map.items()
        if any(name.startswith(prefix) for prefix in dropped_prefixes)
    }
    # Recheck fixed inputs after all fresh originals / the RO snapshot were read.
    _receipt(root, prior_path, prior_hash)
    if digest(current_path.read_bytes()) != current_hash:
        raise ValueError("component_catalog_current_preparation_changed")
    if live_declarations() != live:
        raise ValueError("component_catalog_current_declaration_changed")
    return {
        "schema_version": "managed-component-catalog-retention-plan.v1",
        "passed": True,
        "repository_root": str(root),
        "prior_receipt_sha256": prior_hash,
        "current_preparation_sha256": current_hash,
        **plan,
        "readonly_snapshot": snapshot,
        "dropped_archive_refs": dropped,
        "omitted_source_count": len(dropped_sources),
        "omitted_source_map_bytes": len(canonical(dropped_sources)) - 2,
        "preserves_all_catalog_versions": True,
        "deletes_originals": False,
        "changes_archive_loader": False,
        "grants_approval": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--prior-receipt-path", type=Path, required=True)
    parser.add_argument("--prior-receipt-sha256", required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--current-archive-preparation", type=Path, required=True)
    parser.add_argument("--current-archive-preparation-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = produce(
        args.repository_root,
        args.prior_receipt_path,
        args.prior_receipt_sha256,
        args.database,
        args.current_archive_preparation,
        args.current_archive_preparation_sha256,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("xb") as stream:
        stream.write(json.dumps(result, ensure_ascii=False, indent=2).encode() + b"\n")
    print(
        json.dumps(
            {
                "passed": True,
                "retained_archives": len(result["archives"]),
                "omitted_source_count": result["omitted_source_count"],
                "omitted_source_map_bytes": result["omitted_source_map_bytes"],
            }
        )
    )


if __name__ == "__main__":
    main()

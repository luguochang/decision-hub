"""File-only, hash-bound retention prepare; product startup/migrations are separate.

Rebuild an isolated original view from authenticated installed hashes. The existing
producer runs from that view before applying an overlay, preserving its semantics.
The relocated receipt is preparation evidence, never an installed release receipt.
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import importlib
import json
import os
import re
import sqlite3
import sys
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path, PurePosixPath
from types import ModuleType, SimpleNamespace
from typing import Any

PREFIX = "packages/kernel/research_recipe_versions/"
ARCHIVE_PRODUCER = "packages/kernel/decision_hub_kernel/application/research_recipe_archive.py"
SHA = re.compile(r"[a-f0-9]{64}")
PROVENANCE_CODEC = "packages/kernel/decision_hub_kernel/application/product_release_provenance.py"


@contextmanager
def original_archive_producer(root: Path, mirror: Path, inventory: dict[str, str], *, v2: bool):
    """File-only isolated borrowing; business modules still come from originals.

    This command owns its process. Never overlay this namespace in a running
    application: current modules are restored even when original admission fails.
    """
    def names(name: str) -> bool:
        return name == "packages" or name.startswith("packages.")
    existing = {name: module for name, module in sys.modules.copy().items() if names(name)}
    prior_path = list(sys.path)
    pins: dict[str, str] = {}
    try:
        for name in existing:
            del sys.modules[name]
        sys.path.insert(0, str(mirror))
        package = "packages.kernel.decision_hub_kernel.application"
        for name in ("packages", "packages.kernel", "packages.kernel.decision_hub_kernel", package):
            parent = importlib.import_module(name)
            expected = (mirror / name.replace(".", "/")).resolve()
            if (list(parent.__path__) != [str(expected)]
                    or not isinstance(parent.__file__, str)
                    or Path(parent.__file__).resolve() != expected / "__init__.py"):
                raise ValueError("retention_original_namespace_invalid")
        if v2:
            # These are explicit producer dependencies of this new code epoch;
            # they never become missing historical business-source substitutes.
            for relative in (PROVENANCE_CODEC, ARCHIVE_PRODUCER):
                raw = read(scoped(root, relative))
                if inventory.get(relative) != digest(raw):
                    raise ValueError("retention_current_producer_unvalidated")
                pins[relative] = digest(raw)
                name = relative.removesuffix(".py").replace("/", ".")
                module = ModuleType(name)
                module.__package__ = package
                module.__file__ = str(
                    root / relative if relative == PROVENANCE_CODEC else mirror / relative
                )
                sys.modules[name] = module
                setattr(parent, name.rsplit(".", 1)[1], module)
                exec(compile(raw, str(root / relative), "exec"), module.__dict__)
            producer = sys.modules[ARCHIVE_PRODUCER.removesuffix(".py").replace("/", ".")]
        else:
            producer = importlib.import_module(package + ".research_recipe_archive")
            if (not isinstance(producer.__file__, str)
                    or Path(producer.__file__).resolve()
                    != scoped(mirror, ARCHIVE_PRODUCER).resolve()):
                raise ValueError("retention_original_namespace_invalid")
        yield producer, pins
        for relative, expected in pins.items():
            if digest(read(scoped(root, relative))) != expected:
                raise ValueError("retention_current_producer_changed")
    finally:
        for name in list(sys.modules):
            if names(name):
                del sys.modules[name]
        sys.modules.update(existing)
        sys.path[:] = prior_path


def is_recipe_manifest_input(name: str) -> bool:
    """Match paths observable by the original producer's default v2 manifest.

    Its AST resolver reads packages Python, its schema scan reads
    contracts/schemas JSON/YAML, and its finite method declaration reads approved02.
    Full product and component source guards remain
    independent of which changes are routed to this recipe producer.
    """
    if name == "docs/design/decision-product-control-2026-10-17/02_DECISION_METHOD_AND_EVALUATION.md":
        return True
    suffix = PurePosixPath(name).suffix
    return (name.startswith("packages/") and suffix == ".py") or (
        name.startswith("contracts/schemas/") and suffix in {".json", ".yaml", ".yml"}
    )


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def io_path(path):
    return Path("\\\\?\\" + str(path.absolute())) if os.name == "nt" else path


def read(path):
    return io_path(path).read_bytes()


def scoped(root, relative):
    name = PurePosixPath(relative)
    if (
        not relative
        or "\\" in relative
        or ":" in relative
        or name.is_absolute()
        or any(part in ("", ".", "..") for part in relative.split("/"))
    ):
        raise ValueError("retention_path_invalid")
    path = root.joinpath(*name.parts)
    for item in (path, *path.parents):
        if item == root:
            break
        if io_path(item).exists() and (
            io_path(item).is_symlink()
            or getattr(io_path(item).lstat(), "st_file_attributes", 0) & 0x400
        ):
            raise ValueError("retention_linked_path")
    return path


def publish(path, raw):
    io_path(path.parent).mkdir(parents=True, exist_ok=True)
    if io_path(path).exists():
        if read(path) != raw:
            raise ValueError("retention_immutable_destination_changed")
        return
    # The complete, flushed same-directory file is atomically linked into place.
    # link() creates a new name only: it cannot overwrite an old release resource.
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with io_path(temporary).open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(io_path(temporary), io_path(path))
        except FileExistsError:
            if read(path) != raw:
                raise ValueError("retention_immutable_destination_changed") from None
    finally:
        io_path(temporary).unlink(missing_ok=True)


def publish_mirror_original(path, source, expected_sha256, relative):
    """Reuse only authenticated immutable history in the temporary old view."""
    raw = read(source)
    if digest(raw) != expected_sha256:
        raise ValueError("retention_original_source_changed")
    historical = relative.startswith((
        "packages/kernel/research_recipe_versions/",
        "packages/kernel/market_component_versions/",
        "packages/kernel/research_skill_versions/",
        "packages/kernel/decision_hub_kernel/application/context_selector_versions/",
    ))
    if historical:
        io_path(path.parent).mkdir(parents=True, exist_ok=True)
        try:
            os.link(io_path(source), io_path(path))
        except FileExistsError:
            if read(path) != raw:
                raise ValueError("retention_immutable_destination_changed") from None
        except OSError as error:
            if error.errno != errno.EXDEV and getattr(error, "winerror", None) != 17:
                raise
            publish(path, raw)
    else:
        publish(path, raw)
    if digest(read(source)) != expected_sha256:
        raise ValueError("retention_original_source_changed")
    if digest(read(path)) != expected_sha256:
        raise ValueError("retention_immutable_destination_changed")


def registry_rows(registry):
    if (type(registry) is not dict or set(registry) != {"schema_version", "registrations"}
            or registry["schema_version"] not in {"research-retained-recipe-registry.v1", "research-retained-recipe-registry.v2"}
            or type(registry["registrations"]) is not list or len(registry["registrations"]) > 256):
        raise ValueError("retention_registry_invalid")
    rows, graphs = {}, set()
    v2 = registry["schema_version"] == "research-retained-recipe-registry.v2"
    for row in registry["registrations"]:
        if (type(row) is not dict or set(row) != {
                "directory", "index_hash", "graph_hash", "selector_hash", "private_original_orm"}
                or any(type(row[key]) is not str or SHA.fullmatch(row[key]) is None
                       for key in ("directory", "index_hash", "graph_hash", "selector_hash"))
                or not v2 and (row["directory"] != row["graph_hash"] or row["graph_hash"] in graphs)
                or row["private_original_orm"] is not True or row["directory"] in rows):
            raise ValueError("retention_registry_invalid")
        rows[row["directory"]] = row
        graphs.add(row["graph_hash"])
    if registry["registrations"] != sorted(rows.values(), key=(lambda row: (row["graph_hash"], row["directory"]))
                                           if v2 else (lambda row: row["graph_hash"])):
        raise ValueError("retention_registry_invalid")
    return rows


def union_registries(*registries):
    rows = {}
    v2 = any(registry["schema_version"] == "research-retained-recipe-registry.v2" for registry in registries)
    for registry in registries:
        for key, row in registry_rows(registry).items():
            if key in rows and rows[key] != row:
                raise ValueError("retention_registry_union_conflict")
            rows[key] = row
    return {"schema_version": f"research-retained-recipe-registry.v{2 if v2 else 1}",
            "registrations": sorted(rows.values(), key=(lambda row: (row["graph_hash"], row["directory"]))
                                    if v2 else (lambda row: row["graph_hash"]))}


def canonical(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def compatible_v3_capture_registry(prior_registry, captured_registry, capture_directory, *, older):
    """Retain all current rows; an older recovery never replaces a registered identity."""
    rows, old_rows = registry_rows(captured_registry), registry_rows(prior_registry)
    if (capture_directory not in rows or set(rows) - set(old_rows) - {capture_directory}
            or any(rows[key] != old_rows[key] for key in set(rows) & set(old_rows))
            or not older and set(rows) != set(old_rows) | {capture_directory}):
        raise ValueError("retention_v3_capture_prior_registry_changed")


def retained_v3_installation_directory(graph_hash, installation_receipt, installed_pointer):
    if type(graph_hash) is not str or SHA.fullmatch(graph_hash) is None:
        raise ValueError("retention_v3_capture_directory_invalid")
    for ref in (installation_receipt, installed_pointer):
        if (type(ref) is not dict or set(ref) != {"path", "sha256"}
                or type(ref["sha256"]) is not str or SHA.fullmatch(ref["sha256"]) is None):
            raise ValueError("retention_v3_capture_directory_invalid")
        name = ref["path"]
        if (type(name) is not str or not name or PurePosixPath(name).is_absolute()
                or PurePosixPath(name).as_posix() != name
                or any(part in {"", ".", ".."} for part in name.split("/"))
                or re.search(r'[\\\x00-\x1f<>:"|?*]', name)):
            raise ValueError("retention_v3_capture_directory_invalid")
    return digest(canonical({"schema_version": "retained-v3-installation-key.v1", "graph_hash": graph_hash,
                             "installation_receipt": installation_receipt, "installed_pointer": installed_pointer})[:-1])


def selected_v3_retention_proof(root: Path, database: Path, registry, captured=None):
    """Indexed, bounded original observation; a matching graph alone is insufficient."""
    database = database.absolute()
    scoped(root, database.relative_to(root).as_posix())
    effective = registry if captured is None else union_registries(registry, captured["registry"])
    registrations = registry_rows(effective)
    connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, isolation_level=None)
    rows, native_rows, total, run_count, legacy_count = [], [], 0, 0, 0
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        enumeration = "SELECT DISTINCT run_id FROM run_role_outputs ORDER BY run_id"
        plan = connection.execute("EXPLAIN QUERY PLAN " + enumeration).fetchall()
        lookup = ("SELECT output_id,run_id,role,schema_version,payload_json,payload_hash,created_at "
                  "FROM run_role_outputs WHERE run_id=? AND role='graph_recipe_selection'")
        point_plan = connection.execute("EXPLAIN QUERY PLAN " + lookup, ("coverage-plan",)).fetchall()
        if (not any("COVERING INDEX" in row[3].upper() for row in plan)
                or not any("SEARCH " in row[3].upper() and "RUN_ID=?" in row[3].upper()
                           and "ROLE=?" in row[3].upper() for row in point_plan)):
            raise ValueError("retention_selection_covering_index_required")
        for (run_id,) in connection.execute(enumeration):
            run_count += 1
            originals = connection.execute(lookup, (run_id,)).fetchall()
            if len(originals) > 1:
                raise ValueError("retention_selection_original_invalid")
            for output_id, saved_run, role, schema, encoded, expected, created in originals:
                if (any(type(item) is not str for item in (
                        output_id, saved_run, role, schema, encoded, expected, created))
                        or SHA.fullmatch(expected) is None
                        or schema not in {"research-graph-recipe-selection.v2", "research-graph-recipe-selection.v3"}):
                    raise ValueError("retention_selection_original_invalid")
                if len(native_rows) >= 4096:
                    raise ValueError("retention_selection_observation_limit")
                raw = encoded.encode()
                total += len(raw)
                if len(raw) > 1024 * 1024 or total > 16 * 1024 * 1024:
                    raise ValueError("retention_selection_observation_limit")
                value = json.loads(encoded)
                version = 2 if schema == "research-graph-recipe-selection.v2" else 3
                if (digest(raw) != expected or canonical(value)[:-1] != raw
                        or not isinstance(value, dict) or value.get("schema_version") != schema
                        or value.get("run_id") != saved_run or saved_run != run_id
                        or value.get("recipe_version") != f"research-graph-projection.v{version}"
                        or role != "graph_recipe_selection"
                        or output_id != "graph_recipe_selection_" + expected
                        or not isinstance(value.get("manifest"), dict)
                        or value["manifest"].get("schema_version") != f"research-graph-recipe-manifest.v{version}"
                        or value.get("dependency_hash") != digest(canonical(value["manifest"])[:-1])):
                    raise ValueError("retention_selection_original_invalid")
                if version == 2 and (
                        set(value) != {"schema_version", "run_id", "recipe_version", "original_task_ref",
                                       "original_pack_ref", "manifest", "dependency_hash", "recorded_at"}
                        or set(value["manifest"]) != {"schema_version", "source_files", "schema_files", "runtime_versions"}):
                    raise ValueError("retention_selection_original_invalid")
                native_rows.append([output_id, saved_run, role, schema, encoded, expected, created])
                if version == 2:
                    legacy_count += 1
                    continue
                graph_hash = value["dependency_hash"]
                candidates = [row for row in registrations.values() if row["graph_hash"] == graph_hash]
                if not candidates:
                    raise ValueError("retention_selected_v3_not_registered")
                matched = []
                for registration in candidates:
                    name = PREFIX + registration["directory"] + "/release.json"
                    index_raw = (captured["payloads"][name]
                                 if captured is not None and name in captured["payloads"]
                                 else read(scoped(root, name)))
                    if digest(index_raw) != registration["index_hash"]:
                        raise ValueError("retention_selected_v3_index_changed")
                    index = json.loads(index_raw)
                    binding = index.get("installation_binding", {})
                    if (index.get("graph_recipe_dependency_hash") != graph_hash
                            or registration["directory"] != graph_hash and registration["directory"] !=
                            retained_v3_installation_directory(graph_hash, binding.get("receipt"), binding.get("pointer"))):
                        raise ValueError("retention_v3_capture_directory_invalid")
                    if (index.get("graph_recipe_manifest") == value["manifest"]
                            and binding.get("receipt") == value.get("installation_receipt")
                            and binding.get("pointer") == value.get("installed_pointer")
                            and type(value.get("installation_receipt")) is dict
                            and type(value.get("installed_pointer")) is dict):
                        matched.append(registration)
                if not matched:
                    raise ValueError("retention_selected_v3_installation_mismatch")
                if len(matched) != 1:
                    raise ValueError("retention_selected_v3_installation_ambiguous")
                registration = matched[0]
                rows.append({"output_id": output_id, "run_id": saved_run, "schema_version": schema,
                             "payload_hash": expected, "created_at": created,
                             "dependency_hash": graph_hash,
                             "installation_receipt": value["installation_receipt"],
                             "installed_pointer": value["installed_pointer"],
                             "archive_directory": registration["directory"],
                             "archive_index_hash": registration["index_hash"]})
        if not connection.in_transaction or connection.total_changes != 0:
            raise ValueError("retention_selection_snapshot_incomplete")
    finally:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        changes = connection.total_changes
        connection.close()
    return {"schema_version": "selected-v3-retention-coverage.v1", "mode": "ro/query_only",
            "indexed_run_enumeration": True, "indexed_run_role_lookup": True,
            "physical_BEGIN": True, "total_changes": changes, "run_count": run_count,
            "native_selection_count": len(native_rows), "validated_v2_count": legacy_count,
            "native_selections_sha256": digest(canonical(native_rows)),
            "selection_count": len(rows), "selection_payload_bytes": total,
            "rows": rows, "rows_sha256": digest(canonical(rows)),
            "covers_graph_and_exact_installation": True, "publication_qualified": False}


def load_v3_capture(root, args, prior, prior_registry, inventory):
    supplied = (args.captured_v3_preparation, args.captured_v3_preparation_sha256,
                args.captured_v3_source_root)
    if not any(supplied):
        return None
    if not all(supplied):
        raise ValueError("retention_v3_capture_arguments_incomplete")
    capture_path = args.captured_v3_preparation.absolute()
    capture_root = args.captured_v3_source_root.absolute()
    for path in (capture_path, capture_root):
        scoped(root, path.relative_to(root).as_posix())
        if not path.is_relative_to(root / "artifacts"):
            raise ValueError("retention_v3_capture_scope_invalid")
    raw = read(capture_path)
    if digest(raw) != args.captured_v3_preparation_sha256:
        raise ValueError("retention_v3_capture_preparation_changed")
    plan = json.loads(raw)
    if type(plan) is dict and plan.get("schema_version") == "product-retained-v3-capture-set.v1":
        if (set(plan) != {"schema_version", "captures"} or type(plan["captures"]) is not list
                or not 1 <= len(plan["captures"]) <= 16
                or getattr(args, "selection_database", None) is None):
            raise ValueError("retention_v3_capture_set_invalid")
        members, seen, payloads, aggregate_registry = [], set(), {}, prior_registry
        total = 0
        for item in plan["captures"]:
            if (type(item) is not dict or set(item) != {"preparation", "source_root"}
                    or type(item["preparation"]) is not dict
                    or set(item["preparation"]) != {"path", "sha256"}
                    or type(item["preparation"]["path"]) is not str or type(item["source_root"]) is not str
                    or type(item["preparation"]["sha256"]) is not str
                    or SHA.fullmatch(item["preparation"]["sha256"]) is None):
                raise ValueError("retention_v3_capture_set_invalid")
            member_path = scoped(root, item["preparation"]["path"])
            member_root = scoped(root, item["source_root"])
            if (not member_path.is_relative_to(capture_root) or not member_root.is_relative_to(capture_root)
                    or member_path == capture_path or str(member_path) in seen):
                raise ValueError("retention_v3_capture_set_scope_invalid")
            seen.add(str(member_path))
            member_raw = read(member_path)
            member_plan = json.loads(member_raw)
            if type(member_plan) is not dict or "schema_version" in member_plan:
                raise ValueError("retention_v3_capture_set_nested_forbidden")
            child_args = SimpleNamespace(**vars(args))
            child_args.captured_v3_preparation = member_path
            child_args.captured_v3_preparation_sha256 = item["preparation"]["sha256"]
            child_args.captured_v3_source_root = member_root
            member = load_v3_capture(root, child_args, prior, prior_registry, inventory)
            if any(value["capture_directory"] == member["capture_directory"]
                   or value["graph_hash"] == member["graph_hash"]
                   and value["installation_binding"] == member["installation_binding"] for value in members):
                raise ValueError("retention_v3_capture_set_duplicate_installation")
            aggregate_registry = union_registries(aggregate_registry, member["registry"])
            for name, content in member["payloads"].items():
                if name in payloads and payloads[name] != content:
                    raise ValueError("retention_v3_capture_set_resource_conflict")
                if name not in payloads:
                    total += len(content)
                    if total > 128 * 1024 * 1024:
                        raise ValueError("retention_v3_capture_set_observation_limit")
                    payloads[name] = content
            members.append(member)
        aggregate_raw = canonical(aggregate_registry)
        aggregate_sha = digest(aggregate_raw)
        aggregate_ref = {"path": PREFIX + "registries/" + aggregate_sha + ".json", "sha256": aggregate_sha}
        payloads[aggregate_ref["path"]] = aggregate_raw
        captured = {"path": capture_path, "root": capture_root, "raw": raw, "sha256": args.captured_v3_preparation_sha256,
                    "payloads": payloads, "registry": aggregate_registry, "registry_ref": aggregate_ref,
                    "members": members, "producer_raw": members[0]["producer_raw"],
                    "producer_sources": members[0]["producer_sources"],
                    "capture_prior_receipt_sha256": None,
                    "older_authenticated_capture": any(member["older_authenticated_capture"] for member in members)}
        verify_v3_capture(root, captured)
        return captured
    if (type(plan) is not dict or set(plan) != {
            "changed", "archived_graph_hash", "registry_ref", "install_sources", "shared_source_guards",
            "prior_receipt_sha256", "retained_authority_configured"}
            or plan["changed"] is not True or plan["retained_authority_configured"] is not False
            or type(plan["prior_receipt_sha256"]) is not str
            or SHA.fullmatch(plan["prior_receipt_sha256"]) is None
            or plan["shared_source_guards"] != {} or type(plan["install_sources"]) is not dict
            or not plan["install_sources"] or type(plan["archived_graph_hash"]) is not str
            or SHA.fullmatch(plan["archived_graph_hash"]) is None):
        raise ValueError("retention_v3_capture_plan_invalid")
    older = plan["prior_receipt_sha256"] != args.prior_receipt_sha256
    if older and getattr(args, "selection_database", None) is None:
        raise ValueError("retention_v3_older_capture_requires_selection_coverage")
    ref = plan["registry_ref"]
    if (type(ref) is not dict or set(ref) != {"path", "sha256"}
            or type(ref["sha256"]) is not str or SHA.fullmatch(ref["sha256"]) is None
            or ref["path"] != PREFIX + "registries/" + ref["sha256"] + ".json"):
        raise ValueError("retention_v3_capture_registry_invalid")
    payloads = {}
    for name, expected in plan["install_sources"].items():
        if type(expected) is not str or SHA.fullmatch(expected) is None:
            raise ValueError("retention_v3_capture_source_invalid")
        original = read(scoped(capture_root, name))
        if digest(original) != expected:
            raise ValueError("retention_v3_capture_source_changed")
        payloads[name] = original
    if plan["install_sources"].get(ref["path"]) != ref["sha256"]:
        raise ValueError("retention_v3_capture_registry_invalid")
    registry = json.loads(payloads[ref["path"]])
    if canonical(registry) != payloads[ref["path"]]:
        raise ValueError("retention_v3_capture_registry_invalid")
    rows = registry_rows(registry)
    graph_hash = plan["archived_graph_hash"]
    candidates = [row for row in rows.values() if row["graph_hash"] == graph_hash
                  and PREFIX + row["directory"] + "/release.json" in payloads]
    if len(candidates) != 1:
        raise ValueError("retention_v3_capture_identity_ambiguous")
    registration = candidates[0]
    capture_directory = registration["directory"]
    compatible_v3_capture_registry(prior_registry, registry, capture_directory, older=older)
    prefix = PREFIX + capture_directory + "/"
    index_name = prefix + "release.json"
    if plan["install_sources"].get(index_name) != registration["index_hash"]:
        raise ValueError("retention_v3_capture_index_invalid")
    index = json.loads(payloads[index_name])
    # Load only the frozen, stdlib-only producer. Never import product packages or
    # execute the original-view V2 producer as if it understood the new V3 codec.
    producer_path = scoped(root, ARCHIVE_PRODUCER)
    producer_raw = read(producer_path)
    if inventory.get(ARCHIVE_PRODUCER) != digest(producer_raw):
        raise ValueError("retention_v3_capture_validator_changed")
    module_name = "_product_v3_capture_validator_" + uuid.uuid4().hex
    package = ModuleType(module_name)
    package.__path__ = []  # type: ignore[attr-defined]
    sys.modules[module_name] = package
    loaded = [module_name]
    producer_pins = {ARCHIVE_PRODUCER: digest(producer_raw)}
    try:
        for relative in (PROVENANCE_CODEC, ARCHIVE_PRODUCER):
            original = read(scoped(root, relative))
            if inventory.get(relative) != digest(original):
                raise ValueError("retention_v3_capture_validator_changed")
            producer_pins[relative] = digest(original)
            child_name = relative.rsplit("/", 1)[1].removesuffix(".py")
            qualified = module_name + "." + child_name
            child = ModuleType(qualified)
            child.__package__ = module_name
            child.__file__ = str(root / relative)
            sys.modules[qualified] = child
            loaded.append(qualified)
            setattr(package, child_name, child)
            exec(compile(original, str(root / relative), "exec"), child.__dict__)
        module = sys.modules[module_name + ".research_recipe_archive"]
        declared = module._v3_stored_files(index)
        if (index["graph_recipe_dependency_hash"] != graph_hash
                or index["selector_identity"]["implementation_hash"] != registration["selector_hash"]
                or set(payloads) != {ref["path"], index_name} | {prefix + "root/" + name for name in declared}):
            raise ValueError("retention_v3_capture_closure_invalid")
        originals = {name: payloads[prefix + "root/" + name] for name in declared}
        captured_receipt, _ = module._validate_retained_v3_installation(index, originals, expected_root=root)
        binding = index["installation_binding"]["receipt"]
        if capture_directory != graph_hash and capture_directory != retained_v3_installation_directory(
                graph_hash, binding, index["installation_binding"]["pointer"]):
            raise ValueError("retention_v3_capture_directory_invalid")
        if (binding["sha256"] != plan["prior_receipt_sha256"]
                or originals[binding["path"]] != read(scoped(root, binding["path"]))
                or not older and (
                    binding["path"] != args.prior_receipt_path.absolute().relative_to(root).as_posix()
                    or captured_receipt != prior)):
            raise ValueError("retention_v3_capture_prior_receipt_changed")
    finally:
        for name in reversed(loaded):
            sys.modules.pop(name, None)
    for relative, expected in producer_pins.items():
        if digest(read(scoped(root, relative))) != expected:
            raise ValueError("retention_v3_capture_validator_changed")
    captured = {"path": capture_path, "root": capture_root, "raw": raw, "payloads": payloads,
                "registry": registry, "registry_ref": ref, "producer_raw": producer_raw,
                "producer_sources": producer_pins,
                "sha256": args.captured_v3_preparation_sha256,
                "capture_prior_receipt_sha256": plan["prior_receipt_sha256"],
                "capture_directory": capture_directory,
                "graph_hash": graph_hash,
                "installation_binding": index["installation_binding"],
                "older_authenticated_capture": older}
    verify_v3_capture(root, captured)
    return captured


def verify_v3_capture(root, captured):
    if captured is None:
        return
    if captured.get("members") is not None:
        if read(captured["path"]) != captured["raw"]:
            raise ValueError("retention_v3_capture_changed_during_prepare")
        for member in captured["members"]:
            verify_v3_capture(root, member)
        return
    if (read(captured["path"]) != captured["raw"]
            or read(scoped(root, ARCHIVE_PRODUCER)) != captured["producer_raw"]
            or any(digest(read(scoped(root, name))) != expected
                   for name, expected in captured["producer_sources"].items())
            or any(read(scoped(captured["root"], name)) != raw
                   for name, raw in captured["payloads"].items())):
        raise ValueError("retention_v3_capture_changed_during_prepare")


def merge_v3_capture(root, preparation_root, registry, installed, captured, *, install_candidates):
    # All registry conflicts are resolved before any capture candidate is published.
    registry = union_registries(registry, captured["registry"])
    for name, raw in captured["payloads"].items():
        expected = digest(raw)
        if name in installed and installed[name] != expected:
            raise ValueError("retention_v3_capture_install_conflict")
    union_raw = canonical(registry)
    union_hash = digest(union_raw)
    reference = {"path": PREFIX + "registries/" + union_hash + ".json", "sha256": union_hash}
    payloads = dict(captured["payloads"])
    payloads[reference["path"]] = union_raw
    for name, raw in payloads.items():
        publish(scoped(preparation_root, name), raw)
        if install_candidates:
            publish(scoped(root, name), raw)
        installed[name] = digest(raw)
    extras = dict(installed)
    extras[reference["path"]] = union_hash
    return registry, reference, extras


def selection_usage_proof(
    root: Path, database: Path, candidate_graph_hash: str,
) -> dict[str, Any]:
    """Observe all persisted selections in one physical, write-denied snapshot.

    This supplies only an absence proof for a newly prepared V2 capsule. It
    never authenticates execution, publication, or a model's reported census.
    """
    database = database.absolute()
    scoped(root, database.relative_to(root).as_posix())
    if not database.is_file() or SHA.fullmatch(candidate_graph_hash) is None:
        raise ValueError("retention_selection_database_invalid")
    connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, isolation_level=None)
    connection.execute("PRAGMA query_only=ON")
    denied = {
        sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE,
        sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_CREATE_TEMP_TABLE,
        sqlite3.SQLITE_DROP_TABLE, sqlite3.SQLITE_ALTER_TABLE,
        sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH,
    }
    connection.set_authorizer(
        lambda action, *_args: (  # pyright: ignore[reportUnknownLambdaType]
            sqlite3.SQLITE_DENY if action in denied else sqlite3.SQLITE_OK
        )
    )
    rows, total = [], 0
    try:
        connection.execute("BEGIN")
        if (not connection.in_transaction
                or connection.execute("PRAGMA query_only").fetchone()[0] != 1):
            raise ValueError("retention_selection_snapshot_required")
        selected = connection.execute(
            "SELECT output_id,run_id,role,schema_version,payload_json,payload_hash,created_at "
            "FROM run_role_outputs WHERE role='graph_recipe_selection' ORDER BY output_id"
        )
        for output_id, run_id, role, schema, encoded, expected, created_at in selected:
            if len(rows) >= 4096 or not isinstance(encoded, str):
                raise ValueError("retention_selection_observation_limit")
            raw = encoded.encode("utf-8")
            total += len(raw)
            if len(raw) > 1024 * 1024 or total > 16 * 1024 * 1024:
                raise ValueError("retention_selection_observation_limit")
            if (not isinstance(expected, str) or SHA.fullmatch(expected) is None
                    or digest(raw) != expected):
                raise ValueError("retention_selection_original_changed")
            value = json.loads(encoded)
            version = {
                "research-graph-recipe-selection.v2": "research-graph-projection.v2",
                "research-graph-recipe-selection.v3": "research-graph-projection.v3",
            }.get(schema)
            if (version is None or not isinstance(value, dict)
                    or value.get("schema_version") != schema or value.get("run_id") != run_id
                    or value.get("recipe_version") != version or role != "graph_recipe_selection"
                    or canonical(value)[:-1] != raw
                    or output_id != "graph_recipe_selection_" + expected
                    or not isinstance(value.get("manifest"), dict)
                    or value.get("dependency_hash") != digest(canonical(value["manifest"])[:-1])):
                raise ValueError("retention_selection_original_invalid")
            rows.append({"output_id": output_id, "run_id": run_id, "schema_version": schema,
                         "payload_hash": expected, "created_at": created_at,
                         "dependency_hash": value["dependency_hash"]})
        if not rows or not connection.in_transaction or connection.total_changes != 0:
            raise ValueError("retention_selection_snapshot_incomplete")
    finally:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        changes, closed = connection.total_changes, not connection.in_transaction
        connection.close()
    return {"database": str(database), "mode": "ro", "query_only": True, "physical_BEGIN": True,
            "rollback": closed, "total_changes": changes, "selection_count": len(rows),
            "selection_payload_bytes": total, "rows": rows, "rows_sha256": digest(canonical(rows)),
            "candidate_graph_hash": candidate_graph_hash,
            "candidate_v2_selected": any(
                row["schema_version"] == "research-graph-recipe-selection.v2"
                and row["dependency_hash"] == candidate_graph_hash for row in rows)}


def _maintenance_require(value, code):
    if not value:
        raise ValueError("retention_maintenance_" + code)


def _maintenance_reference(root, value, *, expected=None):
    _maintenance_require(
        type(value) is dict and set(value) == {"path", "sha256"}
        and type(value["path"]) is str and type(value["sha256"]) is str
        and SHA.fullmatch(value["sha256"]) is not None,
        "reference_invalid",
    )
    declared = Path(value["path"])
    path = declared.absolute() if declared.is_absolute() else root / declared
    _maintenance_require(path.is_relative_to(root), "reference_scope_invalid")
    relative = path.relative_to(root).as_posix()
    _maintenance_require(
        path.is_relative_to(root / "artifacts")
        or relative == PREFIX + "registries/" + value["sha256"] + ".json",
        "reference_scope_invalid",
    )
    _maintenance_require(scoped(root, relative) == path, "reference_path_invalid")
    if expected is not None:
        _maintenance_require(path == expected, "reference_path_mismatch")
    raw = read(path)
    _maintenance_require(digest(raw) == value["sha256"], "reference_hash_mismatch")
    return path, raw


def authenticate_unstarted_maintenance_prior(root, args, pointer_raw):
    """Read-only prior evidence; never replace the runtime locator or issue authority."""
    completed = getattr(args, "completed_maintenance_prior", False) is True
    if completed:
        import maintenance_prior_chain as chain
        _maintenance_require(
            Path(str(chain.__file__)).resolve()
            == Path(__file__).with_name("maintenance_prior_chain.py").resolve(),
            "chain_helper_origin_invalid",
        )
        for name, expected in chain.CURRENT.items():
            _maintenance_require(
                digest(read(scoped(root, name))) == expected, "producer_changed"
            )
        sys.path.insert(0, str(root))
        codec = importlib.import_module(
            PROVENANCE_CODEC.removesuffix(".py").replace("/", ".")
        )
        producer = importlib.import_module(
            ARCHIVE_PRODUCER.removesuffix(".py").replace("/", ".")
        )
        for module, name in ((codec, PROVENANCE_CODEC), (producer, ARCHIVE_PRODUCER)):
            _maintenance_require(
                type(module.__file__) is str
                and digest(read(Path(module.__file__))) == chain.CURRENT[name],
                "imported_producer_changed",
            )
        api = SimpleNamespace(
            require=_maintenance_require, reference=_maintenance_reference,
            read=read, scoped=scoped, digest=digest, canonical=canonical,
            registry_rows=registry_rows, resolve=codec.resolve_product_receipt,
            verify_registry=producer._registry_from_prior,
        )
        return chain.authenticate_completed_chain(root, args, pointer_raw, api)
    pointer = json.loads(pointer_raw)
    _maintenance_require(
        type(pointer) is dict and set(pointer) == {
            "schema_version", "repository_root", "receipt_path", "receipt_sha256"
        }
        and pointer["schema_version"] == "product-installed-release-pointer.v1"
        and type(pointer["repository_root"]) is str
        and Path(pointer["repository_root"]).resolve() == root.resolve()
        and type(pointer["receipt_path"]) is str
        and type(pointer["receipt_sha256"]) is str,
        "runtime_locator_invalid",
    )
    runtime_relative = pointer["receipt_path"]
    runtime_path = scoped(root, runtime_relative)
    _maintenance_require(runtime_relative == runtime_path.relative_to(root).as_posix(), "runtime_path_invalid")
    prior_path = args.prior_receipt_path.absolute()
    _maintenance_require(
        prior_path.is_relative_to(root / "artifacts") and runtime_path.is_relative_to(root / "artifacts"),
        "receipt_scope_invalid",
    )
    _maintenance_require(prior_path != runtime_path and args.prior_receipt_sha256 != pointer["receipt_sha256"], "hop_identity_invalid")
    runtime_path, runtime_raw = _maintenance_reference(root, {
        "path": runtime_relative, "sha256": pointer["receipt_sha256"]
    }, expected=runtime_path)
    prior_path, prior_raw = _maintenance_reference(root, {
        "path": str(prior_path), "sha256": args.prior_receipt_sha256
    }, expected=prior_path)

    producer_names = (PROVENANCE_CODEC, ARCHIVE_PRODUCER)
    pins = {
        PROVENANCE_CODEC: "3ba7b5463ee1f61bff06e75fe4fffd4af900a48eac7321112c4b377797247f05",
        ARCHIVE_PRODUCER: "42d3fcfa02c44f874d6d275c5a11cbca1f7ef174b1a0701e061eb0d09a267d7a",
    }
    runtime_pins = pins
    if completed:
        # A completed maintenance build is a prior source view, not the installed release.
        pins = {
            PROVENANCE_CODEC: "180c726a987d3b4deacb940b04113c3f1940bae6870cd47df3690a34b0e68691",
            ARCHIVE_PRODUCER: "b47d8050652b24ac7406146960658bae82e74cc508db6bcb4d657f640f3990e7",
        }
        runtime_pins = {
            PROVENANCE_CODEC: "b0b8c17c1c8eda55a81920d8b7e4fb4baef2c12fe9770078601d0fde1fc8652d",
            ARCHIVE_PRODUCER: pins[ARCHIVE_PRODUCER],
        }
    producer_raws = {name: read(scoped(root, name)) for name in producer_names}
    _maintenance_require(all(digest(producer_raws[name]) == pins[name] for name in producer_names), "producer_changed")
    sys.path.insert(0, str(root))
    from packages.kernel.decision_hub_kernel.application.product_release_provenance import resolve_product_receipt
    resolved_prior = resolve_product_receipt(
        prior_raw, expected_root=root, expected_sha256=args.prior_receipt_sha256,
        read_resource=lambda name: read(scoped(root, name)),
    )
    resolved_runtime = resolve_product_receipt(
        runtime_raw, expected_root=root, expected_sha256=pointer["receipt_sha256"],
        read_resource=lambda name: read(scoped(root, name)),
    )
    prior, runtime = resolved_prior.value, resolved_runtime.value
    _maintenance_require(all(
        prior["sources"].get(name) == pins[name]
        and runtime["sources"].get(name) == runtime_pins[name]
        for name in producer_names
    ), "producer_receipt_binding_invalid")
    prior_evidence = prior["evidence"]
    _maintenance_require(
        prior_evidence.get("origin") == "standard_product_build_only"
        and type(prior_evidence.get("ui_build_count")) is int and prior_evidence["ui_build_count"] == 1
        and type(prior_evidence.get("host_build_count")) is int and prior_evidence["host_build_count"] == 1
        and prior_evidence.get("source_stable_except_standard_generated_identity") is True
        and type(prior_evidence.get("launch_id")) is str
        and re.fullmatch(r"[0-9]{8}T[0-9]{9}Z", prior_evidence["launch_id"]) is not None
        and prior_evidence.get("native_execution_qualified") is False
        and prior_evidence.get("eligible_for_deployment") is False,
        "prior_origin_invalid",
    )
    execution_path = prior_path.parent / "build-execution.json"
    execution_raw = read(scoped(root, execution_path.relative_to(root).as_posix()))
    execution = json.loads(execution_raw)
    _maintenance_require(
        type(execution) is dict and execution.get("schema_version") == "product-build-execution.v1"
        and execution.get("passed") is True
        and all(type(execution.get(name)) is int and execution[name] == expected for name, expected in (
            ("ui_build_attempts", 1), ("host_build_attempts", 1),
            ("ui_build_exit_code", 0), ("host_build_exit_code", 0), ("builds", 2),
            ("migrations", 0), ("process_starts", 0), ("process_stops", 0), ("model_submissions", 0)
        ))
        and execution.get("error") is None
        and type(execution.get("pointer_writes")) is int
        and execution["pointer_writes"] == (0 if completed else 1)
        and execution.get("native_execution_qualified") is False
        and execution.get("whole_product_accepted") is False
        and execution.get("launch_id") == prior_evidence["launch_id"],
        "build_execution_invalid",
    )
    if completed:
        try:
            clocks: list[datetime] = []
            for value in (
                execution["started_at"], prior_evidence["observed_at"], execution["completed_at"]
            ):
                if not isinstance(value, str):
                    raise ValueError("invalid completed build clock")
                clocks.append(datetime.fromisoformat(value))
            valid_clocks = all(
                value.tzinfo is not None and value.utcoffset() is not None for value in clocks
            )
            valid_clocks = valid_clocks and clocks[0] <= clocks[1] <= clocks[2]
        except (KeyError, TypeError, ValueError):
            valid_clocks = False
        _maintenance_require(valid_clocks, "completed_build_clock_invalid")
    validated = execution.get("validated_receipt")
    _maintenance_require(
        type(validated) is dict and set(validated) == {"path", "sha256", "source_count", "artifact_count", "plugin_build_hash"}
        and type(validated["path"]) is str and Path(validated["path"]).absolute() == prior_path
        and validated["sha256"] == args.prior_receipt_sha256
        and type(validated["source_count"]) is int and validated["source_count"] == len(prior["sources"])
        and type(validated["artifact_count"]) is int and validated["artifact_count"] == len(prior["artifacts"])
        and validated["plugin_build_hash"] == prior["plugin_build_hash"],
        "validated_receipt_invalid",
    )
    retention_path = prior_path.parent / "retention/retained-recipe-preparation.json"
    retention_ref = prior_evidence.get("retention_preparation")
    _maintenance_require(execution.get("retention_preparation") == retention_ref, "retention_ref_mismatch")
    retention_path, retention_raw = _maintenance_reference(root, retention_ref, expected=retention_path)
    retention = json.loads(retention_raw)
    _maintenance_require(
        type(retention) is dict and retention.get("schema_version") == "product-retention-preparation.v1"
        and retention.get("passed") is True and retention.get("preparation_only") is False
        and retention.get("prior_receipt") == {"path": str(runtime_path), "sha256": pointer["receipt_sha256"]}
        and all(type(retention.get(name)) is int and retention[name] == 0 for name in (
            "current_source_overwrites", "pointer_writes", "builds", "migrations", "model_submissions"
        ))
        and retention.get("unavailable_prior_sources_not_substituted") == []
        and retention.get("authenticated_current_producer_sources") == pins,
        "retention_binding_invalid",
    )
    runtime_registry_ref = runtime["evidence"].get("retained_recipe_archives_v1", {}).get("registry")
    prior_registry_ref = prior_evidence.get("retained_recipe_archives_v1", {}).get("registry")
    validation = retention.get("prior_registry_validation")
    _maintenance_require(
        type(validation) is dict and validation.get("mode") == "original_producer_actual_installation_root.v1"
        and all(validation.get(name) is True for name in (
            "every_original_reverified", "source_and_artifact_maps_unchanged", "all_original_registrations_preserved"
        ))
        and validation.get("registry") == runtime_registry_ref
        and retention.get("retained_recipe_archives_v1", {}).get("registry") == prior_registry_ref,
        "registry_evidence_invalid",
    )
    registry_raws = []
    registry_values = []
    for value, receipt in ((runtime_registry_ref, runtime), (prior_registry_ref, prior)):
        path, raw = _maintenance_reference(root, value)
        _maintenance_require(
            path.relative_to(root).as_posix() == PREFIX + "registries/" + value["sha256"] + ".json"
            and receipt["sources"].get(path.relative_to(root).as_posix()) == value["sha256"],
            "registry_source_invalid",
        )
        parsed = json.loads(raw)
        _maintenance_require(canonical(parsed) == raw, "registry_canonical_invalid")
        registry_values.append(registry_rows(parsed))
        registry_raws.append((path.relative_to(root).as_posix(), raw))
    runtime_rows, prior_rows = registry_values
    _maintenance_require(all(prior_rows.get(key) == row for key, row in runtime_rows.items()), "registry_rows_lost")
    transports = {}
    for resolved in (resolved_runtime, resolved_prior):
        for relative, expected in resolved.transport.items():
            raw = read(scoped(root, relative))
            _maintenance_require(digest(raw) == expected, "transport_hash_mismatch")
            if relative in transports:
                _maintenance_require(transports[relative] == (expected, raw), "transport_conflict")
            transports[relative] = expected, raw
    witness = (
        ("data/product-local/installed-release-receipt.json", pointer_raw),
        (prior_path.relative_to(root).as_posix(), prior_raw),
        (runtime_path.relative_to(root).as_posix(), runtime_raw),
        (execution_path.relative_to(root).as_posix(), execution_raw),
        (retention_path.relative_to(root).as_posix(), retention_raw),
        *registry_raws, *producer_raws.items(),
        *((relative, raw) for relative, (_expected, raw) in transports.items()),
    )
    verify_unstarted_maintenance_prior(root, witness)
    return witness


def verify_unstarted_maintenance_prior(root, witness):
    if witness is None:
        return
    for relative, expected in witness:
        _maintenance_require(read(scoped(root, relative)) == expected, "evidence_changed_during_prepare")
    verify_resources = getattr(witness, "verify_registry_resources", None)
    if verify_resources is not None:
        verify_resources()


def relocate_preparation_receipt(
    prior_raw: bytes, prior: dict[str, Any], mirror: Path,
    args: argparse.Namespace, pointer: dict[str, Any],
) -> dict[str, Any]:
    """Keep raw source/artifact maps and distinguish a built prior from installed runtime."""
    relocated = json.loads(prior_raw)
    relocated["repository_root"] = str(mirror)
    relocated["evidence"] = dict(prior["evidence"])
    relocated["evidence"].pop("retained_recipe_archives_v1", None)
    completed = getattr(args, "completed_maintenance_prior", False) is True
    key = "original_completed_build_receipt" if completed else "original_installed_receipt"
    relocated["evidence"][key] = {
        "path": str(args.prior_receipt_path),
        "sha256": args.prior_receipt_sha256,
        "maps_exactly_preserved": True,
        "preparation_only": True,
    }
    if completed:
        relocated["evidence"]["original_runtime_installed_receipt"] = {
            "path": str(args.repository_root / pointer["receipt_path"]),
            "sha256": pointer["receipt_sha256"],
        }
    return relocated


def prepare(args: argparse.Namespace) -> None:
    root = args.repository_root.absolute()
    stage = args.output_directory.absolute()
    if not stage.is_relative_to(root / "artifacts") or stage.exists():
        raise ValueError("retention_fresh_artifact_directory_required")
    pointer_raw = read(root / "data/product-local/installed-release-receipt.json")
    pointer = json.loads(pointer_raw)
    maintenance_prior = None
    if (
        pointer["schema_version"] != "product-installed-release-pointer.v1"
        or Path(pointer["repository_root"]).resolve() != root.resolve()
    ):
        raise ValueError("retention_installed_pointer_identity_changed")
    if (
        getattr(args, "completed_maintenance_prior", False) is True
        or pointer["receipt_path"]
        != args.prior_receipt_path.absolute().relative_to(root).as_posix()
        or pointer["receipt_sha256"] != args.prior_receipt_sha256
    ):
        maintenance_prior = authenticate_unstarted_maintenance_prior(root, args, pointer_raw)
    prior_raw = read(args.prior_receipt_path)
    if digest(prior_raw) != args.prior_receipt_sha256:
        raise ValueError("retention_prior_receipt_changed")
    sys.path.insert(0, str(root))
    from packages.kernel.decision_hub_kernel.application.product_release_provenance import (
        resolve_product_receipt,
    )
    resolved_prior = resolve_product_receipt(
        prior_raw, expected_root=root, expected_sha256=args.prior_receipt_sha256,
        read_resource=lambda name: read(scoped(root, name)),
    )
    prior = dict(resolved_prior.value)
    reference = prior["evidence"]["retained_recipe_archives_v1"]["registry"]
    registry_raw = read(scoped(root, reference["path"]))
    if (
        digest(registry_raw) != reference["sha256"]
        or prior["sources"].get(reference["path"]) != reference["sha256"]
    ):
        raise ValueError("retention_prior_registry_changed")
    registry = json.loads(registry_raw)
    inventory = json.loads(read(args.source_inventory))
    captured = load_v3_capture(root, args, prior, registry, inventory)
    selected_v3_coverage = None
    if getattr(args, "selection_database", None) is not None:
        # Before publishing/installing any candidate. Prior/current manifest
        # hashes do not substitute for the saved V3 installation identity.
        selected_v3_coverage = selected_v3_retention_proof(
            root, args.selection_database, registry, captured,
        )
    originals = [path.absolute() for path in args.original_source_root]
    for original in originals:
        if not original.is_relative_to(root / "artifacts") or original.is_symlink():
            raise ValueError("retention_original_source_scope_invalid")
    # Known archive bytes are eligible only when they match the *installed*
    # original hash. Their age or file name by itself grants no authority.
    archived = {}
    for registration in registry["registrations"]:
        prefix = PREFIX + registration["directory"] + "/"
        raw = read(scoped(root, prefix + "release.json"))
        if digest(raw) != registration["index_hash"]:
            raise ValueError("retention_prior_index_changed")
        index = json.loads(raw)
        for name in index["stored_files"]:
            archived[(name, index["all_files"][name])] = prefix + "root/" + name
    stage.mkdir(parents=True)
    mirror = stage / "original-view"
    for name, expected in resolved_prior.transport.items():
        raw = read(scoped(root, name))
        if digest(raw) != expected:
            raise ValueError("retention_prior_transport_changed")
        publish(scoped(mirror, name), raw)
    unavailable = []
    original_sources = {}
    for name, expected in prior["sources"].items():
        candidates = [scoped(root, name)] + [scoped(original, name) for original in originals]
        archive = archived.get((name, expected))
        if archive:
            candidates.append(scoped(root, archive))
        original_source = next(
            (
                path
                for path in candidates
                if io_path(path).is_file() and digest(read(path)) == expected
            ),
            None,
        )
        if original_source is None:
            unavailable.append({"path": name, "expected_sha256": expected})
        else:
            publish_mirror_original(scoped(mirror, name), original_source, expected, name)
            original_sources[name] = expected
    changes = []
    for name in sorted(set(prior["sources"]) | set(inventory)):
        if (name.startswith(PREFIX) or not is_recipe_manifest_input(name)
                or prior["sources"].get(name) == inventory.get(name)):
            continue
        baseline, candidate_hash = prior["sources"].get(name), inventory.get(name)
        # Only original recipe/selector imports are needed for this producer.
        # Unavailable unrelated UI bytes must not be substituted or fabricated.
        if baseline is not None and not io_path(scoped(mirror, name)).is_file():
            continue
        candidate = None
        if candidate_hash is not None:
            raw = read(scoped(root, name))
            if digest(raw) != candidate_hash:
                raise ValueError("retention_candidate_changed")
            candidate = scoped(mirror, "artifacts/candidate-overlay/" + name)
            publish(candidate, raw)
        changes.append((name, baseline, candidate_hash, candidate))
    # Existing V3 capsules bind the actual installation root. Authenticate them
    # with the exact original producer at that root, never at the mirror path.
    # Preserve the actual outer schema and transport reference. The six-field
    # effective map is only an internal view and is never written as raw v2.
    # This receipt is preparation evidence only. The old producer prepares one
    # original closure in isolation; the bridge restores the fully authenticated
    # original registry through an explicit, conflict-rejecting union below.
    relocated = relocate_preparation_receipt(prior_raw, prior, mirror, args, pointer)
    relocated_raw = (json.dumps(relocated, sort_keys=True, separators=(",", ":")) + "\n").encode()
    relocated_path = mirror / "artifacts/original-view-receipt.json"
    publish(relocated_path, relocated_raw)
    relocated_view = dict(resolve_product_receipt(
        relocated_raw, expected_root=mirror,
        read_resource=lambda name: read(scoped(mirror, name)),
    ).value)
    if (relocated_view["sources"] != prior["sources"]
            or relocated_view["artifacts"] != prior["artifacts"]):
        raise ValueError("retention_relocated_maps_changed")
    with original_archive_producer(
        root, mirror, inventory, v2=prior["schema"] == "product-validated-builds.v2"
    ) as (producer, producer_pins):
        _registry_from_prior = producer._registry_from_prior
        if _registry_from_prior(root, prior) != registry["registrations"]:
            raise ValueError("retention_prior_registry_validation_changed")
        prepared = producer.prepare_retained_recipe_archive(
            repository_root=mirror,
            prior_receipt_path=relocated_path,
            prior_receipt_sha256=digest(relocated_raw),
            planned_changes=[producer.PlannedRecipeSourceChange(*row) for row in changes],
            staging_root=mirror / "artifacts/prepared-retention",
        )
    preparation_root = mirror / "artifacts/prepared-retention"
    selection_proof = None
    omit_unselected_v2 = False
    if (captured is not None and prepared.changed
            and getattr(args, "selection_database", None) is not None):
        selection_proof = selection_usage_proof(
            root, args.selection_database,
            prepared.archived_graph_hash,  # pyright: ignore[reportArgumentType]
        )
        omit_unselected_v2 = not selection_proof["candidate_v2_selected"]
    new_reference = dict(reference if omit_unselected_v2 else prepared.registry_ref or reference)
    registry_path = scoped(
        preparation_root if prepared.changed and not omit_unselected_v2 else root,
        new_reference["path"]
    )
    new_registry = json.loads(read(registry_path))
    extras = {new_reference["path"]: new_reference["sha256"]}
    installed = {}
    if prepared.changed and not omit_unselected_v2:
        for name, expected in prepared.install_sources.items():
            raw = read(scoped(preparation_root, name))
            if digest(raw) != expected:
                raise ValueError("retention_prepared_resource_changed")
            if args.install_candidates:
                publish(scoped(root, name), raw)
            installed[name] = expected
        new_registry = union_registries(registry, new_registry)
        union_raw = canonical(new_registry)
        union_hash = digest(union_raw)
        new_reference = {"path": PREFIX + "registries/" + union_hash + ".json", "sha256": union_hash}
        publish(scoped(preparation_root, new_reference["path"]), union_raw)
        if args.install_candidates:
            publish(scoped(root, new_reference["path"]), union_raw)
        installed[new_reference["path"]] = union_hash
        extras = dict(installed)
    if captured is not None:
        new_registry, new_reference, extras = merge_v3_capture(
            root, preparation_root, new_registry, installed, captured,
            install_candidates=args.install_candidates,
        )
    for registration in new_registry["registrations"]:
        prefix = PREFIX + registration["directory"] + "/"
        name = prefix + "release.json"
        location = preparation_root if name in installed else root
        raw = read(scoped(location, name))
        if digest(raw) != registration["index_hash"]:
            raise ValueError("retention_union_index_changed")
        extras[name] = registration["index_hash"]
        index = json.loads(raw)
        for original_name, expected in index["all_files"].items():
            name = (
                prefix + "root/" + original_name
                if original_name in index["stored_files"]
                else original_name
            )
            location = preparation_root if name in installed else root
            if digest(read(scoped(location, name))) != expected:
                raise ValueError("retention_union_resource_changed")
            if name not in installed and prior["sources"].get(name) != expected:
                raise ValueError("retention_union_resource_not_bound_to_prior")
            extras[name] = expected
    if (
        read(args.prior_receipt_path) != prior_raw
        or read(root / "data/product-local/installed-release-receipt.json") != pointer_raw
    ):
        raise ValueError("retention_prior_changed_during_prepare")
    final_prior = resolve_product_receipt(
        prior_raw, expected_root=root, expected_sha256=args.prior_receipt_sha256,
        read_resource=lambda name: read(scoped(root, name)),
    )
    if (final_prior.value != resolved_prior.value
            or final_prior.transport != resolved_prior.transport):
        raise ValueError("retention_prior_transport_changed")
    verify_unstarted_maintenance_prior(root, maintenance_prior)
    verify_v3_capture(root, captured)
    if (selected_v3_coverage is not None and selected_v3_retention_proof(
            root, args.selection_database, registry, captured,
    ) != selected_v3_coverage):
        raise ValueError("retention_selected_v3_changed_during_prepare")
    if _registry_from_prior(root, prior) != registry["registrations"]:
        raise ValueError("retention_prior_registry_validation_changed")
    for name, expected in producer_pins.items():
        if digest(read(scoped(root, name))) != expected:
            raise ValueError("retention_current_producer_changed")
    if (selection_proof is not None and selection_usage_proof(
            root, args.selection_database,
            prepared.archived_graph_hash,  # pyright: ignore[reportArgumentType]
    ) != selection_proof):
        raise ValueError("retention_selection_changed_during_prepare")
    seal: dict[str, Any] = {
        "schema_version": "product-original-source-seal.v1",
        "prior_receipt_sha256": args.prior_receipt_sha256,
        "original_root": str(mirror),
        "sources": original_sources,
        "source_count": len(original_sources),
        "unavailable_not_substituted": unavailable,
        "preparation_only": True,
    }
    seal_raw = (json.dumps(seal, sort_keys=True) + "\n").encode()
    seal_path = stage / "original-view-source-seal.json"
    publish(seal_path, seal_raw)
    report: dict[str, Any] = {
        "schema_version": "product-retention-preparation.v1",
        ("completed_maintenance_prior"
         if getattr(args, "completed_maintenance_prior", False) is True
         else "unstarted_maintenance_prior"): None if maintenance_prior is None else {
            "single_hop": True, "pointer_writes": 0, "grants_start_or_execution": False,
            "evidence": [{"path": relative, "sha256": digest(original)}
                         for relative, original in maintenance_prior],
            **getattr(maintenance_prior, "report_lineage", lambda: dict[str, Any]())(),
        },
        "passed": True,
        "preparation_only": not args.install_candidates,
        "authenticated_current_producer_sources": producer_pins,
        "prior_receipt": {
            "path": str(args.prior_receipt_path),
            "sha256": args.prior_receipt_sha256,
        },
        "relocated_receipt": {
            "path": str(relocated_path),
            "sha256": digest(relocated_raw),
            "maps_exactly_preserved": True,
        },
        "archived_graph_hash": prepared.archived_graph_hash,
        "unused_v2_archive_omission": None if selection_proof is None else {
            "omitted": omit_unselected_v2, "proof": selection_proof,
            "captured_v3_preparation_sha256":
                captured["sha256"],  # pyright: ignore[reportOptionalSubscript]
            "prior_receipt_sha256": args.prior_receipt_sha256,
        },
        "prior_archive_count": len(registry["registrations"]),
        "prior_registry_validation": {
            "mode": "original_producer_actual_installation_root.v1",
            "registry": reference,
            "every_original_reverified": True,
            "relocation_only_preparation_evidence": True,
            "source_and_artifact_maps_unchanged": relocated_view["sources"] == prior["sources"]
            and relocated_view["artifacts"] == prior["artifacts"],
            "all_original_registrations_preserved": all(
                registry_rows(new_registry).get(key) == row
                for key, row in registry_rows(registry).items()
            ),
        },
        "archive_count": len(new_registry["registrations"]),
        "selected_v3_retention_coverage": selected_v3_coverage,
        "original_source_seal": {"path": str(seal_path), "sha256": digest(seal_raw)},
        "retained_recipe_archives_v1": {"registry": new_reference},
        "captured_v3_preparation": None if captured is None else {
            "path": str(captured["path"]), "sha256": captured["sha256"],
            "source_root": str(captured["root"]), "registry": captured["registry_ref"],
            "source_count": len(captured["payloads"]), "every_original_reverified": True,
            "capture_prior_receipt_sha256": captured["capture_prior_receipt_sha256"],
            "older_authenticated_capture": captured["older_authenticated_capture"],
            "explicit_captures": [{"path": str(member["path"]), "sha256": member["sha256"],
                                   "graph_hash": member["graph_hash"], "directory": member["capture_directory"],
                                   "capture_prior_receipt_sha256": member["capture_prior_receipt_sha256"]}
                                  for member in captured.get("members", [captured])],
        },
        "additional_sources": extras,
        "candidate_sources": installed,
        "unavailable_prior_sources_not_substituted": unavailable,
        "current_source_overwrites": 0,
        "pointer_writes": 0,
        "builds": 0,
        "migrations": 0,
        "model_submissions": 0,
    }
    publish(
        stage / "retained-recipe-preparation.json",
        (json.dumps(report, sort_keys=True) + "\n").encode(),
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--prior-receipt-path", type=Path, required=True)
    parser.add_argument("--prior-receipt-sha256", required=True)
    parser.add_argument("--completed-maintenance-prior", action="store_true")
    parser.add_argument("--source-inventory", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--original-source-root", type=Path, action="append", default=[])
    parser.add_argument("--install-candidates", action="store_true")
    parser.add_argument("--captured-v3-preparation", type=Path)
    parser.add_argument("--captured-v3-preparation-sha256")
    parser.add_argument("--captured-v3-source-root", type=Path)
    parser.add_argument("--selection-database", type=Path)
    prepare(parser.parse_args())

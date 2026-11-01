"""Prepare immutable retained readers before applying a changed recipe.

This file-only producer grants no execution, publication or replay authority.
Its output must be installed and included in a new validated release receipt
before trusted startup wiring configures the retained registry. No DB access.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from .product_release_provenance import SOURCE_REF as _PROVENANCE_CODEC_REF
from .product_release_provenance import resolve_product_receipt

_SHA = re.compile(r"[a-f0-9]{64}")
_BASE = "packages/kernel/research_recipe_versions/"
_REGISTRY_SCHEMA = "research-retained-recipe-registry.v1"
_REGISTRY_SCHEMA_V2 = "research-retained-recipe-registry.v2"
_RECEIPT_KEYS = {"schema", "repository_root", "sources", "artifacts", "plugin_build_hash", "evidence"}
_MAX_BYTES = 2 * 1024 * 1024
_MAX_TOTAL = 32 * 1024 * 1024

@dataclass(frozen=True)
class PlannedRecipeSourceChange:
    path: str
    baseline_sha256: str | None
    candidate_sha256: str | None
    candidate_path: Path | None


@dataclass(frozen=True)
class PreparedRecipeArchive:
    changed: bool
    archived_graph_hash: str | None
    registry_ref: Mapping[str, str] | None
    install_sources: Mapping[str, str]
    shared_source_guards: Mapping[str, str]
    prior_receipt_sha256: str
    retained_authority_configured: bool = False


def _fail(code: str = "research_recipe_archive_input_invalid") -> ValueError:
    return ValueError(code)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _relative(value: object) -> str:
    if type(value) is not str:
        raise _fail()
    path = PurePosixPath(value)
    if (not value or path.is_absolute() or path.as_posix() != value
            or any(part in {"", ".", ".."} for part in value.split("/"))
            or re.search(r'[\\\x00-\x1f<>:"|?*]', value)):
        raise _fail()
    return value


def retained_v3_installation_directory(
    graph_hash: str, installation_receipt: Mapping[str, str], installed_pointer: Mapping[str, str],
) -> str:
    if type(graph_hash) is not str or _SHA.fullmatch(graph_hash) is None:
        raise _fail("research_recipe_archive_v3_installation_invalid")
    for ref in (installation_receipt, installed_pointer):
        if (type(ref) is not dict or set(ref) != {"path", "sha256"}
                or type(ref["sha256"]) is not str or _SHA.fullmatch(ref["sha256"]) is None):
            raise _fail("research_recipe_archive_v3_installation_invalid")
        _relative(ref["path"])
    return _sha(_canonical({"schema_version": "retained-v3-installation-key.v1",
                           "graph_hash": graph_hash, "installation_receipt": dict(installation_receipt),
                           "installed_pointer": dict(installed_pointer)})[:-1])


def _registry_document(registrations: Sequence[Mapping[str, Any]], *, force_v2: bool = False) -> dict[str, Any]:
    v2 = force_v2 or any(row["directory"] != row["graph_hash"] for row in registrations)
    return {"schema_version": _REGISTRY_SCHEMA_V2 if v2 else _REGISTRY_SCHEMA,
            "registrations": sorted(registrations, key=(lambda row: (row["graph_hash"], row["directory"]))
                                    if v2 else (lambda row: row["graph_hash"]))}


def _io_path(path: Path) -> Path:
    value = str(path.absolute())
    if os.name == "nt" and not value.startswith("\\\\?\\"):
        value = "\\\\?\\" + value
    return Path(value)


def _path(root: Path, relative: str, *, existing: bool = True) -> Path:
    current = root
    for part in PurePosixPath(_relative(relative)).parts:
        current = current / part
        actual = _io_path(current)
        if actual.exists() or actual.is_symlink():
            status = actual.lstat()
            if actual.is_symlink() or getattr(status, "st_file_attributes", 0) & 0x400:
                raise _fail("research_recipe_archive_link_forbidden")
        elif existing:
            raise _fail("research_recipe_archive_resource_missing")
    if existing and not _io_path(current).is_file():
        raise _fail()
    return _io_path(current)


def _read(path: Path) -> bytes:
    path = _io_path(path)
    before = path.stat()
    if before.st_size > _MAX_BYTES:
        raise _fail("research_recipe_archive_resource_oversized")
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        # Windows path.stat and fstat expose different ctime meanings.
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns):
            raise _fail("research_recipe_archive_source_changed")
        raw = stream.read(_MAX_BYTES + 1)
        ended = os.fstat(stream.fileno())
    final = path.stat()
    stamp = lambda value: (value.st_dev, value.st_ino, value.st_size,
                           value.st_mtime_ns, value.st_ctime_ns)
    if (len(raw) > _MAX_BYTES or stamp(opened) != stamp(ended)
            or stamp(before) != stamp(final)):
        raise _fail("research_recipe_archive_source_changed")
    return raw


def _bound_receipt(root: Path, path: Path, digest: str) -> tuple[dict[str, Any], bytes]:
    if type(digest) is not str or _SHA.fullmatch(digest) is None:
        raise _fail()
    relative = path.absolute().relative_to(root.absolute()).as_posix()
    raw = _read(_path(root, relative))
    if _sha(raw) != digest:
        raise _fail("research_recipe_archive_receipt_changed")
    resolved = resolve_product_receipt(
        raw, expected_root=root, expected_sha256=digest,
        read_resource=lambda name: _read(_path(root, name)),
    )
    return dict(resolved.value), raw


def _installed_closure(root: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, str], dict[str, str]]:
    # This producer is deliberately not imported by either recipe or loader;
    # its registry data therefore cannot create an AST import/hash cycle.
    from . import research_context_selection as selector
    from . import research_context_selection_versions as retained_selector
    from . import research_graph_recipe as recipe

    if recipe._ROOT.resolve() != root.resolve() or selector._ROOT.resolve() != root.resolve():
        raise _fail("research_recipe_archive_loaded_root_mismatch")
    manifest = recipe.graph_recipe_manifest_v2().model_dump(mode="json")
    identity = selector._selector_identity()
    identity_files = selector._selector_file_hashes()
    declared = {row["path"]: row["payload_hash"]
                for row in manifest["source_files"] + manifest["schema_files"]}
    auxiliary = {name: expected for name, expected in retained_selector._FILES.items()
                 if name not in declared}
    return manifest, identity, identity_files, auxiliary


def _recipe_initial_modules(raw: bytes | None, *, version: int) -> tuple[str, ...]:
    """Read an original finite declaration; never execute archived producer code."""
    if raw is None:
        raise _fail("research_recipe_archive_initial_declaration_invalid")
    try:
        functions = [
            node for node in ast.parse(raw).body
            if isinstance(node, ast.FunctionDef) and node.name == {
                2: "_build_graph_recipe_manifest_v2", 3: "graph_recipe_manifest_v3"
            }[version]
        ]
        if len(functions) != 1:
            raise ValueError("function")
        assignments = [
            node for node in functions[0].body
            if isinstance(node, ast.Assign) and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name) and node.targets[0].id == "initial"
        ]
        if len(assignments) != 1:
            raise ValueError("assignment")
        value = ast.literal_eval(assignments[0].value)
        if (type(value) is not tuple or not 1 <= len(value) <= 16 or len(set(value)) != len(value)
                or any(type(name) is not str or re.fullmatch(
                    r"(?:packages|apps)\.[a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)*", name
                ) is None for name in value)):
            raise ValueError("modules")
        return value
    except (SyntaxError, ValueError, TypeError) as error:
        raise _fail("research_recipe_archive_initial_declaration_invalid") from error


def _planned_manifest(root: Path, overlay: Mapping[str, bytes | None], runtime: Mapping[str, str], *, version: int = 2, closed_overlay: bool = False) -> dict[str, Any]:
    """Mirror the installed v2 AST closure; equality is checked before use."""
    def payload(relative: str) -> bytes | None:
        if relative in overlay:
            return overlay[relative]
        if closed_overlay:
            return None
        candidate = _path(root, relative, existing=False)
        return _read(candidate) if candidate.is_file() else None

    def module_file(name: str) -> str | None:
        if not name.startswith("packages."):
            return None
        pieces = name.split(".")
        while len(pieces) >= 2:
            stem = "/".join(pieces)
            for relative in (stem + ".py", stem + "/__init__.py"):
                if payload(relative) is not None:
                    return relative
            pieces.pop()
        return None

    initial = _recipe_initial_modules(payload(
        "packages/kernel/decision_hub_kernel/application/research_graph_recipe.py"
        if version == 2 else
        "packages/kernel/decision_hub_kernel/application/research_graph_recipe_v3.py"
    ), version=version)
    pending = [relative for name in initial if (relative := module_file(name)) is not None]
    seen: dict[str, str] = {}
    total = 0
    while pending:
        relative = pending.pop()
        if relative in seen:
            continue
        raw = payload(relative)
        if raw is None:
            raise _fail("research_recipe_archive_planned_dependency_missing")
        total += len(raw)
        if total > _MAX_TOTAL or len(seen) >= 1024:
            raise _fail("research_recipe_archive_closure_limit")
        seen[relative] = _sha(raw)
        package_parts = relative.removesuffix(".py").split("/")[:-1]
        for node in ast.walk(ast.parse(raw, filename=relative)):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    prefix = package_parts[:len(package_parts) - node.level + 1]
                    module = ".".join([*prefix, *(node.module or "").split(".")]).rstrip(".")
                else:
                    module = node.module or ""
                names = [module, *(f"{module}.{alias.name}" for alias in node.names)]
            for name in names:
                if (dependency := module_file(name)) is not None:
                    pending.append(dependency)
    # The installed builder declares this ONE non-Python method input. Old
    # original builders lack the declaration and keep their exact old closure.
    recipe_raw = payload("packages/kernel/decision_hub_kernel/application/research_graph_recipe.py")
    finite = [] if recipe_raw is None else [
        node for node in ast.parse(recipe_raw).body
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "_FINITE_METHOD_SOURCE_REF"
            for target in node.targets
        )
    ]
    if finite and "packages/kernel/decision_hub_kernel/application/perpetual_close_source.py" in seen:
        if len(finite) != 1:
            raise _fail("research_recipe_archive_method_resource_invalid")
        name = ast.literal_eval(finite[0].value)
        if name != "docs/design/decision-product-control-2026-10-17/02_DECISION_METHOD_AND_EVALUATION.md":
            raise _fail("research_recipe_archive_method_resource_invalid")
        raw = payload(name)
        if raw is None:
            raise _fail("research_recipe_archive_method_resource_missing")
        total += len(raw)
        seen[name] = _sha(raw)
    schema_names = set() if closed_overlay else {path.relative_to(root).as_posix()
                    for path in (root / "contracts/schemas").rglob("*")
                    if path.is_file() and path.suffix in {".json", ".yaml", ".yml"}}
    schema_names.update(name for name, raw in overlay.items()
                        if name.startswith("contracts/schemas/")
                        and PurePosixPath(name).suffix in {".json", ".yaml", ".yml"}
                        and raw is not None)
    if version == 3:
        schema_names.update({
            _BASE + "candidates/schemas/quality-projection-context.v1.json",
            _BASE + "candidates/schemas/quality-decision.v1.json",
        })
    schemas = []
    for name in sorted(schema_names):
        raw = payload(name)
        if raw is not None:
            total += len(raw)
            schemas.append({"path": name, "payload_hash": _sha(raw)})
    if total > _MAX_TOTAL:
        raise _fail("research_recipe_archive_closure_limit")
    result = {"schema_version": f"research-graph-recipe-manifest.v{version}",
            "source_files": [{"path": name, "payload_hash": seen[name]} for name in sorted(seen)],
            "schema_files": schemas, "runtime_versions": dict(runtime)}
    if version == 3:
        ref = _BASE + "candidates/research-graph-projection.v3.json"
        raw = payload(ref)
        if raw is None:
            raise _fail("research_recipe_archive_resource_missing")
        result["registration"] = {"path": ref, "sha256": _sha(raw)}
    return result


def _registry_from_prior(root: Path, receipt: Mapping[str, Any]) -> list[dict[str, Any]]:
    evidence = receipt["evidence"].get("retained_recipe_archives_v1")
    if evidence is None:
        return []
    if (type(evidence) is not dict or set(evidence) != {"registry"}
            or type(evidence["registry"]) is not dict
            or set(evidence["registry"]) != {"path", "sha256"}):
        raise _fail()
    ref = evidence["registry"]
    digest = ref["sha256"]
    if (type(digest) is not str or _SHA.fullmatch(digest) is None
            or ref["path"] != _BASE + "registries/" + digest + ".json"
            or receipt["sources"].get(ref["path"]) != digest):
        raise _fail()
    raw = _read(_path(root, ref["path"]))
    value = json.loads(raw)
    if (_sha(raw) != digest or _canonical(value) != raw
            or set(value) != {"schema_version", "registrations"}
            or value["schema_version"] not in {_REGISTRY_SCHEMA, _REGISTRY_SCHEMA_V2}
            or type(value["registrations"]) is not list or len(value["registrations"]) > 256):
        raise _fail("research_recipe_archive_registry_changed")
    hashes, directories = set(), set()
    v2 = value["schema_version"] == _REGISTRY_SCHEMA_V2
    for row in value["registrations"]:
        if (type(row) is not dict or set(row) != {"directory", "index_hash", "graph_hash",
                                                "selector_hash", "private_original_orm"}
                or any(type(row[key]) is not str or _SHA.fullmatch(row[key]) is None
                       for key in ("directory", "graph_hash", "index_hash", "selector_hash"))
                or not v2 and (row["directory"] != row["graph_hash"] or row["graph_hash"] in hashes)
                or row["private_original_orm"] is not True or row["directory"] in directories):
            raise _fail()
        hashes.add(row["graph_hash"])
        directories.add(row["directory"])
        _existing_archive(root, receipt["sources"], row)
    if value["registrations"] != _registry_document(value["registrations"], force_v2=v2)["registrations"]:
        raise _fail()
    return value["registrations"]


def _existing_archive(root: Path, sources: Mapping[str, str], registration: Mapping[str, Any]) -> None:
    prefix = _BASE + registration["directory"] + "/"
    relative = prefix + "release.json"
    raw = _read(_path(root, relative))
    if sources.get(relative) != registration["index_hash"] or _sha(raw) != registration["index_hash"]:
        raise _fail("research_recipe_archive_existing_changed")
    index = json.loads(raw)
    if (index.get("schema_version") == "research-retained-recipe-release.v3"
            and raw != _canonical(index)):
        raise _fail("research_recipe_archive_v3_index_invalid")
    manifest = index["graph_recipe_manifest"]
    declared = {row["path"]: row["payload_hash"]
                for row in manifest["source_files"] + manifest["schema_files"]}
    v3 = manifest["schema_version"] == "research-graph-recipe-manifest.v3"
    if v3:
        declared = _v3_stored_files(index)
    allowed = {"research-retained-recipe-release.v2", "research-retained-recipe-release.v3"} if v3 else {"research-retained-recipe-release.v1"}
    if (index["schema_version"] not in allowed
            or index["graph_recipe_dependency_hash"] != registration["graph_hash"]
            or _sha(_canonical(manifest)[:-1]) != registration["graph_hash"]
            or index["selector_identity"]["implementation_hash"] != registration["selector_hash"]
            or set(index["stored_files"]) != set(declared)
            or set(index["all_files"]) != set(declared) | set(index["shared_auxiliary_files"])
            or index["orm_mode"] != "private_original_orm.v1"):
        raise _fail()
    for name, digest in index["all_files"].items():
        destination = prefix + "root/" + name if name in declared else name
        if sources.get(destination) != digest or _sha(_read(_path(root, destination))) != digest:
            raise _fail("research_recipe_archive_existing_changed")
    if v3:
        _validate_retained_v3_installation(index, {
            name: _read(_path(root, prefix + "root/" + name)) for name in declared
        }, expected_root=root)
    if registration["directory"] != registration["graph_hash"]:
        binding = index.get("installation_binding", {})
        if (not v3 or registration["directory"] != retained_v3_installation_directory(
                registration["graph_hash"], binding.get("receipt"), binding.get("pointer"))):
            raise _fail("research_recipe_archive_v3_directory_invalid")


def _v3_stored_files(index: Mapping[str, Any]) -> dict[str, str]:
    """Closed immutable V3 manifest plus original installation resources."""
    expected = {"schema_version", "graph_recipe_dependency_hash", "graph_recipe_manifest",
                "selector_identity", "selector_identity_files", "stored_files",
                "shared_auxiliary_files", "all_files", "orm_mode", "installation_binding"}
    if set(index) != expected or index["schema_version"] not in {
            "research-retained-recipe-release.v2", "research-retained-recipe-release.v3"}:
        raise _fail("research_recipe_archive_v3_index_invalid")
    manifest = index["graph_recipe_manifest"]
    if (type(manifest) is not dict or set(manifest) != {
            "schema_version", "source_files", "schema_files", "registration", "runtime_versions"}
            or manifest["schema_version"] != "research-graph-recipe-manifest.v3"):
        raise _fail("research_recipe_archive_v3_manifest_invalid")
    declared: dict[str, str] = {}
    for group in ("source_files", "schema_files"):
        rows = manifest[group]
        if type(rows) is not list or not rows or rows != sorted(rows, key=lambda row: row["path"]):
            raise _fail("research_recipe_archive_v3_manifest_invalid")
        for row in rows:
            if type(row) is not dict or set(row) != {"path", "payload_hash"}:
                raise _fail()
            path, digest = _relative(row["path"]), row["payload_hash"]
            if path in declared or type(digest) is not str or _SHA.fullmatch(digest) is None:
                raise _fail()
            declared[path] = digest
    binding = index["installation_binding"]
    new_transport = index["schema_version"] == "research-retained-recipe-release.v3"
    binding_keys = {"receipt", "pointer", "repository_root"} | ({"source_map_resources"} if new_transport else set())
    if type(binding) is not dict or set(binding) != binding_keys:
        raise _fail("research_recipe_archive_v3_installation_invalid")
    for ref in (manifest["registration"], binding["receipt"], binding["pointer"]):
        if type(ref) is not dict or set(ref) != {"path", "sha256"}:
            raise _fail()
        path, digest = _relative(ref["path"]), ref["sha256"]
        if type(digest) is not str or _SHA.fullmatch(digest) is None or path in declared:
            raise _fail()
        declared[path] = digest
    if new_transport:
        resources = binding["source_map_resources"]
        if type(resources) is not dict or not resources or _PROVENANCE_CODEC_REF not in declared:
            raise _fail("research_recipe_archive_v3_transport_invalid")
        for name, digest in resources.items():
            name = _relative(name)
            if (not name.startswith("artifacts/product-provenance/") or name in declared
                    or type(digest) is not str or _SHA.fullmatch(digest) is None):
                raise _fail("research_recipe_archive_v3_transport_invalid")
            declared[name] = digest
    if (manifest["registration"]["path"] != _BASE + "candidates/research-graph-projection.v3.json"
            or binding["pointer"]["path"] != "infra/dsh/research-graph-recipe-installed.v3.json"
            or type(binding["repository_root"]) is not str
            or index["shared_auxiliary_files"] != {}
            or index["stored_files"] != sorted(declared)
            or index["all_files"] != dict(sorted(declared.items()))
            or index["orm_mode"] != "private_original_orm.v1"
            or _sha(_canonical(manifest)[:-1]) != index["graph_recipe_dependency_hash"]):
        raise _fail("research_recipe_archive_v3_index_invalid")
    return declared


def _validate_retained_v3_installation(
    index: Mapping[str, Any], payloads: Mapping[str, bytes], *, expected_root: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Authenticate old installation bytes, never today's pointer or receipt."""
    declared = _v3_stored_files(index)
    index_bytes = _canonical(index)
    index_size = len(index_bytes) if index["schema_version"] == "research-retained-recipe-release.v3" else 0
    if (len(index_bytes) > _MAX_BYTES or any(len(raw) > _MAX_BYTES for raw in payloads.values())
            or index_size + sum(map(len, payloads.values())) > _MAX_TOTAL):
        raise _fail("research_recipe_archive_closure_limit")
    if any(name not in payloads or _sha(payloads[name]) != digest for name, digest in declared.items()):
        raise _fail("research_recipe_archive_v3_original_changed")
    binding, manifest = index["installation_binding"], index["graph_recipe_manifest"]
    new_transport = index["schema_version"] == "research-retained-recipe-release.v3"
    if new_transport:
        if set(payloads) != set(declared):
            raise _fail("research_recipe_archive_v3_original_changed")
        resolved = resolve_product_receipt(
            payloads[binding["receipt"]["path"]], expected_root=expected_root,
            expected_sha256=binding["receipt"]["sha256"], read_resource=payloads.__getitem__,
        )
        if (resolved.value["schema"] != "product-validated-builds.v2"
                or dict(resolved.transport) != binding["source_map_resources"]):
            raise _fail("research_recipe_archive_v3_transport_invalid")
        receipt = dict(resolved.value)
    else:
        receipt = json.loads(payloads[binding["receipt"]["path"]])
    pointer = json.loads(payloads[binding["pointer"]["path"]])
    if (type(receipt) is not dict or set(receipt) != _RECEIPT_KEYS
            or receipt["schema"] != ("product-validated-builds.v2" if new_transport else "product-validated-builds.v1")
            or receipt["repository_root"] != binding["repository_root"]
            or Path(receipt["repository_root"]).resolve() != expected_root.resolve()
            or type(receipt["sources"]) is not dict or not receipt["sources"]
            or type(receipt["artifacts"]) is not dict or not receipt["artifacts"]
            or type(receipt["evidence"]) is not dict):
        raise _fail("research_recipe_archive_v3_installation_invalid")
    for mapping in (receipt["sources"], receipt["artifacts"]):
        for name, digest in mapping.items():
            _relative(name)
            if type(digest) is not str or _SHA.fullmatch(digest) is None:
                raise _fail()
    evidence = receipt["evidence"].get("research_graph_recipe_v3")
    if (evidence != {"installed_pointer": binding["pointer"]}
            or type(pointer) is not dict or set(pointer) != {
                "schema_version", "recipe_version", "lifecycle", "registration",
                "dependency_hash", "plugin_build_hash"}
            or pointer["schema_version"] != "research-graph-recipe-installed.v3"
            or pointer["recipe_version"] != "research-graph-projection.v3"
            or pointer["lifecycle"] != "installed"
            or pointer["dependency_hash"] != index["graph_recipe_dependency_hash"]
            or pointer["registration"] != manifest["registration"]
            or pointer["plugin_build_hash"] != receipt["plugin_build_hash"]
            or type(pointer["plugin_build_hash"]) is not str
            or _SHA.fullmatch(pointer["plugin_build_hash"]) is None
            or any(receipt["sources"].get(name) != digest for name, digest in declared.items()
                   if name != binding["receipt"]["path"]
                   and name not in binding.get("source_map_resources", {}))):
        raise _fail("research_recipe_archive_v3_installation_invalid")
    return receipt, pointer


def _literal_assignment(raw: bytes, name: str) -> Any:
    for node in ast.parse(raw).body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise _fail("research_recipe_archive_selector_algorithm_unsupported")


def prepare_retained_graph_recipe_v3_archive(
    *, repository_root: Path, prior_receipt_path: Path, prior_receipt_sha256: str,
    original_manifest: Mapping[str, Any], staging_root: Path,
    original_source_roots: Sequence[Path] = (),
) -> PreparedRecipeArchive:
    """File-only capture before source promotion, with no DB or project imports."""
    root, stage = repository_root.absolute(), staging_root.absolute()
    if (not stage.is_relative_to(root / "artifacts") or stage == root / "artifacts"
            or stage.exists() and (stage.is_symlink() or getattr(stage.lstat(), "st_file_attributes", 0) & 0x400)):
        raise _fail("research_recipe_archive_staging_scope_invalid")
    receipt, receipt_raw = _bound_receipt(root, prior_receipt_path, prior_receipt_sha256)
    resolved = resolve_product_receipt(
        receipt_raw, expected_root=root, expected_sha256=prior_receipt_sha256,
        read_resource=lambda name: _read(_path(root, name)),
    )
    manifest = dict(original_manifest)
    if manifest.get("schema_version") != "research-graph-recipe-manifest.v3":
        raise _fail("research_recipe_archive_closure_algorithm_unsupported")
    receipt_ref = {"path": prior_receipt_path.absolute().relative_to(root).as_posix(), "sha256": prior_receipt_sha256}
    pointer_ref = receipt["evidence"]["research_graph_recipe_v3"]["installed_pointer"]
    declared = {row["path"]: row["payload_hash"] for row in manifest["source_files"] + manifest["schema_files"]}
    for ref in (manifest["registration"], pointer_ref):
        if receipt["sources"].get(ref["path"]) != ref["sha256"] or ref["path"] in declared:
            raise _fail("research_recipe_archive_source_unvalidated")
        declared[ref["path"]] = ref["sha256"]
    source_roots = tuple(path.absolute() for path in original_source_roots)
    for location in source_roots:
        if (not location.is_relative_to(root / "artifacts") or location == root / "artifacts"
                or not _path(root, location.relative_to(root).as_posix(), existing=False).is_dir()):
            raise _fail("research_recipe_archive_original_source_scope_invalid")

    def original_payload(name: str, digest: str) -> tuple[bytes, Path]:
        for location in source_roots or (root,):
            candidate = _path(location, name, existing=False)
            if candidate.is_file():
                raw = _read(candidate)
                if _sha(raw) == digest:
                    return raw, location / name
        raise _fail("research_recipe_archive_source_changed")

    payloads, selected_paths = {}, {}
    for name, digest in declared.items():
        if receipt["sources"].get(name) != digest:
            raise _fail("research_recipe_archive_source_unvalidated")
        payloads[name], selected_paths[name] = original_payload(name, digest)
    overlay = payloads if source_roots else {}
    if _planned_manifest(root, overlay, manifest["runtime_versions"], version=3,
                         closed_overlay=bool(source_roots)) != manifest:
        raise _fail("research_recipe_archive_closure_algorithm_unsupported")
    declared[receipt_ref["path"]] = prior_receipt_sha256
    payloads[receipt_ref["path"]] = receipt_raw
    if resolved.transport:
        if _PROVENANCE_CODEC_REF not in declared:
            raise _fail("research_recipe_archive_v3_codec_missing")
        for name, digest in resolved.transport.items():
            if name in declared:
                raise _fail("research_recipe_archive_v3_transport_invalid")
            raw, chosen = original_payload(name, digest)
            declared[name], payloads[name] = digest, raw
            selected_paths[name] = chosen
    if sum(map(len, payloads.values())) > _MAX_TOTAL:
        raise _fail("research_recipe_archive_closure_limit")
    selector_path = "packages/kernel/decision_hub_kernel/application/research_context_selection.py"
    identity_paths = _literal_assignment(payloads[selector_path], "_IDENTITY_PATHS")
    selector_files = {name: declared[name] for name in identity_paths}
    selector_hash = _sha(_canonical({"files": selector_files, "codec": "canonical_context_selection.v3"})[:-1])
    ranking_ref = _literal_assignment(payloads[
        "packages/kernel/decision_hub_kernel/application/research_context_selector_v2.py"], "RANKING_RULES_REF")
    selector = {"time_rule_ref": "research-context.received-cutoff.v1", "ranking_rules_ref": ranking_ref,
                "context_policy_source_hash": selector_files[identity_paths[0]],
                "producer_source_hash": selector_files[identity_paths[2]],
                "implementation_hash": selector_hash, "implementation_hash_kind": "canonical_context_selector_sources.v1"}
    graph_hash = _sha(_canonical(manifest)[:-1])
    index: dict[str, Any] = {"schema_version": "research-retained-recipe-release.v3" if resolved.transport else "research-retained-recipe-release.v2",
             "graph_recipe_dependency_hash": graph_hash, "graph_recipe_manifest": manifest,
             "selector_identity": selector, "selector_identity_files": selector_files,
             "stored_files": sorted(declared), "shared_auxiliary_files": {},
             "all_files": dict(sorted(declared.items())), "orm_mode": "private_original_orm.v1",
             "installation_binding": {"receipt": receipt_ref, "pointer": pointer_ref, "repository_root": receipt["repository_root"]}}
    if resolved.transport:
        index["installation_binding"]["source_map_resources"] = dict(resolved.transport)
    _validate_retained_v3_installation(index, payloads, expected_root=root)
    registrations = _registry_from_prior(root, receipt)
    index_raw = _canonical(index)
    directory = retained_v3_installation_directory(graph_hash, receipt_ref, pointer_ref)
    registration = {"directory": directory, "index_hash": _sha(index_raw), "graph_hash": graph_hash,
                    "selector_hash": selector_hash, "private_original_orm": True}
    previous = next((row for row in registrations if row["directory"] == directory), None)
    if previous is not None and previous != registration:
        raise _fail("research_recipe_archive_registry_conflict")
    if previous is None:
        registrations.append(registration)
    if _bound_receipt(root, prior_receipt_path, prior_receipt_sha256)[1] != receipt_raw:
        raise _fail("research_recipe_archive_receipt_changed")
    for name, raw in payloads.items():
        source = prior_receipt_path if name == receipt_ref["path"] else selected_paths[name]
        if _read(_path(root, source.absolute().relative_to(root).as_posix())) != raw:
            raise _fail("research_recipe_archive_source_changed")
    if _planned_manifest(root, overlay, manifest["runtime_versions"], version=3,
                         closed_overlay=bool(source_roots)) != manifest:
        raise _fail("research_recipe_archive_source_changed")
    prefix = _BASE + directory + "/"
    installed = {}
    for name, raw in sorted(payloads.items()):
        destination = prefix + "root/" + name
        _publish(_path(stage, destination, existing=False), raw)
        installed[destination] = _sha(raw)
    _publish(_path(stage, prefix + "release.json", existing=False), index_raw)
    installed[prefix + "release.json"] = _sha(index_raw)
    registry_raw = _canonical(_registry_document(registrations, force_v2=True))
    registry_ref = _BASE + "registries/" + _sha(registry_raw) + ".json"
    _publish(_path(stage, registry_ref, existing=False), registry_raw)
    installed[registry_ref] = _sha(registry_raw)
    return PreparedRecipeArchive(True, graph_hash, {"path": registry_ref, "sha256": _sha(registry_raw)}, installed, {}, prior_receipt_sha256)


def _publish(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".recipe-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if _read(path) != raw:
                raise _fail("research_recipe_archive_immutable_collision")
    finally:
        Path(temporary).unlink(missing_ok=True)


def prepare_retained_recipe_archive(
    *, repository_root: Path, prior_receipt_path: Path, prior_receipt_sha256: str,
    planned_changes: Sequence[PlannedRecipeSourceChange], staging_root: Path,
    planned_runtime_versions: Mapping[str, str] | None = None,
) -> PreparedRecipeArchive:
    """Called once by trusted maintenance prepare, before changing originals.

    The normal validated-product-builds guard still verifies the full source
    and artifact maps. This producer authenticates every captured closure byte
    and auxiliary dependency against that exact prior receipt, twice. Its
    output is a prepared source delta, never automatic production installation.
    """
    root = repository_root.absolute()
    stage = staging_root.absolute()
    if (not stage.is_relative_to(root / "artifacts") or stage == root / "artifacts"
            or stage.exists() and (stage.is_symlink()
                                  or getattr(stage.lstat(), "st_file_attributes", 0) & 0x400)):
        raise _fail("research_recipe_archive_staging_scope_invalid")
    receipt, receipt_raw = _bound_receipt(root, prior_receipt_path, prior_receipt_sha256)
    manifest, selector, selector_files, auxiliary = _installed_closure(root)
    if _planned_manifest(root, {}, manifest["runtime_versions"]) != manifest:
        raise _fail("research_recipe_archive_closure_algorithm_unsupported")
    overlay: dict[str, bytes | None] = {}
    candidates: dict[Path, bytes] = {}
    for change in planned_changes:
        name = _relative(change.path)
        if (name in overlay or name.startswith(_BASE)
                or change.baseline_sha256 is None and change.candidate_sha256 is None):
            raise _fail("research_recipe_archive_plan_conflict")
        source = _path(root, name, existing=change.baseline_sha256 is not None)
        if change.baseline_sha256 is None:
            if source.exists() or name in receipt["sources"]:
                raise _fail("research_recipe_archive_plan_conflict")
        elif (receipt["sources"].get(name) != change.baseline_sha256
              or _sha(_read(source)) != change.baseline_sha256):
            raise _fail("research_recipe_archive_source_unvalidated")
        if change.candidate_sha256 is None:
            if change.candidate_path is not None:
                raise _fail()
            overlay[name] = None
        else:
            if change.candidate_path is None or _SHA.fullmatch(change.candidate_sha256) is None:
                raise _fail()
            candidate = _path(root, change.candidate_path.absolute().relative_to(root).as_posix())
            raw = _read(candidate)
            if _sha(raw) != change.candidate_sha256:
                raise _fail("research_recipe_archive_candidate_changed")
            candidates[candidate] = raw
            overlay[name] = raw
    runtime = manifest["runtime_versions"] if planned_runtime_versions is None else dict(planned_runtime_versions)
    if set(runtime) != set(manifest["runtime_versions"]) or any(type(value) is not str or not value for value in runtime.values()):
        raise _fail()
    planned = _planned_manifest(root, overlay, runtime)
    if planned == manifest:
        return PreparedRecipeArchive(False, None, None, {}, {}, prior_receipt_sha256)
    declared = {row["path"]: row["payload_hash"]
                for row in manifest["source_files"] + manifest["schema_files"]}
    if (any(name.startswith(_BASE + "registries/") for name in declared)
            or set(selector_files) - set(declared)
            or any(declared[name] != digest for name, digest in selector_files.items())
            or _sha(_canonical({"files": selector_files, "codec": "canonical_context_selection.v3"})[:-1])
            != selector["implementation_hash"]):
        raise _fail("research_recipe_archive_selector_changed")
    payloads: dict[str, bytes] = {}
    for name, digest in (declared | auxiliary).items():
        if receipt["sources"].get(name) != digest:
            raise _fail("research_recipe_archive_source_unvalidated")
        raw = _read(_path(root, name))
        if _sha(raw) != digest:
            raise _fail("research_recipe_archive_source_changed")
        payloads[name] = raw
    if sum(map(len, payloads.values())) > _MAX_TOTAL:
        raise _fail("research_recipe_archive_closure_limit")
    registrations = _registry_from_prior(root, receipt)
    graph_hash = _sha(_canonical(manifest)[:-1])
    prefix = _BASE + graph_hash + "/"
    index = {"schema_version": "research-retained-recipe-release.v1",
             "graph_recipe_dependency_hash": graph_hash, "graph_recipe_manifest": manifest,
             "selector_identity": selector, "selector_identity_files": selector_files,
             "stored_files": sorted(declared), "shared_auxiliary_files": auxiliary,
             "all_files": dict(sorted((declared | auxiliary).items())), "orm_mode": "private_original_orm.v1"}
    index_raw = _canonical(index)
    registration = {"directory": graph_hash, "index_hash": _sha(index_raw), "graph_hash": graph_hash,
                    "selector_hash": selector["implementation_hash"], "private_original_orm": True}
    previous = next((row for row in registrations if row["directory"] == graph_hash), None)
    if previous is not None and previous != registration:
        raise _fail("research_recipe_archive_registry_conflict")
    if previous is None:
        registrations.append(registration)
    registry_raw = _canonical(_registry_document(registrations))
    registry_hash = _sha(registry_raw)
    registry_path = _BASE + "registries/" + registry_hash + ".json"
    # Revalidate original bytes before publishing any prepared archive. No
    # final SHA is replaced by a stat or cached digest.
    if _bound_receipt(root, prior_receipt_path, prior_receipt_sha256)[1] != receipt_raw:
        raise _fail("research_recipe_archive_receipt_changed")
    for name, raw in payloads.items():
        if _read(_path(root, name)) != raw:
            raise _fail("research_recipe_archive_source_changed")
    if _installed_closure(root) != (manifest, selector, selector_files, auxiliary):
        raise _fail("research_recipe_archive_source_changed")
    for path, raw in candidates.items():
        if _read(path) != raw:
            raise _fail("research_recipe_archive_candidate_changed")
    installed: dict[str, str] = {}
    for name in sorted(declared):
        destination = prefix + "root/" + name
        _publish(_path(stage, destination, existing=False), payloads[name])
        installed[destination] = declared[name]
    _publish(_path(stage, prefix + "release.json", existing=False), index_raw)
    installed[prefix + "release.json"] = _sha(index_raw)
    # Registry is published last, only after its entire immutable directory.
    _publish(_path(stage, registry_path, existing=False), registry_raw)
    installed[registry_path] = registry_hash
    return PreparedRecipeArchive(True, graph_hash, {"path": registry_path, "sha256": registry_hash},
                                 installed, auxiliary, prior_receipt_sha256)


if __name__ == "__main__":
    import argparse
    from dataclasses import asdict

    parser = argparse.ArgumentParser(description="Capture original installed V3 readers; file-only, no installation")
    parser.add_argument("--capture-v3", action="store_true", required=True)
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--prior-receipt", type=Path, required=True)
    parser.add_argument("--prior-sha256", required=True)
    parser.add_argument("--originals-json", type=Path, required=True)
    parser.add_argument("--originals-sha256", required=True)
    parser.add_argument("--staging-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--original-source-root", type=Path, action="append", default=[])
    args = parser.parse_args()
    raw = _read(args.originals_json)
    # Observer originals can exceed the per-resource limit; use a separately
    # extracted exact graph_recipe_selection row for this finite capture.
    if _sha(raw) != args.originals_sha256:
        raise _fail("research_recipe_archive_selection_original_changed")
    row = json.loads(raw)
    if (type(row) is not dict or row.get("role") != "graph_recipe_selection"
            or row.get("schema_version") != "research-graph-recipe-selection.v3"
            or type(row.get("payload_json")) is not str
            or _sha(row["payload_json"].encode()) != row.get("payload_hash")):
        raise _fail("research_recipe_archive_selection_original_invalid")
    selection = json.loads(row["payload_json"])
    receipt_path = args.prior_receipt.absolute()
    root = args.repository_root.absolute()
    expected_ref = {"path": receipt_path.relative_to(root).as_posix(), "sha256": args.prior_sha256}
    if (selection.get("installation_receipt") != expected_ref
            or selection.get("dependency_hash") != _sha(_canonical(selection["manifest"])[:-1])):
        raise _fail("research_recipe_archive_selection_original_invalid")
    prepared = prepare_retained_graph_recipe_v3_archive(
        repository_root=root, prior_receipt_path=receipt_path,
        prior_receipt_sha256=args.prior_sha256, original_manifest=selection["manifest"],
        staging_root=args.staging_root,
        original_source_roots=args.original_source_root,
    )
    if _read(args.originals_json) != raw:
        raise _fail("research_recipe_archive_selection_original_changed")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(asdict(prepared), stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write("\n")

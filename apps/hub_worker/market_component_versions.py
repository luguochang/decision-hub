"""Retain pure installed functions, separately from historical research read grants.

Preparation is file-only and grants no execution. Loading requires the archive
index AND every original to be bound to the current managed release receipt.
Only current application code initializes/migrates the database. Nothing here
opens a database, registers approval, changes a pointer or starts old services.
"""

from __future__ import annotations

import ast
import builtins
import hashlib
import importlib
import importlib.metadata
import importlib.util
import inspect
import json
import sys
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import ModuleType
from typing import Any

from apps.hub_worker.market_components import InstalledComponent
from packages.kernel.decision_hub_kernel.application.product_release_provenance import (
    read_repository_file,
    resolve_product_receipt,
)
from packages.kernel.decision_hub_kernel.ports.component_registry import ComponentManifest

ARCHIVE_PREFIX = "packages/kernel/market_component_versions"
_LOCK = threading.RLock()
_MAX_FILES = 4096
_MAX_BYTES = 64 * 1024 * 1024
_REPOSITORY_ROOTS = ("packages", "apps", "tools", "tests", "infra")


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _json(raw: bytes) -> dict[str, Any]:
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("component_archive_json_invalid")
    return value


def _path(root: Path, relative: str) -> Path:
    parts = PurePosixPath(relative)
    if (
        not relative
        or parts.is_absolute()
        or parts.as_posix() != relative
        or any(part in ("", ".", "..") for part in relative.split("/"))
        or "\\" in relative
        or ":" in relative
    ):
        raise ValueError("component_archive_path_invalid")
    path = root
    for part in parts.parts:
        path = path / part
        if path.is_symlink() or (
            path.exists() and getattr(path.lstat(), "st_file_attributes", 0) & 0x400
        ):
            raise ValueError("component_archive_link_forbidden")
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("component_archive_path_invalid")
    return path


def _runtime() -> dict[str, str]:
    return {
        "python": sys.version,
        **{
            name: importlib.metadata.version(name)
            for name in ("pydantic", "pydantic-core", "SQLAlchemy", "PyYAML", "jsonschema")
        },
    }


def _receipt(root: Path, path: Path, expected: str) -> tuple[dict[str, Any], bytes]:
    raw = path.read_bytes()
    value = _json(raw)
    if value.get("schema") == "product-validated-builds.v1":
        # Frozen v1 receipts keep their original admission predicates and bytes.
        if (
            _sha(raw) != expected
            or Path(value["repository_root"]).resolve() != root.resolve()
            or not isinstance(value.get("sources"), dict)
        ):
            raise ValueError("component_archive_release_unbound")
        return value, raw
    if value.get("schema") != "product-validated-builds.v2":
        raise ValueError("component_archive_release_unbound")
    raw = read_repository_file(root, path.absolute().relative_to(root).as_posix())
    resolved = resolve_product_receipt(
        raw,
        expected_root=root,
        expected_sha256=expected,
        read_resource=lambda name: read_repository_file(root, name),
    )
    if resolved.value["schema"] != "product-validated-builds.v2":
        raise ValueError("component_archive_release_unbound")
    return dict(resolved.value), raw


def _binding_sources(
    root: Path, archive: Path, index: dict[str, Any], pins: Mapping[str, str]
) -> tuple[dict[str, str], dict[str, str]]:
    """Original receipt transport is read only inside this selected capsule."""
    relative = (archive / "source-binding.json").relative_to(root).as_posix()
    raw = read_repository_file(root, relative)
    if _sha(raw) != index.get("source_binding_sha256") or pins.get(relative) != _sha(raw):
        raise ValueError("component_archive_release_invalid")
    schema, mode = index.get("schema_version"), index.get("source_binding_mode")
    if schema not in {
        "market-component-retained-release.v1",
        "market-component-retained-release.v2",
    }:
        raise ValueError("component_archive_release_invalid")
    resources = index.get("source_binding_resources", {})
    if schema.endswith(".v1") and resources:
        raise ValueError("component_archive_source_binding_invalid")
    additions: dict[str, str] = {}
    if mode == "managed_candidate_inventory":
        if schema.endswith(".v2") or resources:
            raise ValueError("component_archive_source_binding_invalid")
        return _json(raw), additions
    if mode != "validated_prior_release":
        raise ValueError("component_archive_source_binding_invalid")
    resolved = resolve_product_receipt(
        raw,
        expected_root=root,
        expected_sha256=index["source_binding_sha256"],
        read_resource=lambda name: read_repository_file(archive / "root", name),
    )
    if (
        (resolved.value["schema"] == "product-validated-builds.v2") != schema.endswith(".v2")
        or type(resources) is not dict
        or resources != dict(resolved.transport)
    ):
        raise ValueError("component_archive_source_binding_invalid")
    for name, expected in resolved.transport.items():
        stored = _path(archive / "root", name)
        bound = stored.relative_to(root).as_posix()
        if pins.get(bound) != expected:
            raise ValueError("component_archive_transport_unbound")
        additions[bound] = expected
    if schema.endswith(".v2"):
        names = {*index["files"], *resolved.transport}
        if (
            len(names) > _MAX_FILES
            or len(raw) + sum(_path(archive / "root", name).stat().st_size for name in names)
            > _MAX_BYTES
        ):
            raise ValueError("component_archive_closure_limit")
    return dict(resolved.value["sources"]), additions


def _module_path(root: Path, name: str) -> str | None:
    base = name.replace(".", "/")
    for relative in (base + "/__init__.py", base + ".py"):
        if _path(root, relative).is_file():
            return relative
    return None


def _closure(root: Path, initial: set[str]) -> dict[str, bytes]:
    pending = list(initial)
    output: dict[str, bytes] = {}
    size = 0
    while pending:
        relative = pending.pop()
        if relative in output:
            continue
        raw = _path(root, relative).read_bytes()
        output[relative] = raw
        size += len(raw)
        if len(output) > _MAX_FILES or size > _MAX_BYTES:
            raise ValueError("component_archive_closure_limit")
        if not relative.endswith(".py"):
            continue
        name = relative.removesuffix(".py").replace("/", ".")
        package = (
            name.removesuffix(".__init__")
            if name.endswith(".__init__")
            else name.rpartition(".")[0]
        )
        imports: set[str] = set()
        for node in ast.walk(ast.parse(raw, filename=relative)):
            if isinstance(node, ast.Import):
                imports.update(item.name for item in node.names)
            elif isinstance(node, ast.ImportFrom):
                target = node.module or ""
                if node.level:
                    target = importlib.util.resolve_name("." * node.level + target, package)
                imports.add(target)
                imports.update(target + "." + item.name for item in node.names if item.name != "*")
            elif isinstance(node, ast.Call) and node.args:
                function = node.func
                dynamic_name = (
                    isinstance(function, ast.Name)
                    and function.id in ("import_module", "__import__")
                ) or (isinstance(function, ast.Attribute) and function.attr == "import_module")
                argument = node.args[0]
                if (
                    dynamic_name
                    and isinstance(argument, ast.Constant)
                    and isinstance(argument.value, str)
                ):
                    target = argument.value
                    if target.startswith("."):
                        target = importlib.util.resolve_name(target, package)
                    imports.add(target)
        imports.add(package)
        for imported in imports:
            if imported.split(".")[0] not in _REPOSITORY_ROOTS:
                continue
            # Include every package initializer, including its reexports.
            for count in range(1, len(imported.split(".")) + 1):
                module = ".".join(imported.split(".")[:count])
                found = _module_path(root, module)
                if found and found not in output:
                    pending.append(found)
    return output


def _reuse_component_archive(
    root: Path,
    receipt_path: Path,
    receipt_sha256: str,
    *,
    runtime: dict[str, str],
    files: dict[str, str],
    components: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Reuse only exact originals referenced by the authenticated prior owner.

    The current closure has already been checked against this preparation's
    actual source inventory. A global inventory change is not a component
    identity change. Old binding bytes remain immutable and fully rechecked.
    """
    receipt, _ = _receipt(root, receipt_path, receipt_sha256)
    evidence = receipt.get("evidence", {}).get("retained_market_components_v1")
    if evidence is None:
        return None
    references = evidence.get("archives") if isinstance(evidence, dict) else None
    if not isinstance(references, list) or len(references) > _MAX_FILES:
        raise ValueError("component_archive_reuse_evidence_invalid")
    seen: dict[str, str] = {}
    selected = None
    for reference in references:
        if not isinstance(reference, dict) or set(reference) != {"index_ref", "index_sha256"}:
            raise ValueError("component_archive_reuse_evidence_invalid")
        name, expected = reference["index_ref"], reference["index_sha256"]
        if name in seen:
            if seen[name] != expected:
                raise ValueError("component_archive_reuse_evidence_conflict")
            continue
        seen[name] = expected
        index_path = _path(root, name)
        if not index_path.is_relative_to(root / ARCHIVE_PREFIX) or index_path.name != "index.json":
            raise ValueError("component_archive_index_scope_invalid")
        raw_index = index_path.read_bytes()
        if _sha(raw_index) != expected or receipt["sources"].get(name) != expected:
            raise ValueError("component_archive_index_unbound")
        index = _json(raw_index)
        archive = index_path.parent
        binding_path = _path(archive, "source-binding.json")
        raw_binding = binding_path.read_bytes()
        if _sha(raw_binding) != index.get("source_binding_sha256") or receipt["sources"].get(
            binding_path.relative_to(root).as_posix()
        ) != _sha(raw_binding):
            raise ValueError("component_archive_release_invalid")
        mode = index.get("source_binding_mode")
        source_map, transport_additions = _binding_sources(root, archive, index, receipt["sources"])
        originals = index.get("files")
        if (
            index.get("schema_version")
            not in {"market-component-retained-release.v1", "market-component-retained-release.v2"}
            or not isinstance(originals, dict)
            or not originals
            or len(originals) > _MAX_FILES
        ):
            raise ValueError("component_archive_release_invalid")
        additions = {
            name: expected,
            binding_path.relative_to(root).as_posix(): _sha(raw_binding),
            **transport_additions,
        }
        for relative, digest in originals.items():
            stored = _path(archive / "root", relative)
            bound = stored.relative_to(root).as_posix()
            if receipt["sources"].get(bound) != digest or source_map.get(relative) != digest:
                raise ValueError("component_archive_original_unbound")
            additions[bound] = digest
        # Verification is uncached and reads every original, including content
        # behind an unchanged size/mtime. Bad old bytes cannot trigger fallback.
        _PrivateComponentRelease(archive / "root", originals, expected).verify()
        if (
            selected is None
            and index["runtime"] == runtime
            and originals == files
            and index["components"] == components
        ):
            selected = {
                "index_ref": name,
                "index_sha256": expected,
                "source_additions": additions,
                "preparation_only": True,
                "source_binding_mode": mode,
                "reused_existing_archive": True,
            }
    _receipt(root, receipt_path, receipt_sha256)
    return selected


def prepare_component_archive(
    *,
    repository_root: Path,
    prior_receipt_path: Path | None = None,
    prior_receipt_sha256: str | None = None,
    installed: tuple[InstalledComponent, ...],
    output_directory: Path,
    candidate_source_map_path: Path | None = None,
    candidate_source_map_sha256: str | None = None,
    reuse_receipt_path: Path | None = None,
    reuse_receipt_sha256: str | None = None,
) -> dict[str, Any]:
    """Called before source changes by managed release preparation.

    Return index ref/hash and all source-map additions. The result is only a
    candidate until those additions are included in a new standard build.
    """
    root = repository_root.resolve()
    output = output_directory.absolute()
    if (
        output.exists()
        or not output.resolve().is_relative_to(root / ARCHIVE_PREFIX)
        or not installed
    ):
        raise ValueError("component_archive_fresh_directory_required")
    _path(root, output.relative_to(root).as_posix())
    binding_transport: dict[str, str] = {}
    if prior_receipt_path is not None and prior_receipt_sha256 is not None:
        if candidate_source_map_path is not None or candidate_source_map_sha256 is not None:
            raise ValueError("component_archive_source_binding_ambiguous")
        binding, binding_raw = _receipt(root, prior_receipt_path, prior_receipt_sha256)
        source_map = binding["sources"]
        binding_mode = "validated_prior_release"
        binding_transport = dict(
            resolve_product_receipt(
                binding_raw,
                expected_root=root,
                expected_sha256=prior_receipt_sha256,
                read_resource=lambda name: read_repository_file(root, name),
            ).transport
        )
    elif candidate_source_map_path is not None and candidate_source_map_sha256 is not None:
        if prior_receipt_path is not None or prior_receipt_sha256 is not None:
            raise ValueError("component_archive_source_binding_ambiguous")
        binding_raw = candidate_source_map_path.read_bytes()
        if _sha(binding_raw) != candidate_source_map_sha256:
            raise ValueError("component_archive_candidate_inventory_changed")
        source_map = _json(binding_raw)
        binding_mode = "managed_candidate_inventory"
    else:
        raise ValueError("component_archive_source_binding_required")
    initial: set[str] = set()
    declarations = []
    for item in installed:
        manifest = item.manifest()
        files = {
            key: path.resolve().relative_to(root).as_posix()
            for key, path in item.code_files.items()
        }
        resources = {
            key: path.resolve().relative_to(root).as_posix()
            for key, path in item.resource_files.items()
        }
        initial.update((*files.values(), *resources.values()))
        module = (
            Path(inspect.getsourcefile(item.callback) or "")
            .resolve()
            .relative_to(root)
            .as_posix()
            .removesuffix(".py")
            .replace("/", ".")
        )
        if item.callback.__qualname__ != item.callback.__name__:
            raise ValueError("component_archive_top_level_function_required")
        declarations.append(
            {
                "manifest": manifest.model_dump(mode="json"),
                "module": module,
                "function": item.callback.__name__,
                "code_files": files,
                "resource_files": resources,
            }
        )
    payloads = _closure(root, initial)
    for name, raw in payloads.items():
        if source_map.get(name) != _sha(raw):
            raise ValueError("component_archive_original_not_in_source_binding:" + name)
    if (reuse_receipt_path is None) != (reuse_receipt_sha256 is None):
        raise ValueError("component_archive_reuse_receipt_required")
    runtime = _runtime()
    files = {name: _sha(raw) for name, raw in payloads.items()}
    transport_payloads = {name: read_repository_file(root, name) for name in binding_transport}
    if any(_sha(raw) != binding_transport[name] for name, raw in transport_payloads.items()):
        raise ValueError("component_archive_transport_changed")
    if set(transport_payloads) & set(payloads):
        raise ValueError("component_archive_transport_conflict")
    if binding_transport and (
        len(payloads) + len(transport_payloads) > _MAX_FILES
        or len(binding_raw)
        + sum(map(len, payloads.values()))
        + sum(map(len, transport_payloads.values()))
        > _MAX_BYTES
    ):
        raise ValueError("component_archive_closure_limit")
    if reuse_receipt_path is not None and reuse_receipt_sha256 is not None:
        reused = _reuse_component_archive(
            root,
            reuse_receipt_path,
            reuse_receipt_sha256,
            runtime=runtime,
            files=files,
            components=declarations,
        )
        if reused is not None:
            return reused
    if binding_mode == "managed_candidate_inventory":
        # The full candidate inventory SHA and every closure member were checked
        # above. Bind only this archive's exact closure; final release admission
        # still authenticates the index, this binding, and every stored original.
        binding_raw = (json.dumps(files, sort_keys=True, separators=(",", ":")) + "\n").encode()
    index = {
        "schema_version": "market-component-retained-release.v1",
        "runtime": runtime,
        "source_binding_mode": binding_mode,
        "source_binding_sha256": _sha(binding_raw),
        "files": files,
        "components": declarations,
    }
    if binding_transport:
        index.update(
            {
                "schema_version": "market-component-retained-release.v2",
                "source_binding_resources": binding_transport,
            }
        )
    raw_index = (json.dumps(index, sort_keys=True, separators=(",", ":")) + "\n").encode()
    output.mkdir(parents=True)
    additions: dict[str, str] = {}
    for name, raw in {
        "index.json": raw_index,
        "source-binding.json": binding_raw,
        **{"root/" + name: raw for name, raw in payloads.items()},
        **{"root/" + name: raw for name, raw in transport_payloads.items()},
    }.items():
        path = _path(output, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(raw)
        additions[path.relative_to(root).as_posix()] = _sha(raw)
    return {
        "index_ref": (output / "index.json").relative_to(root).as_posix(),
        "index_sha256": _sha(raw_index),
        "source_additions": additions,
        "preparation_only": True,
        "source_binding_mode": binding_mode,
        "reused_existing_archive": False,
    }


class _PrivateComponentRelease:
    def __init__(
        self,
        root: Path,
        files: Mapping[str, str],
        identity: str,
        verify_binding: Callable[[], None] | None = None,
    ):
        self.root, self.files = root, dict(files)
        self.verify_binding = verify_binding
        self.prefix = "_decision_hub_component_" + identity
        self.modules: dict[str, ModuleType] = {}
        self.paths = {}
        self.importlib = ModuleType("importlib")
        self.importlib.__dict__.update(vars(importlib))
        self.importlib.__dict__["import_module"] = self._import_module
        for path in files:
            if path.endswith(".py"):
                name = path.removesuffix(".py").replace("/", ".").removesuffix(".__init__")
                self.paths[name] = path

    def verify(self) -> None:
        if self.verify_binding is not None:
            self.verify_binding()
        total = 0
        for name, expected in self.files.items():
            raw = _path(self.root, name).read_bytes()
            total += len(raw)
            if total > _MAX_BYTES:
                raise ValueError("component_archive_closure_limit")
            if _sha(raw) != expected:
                raise ValueError("component_archive_original_changed")

    def _import(
        self,
        name: str,
        globals: dict[str, Any] | None = None,
        locals: dict[str, Any] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> Any:
        if level:
            package = (globals or {}).get("_component_original_package")
            if not isinstance(package, str):
                raise ValueError("component_archive_relative_package_missing")
            name = importlib.util.resolve_name("." * level + name, package)
        if name.split(".")[0] in _REPOSITORY_ROOTS:
            module = self.load(name)
            for child in fromlist:
                if (
                    child != "*"
                    and not hasattr(module, child)
                    and (
                        name + "." + child in self.paths
                        or any(key.startswith(name + "." + child + ".") for key in self.paths)
                    )
                ):
                    self.load(name + "." + child)
            return module if fromlist else self.load(name.split(".")[0])
        if level:
            raise ValueError("component_archive_repository_import_missing")
        imported = builtins.__import__(name, globals, locals, fromlist, 0)
        if name == "importlib" or (name.startswith("importlib.") and not fromlist):
            # Keep external importlib APIs, but never resolve repository dynamic
            # imports against the current release's global sys.modules/sys.path.
            self.importlib.__dict__.update(
                {key: value for key, value in vars(importlib).items() if key != "import_module"}
            )
            return self.importlib
        return imported

    def _import_module(self, name: str, package: str | None = None) -> ModuleType:
        if name.startswith("."):
            if package is None:
                raise ValueError("component_archive_relative_package_missing")
            logical = package.removeprefix(self.prefix + ".")
            name = importlib.util.resolve_name(name, logical)
        if name.split(".")[0] in _REPOSITORY_ROOTS:
            return self.load(name)
        return importlib.import_module(name)

    def load(self, name: str) -> ModuleType:
        if name in self.modules:
            return self.modules[name]
        relative = self.paths.get(name)
        if relative is None and not any(key.startswith(name + ".") for key in self.paths):
            raise ValueError("component_archive_repository_import_missing:" + name)
        parent, _, child = name.rpartition(".")
        parent_module = self.load(parent) if parent else None
        if name in self.modules:
            return self.modules[name]
        alias = self.prefix + "." + name
        if alias in sys.modules:
            raise ValueError("component_archive_namespace_conflict")
        module = ModuleType(alias)
        self.modules[name] = module
        sys.modules[alias] = module
        if parent_module is not None:
            setattr(parent_module, child, module)
        package = name if relative is None or relative.endswith("/__init__.py") else parent
        module.__package__ = self.prefix + "." + package
        module.__dict__["_component_original_package"] = package
        module.__dict__["__builtins__"] = {**vars(builtins), "__import__": self._import}
        if package == name:
            module.__path__ = []
        if relative:
            module.__file__ = str(_path(self.root, relative))
            raw = Path(module.__file__).read_bytes()
            if _sha(raw) != self.files[relative]:
                raise ValueError("component_archive_original_changed")
            exec(compile(raw, module.__file__, "exec"), module.__dict__)
        return module

    def discard(self) -> None:
        for name, module in self.modules.items():
            alias = self.prefix + "." + name
            if sys.modules.get(alias) is module:
                sys.modules.pop(alias)


@dataclass(frozen=True)
class RetainedInstalledComponent(InstalledComponent):
    retained_release: _PrivateComponentRelease | None = None

    def verify(self):
        super().verify()
        if self.retained_release is None:
            raise ValueError("component_archive_release_missing")
        self.retained_release.verify()


def load_component_versions(
    *,
    repository_root: Path,
    validated_receipt_path: Path,
    validated_receipt_sha256: str,
    index_ref: str,
    index_sha256: str,
    already_installed: tuple[InstalledComponent, ...] = (),
) -> tuple[InstalledComponent, ...]:
    """Trusted composition supplies refs. Registry/client text never picks code.

    Caller injects the returned functions into BOTH the actual consumer and its
    ProductionComponentOriginalReader. This grants installation, never approval.
    """
    root = repository_root.resolve()
    receipt, _ = _receipt(root, validated_receipt_path, validated_receipt_sha256)
    index_path = _path(root, index_ref)
    if not index_path.is_relative_to(root / ARCHIVE_PREFIX):
        raise ValueError("component_archive_index_scope_invalid")
    raw = index_path.read_bytes()
    if _sha(raw) != index_sha256 or receipt["sources"].get(index_ref) != index_sha256:
        raise ValueError("component_archive_index_unbound")
    index = _json(raw)
    archive = index_path.parent
    binding_raw = _path(archive, "source-binding.json").read_bytes()
    source_map, _ = _binding_sources(root, archive, index, receipt["sources"])
    if (
        index["schema_version"]
        not in {"market-component-retained-release.v1", "market-component-retained-release.v2"}
        or index["runtime"] != _runtime()
        or _sha(binding_raw) != index["source_binding_sha256"]
        or receipt["sources"].get((archive / "source-binding.json").relative_to(root).as_posix())
        != _sha(binding_raw)
        or len(index["files"]) > _MAX_FILES
    ):
        raise ValueError("component_archive_release_invalid")
    for name, expected in index["files"].items():
        stored = _path(archive / "root", name)
        if (
            receipt["sources"].get(stored.relative_to(root).as_posix()) != expected
            or source_map.get(name) != expected
        ):
            raise ValueError("component_archive_original_unbound")

    def verify_binding() -> None:
        current, _ = _receipt(root, validated_receipt_path, validated_receipt_sha256)
        if (
            _sha(index_path.read_bytes()) != index_sha256
            or current["sources"].get(index_ref) != index_sha256
        ):
            raise ValueError("component_archive_index_unbound")
        _binding_sources(root, archive, index, current["sources"])

    private = _PrivateComponentRelease(
        archive / "root",
        index["files"],
        index_sha256,
        verify_binding if index["schema_version"].endswith(".v2") else None,
    )
    existing = {item.version_id: item for item in already_installed}
    if len(existing) != len(already_installed):
        raise ValueError("component_archive_current_version_duplicate")
    with _LOCK:
        private.verify()
        try:
            output = []
            declared = set()
            for item in index["components"]:
                manifest = ComponentManifest.model_validate(item["manifest"])
                if manifest.version_id in declared:
                    raise ValueError("component_archive_version_duplicate")
                declared.add(manifest.version_id)
                current = existing.get(manifest.version_id)
                if current is not None:
                    if current.manifest(parent_version_id=manifest.parent_version_id) != manifest:
                        raise ValueError("component_archive_current_manifest_mismatch")
                    # Originals were authenticated above. Matching current code
                    # needs no duplicate module/ORM namespace at startup.
                    continue
                callback = getattr(private.load(item["module"]), item["function"])
                installed = RetainedInstalledComponent(
                    kind=manifest.kind,
                    component_ref=manifest.component_ref,
                    scope_id=manifest.scope_id,
                    version_id=manifest.version_id,
                    input_contract=manifest.input_contract,
                    output_contract=manifest.output_contract,
                    callback=callback,
                    code_files={
                        key: _path(private.root, name) for key, name in item["code_files"].items()
                    },
                    resource_files={
                        key: _path(private.root, name)
                        for key, name in item["resource_files"].items()
                    },
                    parameters=manifest.parameters,
                    retained_release=private,
                )
                if installed.manifest(parent_version_id=manifest.parent_version_id) != manifest:
                    raise ValueError("component_archive_manifest_identity_changed")
                output.append(installed)
            if len({item.version_id for item in output}) != len(output):
                raise ValueError("component_archive_version_duplicate")
            return tuple(output)
        except BaseException:
            private.discard()
            raise

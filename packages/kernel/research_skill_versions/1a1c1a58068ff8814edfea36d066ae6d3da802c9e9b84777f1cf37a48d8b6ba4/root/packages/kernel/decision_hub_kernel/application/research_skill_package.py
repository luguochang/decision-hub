"""Immutable packages backed by canonical contracts and authenticated resources."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from packages.kernel.decision_hub_kernel.application.component_registry import (
    ComponentRegistry,
    _read,
)
from packages.kernel.decision_hub_kernel.persistence.db import as_utc, utcnow
from packages.kernel.decision_hub_kernel.persistence.market_case_records import (
    MarketComponentVersionRecord,
)
from packages.kernel.decision_hub_kernel.ports.component_registry import ComponentManifest
from packages.provider_adapters.research.raw_capture import RawBodyArchive
from sqlalchemy.orm import Session

from packages.kernel.decision_hub_kernel.ports.market_path import aware_utc, canonical_hash

from . import research_skill_compiler as compiler


def encoded(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def sha(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def decoded(body: bytes | str) -> Any:
    return compiler._decode(body)


class SkillContracts:
    """Use the installed canonical validator behind generated DTOs."""

    def __init__(self, *unused):
        pass

    def check(self, name: str, body: dict) -> None:
        from packages.contracts_py.decision_hub_contracts.research_skill_contract_base import (
            validate_shape,
            validators,
        )

        key = "research_skill_" + name
        # Canonical titles, including generated aliases, are deterministic.
        title = "".join(part.capitalize() for part in key.split("_"))
        validate_shape(title if title in validators() else key, body)
        encoded(body)


# Internal compatibility for callers constructing the previously staged class.
DraftContracts = SkillContracts


@dataclass(frozen=True)
class InstalledSkillCompiler:
    """Composition-owned closure, never supplied in a model/API request."""

    files: tuple[tuple[str, Path], ...]
    code_hash: str

    @classmethod
    def capture(cls, files: dict[str, Path]):
        if not files or Path(compiler.__file__).resolve() not in {
            path.resolve() for path in files.values()
        }:
            raise ValueError("skill_compiler_source_missing")
        frozen = tuple(sorted((key, path.resolve()) for key, path in files.items()))
        return cls(frozen, canonical_hash({key: sha(path.read_bytes()) for key, path in frozen}))

    def verify(self):
        if (
            canonical_hash({key: sha(path.read_bytes()) for key, path in self.files})
            != self.code_hash
        ):
            raise ValueError("skill_installed_closure_changed")


@dataclass(frozen=True)
class PreparedPackage:
    manifest: ComponentManifest
    resources: tuple[compiler.ResourceBytes, ...]

    @property
    def version(self) -> dict:
        return decoded(encoded(self.manifest.parameters["skill"]))


class SkillPackageService:
    def __init__(
        self,
        registry: ComponentRegistry,
        archive: RawBodyArchive,
        installed: InstalledSkillCompiler,
        contracts: DraftContracts,
        *,
        clock=utcnow,
    ):
        self.registry, self.archive = registry, archive
        self.installed, self.contracts, self.clock = installed, contracts, clock

    @staticmethod
    def _scan(root: Path) -> tuple[compiler.ResourceBytes, ...]:
        if root.is_symlink() or root.is_junction():
            raise ValueError("skill_package_link_forbidden")
        root = root.resolve(strict=True)
        if not root.is_dir():
            raise ValueError("skill_package_directory_required")
        result, names, total = [], set(), 0
        for path in sorted(root.rglob("*")):
            if path.is_symlink() or path.is_junction():
                raise ValueError("skill_package_link_forbidden")
            if path.is_dir():
                continue
            relative = path.relative_to(root).as_posix()
            if (
                not path.resolve().is_relative_to(root)
                or not path.is_file()
                or PurePosixPath(relative).suffix not in {".md", ".json"}
                or ":" in relative
                or "\\" in relative
                or relative.casefold() in names
            ):
                raise ValueError("skill_package_path_invalid")
            size = path.stat().st_size
            if not 0 < size <= 1_000_000 or len(result) >= 128 or total + size > 8_000_000:
                raise ValueError("skill_package_size_invalid")
            body = path.read_bytes()
            if len(body) != size:
                raise ValueError("skill_package_changed_during_read")
            names.add(relative.casefold())
            total += size
            result.append(compiler.ResourceBytes(relative, body, sha(body)))
        if not {"SKILL.md", "evaluation-protocol.json"}.issubset({x.path for x in result}):
            raise ValueError("skill_package_required_file_missing")
        return tuple(result)

    def prepare(
        self,
        author_root: Path,
        *,
        skill_id: str,
        version_id: str,
        context: compiler.CompilerContext,
        kind: str = "method_workflow",
        input_contract: str | None = None,
        output_contract: str | None = None,
        parent_version_id: str | None = None,
    ) -> PreparedPackage:
        self.installed.verify()
        if context.compiler_hash != self.installed.code_hash:
            raise ValueError("skill_installed_compiler_mismatch")
        resources = self._scan(author_root)
        files = {item.path: item for item in resources}
        if not files["SKILL.md"].body.decode("utf-8").strip():
            raise ValueError("skill_method_empty")
        if not isinstance(decoded(files["evaluation-protocol.json"].body), dict):
            raise ValueError("skill_evaluation_protocol_invalid")
        workflow = None
        if kind == "method_workflow":
            plan = compiler.compile_skill_package(resources, context).read()
            workflow = plan["workflow"]
            if (
                workflow["schema_version"] != "skill-workflow.v2"
                or workflow["version_id"] != version_id
            ):
                raise ValueError("skill_workflow_version_mismatch")
            input_contract, output_contract = workflow["input_schema"], workflow["output_schema"]
        elif kind != "method_readonly" or "workflow.json" in files:
            raise ValueError("skill_package_kind_invalid")
        schemas = []
        for item in context.schemas:
            resource = files.get(item.resource_path)
            if resource is None or resource.sha256 != item.schema_hash:
                raise ValueError("skill_schema_resource_mismatch")
            schemas.append(
                {
                    "contract_ref": item.reference,
                    "resource_path": item.resource_path,
                    "schema_hash": item.schema_hash,
                }
            )
        if not {input_contract, output_contract}.issubset({x["contract_ref"] for x in schemas}):
            raise ValueError("skill_package_contract_missing")
        refs = []
        for item in resources:
            digest, ref = self.archive.put(item.body)
            refs.append(
                {
                    "path": item.path,
                    "body_ref": ref,
                    "body_hash": digest,
                    "size_bytes": len(item.body),
                }
            )
        required = set(workflow["required_capabilities"]) if workflow else set()
        capabilities = tuple(
            item for item in context.capabilities if item.capability_id in required
        )
        surfaces = []
        for item in capabilities:
            surface = compiler._surface_value(item)
            route_hash, route_ref = self.archive.put(encoded(surface["controlled_routes"]))
            surfaces.append(
                {
                    key: surface[key]
                    for key in (
                        "capability_id",
                        "protocol_ref",
                        "adapter_version",
                        "adapter_code_hash",
                        "input_schema_hash",
                        "output_schema_hash",
                        "scope_ids",
                    )
                }
                | {
                    "unit_bindings_hash": canonical_hash(surface["unit_bindings"]),
                    "controlled_routes_hash": route_hash,
                    "controlled_routes_ref": route_ref,
                }
            )
        first_known = aware_utc(self.clock())
        with self.registry.database.session() as session:
            old = session.get(MarketComponentVersionRecord, version_id)
            if old is not None:
                old_manifest = _read(old, ComponentManifest)
                first_known = datetime.fromisoformat(
                    old_manifest.parameters["skill"]["first_known_at"]
                )
        version = {
            "schema_version": "research-skill-version.v1",
            "skill_id": skill_id,
            "version_id": version_id,
            "parent_version_id": parent_version_id,
            "kind": kind,
            "domain_pack_ref": "crypto_macro.v1",
            "scope_ids": [context.scope_id],
            "horizon": "24h",
            "files": refs,
            "input_contract": input_contract,
            "output_contract": output_contract,
            "schema_bindings": schemas,
            "workflow_hash": files["workflow.json"].sha256 if workflow else None,
            "compiler_hash": self.installed.code_hash,
            "required_capability_surface_hash": compiler.required_capability_surface_hash(
                capabilities
            ),
            "capability_surfaces": surfaces,
            "component_manifest_refs": [
                {"version_id": x.manifest.version_id, "payload_hash": x.manifest.payload_hash}
                for x in context.components
            ]
            if workflow
            else [],
            "evaluation_protocol_hash": files["evaluation-protocol.json"].sha256,
            "origin_refs": [],
            "first_known_at": first_known.isoformat(),
        }
        self.contracts.check("version_v1", version)
        manifest = ComponentManifest(
            version_id=version_id,
            kind="research_recipe",
            component_ref="skill:" + skill_id,
            scope_id=context.scope_id,
            parent_version_id=parent_version_id,
            input_contract=input_contract,
            output_contract=output_contract,
            code_hash=self.installed.code_hash,
            resource_hash=canonical_hash({"files": refs}),
            parameters={"skill": version},
        )
        return PreparedPackage(manifest, resources)

    def register(self, prepared: PreparedPackage) -> ComponentManifest:
        self._verify(prepared.manifest)
        return self.registry.register(prepared.manifest)

    def _verify(self, manifest: ComponentManifest) -> PreparedPackage:
        self.installed.verify()
        version = manifest.parameters.get("skill")
        self.contracts.check("version_v1", version)
        if (
            set(manifest.parameters) != {"skill"}
            or manifest.kind != "research_recipe"
            or manifest.component_ref != "skill:" + version["skill_id"]
            or manifest.version_id != version["version_id"]
            or manifest.parent_version_id != version["parent_version_id"]
            or version["scope_ids"] != [manifest.scope_id]
            or manifest.input_contract != version["input_contract"]
            or manifest.output_contract != version["output_contract"]
            or manifest.code_hash != self.installed.code_hash
            or version["compiler_hash"] != self.installed.code_hash
            or manifest.resource_hash != canonical_hash({"files": version["files"]})
        ):
            raise ValueError("skill_package_manifest_mismatch")
        resources, names, total = [], set(), 0
        for item in version["files"]:
            body = self.archive.read(item["body_ref"])
            if (
                sha(body) != item["body_hash"]
                or len(body) != item["size_bytes"]
                or item["path"].casefold() in names
                or total + len(body) > 8_000_000
            ):
                raise ValueError("skill_package_resource_changed")
            names.add(item["path"].casefold())
            total += len(body)
            resources.append(compiler.ResourceBytes(item["path"], body, sha(body)))
        files = {item.path: item for item in resources}
        if (
            not {"SKILL.md", "evaluation-protocol.json"}.issubset(files)
            or files["evaluation-protocol.json"].sha256 != version["evaluation_protocol_hash"]
            or (
                version["kind"] == "method_workflow"
                and (
                    "workflow.json" not in files
                    or files["workflow.json"].sha256 != version["workflow_hash"]
                )
            )
        ):
            raise ValueError("skill_package_dependency_changed")
        for item in version["schema_bindings"]:
            if (
                item["resource_path"] not in files
                or files[item["resource_path"]].sha256 != item["schema_hash"]
            ):
                raise ValueError("skill_schema_resource_mismatch")
        for item in version["capability_surfaces"]:
            if (
                sha(self.archive.read(item["controlled_routes_ref"]))
                != item["controlled_routes_hash"]
            ):
                raise ValueError("skill_route_resource_changed")
        return PreparedPackage(manifest, tuple(resources))

    def read_in_session(self, session: Session, version_id: str) -> PreparedPackage:
        row = session.get(MarketComponentVersionRecord, version_id, populate_existing=True)
        manifest = _read(row, ComponentManifest)
        value = self._verify(manifest)
        if aware_utc(datetime.fromisoformat(value.version["first_known_at"])) > as_utc(
            row.created_at
        ):
            raise ValueError("skill_package_registration_time_invalid")
        return value

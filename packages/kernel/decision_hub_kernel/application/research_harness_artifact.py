"""Immutable, data-only prepared Harness artifacts; no deployment or execution grant.

The caller must supply its trusted installed baseline. This module does not
discover live Host readiness, approve a candidate, or change any Run receipt.
The first version permits instruction edits and narrower capabilities/budgets;
evidence, gates, scoring, role tools, Pack identity and runtime stay protected.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

from packages.contracts_py.decision_hub_contracts.generated.research_binding import (
    DomainPackManifest,
    ResearchPackDefinitionV2,
)
from packages.kernel.decision_hub_kernel.application.research_binding import (
    PreparedResearchPolicyRefs,
    _validate_pack_definition,  # pyright: ignore[reportPrivateUsage]
    load_prepared_pack_definition,
)
from packages.kernel.decision_hub_kernel.application.research_method import canonical_recipe
from packages.kernel.decision_hub_kernel.application.research_policy_resources import (
    MAX_RESOURCE_BYTES,
    MAX_RESOURCES,
    MAX_TOTAL_BYTES,
    canonical_hash,
    normalized_relative_ref,
    parse_resource_bytes,
)

MAX_ARTIFACT_BYTES = 1_048_576
Hash = Annotated[StrictStr, Field(pattern=r"^[a-f0-9]{64}$")]
Identifier = Annotated[StrictStr, Field(min_length=1, max_length=128)]


class HarnessArtifactError(ValueError):
    def __init__(self, code: str) -> None:
        self.error_code = code
        super().__init__(code)


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class HarnessInstalledRuntimeV1(_Closed):
    runtime_id: Identifier
    runtime_version: Identifier
    plugin_build_hash: Hash
    runtime_source_hash: Hash
    renderer_source_hash: Hash
    compiler_source_hash: Hash
    agent_preset: Literal["decision-research"]
    permission_ref: Literal["decision-hub://permissions/research-only"]


class HarnessResourceV1(_Closed):
    relative_ref: Annotated[StrictStr, Field(min_length=1, max_length=512)]
    raw_utf8: StrictStr
    raw_sha256: Hash
    byte_count: Annotated[StrictInt, Field(ge=1, le=MAX_RESOURCE_BYTES)]


class PreparedHarnessManifestV1(_Closed):
    schema_version: Literal["research-harness-manifest.v1"]
    execution_scope: Literal["research_harness"]
    harness_kind: Literal["prepared-policy-bundle.v1"]
    candidate_id: Identifier
    version: Identifier
    domain_pack_ref: Identifier
    installed_runtime_binding: HarnessInstalledRuntimeV1
    installed_pack_definition_hash: Hash
    prepared_pack_definition: ResearchPackDefinitionV2
    resources: Annotated[tuple[HarnessResourceV1, ...], Field(max_length=MAX_RESOURCES)]


class HarnessArtifactIndexV1(_Closed):
    schema_version: Literal["research-harness-artifact-index.v1"]
    artifact_bytes_sha256: Hash
    manifest_hash: Hash
    definition_hash: Hash

    @property
    def content_address(self) -> str:
        return f"{self.artifact_bytes_sha256}.json"


@dataclass(frozen=True)
class InstalledHarnessBaseline:
    """Owned snapshot injected by future composition, never candidate authority."""

    definition_json: bytes
    runtime_json: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.definition_json, bytes) or not isinstance(self.runtime_json, bytes):
            raise HarnessArtifactError("harness_baseline_bytes_required")

    @property
    def definition(self) -> ResearchPackDefinitionV2:
        definition = ResearchPackDefinitionV2.model_validate_json(self.definition_json)
        _validate_pack_definition(definition)
        return definition

    @property
    def runtime(self) -> HarnessInstalledRuntimeV1:
        return HarnessInstalledRuntimeV1.model_validate_json(self.runtime_json)


@dataclass(frozen=True)
class ValidatedHarnessArtifact:
    raw_bytes: bytes
    index: HarnessArtifactIndexV1

    def __post_init__(self) -> None:
        if not isinstance(self.raw_bytes, bytes):
            raise HarnessArtifactError("harness_artifact_bytes_required")

    @property
    def definition(self) -> ResearchPackDefinitionV2:
        # Each consumer gets a new typed copy; mutation cannot alter saved bytes.
        return PreparedHarnessManifestV1.model_validate_json(
            self.raw_bytes
        ).prepared_pack_definition


def capture_installed_harness_baseline(
    pack_root: Path,
    refs: PreparedResearchPolicyRefs,
    *,
    allowed_capabilities: tuple[str, ...],
    execution_mode: Literal["live", "replay"],
    runtime: HarnessInstalledRuntimeV1,
) -> InstalledHarnessBaseline:
    definition = load_prepared_pack_definition(
        pack_root, refs, allowed_capabilities=allowed_capabilities, execution_mode=execution_mode
    )
    runtime = HarnessInstalledRuntimeV1.model_validate(runtime.model_dump(mode="json"))
    return InstalledHarnessBaseline(
        canonical_recipe(definition.model_dump(mode="json")).encode("utf-8"),
        canonical_recipe(runtime.model_dump(mode="json")).encode("utf-8"),
    )


def _expected_resources(definition: ResearchPackDefinitionV2) -> dict[str, bytes]:
    resources = {
        resource.relative_ref: resource.raw_utf8.encode("utf-8")
        for resource in (
            definition.evidence_policy_resource,
            definition.gate_policy_resource,
            definition.rubric_policy_resource,
            *(role.profile_resource for role in definition.role_rubric_bindings),
        )
    }
    for reference, file in definition.research_policy.files.items():
        raw = file.content.encode("utf-8")
        if reference in resources and resources[reference] != raw:
            raise HarnessArtifactError("harness_resource_identity_conflict")
        resources[reference] = raw
    return resources


def _validate_manifest(
    manifest: PreparedHarnessManifestV1, baseline: InstalledHarnessBaseline
) -> None:
    candidate = manifest.prepared_pack_definition
    installed = baseline.definition
    _validate_pack_definition(candidate)
    if manifest.installed_runtime_binding != baseline.runtime:
        raise HarnessArtifactError("harness_installed_runtime_mismatch")
    installed_json = installed.model_dump(mode="json")
    if (
        manifest.installed_pack_definition_hash != canonical_hash(installed_json)
        or manifest.domain_pack_ref
        != f"{installed.manifest.pack_id}.v{installed.manifest.version.split('.')[0]}"
    ):
        raise HarnessArtifactError("harness_installed_pack_mismatch")
    candidate_json = candidate.model_dump(mode="json")
    original_manifest = installed_json["manifest"]
    proposed_manifest = candidate_json["manifest"]
    for key, value in original_manifest.items():
        if key not in ("capability_refs", "execution_budget") and proposed_manifest[key] != value:
            raise HarnessArtifactError("harness_protected_pack_changed")
    for key, ceiling in original_manifest["execution_budget"].items():
        if proposed_manifest["execution_budget"][key] > ceiling:
            raise HarnessArtifactError("harness_budget_expanded")
    if (
        len(candidate.allowed_capabilities) != len(set(candidate.allowed_capabilities))
        or len(candidate.manifest.capability_refs) != len(set(candidate.manifest.capability_refs))
        or not set(candidate.allowed_capabilities).issubset(installed.allowed_capabilities)
        or not set(candidate.manifest.capability_refs).issubset(installed.manifest.capability_refs)
    ):
        raise HarnessArtifactError("harness_capabilities_expanded")
    for key in (
        "execution_mode",
        "evidence_policy",
        "evidence_policy_resource",
        "gate_policy_resource",
        "rubric_policy_resource",
        "role_rubric_bindings",
    ):
        if candidate_json[key] != installed_json[key]:
            raise HarnessArtifactError("harness_protected_policy_changed")
    for key in ("schema_version", "doctrine_file", "manager_ref", "roles"):
        if candidate_json["research_policy"][key] != installed_json["research_policy"][key]:
            raise HarnessArtifactError("harness_protected_role_changed")
    expected = _expected_resources(candidate)
    observed: dict[str, bytes] = {}
    for resource in manifest.resources:
        reference = resource.relative_ref
        raw = resource.raw_utf8.encode("utf-8")
        if (
            normalized_relative_ref(reference) != reference
            or reference in observed
            or len(raw) != resource.byte_count
            or hashlib.sha256(raw).hexdigest() != resource.raw_sha256
        ):
            raise HarnessArtifactError("harness_resource_integrity_invalid")
        observed[reference] = raw
    manifest_raw = observed.pop("pack.yaml", None)
    if manifest_raw is None:
        raise HarnessArtifactError("harness_manifest_resource_missing")
    original = DomainPackManifest.model_validate(parse_resource_bytes(manifest_raw))
    if original != candidate.manifest or observed != expected:
        raise HarnessArtifactError("harness_resource_closure_mismatch")
    if sum(resource.byte_count for resource in manifest.resources) > MAX_TOTAL_BYTES:
        raise HarnessArtifactError("harness_resources_exceeded")


def _index(raw: bytes, manifest: PreparedHarnessManifestV1) -> HarnessArtifactIndexV1:
    return HarnessArtifactIndexV1(
        schema_version="research-harness-artifact-index.v1",
        artifact_bytes_sha256=hashlib.sha256(raw).hexdigest(),
        manifest_hash=canonical_hash(manifest.model_dump(mode="json")),
        definition_hash=canonical_hash(manifest.prepared_pack_definition.model_dump(mode="json")),
    )


def build_prepared_harness_artifact(
    candidate_root: Path,
    refs: PreparedResearchPolicyRefs,
    *,
    candidate_id: str,
    version: str,
    allowed_capabilities: tuple[str, ...],
    baseline: InstalledHarnessBaseline,
) -> ValidatedHarnessArtifact:
    definition = load_prepared_pack_definition(
        candidate_root,
        refs,
        allowed_capabilities=allowed_capabilities,
        execution_mode=baseline.definition.execution_mode,
    )
    root = candidate_root.resolve(strict=True)
    path = (root / "pack.yaml").resolve(strict=True)
    if not path.is_relative_to(root) or not path.is_file():
        raise HarnessArtifactError("harness_resource_outside_root")
    with path.open("rb") as stream:
        manifest_raw = stream.read(MAX_RESOURCE_BYTES + 1)
    if (root / "pack.yaml").resolve(strict=True) != path:
        raise HarnessArtifactError("harness_resource_path_changed")
    resources = {"pack.yaml": manifest_raw, **_expected_resources(definition)}
    manifest = PreparedHarnessManifestV1(
        schema_version="research-harness-manifest.v1",
        execution_scope="research_harness",
        harness_kind="prepared-policy-bundle.v1",
        candidate_id=candidate_id,
        version=version,
        domain_pack_ref=(
            f"{baseline.definition.manifest.pack_id}.v{baseline.definition.manifest.version.split('.')[0]}"
        ),
        installed_runtime_binding=baseline.runtime,
        installed_pack_definition_hash=canonical_hash(baseline.definition.model_dump(mode="json")),
        prepared_pack_definition=definition,
        resources=tuple(
            HarnessResourceV1(
                relative_ref=ref,
                raw_utf8=raw.decode("utf-8"),
                raw_sha256=hashlib.sha256(raw).hexdigest(),
                byte_count=len(raw),
            )
            for ref, raw in sorted(resources.items())
        ),
    )
    _validate_manifest(manifest, baseline)
    raw = canonical_recipe(manifest.model_dump(mode="json")).encode("utf-8")
    if len(raw) > MAX_ARTIFACT_BYTES:
        raise HarnessArtifactError("harness_artifact_size_invalid")
    return ValidatedHarnessArtifact(raw, _index(raw, manifest))


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise HarnessArtifactError("harness_duplicate_json_key")
        result[key] = value
    return result


def validate_harness_artifact_bytes(
    raw: bytes, index: HarnessArtifactIndexV1, *, baseline: InstalledHarnessBaseline
) -> ValidatedHarnessArtifact:
    index = HarnessArtifactIndexV1.model_validate(index.model_dump(mode="json"))
    if not isinstance(raw, bytes):
        raise HarnessArtifactError("harness_artifact_bytes_required")
    if not raw or len(raw) > MAX_ARTIFACT_BYTES:
        raise HarnessArtifactError("harness_artifact_size_invalid")
    if hashlib.sha256(raw).hexdigest() != index.artifact_bytes_sha256:
        raise HarnessArtifactError("harness_artifact_bytes_mismatch")
    value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    # JSON-mode preserves the strict tuple contract without coercing scalars.
    manifest = PreparedHarnessManifestV1.model_validate_json(canonical_recipe(value))
    _validate_manifest(manifest, baseline)
    if _index(raw, manifest) != index:
        raise HarnessArtifactError("harness_artifact_index_mismatch")
    return ValidatedHarnessArtifact(raw, index)


def load_harness_artifact(
    archive_root: Path, index: HarnessArtifactIndexV1, *, baseline: InstalledHarnessBaseline
) -> ValidatedHarnessArtifact:
    index = HarnessArtifactIndexV1.model_validate(index.model_dump(mode="json"))
    root = archive_root.resolve(strict=True)
    path = (root / index.content_address).resolve(strict=True)
    if not root.is_dir() or not path.is_relative_to(root) or not path.is_file():
        raise HarnessArtifactError("harness_archive_path_invalid")
    with path.open("rb") as stream:
        raw = stream.read(MAX_ARTIFACT_BYTES + 1)
    if (root / index.content_address).resolve(strict=True) != path:
        raise HarnessArtifactError("harness_archive_path_changed")
    return validate_harness_artifact_bytes(raw, index, baseline=baseline)


def store_harness_artifact(
    archive_root: Path, artifact: ValidatedHarnessArtifact, *, baseline: InstalledHarnessBaseline
) -> Path:
    checked = validate_harness_artifact_bytes(artifact.raw_bytes, artifact.index, baseline=baseline)
    root = archive_root.resolve(strict=True)
    if not root.is_dir():
        raise HarnessArtifactError("harness_archive_path_invalid")
    path = root / checked.index.content_address
    try:
        # Never replace an existing archived artifact, including a collision.
        with path.open("xb") as stream:
            stream.write(checked.raw_bytes)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        load_harness_artifact(root, checked.index, baseline=baseline)
    return path

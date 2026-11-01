"""Opt-in V3 registration. A candidate file grants no execution or publication authority.

Only trusted startup may configure an already product-guarded installed release.
This module verifies its V3 subset again. It never writes an installation pointer.
Existing V1/V2 selection and historical readers are intentionally not changed.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Literal, TypedDict, cast

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..decision.research_quality import QualityDecisionV1
from ..persistence.db import DshSessionLinkRecord, RunRecord, RunRoleOutputRecord, as_utc
from ..ports.research_observation import ObservedExecutionRef, Sha
from . import research_graph_recipe as legacy
from .product_release_provenance import resolve_product_receipt
from .research_publication_binding import observation_json, observation_sha
from .research_quality_projection import QualityProjectionContextV1

RECIPE_VERSION_V3 = "research-graph-projection.v3"
SELECTION_SCHEMA_V3 = "research-graph-recipe-selection.v3"
REGISTRATION_REF = (
    "packages/kernel/research_recipe_versions/candidates/research-graph-projection.v3.json"
)
CONTEXT_SCHEMA_REF = (
    "packages/kernel/research_recipe_versions/candidates/schemas/quality-projection-context.v1.json"
)
QUALITY_SCHEMA_REF = (
    "packages/kernel/research_recipe_versions/candidates/schemas/quality-decision.v1.json"
)
INSTALLED_POINTER_REF = "infra/dsh/research-graph-recipe-installed.v3.json"
SOURCE_REF = "packages/kernel/decision_hub_kernel/application/research_graph_recipe_v3.py"
_ROOT = Path(__file__).resolve().parents[4]
_SHA = re.compile(r"[a-f0-9]{64}")
_MAX_RESOURCE_BYTES = 2 * 1024 * 1024
_MAX_CLOSURE_BYTES = 32 * 1024 * 1024


class _ClosedV3(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class GraphRecipeResourceV3(_ClosedV3):
    path: str
    sha256: Sha


class GraphRecipeRegistrationV3(_ClosedV3):
    schema_version: Literal["research-graph-recipe-registration.v3"] = (
        "research-graph-recipe-registration.v3"
    )
    recipe_version: Literal["research-graph-projection.v3"] = RECIPE_VERSION_V3
    lifecycle: Literal["candidate"] = "candidate"
    recipe_module: Literal[
        "packages.kernel.decision_hub_kernel.application.research_graph_recipe_v3"
    ] = "packages.kernel.decision_hub_kernel.application.research_graph_recipe_v3"
    projection_module: Literal[
        "packages.kernel.decision_hub_kernel.application.research_graph_projection_v3"
    ] = "packages.kernel.decision_hub_kernel.application.research_graph_projection_v3"
    selection_schema: Literal["research-graph-recipe-selection.v3"] = SELECTION_SCHEMA_V3
    round_entrypoint: Literal["project_round_v3"] = "project_round_v3"
    continuation_entrypoint: Literal["should_continue_after_round"] = "should_continue_after_round"
    final_entrypoint: Literal["finalize"] = "finalize"
    context_schema_ref: Literal[
        "packages/kernel/research_recipe_versions/candidates/schemas/quality-projection-context.v1.json"
    ] = CONTEXT_SCHEMA_REF
    quality_schema_ref: Literal[
        "packages/kernel/research_recipe_versions/candidates/schemas/quality-decision.v1.json"
    ] = QUALITY_SCHEMA_REF
    supported_scope_ids: tuple[str, str] = (
        "crypto:spot:okx:BTC-USDT:trade:1m",
        "crypto:spot:okx:ETH-USDT:trade:1m",
    )
    auto_install: Literal[False] = False

    @model_validator(mode="after")
    def check_scopes(self):
        if self.supported_scope_ids != (
            "crypto:spot:okx:BTC-USDT:trade:1m",
            "crypto:spot:okx:ETH-USDT:trade:1m",
        ):
            raise ValueError("research_graph_recipe_v3_scope_not_supported")
        return self


class GraphRecipeManifestV3(_ClosedV3):
    schema_version: Literal["research-graph-recipe-manifest.v3"] = (
        "research-graph-recipe-manifest.v3"
    )
    source_files: tuple[legacy.GraphRecipeDependencyV2, ...] = Field(min_length=1)
    schema_files: tuple[legacy.GraphRecipeDependencyV2, ...] = Field(min_length=1)
    registration: GraphRecipeResourceV3
    runtime_versions: dict[str, str]


class GraphRecipeInstalledPointerV3(_ClosedV3):
    schema_version: Literal["research-graph-recipe-installed.v3"] = (
        "research-graph-recipe-installed.v3"
    )
    recipe_version: Literal["research-graph-projection.v3"] = RECIPE_VERSION_V3
    lifecycle: Literal["installed"] = "installed"
    registration: GraphRecipeResourceV3
    dependency_hash: Sha
    plugin_build_hash: Sha


class GraphRecipeSelectionV3(_ClosedV3):
    schema_version: Literal["research-graph-recipe-selection.v3"] = SELECTION_SCHEMA_V3
    run_id: str = Field(min_length=1)
    recipe_version: Literal["research-graph-projection.v3"] = RECIPE_VERSION_V3
    original_task_ref: ObservedExecutionRef
    original_pack_ref: ObservedExecutionRef
    manifest: GraphRecipeManifestV3
    dependency_hash: Sha
    installation_receipt: GraphRecipeResourceV3
    installed_pointer: GraphRecipeResourceV3
    quality_context: QualityProjectionContextV1
    quality_context_hash: Sha
    effective_hash: Sha
    recorded_at: AwareDatetime

    @model_validator(mode="after")
    def check_binding(self):
        baseline = self.quality_context
        _require_preexecution_context(baseline)
        if (
            self.dependency_hash != observation_sha(self.manifest.model_dump(mode="json"))
            or self.quality_context_hash != observation_sha(baseline.model_dump(mode="json"))
            or self.effective_hash != _effective_hash(self)
        ):
            raise ValueError("research_graph_recipe_v3_selection_binding_invalid")
        return self


class _SelectionArguments(TypedDict):
    run_id: str
    original_task_ref: ObservedExecutionRef
    original_pack_ref: ObservedExecutionRef
    manifest: GraphRecipeManifestV3
    dependency_hash: str
    installation_receipt: GraphRecipeResourceV3
    installed_pointer: GraphRecipeResourceV3
    quality_context: QualityProjectionContextV1
    quality_context_hash: str
    recorded_at: datetime


@dataclass(frozen=True)
class FrozenGraphRecipeSelectionV3:
    reference: ObservedExecutionRef
    receipt: GraphRecipeSelectionV3


@dataclass(frozen=True)
class _InstalledBindingV3:
    receipt: GraphRecipeResourceV3
    pointer: GraphRecipeResourceV3
    dependency_hash: str
    registration: GraphRecipeResourceV3
    plugin_build_hash: str
    manifest: GraphRecipeManifestV3


_INSTALL_LOCK = threading.RLock()
_INSTALLED_V3: _InstalledBindingV3 | None = None
_IMPORT_PARSE_CACHE: dict[str, tuple[str, tuple[str, ...]]] = {}


def _import_names(relative: str, payload: bytes, payload_hash: str) -> tuple[str, ...]:
    """Cache syntax work only. Every admission still reads and hashes raw files.

    Module resolution and source/schema/installation authentication are never
    cached. A changed body is parsed again even when it has the same file name.
    """
    with _INSTALL_LOCK:
        cached = _IMPORT_PARSE_CACHE.get(relative)
    if cached is not None and cached[0] == payload_hash:
        return cached[1]
    package_parts = relative.removesuffix(".py").split("/")[:-1]
    names: list[str] = []
    for node in ast.walk(ast.parse(payload, filename=relative)):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                prefix = package_parts[: len(package_parts) - node.level + 1]
                module = ".".join([*prefix, *(node.module or "").split(".")]).rstrip(".")
            else:
                module = node.module or ""
            names.extend([module, *(f"{module}.{alias.name}" for alias in node.names)])
    result = tuple(dict.fromkeys(names))
    with _INSTALL_LOCK:
        if len(_IMPORT_PARSE_CACHE) < 1024 or relative in _IMPORT_PARSE_CACHE:
            _IMPORT_PARSE_CACHE[relative] = (payload_hash, result)
    return result


def _require_preexecution_context(value: QualityProjectionContextV1) -> None:
    # Results, accepted synthesis, context selection, monitoring and qualification
    # cannot already be known at Task creation. Freeze only the scoped input policy.
    if (
        value.scope_id not in GraphRecipeRegistrationV3().supported_scope_ids
        or not value.additional_required_context_refs
        or any(not ref.strip() for ref in value.additional_required_context_refs)
        or len(set(value.additional_required_context_refs))
        != len(value.additional_required_context_refs)
        or value.bound_result_hash is not None
        or value.runtime_attestation_ref is not None
        or value.effective_budget_ref is not None
        or value.assessed_at is not None
        or value.selection_manifest_hash is not None
        or value.context_fidelity_verified
        or value.challenges
        or value.authenticated_review_ids
        or value.authenticated_review_lineage
        or value.authenticated_retained_context_refs
        or value.key_risks
        or value.path_risk_model_qualified
        or value.path_risk_qualification_ref is not None
        or value.execution_support_verified
        or value.monitor_state != "unknown"
    ):
        raise ValueError("research_graph_recipe_v3_preexecution_claim_forbidden")


def verify_round_quality_binding_v3(
    selection: GraphRecipeSelectionV3,
    context: QualityProjectionContextV1,
    quality: QualityDecisionV1,
) -> tuple[str, str]:
    """Structural binding only, after the caller verifies actual immutable proof rows.

    Root's round admission persists this context and its returned hash. Continuation,
    final projection and re-read use that exact value and recompute the decision from
    the authenticated state. This cannot authenticate a model's proof references.
    """
    selection = GraphRecipeSelectionV3.model_validate_json(selection.model_dump_json())
    context = QualityProjectionContextV1.model_validate(context.model_dump())
    quality = QualityDecisionV1.model_validate(quality.model_dump())
    if (
        context.scope_id != selection.quality_context.scope_id
        or quality.scope_id != context.scope_id
        or quality.run_id != selection.run_id
        or context.bound_result_hash != quality.source_result_hash
        or not set(selection.quality_context.additional_required_context_refs).issubset(
            context.additional_required_context_refs
        )
    ):
        raise ValueError("research_graph_recipe_v3_round_quality_binding_invalid")
    return observation_sha(context.model_dump(mode="json")), quality.decision_hash


def _effective_hash(value: GraphRecipeSelectionV3) -> str:
    # Registration/version, closure and per-Task policy are independently bound.
    return observation_sha(
        value.model_dump(mode="json", exclude={"effective_hash", "recorded_at", "schema_version"})
    )


FROZEN_PUBLICATION_CURRENT_CONTRACT = "publication-current-installation.v1"
_FROZEN_PUBLICATION_CURRENT = globals().get("_FROZEN_PUBLICATION_CURRENT")

def _read_resource(relative: str) -> bytes:
    path = PurePosixPath(relative)
    if (
        not relative
        or path.is_absolute()
        or path.as_posix() != relative
        or any(part in {"", ".", ".."} for part in relative.split("/"))
        or re.search(r'[\\\x00-\x1f<>:"|?*]', relative)
    ):
        raise ValueError("research_graph_recipe_v3_resource_path_invalid")
    if _FROZEN_PUBLICATION_CURRENT is not None:
        raw = _FROZEN_PUBLICATION_CURRENT.current_bytes(relative)
        if len(raw) > _MAX_RESOURCE_BYTES:
            raise ValueError("research_graph_recipe_v3_resource_size_invalid")
        return raw
    current = _ROOT
    for part in path.parts:
        current = current / part
        status = current.lstat()
        if current.is_symlink() or getattr(status, "st_file_attributes", 0) & 0x400:
            raise ValueError("research_graph_recipe_v3_resource_link_forbidden")
    if not current.is_file() or current.lstat().st_size > _MAX_RESOURCE_BYTES:
        raise ValueError("research_graph_recipe_v3_resource_size_invalid")
    raw = current.read_bytes()
    if len(raw) > _MAX_RESOURCE_BYTES:
        raise ValueError("research_graph_recipe_v3_resource_size_invalid")
    return raw


def graph_recipe_manifest_v3() -> GraphRecipeManifestV3:
    """Finite AST closure with raw schema/registration hashes; no shared V2 memo edit."""
    initial = (
        "packages.kernel.decision_hub_kernel.application.research_graph_binding",
        "packages.kernel.decision_hub_kernel.application.research_graph_recipe_v3",
        "packages.kernel.decision_hub_kernel.application.research_graph_projection_v3",
        "apps.hub_worker.research_skill_bootstrap",
        "apps.hub_api.research_skill_routes",
        "packages.kernel.decision_hub_kernel.application.research_skill_publication",
        "packages.kernel.decision_hub_kernel.decision.action_comparison",
        "packages.kernel.decision_hub_kernel.decision.perpetual_publication",
    )
    root_before = legacy._ROOT  # pyright: ignore[reportPrivateUsage]
    if root_before.resolve() != _ROOT.resolve():
        raise ValueError("research_graph_recipe_v3_loaded_root_mismatch")
    pending = [
        path
        for name in initial
        if (path := legacy._module_file(name)) is not None  # pyright: ignore[reportPrivateUsage]
    ]
    seen: dict[str, legacy.GraphRecipeDependencyV2] = {}
    total = 0
    while pending:
        path = pending.pop()
        relative = path.relative_to(_ROOT).as_posix()
        if relative in seen:
            continue
        payload = _read_resource(relative)
        total += len(payload)
        if len(seen) >= 1024 or total > _MAX_CLOSURE_BYTES:
            raise ValueError("research_graph_recipe_v3_dependency_limit")
        payload_hash = hashlib.sha256(payload).hexdigest()
        seen[relative] = legacy.GraphRecipeDependencyV2(path=relative, payload_hash=payload_hash)
        for name in _import_names(relative, payload, payload_hash):
            dependency = legacy._module_file(name)  # pyright: ignore[reportPrivateUsage]
            if dependency is not None:
                pending.append(dependency)
    if "packages/kernel/decision_hub_kernel/application/perpetual_close_source.py" in seen:
        relative = legacy._FINITE_METHOD_SOURCE_REF
        raw = _read_resource(relative)
        total += len(raw)
        if total > _MAX_CLOSURE_BYTES:
            raise ValueError("research_graph_recipe_v3_dependency_limit")
        seen[relative] = legacy.GraphRecipeDependencyV2(
            path=relative, payload_hash=hashlib.sha256(raw).hexdigest()
        )
    schemas = [
        path.relative_to(_ROOT).as_posix()
        for path in sorted((_ROOT / "contracts" / "schemas").rglob("*"))
        if path.is_file() and path.suffix in {".json", ".yaml", ".yml"}
    ]
    schemas.extend([CONTEXT_SCHEMA_REF, QUALITY_SCHEMA_REF])
    schema_rows = []
    for relative in sorted(set(schemas)):
        raw = _read_resource(relative)
        total += len(raw)
        if total > _MAX_CLOSURE_BYTES:
            raise ValueError("research_graph_recipe_v3_dependency_limit")
        schema_rows.append(
            legacy.GraphRecipeDependencyV2(
                path=relative, payload_hash=hashlib.sha256(raw).hexdigest()
            )
        )
    registration_raw = _read_resource(REGISTRATION_REF)
    registration = GraphRecipeRegistrationV3.model_validate_json(registration_raw)
    if (observation_json(registration.model_dump(mode="json")) + "\n").encode() != registration_raw:
        raise ValueError("research_graph_recipe_v3_registration_noncanonical")
    return GraphRecipeManifestV3(
        source_files=tuple(seen[key] for key in sorted(seen)),
        schema_files=tuple(schema_rows),
        registration=GraphRecipeResourceV3(
            path=REGISTRATION_REF, sha256=hashlib.sha256(registration_raw).hexdigest()
        ),
        runtime_versions=legacy._manifest_runtime_versions(),  # pyright: ignore[reportPrivateUsage]
    )


def _validated_installation(
    receipt_ref: GraphRecipeResourceV3, *, expected: _InstalledBindingV3 | None = None
) -> _InstalledBindingV3:
    raw = _read_resource(receipt_ref.path)
    if hashlib.sha256(raw).hexdigest() != receipt_ref.sha256:
        raise ValueError("research_graph_recipe_v3_installation_receipt_changed")
    resolved = resolve_product_receipt(
        raw, expected_root=(_FROZEN_PUBLICATION_CURRENT.logical_root if _FROZEN_PUBLICATION_CURRENT is not None else _ROOT), read_resource=_read_resource,
        expected_sha256=receipt_ref.sha256,
    )
    receipt = resolved.value
    evidence = receipt["evidence"].get("research_graph_recipe_v3")
    if not isinstance(evidence, dict) or set(evidence) != {"installed_pointer"}:
        raise ValueError("research_graph_recipe_v3_installed_pointer_required")
    pointer_ref = GraphRecipeResourceV3.model_validate(evidence["installed_pointer"])
    if (
        pointer_ref.path != INSTALLED_POINTER_REF
        or receipt["sources"].get(pointer_ref.path) != pointer_ref.sha256
    ):
        raise ValueError("research_graph_recipe_v3_installed_pointer_invalid")
    pointer_raw = _read_resource(pointer_ref.path)
    if hashlib.sha256(pointer_raw).hexdigest() != pointer_ref.sha256:
        raise ValueError("research_graph_recipe_v3_installed_pointer_changed")
    pointer = GraphRecipeInstalledPointerV3.model_validate_json(pointer_raw)
    manifest = graph_recipe_manifest_v3()
    if (
        pointer.registration != manifest.registration
        or pointer.dependency_hash != observation_sha(manifest.model_dump(mode="json"))
        or pointer.plugin_build_hash != receipt["plugin_build_hash"]
    ):
        raise ValueError("research_graph_recipe_v3_effective_implementation_mismatch")
    resources = (
        *(
            GraphRecipeResourceV3(path=item.path, sha256=item.payload_hash)
            for item in (*manifest.source_files, *manifest.schema_files)
        ),
        manifest.registration,
    )
    if any(receipt["sources"].get(item.path) != item.sha256 for item in resources):
        raise ValueError("research_graph_recipe_v3_effective_source_not_validated")
    if (
        dict((item.path, item.payload_hash) for item in manifest.source_files).get(SOURCE_REF)
        != _LOADED_SOURCE_SHA
    ):
        raise ValueError("research_graph_recipe_v3_loaded_source_changed")
    binding = _InstalledBindingV3(
        receipt_ref,
        pointer_ref,
        pointer.dependency_hash,
        pointer.registration,
        pointer.plugin_build_hash,
        manifest,
    )
    if expected is not None and binding != expected:
        raise ValueError("research_graph_recipe_v3_installation_binding_changed")
    return binding


def configure_graph_recipe_v3_from_validated_release(
    *, receipt_path: Path, receipt_sha256: str
) -> None:
    """Trusted startup after normal product guard; not an API or model supplied receipt.

    No pointer is created. Missing candidate installation evidence rejects the
    selection. Existing V2 startup does not call this opt-in configuration.
    """
    global _INSTALLED_V3
    relative = receipt_path.absolute().relative_to(_ROOT.absolute()).as_posix()
    receipt = GraphRecipeResourceV3(path=relative, sha256=receipt_sha256)
    binding = _validated_installation(receipt)
    with _INSTALL_LOCK:
        if _INSTALLED_V3 is not None and _INSTALLED_V3 != binding:
            raise ValueError("research_graph_recipe_v3_process_rebind_forbidden")
        _INSTALLED_V3 = binding  # pyright: ignore[reportConstantRedefinition]


def _installation_original() -> _InstalledBindingV3:
    with _INSTALL_LOCK:
        binding = _INSTALLED_V3
    if binding is None:
        raise ValueError("research_graph_recipe_v3_not_installed")
    return _validated_installation(binding.receipt, expected=binding)


def _installation_json(binding: _InstalledBindingV3) -> str:
    return observation_json({
        "receipt": binding.receipt.model_dump(mode="json"),
        "pointer": binding.pointer.model_dump(mode="json"),
        "dependency_hash": binding.dependency_hash,
        "registration": binding.registration.model_dump(mode="json"),
        "plugin_build_hash": binding.plugin_build_hash,
        "manifest": binding.manifest.model_dump(mode="json"),
    })


def _installation_clone(body: str) -> _InstalledBindingV3:
    value = json.loads(body)
    return _InstalledBindingV3(
        GraphRecipeResourceV3.model_validate_json(observation_json(value["receipt"])),
        GraphRecipeResourceV3.model_validate_json(observation_json(value["pointer"])),
        value["dependency_hash"],
        GraphRecipeResourceV3.model_validate_json(observation_json(value["registration"])),
        value["plugin_build_hash"],
        GraphRecipeManifestV3.model_validate_json(observation_json(value["manifest"])),
    )


def _installation() -> _InstalledBindingV3:
    from .research_recipe_read_scope import current_recipe_read_scope

    scope = current_recipe_read_scope()
    if scope is None:
        return _installation_original()
    with _INSTALL_LOCK:
        installed = _INSTALLED_V3
    if installed is None:
        return _installation_original()
    return scope.completed(
        scope.session,
        ("installation", str(_ROOT.resolve()), _installation_json(installed)),
        _installation_original, _installation_json, _installation_clone,
        kind="installation",
    )


def _require_task_scope(task: RunRoleOutputRecord, context: QualityProjectionContextV1) -> None:
    body = json.loads(task.payload_json)
    scope = body.get("scope") or {}
    instrument = scope.get("instrument")
    if (
        scope.get("market") != "crypto"
        or instrument not in {"BTC-USDT", "ETH-USDT"}
        or context.scope_id != f"crypto:spot:okx:{instrument}:trade:1m"
    ):
        raise ValueError("research_graph_recipe_v3_original_scope_mismatch")


def _read_graph_recipe_selection_v3_original(
    session: Session, run_id: str, *, _completed_current: set[str] | None = None
) -> FrozenGraphRecipeSelectionV3 | None:
    from .research_graph_history_versions import replay_retained_graph_recipe_v3_in_session

    retained = replay_retained_graph_recipe_v3_in_session(session, run_id)
    if retained is not None:
        return FrozenGraphRecipeSelectionV3(
            ObservedExecutionRef.model_validate_json(retained.reference_json),
            GraphRecipeSelectionV3.model_validate_json(retained.receipt_json),
        )
    rows = list(
        session.scalars(
            select(RunRoleOutputRecord)
            .where(
                RunRoleOutputRecord.run_id == run_id,
                RunRoleOutputRecord.role == legacy.SELECTION_ROLE,
            )
            .execution_options(populate_existing=True)
        )
    )
    if not rows:
        return None
    if len(rows) != 1:
        raise ValueError("research_graph_recipe_selection_ambiguous")
    row = rows[0]
    if row.schema_version != SELECTION_SCHEMA_V3:
        raise ValueError("research_graph_recipe_v3_selection_version_mismatch")
    reference = legacy._ref(row)  # pyright: ignore[reportPrivateUsage]
    value = GraphRecipeSelectionV3.model_validate_json(row.payload_json)
    task, pack = legacy._original_rows(session, run_id)  # pyright: ignore[reportPrivateUsage]
    _require_task_scope(task, value.quality_context)
    installation = _installation()
    if (
        value.run_id != run_id
        or value.original_task_ref != legacy._ref(task)  # pyright: ignore[reportPrivateUsage]
        or value.original_pack_ref != legacy._ref(pack)  # pyright: ignore[reportPrivateUsage]
        or value.recorded_at != reference.recorded_at
        or value.recorded_at != value.original_task_ref.recorded_at
        or value.recorded_at != value.original_pack_ref.recorded_at
        or value.installation_receipt != installation.receipt
        or value.installed_pointer != installation.pointer
        or value.dependency_hash != installation.dependency_hash
        or value.manifest != installation.manifest
        or observation_json(value.model_dump(mode="json")) != row.payload_json
        or row.output_id
        != "graph_recipe_selection_" + observation_sha(value.model_dump(mode="json"))
    ):
        raise ValueError("research_graph_recipe_v3_selection_changed")
    clocks = session.scalars(
        select(RunRoleOutputRecord.created_at).where(
            RunRoleOutputRecord.run_id == run_id,
            RunRoleOutputRecord.role.like("dsh_execution_request:%"),
        )
    ).all()
    if any(
        (clock := as_utc(cast(datetime | None, raw))) is None or clock < value.recorded_at
        for raw in clocks
    ):
        raise ValueError("research_graph_recipe_selection_after_dispatch")
    if _completed_current is not None:
        _completed_current.add(run_id)
    return FrozenGraphRecipeSelectionV3(reference, value)


def _selection_json(value: FrozenGraphRecipeSelectionV3 | None) -> str:
    assert value is not None
    return observation_json({
        "reference": value.reference.model_dump(mode="json"),
        "receipt": value.receipt.model_dump(mode="json"),
    })


def _selection_clone(body: str) -> FrozenGraphRecipeSelectionV3:
    value = json.loads(body)
    return FrozenGraphRecipeSelectionV3(
        ObservedExecutionRef.model_validate_json(observation_json(value["reference"])),
        GraphRecipeSelectionV3.model_validate_json(observation_json(value["receipt"])),
    )


def read_graph_recipe_selection_v3_in_session(
    session: Session, run_id: str
) -> FrozenGraphRecipeSelectionV3 | None:
    from .research_context_execution import _PROOF_STACK  # pyright: ignore[reportPrivateUsage]
    from .research_recipe_read_scope import current_recipe_read_scope

    scope = current_recipe_read_scope()
    if scope is None:
        return _read_graph_recipe_selection_v3_original(session, run_id)
    with _INSTALL_LOCK:
        installed = _INSTALLED_V3
    if installed is None:
        return _read_graph_recipe_selection_v3_original(session, run_id)
    completed_current: set[str] = set()

    def read_original() -> FrozenGraphRecipeSelectionV3 | None:
        completed_current.clear()
        return _read_graph_recipe_selection_v3_original(
            session, run_id, _completed_current=completed_current
        )

    # The registered current release is an exact identity. Retained originals
    # remain on their original full reader and never become execution authority.
    return scope.completed(
        session,
        ("selection", str(_ROOT.resolve()), run_id, _PROOF_STACK.get(),
         _installation_json(installed)),
        read_original,
        _selection_json, _selection_clone,
        kind="selection",
        cacheable=lambda value: value is not None and run_id in completed_current and (
            value.receipt.installation_receipt == installed.receipt
            and value.receipt.manifest == installed.manifest
            and value.receipt.installed_pointer == installed.pointer
        ),
    )


def freeze_graph_recipe_selection_v3_in_session(
    session: Session,
    *,
    run_id: str,
    recorded_at: datetime,
    quality_context: QualityProjectionContextV1,
) -> FrozenGraphRecipeSelectionV3:
    """Factory-only new Task using nonserializable permit and verified installed bytes."""
    connection = session.connection()
    driver = connection.connection.driver_connection
    if (
        connection.dialect.name != "sqlite"
        or not isinstance(driver, sqlite3.Connection)
        or not driver.in_transaction
    ):
        raise ValueError("research_graph_recipe_writer_required")
    if recorded_at.tzinfo is None or recorded_at.utcoffset() is None:
        raise ValueError("research_graph_recipe_clock_timezone_required")
    quality_context = QualityProjectionContextV1.model_validate(quality_context.model_dump())
    _require_preexecution_context(quality_context)
    installation = _installation()
    if previous := read_graph_recipe_selection_v3_in_session(session, run_id):
        if (
            previous.receipt.recorded_at != recorded_at
            or previous.receipt.quality_context != quality_context
        ):
            raise ValueError("research_graph_recipe_original_conflict")
        return previous
    permits = [
        permit
        for permit in legacy._CREATION_PERMITS.get()  # pyright: ignore[reportPrivateUsage]
        if permit.session is session
        and permit.transaction is session.get_transaction()
        and permit.driver is driver
        and permit.run_id == run_id
        and not permit.used
    ]
    if len(permits) != 1:
        raise ValueError("research_graph_recipe_new_task_scope_required")
    run = session.get(RunRecord, run_id, populate_existing=True)
    task, pack = legacy._original_rows(session, run_id)  # pyright: ignore[reportPrivateUsage]
    _require_task_scope(task, quality_context)
    if (
        run is None
        or run.status not in {"admitted", "dispatched", "running"}
        or run.artifact_id is not None
        or as_utc(task.created_at) != recorded_at
        or as_utc(pack.created_at) != recorded_at
        or session.get(DshSessionLinkRecord, run_id, populate_existing=True) is not None
        or session.scalar(
            select(RunRoleOutputRecord.output_id).where(
                RunRoleOutputRecord.run_id == run_id,
                (RunRoleOutputRecord.role.like("dsh_execution_request:%"))
                | (RunRoleOutputRecord.role.like("research_graph_%")),
            )
        )
        is not None
    ):
        raise ValueError("research_graph_recipe_existing_task_not_upgradeable")
    manifest = installation.manifest
    arguments = _SelectionArguments(
        run_id=run_id,
        original_task_ref=legacy._ref(task),  # pyright: ignore[reportPrivateUsage]
        original_pack_ref=legacy._ref(pack),  # pyright: ignore[reportPrivateUsage]
        manifest=manifest,
        dependency_hash=installation.dependency_hash,
        installation_receipt=installation.receipt,
        installed_pointer=installation.pointer,
        quality_context=quality_context,
        quality_context_hash=observation_sha(quality_context.model_dump(mode="json")),
        recorded_at=recorded_at,
    )
    # The effective hash excludes only its own seal and the non-semantic row clock.
    unsealed = GraphRecipeSelectionV3.model_construct(**arguments, effective_hash="0" * 64)
    value = GraphRecipeSelectionV3(**arguments, effective_hash=_effective_hash(unsealed))
    encoded = observation_json(value.model_dump(mode="json"))
    row = RunRoleOutputRecord(
        output_id="graph_recipe_selection_" + observation_sha(value.model_dump(mode="json")),
        run_id=run_id,
        role=legacy.SELECTION_ROLE,
        schema_version=SELECTION_SCHEMA_V3,
        payload_json=encoded,
        payload_hash=hashlib.sha256(encoded.encode()).hexdigest(),
        created_at=recorded_at,
    )
    session.add(row)
    session.flush()
    permits[0].used = True
    return FrozenGraphRecipeSelectionV3(legacy._ref(row), value)  # pyright: ignore[reportPrivateUsage]


_LOADED_SOURCE_SHA = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()

"""Prepare an immutable input bundle in the caller's SQLite transaction.

The bundle records original inputs and prepared declarations. It does not
attest checker execution, bind a Host deployment, or permit execution.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts.generated.agentic_research import (
    DomainPackManifest,
    ResearchSessionRequest,
)
from packages.contracts_py.decision_hub_contracts.generated.research_binding import (
    DomainPackManifest as StrictPreparedManifest,
)
from packages.contracts_py.decision_hub_contracts.generated.research_binding import (
    ResearchCompiledPolicyReceiptV1,
    ResearchFactoryLibraryScopeSource,
    ResearchFactoryScopeSource,
    ResearchFactoryScopeSourceV2,
    ResearchFactoryTriggerSource,
    ResearchPackDefinitionV2,
    ResearchRequestPackReceiptV2,
    ResearchTaskReceiptV2,
)
from packages.contracts_py.decision_hub_contracts.generated.research_policy import (
    ResearchPolicyResource,
    ResearchRoleRubricBinding,
    ResearchTaskScope,
    ResearchTaskScopeV2,
)
from packages.kernel.decision_hub_kernel.application.event_watch import (
    _watch_model,  # pyright: ignore[reportPrivateUsage]
)
from packages.kernel.decision_hub_kernel.application.research_method import canonical_recipe
from packages.kernel.decision_hub_kernel.application.research_policy_compiler import (
    compile_policy,
    scope_hash,
)
from packages.kernel.decision_hub_kernel.application.research_policy_resources import (
    MAX_RESOURCE_BYTES,
    MAX_RESOURCES,
    MAX_TOTAL_BYTES,
    PolicyResourceLoader,
    canonical_hash,
    normalized_relative_ref,
    parse_resource_bytes,
    resource_definition,
)
from packages.kernel.decision_hub_kernel.application.snapshot import (
    SnapshotService,
    _evidence_item,  # pyright: ignore[reportPrivateUsage]
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    DshSessionLinkRecord,
    DshSessionPromptRecord,
    EventRecord,
    EventWatchLinkRecord,
    EventWatchRecord,
    ResearchDocumentVersionRecord,
    ResearchResultRecord,
    ResearchRunScopeRecord,
    ResearchTraceRecord,
    RunRecord,
    RunRoleOutputRecord,
    SnapshotRecord,
    StudyRecord,
    as_utc,
)

if TYPE_CHECKING:
    from .research_probability_binding import KernelProbabilitySelection

PACK_ROLE = "research_request_pack"
TASK_ROLE = "research_task_request"
COMPILED_ROLE = "research_compiled_policy"
PACK_SCHEMA = "research-request-pack.v2"
TASK_SCHEMA = "research-task-request.v2"
COMPILED_SCHEMA = "research-compiled-policy-receipt.v1"
BUNDLE_ROLES = (PACK_ROLE, TASK_ROLE, COMPILED_ROLE)
Receipt = ResearchRequestPackReceiptV2 | ResearchTaskReceiptV2 | ResearchCompiledPolicyReceiptV1


class ResearchBindingError(ValueError):
    def __init__(self, code: str) -> None:
        self.error_code = code
        super().__init__(code)


@dataclass(frozen=True)
class PreparedResearchPolicyRefs:
    """Explicit confirmation of the refs in an actual prepared Pack manifest."""

    gate_policy_ref: str
    rubric_policy_ref: str
    role_profile_refs: tuple[str, ...]


@dataclass(frozen=True)
class FrozenResearchBundle:
    pack: ResearchRequestPackReceiptV2
    task: ResearchTaskReceiptV2
    compiled: ResearchCompiledPolicyReceiptV1
    recorded_at: datetime

    @property
    def request(self) -> ResearchSessionRequest:
        # The new generated contract has strict local copies of referenced DTOs.
        return ResearchSessionRequest.model_validate(self.task.request.model_dump(mode="json"))


@dataclass(frozen=True)
class LegacyResearchBundle:
    """An existing v1 receipt or execution marker; never upgraded in place."""

    run_id: str


def _utc(value: datetime) -> datetime:
    normalized = as_utc(cast(datetime | None, value))
    if normalized is None:
        raise ResearchBindingError("research_bundle_timestamp_invalid")
    return normalized


def _json(value: BaseModel) -> dict[str, Any]:
    return value.model_dump(mode="json")


def _read_bounded(root: Path, reference: str) -> tuple[str, bytes]:
    relative = normalized_relative_ref(reference)
    path = (root / relative).resolve(strict=True)
    if not path.is_relative_to(root) or not path.is_file():
        raise ResearchBindingError("research_bundle_resource_outside_root")
    with path.open("rb") as stream:
        raw = stream.read(MAX_RESOURCE_BYTES + 1)
    if not raw or len(raw) > MAX_RESOURCE_BYTES:
        raise ResearchBindingError("research_bundle_resource_size_invalid")
    if (root / relative).resolve(strict=True) != path:
        raise ResearchBindingError("research_bundle_resource_path_changed")
    raw.decode("utf-8", errors="strict")
    return path.relative_to(root).as_posix(), raw


def load_prepared_pack_definition(
    root: Path,
    refs: PreparedResearchPolicyRefs,
    *,
    allowed_capabilities: tuple[str, ...],
    execution_mode: Literal["live", "replay"],
) -> ResearchPackDefinitionV2:
    """Load actual opt-in resources, without altering the manifest or old renderer."""
    root = root.resolve(strict=True)
    manifest_ref, manifest_raw = _read_bounded(root, "pack.yaml")
    # Validate actual YAML values before an old v1 DTO can coerce them.
    strict_manifest = StrictPreparedManifest.model_validate(parse_resource_bytes(manifest_raw))
    manifest = DomainPackManifest.model_validate(_json(strict_manifest))
    if (
        refs.gate_policy_ref != manifest.gate_policy_ref
        or refs.rubric_policy_ref != manifest.evaluation_policy_ref
        or refs.role_profile_refs != tuple(manifest.role_profile_refs)
    ):
        raise ResearchBindingError("research_bundle_opt_in_manifest_mismatch")
    loader = PolicyResourceLoader(root)
    gate = loader.load(refs.gate_policy_ref, expected_schema_version="research-gate-policy.v2")
    rubric = loader.load(
        refs.rubric_policy_ref, expected_schema_version="research-rubric-policy.v2"
    )
    bindings = [loader.load_role_binding(ref, rubric=rubric) for ref in refs.role_profile_refs]
    resources = {
        manifest_ref: manifest_raw,
        **{
            value.relative_ref: value.raw_utf8.encode("utf-8")
            for value in (gate, rubric, *(binding.profile_resource for binding in bindings))
        },
    }
    if len(resources) > MAX_RESOURCES or sum(map(len, resources.values())) > MAX_TOTAL_BYTES:
        raise ResearchBindingError("research_bundle_resources_exceeded")

    def read(reference: str) -> tuple[str, bytes]:
        relative = normalized_relative_ref(reference)
        if relative in resources:
            return relative, resources[relative]
        key, raw = _read_bounded(root, relative)
        if key in resources and resources[key] != raw:
            raise ResearchBindingError("research_bundle_resource_identity_conflict")
        resources[key] = raw
        if len(resources) > MAX_RESOURCES or sum(map(len, resources.values())) > MAX_TOTAL_BYTES:
            raise ResearchBindingError("research_bundle_resources_exceeded")
        return key, raw

    evidence_ref, evidence_raw = read(manifest.evidence_policy_ref)
    evidence = parse_resource_bytes(evidence_raw)
    doctrine_ref, doctrine_raw = read(manifest.doctrine_ref)
    files = {
        doctrine_ref: {
            "content": doctrine_raw.decode("utf-8"),
            "sha256": hashlib.sha256(doctrine_raw).hexdigest(),
        }
    }
    roles = []
    for binding in bindings:
        profile = resource_definition(binding.profile_resource)
        instruction_ref = normalized_relative_ref(
            profile["system_instruction_ref"], parent_reference=binding.profile_ref
        )
        instruction_ref, raw = read(instruction_ref)
        files[instruction_ref] = {
            "content": raw.decode("utf-8"),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
        roles.append({"profile": profile, "instruction_file": instruction_ref})
    manager_id = f"{manifest.pack_id}.manager"
    managers = [item["profile"] for item in roles if item["profile"]["profile_id"] == manager_id]
    if len(managers) != 1:
        raise ResearchBindingError("research_bundle_manager_invalid")
    definition = ResearchPackDefinitionV2.model_validate(
        {
            "schema_version": "research-pack-definition.v2",
            "scope": "request_manifest_evidence_and_research_instructions",
            "manifest": _json(manifest),
            "evidence_policy": evidence,
            "research_policy": {
                "schema_version": "research-pack-instructions.v2",
                "doctrine_file": doctrine_ref,
                "manager_ref": f"{manager_id}.v{managers[0]['version'].split('.')[0]}",
                "files": files,
                "roles": roles,
            },
            "allowed_capabilities": list(allowed_capabilities),
            "execution_mode": execution_mode,
            "evidence_policy_resource": {
                "relative_ref": evidence_ref,
                "source_schema_version": "crypto-macro-evidence-policy.v3",
                "raw_sha256": hashlib.sha256(evidence_raw).hexdigest(),
                "raw_utf8": evidence_raw.decode("utf-8"),
                "canonical_definition_hash": canonical_hash(evidence),
            },
            "gate_policy_resource": _json(gate),
            "rubric_policy_resource": _json(rubric),
            "role_rubric_bindings": [_json(binding) for binding in bindings],
        }
    )
    _validate_pack_definition(definition)
    return definition


def _study_source(study: StudyRecord, *, perpetual=None) -> dict[str, Any]:
    horizons = [item.strip() for item in study.horizon_scope.split(",") if item.strip()]
    if perpetual is None and (len(horizons) != 3 or set(horizons) != {"30m", "24h", "72h"}):
        raise ResearchBindingError("research_bundle_study_horizons_invalid")
    return {
        **({"schema_version": "research-factory-study-source.v2",
            "perpetual_scope_id": perpetual[0]["context"]["scope_id"],
            "admission_ref": perpetual[1]} if perpetual is not None else {}),
        "study_id": study.study_id,
        "scope_key": study.scope_key,
        "question_key": study.question_key,
        "market": study.market,
        "instrument": study.instrument,
        "raw_horizon_scope": study.horizon_scope,
        "horizon_scope": horizons,
        "identity_basis": study.identity_basis,
        "created_at": _utc(study.created_at),
        "updated_at": _utc(study.updated_at),
    }


def _library_scope_source(scope: ResearchRunScopeRecord | None) -> dict[str, Any] | None:
    if scope is None:
        return None
    return {
        "run_id": scope.run_id,
        "research_family": scope.research_family,
        "domain_pack_ref": scope.domain_pack_ref,
        "instrument_scope": json.loads(scope.instrument_scope_json),
        "instrument_scope_json": scope.instrument_scope_json,
        "library_context_mode": scope.library_context_mode,
        "selected_version_ids": json.loads(scope.selected_version_ids_json),
        "selected_version_ids_json": scope.selected_version_ids_json,
        "allow_remote_library_content": scope.allow_remote_library_content,
        "admission_request_hash": scope.request_hash,
        "created_at": _utc(scope.created_at),
    }


def _trigger_source(session: Session, run: RunRecord) -> dict[str, Any]:
    snapshot = session.get(SnapshotRecord, run.snapshot_id, populate_existing=True)
    if snapshot is None:
        raise ResearchBindingError("research_bundle_trigger_missing")
    rows = SnapshotService.read_evidence_in_session(session, snapshot.snapshot_id)
    actual = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
    expected_id = (
        "snap_" + hashlib.sha256(f"{run.run_id}:{actual}".encode()).hexdigest()[:32]
        if snapshot.run_id is not None
        else f"snap_{actual[:32]}"
    )
    if (
        not rows
        or actual != snapshot.snapshot_hash
        or expected_id != snapshot.snapshot_id
        or snapshot.event_id != run.event_id
        or snapshot.run_id not in {None, run.run_id}
        or snapshot.snapshot_type != "trigger"
    ):
        raise ResearchBindingError("research_bundle_trigger_invalid")
    cutoff = _utc(snapshot.cutoff_at)
    for item in rows:
        original = _evidence_item(
            text=str(item["text"]),
            source_id=str(item["source_id"]),
            source_type=str(item["source_type"]),
            observed_at=str(item["observed_at"]),
            published_at=str(item["published_at"])
            if item.get("published_at") is not None
            else None,
            received_at=str(item["received_at"]),
            cutoff_at=str(item["cutoff_at"]),
            content_hash=str(item["content_hash"]),
        )
        if original != item or _utc(datetime.fromisoformat(str(item["cutoff_at"]))) != cutoff:
            raise ResearchBindingError("research_bundle_trigger_invalid")
        received = _utc(datetime.fromisoformat(str(item["received_at"])))
        observed = _utc(datetime.fromisoformat(str(item["observed_at"])))
        published = item.get("published_at")
        if (
            observed > received
            or received > cutoff
            or (published is not None and _utc(datetime.fromisoformat(str(published))) > received)
        ):
            raise ResearchBindingError("research_bundle_trigger_pit_invalid")
    return {
        "snapshot_id": snapshot.snapshot_id,
        "snapshot_hash": snapshot.snapshot_hash,
        "event_id": snapshot.event_id,
        "run_id": snapshot.run_id,
        "snapshot_type": snapshot.snapshot_type,
        "generation": snapshot.generation,
        "cutoff_at": cutoff,
        "created_at": _utc(snapshot.created_at),
        "pack_version": snapshot.pack_version,
    }


def read_factory_source_in_session(session: Session, run_id: str) -> ResearchFactoryScopeSource:
    """Read the actual first source; no DTO caller can replace these SQL inputs."""
    run = session.get(RunRecord, run_id, populate_existing=True)
    if run is None:
        raise KeyError(run_id)
    study = session.get(StudyRecord, run.study_id, populate_existing=True) if run.study_id else None
    if study is None:
        raise ResearchBindingError("research_study_scope_missing")
    linked = session.get(EventWatchLinkRecord, run.event_id, populate_existing=True)
    watch = (
        session.get(EventWatchRecord, linked.watch_id, populate_existing=True)
        if linked
        else session.scalars(
            select(EventWatchRecord)
            .filter_by(event_id=run.event_id)
            .execution_options(populate_existing=True)
        ).one_or_none()
    )
    event = session.get(EventRecord, run.event_id, populate_existing=True)
    library = session.get(ResearchRunScopeRecord, run_id, populate_existing=True)
    from .perpetual_research_binding import read_perpetual_admission_identity_in_session
    perpetual = read_perpetual_admission_identity_in_session(session, run_id)
    source_model = ResearchFactoryScopeSourceV2 if perpetual is not None else ResearchFactoryScopeSource
    body = {
        "schema_version": "research-factory-scope-source.v2" if perpetual is not None else "research-factory-scope-source.v1",
        "run_id": run_id,
        "event_id": run.event_id,
        "study_id": study.study_id,
        "selected_from": "watch" if watch is not None else "event" if event is not None else "none",
        "watch": _json(_watch_model(watch)) if watch is not None else None,
        "event": {
            "event_id": event.event_id,
            "event_type": event.event_type,
            "occurred_at": _utc(event.occurred_at),
            "received_at": _utc(event.received_at),
            "generation": event.generation,
            "status": event.status,
        }
        if event is not None
        else None,
        "study": _study_source(study, perpetual=perpetual),
        "trigger": _trigger_source(session, run),
        "library_scope": _library_scope_source(library),
    }
    value = source_model.model_validate({**body, "source_hash": "0" * 64})
    encoded = _json(value)
    encoded.pop("source_hash")
    return source_model.model_validate(
        {**encoded, "source_hash": canonical_hash(encoded)}
    )


def factory_event_identity(source: ResearchFactoryScopeSource) -> tuple[str, str, str | None]:
    if source.watch is not None:
        family, basis, watch_id = source.watch.event_family, "watch", source.watch.watch_id
    elif source.event is not None:
        family, basis, watch_id = source.event.event_type, "event", None
    else:
        family, basis, watch_id = "", "unknown_conservative_default", None
    if not family.strip():
        return "unknown", "unknown_conservative_default", None
    return family, basis, watch_id


def task_scope(
    request: ResearchSessionRequest, source: ResearchFactoryScopeSource
) -> ResearchTaskScope:
    family, basis, watch_id = factory_event_identity(source)
    current = any(
        item.requirement_id == "market_regime_context" and item.importance == "hard"
        for item in request.evidence_requirements
    )
    request_body = _json(request)
    perpetual = isinstance(source, ResearchFactoryScopeSourceV2)
    scope_model = ResearchTaskScopeV2 if perpetual else ResearchTaskScope
    body = {
        "schema_version": "research-task-scope.v2" if perpetual else "research-task-scope.v1",
        **({"perpetual_scope_id": source.study.perpetual_scope_id,
            "admission_ref": _json(source.study.admission_ref)} if perpetual else {}),
        "run_id": request.run_id,
        "event_id": request.event_id,
        "study_id": source.study_id,
        "trigger_snapshot_id": request.trigger_snapshot_id,
        "trigger_snapshot_hash": source.trigger.snapshot_hash,
        "market": source.study.market,
        "instrument": source.study.instrument,
        "horizon_scope": list(source.study.horizon_scope),
        "domain_pack_ref": request.domain_pack_ref,
        "execution_mode": request.execution_mode,
        "research_mode": request.research_mode,
        "scope_kind": "historical_review"
        if request.research_mode == "historical_review"
        else "current_snapshot"
        if current
        else "event",
        "event_family": family,
        "event_family_basis": basis,
        "watch_id": watch_id,
        "full_task_hash": canonical_hash(request_body),
        "requirements_hash": canonical_hash(
            [_json(item) for item in request.evidence_requirements]
        ),
        "allowed_capabilities_hash": canonical_hash(request.allowed_capabilities),
        "pit_cutoff_at": request_body["pit_cutoff_at"],
        "deadline_at": request_body["deadline_at"],
    }
    return scope_model.model_validate({**body, "scope_hash": scope_hash(body)})


def _validate_pack_definition(definition: ResearchPackDefinitionV2) -> None:
    manifest, evidence, policy = (
        definition.manifest,
        definition.evidence_policy_resource,
        definition.research_policy,
    )
    raw = evidence.raw_utf8.encode("utf-8")
    parsed = parse_resource_bytes(raw)
    if (
        normalized_relative_ref(manifest.evidence_policy_ref) != evidence.relative_ref
        or normalized_relative_ref(evidence.relative_ref) != evidence.relative_ref
        or evidence.raw_sha256 != hashlib.sha256(raw).hexdigest()
        or evidence.canonical_definition_hash != canonical_hash(parsed)
        or _json(definition.evidence_policy) != parsed
        or definition.evidence_policy.pack_id
        != f"{manifest.pack_id}.v{manifest.version.split('.')[0]}"
        or not set(definition.allowed_capabilities).issubset(manifest.capability_refs)
        or normalized_relative_ref(manifest.gate_policy_ref)
        != definition.gate_policy_resource.relative_ref
        or normalized_relative_ref(manifest.evaluation_policy_ref)
        != definition.rubric_policy_resource.relative_ref
        or [normalized_relative_ref(ref) for ref in manifest.role_profile_refs]
        != [role.profile_ref for role in definition.role_rubric_bindings]
        or normalized_relative_ref(manifest.doctrine_ref) != policy.doctrine_file
    ):
        raise ResearchBindingError("research_bundle_pack_resource_mismatch")
    resources: dict[str, bytes] = {evidence.relative_ref: raw}
    profiles = []
    for resource in (
        definition.gate_policy_resource,
        definition.rubric_policy_resource,
        *(role.profile_resource for role in definition.role_rubric_bindings),
    ):
        value = ResearchPolicyResource.model_validate(_json(resource))
        body = resource_definition(value)
        if (
            value.relative_ref in resources
            and resources[value.relative_ref] != value.raw_utf8.encode()
        ):
            raise ResearchBindingError("research_bundle_resource_identity_conflict")
        resources[value.relative_ref] = value.raw_utf8.encode()
        if value.source_schema_version == "role-profile.v1":
            profiles.append(body)
    if profiles != [_json(role.profile) for role in policy.roles]:
        raise ResearchBindingError("research_bundle_instruction_roles_mismatch")
    manager_id = f"{manifest.pack_id}.manager"
    managers = [role.profile for role in policy.roles if role.profile.profile_id == manager_id]
    if (
        len(managers) != 1
        or policy.manager_ref != f"{manager_id}.v{managers[0].version.split('.')[0]}"
    ):
        raise ResearchBindingError("research_bundle_manager_invalid")
    expected_files = {policy.doctrine_file}
    for role, binding in zip(policy.roles, definition.role_rubric_bindings, strict=True):
        expected = normalized_relative_ref(
            role.profile.system_instruction_ref, parent_reference=binding.profile_ref
        )
        if expected != role.instruction_file:
            raise ResearchBindingError("research_bundle_instruction_reference_mismatch")
        expected_files.add(expected)
    if set(policy.files) != expected_files:
        raise ResearchBindingError("research_bundle_instruction_files_mismatch")
    for reference, file in policy.files.items():
        content = file.content.encode("utf-8")
        if (
            normalized_relative_ref(reference) != reference
            or len(content) > MAX_RESOURCE_BYTES
            or hashlib.sha256(content).hexdigest() != file.sha256
            or (reference in resources and resources[reference] != content)
        ):
            raise ResearchBindingError("research_bundle_instruction_content_invalid")
        resources[reference] = content
    if len(resources) > MAX_RESOURCES or sum(map(len, resources.values())) > MAX_TOTAL_BYTES:
        raise ResearchBindingError("research_bundle_resources_exceeded")


def _validate_payloads(
    pack: ResearchRequestPackReceiptV2,
    task: ResearchTaskReceiptV2,
    compiled: ResearchCompiledPolicyReceiptV1,
) -> None:
    source, scope, request = task.factory_source, task.scope, task.request
    original_source = _json(source)
    original_source.pop("source_hash")
    original_compiled = _json(compiled.compiled)
    original_compiled.pop("compiled_hash")
    original_request = ResearchSessionRequest.model_validate(_json(request))
    expected_scope = task_scope(original_request, source)
    watch_changed = (request.event_watch is None) != (source.watch is None)
    if request.event_watch is not None and source.watch is not None:
        watch_changed = _json(request.event_watch) != _json(source.watch)
    if (
        not (pack.run_id == task.run_id == compiled.run_id == request.run_id == source.run_id)
        or request.event_id != source.event_id
        or compiled.event_id != request.event_id
        or source.study_id != source.study.study_id
        or source.trigger.snapshot_id != request.trigger_snapshot_id
        or source.trigger.event_id != request.event_id
        or request.current_round != 1
        or task.request_hash != canonical_hash(_json(request))
        or pack.definition_hash != canonical_hash(_json(pack.definition))
        or task.pack_definition_hash != pack.definition_hash
        or compiled.pack_definition_hash != pack.definition_hash
        or compiled.full_task_hash != task.request_hash
        or task.compiled_policy_hash != compiled.compiled.compiled_hash
        or source.source_hash != canonical_hash(original_source)
        or compiled.compiled.compiled_hash != canonical_hash(original_compiled)
        or _json(scope) != _json(expected_scope)
        or compiled.compiled.scope_hash != scope.scope_hash
        or compiled.compiled.requirements_hash != scope.requirements_hash
        or request.execution_mode != pack.definition.execution_mode
        or request.domain_pack_ref
        != f"{pack.definition.manifest.pack_id}.v{pack.definition.manifest.version.split('.')[0]}"
        or request.role_profile_ref != pack.definition.research_policy.manager_ref
        or not set(request.allowed_capabilities).issubset(pack.definition.allowed_capabilities)
        or request.evidence_refs != [item.evidence_id for item in request.input_evidence]
        or (source.selected_from == "watch") != (source.watch is not None)
        or (source.selected_from == "event" and (source.watch is not None or source.event is None))
        or (
            source.selected_from == "none"
            and (source.watch is not None or source.event is not None)
        )
        or watch_changed
    ):
        raise ResearchBindingError("research_bundle_binding_invalid")
    _validate_pack_definition(pack.definition)
    if (
        _json(compiled.compiled.gate_resource) != _json(pack.definition.gate_policy_resource)
        or _json(compiled.compiled.rubric_resource) != _json(pack.definition.rubric_policy_resource)
        or [_json(item) for item in compiled.compiled.role_rubric_bindings]
        != [_json(item) for item in pack.definition.role_rubric_bindings]
        or compiled.compiled.allowed_execution
        or compiled.compiled.status != "prepared"
        or any(
            rule.status != "not_available" or rule.reason_code != "prepared_not_executed"
            for rule in compiled.compiled.compiled_rules
        )
    ):
        raise ResearchBindingError("research_bundle_prepared_policy_invalid")


def _validate_current_source(session: Session, run: RunRecord, task: ResearchTaskReceiptV2) -> None:
    source = task.factory_source
    from .action_review import validate_action_review_context_in_session

    validate_action_review_context_in_session(session, run.run_id, task.request.input_evidence)
    from .cpi_pre_event_context import validate_cpi_pre_event_context_in_session
    validate_cpi_pre_event_context_in_session(session, task.request.input_evidence)
    from .event_scenario_snapshot import validate_event_scenario_context_in_session
    validate_event_scenario_context_in_session(session, run.run_id, task.request.input_evidence, cutoff_at=task.request.context_manifest.created_at)
    from .event_opportunity_snapshot import validate_event_opportunity_context_in_session
    validate_event_opportunity_context_in_session(session, run.run_id, task.request.input_evidence, cutoff_at=task.request.context_manifest.created_at)
    from .event_scenario_autoproduction import validate_event_owned_scenario_context_in_session
    validate_event_owned_scenario_context_in_session(session, task.request.input_evidence, cutoff_at=task.request.context_manifest.created_at)
    if (
        run.run_id != source.run_id
        or run.event_id != source.event_id
        or run.study_id != source.study_id
        or run.snapshot_id != source.trigger.snapshot_id
    ):
        raise ResearchBindingError("research_bundle_run_identity_mismatch")
    current_trigger = ResearchFactoryTriggerSource.model_validate(_trigger_source(session, run))
    if _json(current_trigger) != _json(source.trigger):
        raise ResearchBindingError("research_bundle_trigger_changed")
    study = session.get(StudyRecord, source.study_id, populate_existing=True)
    if study is None:
        raise ResearchBindingError("research_bundle_study_scope_changed")
    current = {
        "study_id": study.study_id,
        "scope_key": study.scope_key,
        "question_key": study.question_key,
        "market": study.market,
        "instrument": study.instrument,
        "raw_horizon_scope": study.horizon_scope,
        "identity_basis": study.identity_basis,
    }
    if current != {key: _json(source.study)[key] for key in current}:
        raise ResearchBindingError("research_bundle_study_scope_changed")
    from .research_run_scope import require_run_research_scope_in_session

    require_run_research_scope_in_session(
        session, run, domain_pack_ref=task.request.domain_pack_ref
    )
    from .research_run_scope import (
        read_run_spot_admission_in_session,
        spot_run_context_inputs_in_session,
    )

    frozen_spot = read_run_spot_admission_in_session(session, run.run_id)
    protected_inputs = [
        item
        for item in task.request.input_evidence
        if item.source_id.startswith(("research-run-spot-admission:", "market-path-manifest:"))
    ]
    if frozen_spot is not None:
        expected_inputs = spot_run_context_inputs_in_session(session, run.run_id)
        if sorted(
            (_json(item) for item in protected_inputs), key=lambda item: item["evidence_id"]
        ) != sorted(
            (_json(item) for item in expected_inputs), key=lambda item: item["evidence_id"]
        ):
            raise ResearchBindingError("research_bundle_spot_original_inputs_changed")
    elif protected_inputs:
        raise ResearchBindingError("research_bundle_spot_original_inputs_unadmitted")
    record = session.get(ResearchRunScopeRecord, run.run_id, populate_existing=True)
    current_library = _library_scope_source(record)
    current_library = (
        _json(ResearchFactoryLibraryScopeSource.model_validate(current_library))
        if current_library is not None
        else None
    )
    original_library = _json(source.library_scope) if source.library_scope is not None else None
    if current_library != original_library:
        raise ResearchBindingError("research_bundle_library_scope_changed")
    receipts = [_json(item) for item in task.library_inputs]
    if (json.loads(record.input_receipts_json) if record is not None else []) != receipts:
        raise ResearchBindingError("research_bundle_library_inputs_changed")
    inputs = [
        item
        for item in task.request.input_evidence
        if item.source_id.startswith("research-library:")
    ]
    if len(receipts) != len(inputs) or len({item["evidence_id"] for item in receipts}) != len(
        receipts
    ):
        raise ResearchBindingError("research_bundle_library_inputs_invalid")
    by_id = {item["evidence_id"]: item for item in receipts}
    for item in inputs:
        saved = by_id.get(item.evidence_id)
        if (
            saved is None
            or source.library_scope is None
            or saved["version_id"] not in source.library_scope.selected_version_ids
            or saved["version_id"] != item.source_id.removeprefix("research-library:")
            or saved["excerpt"] != item.excerpt
            or saved["excerpt_hash"] != hashlib.sha256(item.excerpt.encode()).hexdigest()
            or saved["locator_refs"]
            != list(dict.fromkeys(re.findall(r"document://[^;\s\]]+", item.excerpt)))
        ):
            raise ResearchBindingError("research_bundle_library_inputs_invalid")


def _receipt_id(role: str, run_id: str) -> str:
    prefix = {PACK_ROLE: "pack_", TASK_ROLE: "task_", COMPILED_ROLE: "compiled_"}[role]
    return prefix + hashlib.sha256(run_id.encode()).hexdigest()[:32]


def _read_receipt[Model: BaseModel](
    row: RunRoleOutputRecord, role: str, model: type[Model]
) -> Model:
    value = model.model_validate_json(row.payload_json)
    body = _json(value)
    if (
        row.role != role
        or body["run_id"] != row.run_id
        or row.output_id != _receipt_id(role, row.run_id)
        or row.schema_version != body["schema_version"]
        or row.payload_hash != hashlib.sha256(row.payload_json.encode()).hexdigest()
        or row.payload_json != canonical_recipe(body)
    ):
        raise ResearchBindingError("research_bundle_receipt_invalid")
    return value


def _has_execution(session: Session, run_id: str) -> bool:
    return bool(
        session.get(DshSessionLinkRecord, run_id, populate_existing=True)
        or session.get(ResearchResultRecord, run_id, populate_existing=True)
        or session.scalar(select(DshSessionPromptRecord).filter_by(run_id=run_id).limit(1))
        or session.scalar(select(ResearchTraceRecord).filter_by(run_id=run_id).limit(1))
        or session.scalar(
            select(RunRoleOutputRecord)
            .where(
                RunRoleOutputRecord.run_id == run_id,
                RunRoleOutputRecord.role.in_(("research_execution_method", "task_policy")),
            )
            .limit(1)
        )
    )


def has_prepared_bundle_in_session(session: Session, run_id: str) -> bool:
    """Recognize a partial v2 bundle, so missing Task never becomes legacy scope."""
    rows = session.scalars(
        select(RunRoleOutputRecord)
        .where(RunRoleOutputRecord.run_id == run_id, RunRoleOutputRecord.role.in_(BUNDLE_ROLES))
        .execution_options(populate_existing=True)
    ).all()
    return any(
        row.role == COMPILED_ROLE
        or (row.role == PACK_ROLE and row.schema_version == PACK_SCHEMA)
        or (row.role == TASK_ROLE and row.schema_version == TASK_SCHEMA)
        for row in rows
    )


def _bundle_original_json(value: FrozenResearchBundle | LegacyResearchBundle | None) -> str:
    if not isinstance(value, FrozenResearchBundle):
        raise ResearchBindingError("research_bundle_prepared_required")
    return canonical_recipe({
        "pack": _json(value.pack),
        "task": _json(value.task),
        "compiled": _json(value.compiled),
        "recorded_at": value.recorded_at.isoformat(),
    })


def _bundle_original_clone(body: str) -> FrozenResearchBundle:
    value = json.loads(body)
    return FrozenResearchBundle(
        ResearchRequestPackReceiptV2.model_validate_json(canonical_recipe(value["pack"])),
        ResearchTaskReceiptV2.model_validate_json(canonical_recipe(value["task"])),
        ResearchCompiledPolicyReceiptV1.model_validate_json(canonical_recipe(value["compiled"])),
        _utc(datetime.fromisoformat(value["recorded_at"])),
    )


def read_research_bundle_in_session(
    session: Session, run_id: str, *, assessed_at: datetime | None = None
) -> FrozenResearchBundle | LegacyResearchBundle | None:
    """Reuse originals only inside the existing clean physical RO preparation.

    The first successful read and scope exit use the complete original reader.
    Each return has fresh receipt models and checks its own assessment cutoff.
    Execution permissions, generations and writer fences are not reused here.
    """
    from .research_recipe_read_scope import current_recipe_read_scope

    scope = current_recipe_read_scope()
    if scope is None:
        return _read_research_bundle_original_in_session(session, run_id, assessed_at=assessed_at)
    result = scope.completed(
        session,
        ("bundle_original", _read_research_bundle_original_in_session, run_id),
        lambda: _read_research_bundle_original_in_session(session, run_id, assessed_at=assessed_at),
        _bundle_original_json,
        _bundle_original_clone,
        kind="bundle_original",
        cacheable=lambda value: isinstance(value, FrozenResearchBundle),
    )
    if isinstance(result, FrozenResearchBundle) and assessed_at is not None:
        try:
            if result.recorded_at > _utc(assessed_at):
                raise ResearchBindingError("research_bundle_receipt_time_invalid")
        except ResearchBindingError:
            raise
        except (ValueError, KeyError, TypeError) as error:
            raise ResearchBindingError("research_bundle_receipt_invalid") from error
    return result


def _read_research_bundle_original_in_session(
    session: Session, run_id: str, *, assessed_at: datetime | None = None
) -> FrozenResearchBundle | LegacyResearchBundle | None:
    """Read original rows freshly, including current permission and Run identity guards."""
    run = session.get(RunRecord, run_id, populate_existing=True)
    if run is None:
        raise KeyError(run_id)
    rows = {
        row.role: row
        for row in session.scalars(
            select(RunRoleOutputRecord)
            .where(
                RunRoleOutputRecord.run_id == run_id,
                RunRoleOutputRecord.role.in_(BUNDLE_ROLES),
            )
            .execution_options(populate_existing=True)
        ).all()
    }
    if not rows:
        return LegacyResearchBundle(run_id) if _has_execution(session, run_id) else None
    if COMPILED_ROLE not in rows and all(
        row.schema_version
        == {PACK_ROLE: "research-request-pack.v1", TASK_ROLE: "research-task-request.v1"}[role]
        for role, row in rows.items()
    ):
        return LegacyResearchBundle(run_id)
    if set(rows) != set(BUNDLE_ROLES) or any(
        rows[role].schema_version != expected
        for role, expected in (
            (PACK_ROLE, PACK_SCHEMA),
            (TASK_ROLE, TASK_SCHEMA),
            (COMPILED_ROLE, COMPILED_SCHEMA),
        )
    ):
        raise ResearchBindingError("research_bundle_partial_or_mixed")
    try:
        pack = _read_receipt(rows[PACK_ROLE], PACK_ROLE, ResearchRequestPackReceiptV2)
        task = _read_receipt(rows[TASK_ROLE], TASK_ROLE, ResearchTaskReceiptV2)
        compiled = _read_receipt(
            rows[COMPILED_ROLE], COMPILED_ROLE, ResearchCompiledPolicyReceiptV1
        )
        _validate_payloads(pack, task, compiled)
        _validate_current_source(session, run, task)
        recorded = _utc(rows[TASK_ROLE].created_at)
        first_prompt = session.get(DshSessionPromptRecord, (run_id, 1), populate_existing=True)
        if (
            any(_utc(row.created_at) != recorded for row in rows.values())
            or (assessed_at is not None and recorded > _utc(assessed_at))
            or (first_prompt is not None and recorded > _utc(first_prompt.created_at))
        ):
            raise ResearchBindingError("research_bundle_receipt_time_invalid")
        return FrozenResearchBundle(pack, task, compiled, recorded)
    except ResearchBindingError:
        raise
    except (ValueError, KeyError, TypeError) as exc:
        raise ResearchBindingError("research_bundle_receipt_invalid") from exc


def _insert_receipt_in_session(
    session: Session, role: str, receipt: Receipt, recorded_at: datetime
) -> RunRoleOutputRecord:
    """One real insert/flush seam; the caller owns rollback of every preceding row."""
    output_id = _receipt_id(role, receipt.run_id)
    if session.get(RunRoleOutputRecord, output_id, populate_existing=True) is not None:
        raise ResearchBindingError("research_bundle_receipt_id_collision")
    encoded = canonical_recipe(_json(receipt))
    row = RunRoleOutputRecord(
        output_id=output_id,
        run_id=receipt.run_id,
        role=role,
        schema_version=receipt.schema_version,
        payload_json=encoded,
        payload_hash=hashlib.sha256(encoded.encode()).hexdigest(),
        created_at=recorded_at,
    )
    session.add(row)
    session.flush()
    return row


def freeze_research_bundle_in_session(
    session: Session,
    run_id: str,
    pack_receipt: ResearchRequestPackReceiptV2,
    task_receipt: ResearchTaskReceiptV2,
    compiled_receipt: ResearchCompiledPolicyReceiptV1,
    recorded_at: datetime,
    *,
    _probability_selection: KernelProbabilitySelection | None = None,
) -> FrozenResearchBundle:
    """Compare or insert all three rows in a physical caller transaction.

    SQLite cannot reveal the BEGIN mode; Factory takes BEGIN IMMEDIATE before
    its first input read. This function never starts or finishes a transaction.
    """
    connection = session.connection()
    native = connection.connection.driver_connection
    if (
        connection.dialect.name != "sqlite"
        or not isinstance(native, sqlite3.Connection)
        or not native.in_transaction
    ):
        raise ResearchBindingError("research_bundle_caller_write_transaction_required")
    pack = ResearchRequestPackReceiptV2.model_validate_json(pack_receipt.model_dump_json())
    task = ResearchTaskReceiptV2.model_validate_json(task_receipt.model_dump_json())
    compiled = ResearchCompiledPolicyReceiptV1.model_validate_json(
        compiled_receipt.model_dump_json()
    )
    if any(value.run_id != run_id for value in (pack, task, compiled)):
        raise ResearchBindingError("research_bundle_run_identity_mismatch")
    _validate_payloads(pack, task, compiled)
    saved = read_research_bundle_in_session(session, run_id)
    if isinstance(saved, FrozenResearchBundle):
        if saved.pack != pack or saved.task != task or saved.compiled != compiled:
            raise ResearchBindingError("research_bundle_changed")
        # Existing bundles are never filled with a late probability selection.
        # For this new implementation the first-freeze sidecar must already
        # exist; historical recipes retain their original implementation.
        from .research_policy_checker import citation_execution_identity
        from .research_probability_binding import read_probability_materials_in_session

        if saved.compiled.compiled.checker_code_hash == citation_execution_identity()["code_hash"]:
            probability = read_probability_materials_in_session(session, saved).binding
            if (
                _probability_selection is not None
                and probability.selection != _probability_selection
            ):
                raise ResearchBindingError("research_probability_selection_changed")
        elif _probability_selection is not None:
            raise ResearchBindingError("research_probability_legacy_not_upgradeable")
        return saved
    if saved is not None:
        raise ResearchBindingError("research_bundle_legacy_not_upgradeable")
    run = session.get(RunRecord, run_id, populate_existing=True)
    assert run is not None
    if run.status not in {"admitted", "dispatched", "running"}:
        raise ResearchBindingError("research_bundle_missing_before_execution")
    if read_factory_source_in_session(session, run_id) != task.factory_source:
        raise ResearchBindingError("research_bundle_factory_source_mismatch")
    _validate_current_source(session, run, task)
    for item in task.library_inputs:
        version = session.get(
            ResearchDocumentVersionRecord, item.version_id, populate_existing=True
        )
        if version is None or version.original_file_hash != item.original_file_hash:
            raise ResearchBindingError("research_bundle_library_original_hash_mismatch")
    expected = compile_policy(
        ResearchPolicyResource.model_validate(_json(pack.definition.gate_policy_resource)),
        ResearchPolicyResource.model_validate(_json(pack.definition.rubric_policy_resource)),
        [
            ResearchRoleRubricBinding.model_validate(_json(item))
            for item in pack.definition.role_rubric_bindings
        ],
        scope=(ResearchTaskScopeV2 if task.scope.schema_version == "research-task-scope.v2" else ResearchTaskScope).model_validate(_json(task.scope)),
    )
    if _json(expected) != _json(compiled.compiled):
        raise ResearchBindingError("research_bundle_compilation_mismatch")
    # Establish real absence before inserting any of the three bundle rows.
    # The private one-shot finish stays in this physical writer, so a later
    # caller cannot backfill a missing binding on an already-frozen bundle.
    from .research_probability_binding import probability_first_freeze_in_session

    with probability_first_freeze_in_session(session, run_id) as finish_probability:
        for role, value in ((PACK_ROLE, pack), (TASK_ROLE, task), (COMPILED_ROLE, compiled)):
            _insert_receipt_in_session(session, role, value, _utc(recorded_at))
        result = read_research_bundle_in_session(session, run_id)
        if not isinstance(result, FrozenResearchBundle):
            raise ResearchBindingError("research_bundle_insert_incomplete")
        # Private server input only. Ordinary Worker/API/UI callers keep their
        # behavior and freeze explicit missing references by default.
        finish_probability(result, _probability_selection)
    return result

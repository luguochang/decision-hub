"""Staged exact S2 selection; no Host load/execute/consume or model loop."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from packages.kernel.decision_hub_kernel.application.research_binding import (
    FrozenResearchBundle,
    read_research_bundle_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_execution_binding import (
    read_prepared_execution_in_session,
)
from packages.kernel.decision_hub_kernel.persistence.action_records import (
    ResearchSkillUseEventRecord,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    RunRoleOutputRecord,
    as_utc,
    utcnow,
)
from packages.kernel.decision_hub_kernel.persistence.original_preparation import prepare_originals
from packages.kernel.decision_hub_kernel.ports.research_synthesis_protocol import (
    DECISION_OUTPUT_SCHEMA_REF,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.kernel.decision_hub_kernel.ports.market_path import aware_utc, canonical_hash

from . import research_skill_compiler as compiler
from .research_skill_package import SkillPackageService, decoded, encoded, sha

ROLE = "research_skill_selection"


@dataclass(frozen=True)
class SkillExecution:
    run_id: str
    generation: int
    task_request_hash: str
    compiled_task_hash: str
    method_hash: str
    cutoff: datetime
    scope_identity_json: bytes
    allowed_capabilities: frozenset[str]
    turn_kind: str


def read_skill_execution(session: Session, run_id: str, generation: int | None) -> SkillExecution:
    """Production adapter: authenticate originals, not caller identity declarations."""
    prepared = read_prepared_execution_in_session(session, run_id, generation)
    bundle = read_research_bundle_in_session(session, run_id)
    if not isinstance(bundle, FrozenResearchBundle):
        raise ValueError("skill_prepared_task_required")
    request, execution = prepared.execution.request, prepared.execution
    if (
        request.output_schema_ref != DECISION_OUTPUT_SCHEMA_REF
        or request.execution_mode != "live"
        or request.research_mode != "current_decision"
    ):
        raise ValueError("skill_execution_protocol_unavailable")
    cutoff = (
        bundle.request.context_manifest.created_at
        if bundle.request.context_manifest is not None
        else bundle.recorded_at
    )
    identity = {
        "run_id": run_id,
        "dsh_session_id": execution.dsh_session_id,
        "request_hash": execution.execution_request_hash,
        "generation": execution.generation,
        "status": "bound",
        "method_hash": prepared.method.method_hash,
        "execution_mode": "live",
    }
    return SkillExecution(
        run_id,
        execution.generation,
        execution.task_request_hash,
        bundle.compiled.compiled.compiled_hash,
        prepared.method.method_hash,
        aware_utc(cutoff),
        encoded(identity),
        frozenset(request.allowed_capabilities),
        execution.turn_kind,
    )


@dataclass(frozen=True)
class SelectionMaterial:
    intent_json: bytes
    archive_readset: tuple[tuple[str, str], ...]


class SkillSelectionService:
    def __init__(
        self,
        packages: SkillPackageService,
        *,
        baseline_manifests: dict[str, str],
        context_reader: Callable[[Session, SkillExecution], compiler.CompilerContext],
        execution_reader=read_skill_execution,
        originals_preparer=prepare_originals,
        clock=utcnow,
    ):
        self.packages, self.clock = packages, clock
        # Composition owns exact baseline IDs AND payload hashes. Registration is not approval.
        self.baselines = dict(baseline_manifests)
        self.context_reader, self.execution_reader = context_reader, execution_reader
        self.originals_preparer = originals_preparer

    @staticmethod
    def _row(session, run_id):
        return session.scalar(
            select(RunRoleOutputRecord)
            .where(
                RunRoleOutputRecord.run_id == run_id,
                RunRoleOutputRecord.role == ROLE,
            )
            .execution_options(populate_existing=True)
        )

    def prepare(self, run_id: str, version_id: str, *, origin="baseline_registered"):
        if origin != "baseline_registered":
            raise ValueError("skill_qualified_pointer_not_installed")
        return self.originals_preparer(
            self.packages.registry.database,
            lambda session: self._prepare(session, run_id, version_id),
        )

    def _prepare(self, session, run_id, version_id):
        existing = self._row(session, run_id)
        if existing is not None:
            body = self.read_in_session(session, run_id)
            if body["skill_version_id"] != version_id:
                raise ValueError("skill_selection_key_conflict")
            return SelectionMaterial(encoded(body), self._archive_refs(session, body))
        execution = self.execution_reader(session, run_id, None)
        if execution.generation != 1 or execution.turn_kind != "research_round":
            raise ValueError("skill_selection_first_dispatch_required")
        dispatched = session.scalar(
            select(RunRoleOutputRecord.output_id)
            .where(
                RunRoleOutputRecord.run_id == run_id,
                RunRoleOutputRecord.schema_version == "research-provider-input.v1",
            )
            .limit(1)
        )
        if dispatched is not None:
            raise ValueError("skill_selection_after_dispatch")
        package = self.packages.read_in_session(session, version_id)
        version, manifest = package.version, package.manifest
        if self.baselines.get(version_id) != manifest.payload_hash:
            raise ValueError("skill_baseline_not_installed")
        if version["kind"] != "method_workflow":
            raise ValueError("skill_readonly_selection_not_installed")
        known = aware_utc(datetime.fromisoformat(version["first_known_at"]))
        if known > execution.cutoff or execution.cutoff > aware_utc(self.clock()):
            raise ValueError("skill_selection_method_after_cutoff")
        context = self.context_reader(session, execution)
        if (
            context.scope_id != manifest.scope_id
            or context.execution_mode != "live"
            or context.compiler_hash != self.packages.installed.code_hash
            or context.execution_identity_json is None
            or decoded(context.execution_identity_json) != decoded(execution.scope_identity_json)
            or not context.allowed_capabilities.issubset(execution.allowed_capabilities)
        ):
            raise ValueError("skill_selection_execution_mismatch")
        plan = compiler.compile_skill_workflow(package.resources, context)
        value = plan.read()
        if (
            value["workflow_hash"] != version["workflow_hash"]
            or value["required_capability_surface_hash"]
            != version["required_capability_surface_hash"]
        ):
            raise ValueError("skill_selection_dependency_changed")
        # Hashes of actual installed component manifests remain pinned by package identity.
        actual_components = {
            x.manifest.version_id: x.manifest.payload_hash for x in context.components
        }
        if any(
            actual_components.get(x["version_id"]) != x["payload_hash"]
            for x in version["component_manifest_refs"]
        ):
            raise ValueError("skill_selection_component_changed")
        digest, reference = self.packages.archive.put(plan.plan_json.encode())
        if digest != plan.plan_hash:
            raise ValueError("skill_plan_encoding_mismatch")
        key = canonical_hash([run_id, ROLE])[:40]
        body = {
            "schema_version": "research-skill-selection.v1",
            "selection_id": "skill_select_" + key,
            "use_id": "skill_use_" + key,
            "run_id": run_id,
            "generation": 1,
            "task_request_hash": execution.task_request_hash,
            "compiled_task_hash": execution.compiled_task_hash,
            "method_binding_hash": execution.method_hash,
            "scope_id": context.scope_id,
            "horizon": "24h",
            "producer_cutoff_at": execution.cutoff.isoformat(),
            "skill_version_id": version_id,
            "version_hash": canonical_hash(version),
            "package_hash": manifest.resource_hash,
            "workflow_hash": version["workflow_hash"],
            "compiled_plan_ref": reference,
            "compiled_plan_hash": plan.plan_hash,
            "required_capability_surface_hash": version["required_capability_surface_hash"],
            "execution_catalog_hash": value["catalog_hash"],
            "selection_origin": "baseline_registered",
            "component_manifest_ref": {
                "version_id": version_id,
                "payload_hash": manifest.payload_hash,
            },
            "activation_audit_ref": None,
            "pointer_generation": None,
            "method_first_known_at": known.isoformat(),
        }
        return SelectionMaterial(
            encoded(body),
            tuple(
                [(item["body_ref"], item["body_hash"]) for item in version["files"]]
                + [
                    (x["controlled_routes_ref"], x["controlled_routes_hash"])
                    for x in version["capability_surfaces"]
                ]
                + [(reference, digest)]
            ),
        )

    def _archive_refs(self, session, body):
        # Package file refs are independently read by read_in_session; track all for writer recheck.
        package = self.packages.read_in_session(session, body["skill_version_id"])
        version = package.version
        return tuple(
            [(x["body_ref"], x["body_hash"]) for x in version["files"]]
            + [
                (x["controlled_routes_ref"], x["controlled_routes_hash"])
                for x in version["capability_surfaces"]
            ]
            + [(body["compiled_plan_ref"], body["compiled_plan_hash"])]
        )

    def freeze_in_session(self, session: Session, prepared) -> dict:
        """Caller owns the short writer; no full authentication/compiler/network here."""
        prepared.verify(session)
        material = prepared.value
        self.packages.installed.verify()
        for reference, digest in material.archive_readset:
            if sha(self.packages.archive.read(reference)) != digest:
                raise ValueError("skill_selection_archive_changed")
        body = decoded(material.intent_json)
        row = self._row(session, body["run_id"])
        if row is not None:
            if row.payload_json.encode() != material.intent_json or row.payload_hash != sha(
                material.intent_json
            ):
                raise ValueError("skill_selection_key_conflict")
            return body
        if "selected_at" in body:
            raise ValueError("skill_selection_original_deleted")
        now = aware_utc(self.clock())
        if now < datetime.fromisoformat(body["producer_cutoff_at"]):
            raise ValueError("skill_selection_time_invalid")
        body["selected_at"] = now.isoformat()
        self.packages.contracts.check("selection_v1", body)
        payload, digest = encoded(body), canonical_hash(body)
        reference = {
            "output_id": body["selection_id"],
            "role": ROLE,
            "schema_version": body["schema_version"],
            "payload_hash": digest,
            "recorded_at": now.isoformat(),
        }
        event = {
            "schema_version": "research-skill-use-event.v1",
            "event_id": body["use_id"] + "_selected",
            "use_id": body["use_id"],
            "run_id": body["run_id"],
            "generation": 1,
            "root_session_id": None,
            "execution_session_id": None,
            "skill_version_id": body["skill_version_id"],
            "package_hash": body["package_hash"],
            "workflow_hash": body["workflow_hash"],
            "method_binding_hash": body["method_binding_hash"],
            "stage": "selected",
            "node_id": "",
            "stage_request_id": body["selection_id"],
            "parent_event": None,
            "original": reference,
            "first_known_at": now.isoformat(),
            "details": {
                "stage": "selected",
                "selection_ref": reference,
                "selection_hash": digest,
                "execution_catalog_hash": body["execution_catalog_hash"],
                "required_capability_surface_hash": body["required_capability_surface_hash"],
            },
        }
        self.packages.contracts.check("use_event_v1", event)
        session.add(
            RunRoleOutputRecord(
                output_id=body["selection_id"],
                run_id=body["run_id"],
                role=ROLE,
                schema_version=body["schema_version"],
                payload_json=payload.decode(),
                payload_hash=digest,
                created_at=now,
            )
        )
        columns = {
            key: event[key]
            for key in (
                "event_id",
                "use_id",
                "run_id",
                "generation",
                "root_session_id",
                "execution_session_id",
                "skill_version_id",
                "package_hash",
                "workflow_hash",
                "method_binding_hash",
                "stage",
                "node_id",
                "stage_request_id",
            )
        }
        session.add(
            ResearchSkillUseEventRecord(
                **columns,
                parent_event_ref=None,
                original_ref=encoded(reference).decode(),
                original_hash=digest,
                first_known_at=now,
                payload_json=encoded(event).decode(),
                payload_hash=canonical_hash(event),
            )
        )
        session.flush()
        prepared.verify_after_writes(session)
        return body

    def read_in_session(self, session: Session, run_id: str) -> dict:
        row = self._row(session, run_id)
        if row is None:
            raise ValueError("legacy_skill_unversioned")
        body = decoded(row.payload_json)
        self.packages.contracts.check("selection_v1", body)
        if (
            body["run_id"] != run_id
            or row.output_id != body["selection_id"]
            or row.schema_version != body["schema_version"]
            or row.payload_hash != canonical_hash(body)
            or row.payload_json.encode() != encoded(body)
            or as_utc(row.created_at) != datetime.fromisoformat(body["selected_at"])
            or body["selection_origin"] != "baseline_registered"
        ):
            raise ValueError("skill_selection_original_invalid")
        execution = self.execution_reader(session, run_id, body["generation"])
        if (
            execution.task_request_hash != body["task_request_hash"]
            or execution.compiled_task_hash != body["compiled_task_hash"]
            or execution.method_hash != body["method_binding_hash"]
            or execution.cutoff != datetime.fromisoformat(body["producer_cutoff_at"])
        ):
            raise ValueError("skill_selection_execution_mismatch")
        if (
            not execution.cutoff
            <= datetime.fromisoformat(body["selected_at"])
            <= aware_utc(self.clock())
        ):
            raise ValueError("skill_selection_time_invalid")
        package = self.packages.read_in_session(session, body["skill_version_id"])
        version, manifest = package.version, package.manifest
        if (
            self.baselines.get(manifest.version_id) != manifest.payload_hash
            or body["component_manifest_ref"]
            != {"version_id": manifest.version_id, "payload_hash": manifest.payload_hash}
            or canonical_hash(version) != body["version_hash"]
            or manifest.resource_hash != body["package_hash"]
            or manifest.scope_id != body["scope_id"]
            or version["workflow_hash"] != body["workflow_hash"]
            or version["first_known_at"] != body["method_first_known_at"]
            or datetime.fromisoformat(body["method_first_known_at"]) > execution.cutoff
        ):
            raise ValueError("skill_selection_package_changed")
        raw = self.packages.archive.read(body["compiled_plan_ref"])
        plan = compiler.CompiledWorkflow(raw.decode(), body["compiled_plan_hash"]).read()
        if (
            plan["catalog_hash"] != body["execution_catalog_hash"]
            or plan["execution_identity"] != decoded(execution.scope_identity_json)
            or plan["workflow_hash"] != body["workflow_hash"]
            or plan["required_capability_surface_hash"] != body["required_capability_surface_hash"]
        ):
            raise ValueError("skill_selection_plan_changed")
        event = session.get(
            ResearchSkillUseEventRecord, body["use_id"] + "_selected", populate_existing=True
        )
        if event is None:
            raise ValueError("skill_selected_event_missing")
        value = decoded(event.payload_json)
        self.packages.contracts.check("use_event_v1", value)
        reference = {
            "output_id": row.output_id,
            "role": row.role,
            "schema_version": row.schema_version,
            "payload_hash": row.payload_hash,
            "recorded_at": as_utc(row.created_at).isoformat(),
        }
        if (
            canonical_hash(value) != event.payload_hash
            or value["original"] != reference
            or value["details"]["selection_ref"] != reference
            or value["details"]["selection_hash"] != row.payload_hash
            or event.original_hash != row.payload_hash
            or decoded(event.original_ref) != reference
            or event.parent_event_ref is not None
            or value["parent_event"] is not None
            or as_utc(event.first_known_at) != datetime.fromisoformat(value["first_known_at"])
            or value["first_known_at"] != body["selected_at"]
        ):
            raise ValueError("skill_selected_event_invalid")
        for key in (
            "event_id",
            "use_id",
            "run_id",
            "generation",
            "root_session_id",
            "execution_session_id",
            "skill_version_id",
            "package_hash",
            "workflow_hash",
            "method_binding_hash",
            "stage",
            "node_id",
            "stage_request_id",
        ):
            if getattr(event, key) != value[key]:
                raise ValueError("skill_selected_event_identity_invalid")
        expected = {
            key: body[key]
            for key in (
                "use_id",
                "run_id",
                "generation",
                "skill_version_id",
                "package_hash",
                "workflow_hash",
                "method_binding_hash",
            )
        }
        expected.update(
            stage="selected",
            node_id="",
            stage_request_id=body["selection_id"],
            root_session_id=None,
            execution_session_id=None,
        )
        if (
            any(value[key] != item for key, item in expected.items())
            or value["details"]["execution_catalog_hash"] != body["execution_catalog_hash"]
            or value["details"]["required_capability_surface_hash"]
            != body["required_capability_surface_hash"]
        ):
            raise ValueError("skill_selected_event_binding_invalid")
        return body

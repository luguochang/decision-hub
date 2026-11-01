"""Exact same-version repair bindings; no re-selection or carried stage proof."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select

from ..persistence.action_records import ResearchSkillUseEventRecord
from ..persistence.db import DshSessionLinkRecord, RunRecord, RunRoleOutputRecord, as_utc
from ..ports.market_path import aware_utc, canonical_hash
from . import research_skill_compiler as compiler
from .research_skill_package import decoded, encoded, sha

ROLE = "research_skill_generation"
POLICY = "same_version_reexecute.v1"


def row_ref(row):
    return dict(
        output_id=row.output_id,
        role=row.role,
        schema_version=row.schema_version,
        payload_hash=row.payload_hash,
        recorded_at=as_utc(row.created_at).isoformat(),
    )


@dataclass(frozen=True)
class GenerationAuthority:
    prepared_ref_json: bytes
    prepared_at: datetime
    deadline_at: datetime
    scope_id: str
    horizon: str
    target_json: bytes


def read_generation_authority(session, execution):
    """Authenticate prepared scope/catalog; no candidate or client scope input."""
    from ..decision.action_research_policy import research_action_catalog
    from .action_synthesis_preflight import frozen_prompt_catalog
    from .dsh_sessions import execution_request_role
    from .research_execution_binding import read_prepared_execution_in_session
    from .research_run_scope import read_run_spot_admission_in_session

    saved = read_prepared_execution_in_session(session, execution.run_id, execution.generation)
    admission = read_run_spot_admission_in_session(session, execution.run_id)
    row = session.scalar(
        select(RunRoleOutputRecord).where(
            RunRoleOutputRecord.run_id == execution.run_id,
            RunRoleOutputRecord.role == execution_request_role(execution.generation),
        )
    )
    if (
        admission is None
        or row is None
        or (
            saved.execution.task_request_hash != execution.task_request_hash
            or saved.execution.method_hash != execution.method_hash
            or canonical_hash(decoded(row.payload_json)) != row.payload_hash
        )
    ):
        raise ValueError("skill_generation_prepared_original_missing")
    if execution.generation > 1:
        from .research_skill_repair_policy import prompt_policy_reference, read_repair_policy_in_session
        reference = prompt_policy_reference(saved.prompt.prompt)
        if reference is None:
            raise ValueError("skill_generation_reexecution_policy_required")
        read_repair_policy_in_session(session, run_id=execution.run_id, generation=execution.generation,
            reference=reference, request=saved.execution.request)
    catalog = frozen_prompt_catalog(
        saved.prompt.prompt, research_action_catalog(admission.instrument)
    )
    if catalog["scope_id"] != admission.scope.scope_id:
        raise ValueError("skill_generation_scope_mismatch")
    deadline = aware_utc(saved.method.definition.deadline_at)
    if aware_utc(saved.execution.request.deadline_at) > deadline:
        raise ValueError("skill_generation_deadline_expanded")
    return GenerationAuthority(
        encoded(row_ref(row)),
        aware_utc(saved.execution.recorded_at),
        min(deadline, aware_utc(saved.execution.request.deadline_at)),
        admission.scope.scope_id,
        "24h",
        encoded(catalog["targets"]["24h"]),
    )


def require_current_generation(session, execution, now):
    """Mutable creation authority only; historical reads never require a live lease."""
    run = session.get(RunRecord, execution.run_id, populate_existing=True)
    link = session.get(DshSessionLinkRecord, execution.run_id, populate_existing=True)
    identity = decoded(execution.scope_identity_json)
    if (
        run is None
        or link is None
        or run.status != "running"
        or not run.lease_owner
        or run.lease_expires_at is None
        or as_utc(run.lease_expires_at) <= now
        or link.generation != execution.generation
        or link.dsh_session_id != identity["dsh_session_id"]
        or link.state in {"cancelled", "failed", "completed"}
    ):
        raise ValueError("skill_generation_not_current_active")
    # Existing exact binding replay is handled before this creation-only test.
    rows = session.scalars(
        select(RunRoleOutputRecord).where(
            RunRoleOutputRecord.run_id == execution.run_id,
            RunRoleOutputRecord.schema_version == "research-provider-input.v1",
        )
    ).all()
    for row in rows:
        body = decoded(row.payload_json)
        submit = body.get("submit", body)
        if submit.get("generation") == execution.generation:
            raise ValueError("skill_generation_after_dispatch")


@dataclass(frozen=True)
class GenerationMaterial:
    intent_json: bytes
    archive_readset: tuple[tuple[str, str], ...]
    execution: object | None = None


class SkillGenerationService:
    def __init__(
        self,
        selections,
        *,
        authority_reader=read_generation_authority,
        current_guard=require_current_generation,
    ):
        self.selections = selections
        self.authority_reader, self.current_guard = authority_reader, current_guard

    def with_selections(self, selections):
        # Publication installs a tracking package archive in a shallow service copy.
        return type(self)(
            selections, authority_reader=self.authority_reader, current_guard=self.current_guard
        )

    @property
    def packages(self):
        return self.selections.packages

    @staticmethod
    def _key(run_id, generation):
        return "skill_gen_" + canonical_hash([run_id, ROLE, generation])[:40]

    def _row(self, session, run_id, generation):
        return session.get(
            RunRoleOutputRecord, self._key(run_id, generation), populate_existing=True
        )

    def prepare(self, run_id, generation):
        if type(generation) is not int or generation <= 1:
            raise ValueError("skill_generation_repair_required")
        return self.selections.originals_preparer(
            self.packages.registry.database,
            lambda session: self._prepare(session, run_id, generation),
        )

    def _origins(self, session, run_id, generation):
        original = self.selections.read_in_session(session, run_id)
        first = self.selections.execution_reader(session, run_id, original["generation"])
        execution = self.selections.execution_reader(session, run_id, generation)
        root_auth = self.authority_reader(session, first)
        authority = self.authority_reader(session, execution)
        if (
            execution.generation != generation
            or generation <= original["generation"]
            or execution.turn_kind != "synthesis_repair"
            or execution.task_request_hash != original["task_request_hash"]
            or execution.compiled_task_hash != original["compiled_task_hash"]
            or execution.method_hash != original["method_binding_hash"]
            or execution.cutoff != first.cutoff
            or not execution.allowed_capabilities.issubset(first.allowed_capabilities)
            or (authority.scope_id, authority.horizon, authority.target_json)
            != (root_auth.scope_id, root_auth.horizon, root_auth.target_json)
            or authority.scope_id != original["scope_id"]
            or authority.horizon != original["horizon"]
            or authority.deadline_at > root_auth.deadline_at
        ):
            raise ValueError("skill_generation_original_identity_changed")
        return original, execution, authority

    def _window(self, session, original, authority):
        end = authority.deadline_at
        if original["selection_origin"] == "trial_registered":
            admission = self.selections.admission.prepare_receipt(original["trial_receipt"]["ref"])
            admission.verify(session)
            if (
                admission.receipt.payload_hash != original["trial_receipt"]["hash"]
                or admission.protocol.target.model_dump(mode="json")
                != decoded(authority.target_json)
                or admission.protocol.scope_id != authority.scope_id
                or admission.protocol.horizon != authority.horizon
            ):
                raise ValueError("skill_generation_trial_identity_changed")
            end = min(end, aware_utc(admission.protocol.end_at))
        return end

    def _creation(self, session, original, execution, authority):
        now = aware_utc(self.selections.clock())
        self.current_guard(session, execution, now)
        end = self._window(session, original, authority)
        if not (
            datetime.fromisoformat(original["selected_at"]) <= authority.prepared_at <= now < end
        ):
            raise ValueError("skill_generation_window_elapsed")
        return now, end

    def _prepare(self, session, run_id, generation):
        if self._row(session, run_id, generation) is not None:
            body = self.read_execution_binding_in_session(session, run_id, generation)
            return GenerationMaterial(encoded(body), self.selections._archive_refs(session, body))
        original, execution, authority = self._origins(session, run_id, generation)
        _, end = self._creation(session, original, execution, authority)
        package = self.packages.read_in_session(session, original["skill_version_id"])
        context = self.selections.context_reader(session, execution)
        if (
            context.scope_id != original["scope_id"]
            or context.execution_mode != "live"
            or context.compiler_hash != self.packages.installed.code_hash
            or context.execution_identity_json != execution.scope_identity_json
            or not context.allowed_capabilities.issubset(execution.allowed_capabilities)
        ):
            raise ValueError("skill_generation_catalog_identity_changed")
        plan = compiler.compile_skill_workflow(package.resources, context)
        value = plan.read()
        component_refs = {
            item.manifest.version_id: item.manifest.payload_hash for item in context.components
        }
        if (
            value["workflow_hash"] != original["workflow_hash"]
            or value["required_capability_surface_hash"]
            != original["required_capability_surface_hash"]
            or any(
                component_refs.get(item["version_id"]) != item["payload_hash"]
                for item in package.version["component_manifest_refs"]
            )
        ):
            raise ValueError("skill_generation_dependency_changed")
        digest, reference = self.packages.archive.put(plan.plan_json.encode())
        if digest != plan.plan_hash:
            raise ValueError("skill_generation_plan_encoding_mismatch")
        original_row = self.selections._row(session, run_id)
        body = dict(original)
        body.pop("selected_at")
        body.update(
            schema_version=("research-skill-generation-binding.v2"
                            if original.get("delivery_mode") == "selected_package_provider_input.v1"
                            else "research-skill-generation-binding.v1"),
            binding_id=self._key(run_id, generation),
            continuation_policy=POLICY,
            original_selection_ref=row_ref(original_row),
            generation=generation,
            use_id="skill_use_" + canonical_hash([original["selection_id"], generation])[:40],
            prepared_execution_ref=decoded(authority.prepared_ref_json),
            prepared_at=authority.prepared_at.isoformat(),
            binding_deadline_at=end.isoformat(),
            target=decoded(authority.target_json),
            root_session_id=decoded(execution.scope_identity_json)["dsh_session_id"],
            execution_request_hash=decoded(execution.scope_identity_json)["request_hash"],
            compiled_plan_ref=reference,
            compiled_plan_hash=plan.plan_hash,
            execution_catalog_hash=value["catalog_hash"],
        )
        refs = tuple((f["body_ref"], f["body_hash"]) for f in package.version["files"])
        refs += tuple(
            (f["controlled_routes_ref"], f["controlled_routes_hash"])
            for f in package.version["capability_surfaces"]
        )
        return GenerationMaterial(encoded(body), refs + ((reference, digest),), execution)

    def _event(self, body, reference):
        return dict(
            schema_version="research-skill-use-event.v1",
            event_id=body["use_id"] + "_selected",
            **{
                k: body[k]
                for k in (
                    "use_id",
                    "run_id",
                    "generation",
                    "skill_version_id",
                    "package_hash",
                    "workflow_hash",
                    "method_binding_hash",
                )
            },
            root_session_id=body["root_session_id"],
            execution_session_id=body["root_session_id"],
            stage="selected",
            node_id="",
            stage_request_id=body["binding_id"],
            parent_event=None,
            original=reference,
            first_known_at=body["selected_at"],
            details=dict(
                stage="selected",
                selection_ref=reference,
                selection_hash=reference["payload_hash"],
                execution_catalog_hash=body["execution_catalog_hash"],
                required_capability_surface_hash=body["required_capability_surface_hash"],
            ),
        )

    def freeze_in_session(self, session, prepared):
        prepared.verify(session)
        body = decoded(prepared.value.intent_json)
        row = self._row(session, body["run_id"], body["generation"])
        if row is not None:
            if (
                row.payload_json.encode() != prepared.value.intent_json
                or row.payload_hash != canonical_hash(body)
            ):
                raise ValueError("skill_generation_key_conflict")
            return body
        if "selected_at" in body:
            raise ValueError("skill_generation_original_deleted")
        # Do not rerun complete authentication/compiler under the writer. Source rows
        # were captured by prepare_originals; re-read only mutable lease/link/time.
        execution = prepared.value.execution
        if execution is None:
            raise ValueError("skill_generation_prepared_execution_required")
        now = aware_utc(self.selections.clock())
        self.current_guard(session, execution, now)
        if (
            not datetime.fromisoformat(body["prepared_at"])
            <= now
            < datetime.fromisoformat(body["binding_deadline_at"])
        ):
            raise ValueError("skill_generation_writer_window_elapsed")
        self.packages.installed.verify()
        for ref, digest in prepared.value.archive_readset:
            if sha(self.packages.archive.read(ref)) != digest:
                raise ValueError("skill_generation_archive_changed")
        body["selected_at"] = now.isoformat()
        self.packages.contracts.check(
            "generation_binding_v2"
            if body.get("schema_version") == "research-skill-generation-binding.v2"
            else "generation_binding_v1", body
        )
        payload = encoded(body)
        reference = dict(
            output_id=body["binding_id"],
            role=ROLE + ":" + str(body["generation"]),
            schema_version=body["schema_version"],
            payload_hash=sha(payload),
            recorded_at=body["selected_at"],
        )
        event = self._event(body, reference)
        self.packages.contracts.check("use_event_v1", event)
        session.add(
            RunRoleOutputRecord(
                output_id=body["binding_id"],
                run_id=body["run_id"],
                role=reference["role"],
                schema_version=body["schema_version"],
                payload_json=payload.decode(),
                payload_hash=sha(payload),
                created_at=now,
            )
        )
        columns = (
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
        session.add(
            ResearchSkillUseEventRecord(
                **{k: event[k] for k in columns},
                parent_event_ref=None,
                original_ref=encoded(reference).decode(),
                original_hash=reference["payload_hash"],
                first_known_at=now,
                payload_json=encoded(event).decode(),
                payload_hash=canonical_hash(event),
            )
        )
        session.flush()
        prepared.verify_after_writes(session)
        return body

    def read_execution_binding_in_session(self, session, run_id, generation):
        if type(generation) is not int or generation < 1:
            raise ValueError("skill_generation_invalid")
        if generation == 1:
            return self.selections.read_in_session(session, run_id)
        row = self._row(session, run_id, generation)
        if row is None:
            raise ValueError("skill_generation_binding_missing")
        body = decoded(row.payload_json)
        self.packages.contracts.check(
            "generation_binding_v2"
            if body.get("schema_version") == "research-skill-generation-binding.v2"
            else "generation_binding_v1", body
        )
        original, execution, authority = self._origins(session, run_id, generation)
        original_row = self.selections._row(session, run_id)
        stable = {
            k: v
            for k, v in original.items()
            if k
            not in {
                "schema_version",
                "generation",
                "use_id",
                "compiled_plan_ref",
                "compiled_plan_hash",
                "execution_catalog_hash",
                "selected_at",
            }
        }
        identity = decoded(execution.scope_identity_json)
        if (
            any(body.get(k) != v for k, v in stable.items())
            or body["schema_version"] != (
                "research-skill-generation-binding.v2"
                if original.get("delivery_mode") == "selected_package_provider_input.v1"
                else "research-skill-generation-binding.v1"
            )
            or body["binding_id"] != self._key(run_id, generation)
            or body["generation"] != generation
            or body["run_id"] != run_id
            or row.role != ROLE + ":" + str(generation)
            or row.schema_version != body["schema_version"]
            or row.payload_hash != canonical_hash(body)
            or row.payload_json.encode() != encoded(body)
            or as_utc(row.created_at) != datetime.fromisoformat(body["selected_at"])
            or body["original_selection_ref"] != row_ref(original_row)
            or body["use_id"]
            != "skill_use_" + canonical_hash([original["selection_id"], generation])[:40]
            or body["prepared_execution_ref"] != decoded(authority.prepared_ref_json)
            or body["prepared_at"] != authority.prepared_at.isoformat()
            or body["target"] != decoded(authority.target_json)
            or body["root_session_id"] != identity["dsh_session_id"]
            or body["execution_request_hash"] != identity["request_hash"]
            or datetime.fromisoformat(body["binding_deadline_at"])
            != self._window(session, original, authority)
            or not authority.prepared_at
            <= datetime.fromisoformat(body["selected_at"])
            < datetime.fromisoformat(body["binding_deadline_at"])
            or datetime.fromisoformat(body["selected_at"]) > aware_utc(self.selections.clock())
        ):
            raise ValueError("skill_generation_original_invalid")
        raw = self.packages.archive.read(body["compiled_plan_ref"])
        plan = compiler.CompiledWorkflow(raw.decode(), body["compiled_plan_hash"]).read()
        if (
            plan["execution_identity"] != identity
            or plan["catalog_hash"] != body["execution_catalog_hash"]
            or plan["workflow_hash"] != body["workflow_hash"]
            or plan["required_capability_surface_hash"] != body["required_capability_surface_hash"]
        ):
            raise ValueError("skill_generation_plan_changed")
        event = session.get(
            ResearchSkillUseEventRecord, body["use_id"] + "_selected", populate_existing=True
        )
        expected = self._event(body, row_ref(row))
        if (
            event is None
            or event.payload_json.encode() != encoded(expected)
            or event.payload_hash != canonical_hash(expected)
        ):
            raise ValueError("skill_generation_selected_event_invalid")
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
            if getattr(event, key) != expected[key]:
                raise ValueError("skill_generation_selected_columns_invalid")
        if (
            event.parent_event_ref is not None
            or decoded(event.original_ref) != row_ref(row)
            or event.original_hash != row.payload_hash
            or as_utc(event.first_known_at) != as_utc(row.created_at)
        ):
            raise ValueError("skill_generation_selected_columns_invalid")
        return body

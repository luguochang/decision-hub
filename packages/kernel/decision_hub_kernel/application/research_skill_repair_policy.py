"""Server-frozen permission for a bounded same-version skill repair generation.

The receipt precedes preparation. It never opens the Gateway acquisition window
and never certifies a tool execution or a financial result.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..decision.research_loop import research_acquisition_close_at
from ..persistence.db import DshSessionLinkRecord, RunRecord, RunRoleOutputRecord, as_utc
from ..ports.market_path import aware_utc, canonical_hash
from ..ports.research_synthesis_protocol import DECISION_OUTPUT_SCHEMA_REF
from .research_execution_binding import read_prepared_execution_in_session
from .provider_input_receipts import row_reference
from .research_skill_generation import row_ref
from .research_skill_package import decoded, encoded, sha

ROLE = "research_skill_repair_policy"
SCHEMA = "research-skill-repair-policy.v1"
MODE = "same_version_reexecute.v1"
LATE_MODE = "same_version_original_read.v1"
LATE_TOOLS = ("decision_hub_skill", "decision_hub_synthesis_submit", "subagent_reviewer")
BEGIN = "BEGIN_FROZEN_SKILL_REPAIR_POLICY_REF"
END = "END_FROZEN_SKILL_REPAIR_POLICY_REF"
TOOLS = (
    "decision_hub_skill",
    "decision_hub_research",
    "decision_hub_synthesis_submit",
    "subagent_reviewer",
)


def policy_key(run_id, generation):
    return "skill_repair_" + canonical_hash([run_id, ROLE, generation])[:40]


def _row(session, run_id, generation):
    return session.get(RunRoleOutputRecord, policy_key(run_id, generation), populate_existing=True)


def prompt_policy_reference(prompt):
    """The exact product segment is reconstructed by the runtime reader."""
    lines = prompt.split("\n")
    if BEGIN not in lines and END not in lines:
        return None
    if lines.count(BEGIN) != 1 or lines.count(END) != 1:
        raise ValueError("skill_repair_policy_prompt_invalid")
    i = lines.index(BEGIN)
    if i + 2 >= len(lines) or lines[i + 2] != END:
        raise ValueError("skill_repair_policy_prompt_invalid")
    value = decoded(lines[i + 1])
    if not isinstance(value, dict):
        raise ValueError("skill_repair_policy_prompt_invalid")
    return value


def _strict_row(row, run_id, generation):
    if row is None:
        raise ValueError("skill_repair_policy_missing")
    body = decoded(row.payload_json)
    fields = {
        "schema_version",
        "policy",
        "run_id",
        "source_generation",
        "generation",
        "selection_ref",
        "previous_prepared_ref",
        "task_request_hash",
        "method_hash",
        "request_hash",
        "skill_version_id",
        "package_hash",
        "scope_id",
        "horizon",
        "target",
        "original_deadline_at",
        "binding_deadline_at",
        "acquisition_close_at",
        "authorized_at",
        "max_tool_calls",
        "tool_calls_before",
        "allowed_tools",
        "repair_errors_hash",
        "repair_evidence_ids_hash",
        "allowed_capabilities",
    }
    if isinstance(body, dict) and body.get("policy") == LATE_MODE:
        fields.add("original_source")
    if (
        not isinstance(body, dict)
        or set(body) != fields
        or body["schema_version"] != SCHEMA
        or body["policy"] not in (MODE, LATE_MODE)
        or body["run_id"] != run_id
        or type(generation) is not int
        or generation <= 1
        or body["generation"] != generation
        or body["source_generation"] != generation - 1
        or row.output_id != policy_key(run_id, generation)
        or row.run_id != run_id
        or row.role != f"{ROLE}:{generation}"
        or row.schema_version != SCHEMA
        or row.payload_json.encode() != encoded(body)
        or row.payload_hash != canonical_hash(body)
        or row.created_at is None
        or as_utc(row.created_at) != aware_utc(datetime.fromisoformat(body["authorized_at"]))
        or body["allowed_tools"] != list(LATE_TOOLS if body["policy"] == LATE_MODE else TOOLS)
        or type(body["source_generation"]) is not int
        or type(body["max_tool_calls"]) is not int
        or type(body["tool_calls_before"]) is not int
    ):
        raise ValueError("skill_repair_policy_original_invalid")
    return body


def _prepared_authority(session, run_id, generation):
    from .research_skill_generation import read_generation_authority
    from .research_skill_selection import read_skill_execution

    return read_generation_authority(session, read_skill_execution(session, run_id, generation))


def read_repair_policy_in_session(session, *, run_id, generation, reference, request):
    """Read the saved policy and its original rows, never caller-supplied permission.

    Full package/selection authentication is performed before policy creation and
    again by generation binding. This reader also authenticates the previous
    prepared receipt with the existing Kernel reader; it never reads a future
    generation or recursively asks the new binding for authority.
    """
    row = _row(session, run_id, generation)
    body = _strict_row(row, run_id, generation)
    if row_ref(row) != reference:
        raise ValueError("skill_repair_policy_reference_changed")
    previous = read_prepared_execution_in_session(session, run_id, generation - 1)
    authority = _prepared_authority(session, run_id, generation - 1)
    selection = session.get(
        RunRoleOutputRecord, body["selection_ref"]["output_id"], populate_existing=True
    )
    prior = session.get(
        RunRoleOutputRecord, body["previous_prepared_ref"]["output_id"], populate_existing=True
    )
    value = decoded(selection.payload_json) if selection is not None else {}
    if (
        request.output_schema_ref != DECISION_OUTPUT_SCHEMA_REF
        or request.research_mode != "current_decision"
        or request.execution_mode != "live"
        or selection is None
        or prior is None
        or selection.run_id != run_id
        or selection.role != "research_skill_selection"
        or row_ref(selection) != body["selection_ref"]
        or selection.payload_json.encode() != encoded(value)
        or selection.payload_hash != canonical_hash(value)
        or prior.run_id != run_id
        or row_ref(prior) != body["previous_prepared_ref"]
        or prior.role != f"dsh_execution_request:{generation - 1}"
        or prior.payload_hash != canonical_hash(decoded(prior.payload_json))
        or previous.execution.generation != generation - 1
        or previous.execution.task_request_hash != body["task_request_hash"]
        or previous.method.method_hash != body["method_hash"]
        or body["previous_prepared_ref"] != decoded(authority.prepared_ref_json)
        or body["scope_id"] != authority.scope_id
        or body["horizon"] != authority.horizon
        or body["target"] != decoded(authority.target_json)
        or datetime.fromisoformat(body["binding_deadline_at"]) > authority.deadline_at
        or canonical_hash(request.model_dump(mode="json")) != body["request_hash"]
        or request.model_dump(mode="json") != previous.execution.request.model_dump(mode="json")
        or body["original_deadline_at"]
        != aware_utc(previous.method.definition.deadline_at).isoformat()
        or body["acquisition_close_at"] != research_acquisition_close_at(request).isoformat()
        or body["max_tool_calls"] != previous.method.definition.execution_budget.max_tool_calls
        or body["allowed_capabilities"] != sorted(request.allowed_capabilities)
        or not 0 <= body["tool_calls_before"] < body["max_tool_calls"]
        or any(
            body[key] != value[key]
            for key in ("skill_version_id", "package_hash", "scope_id", "horizon")
        )
        or not aware_utc(previous.execution.recorded_at)
        <= aware_utc(datetime.fromisoformat(body["authorized_at"]))
        < min(
            aware_utc(datetime.fromisoformat(body["original_deadline_at"])),
            aware_utc(datetime.fromisoformat(body["binding_deadline_at"])),
            (research_acquisition_close_at(request) if body["policy"] == MODE
             else aware_utc(request.deadline_at)),
        )
    ):
        raise ValueError("skill_repair_policy_identity_changed")
    if body["policy"] == LATE_MODE:
        source = body["original_source"]
        if (not isinstance(source, dict) or source.get("protocol") != "same-run-spot-original-read.v1"
                or source.get("run_id") != run_id or source.get("source_generation") != generation - 1
                or source.get("acquisition_cutoff_at") != body["acquisition_close_at"]
                or datetime.fromisoformat(body["authorized_at"]) < research_acquisition_close_at(request)):
            raise ValueError("skill_repair_original_source_invalid")
        for key in ("provider_emission_ref", "adapter_ref"):
            reference = source[key]
            original = session.get(RunRoleOutputRecord, reference["output_id"], populate_existing=True)
            if (original is None or original.run_id != run_id or row_reference(original).model_dump(mode="json") != reference
                    or sha(original.payload_json.encode()) != original.payload_hash):
                raise ValueError("skill_repair_original_source_changed")
    return body


@dataclass(frozen=True)
class RepairPolicyMaterial:
    intent_json: bytes
    archive_readset: tuple[tuple[str, str], ...]
    lease_owner: str
    market_readset: tuple[tuple[str, str], ...] = ()


class SkillRepairPolicyService:
    def __init__(self, bindings, nodes=None):
        self.nodes = nodes
        self.bindings = bindings
        self.selections = bindings.selections
        self.packages = self.selections.packages

    def _live(self, session, body, owner):
        now = aware_utc(self.selections.clock())
        run = session.get(RunRecord, body["run_id"], populate_existing=True)
        link = session.get(DshSessionLinkRecord, body["run_id"], populate_existing=True)
        if (
            not owner
            or run is None
            or link is None
            or run.status != "running"
            or run.lease_owner != owner
            or run.lease_expires_at is None
            or as_utc(run.lease_expires_at) <= now
            or link.generation != body["source_generation"]
            or link.state in {"failed", "cancelled"}
            or link.tool_calls_started >= body["max_tool_calls"]
        ):
            raise ValueError("skill_repair_policy_not_current_owner")
        if body["policy"] == MODE and now >= datetime.fromisoformat(body["acquisition_close_at"]):
            raise ValueError("skill_reexecution_acquisition_window_closed")
        if now >= min(
            datetime.fromisoformat(body["original_deadline_at"]),
            datetime.fromisoformat(body["binding_deadline_at"]),
        ):
            raise ValueError("skill_repair_policy_deadline_elapsed")
        return now, link

    def prepare(self, *, run_id, source_generation, request, errors, evidence_ids, lease_owner):
        return self.selections.originals_preparer(
            self.packages.registry.database,
            lambda session: self._prepare(
                session, run_id, source_generation, request, errors, evidence_ids, lease_owner
            ),
        )

    def _prepare(self, session, run_id, source_generation, request, errors, evidence_ids, owner):
        if type(source_generation) is not int or source_generation < 1:
            raise ValueError("skill_repair_source_generation_invalid")
        if self.selections._row(session, run_id) is None:
            return None
        original = self.selections.read_in_session(session, run_id)
        execution = self.selections.execution_reader(session, run_id, source_generation)
        authority = self.bindings.authority_reader(session, execution)
        saved = read_prepared_execution_in_session(session, run_id, source_generation)
        if (
            request.output_schema_ref != DECISION_OUTPUT_SCHEMA_REF
            or request.research_mode != "current_decision"
            or request.execution_mode != "live"
            or saved.execution.request.model_dump(mode="json") != request.model_dump(mode="json")
            or execution.task_request_hash != original["task_request_hash"]
            or execution.method_hash != original["method_binding_hash"]
            or execution.compiled_task_hash != original["compiled_task_hash"]
            or authority.scope_id != original["scope_id"]
            or authority.horizon != original["horizon"]
        ):
            raise ValueError("skill_repair_original_identity_changed")
        if source_generation != original["generation"]:
            self.bindings.read_execution_binding_in_session(session, run_id, source_generation)
        self.bindings._window(session, original, authority)
        generation = source_generation + 1
        if saved.execution.turn_kind == "synthesis_repair":
            reference = prompt_policy_reference(saved.prompt.prompt)
            if reference is None:
                raise ValueError("skill_repair_policy_recursive_repair_forbidden")
            body = read_repair_policy_in_session(
                session,
                run_id=run_id,
                generation=source_generation,
                reference=reference,
                request=request,
            )
            if body["repair_errors_hash"] != canonical_hash(errors) or body[
                "repair_evidence_ids_hash"
            ] != canonical_hash(sorted(set(evidence_ids))):
                raise ValueError("skill_repair_policy_key_conflict")
            return RepairPolicyMaterial(encoded(body), (), owner)
        existing = _row(session, run_id, generation)
        if existing is not None:
            body = read_repair_policy_in_session(
                session,
                run_id=run_id,
                generation=generation,
                reference=row_ref(existing),
                request=request,
            )
            if body["repair_errors_hash"] != canonical_hash(errors) or body[
                "repair_evidence_ids_hash"
            ] != canonical_hash(sorted(set(evidence_ids))):
                raise ValueError("skill_repair_policy_key_conflict")
            return RepairPolicyMaterial(encoded(body), (), owner)
        if (
            min(
                request.execution_budget.max_structured_repairs,
                saved.method.definition.execution_budget.max_structured_repairs,
            )
            < 1
        ):
            raise ValueError("skill_repair_policy_budget_exhausted")
        body = dict(
            schema_version=SCHEMA,
            policy=MODE,
            run_id=run_id,
            source_generation=source_generation,
            generation=generation,
            selection_ref=row_ref(self.selections._row(session, run_id)),
            previous_prepared_ref=decoded(authority.prepared_ref_json),
            task_request_hash=execution.task_request_hash,
            method_hash=execution.method_hash,
            request_hash=canonical_hash(request.model_dump(mode="json")),
            **{
                key: original[key]
                for key in ("skill_version_id", "package_hash", "scope_id", "horizon")
            },
            target=decoded(authority.target_json),
            original_deadline_at=aware_utc(saved.method.definition.deadline_at).isoformat(),
            binding_deadline_at=self.bindings._window(session, original, authority).isoformat(),
            acquisition_close_at=research_acquisition_close_at(request).isoformat(),
            max_tool_calls=saved.method.definition.execution_budget.max_tool_calls,
            allowed_capabilities=sorted(request.allowed_capabilities),
            allowed_tools=list(TOOLS),
            repair_errors_hash=canonical_hash(errors),
            repair_evidence_ids_hash=canonical_hash(sorted(set(evidence_ids))),
        )
        market_refs = ()
        if aware_utc(self.selections.clock()) >= research_acquisition_close_at(request):
            if self.nodes is None:
                raise ValueError("skill_original_reader_not_installed")
            from .research_skill_original_read import read_spot_original_in_session
            source = read_spot_original_in_session(self.nodes.use, self.nodes.market, session,
                selection=original, source_generation=source_generation,
                cutoff_at=research_acquisition_close_at(request),
                prepared_reader=self.nodes.gateway_prepared_reader)
            body.update(policy=LATE_MODE, allowed_tools=list(LATE_TOOLS),
                        original_source=decoded(source.manifest_json))
            market_refs = source.market_refs
        now, link = self._live(session, body, owner)
        if now >= self.bindings._window(session, original, authority):
            raise ValueError("skill_repair_trial_window_elapsed")
        body["tool_calls_before"] = link.tool_calls_started
        refs = self.selections._archive_refs(session, original)
        return RepairPolicyMaterial(encoded(body), refs, owner, market_refs)

    def freeze_in_session(self, session, prepared):
        prepared.verify(session)
        if prepared.value is None:
            return None
        material = prepared.value
        body = decoded(material.intent_json)
        existing = _row(session, body["run_id"], body["generation"])
        if existing is not None:
            if existing.payload_json.encode() != material.intent_json:
                raise ValueError("skill_repair_policy_key_conflict")
            return row_ref(existing)
        if "authorized_at" in body:
            raise ValueError("skill_repair_policy_original_deleted")
        now, _ = self._live(session, body, material.lease_owner)
        self.packages.installed.verify()
        for ref, digest in material.archive_readset:
            if sha(self.packages.archive.read(ref)) != digest:
                raise ValueError("skill_repair_policy_archive_changed")
        for ref, digest in material.market_readset:
            if self.nodes is None or sha(self.nodes.market.archive.read(ref)) != digest:
                raise ValueError("skill_repair_market_original_changed")
        body["authorized_at"] = now.isoformat()
        row = RunRoleOutputRecord(
            output_id=policy_key(body["run_id"], body["generation"]),
            run_id=body["run_id"],
            role=f"{ROLE}:{body['generation']}",
            schema_version=SCHEMA,
            payload_json=encoded(body).decode(),
            payload_hash=canonical_hash(body),
            created_at=now,
        )
        session.add(row)
        session.flush()
        prepared.verify_after_writes(session)
        return row_ref(row)

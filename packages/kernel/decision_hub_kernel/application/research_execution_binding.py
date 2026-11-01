"""Prepare V2 research execution receipts in one caller-owned transaction.

Prepared policy metadata is neither a checker execution receipt nor permission
to trade. The policy prefix is authenticated against frozen inputs; the whole
trusted/repair prompt body remains the future Runtime producer's responsibility.
No Session or lock is held across a Host operation here.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts.generated.agentic_research import (
    ResearchInputEvidence,
    ResearchSessionRequest,
)
from packages.contracts_py.decision_hub_contracts.generated.dsh_host_bridge import (
    DshRunSessionLinkView,
    DshSessionPrompt,
    DshSessionSubmit,
    DshUpstreamIdentity,
)
from packages.contracts_py.decision_hub_contracts.generated.research_context_execution import (
    ResearchExecutionMethodDefinitionV3,
    ResearchExecutionMethodReceiptV3,
    ResearchExecutionRequestReceiptV3,
)
from packages.contracts_py.decision_hub_contracts.generated.research_execution_binding import (
    DshSessionSubmit as StrictSubmit,
)
from packages.contracts_py.decision_hub_contracts.generated.research_execution_binding import (
    ResearchExecutionMethodDefinitionV2,
    ResearchExecutionMethodReceiptV2,
    ResearchExecutionPromptIntegrityV2,
    ResearchExecutionPromptV2,
    ResearchExecutionRequestReceiptV2,
    ResearchPreparedBundleIdentity,
)
from packages.contracts_py.decision_hub_contracts.generated.research_execution_binding import (
    ResearchSessionRequest as StrictResearchRequest,
)
from packages.kernel.decision_hub_kernel.application.dsh_sessions import (
    DshSessionLinkService,
    execution_request_role,
    prompt_integrity_role,
)
from packages.kernel.decision_hub_kernel.application.research_binding import (
    BUNDLE_ROLES,
    COMPILED_ROLE,
    PACK_ROLE,
    TASK_ROLE,
    FrozenResearchBundle,
    read_research_bundle_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_context import (
    CONTEXT_POLICY_HASH,
    build_task_policy,
    select_context,
)
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    _evidence_model,  # pyright: ignore[reportPrivateUsage]
    research_evidence_content_hash,
)
from packages.kernel.decision_hub_kernel.application.research_method import (
    ROLE as METHOD_ROLE,
)
from packages.kernel.decision_hub_kernel.application.research_method import (
    canonical_recipe,
    recipe_hash,
)
from packages.kernel.decision_hub_kernel.application.research_policy_render_v2 import (
    RENDERER_HASH,
    render_prepared_research_policy,
)
from packages.kernel.decision_hub_kernel.application.research_request_identity import (
    research_request_hash,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    DshSessionLinkRecord,
    DshSessionPromptRecord,
    ResearchEvidenceRecord,
    RunRecord,
    RunRoleOutputRecord,
    as_utc,
    utcnow,
)

from packages.kernel.decision_hub_kernel.persistence.original_preparation import (
    DeclaredOriginalWrite, PreparedOriginals,
)

METHOD_SCHEMA = "research-execution-method.v2"
EXECUTION_SCHEMA = "dsh-execution-request.v2"
INTEGRITY_SCHEMA = "dsh-prompt-integrity.v2"
TurnKind = Literal["research_round", "synthesis_repair"]
type V2Receipt = (
    ResearchExecutionMethodReceiptV2
    | ResearchExecutionMethodReceiptV3
    | ResearchExecutionRequestReceiptV3
    | ResearchExecutionRequestReceiptV2
    | ResearchExecutionPromptIntegrityV2
)


class ResearchExecutionBindingError(ValueError):
    def __init__(self, code: str) -> None:
        self.error_code = code
        super().__init__(code)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ResearchExecutionBindingError("research_execution_time_invalid")
    return value.astimezone(UTC)


def _json(value: BaseModel) -> dict[str, Any]:
    return value.model_dump(mode="json")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _role_row(session: Session, run_id: str, role: str) -> RunRoleOutputRecord | None:
    return session.scalar(
        select(RunRoleOutputRecord)
        .where(
            RunRoleOutputRecord.run_id == run_id,
            RunRoleOutputRecord.role == role,
        )
        .execution_options(populate_existing=True)
    )


def _method_id(run_id: str) -> str:
    return "method_" + _sha(run_id)[:32]


def _turn_id(prefix: str, run_id: str, generation: int) -> str:
    return prefix + _sha(f"{run_id}\0{generation}")


def _require_transaction(session: Session) -> None:
    """Verify a physical caller transaction, without pretending to detect mode."""
    if not session.in_transaction():
        raise ResearchExecutionBindingError("research_execution_transaction_required")
    connection = session.connection()
    if connection.dialect.name == "sqlite":
        driver = connection.connection.driver_connection
        if not isinstance(driver, sqlite3.Connection) or not driver.in_transaction:
            raise ResearchExecutionBindingError("research_execution_transaction_required")


def read_bundle_identity_in_session(
    session: Session,
    run_id: str,
) -> tuple[FrozenResearchBundle, ResearchPreparedBundleIdentity]:
    """Bind the actual original three raw rows, including their common time."""
    frozen = read_research_bundle_in_session(session, run_id)
    if not isinstance(frozen, FrozenResearchBundle):
        raise ResearchExecutionBindingError("research_execution_v2_bundle_required")
    rows = {
        row.role: row
        for row in session.scalars(
            select(RunRoleOutputRecord)
            .where(
                RunRoleOutputRecord.run_id == run_id,
                RunRoleOutputRecord.role.in_(BUNDLE_ROLES),
            )
            .execution_options(populate_existing=True),
        ).all()
    }
    expected = {PACK_ROLE: frozen.pack, TASK_ROLE: frozen.task, COMPILED_ROLE: frozen.compiled}
    identities = {}
    for role, receipt in expected.items():
        row = rows.get(role)
        if (
            row is None
            or row.payload_json != canonical_recipe(_json(receipt))
            or row.payload_hash != _sha(row.payload_json)
            or as_utc(row.created_at) != frozen.recorded_at
        ):
            raise ResearchExecutionBindingError("research_execution_bundle_identity_invalid")
        identities[role] = {
            "output_id": row.output_id,
            "run_id": row.run_id,
            "role": row.role,
            "row_schema_version": row.schema_version,
            "payload_hash": row.payload_hash,
            "recorded_at": frozen.recorded_at.isoformat(),
        }
    body = {
        "schema_version": "research-prepared-bundle-identity.v1",
        "run_id": run_id,
        "recorded_at": frozen.recorded_at.isoformat(),
        "bundle_hash_kind": "canonical_bundle_rows.v1",
        "pack": identities[PACK_ROLE],
        "task": identities[TASK_ROLE],
        "compiled": identities[COMPILED_ROLE],
        "bundle_hash": "0" * 64,
    }
    identity = ResearchPreparedBundleIdentity.model_validate(body)
    body = _json(identity)
    body["bundle_hash"] = recipe_hash({k: v for k, v in body.items() if k != "bundle_hash"})
    return frozen, ResearchPreparedBundleIdentity.model_validate(body)


def _policy_binding(bundle: FrozenResearchBundle, first_full_hash: str) -> dict[str, Any]:
    compiled = bundle.compiled.compiled
    body = {
        "schema_version": "research-policy-method-binding.v1",
        "run_id": bundle.task.run_id,
        "event_id": bundle.request.event_id,
        "full_task_hash": bundle.task.request_hash,
        "scope_hash": bundle.task.scope.scope_hash,
        "requirements_hash": bundle.task.scope.requirements_hash,
        "request_pack_hash": bundle.pack.definition_hash,
        "first_execution_request_hash": first_full_hash,
        "first_execution_hash_kind": "actual_first_execution_request",
        "gate_policy_hash": compiled.gate_resource.canonical_definition_hash,
        "rubric_hash": compiled.rubric_resource.canonical_definition_hash,
        "compiled_policy_hash": compiled.compiled_hash,
        "compiler_version": compiled.compiler_version,
        "compiler_code_hash": compiled.compiler_code_hash,
        "checker_version": compiled.checker_version,
        "checker_code_hash": compiled.checker_code_hash,
        "checker_activation": "prepared",
    }
    return {**body, "binding_hash": recipe_hash(body)}


def _assert_actual_scope(
    session: Session,
    request: ResearchSessionRequest,
    bundle: FrozenResearchBundle,
    *,
    recorded_at: datetime,
    ceiling: ResearchExecutionMethodDefinitionV2
    | ResearchExecutionMethodDefinitionV3
    | None = None,
) -> None:
    original = bundle.request
    immutable = (
        "request_id",
        "run_id",
        "event_id",
        "trigger_snapshot_id",
        "domain_pack_ref",
        "role_profile_ref",
        "execution_mode",
        "research_mode",
        "pit_cutoff_at",
        "output_schema_ref",
        "event_watch",
        "event_window_samples",
    )
    requirements = {item.requirement_id: _json(item) for item in original.evidence_requirements}
    allowed = ceiling.allowed_capabilities if ceiling is not None else original.allowed_capabilities
    budget = ceiling.execution_budget if ceiling is not None else original.execution_budget
    deadline = ceiling.deadline_at if ceiling is not None else original.deadline_at
    # Timing policy is frozen identity, not a numeric resource ceiling. A
    # lower reserve would expand acquisition; omitting V2 would restore the
    # legacy formula. Neither a request nor a later method can authorize it.
    original_budget = _json(original.execution_budget)
    requested_budget = _json(request.execution_budget)
    ceiling_budget = _json(budget)
    policy_keys = {"schema_version", "closing_reserve_seconds"}
    if any(
        requested_budget.get(key) != original_budget.get(key)
        or ceiling_budget.get(key) != original_budget.get(key)
        for key in policy_keys
    ):
        raise ResearchExecutionBindingError("research_execution_budget_policy_changed")

    def requirement_matches(item: BaseModel) -> bool:
        body = _json(item)
        basis = requirements.get(body["requirement_id"])
        if basis is None:
            return False
        if body == basis:
            return True
        # Graph projects executable untried original preferred/fallback routes;
        # all factual requirements and thresholds remain the frozen original.
        routes = body.pop("preferred_capabilities")
        expected = {key: value for key, value in basis.items() if key != "preferred_capabilities"}
        if body != expected or len(routes) != len(set(routes)):
            return False
        if request.execution_mode == "replay" and set(request.allowed_capabilities) == {
            "replay.research"
        }:
            return routes == ["replay.research"]
        return (
            bool(routes)
            and set(routes).issubset(
                set(basis["preferred_capabilities"]) | set(basis["allowed_fallbacks"])
            )
            and set(routes).issubset(request.allowed_capabilities)
        )

    if (
        any(getattr(request, key) != getattr(original, key) for key in immutable)
        or not set(request.allowed_capabilities).issubset(allowed)
        or len(request.allowed_capabilities) != len(set(request.allowed_capabilities))
        or any(not requirement_matches(item) for item in request.evidence_requirements)
        or len({item.requirement_id for item in request.evidence_requirements})
        != len(request.evidence_requirements)
        or any(
            value > ceiling_budget[key]
            for key, value in requested_budget.items()
            if key not in policy_keys
        )
        or request.deadline_at > deadline
        or request.current_round > budget.max_evidence_rounds
    ):
        raise ResearchExecutionBindingError("research_execution_task_scope_conflict")
    # Graph retains eligible collected refs even when context budget omits their
    # excerpts. A reference needs original Task or real same-Run SQL provenance;
    # it does not become admitted Evidence merely by appearing in this request.
    original_inputs = {item.evidence_id: item for item in original.input_evidence}
    input_origins = dict(original_inputs)
    cutoff = min(request.pit_cutoff_at, recorded_at)
    if len(request.evidence_refs) != len(set(request.evidence_refs)) or not {
        item.evidence_id for item in request.input_evidence
    }.issubset(request.evidence_refs):
        raise ResearchExecutionBindingError("research_execution_evidence_reference_invalid")
    for reference in request.evidence_refs:
        if reference in original_inputs:
            if original_inputs[reference].received_at > cutoff:
                raise ResearchExecutionBindingError("research_execution_evidence_reference_invalid")
            continue
        row = session.get(ResearchEvidenceRecord, reference, populate_existing=True)
        if row is None or row.run_id != request.run_id or row.requirement_id not in requirements:
            raise ResearchExecutionBindingError("research_execution_evidence_reference_invalid")
        candidate = _evidence_model(row)
        content_hash = research_evidence_content_hash(
            requirement_id=candidate.requirement_id,
            kind=candidate.kind,
            authority=candidate.authority,
            source_id=candidate.source_id,
            source_url=str(candidate.source_url) if candidate.source_url is not None else None,
            published_at=candidate.published_at,
            excerpt=candidate.excerpt,
            structured_payload_ref=candidate.structured_payload_ref,
        )
        if (
            candidate.content_hash != content_hash
            or candidate.received_at > cutoff
            or candidate.observed_at > candidate.received_at
            or (
                candidate.published_at is not None
                and candidate.published_at > candidate.observed_at
            )
        ):
            raise ResearchExecutionBindingError("research_execution_evidence_reference_invalid")
        candidate_body = _json(candidate)
        input_origins[reference] = ResearchInputEvidence.model_validate(
            {key: candidate_body[key] for key in ResearchInputEvidence.model_fields}
        )
    # A valid reference authenticates the source, not arbitrary caller DTO
    # contents under that ID. Context selection only projects the excerpt;
    # every factual metadata field must remain the frozen/SQL original.
    policy = build_task_policy(
        research_mode=request.research_mode or "current_decision",
        requirements=request.evidence_requirements,
        created_at=recorded_at,
        max_evidence_rounds=request.execution_budget.max_evidence_rounds,
    )
    for item in request.input_evidence:
        basis = input_origins[item.evidence_id]
        if item.model_dump(exclude={"excerpt"}) != basis.model_dump(exclude={"excerpt"}):
            raise ResearchExecutionBindingError("research_execution_input_origin_invalid")
        if item.excerpt == basis.excerpt:
            continue
        projected, _manifest = select_context(
            [basis],
            [],
            requirements=request.evidence_requirements,
            policy=policy,
            manifest_prefix=f"input-origin:{request.run_id}",
            revision=request.current_round,
            created_at=recorded_at,
            allowed_capabilities=request.allowed_capabilities,
            eligibility_cutoff_at=cutoff,
        )
        if item.excerpt != projected[0].excerpt:
            raise ResearchExecutionBindingError("research_execution_input_origin_invalid")


def build_method_v2_in_session(
    session: Session,
    request: ResearchSessionRequest,
    submit: DshSessionSubmit,
    identity: DshUpstreamIdentity,
    *,
    runtime_id: str,
    runtime_version: str,
    profile_ref: str,
    prompt_policy_hash: str,
    recorded_at: datetime,
) -> ResearchExecutionMethodReceiptV2:
    """Build only from original same-Session inputs and the actual first request."""
    bundle, rows = read_bundle_identity_in_session(session, request.run_id)
    _assert_actual_scope(session, request, bundle, recorded_at=_utc(recorded_at))
    if submit.generation != 1 or request.current_round != 1:
        raise ResearchExecutionBindingError("research_execution_first_generation_required")
    if (
        submit.request_hash != research_request_hash(request)
        or submit.max_tool_calls != request.execution_budget.max_tool_calls
        or submit.deadline_at != request.deadline_at
        or submit.model_step_timeout_ms
        != request.execution_budget.per_model_step_timeout_seconds * 1000
    ):
        raise ResearchExecutionBindingError("research_execution_initial_ceiling_conflict")
    block = render_prepared_research_policy(bundle, request.allowed_capabilities)
    definition = ResearchExecutionMethodDefinitionV2.model_validate(
        {
            "schema_version": METHOD_SCHEMA,
            "runtime_id": runtime_id,
            "runtime_version": runtime_version,
            "profile_ref": profile_ref,
            "prompt_policy_hash": prompt_policy_hash,
            "upstream_identity": _json(identity),
            "domain_pack_ref": request.domain_pack_ref,
            "role_profile_ref": request.role_profile_ref,
            "execution_mode": request.execution_mode,
            "research_mode": request.research_mode,
            "context_policy_hash": CONTEXT_POLICY_HASH,
            "allowed_capabilities": sorted(request.allowed_capabilities),
            "execution_budget": _json(request.execution_budget),
            "deadline_at": submit.deadline_at,
            "output_schema_ref": request.output_schema_ref,
            "workspace_ref": submit.workspace_ref,
            "agent_preset": submit.agent_preset,
            "permission_ref": submit.permission_ref,
            "provider_configuration_attested": False,
            "evolution_pointer_consumed": False,
            "candidate_id": None,
            "research_policy_renderer_hash": RENDERER_HASH,
            "research_policy_prompt_hash": _sha(block),
            "bundle": _json(rows),
            "policy_binding": _policy_binding(bundle, recipe_hash(_json(request))),
            "first_execution_request_hash_kind": "canonical_research_session_request.v1",
            "request_pack_hash": bundle.pack.definition_hash,
        }
    )
    return ResearchExecutionMethodReceiptV2.model_validate(
        {
            "schema_version": METHOD_SCHEMA,
            "run_id": request.run_id,
            "request_hash": submit.request_hash,
            "request_hash_kind": "dsh_link_identity.v1",
            "method_hash": recipe_hash(_json(definition)),
            "definition": _json(definition),
            "recorded_at": _utc(recorded_at),
        }
    )


def _read_role[Model: V2Receipt](
    row: RunRoleOutputRecord,
    model: type[Model],
    *,
    role: str,
    output_id: str,
) -> Model:
    try:
        value = model.model_validate_json(row.payload_json)
        body = _json(value)
        if (
            row.role != role
            or row.output_id != output_id
            or row.run_id != body["run_id"]
            or row.schema_version != body["schema_version"]
            or row.payload_hash != _sha(row.payload_json)
            or row.payload_json != canonical_recipe(body)
            or as_utc(row.created_at) != value.recorded_at
        ):
            raise ValueError("receipt_identity")
        return value
    except (ValueError, KeyError, TypeError) as exc:
        raise ResearchExecutionBindingError("research_execution_receipt_invalid") from exc


def _read_method_row(
    session: Session,
    row: RunRoleOutputRecord,
) -> tuple[
    ResearchExecutionMethodReceiptV2 | ResearchExecutionMethodReceiptV3, FrozenResearchBundle
]:
    model = (
        ResearchExecutionMethodReceiptV3
        if row.schema_version == "research-execution-method.v3"
        else ResearchExecutionMethodReceiptV2
    )
    method = _read_role(row, model, role=METHOD_ROLE, output_id=_method_id(row.run_id))
    return method, _validate_method_binding(session, method)


def _validate_method_binding(
    session: Session,
    method: ResearchExecutionMethodReceiptV2 | ResearchExecutionMethodReceiptV3,
) -> FrozenResearchBundle:
    bundle, identity = read_bundle_identity_in_session(session, method.run_id)
    definition = method.definition
    expected_binding = _policy_binding(
        bundle, definition.policy_binding.first_execution_request_hash
    )
    if (
        method.method_hash != recipe_hash(_json(definition))
        or _json(definition.bundle) != _json(identity)
        or _json(definition.policy_binding) != expected_binding
        or definition.request_pack_hash != definition.policy_binding.request_pack_hash
        or definition.research_policy_renderer_hash != RENDERER_HASH
        or definition.context_policy_hash != CONTEXT_POLICY_HASH
        or identity.recorded_at > method.recorded_at
    ):
        raise ResearchExecutionBindingError("research_execution_method_binding_changed")
    return bundle


def _flush_stage(session: Session, stage: str) -> None:
    """Real SQL flush boundary, used by precise post-write rollback tests."""
    session.flush()


def _insert_role(
    session: Session,
    receipt: V2Receipt,
    *,
    role: str,
    output_id: str,
    stage: str,
) -> RunRoleOutputRecord:
    body = _json(receipt)
    if session.get(RunRoleOutputRecord, output_id, populate_existing=True) is not None:
        raise ResearchExecutionBindingError("research_execution_receipt_id_collision")
    encoded = canonical_recipe(body)
    row = RunRoleOutputRecord(
        output_id=output_id,
        run_id=body["run_id"],
        role=role,
        schema_version=body["schema_version"],
        payload_json=encoded,
        payload_hash=_sha(encoded),
        created_at=receipt.recorded_at,
    )
    session.add(row)
    _flush_stage(session, stage)
    return row


def freeze_method_v2_in_session(
    session: Session,
    receipt: ResearchExecutionMethodReceiptV2 | ResearchExecutionMethodReceiptV3,
) -> ResearchExecutionMethodReceiptV2 | ResearchExecutionMethodReceiptV3:
    """Compare or insert; no transaction ownership and no partial-history upgrade."""
    _require_transaction(session)
    model = (
        ResearchExecutionMethodReceiptV3
        if isinstance(receipt, ResearchExecutionMethodReceiptV3)
        else ResearchExecutionMethodReceiptV2
    )
    value = model.model_validate_json(canonical_recipe(_json(receipt)))
    _validate_method_binding(session, value)
    existing = _role_row(session, value.run_id, METHOD_ROLE)
    if existing is not None:
        stored, _bundle = _read_method_row(session, existing)
        if _json(stored) != _json(value):
            raise ResearchExecutionBindingError("research_execution_method_conflict")
        return stored
    run = session.get(RunRecord, value.run_id, populate_existing=True)
    if run is None or run.status not in {"admitted", "running"}:
        raise ResearchExecutionBindingError("research_execution_run_not_active")
    row = _insert_role(
        session, value, role=METHOD_ROLE, output_id=_method_id(value.run_id), stage="method"
    )
    return _read_method_row(session, row)[0]


def _assert_link(
    method: ResearchExecutionMethodReceiptV2 | ResearchExecutionMethodReceiptV3,
    link: DshSessionLinkRecord,
) -> None:
    definition = method.definition
    session_id, _request_id = DshSessionLinkService.deterministic_ids(
        method.run_id, method.request_hash
    )
    upstream = DshUpstreamIdentity.model_validate(_json(definition.upstream_identity))
    if (
        link.run_id != method.run_id
        or link.dsh_session_id != session_id
        or link.request_hash != method.request_hash
        or link.upstream_identity_json != DshSessionLinkService._identity_json(upstream)  # pyright: ignore[reportPrivateUsage]
        or link.plugin_build_hash != upstream.plugin_build_hash
        or as_utc(link.created_at) != method.recorded_at
        or as_utc(link.deadline_at) != definition.deadline_at
        or link.max_tool_calls != definition.execution_budget.max_tool_calls
        or link.model_step_timeout_ms
        != definition.execution_budget.per_model_step_timeout_seconds * 1000
    ):
        raise ResearchExecutionBindingError("research_execution_link_conflict")


def _assert_submit(
    request: ResearchSessionRequest,
    submit: DshSessionSubmit,
    upstream: DshUpstreamIdentity,
    method: ResearchExecutionMethodReceiptV2 | ResearchExecutionMethodReceiptV3,
    runtime_version: str,
    recorded_at: datetime,
) -> None:
    session_id, request_id = DshSessionLinkService.deterministic_ids(
        request.run_id,
        method.request_hash,
        submit.generation,
    )
    if (
        submit.run_id != request.run_id
        or submit.request_hash != method.request_hash
        or submit.deterministic_session_id != session_id
        or submit.deterministic_request_id != request_id
        or submit.prompt_ref != f"hub://runs/{request.run_id}/prompts/{submit.generation}"
        or runtime_version != f"dsh-web-{upstream.source_version}+{upstream.plugin_build_hash[:12]}"
        or recorded_at >= method.definition.deadline_at
    ):
        raise ResearchExecutionBindingError("research_execution_submit_binding_invalid")


@dataclass(frozen=True)
class FrozenExecutionPreparation:
    method: ResearchExecutionMethodReceiptV2 | ResearchExecutionMethodReceiptV3
    link: DshRunSessionLinkView
    prompt: ResearchExecutionPromptV2
    execution: ResearchExecutionRequestReceiptV2 | ResearchExecutionRequestReceiptV3
    integrity: ResearchExecutionPromptIntegrityV2

    def host_prompt(self) -> DshSessionPrompt:
        """Explicit public V1 projection after authenticating the internal sidecars."""
        prompt = self.prompt
        return DshSessionPrompt(
            schema_version="dsh-session-prompt.v1",
            run_id=prompt.run_id,
            dsh_session_id=prompt.dsh_session_id,
            request_id=prompt.request_id,
            request_hash=prompt.request_hash,
            generation=prompt.generation,
            prompt=prompt.prompt,
        )


def _read_generation(
    session: Session,
    method: ResearchExecutionMethodReceiptV2 | ResearchExecutionMethodReceiptV3,
    bundle: FrozenResearchBundle,
    link: DshSessionLinkRecord,
    generation: int,
) -> FrozenExecutionPreparation:
    prompt = session.get(
        DshSessionPromptRecord, (method.run_id, generation), populate_existing=True
    )
    execution_row = _role_row(session, method.run_id, execution_request_role(generation))
    integrity_row = _role_row(session, method.run_id, prompt_integrity_role(generation))
    if prompt is None or execution_row is None or integrity_row is None:
        raise ResearchExecutionBindingError("research_execution_generation_incomplete")
    execution = _read_role(
        execution_row,
        (
            ResearchExecutionRequestReceiptV3
            if isinstance(method, ResearchExecutionMethodReceiptV3)
            else ResearchExecutionRequestReceiptV2
        ),
        role=execution_request_role(generation),
        output_id=_turn_id("execution_", method.run_id, generation),
    )
    integrity = _read_role(
        integrity_row,
        ResearchExecutionPromptIntegrityV2,
        role=prompt_integrity_role(generation),
        output_id=_turn_id("prompt_", method.run_id, generation),
    )
    request = ResearchSessionRequest.model_validate(_json(execution.request))
    _assert_actual_scope(
        session, request, bundle, ceiling=method.definition, recorded_at=execution.recorded_at
    )
    session_id, request_id = DshSessionLinkService.deterministic_ids(
        method.run_id,
        method.request_hash,
        generation,
    )
    recorded_at = as_utc(prompt.created_at)
    expected_execution = {
        "run_id": method.run_id,
        "dsh_session_id": session_id,
        "request_id": request_id,
        "generation": generation,
        "link_request_hash": method.request_hash,
        "task_request_hash": bundle.task.request_hash,
        "request_hash": recipe_hash(_json(request)),
        "execution_request_hash": research_request_hash(request),
        "first_execution_request_full_hash": (
            method.definition.policy_binding.first_execution_request_hash
        ),
        "method_hash": method.method_hash,
        "bundle_hash": method.definition.bundle.bundle_hash,
    }
    if (
        any(getattr(execution, key) != value for key, value in expected_execution.items())
        or prompt.dsh_session_id != session_id
        or prompt.request_id != request_id
        or prompt.request_hash != method.request_hash
        or execution.recorded_at != recorded_at
        or integrity.recorded_at != recorded_at
        or recorded_at is None
        or recorded_at < method.recorded_at
        or (generation == 1 and recorded_at != method.recorded_at)
    ):
        raise ResearchExecutionBindingError("research_execution_generation_binding_invalid")
    expected_integrity = {
        "run_id": method.run_id,
        "dsh_session_id": session_id,
        "request_id": request_id,
        "generation": generation,
        "request_hash": method.request_hash,
        "method_hash": method.method_hash,
        "bundle_hash": method.definition.bundle.bundle_hash,
        "execution_request_receipt_hash": execution_row.payload_hash,
        "prompt_hash": _sha(prompt.prompt),
    }
    if any(getattr(integrity, key) != value for key, value in expected_integrity.items()):
        raise ResearchExecutionBindingError("research_execution_prompt_integrity_invalid")
    block = render_prepared_research_policy(bundle, request.allowed_capabilities)
    if not prompt.prompt.startswith(block + "\n\n"):
        raise ResearchExecutionBindingError("research_execution_policy_prefix_invalid")
    if generation == 1 and (
        execution.turn_kind != "research_round"
        or request.current_round != 1
        or execution.request_hash != method.definition.policy_binding.first_execution_request_hash
        or execution.execution_request_hash != method.request_hash
        or method.definition.research_policy_prompt_hash != _sha(block)
        or sorted(request.allowed_capabilities) != method.definition.allowed_capabilities
        or _json(request.execution_budget) != _json(method.definition.execution_budget)
        or request.deadline_at != method.definition.deadline_at
    ):
        raise ResearchExecutionBindingError("research_execution_first_request_invalid")
    internal = ResearchExecutionPromptV2.model_validate(
        {
            "schema_version": "dsh-session-prompt.v2",
            **{key: value for key, value in expected_integrity.items() if key != "prompt_hash"},
            "request_hash_kind": "dsh_link_identity.v1",
            "recorded_at": recorded_at,
            "prompt": prompt.prompt,
        }
    )
    prepared = FrozenExecutionPreparation(
        method,
        DshSessionLinkService._view(link),  # pyright: ignore[reportPrivateUsage]
        internal,
        execution,
        integrity,
    )
    if isinstance(method, ResearchExecutionMethodReceiptV3):
        from .research_context_execution import verify_generation_in_session

        verify_generation_in_session(session, prepared)
    return prepared


def read_method_v2_in_session(
    session: Session,
    run_id: str,
) -> ResearchExecutionMethodReceiptV2 | None:
    """Authenticate persistent original rows; this read is not current execution admission."""
    row = _role_row(session, run_id, METHOD_ROLE)
    if row is None:
        return None
    if row.schema_version != METHOD_SCHEMA:
        raise ResearchExecutionBindingError("research_execution_legacy_or_partial")
    from .research_context_execution import has_selection_link_in_session

    if has_selection_link_in_session(session, run_id):
        raise ResearchExecutionBindingError("research_execution_legacy_or_partial")
    method, bundle = _read_method_row(session, row)
    if not isinstance(method, ResearchExecutionMethodReceiptV2):
        raise ResearchExecutionBindingError("research_execution_legacy_or_partial")
    link = session.get(DshSessionLinkRecord, run_id, populate_existing=True)
    run = session.get(RunRecord, run_id, populate_existing=True)
    if link is None or run is None:
        raise ResearchExecutionBindingError("research_execution_preparation_incomplete")
    _assert_link(method, link)
    if run.runtime_version != method.definition.runtime_version:
        raise ResearchExecutionBindingError("research_execution_runtime_identity_changed")
    generations = session.scalars(
        select(DshSessionPromptRecord.generation)
        .where(
            DshSessionPromptRecord.run_id == run_id,
        )
        .order_by(DshSessionPromptRecord.generation)
    ).all()
    if (
        len(generations) != link.generation
        or not generations
        or generations[0] != 1
        or generations[-1] != link.generation
    ):
        raise ResearchExecutionBindingError("research_execution_generation_incomplete")
    sidecars = session.scalars(
        select(RunRoleOutputRecord.role).where(
            RunRoleOutputRecord.run_id == run_id,
            (RunRoleOutputRecord.role.like("dsh_execution_request:%"))
            | (RunRoleOutputRecord.role.like("dsh_prompt_integrity:%")),
        )
    ).all()
    expected_roles = {
        role
        for generation in generations
        for role in (
            execution_request_role(generation),
            prompt_integrity_role(generation),
        )
    }
    if set(sidecars) != expected_roles:
        raise ResearchExecutionBindingError("research_execution_generation_incomplete")
    for generation in generations:
        _read_generation(session, method, bundle, link, generation)
    return method


def read_prepared_execution_in_session(
    session: Session,
    run_id: str,
    generation: int | None = None,
) -> FrozenExecutionPreparation:
    row = _role_row(session, run_id, METHOD_ROLE)
    if row is not None and row.schema_version == "research-execution-method.v3":
        from .research_context_execution import read_prepared_v3_in_session

        return read_prepared_v3_in_session(session, run_id, generation)
    method = read_method_v2_in_session(session, run_id)
    if method is None:
        raise ResearchExecutionBindingError("research_execution_method_missing")
    link = session.get(DshSessionLinkRecord, run_id, populate_existing=True)
    assert link is not None
    bundle, _identity = read_bundle_identity_in_session(session, run_id)
    return _read_generation(
        session, method, bundle, link, link.generation if generation is None else generation
    )


def method_v2_view(
    method: ResearchExecutionMethodReceiptV2 | ResearchExecutionMethodReceiptV3,
) -> dict[str, Any]:
    return {"output_id": _method_id(method.run_id), **_json(method)}


def parse_execution_receipt(
    row: RunRoleOutputRecord,
) -> ResearchExecutionRequestReceiptV2 | ResearchExecutionRequestReceiptV3:
    """Strict schema dispatch; this parse alone grants no execution permission."""
    if row.schema_version == "dsh-execution-request.v3":
        return ResearchExecutionRequestReceiptV3.model_validate_json(row.payload_json)
    if row.schema_version == EXECUTION_SCHEMA:
        return ResearchExecutionRequestReceiptV2.model_validate_json(row.payload_json)
    raise ResearchExecutionBindingError("research_execution_legacy_or_partial")


class ResearchExecutionPreparationService:
    def __init__(self, database: Database, *, clock: Callable[[], datetime] = utcnow) -> None:
        self.database, self.clock = database, clock

    def prepare(
        self,
        request: ResearchSessionRequest,
        submit: DshSessionSubmit,
        upstream_identity: DshUpstreamIdentity,
        prompt: str,
        *,
        runtime_id: str,
        runtime_version: str,
        profile_ref: str,
        prompt_policy_hash: str,
        turn_kind: TurnKind = "research_round",
        selection_reference: Any | None = None,
        expected_lease_owner: str | None = None,
    ) -> FrozenExecutionPreparation:
        with self.database.session() as session:
            if session.get_bind().dialect.name == "sqlite":
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            return self.prepare_in_session(
                session,
                request,
                submit,
                upstream_identity,
                prompt,
                runtime_id=runtime_id,
                runtime_version=runtime_version,
                profile_ref=profile_ref,
                prompt_policy_hash=prompt_policy_hash,
                turn_kind=turn_kind,
                recorded_at=self.clock(),
                selection_reference=selection_reference,
                expected_lease_owner=expected_lease_owner,
            )

    def prepare_in_session(
        self,
        session: Session,
        request: ResearchSessionRequest,
        submit: DshSessionSubmit,
        upstream_identity: DshUpstreamIdentity,
        prompt: str,
        *,
        runtime_id: str,
        runtime_version: str,
        profile_ref: str,
        prompt_policy_hash: str,
        recorded_at: datetime,
        turn_kind: TurnKind = "research_round",
        selection_reference: Any | None = None,
        expected_lease_owner: str | None = None,
        _candidate_only: bool = False,
    ) -> FrozenExecutionPreparation | ExecutionPreparationCandidate:
        """One physical caller transaction; never commit, begin, close or call Host.

        The exact original policy prefix is checked. The rest of the prompt
        body is caller supplied; this does not attest its canonical renderer.
        """
        _require_transaction(session)
        now = _utc(recorded_at)
        request = ResearchSessionRequest.model_validate(
            _json(StrictResearchRequest.model_validate(_json(request)))
        )
        submit = DshSessionSubmit.model_validate(_json(StrictSubmit.model_validate(_json(submit))))
        upstream_identity = DshUpstreamIdentity.model_validate(_json(upstream_identity))
        run = session.get(RunRecord, request.run_id, populate_existing=True)
        if run is None or run.status not in {"admitted", "running"}:
            raise ResearchExecutionBindingError("research_execution_run_not_active")
        if type(submit.generation) is not int or turn_kind not in {
            "research_round",
            "synthesis_repair",
        }:
            raise ResearchExecutionBindingError("research_execution_turn_invalid")
        bundle, _rows = read_bundle_identity_in_session(session, request.run_id)
        selection = None
        if selection_reference is not None:
            from .research_context_execution import read_selection_in_session

            selection = read_selection_in_session(
                session,
                request.run_id,
                selection_reference,
                expected_lease_owner=expected_lease_owner,
                require_current_permission=not _candidate_only,
            )
            if _json(selection.request) != _json(request):
                raise ResearchExecutionBindingError("research_context_selected_request_conflict")
        method_row = _role_row(session, request.run_id, METHOD_ROLE)
        link = session.get(DshSessionLinkRecord, request.run_id, populate_existing=True)
        if method_row is None:
            from .research_context_execution import has_selection_link_in_session

            prompts = session.scalar(
                select(DshSessionPromptRecord)
                .where(
                    DshSessionPromptRecord.run_id == request.run_id,
                )
                .execution_options(populate_existing=True)
            )
            sidecar = session.scalar(
                select(RunRoleOutputRecord)
                .where(
                    RunRoleOutputRecord.run_id == request.run_id,
                    (RunRoleOutputRecord.role.like("dsh_execution_request:%"))
                    | (RunRoleOutputRecord.role.like("dsh_prompt_integrity:%")),
                )
                .execution_options(populate_existing=True)
            )
            if (
                link is not None
                or prompts is not None
                or sidecar is not None
                or has_selection_link_in_session(session, request.run_id)
            ):
                raise ResearchExecutionBindingError("research_execution_legacy_or_partial")
            if turn_kind != "research_round":
                raise ResearchExecutionBindingError("research_execution_first_generation_required")
            builder = build_method_v2_in_session
            extra = {}
            if selection is not None:
                from .research_context_execution import build_method_v3_in_session

                builder = build_method_v3_in_session
                extra = {"selection": selection}
            method = builder(
                session,
                request,
                submit,
                upstream_identity,
                **extra,
                runtime_id=runtime_id,
                runtime_version=runtime_version,
                profile_ref=profile_ref,
                prompt_policy_hash=prompt_policy_hash,
                recorded_at=now,
            )
        else:
            expected_schema = (
                "research-execution-method.v3" if selection is not None else METHOD_SCHEMA
            )
            if method_row.schema_version != expected_schema:
                raise ResearchExecutionBindingError("research_execution_legacy_or_partial")
            method = (
                read_prepared_execution_in_session(session, request.run_id).method
                if selection is not None
                else read_method_v2_in_session(session, request.run_id)
            )
            assert method is not None and link is not None
            definition = method.definition
            if (
                definition.runtime_id != runtime_id
                or definition.runtime_version != runtime_version
                or definition.profile_ref != profile_ref
                or definition.prompt_policy_hash != prompt_policy_hash
                or _json(definition.upstream_identity) != _json(upstream_identity)
                or definition.workspace_ref != submit.workspace_ref
                or definition.agent_preset != submit.agent_preset
                or definition.permission_ref != submit.permission_ref
                or definition.deadline_at != submit.deadline_at
                or definition.execution_budget.max_tool_calls != submit.max_tool_calls
                or definition.execution_budget.per_model_step_timeout_seconds * 1000
                != submit.model_step_timeout_ms
            ):
                raise ResearchExecutionBindingError("research_execution_method_conflict")
            _assert_actual_scope(session, request, bundle, ceiling=definition, recorded_at=now)
            _assert_submit(request, submit, upstream_identity, method, runtime_version, now)
            if submit.generation == link.generation:
                stored = read_prepared_execution_in_session(
                    session, request.run_id, submit.generation
                )
                if (
                    _json(stored.execution.request) != _json(request)
                    or stored.execution.turn_kind != turn_kind
                    or stored.prompt.prompt != prompt
                ):
                    raise ResearchExecutionBindingError("research_execution_generation_conflict")
                if selection is not None:
                    from .research_context_execution import (
                        fence_selection_permission_in_session,
                        selection_identity,
                    )

                    if (
                        not isinstance(stored.execution, ResearchExecutionRequestReceiptV3)
                        or _json(stored.execution.current_selection_row)
                        != selection_identity(selection.reference)
                        or stored.execution.current_selection_payload_hash
                        != selection.receipt.selection_payload_hash
                    ):
                        raise ResearchExecutionBindingError(
                            "research_context_selected_generation_conflict"
                        )
                    if not _candidate_only:
                        fence_selection_permission_in_session(
                            session, request.run_id, expected_lease_owner or "", definition.deadline_at
                        )
                if _candidate_only:
                    return _execution_candidate(session, self.database, request, submit,
                        upstream_identity, prompt, method, bundle, selection, now,
                        first_method=False, expected_lease_owner=expected_lease_owner, turn_kind=turn_kind, existing=stored)
                return stored
            previous = read_prepared_execution_in_session(session, request.run_id, link.generation)
            if request.current_round < previous.execution.request.current_round or (
                turn_kind == "synthesis_repair"
                and request.current_round != previous.execution.request.current_round
            ):
                raise ResearchExecutionBindingError("research_execution_round_conflict")
        session_id, request_id = DshSessionLinkService.deterministic_ids(
            request.run_id,
            method.request_hash,
            submit.generation,
        )
        _assert_submit(request, submit, upstream_identity, method, runtime_version, now)
        block = render_prepared_research_policy(bundle, request.allowed_capabilities)
        if not prompt.startswith(block + "\n\n"):
            raise ResearchExecutionBindingError("research_execution_policy_prefix_invalid")
        if _candidate_only:
            return _execution_candidate(session, self.database, request, submit,
                upstream_identity, prompt, method, bundle, selection, now,
                first_method=method_row is None, expected_lease_owner=expected_lease_owner, turn_kind=turn_kind)
        if method_row is None:
            method = freeze_method_v2_in_session(session, method)
        links = DshSessionLinkService(self.database, clock=lambda: now)
        links.reserve_in_session(session, submit, upstream_identity, recorded_at=now)
        _flush_stage(session, "link")
        execution_model = (
            ResearchExecutionRequestReceiptV3
            if selection is not None
            else ResearchExecutionRequestReceiptV2
        )
        selection_body = {}
        if selection is not None:
            from .research_context_execution import selection_identity

            selection_body = {
                "current_selection_row": selection_identity(selection.reference),
                "current_selection_payload_hash": selection.receipt.selection_payload_hash,
            }
        execution = execution_model.model_validate(
            {
                **selection_body,
                "schema_version": "dsh-execution-request.v3"
                if selection is not None
                else EXECUTION_SCHEMA,
                "run_id": request.run_id,
                "dsh_session_id": session_id,
                "request_id": request_id,
                "generation": submit.generation,
                "turn_kind": turn_kind,
                "link_request_hash": method.request_hash,
                "link_request_hash_kind": "dsh_link_identity.v1",
                "task_request_hash": bundle.task.request_hash,
                "task_request_hash_kind": "canonical_research_session_request.v1",
                "request": _json(request),
                "request_hash": recipe_hash(_json(request)),
                "request_hash_kind": "canonical_research_session_request.v1",
                "execution_request_hash": research_request_hash(request),
                "execution_request_hash_kind": "dsh_link_identity.v1",
                "first_execution_request_full_hash": (
                    method.definition.policy_binding.first_execution_request_hash
                ),
                "first_execution_request_full_hash_kind": "canonical_research_session_request.v1",
                "method_hash": method.method_hash,
                "bundle_hash": method.definition.bundle.bundle_hash,
                "recorded_at": now,
            }
        )
        if (
            session.get(
                DshSessionPromptRecord, (request.run_id, submit.generation), populate_existing=True
            )
            is not None
        ):
            raise ResearchExecutionBindingError("research_execution_generation_conflict")
        session.add(
            DshSessionPromptRecord(
                run_id=request.run_id,
                generation=submit.generation,
                dsh_session_id=session_id,
                request_id=request_id,
                request_hash=method.request_hash,
                prompt=prompt,
                created_at=now,
            )
        )
        _flush_stage(session, "prompt")
        execution_row = _insert_role(
            session,
            execution,
            role=execution_request_role(submit.generation),
            output_id=_turn_id("execution_", request.run_id, submit.generation),
            stage="execution",
        )
        integrity = ResearchExecutionPromptIntegrityV2.model_validate(
            {
                "schema_version": INTEGRITY_SCHEMA,
                "run_id": request.run_id,
                "dsh_session_id": session_id,
                "request_id": request_id,
                "generation": submit.generation,
                "request_hash": method.request_hash,
                "request_hash_kind": "dsh_link_identity.v1",
                "method_hash": method.method_hash,
                "bundle_hash": method.definition.bundle.bundle_hash,
                "execution_request_receipt_hash": execution_row.payload_hash,
                "recorded_at": now,
                "prompt_hash": _sha(prompt),
            }
        )
        _insert_role(
            session,
            integrity,
            role=prompt_integrity_role(submit.generation),
            output_id=_turn_id("prompt_", request.run_id, submit.generation),
            stage="integrity",
        )
        if selection is not None:
            from .research_context_execution import (
                freeze_link_in_session,
                read_selection_in_session,
            )

            stored_link = session.get(DshSessionLinkRecord, request.run_id, populate_existing=True)
            assert stored_link is not None
            internal = ResearchExecutionPromptV2.model_validate(
                {
                    **{
                        k: v
                        for k, v in _json(integrity).items()
                        if k not in {"schema_version", "prompt_hash"}
                    },
                    "schema_version": "dsh-session-prompt.v2",
                    "prompt": prompt,
                }
            )
            # Internal Prompt omits the hash-only integrity field.
            prepared = FrozenExecutionPreparation(
                method,
                DshSessionLinkService._view(stored_link),  # pyright: ignore[reportPrivateUsage]
                internal,
                execution,
                integrity,
            )
            freeze_link_in_session(session, prepared, selection)
            read_selection_in_session(
                session,
                request.run_id,
                selection.reference,
                expected_lease_owner=expected_lease_owner,
                require_current_permission=True,
            )
        from .research_harness_execution import freeze_harness_method_in_session
        freeze_harness_method_in_session(
            session, bundle, first_method=method_row is None, recorded_at=now,
        )
        result = read_prepared_execution_in_session(session, request.run_id, submit.generation)
        if selection is not None:
            from .research_context_execution import fence_selection_permission_in_session

            fence_selection_permission_in_session(
                session, request.run_id, expected_lease_owner or "", method.definition.deadline_at
            )
        return result


@dataclass(frozen=True)
class ExecutionPreparationCandidate:
    """Internal RO output; only an issued PreparedOriginals may publish it."""

    prepared: FrozenExecutionPreparation
    writes: tuple[DeclaredOriginalWrite, ...]
    run_id: str
    owner: str | None
    deadline_at: datetime


def _row_values(row) -> dict[str, Any]:
    return {column.key: getattr(row, column.key) for column in inspect(type(row)).columns}


def _candidate_role(receipt, role: str, output_id: str) -> RunRoleOutputRecord:
    encoded = canonical_recipe(_json(receipt))
    return RunRoleOutputRecord(output_id=output_id, run_id=receipt.run_id, role=role,
        schema_version=receipt.schema_version, payload_json=encoded, payload_hash=_sha(encoded),
        created_at=receipt.recorded_at)


def _execution_candidate(
    session: Session, database: Database, request: ResearchSessionRequest,
    submit: DshSessionSubmit, upstream: DshUpstreamIdentity, prompt: str,
    method, bundle: FrozenResearchBundle, selection, now: datetime, *,
    first_method: bool, expected_lease_owner: str | None,
    turn_kind: TurnKind = "research_round", existing: FrozenExecutionPreparation | None = None,
) -> ExecutionPreparationCandidate:
    from packages.kernel.decision_hub_kernel.persistence.original_preparation import (
        declare_existing_write, declare_insert,
    )
    from .research_context_execution import _encoded_link, link_body_from_rows, link_role, selection_identity
    from .research_harness_execution import prepare_harness_method_in_session

    run = session.get(RunRecord, request.run_id, populate_existing=True)
    assert run is not None
    owner = expected_lease_owner if expected_lease_owner is not None else run.lease_owner
    if expected_lease_owner is not None and owner != run.lease_owner:
        raise ResearchExecutionBindingError("research_context_expected_owner_required")
    writes = []

    def insert(row) -> None:
        mapper = inspect(type(row))
        identity = tuple(getattr(row, column.key) for column in mapper.primary_key)
        if session.get(type(row), identity, populate_existing=True) is not None:
            raise ResearchExecutionBindingError("research_execution_receipt_id_collision")
        if isinstance(row, RunRoleOutputRecord) and _role_row(session, row.run_id, row.role) is not None:
            raise ResearchExecutionBindingError("research_execution_generation_conflict")
        # SQLite returns naive UTC datetimes from these DateTime columns. Freeze
        # the exact stored scalar representation, including all insert defaults.
        for column in mapper.columns:
            value = getattr(row, column.key)
            if isinstance(value, datetime):
                setattr(row, column.key, _utc(value).replace(tzinfo=None))
        writes.append(declare_insert(row))

    method_row = _candidate_role(method, METHOD_ROLE, _method_id(request.run_id)) if first_method else _role_row(session, request.run_id, METHOD_ROLE)
    assert method_row is not None
    # Preserve the old freeze/read-method original binding validation in RO,
    # including the first method that is not yet present in the database.
    _validate_method_binding(session, method)
    if existing is not None:
        prepare_harness_method_in_session(session, bundle, method_row,
                                         first_method=False, recorded_at=now)
        return ExecutionPreparationCandidate(existing, (), request.run_id, owner, method.definition.deadline_at)

    if first_method:
        insert(method_row)
    old_link = session.get(DshSessionLinkRecord, request.run_id, populate_existing=True)
    if old_link is None:
        conflict = session.scalar(select(DshSessionLinkRecord).where(
            DshSessionLinkRecord.dsh_session_id == submit.deterministic_session_id))
        if conflict is not None:
            raise ResearchExecutionBindingError("dsh_session_already_linked")
        link = DshSessionLinkRecord(run_id=submit.run_id,
            dsh_session_id=submit.deterministic_session_id, request_hash=submit.request_hash,
            state="admitted", generation=submit.generation,
            upstream_identity_json=DshSessionLinkService._identity_json(upstream),
            plugin_build_hash=upstream.plugin_build_hash, deadline_at=submit.deadline_at,
            max_tool_calls=submit.max_tool_calls, model_step_timeout_ms=submit.model_step_timeout_ms,
            tool_calls_started=0, accepted_at=None, last_seen_at=None, terminal_at=None,
            last_seq=0, trace_ref=None, result_ref=None, result_hash=None, error_code=None,
            created_at=now, updated_at=now)
        insert(link)
        runtime_version = f"dsh-web-{upstream.source_version}+{upstream.plugin_build_hash[:12]}"
        if run.runtime_version != runtime_version:
            writes.append(declare_existing_write(run, (("runtime_version", runtime_version),)))
        if method.definition.runtime_version != runtime_version:
            raise ResearchExecutionBindingError("research_execution_runtime_identity_changed")
    else:
        link = DshSessionLinkRecord(**_row_values(old_link))
        DshSessionLinkService(database)._advance_or_assert_reservation(link, submit, upstream, recorded_at=now)
        changes = {key: value for key, value in _row_values(link).items()
                   if value != getattr(old_link, key)}
        changes = {key: (_utc(value).replace(tzinfo=None) if isinstance(value, datetime) else value)
                   for key, value in changes.items()}
        if changes:
            writes.append(declare_existing_write(old_link, tuple(changes.items())))
    _assert_link(method, link)

    execution_type = ResearchExecutionRequestReceiptV3 if selection is not None else ResearchExecutionRequestReceiptV2
    selection_body = {} if selection is None else {
        "current_selection_row": selection_identity(selection.reference),
        "current_selection_payload_hash": selection.receipt.selection_payload_hash,
    }
    execution = execution_type.model_validate({
        **selection_body, "schema_version": "dsh-execution-request.v3" if selection is not None else EXECUTION_SCHEMA,
        "run_id": request.run_id, "dsh_session_id": submit.deterministic_session_id,
        "request_id": submit.deterministic_request_id, "generation": submit.generation,
        "turn_kind": turn_kind, "link_request_hash": method.request_hash,
        "link_request_hash_kind": "dsh_link_identity.v1", "task_request_hash": bundle.task.request_hash,
        "task_request_hash_kind": "canonical_research_session_request.v1", "request": _json(request),
        "request_hash": recipe_hash(_json(request)), "request_hash_kind": "canonical_research_session_request.v1",
        "execution_request_hash": research_request_hash(request), "execution_request_hash_kind": "dsh_link_identity.v1",
        "first_execution_request_full_hash": method.definition.policy_binding.first_execution_request_hash,
        "first_execution_request_full_hash_kind": "canonical_research_session_request.v1",
        "method_hash": method.method_hash, "bundle_hash": method.definition.bundle.bundle_hash,
        "recorded_at": now,
    })
    prompt_row = DshSessionPromptRecord(run_id=request.run_id, generation=submit.generation,
        dsh_session_id=submit.deterministic_session_id, request_id=submit.deterministic_request_id,
        request_hash=method.request_hash, prompt=prompt, created_at=now)
    insert(prompt_row)
    execution_row = _candidate_role(execution, execution_request_role(submit.generation),
                                    _turn_id("execution_", request.run_id, submit.generation))
    insert(execution_row)
    integrity = ResearchExecutionPromptIntegrityV2.model_validate({
        "schema_version": INTEGRITY_SCHEMA, "run_id": request.run_id,
        "dsh_session_id": submit.deterministic_session_id, "request_id": submit.deterministic_request_id,
        "generation": submit.generation, "request_hash": method.request_hash,
        "request_hash_kind": "dsh_link_identity.v1", "method_hash": method.method_hash,
        "bundle_hash": method.definition.bundle.bundle_hash,
        "execution_request_receipt_hash": execution_row.payload_hash, "recorded_at": now,
        "prompt_hash": _sha(prompt),
    })
    integrity_row = _candidate_role(integrity, prompt_integrity_role(submit.generation),
                                    _turn_id("prompt_", request.run_id, submit.generation))
    insert(integrity_row)
    internal = ResearchExecutionPromptV2.model_validate({
        **{key: value for key, value in _json(integrity).items() if key not in {"schema_version", "prompt_hash"}},
        "schema_version": "dsh-session-prompt.v2", "prompt": prompt,
    })
    prepared = FrozenExecutionPreparation(method, DshSessionLinkService._view(link), internal, execution, integrity)
    if selection is not None:
        value, raw = _encoded_link(link_body_from_rows(prepared, selection, execution_row, integrity_row))
        insert(RunRoleOutputRecord(output_id=value.link_id, run_id=value.run_id,
            role=link_role(value.generation), schema_version=value.schema_version,
            payload_json=raw, payload_hash=_sha(raw), created_at=value.recorded_at))
    harness = prepare_harness_method_in_session(session, bundle, method_row,
        first_method=first_method, recorded_at=now)
    if harness is not None:
        insert(harness)
    return ExecutionPreparationCandidate(prepared, tuple(writes), request.run_id, owner,
                                         method.definition.deadline_at)


def commit_execution_candidate_in_session(
    session: Session, originals: PreparedOriginals, *, checkpoint: Callable[[], None],
) -> FrozenExecutionPreparation:
    """Publish issued exact rows only; no full bundle/selection/history reader."""
    from .research_context_execution import fence_selection_permission_in_session
    from .research_graph_binding import _permission

    originals._require_issued(session)
    candidate = originals.value.execution
    if type(candidate) is not ExecutionPreparationCandidate:
        raise ResearchExecutionBindingError("research_execution_preparation_unissued")
    checkpoint()
    originals.verify(session, checkpoint=checkpoint)

    def fresh() -> None:
        checkpoint()
        if datetime.now(UTC) >= candidate.deadline_at:
            raise ResearchExecutionBindingError("research_context_selection_deadline_elapsed")
        if candidate.owner is not None:
            _permission(session, candidate.run_id, candidate.owner)
        else:
            run = session.get(RunRecord, candidate.run_id, populate_existing=True)
            if run is None or run.status != "admitted" or run.lease_owner is not None or run.artifact_id is not None:
                raise ResearchExecutionBindingError("research_execution_run_not_active")

    fresh()
    for write in candidate.writes:
        checkpoint()
        if write.before is None:
            session.add(write.model(**dict(write.changes)))
        else:
            row = session.get(write.model, write.identity, populate_existing=True)
            if row is None:
                raise ResearchExecutionBindingError("research_execution_preparation_changed")
            for key, value in write.changes:
                setattr(row, key, value)
        session.flush()  # One SQL effect for each exact issued row declaration.
    originals.verify_after_declared_writes(session, checkpoint=checkpoint)
    fresh()
    # This one existing SQL permission CAS follows exact write-set verification.
    # It changes no value and retains the original final predicate before COMMIT.
    if candidate.prepared.method.schema_version == "research-execution-method.v3":
        fence_selection_permission_in_session(session, candidate.run_id, candidate.owner or "",
                                              candidate.deadline_at)
    fresh()
    from sqlalchemy import event
    event.listen(session, "before_commit", lambda _session: fresh())
    link = session.get(DshSessionLinkRecord, candidate.run_id, populate_existing=True)
    if link is None:
        raise ResearchExecutionBindingError("research_execution_preparation_changed")
    method_row = _role_row(session, candidate.run_id, METHOD_ROLE)
    execution_row = _role_row(session, candidate.run_id,
                              execution_request_role(candidate.prepared.execution.generation))
    integrity_row = _role_row(session, candidate.run_id,
                              prompt_integrity_role(candidate.prepared.execution.generation))
    prompt_row = session.get(DshSessionPromptRecord,
        (candidate.run_id, candidate.prepared.execution.generation), populate_existing=True)
    if any(row is None for row in (method_row, execution_row, integrity_row, prompt_row)):
        raise ResearchExecutionBindingError("research_execution_preparation_changed")
    method_type = ResearchExecutionMethodReceiptV3 if method_row.schema_version == "research-execution-method.v3" else ResearchExecutionMethodReceiptV2
    execution_type = ResearchExecutionRequestReceiptV3 if execution_row.schema_version == "dsh-execution-request.v3" else ResearchExecutionRequestReceiptV2
    method = method_type.model_validate_json(method_row.payload_json)
    execution = execution_type.model_validate_json(execution_row.payload_json)
    integrity = ResearchExecutionPromptIntegrityV2.model_validate_json(integrity_row.payload_json)
    internal = ResearchExecutionPromptV2.model_validate({
        **{key: value for key, value in _json(integrity).items() if key not in {"schema_version", "prompt_hash"}},
        "schema_version": "dsh-session-prompt.v2", "prompt": prompt_row.prompt,
    })
    return FrozenExecutionPreparation(method, DshSessionLinkService._view(link), internal,
                                       execution, integrity)

"""Server-built conservative quality context and immutable same-writer sidecars.

Prepared profile/budget/selector identities are recorded as prepared inputs. A
request-correlated transport is not promoted to actual model context fidelity,
runtime attestation, cost accounting or a qualified predictive risk model.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from typing import Literal, cast

from pydantic import AwareDatetime, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts import ResearchSessionResult

from ..decision.research_quality import FrozenQualityContract, QualityDecisionV1
from ..persistence.db import RunRoleOutputRecord, as_utc
from ..persistence.research_quality_records import ResearchQualitySidecarRecord
from ..ports.research_observation import ObservedExecutionRef, ResearchNativeObservationContext
from .research_execution_binding import FrozenExecutionPreparation
from .research_graph_recipe_v3 import GraphRecipeSelectionV3, verify_round_quality_binding_v3
from .research_publication_binding import observation_json, observation_sha
from .research_quality_projection import QualityProjectionContextV1

USAGE_PROOF_VERSION = "provider-conversation-usage-proof.v2"


def _requires_native_census(runtime: dict[str, object]) -> bool:
    # The marker is inside the immutable, authenticated sidecar original. No
    # wall-clock/build heuristic or model/owner-provided completeness flag.
    if "conversation_usage_proof_version" not in runtime:
        return False
    if runtime["conversation_usage_proof_version"] != USAGE_PROOF_VERSION:
        raise ValueError("research_quality_usage_proof_version_invalid")
    return True


class ResearchQualitySidecarV1(FrozenQualityContract):
    schema_version: Literal["research-quality-sidecar.v1"] = "research-quality-sidecar.v1"
    run_id: str
    logical_round: int = Field(ge=1)
    phase: Literal["round", "final"]
    recipe_selection_ref: ObservedExecutionRef
    quality_context: QualityProjectionContextV1
    quality_context_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    quality_decision: QualityDecisionV1
    source_result_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    projected_result_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    proof_rows: tuple[ObservedExecutionRef, ...]
    unknown_receipt_codes: tuple[str, ...]
    prepared_runtime_json: str
    recorded_at: AwareDatetime

    @model_validator(mode="after")
    def validate_binding(self):
        if (
            self.run_id != self.quality_decision.run_id
            or self.quality_context.scope_id != self.quality_decision.scope_id
            or self.quality_context_hash
            != observation_sha(self.quality_context.model_dump(mode="json"))
            or self.source_result_hash != self.quality_decision.source_result_hash
            or self.quality_context.bound_result_hash != self.source_result_hash
            or not isinstance(json.loads(self.prepared_runtime_json), dict)
            or (self.phase == "final") != (self.projected_result_hash is not None)
        ):
            raise ValueError("research_quality_sidecar_binding_invalid")
        return self


def _row_ref(row: RunRoleOutputRecord) -> ObservedExecutionRef:
    clock = as_utc(cast(datetime | None, row.created_at))
    if clock is None or hashlib.sha256(row.payload_json.encode()).hexdigest() != row.payload_hash:
        raise ValueError("research_quality_proof_original_changed")
    return ObservedExecutionRef(
        output_id=row.output_id,
        role=row.role,
        schema_version=row.schema_version,
        payload_hash=row.payload_hash,
        recorded_at=clock,
    )


def build_server_quality_context(
    session: Session,
    *,
    selection: GraphRecipeSelectionV3,
    source_result: ResearchSessionResult,
    native_context: ResearchNativeObservationContext,
    preparation: FrozenExecutionPreparation,
    assessed_at: datetime,
    mapped_ref: ObservedExecutionRef | None = None,
    previous_mapped_ref: ObservedExecutionRef | None = None,
) -> tuple[QualityProjectionContextV1, tuple[ObservedExecutionRef, ...], str, tuple[str, ...]]:
    """Use already authenticated original native/preparation arguments from binding.

    The source result has no authority to supply these flags. All actual input
    identities are captured for later inspection. A separately authenticated
    Host terminal can attest this exact execution. It cannot attest model
    comprehension, actual cost or calibrated financial qualification.
    """
    from .dsh_sessions import execution_request_role
    from .research_method import ROLE as METHOD_ROLE

    expected_refs = tuple(native_context.rows)
    for reference in expected_refs:
        row = session.get(RunRoleOutputRecord, reference.output_id, populate_existing=True)
        if row is None or row.run_id != selection.run_id or _row_ref(row) != reference:
            raise ValueError("research_quality_proof_original_changed")
    roles = {METHOD_ROLE, execution_request_role(native_context.generation)}
    runtime_rows = list(
        session.scalars(
            select(RunRoleOutputRecord)
            .where(
                RunRoleOutputRecord.run_id == selection.run_id, RunRoleOutputRecord.role.in_(roles)
            )
            .execution_options(populate_existing=True)
        )
    )
    by_role = {row.role: row for row in runtime_rows}
    if len(runtime_rows) != len(roles):
        raise ValueError("research_quality_prepared_runtime_missing")
    execution_ref = _row_ref(by_role[execution_request_role(native_context.generation)])
    selector_row = getattr(preparation.execution, "current_selection_row", None)
    runtime = {
        "meaning": "authenticated_prepared_identity_not_actual_provider_usage_or_attestation",
        "runtime_id": preparation.method.definition.runtime_id,
        "runtime_version": preparation.method.definition.runtime_version,
        "profile_ref": preparation.method.definition.profile_ref,
        "prepared_budget": preparation.execution.request.execution_budget.model_dump(mode="json"),
        "deadline_at": preparation.execution.request.deadline_at.isoformat(),
        "execution_request_hash": preparation.execution.request_hash,
        "selector_row": (
            selector_row.model_dump(mode="json") if selector_row is not None else None
        ),
        "actual_total_tokens": None,
        "actual_estimated_cost_usd": None,
    }
    proof_map = {ref.output_id: ref for ref in expected_refs}
    proof_map.update((row.output_id, _row_ref(row)) for row in runtime_rows)
    attestation_ref = None
    # The caller already verified the native mapped result and its selected
    # candidate witness. The Host callback is an independent authority;
    # transport strings, completion status and model metadata alone are not.
    from .dsh_terminal_receipts import read_authenticated_terminal_in_session

    terminal = read_authenticated_terminal_in_session(session, selection.run_id, now=assessed_at)
    if terminal is not None and (
        terminal.completion.terminal_status == "completed"
        and terminal.completion.error is None
        and terminal.completion.run_id == selection.run_id == native_context.run_id
        and terminal.completion.generation == native_context.generation
        and terminal.completion.dsh_session_id == native_context.dsh_session_id
        and terminal.prompt_hash == native_context.prompt_hash
        and terminal.task_request_hash == native_context.original_task_request_hash
        and source_result.request_id == native_context.request_id
        and source_result.research_session_id == native_context.dsh_session_id
        and source_result.synthesis_failure_code is None
    ):
        terminal_row = session.get(
            RunRoleOutputRecord, terminal.receipt_ref, populate_existing=True
        )
        if terminal_row is None or terminal_row.payload_hash != terminal.receipt_hash:
            raise ValueError("research_quality_terminal_original_changed")
        terminal_ref = _row_ref(terminal_row)
        proof_map[terminal_ref.output_id] = terminal_ref
        attestation_ref = "hub:row:" + terminal_ref.output_id + "@" + terminal_ref.payload_hash
        runtime["authenticated_host_terminal"] = terminal_ref.model_dump(mode="json")
        runtime["meaning"] = (
            "authenticated_preparation_and_host_execution_not_model_comprehension_or_usage"
        )
    delivery = None
    effects = None
    lineage = ()
    if mapped_ref is not None:
        from .research_context_delivery import read_context_delivery_in_session
        from .research_review_effects import (
            read_review_effects_in_session, read_review_issue_lineage_in_session,
        )

        delivery = read_context_delivery_in_session(
            session,
            mapped_ref=mapped_ref,
            additional_required_refs=selection.quality_context.additional_required_context_refs,
        )
        effects = read_review_effects_in_session(
            session, mapped_ref=mapped_ref, source_result=source_result
        )
        if previous_mapped_ref is not None and effects is not None:
            lineage = read_review_issue_lineage_in_session(
                session, previous_mapped_ref=previous_mapped_ref,
                mapped_ref=mapped_ref, source_result=source_result,
            )
            runtime["authenticated_review_lineage"] = [
                item.model_dump(mode="json") for item in lineage
            ]
            for item in lineage:
                for ref in item.proof_refs:
                    proof_map[ref.output_id] = ref
        runtime["authenticated_context_delivery"] = delivery.model_dump(mode="json")
        for ref in (
            delivery.mapped_ref,
            delivery.transport_ref,
            delivery.provider_input_ref,
            delivery.provider_emission_ref,
            delivery.provider_finish_ref,
            *(item.original_ref for item in delivery.items),
        ):
            if ref is not None:
                proof_map[ref.output_id] = ref
        if effects is not None:
            runtime["authenticated_review_effects"] = effects.model_dump(mode="json")
            for ref in (effects.mapped_ref, effects.transport_ref, effects.review_effect_ref):
                if ref is not None:
                    proof_map[ref.output_id] = ref
    fidelity = bool(delivery is not None and delivery.context_fidelity_verified)
    if mapped_ref is not None:
        from .research_provider_usage import read_provider_run_usage_in_session

        usage = read_provider_run_usage_in_session(
            session, mapped_ref=mapped_ref, as_of=assessed_at, require_native_census=True
        )
        runtime["authenticated_conversation_usage"] = usage.model_dump(mode="json")
        runtime["conversation_usage_proof_version"] = USAGE_PROOF_VERSION
        # Scope is explicit and never upgrades the prepared budget qualification.
        runtime["actual_total_tokens"] = usage.total_tokens
        for ref in usage.source_refs:
            proof_map[ref.output_id] = ref
    updates: dict[str, object] = {
        "bound_result_hash": observation_sha(source_result.model_dump(mode="json")),
        "effective_budget_ref": (
            "hub:row:" + execution_ref.output_id + "@" + execution_ref.payload_hash
        ),
        "assessed_at": assessed_at,
        "runtime_attestation_ref": attestation_ref,
        "selection_manifest_hash": (
            delivery.selection_manifest_hash if fidelity and delivery is not None else None
        ),
        "context_fidelity_verified": fidelity,
        "authenticated_retained_context_refs": tuple(
            item.evidence_ref for item in delivery.items if item.covered
        )
        if fidelity and delivery is not None
        else (),
    }
    if effects is not None:
        updates["challenges"] = effects.challenges
        updates["authenticated_review_ids"] = effects.public_review_ids
        updates["authenticated_review_lineage"] = lineage
    context = selection.quality_context.model_copy(
        update={
            **updates,
        }
    )
    unknowns = (
        *(("actual_runtime_attestation_receipt_unavailable",) if attestation_ref is None else ()),
        *(("actual_context_fidelity_receipt_unavailable",) if not fidelity else ()),
        *(("structured_review_receipt_unavailable",) if effects is None else ()),
        "actual_usage_cost_receipt_unavailable",
        "qualified_path_probability_receipt_unavailable",
        "execution_support_receipt_unavailable",
    )
    return (
        context,
        tuple(proof_map[key] for key in sorted(proof_map)),
        observation_json(runtime),
        unknowns,
    )


def persist_quality_sidecar_in_session(
    session: Session,
    *,
    selection: GraphRecipeSelectionV3,
    recipe_selection_ref: ObservedExecutionRef,
    logical_round: int,
    context: QualityProjectionContextV1,
    quality: QualityDecisionV1,
    proof_rows: tuple[ObservedExecutionRef, ...],
    prepared_runtime_json: str,
    unknown_receipt_codes: tuple[str, ...],
    recorded_at: datetime,
    projected_result_hash: str | None = None,
) -> ResearchQualitySidecarRecord:
    connection = session.connection()
    driver = connection.connection.driver_connection
    if (
        connection.dialect.name != "sqlite"
        or not isinstance(driver, sqlite3.Connection)
        or not driver.in_transaction
    ):
        raise ValueError("research_quality_sidecar_writer_required")
    context_hash, _ = verify_round_quality_binding_v3(selection, context, quality)
    value = ResearchQualitySidecarV1(
        run_id=selection.run_id,
        logical_round=logical_round,
        phase="final" if projected_result_hash is not None else "round",
        recipe_selection_ref=recipe_selection_ref,
        quality_context=context,
        quality_context_hash=context_hash,
        quality_decision=quality,
        source_result_hash=quality.source_result_hash,
        projected_result_hash=projected_result_hash,
        proof_rows=proof_rows,
        unknown_receipt_codes=unknown_receipt_codes,
        prepared_runtime_json=prepared_runtime_json,
        recorded_at=recorded_at,
    )
    raw = observation_json(value.model_dump(mode="json"))
    digest = hashlib.sha256(raw.encode()).hexdigest()
    identifier = "quality_" + digest
    previous = session.scalar(
        select(ResearchQualitySidecarRecord)
        .where(
            ResearchQualitySidecarRecord.run_id == value.run_id,
            ResearchQualitySidecarRecord.logical_round == logical_round,
            ResearchQualitySidecarRecord.phase == value.phase,
        )
        .execution_options(populate_existing=True)
    )
    if previous is not None:
        actual = read_quality_sidecar_in_session(session, previous.sidecar_id, selection=selection)
        if actual != value:
            raise ValueError("research_quality_sidecar_original_conflict")
        return previous
    row = ResearchQualitySidecarRecord(
        sidecar_id=identifier,
        run_id=value.run_id,
        logical_round=logical_round,
        phase=value.phase,
        source_result_hash=value.source_result_hash,
        projected_result_hash=projected_result_hash,
        quality_context_hash=context_hash,
        quality_decision_hash=quality.decision_hash,
        status=quality.status,
        publication_mode=quality.publication_mode,
        payload_json=raw,
        payload_hash=digest,
        created_at=recorded_at,
    )
    session.add(row)
    session.flush()
    return row


def read_quality_sidecar_in_session(
    session: Session, sidecar_id: str, *, selection: GraphRecipeSelectionV3
) -> ResearchQualitySidecarV1:
    row = session.get(ResearchQualitySidecarRecord, sidecar_id, populate_existing=True)
    if row is None or hashlib.sha256(row.payload_json.encode()).hexdigest() != row.payload_hash:
        raise ValueError("research_quality_sidecar_original_changed")
    value = ResearchQualitySidecarV1.model_validate_json(row.payload_json)
    verify_round_quality_binding_v3(selection, value.quality_context, value.quality_decision)
    if (
        row.sidecar_id != "quality_" + row.payload_hash
        or observation_json(value.model_dump(mode="json")) != row.payload_json
        or row.run_id != value.run_id
        or row.logical_round != value.logical_round
        or row.phase != value.phase
        or row.source_result_hash != value.source_result_hash
        or row.projected_result_hash != value.projected_result_hash
        or row.quality_context_hash != value.quality_context_hash
        or row.quality_decision_hash != value.quality_decision.decision_hash
        or row.status != value.quality_decision.status
        or row.publication_mode != value.quality_decision.publication_mode
        or as_utc(row.created_at) != value.recorded_at
    ):
        raise ValueError("research_quality_sidecar_projection_changed")
    for ref in (*value.proof_rows, value.recipe_selection_ref):
        original = session.get(RunRoleOutputRecord, ref.output_id, populate_existing=True)
        if original is None or original.run_id != value.run_id or _row_ref(original) != ref:
            raise ValueError("research_quality_proof_original_changed")
    # A legacy sidecar is never filled from newer callbacks or current/latest.
    runtime = json.loads(value.prepared_runtime_json)
    if "authenticated_conversation_usage" in runtime:
        from ..ports.provider_input import ProviderConversationUsageCensusV1
        from .research_provider_usage import verify_provider_usage_census_in_session

        usage = ProviderConversationUsageCensusV1.model_validate_json(
            observation_json(runtime["authenticated_conversation_usage"])
        )
        if (
            usage.run_id != value.run_id
            or usage.as_of > value.recorded_at
            or not set(usage.source_refs).issubset(set(value.proof_rows))
            or runtime.get("actual_total_tokens") != usage.total_tokens
            or runtime.get("actual_estimated_cost_usd") is not None
        ):
            raise ValueError("research_quality_usage_binding_invalid")
        verify_provider_usage_census_in_session(
            session, usage, require_native_census=_requires_native_census(runtime)
        )
    return value

"""Native Graph admissions and deterministic projections in one SQLite writer.

Runtime mapping receipts are observations, not publication permission. This
module binds each adopted observation to original inputs and durable admitted
materials. It never calls a Host, charges usage, signs a caller Result, or imports
orchestration/Commit. Legacy runs do not gain replacement native receipts.
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, cast

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from sqlalchemy import Table, select, update
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts import (
    DshSessionResult,
    EvidenceCandidate,
    FactEnvelope,
    ResearchSessionRequest,
    ResearchSessionResult,
)
from packages.kernel.decision_hub_kernel.application.dsh_sessions import (
    execution_request_role,
    prompt_integrity_role,
)
from packages.kernel.decision_hub_kernel.application.fact_store import ResearchFactStore
from packages.kernel.decision_hub_kernel.application.research_binding import (
    BUNDLE_ROLES,
    FrozenResearchBundle,
    read_research_bundle_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchEvidenceService,
)
from packages.kernel.decision_hub_kernel.application.research_execution_binding import (
    read_prepared_execution_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_graph_projection import (
    RECIPE_VERSION,
    AgenticResearchState,
    _decision_cutoff,  # pyright: ignore[reportPrivateUsage]
    _round_cutoff,  # pyright: ignore[reportPrivateUsage]
    initial_research_state,
    project_failure,
    project_final,
    project_round,
    requirements_for_round,
    should_continue_after_round,
)
from packages.kernel.decision_hub_kernel.application.research_method import ROLE as METHOD_ROLE
from packages.kernel.decision_hub_kernel.application.research_publication_binding import (
    native_observation_context_in_session,
    observation_json,
    observation_sha,
    read_mapped_observation,
    read_transport_observation,
    verify_selection_witness,
)
from packages.kernel.decision_hub_kernel.application.research_review import (
    research_result_content_hash,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    Base,
    Database,
    DshSessionLinkRecord,
    EvidenceParsedLinkRecord,
    FactVersionRecord,
    ParsedVersionRecord,
    ResearchEvidenceRecord,
    ResearchFactRecord,
    RunInputRefRecord,
    RunRecord,
    RunRoleOutputRecord,
    SnapshotRecord,
    as_utc,
    utcnow,
)
from packages.kernel.decision_hub_kernel.ports.research_observation import (
    ObservedAgentExecutionError,
    ObservedExecutionRef,
    ObservedResearchResult,
    ResearchMappedObservation,
    ResearchNativeObservationContext,
    ResearchTransportObservation,
    Sha,
)
from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError

ROUND_SCHEMA = "research-graph-round-admission.v1"
FINAL_SCHEMA = "research-graph-final-projection.v1"
MAX_RECEIPT_BYTES = 64 * 1024 * 1024


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class GraphRoundAdmissionRef(_Closed):
    kind: Literal["graph_round_admission"] = "graph_round_admission"
    row: ObservedExecutionRef


class GraphFinalProjectionRef(_Closed):
    kind: Literal["graph_final_projection"] = "graph_final_projection"
    row: ObservedExecutionRef


def parse_graph_round_ref(value: object) -> GraphRoundAdmissionRef:
    """Decode the JSON checkpoint boundary without weakening strict native refs."""
    return GraphRoundAdmissionRef.model_validate_json(observation_json(value))


def parse_graph_final_ref(value: object) -> GraphFinalProjectionRef:
    return GraphFinalProjectionRef.model_validate_json(observation_json(value))


class GraphRowFingerprint(_Closed):
    table: str
    row_id: str
    payload_hash: str = Field(pattern=r"^[a-f0-9]{64}$", min_length=64, max_length=64)


class ResearchGraphRoundAdmission(_Closed):
    schema_version: Literal["research-graph-round-admission.v1"] = ROUND_SCHEMA
    run_id: str
    logical_round: int = Field(strict=True, ge=1)
    previous_ref: GraphRoundAdmissionRef | None
    observation_ref: ObservedExecutionRef
    observation_kind: Literal["mapped", "failure"]
    observed_tool_calls: int = Field(strict=True, ge=0)
    native_context: ResearchNativeObservationContext
    selected_synthesis_observation_ref: ObservedExecutionRef
    latest_observation_ref: ObservedExecutionRef
    # These are local prepared/mapped identities; capture origin stays unknown.
    selected_mapping_generation: int = Field(strict=True, ge=1)
    latest_transport_generation: int = Field(strict=True, ge=1)
    recipe_version: Literal["research-graph-projection.v1"] = RECIPE_VERSION
    recipe_hash: Sha
    evidence: list[EvidenceCandidate]
    facts: list[FactEnvelope]
    admitted_evidence_ids: list[str]
    material_rows: list[GraphRowFingerprint]
    projected_state: dict[str, object]
    state_hash: Sha
    admitted_lease_owner: str
    writer_clock_at: AwareDatetime
    lease_expires_at: AwareDatetime
    recorded_at: AwareDatetime


class ResearchGraphFinalProjection(_Closed):
    schema_version: Literal["research-graph-final-projection.v1"] = FINAL_SCHEMA
    run_id: str
    last_admission_ref: GraphRoundAdmissionRef
    selected_synthesis_observation_ref: ObservedExecutionRef
    latest_observation_ref: ObservedExecutionRef
    native_context: ResearchNativeObservationContext
    recipe_version: Literal["research-graph-projection.v1"] = RECIPE_VERSION
    recipe_hash: Sha
    decision_snapshot_id: str
    decision_snapshot_hash: Sha
    decision_snapshot_row_hash: Sha
    decision_cutoff_at: AwareDatetime
    projected_result: ResearchSessionResult
    projected_result_hash: Sha
    projected_result_content_hash: Sha
    recorded_at: AwareDatetime


@dataclass(frozen=True)
class FrozenGraphRound:
    reference: GraphRoundAdmissionRef
    receipt: ResearchGraphRoundAdmission
    state: AgenticResearchState


@dataclass(frozen=True)
class FrozenGraphFinal:
    reference: GraphFinalProjectionRef
    receipt: ResearchGraphFinalProjection
    result: ResearchSessionResult


def _recipe_hash() -> str:
    return hashlib.sha256(
        Path(__file__).with_name("research_graph_projection.py").read_bytes()
    ).hexdigest()


def _require_writer(session: Session) -> None:
    connection = session.connection()
    driver = connection.connection.driver_connection
    if (
        connection.dialect.name != "sqlite"
        or not isinstance(driver, sqlite3.Connection)
        or not driver.in_transaction
    ):
        raise ValueError("research_graph_writer_transaction_required")


def _permission(session: Session, run_id: str, owner: str | None) -> RunRecord:
    run = session.get(RunRecord, run_id, populate_existing=True)
    now = utcnow()
    if (
        run is None
        or not owner
        or run.status != "running"
        or run.artifact_id
        or run.lease_owner != owner
        or as_utc(run.lease_expires_at) is None
        or cast(datetime, as_utc(run.lease_expires_at)) <= now
    ):
        raise PermissionError("research_graph_writer_permission_lost")
    return run


def _fence(session: Session, run_id: str, owner: str | None) -> None:
    _permission(session, run_id, owner)
    # A real SQL predicate after expensive native/semantic validation protects
    # an expiry crossing in this same physical write transaction.
    updated = session.execute(
        update(RunRecord)
        .where(
            RunRecord.run_id == run_id,
            RunRecord.status == "running",
            RunRecord.artifact_id.is_(None),
            RunRecord.lease_owner == owner,
            RunRecord.lease_expires_at > utcnow(),
        )
        .values(lease_owner=owner)
        .execution_options(synchronize_session=False)
    )
    changed = getattr(updated, "rowcount", None)
    if changed != 1:
        raise PermissionError("research_graph_final_writer_fence_lost")


def graph_requires_native_binding_in_session(
    session: Session,
    run_id: str,
    *,
    has_checkpoint_locator: bool = False,
) -> bool:
    """Metadata-only dispatch; raw adapter-return is also valid on legacy V1."""
    metadata = session.execute(
        select(RunRoleOutputRecord.role, RunRoleOutputRecord.schema_version).where(
            RunRoleOutputRecord.run_id == run_id
        )
    ).all()
    required = has_checkpoint_locator
    for role, schema in metadata:
        if (
            role in {"research_compiled_policy", "research_publication_source"}
            or role in {"research_request_pack", "research_task_request"}
            and schema not in {"research-request-pack.v1", "research-task-request.v1"}
            or role == "research_execution_method"
            and schema != "research-execution-method.v1"
            or role.startswith(("research_observed:", "research_transport:", "research_graph_"))
        ):
            required = True
        suffix = role.removeprefix("context_execution_link:")
        if (
            role.startswith("context_execution_link:")
            and suffix.isascii()
            and suffix.isdecimal()
            and not suffix.startswith("0")
        ):
            required = True
        for prefix, legacy in (
            ("dsh_execution_request:", "dsh-execution-request.v1"),
            ("dsh_prompt_integrity:", "dsh-prompt-integrity.v1"),
        ):
            suffix = role.removeprefix(prefix)
            if (
                role.startswith(prefix)
                and suffix.isdecimal()
                and int(suffix) >= 1
                and schema != legacy
            ):
                required = True
    if required:
        if not isinstance(read_research_bundle_in_session(session, run_id), FrozenResearchBundle):
            raise ValueError("research_graph_native_bundle_missing")
    return required


def _original_request(session: Session, run_id: str) -> ResearchSessionRequest:
    bundle = read_research_bundle_in_session(session, run_id)
    if not isinstance(bundle, FrozenResearchBundle):
        raise ValueError("research_graph_native_bundle_missing")
    return ResearchSessionRequest.model_validate(bundle.task.request.model_dump(mode="json"))


def prepare_graph_state_in_session(
    session: Session, state: AgenticResearchState, expected_lease_owner: str | None
) -> tuple[bool, AgenticResearchState]:
    request = ResearchSessionRequest.model_validate(state["request"])
    has_locator = bool(
        state.get("graph_round_admission_ref") or state.get("graph_final_projection_ref")
    )
    if not graph_requires_native_binding_in_session(
        session, request.run_id, has_checkpoint_locator=has_locator
    ):
        return False, state
    _permission(session, request.run_id, expected_lease_owner)
    original = _original_request(session, request.run_id)
    if request != original:
        raise ValueError("research_graph_checkpoint_task_changed")
    if state.get("graph_final_projection_ref"):
        frozen_final = read_graph_final_in_session(
            session,
            run_id=request.run_id,
            reference=parse_graph_final_ref(state["graph_final_projection_ref"]),
            expected_lease_owner=expected_lease_owner,
            require_current_permission=True,
        )
        frozen_round = read_graph_round_in_session(
            session,
            run_id=request.run_id,
            reference=frozen_final.receipt.last_admission_ref,
            expected_lease_owner=expected_lease_owner,
            require_current_permission=True,
        )
        expected: AgenticResearchState = {
            **frozen_round.state,
            "graph_round_admission_ref": frozen_round.reference.model_dump(mode="json"),
            "graph_final_projection_ref": frozen_final.reference.model_dump(mode="json"),
            "final_result": frozen_final.result.model_dump(mode="json"),
            "decision_snapshot_id": frozen_final.receipt.decision_snapshot_id,
            "stop_code": frozen_final.result.stop_reason.code,
            "stop_detail": frozen_final.result.stop_reason.detail,
        }
    elif state.get("graph_round_admission_ref"):
        frozen_round = read_graph_round_in_session(
            session,
            run_id=request.run_id,
            reference=parse_graph_round_ref(state["graph_round_admission_ref"]),
            expected_lease_owner=expected_lease_owner,
            require_current_permission=True,
        )
        logical_round = state.get("current_round", frozen_round.receipt.logical_round)
        if logical_round != frozen_round.receipt.logical_round and (
            logical_round != frozen_round.receipt.logical_round + 1
            or not should_continue_after_round(frozen_round.state)
        ):
            raise ValueError("research_graph_checkpoint_round_invalid")
        expected = {
            **frozen_round.state,
            "current_round": logical_round,
            "graph_round_admission_ref": frozen_round.reference.model_dump(mode="json"),
        }
    else:
        if (
            session.scalar(
                select(RunRoleOutputRecord.output_id).where(
                    RunRoleOutputRecord.run_id == request.run_id,
                    RunRoleOutputRecord.role.like("research_graph_%"),
                )
            )
            is not None
        ):
            raise ValueError("research_graph_checkpoint_original_locator_missing")
        expected = initial_research_state(original)
    # Cache JSON never becomes authorization or a replacement projection. An
    # inconsistent checkpoint is diagnosed instead of silently signing it.
    if state != expected:
        raise ValueError("research_graph_checkpoint_projection_changed")
    return True, expected


def _row_ref(row: RunRoleOutputRecord) -> ObservedExecutionRef:
    recorded = as_utc(row.created_at)
    if (
        recorded is None
        or hashlib.sha256(row.payload_json.encode()).hexdigest() != row.payload_hash
    ):
        raise ValueError("research_graph_original_row_invalid")
    return ObservedExecutionRef(
        output_id=row.output_id,
        role=row.role,
        schema_version=row.schema_version,
        payload_hash=row.payload_hash,
        recorded_at=recorded,
    )


def _context_valid(
    session: Session, context: ResearchNativeObservationContext, *, active: bool
) -> None:
    saved = read_prepared_execution_in_session(session, context.run_id, context.generation)
    if active:
        if (
            native_observation_context_in_session(session, context.run_id, prepared=saved)
            != context
        ):
            raise ValueError("research_graph_native_context_changed")
    else:
        # Historical consumers may read an existing Artifact without promoting
        # a completed Run to active. Original six rows and whole Prompt remain
        # exact; the native reader authenticates all generations and the bundle.
        run = session.get(RunRecord, context.run_id, populate_existing=True)
        if (
            run is None
            or context.event_id != run.event_id
            or context.snapshot_id != run.snapshot_id
            or context.request_id != saved.execution.request.request_id
            or context.dsh_session_id != saved.execution.dsh_session_id
            or context.original_task_request_hash != saved.execution.task_request_hash
            or context.bundle_hash != saved.method.definition.bundle.bundle_hash
            or context.method_hash != saved.method.method_hash
            or context.execution_request_hash != saved.execution.request_hash
            or context.first_linked_request_hash != saved.method.request_hash
            or context.prompt_hash != hashlib.sha256(saved.prompt.prompt.encode()).hexdigest()
        ):
            raise ValueError("research_graph_native_context_changed")
        roles = (
            *BUNDLE_ROLES,
            METHOD_ROLE,
            execution_request_role(context.generation),
            prompt_integrity_role(context.generation),
        )
        if tuple(reference.role for reference in context.rows) != roles:
            raise ValueError("research_graph_native_context_changed")
        for reference in context.rows:
            row = session.get(RunRoleOutputRecord, reference.output_id, populate_existing=True)
            if row is None or row.run_id != context.run_id or _row_ref(row) != reference:
                raise ValueError("research_graph_native_context_changed")


def _mapped(
    session: Session, reference: ObservedExecutionRef, *, active: bool
) -> ResearchMappedObservation:
    observed = read_mapped_observation(session, reference)
    _context_valid(session, observed.context, active=active)
    raw = read_transport_observation(session, observed.mapping_input_ref)
    latest = read_transport_observation(session, observed.latest_transport_ref)
    _context_valid(session, raw.context, active=active)
    _context_valid(session, latest.context, active=active)
    if (
        raw.context.run_id != observed.context.run_id
        or latest.context != observed.context
        or raw.phase != "result"
        or raw.reply is None
        or observed.mapping_input_generation != raw.context.generation
        or observed.latest_transport_generation != observed.context.generation
        or research_result_content_hash(observed.result) != observed.result_content_hash
    ):
        raise ValueError("research_graph_mapping_binding_invalid")
    # Pointer integrity is the existing Runtime mapping witness; it is not an
    # independent Kernel tool matcher or a checker-executed assertion.
    if not isinstance(raw.reply, DshSessionResult):
        raise ValueError("research_graph_mapping_input_invalid")
    payload = raw.reply
    if (
        payload.run_id != raw.context.run_id
        or payload.dsh_session_id != raw.context.dsh_session_id
        or payload.generation != raw.context.generation
        or payload.result_hash
        != hashlib.sha256(
            (payload.final_response + "\0" + payload.events_json).encode()
        ).hexdigest()
        or raw.events_json_hash != hashlib.sha256(payload.events_json.encode()).hexdigest()
        or raw.final_response_hash != hashlib.sha256(payload.final_response.encode()).hexdigest()
        or observed.result.request_id != raw.context.request_id
        or observed.result.research_session_id != payload.dsh_session_id
    ):
        raise ValueError("research_graph_mapping_identity_invalid")
    verify_selection_witness(raw.reply, observed.selection)
    candidate = observed.selection.candidate
    if candidate is not None and (
        candidate.request_id != raw.context.request_id
        or observed.result.causal_case != candidate.causal_case
        or observed.result.horizons != candidate.horizons
    ):
        raise ValueError("research_graph_selected_projection_changed")
    if observed.selection.kind == "evidence_only" and (
        observed.result.causal_case or observed.result.horizons
    ):
        raise ValueError("research_graph_evidence_only_projection_invalid")
    return observed


def _columns(row: Base) -> dict[str, object]:
    table = row.__table__
    assert isinstance(table, Table)
    result: dict[str, object] = {}
    for column in table.columns:
        value = getattr(row, column.key)
        if isinstance(value, datetime):
            timestamp = as_utc(value)
            assert timestamp is not None
            result[column.key] = timestamp.isoformat()
        else:
            result[column.key] = value
    return result


def _material_rows(
    session: Session, run_id: str, evidence_ids: list[str], fact_ids: list[str]
) -> list[GraphRowFingerprint]:
    rows: list[Base] = []
    for model, ids in (
        (ResearchEvidenceRecord, evidence_ids),
        (ResearchFactRecord, fact_ids),
        (EvidenceParsedLinkRecord, evidence_ids),
        (RunInputRefRecord, fact_ids),
    ):
        for identifier in ids:
            row = session.get(model, identifier, populate_existing=True)
            if row is not None:
                rows.append(row)
            elif model in {ResearchEvidenceRecord, ResearchFactRecord, RunInputRefRecord}:
                raise ValueError("research_graph_material_lineage_missing")
    parsed_ids = [r.parsed_version_id for r in rows if isinstance(r, EvidenceParsedLinkRecord)]
    version_ids = [r.fact_version_id for r in rows if isinstance(r, RunInputRefRecord)]
    for model, ids in ((ParsedVersionRecord, parsed_ids), (FactVersionRecord, version_ids)):
        for identifier in sorted(set(ids)):
            row = session.get(model, identifier, populate_existing=True)
            if row is None:
                raise ValueError("research_graph_material_lineage_missing")
            rows.append(row)
    result = []
    for row in rows:
        table = row.__table__
        assert isinstance(table, Table)
        if getattr(row, "run_id", run_id) != run_id:
            raise ValueError("research_graph_material_run_mismatch")
        key = list(table.primary_key.columns)[0].key
        result.append(
            GraphRowFingerprint(
                table=table.name,
                row_id=str(getattr(row, key)),
                payload_hash=observation_sha(_columns(row)),
            )
        )
    return sorted(result, key=lambda r: (r.table, r.row_id))


def _verify_material(session: Session, receipt: ResearchGraphRoundAdmission) -> None:
    current = {
        (r.table, r.row_id): r
        for r in _material_rows(
            session,
            receipt.run_id,
            [e.evidence_id for e in receipt.evidence],
            [f.fact_id for f in receipt.facts],
        )
    }
    # Later rounds may append a ParsedVersion for an existing Evidence. They
    # cannot alter any original material/lineage row already frozen here.
    if any(current.get((r.table, r.row_id)) != r for r in receipt.material_rows):
        raise ValueError("research_graph_admitted_material_changed")
    evidence_ids = {e.evidence_id for e in receipt.evidence}
    fact_ids = {f.fact_id for f in receipt.facts}
    if (
        len(evidence_ids) != len(receipt.evidence)
        or len(fact_ids) != len(receipt.facts)
        or [
            e
            for e in ResearchEvidenceService.list_run_evidence_in_session(session, receipt.run_id)
            if e.evidence_id in evidence_ids
        ]
        != receipt.evidence
        or [
            f
            for f in ResearchFactStore.list_run_facts_in_session(session, receipt.run_id)
            if f.fact_id in fact_ids
        ]
        != receipt.facts
    ):
        raise ValueError("research_graph_material_projection_invalid")


def _read[T: ResearchGraphRoundAdmission | ResearchGraphFinalProjection](
    session: Session,
    reference: ObservedExecutionRef,
    model: type[T],
    run_id: str,
) -> T:
    row = session.get(RunRoleOutputRecord, reference.output_id, populate_existing=True)
    if row is None or row.run_id != run_id or _row_ref(row) != reference:
        raise ValueError("research_graph_locator_invalid")
    value = model.model_validate_json(row.payload_json)
    prefix = (
        "research_graph_round" if model is ResearchGraphRoundAdmission else "research_graph_final"
    )
    digest = observation_sha(value.model_dump(mode="json", exclude={"recorded_at"}))
    role = (
        f"{prefix}:{value.logical_round}"
        if isinstance(value, ResearchGraphRoundAdmission)
        else prefix
    )
    if (
        row.role != role
        or row.schema_version != value.schema_version
        or row.output_id != prefix + "_" + digest
        or value.run_id != run_id
        or as_utc(row.created_at) != value.recorded_at
        or observation_json(value.model_dump(mode="json")) != row.payload_json
        or value.recipe_hash != _recipe_hash()
    ):
        raise ValueError("research_graph_saved_receipt_invalid")
    return value


def _store(
    session: Session, value: ResearchGraphRoundAdmission | ResearchGraphFinalProjection
) -> ObservedExecutionRef:
    _require_writer(session)
    prefix = (
        "research_graph_round"
        if isinstance(value, ResearchGraphRoundAdmission)
        else "research_graph_final"
    )
    role = (
        f"{prefix}:{value.logical_round}"
        if isinstance(value, ResearchGraphRoundAdmission)
        else prefix
    )
    digest = observation_sha(value.model_dump(mode="json", exclude={"recorded_at"}))
    encoded = observation_json(value.model_dump(mode="json"))
    if len(encoded.encode()) > MAX_RECEIPT_BYTES:
        raise ValueError("research_graph_receipt_bytes_exceeded")
    existing = session.scalar(
        select(RunRoleOutputRecord)
        .where(RunRoleOutputRecord.run_id == value.run_id, RunRoleOutputRecord.role == role)
        .execution_options(populate_existing=True)
    )
    if existing is not None:
        previous = _read(session, _row_ref(existing), type(value), value.run_id)
        if previous.model_dump(mode="json", exclude={"recorded_at"}) != value.model_dump(
            mode="json", exclude={"recorded_at"}
        ):
            raise ValueError("research_graph_original_receipt_conflict")
        return _row_ref(existing)
    row = RunRoleOutputRecord(
        output_id=prefix + "_" + digest,
        run_id=value.run_id,
        role=role,
        schema_version=value.schema_version,
        payload_json=encoded,
        payload_hash=hashlib.sha256(encoded.encode()).hexdigest(),
        created_at=value.recorded_at,
    )
    session.add(row)
    session.flush()
    return _row_ref(row)


def _previous_state(
    session: Session,
    run_id: str,
    previous: GraphRoundAdmissionRef | None,
    owner: str | None,
    logical_round: int,
) -> AgenticResearchState:
    request = _original_request(session, run_id)
    if previous is None:
        if logical_round != 1:
            raise ValueError("research_graph_first_round_invalid")
        return initial_research_state(request)
    frozen = read_graph_round_in_session(
        session,
        run_id=run_id,
        reference=previous,
        expected_lease_owner=owner,
        require_current_permission=True,
    )
    if logical_round != frozen.receipt.logical_round + 1 or not should_continue_after_round(
        frozen.state
    ):
        raise ValueError("research_graph_continuation_not_authorized")
    return {**frozen.state, "current_round": logical_round}


def _check_round_request(
    base: ResearchSessionRequest, actual: ResearchSessionRequest, state: AgenticResearchState
) -> None:
    expected_requirements = requirements_for_round(
        base.evidence_requirements, state, allowed_capabilities=base.allowed_capabilities
    )
    expected_budget = base.execution_budget.model_copy(
        update={
            "max_tool_calls": base.execution_budget.max_tool_calls
            - state.get("total_tool_calls", 0)
        }
    )
    if (
        actual.current_round > base.execution_budget.max_evidence_rounds
        or actual.evidence_requirements != expected_requirements
        or actual.execution_budget != expected_budget
    ):
        raise ValueError("research_graph_round_request_projection_invalid")


def read_graph_round_in_session(
    session: Session,
    *,
    run_id: str,
    reference: GraphRoundAdmissionRef,
    expected_lease_owner: str | None,
    require_current_permission: bool,
) -> FrozenGraphRound:
    if require_current_permission:
        _permission(session, run_id, expected_lease_owner)
    receipt = _read(session, reference.row, ResearchGraphRoundAdmission, run_id)
    _context_valid(session, receipt.native_context, active=require_current_permission)
    _verify_material(session, receipt)
    base = _original_request(session, run_id)
    previous: FrozenGraphRound | None = None
    if receipt.previous_ref is not None:
        if receipt.previous_ref.row.role != f"research_graph_round:{receipt.logical_round - 1}":
            raise ValueError("research_graph_round_chain_invalid")
        previous = read_graph_round_in_session(
            session,
            run_id=run_id,
            reference=receipt.previous_ref,
            expected_lease_owner=expected_lease_owner,
            require_current_permission=require_current_permission,
        )
        if receipt.logical_round != previous.receipt.logical_round + 1:
            raise ValueError("research_graph_round_chain_invalid")
        state: AgenticResearchState = {**previous.state, "current_round": receipt.logical_round}
    else:
        if receipt.logical_round != 1:
            raise ValueError("research_graph_round_chain_invalid")
        state = initial_research_state(base)
    saved = read_prepared_execution_in_session(session, run_id, receipt.native_context.generation)
    actual = ResearchSessionRequest.model_validate(saved.execution.request.model_dump(mode="json"))
    if actual.current_round != receipt.logical_round:
        raise ValueError("research_graph_round_execution_mismatch")
    _check_round_request(base, actual, state)
    link = session.get(DshSessionLinkRecord, run_id, populate_existing=True)
    if (
        link is None
        or receipt.observed_tool_calls > base.execution_budget.max_tool_calls
        or receipt.observed_tool_calls > link.tool_calls_started
    ):
        raise ValueError("research_graph_original_tool_counter_invalid")
    if receipt.observation_kind == "mapped":
        mapped = _mapped(session, receipt.observation_ref, active=require_current_permission)
        if mapped.context != receipt.native_context:
            raise ValueError("research_graph_round_observation_mismatch")
        admitted = [e for e in receipt.evidence if e.evidence_id in receipt.admitted_evidence_ids]
        delta = project_round(
            state, base, actual, mapped.result, receipt.evidence, receipt.facts, admitted
        )
        calculated = {**state, **delta}
        if receipt.selected_synthesis_observation_ref != receipt.observation_ref:
            if (
                receipt.previous_ref is None
                or previous is None
                or mapped.selection.kind != "evidence_only"
                or not mapped.result.synthesis_failure_code
                or receipt.selected_synthesis_observation_ref
                != previous.receipt.selected_synthesis_observation_ref
            ):
                raise ValueError("research_graph_selected_synthesis_invalid")
            prior_mapped = _mapped(
                session,
                receipt.selected_synthesis_observation_ref,
                active=require_current_permission,
            )
            if (
                prior_mapped.selection.kind == "evidence_only"
                or prior_mapped.result.synthesis_failure_code
            ):
                raise ValueError("research_graph_prior_synthesis_not_admissible")
            calculated["latest_result"] = prior_mapped.result.model_dump(mode="json")
        if receipt.latest_observation_ref != mapped.latest_transport_ref:
            raise ValueError("research_graph_latest_observation_invalid")
    else:
        raw = _failure(session, receipt.observation_ref, active=require_current_permission)
        if raw.context != receipt.native_context:
            raise ValueError("research_graph_round_observation_mismatch")
        if (
            receipt.previous_ref is None
            or previous is None
            or receipt.selected_synthesis_observation_ref
            != previous.receipt.selected_synthesis_observation_ref
            or receipt.latest_observation_ref != receipt.observation_ref
        ):
            raise ValueError("research_graph_failure_projection_invalid")
        calculated = {
            **state,
            **project_failure(
                state,
                base,
                _error(raw),
                receipt.evidence,
                receipt.facts,
                observed_tool_calls=receipt.observed_tool_calls,
            ),
        }
    if observation_sha(calculated) != receipt.state_hash or calculated != receipt.projected_state:
        raise ValueError("research_graph_projection_changed")
    selected = _mapped(
        session, receipt.selected_synthesis_observation_ref, active=require_current_permission
    )
    latest = (
        read_transport_observation(session, receipt.latest_observation_ref)
        if receipt.observation_kind == "failure"
        else read_transport_observation(
            session,
            _mapped(
                session, receipt.observation_ref, active=require_current_permission
            ).latest_transport_ref,
        )
    )
    if (
        selected.mapping_input_generation != receipt.selected_mapping_generation
        or latest.context.generation != receipt.latest_transport_generation
    ):
        raise ValueError("research_graph_projection_generations_invalid")
    return FrozenGraphRound(reference, receipt, cast(AgenticResearchState, calculated))


def _existing_round(
    session: Session,
    run_id: str,
    logical_round: int,
    observation_ref: ObservedExecutionRef,
    owner: str | None,
    previous: GraphRoundAdmissionRef | None,
) -> FrozenGraphRound | None:
    row = session.scalar(
        select(RunRoleOutputRecord)
        .where(
            RunRoleOutputRecord.run_id == run_id,
            RunRoleOutputRecord.role == f"research_graph_round:{logical_round}",
        )
        .execution_options(populate_existing=True)
    )
    if row is None:
        return None
    frozen = read_graph_round_in_session(
        session,
        run_id=run_id,
        reference=GraphRoundAdmissionRef(row=_row_ref(row)),
        expected_lease_owner=owner,
        require_current_permission=True,
    )
    if frozen.receipt.observation_ref != observation_ref or frozen.receipt.previous_ref != previous:
        raise ValueError("research_graph_original_round_conflict")
    return frozen


def admit_observed_round_in_session(
    session: Session,
    *,
    run_id: str,
    expected_lease_owner: str | None,
    observed: ObservedResearchResult,
    database: Database,
    previous: GraphRoundAdmissionRef | None,
) -> FrozenGraphRound:
    _require_writer(session)
    run = _permission(session, run_id, expected_lease_owner)
    mapped = _mapped(session, observed.observation_ref, active=True)
    if mapped.context.run_id != run_id or mapped.result != observed.result:
        raise ValueError("research_graph_declared_runtime_result_mismatch")
    actual = ResearchSessionRequest.model_validate(
        read_prepared_execution_in_session(
            session, run_id, mapped.context.generation
        ).execution.request.model_dump(mode="json")
    )
    if existing := _existing_round(
        session,
        run_id,
        actual.current_round,
        observed.observation_ref,
        expected_lease_owner,
        previous,
    ):
        _fence(session, run_id, expected_lease_owner)
        return existing
    if (
        read_prepared_execution_in_session(session, run_id).execution.generation
        != mapped.context.generation
    ):
        raise ValueError("research_graph_current_transport_changed")
    state = _previous_state(session, run_id, previous, expected_lease_owner, actual.current_round)
    base = _original_request(session, run_id)
    _check_round_request(base, actual, state)
    cutoff = _round_cutoff(state, actual, mapped.result)
    evidence_service = ResearchEvidenceService(database)
    accepted = evidence_service.accept_candidates_in_session(
        session,
        run_id=run_id,
        capability_id="dsh.research",
        candidates=mapped.result.evidence_candidates,
        requirements={r.requirement_id: r for r in base.evidence_requirements},
        cutoff_at=cutoff,
        facts=mapped.result.facts or [],
    )
    session.flush()
    fact_store = ResearchFactStore(evidence_service.database)
    fact_store.accept_facts_in_session(
        session,
        run_id=run_id,
        capability_id="dsh.research",
        facts=mapped.result.facts or [],
        cutoff_at=cutoff,
    )
    evidence = evidence_service.list_run_evidence_in_session(session, run_id)
    facts = fact_store.list_run_facts_in_session(session, run_id)
    updated = {
        **state,
        **project_round(state, base, actual, mapped.result, evidence, facts, accepted),
    }
    selected_ref = observed.observation_ref
    selected_generation = mapped.mapping_input_generation
    if (
        mapped.selection.kind == "evidence_only"
        and mapped.result.synthesis_failure_code
        and previous
    ):
        original = read_graph_round_in_session(
            session,
            run_id=run_id,
            reference=previous,
            expected_lease_owner=expected_lease_owner,
            require_current_permission=True,
        )
        selected = _mapped(
            session, original.receipt.selected_synthesis_observation_ref, active=True
        )
        if (
            selected.selection.kind != "evidence_only"
            and not selected.result.synthesis_failure_code
        ):
            # Actual prior mapped synthesis, not historical capture presence.
            selected_ref = original.receipt.selected_synthesis_observation_ref
            selected_generation = original.receipt.selected_mapping_generation
            updated["latest_result"] = selected.result.model_dump(mode="json")
    now = utcnow()
    receipt = ResearchGraphRoundAdmission(
        run_id=run_id,
        logical_round=actual.current_round,
        previous_ref=previous,
        observation_ref=observed.observation_ref,
        observation_kind="mapped",
        observed_tool_calls=mapped.result.total_tool_calls,
        native_context=mapped.context,
        selected_synthesis_observation_ref=selected_ref,
        latest_observation_ref=mapped.latest_transport_ref,
        selected_mapping_generation=selected_generation,
        latest_transport_generation=mapped.context.generation,
        recipe_hash=_recipe_hash(),
        evidence=evidence,
        facts=facts,
        admitted_evidence_ids=[e.evidence_id for e in accepted],
        material_rows=_material_rows(
            session, run_id, [e.evidence_id for e in evidence], [f.fact_id for f in facts]
        ),
        projected_state=updated,
        state_hash=observation_sha(updated),
        admitted_lease_owner=cast(str, expected_lease_owner),
        writer_clock_at=now,
        lease_expires_at=cast(datetime, as_utc(run.lease_expires_at)),
        recorded_at=now,
    )
    reference = GraphRoundAdmissionRef(row=_store(session, receipt))
    _context_valid(session, mapped.context, active=True)
    _verify_material(session, receipt)
    _fence(session, run_id, expected_lease_owner)
    return FrozenGraphRound(reference, receipt, cast(AgenticResearchState, updated))


def _failure(
    session: Session, reference: ObservedExecutionRef, *, active: bool
) -> ResearchTransportObservation:
    raw = read_transport_observation(session, reference)
    _context_valid(session, raw.context, active=active)
    if raw.phase != "error" or raw.error is None or raw.reply is not None:
        raise ValueError("research_graph_continuation_error_witness_invalid")
    error = raw.error
    if (
        error.error_code == "research_observation_unavailable"
        or error.error_code
        not in {
            "structured_output_invalid",
            "model_timeout",
            "provider_timeout",
            "provider_unavailable",
            "dsh_host_unavailable",
        }
        or not error.retryable
        and error.error_code != "structured_output_invalid"
    ):
        raise ValueError("research_graph_continuation_error_not_admissible")
    return raw


def _error(raw: ResearchTransportObservation) -> AgentExecutionError:
    error = raw.error
    assert error is not None
    return AgentExecutionError(
        error.error_code,
        error.message,
        retryable=error.retryable,
        attempt=error.attempt,
        provider_id=error.provider_id,
        model=error.model,
        origin=error.origin,
        cause_code=error.cause_code,
        capability_id=error.capability_id,
        tool_call_id=error.tool_call_id,
        deadline_ms=error.deadline_ms,
    )


def record_continuation_failure_in_session(
    session: Session,
    *,
    run_id: str,
    expected_lease_owner: str | None,
    previous: GraphRoundAdmissionRef,
    database: Database,
    failure: ObservedAgentExecutionError,
) -> FrozenGraphRound:
    _require_writer(session)
    run = _permission(session, run_id, expected_lease_owner)
    raw = _failure(session, failure.observation_ref, active=True)
    logical_round = read_prepared_execution_in_session(
        session, run_id, raw.context.generation
    ).execution.request.current_round
    if raw.context.run_id != run_id:
        raise ValueError("research_graph_failure_run_mismatch")
    if existing := _existing_round(
        session,
        run_id,
        logical_round,
        failure.observation_ref,
        expected_lease_owner,
        previous,
    ):
        _fence(session, run_id, expected_lease_owner)
        return existing
    if (
        read_prepared_execution_in_session(session, run_id).execution.generation
        != raw.context.generation
    ):
        raise ValueError("research_graph_current_transport_changed")
    old = read_graph_round_in_session(
        session,
        run_id=run_id,
        reference=previous,
        expected_lease_owner=expected_lease_owner,
        require_current_permission=True,
    )
    selected = _mapped(session, old.receipt.selected_synthesis_observation_ref, active=True)
    if (
        selected.selection.kind == "evidence_only"
        or selected.result.synthesis_failure_code
        or not selected.result.causal_case
        or not selected.result.horizons
    ):
        raise ValueError("research_graph_prior_synthesis_not_admissible")
    state = _previous_state(session, run_id, previous, expected_lease_owner, logical_round)
    base = _original_request(session, run_id)
    evidence = ResearchEvidenceService(database).list_run_evidence_in_session(session, run_id)
    facts = ResearchFactStore(database).list_run_facts_in_session(session, run_id)
    link = session.get(DshSessionLinkRecord, run_id, populate_existing=True)
    if link is None or link.generation != raw.context.generation or link.tool_calls_started < 0:
        raise ValueError("research_graph_failure_tool_counter_unavailable")
    updated = {
        **state,
        **project_failure(
            state, base, _error(raw), evidence, facts, observed_tool_calls=link.tool_calls_started
        ),
    }
    now = utcnow()
    receipt = ResearchGraphRoundAdmission(
        run_id=run_id,
        logical_round=logical_round,
        previous_ref=previous,
        observation_ref=failure.observation_ref,
        observation_kind="failure",
        observed_tool_calls=link.tool_calls_started,
        native_context=raw.context,
        selected_synthesis_observation_ref=old.receipt.selected_synthesis_observation_ref,
        latest_observation_ref=failure.observation_ref,
        selected_mapping_generation=old.receipt.selected_mapping_generation,
        latest_transport_generation=raw.context.generation,
        recipe_hash=_recipe_hash(),
        evidence=evidence,
        facts=facts,
        admitted_evidence_ids=[],
        material_rows=_material_rows(
            session, run_id, [e.evidence_id for e in evidence], [f.fact_id for f in facts]
        ),
        projected_state=updated,
        state_hash=observation_sha(updated),
        admitted_lease_owner=cast(str, expected_lease_owner),
        writer_clock_at=now,
        lease_expires_at=cast(datetime, as_utc(run.lease_expires_at)),
        recorded_at=now,
    )
    reference = GraphRoundAdmissionRef(row=_store(session, receipt))
    _context_valid(session, raw.context, active=True)
    _verify_material(session, receipt)
    _fence(session, run_id, expected_lease_owner)
    return FrozenGraphRound(reference, receipt, cast(AgenticResearchState, updated))


def freeze_graph_final_in_session(
    session: Session,
    *,
    run_id: str,
    expected_lease_owner: str | None,
    last_admission: GraphRoundAdmissionRef,
    database: Database,
) -> FrozenGraphFinal:
    _require_writer(session)
    _permission(session, run_id, expected_lease_owner)
    frozen = read_graph_round_in_session(
        session,
        run_id=run_id,
        reference=last_admission,
        expected_lease_owner=expected_lease_owner,
        require_current_permission=True,
    )
    prepared = read_prepared_execution_in_session(session, run_id)
    if prepared.execution.generation != frozen.receipt.latest_transport_generation:
        raise ValueError("research_graph_latest_transport_not_admitted")
    final = project_final(frozen.state)
    snapshot = ResearchEvidenceService(database).freeze_decision_snapshot_in_session(
        session,
        run_id=run_id,
        evidence_refs=[e.evidence_id for e in frozen.receipt.evidence if e.quality == "accepted"],
        cutoff_at=_decision_cutoff(frozen.state, _original_request(session, run_id)),
    )
    row = session.get(SnapshotRecord, snapshot.snapshot_id, populate_existing=True)
    assert row is not None
    receipt = ResearchGraphFinalProjection(
        run_id=run_id,
        last_admission_ref=last_admission,
        selected_synthesis_observation_ref=frozen.receipt.selected_synthesis_observation_ref,
        latest_observation_ref=frozen.receipt.latest_observation_ref,
        native_context=frozen.receipt.native_context,
        recipe_hash=_recipe_hash(),
        decision_snapshot_id=row.snapshot_id,
        decision_snapshot_hash=row.snapshot_hash,
        decision_snapshot_row_hash=observation_sha(_columns(row)),
        decision_cutoff_at=cast(datetime, as_utc(row.cutoff_at)),
        projected_result=final,
        projected_result_hash=observation_sha(final.model_dump(mode="json")),
        projected_result_content_hash=research_result_content_hash(final),
        recorded_at=utcnow(),
    )
    existing = session.scalar(
        select(RunRoleOutputRecord)
        .where(
            RunRoleOutputRecord.run_id == run_id, RunRoleOutputRecord.role == "research_graph_final"
        )
        .execution_options(populate_existing=True)
    )
    if existing is not None:
        prior = read_graph_final_in_session(
            session,
            run_id=run_id,
            reference=GraphFinalProjectionRef(row=_row_ref(existing)),
            expected_lease_owner=expected_lease_owner,
            require_current_permission=True,
        )
        if prior.receipt.last_admission_ref != last_admission:
            raise ValueError("research_graph_final_original_conflict")
        _fence(session, run_id, expected_lease_owner)
        return prior
    reference = GraphFinalProjectionRef(row=_store(session, receipt))
    _context_valid(session, frozen.receipt.native_context, active=True)
    _verify_material(session, frozen.receipt)
    _snapshot_valid(session, frozen, row)
    _fence(session, run_id, expected_lease_owner)
    return FrozenGraphFinal(reference, receipt, final)


def _snapshot_valid(session: Session, frozen: FrozenGraphRound, row: SnapshotRecord) -> None:
    base = _original_request(session, frozen.receipt.run_id)
    run = session.get(RunRecord, frozen.receipt.run_id, populate_existing=True)
    trigger = session.get(SnapshotRecord, base.trigger_snapshot_id, populate_existing=True)
    cutoff = _decision_cutoff(frozen.state, base)
    if (
        run is None
        or trigger is None
        or row.event_id != run.event_id
        or row.run_id != run.run_id
        or row.parent_snapshot_id != trigger.snapshot_id
        or row.snapshot_type != "decision"
        or row.generation != 2
        or as_utc(row.cutoff_at) != cutoff
        or as_utc(trigger.cutoff_at) is None
        or cutoff < cast(datetime, as_utc(trigger.cutoff_at))
        or cutoff > base.pit_cutoff_at
        or cutoff > base.deadline_at
    ):
        raise ValueError("research_graph_decision_snapshot_binding_invalid")
    import json

    evidence = [
        *json.loads(trigger.evidence_json),
        *(
            e.model_dump(mode="json")
            for e in sorted(frozen.receipt.evidence, key=lambda e: e.evidence_id)
            if e.quality == "accepted"
        ),
    ]
    semantic = {
        "cutoff_at": cutoff.isoformat(),
        "event_id": run.event_id,
        "evidence": evidence,
        "generation": 2,
        "parent_snapshot_id": trigger.snapshot_id,
        "run_id": run.run_id,
        "snapshot_type": "decision",
    }
    digest = observation_sha(semantic)
    if (
        row.snapshot_hash != digest
        or row.snapshot_id != f"snap_dec_{digest[:28]}"
        or json.loads(row.evidence_json) != evidence
    ):
        raise ValueError("research_graph_decision_snapshot_content_invalid")


def read_graph_final_in_session(
    session: Session,
    *,
    run_id: str,
    reference: GraphFinalProjectionRef,
    expected_lease_owner: str | None,
    require_current_permission: bool,
) -> FrozenGraphFinal:
    receipt = _read(session, reference.row, ResearchGraphFinalProjection, run_id)
    frozen = read_graph_round_in_session(
        session,
        run_id=run_id,
        reference=receipt.last_admission_ref,
        expected_lease_owner=expected_lease_owner,
        require_current_permission=require_current_permission,
    )
    current = read_prepared_execution_in_session(session, run_id)
    final = project_final(frozen.state)
    run = session.get(RunRecord, run_id, populate_existing=True)
    snapshot = session.get(SnapshotRecord, receipt.decision_snapshot_id, populate_existing=True)
    if snapshot is not None:
        _snapshot_valid(session, frozen, snapshot)
    if (
        run is None
        or snapshot is None
        or run.decision_snapshot_id != snapshot.snapshot_id
        or snapshot.run_id != run_id
        or snapshot.event_id != run.event_id
        or snapshot.snapshot_type != "decision"
        or snapshot.generation != 2
        or snapshot.parent_snapshot_id != run.snapshot_id
        or as_utc(snapshot.cutoff_at) != receipt.decision_cutoff_at
        or snapshot.snapshot_hash != receipt.decision_snapshot_hash
        or observation_sha(_columns(snapshot)) != receipt.decision_snapshot_row_hash
        or current.execution.generation != frozen.receipt.latest_transport_generation
        or receipt.native_context != frozen.receipt.native_context
        or receipt.selected_synthesis_observation_ref
        != frozen.receipt.selected_synthesis_observation_ref
        or receipt.latest_observation_ref != frozen.receipt.latest_observation_ref
        or final != receipt.projected_result
        or observation_sha(final.model_dump(mode="json")) != receipt.projected_result_hash
        or research_result_content_hash(final) != receipt.projected_result_content_hash
    ):
        raise ValueError("research_graph_final_projection_invalid")
    return FrozenGraphFinal(reference, receipt, final)


def verify_declared_graph_result_in_session(
    session: Session,
    *,
    run_id: str,
    reference: GraphFinalProjectionRef,
    declared_result: ResearchSessionResult,
    expected_lease_owner: str | None,
    allow_reviews_only: bool = False,
) -> FrozenGraphFinal:
    frozen = read_graph_final_in_session(
        session,
        run_id=run_id,
        reference=reference,
        expected_lease_owner=expected_lease_owner,
        require_current_permission=True,
    )
    if (
        research_result_content_hash(declared_result)
        != frozen.receipt.projected_result_content_hash
        or not allow_reviews_only
        and declared_result != frozen.result
    ):
        raise ValueError("research_graph_declared_final_result_mismatch")
    # allow_reviews_only permits only the separate original native review
    # consumer to validate reviews; this function does not attest those rows.
    _permission(session, run_id, expected_lease_owner)
    return frozen

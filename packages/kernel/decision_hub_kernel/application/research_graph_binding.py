"""Native Graph admissions and deterministic projections in one SQLite writer.

Runtime mapping receipts are observations, not publication permission. This
module binds each adopted observation to original inputs and durable admitted
materials. It never calls a Host, charges usage, signs a caller Result, or imports
orchestration/Commit. Legacy runs do not gain replacement native receipts.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, TypedDict, cast

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from sqlalchemy import Table, event, select, update
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts import (
    DshSessionResult,
    EvidenceCandidate,
    FactEnvelope,
    ResearchSessionRequest,
    ResearchSessionResult,
)
from packages.kernel.decision_hub_kernel.application import (
    research_graph_projection_v3 as quality_projection,
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
from packages.kernel.decision_hub_kernel.application.research_candidate_clock import (
    candidate_clock_state,
    read_candidate_clock_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchEvidenceService,
)
from packages.kernel.decision_hub_kernel.application.research_execution_binding import (
    FrozenExecutionPreparation,
    read_prepared_execution_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_graph_history_versions import (
    replay_retained_graph_final_in_session,
    replay_retained_graph_round_in_session,
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
from packages.kernel.decision_hub_kernel.application.research_graph_projection_v2 import (
    RECIPE_VERSION_V2,
    eligible_synthesis,
    project_final_v2,
    project_round_v2,
)
from packages.kernel.decision_hub_kernel.application.research_graph_recipe import (
    read_graph_recipe_selection_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_graph_recipe_v3 import (
    SELECTION_SCHEMA_V3,
    FrozenGraphRecipeSelectionV3,
    read_graph_recipe_selection_v3_in_session,
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
from packages.kernel.decision_hub_kernel.application.research_quality_projection import (
    QualityProjectionContextV1,
)
from packages.kernel.decision_hub_kernel.application.research_quality_sidecar import (
    ResearchQualitySidecarV1,
    build_server_quality_context,
    persist_quality_sidecar_in_session,
    read_quality_sidecar_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_review import (
    research_result_content_hash,
)
from packages.kernel.decision_hub_kernel.decision.citation_membership_v2 import (
    CitationDisposition,
    CitationMembershipCheckV2,
)
from packages.kernel.decision_hub_kernel.decision.research_quality import QualityDecisionV1
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
    ResearchMappedObservationV2,
    ResearchNativeObservationContext,
    ResearchTransportObservation,
    Sha,
)
from packages.kernel.decision_hub_kernel.ports.research_synthesis_protocol import synthesis_protocol
from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError

ROUND_SCHEMA = "research-graph-round-admission.v1"
FINAL_SCHEMA = "research-graph-final-projection.v1"
ROUND_SCHEMA_V2 = "research-graph-round-admission.v2"
FINAL_SCHEMA_V2 = "research-graph-final-projection.v2"
ROUND_SCHEMA_V3 = "research-graph-round-admission.v3"
FINAL_SCHEMA_V3 = "research-graph-final-projection.v3"
PERPETUAL_ROUND_SCHEMA = "research-perpetual-graph-round.v1"
PERPETUAL_FINAL_SCHEMA = "research-perpetual-graph-final.v1"
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


class ResearchPerpetualGraphRound(ResearchGraphRoundAdmission):
    schema_version: Literal["research-perpetual-graph-round.v1"] = PERPETUAL_ROUND_SCHEMA
    recipe_version: Literal["research-perpetual-graph.v1"] = "research-perpetual-graph.v1"
    financial_qualification: Literal["unqualified"] = "unqualified"


class ResearchPerpetualGraphFinal(ResearchGraphFinalProjection):
    schema_version: Literal["research-perpetual-graph-final.v1"] = PERPETUAL_FINAL_SCHEMA
    recipe_version: Literal["research-perpetual-graph.v1"] = "research-perpetual-graph.v1"
    financial_qualification: Literal["unqualified"] = "unqualified"


class ResearchGraphRoundAdmissionV2(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["research-graph-round-admission.v2"] = ROUND_SCHEMA_V2
    run_id: str
    logical_round: int = Field(strict=True, ge=1)
    previous_ref: GraphRoundAdmissionRef | None
    observation_ref: ObservedExecutionRef
    observation_kind: Literal["mapped", "failure"]
    observed_tool_calls: int = Field(strict=True, ge=0)
    native_context: ResearchNativeObservationContext
    selected_synthesis_observation_ref: ObservedExecutionRef | None
    latest_observation_ref: ObservedExecutionRef
    selected_mapping_generation: int | None = Field(strict=True, ge=1)
    latest_transport_generation: int = Field(strict=True, ge=1)
    recipe_version: Literal["research-graph-projection.v2"] = RECIPE_VERSION_V2
    recipe_selection_ref: ObservedExecutionRef
    recipe_hash: Sha
    citation_disposition: CitationDisposition
    citation_check: CitationMembershipCheckV2 | None
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


class ResearchGraphFinalProjectionV2(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["research-graph-final-projection.v2"] = FINAL_SCHEMA_V2
    run_id: str
    last_admission_ref: GraphRoundAdmissionRef
    selected_synthesis_observation_ref: ObservedExecutionRef | None
    latest_observation_ref: ObservedExecutionRef
    native_context: ResearchNativeObservationContext
    recipe_version: Literal["research-graph-projection.v2"] = RECIPE_VERSION_V2
    recipe_selection_ref: ObservedExecutionRef
    recipe_hash: Sha
    citation_disposition: CitationDisposition
    latest_citation_disposition: CitationDisposition
    citation_check: CitationMembershipCheckV2
    decision_snapshot_id: str
    decision_snapshot_hash: Sha
    decision_snapshot_row_hash: Sha
    decision_cutoff_at: AwareDatetime
    projected_result: ResearchSessionResult
    projected_result_hash: Sha
    projected_result_content_hash: Sha
    recorded_at: AwareDatetime


class ResearchGraphRoundAdmissionV3(ResearchGraphRoundAdmissionV2):
    # Pydantic freezes these literals and revalidates each variant independently.
    schema_version: Literal["research-graph-round-admission.v3"] = ROUND_SCHEMA_V3  # pyright: ignore[reportIncompatibleVariableOverride]
    recipe_version: Literal["research-graph-projection.v3"] = "research-graph-projection.v3"  # pyright: ignore[reportIncompatibleVariableOverride]
    quality_sidecar_id: str
    quality_context_hash: Sha
    quality_decision_hash: Sha
    source_result_hash: Sha


class ResearchGraphFinalProjectionV3(ResearchGraphFinalProjectionV2):
    schema_version: Literal["research-graph-final-projection.v3"] = FINAL_SCHEMA_V3  # pyright: ignore[reportIncompatibleVariableOverride]
    recipe_version: Literal["research-graph-projection.v3"] = "research-graph-projection.v3"  # pyright: ignore[reportIncompatibleVariableOverride]
    quality_sidecar_id: str
    round_quality_sidecar_id: str
    quality_context_hash: Sha
    quality_decision_hash: Sha
    source_result_hash: Sha


type GraphRoundReceipt = (
    ResearchGraphRoundAdmission | ResearchGraphRoundAdmissionV2 | ResearchGraphRoundAdmissionV3 | ResearchPerpetualGraphRound
)
type GraphFinalReceipt = (
    ResearchGraphFinalProjection | ResearchGraphFinalProjectionV2 | ResearchGraphFinalProjectionV3 | ResearchPerpetualGraphFinal
)
type GraphReceipt = GraphRoundReceipt | GraphFinalReceipt


def parse_graph_final_projection_declaration(
    payload_json: str, *, schema_version: str
) -> GraphFinalReceipt:
    """Strict JSON/schema dispatch only; callers must authenticate the original."""
    if schema_version == FINAL_SCHEMA:
        return ResearchGraphFinalProjection.model_validate_json(payload_json)
    if schema_version == FINAL_SCHEMA_V2:
        return ResearchGraphFinalProjectionV2.model_validate_json(payload_json)
    if schema_version == FINAL_SCHEMA_V3:
        return ResearchGraphFinalProjectionV3.model_validate_json(payload_json)
    if schema_version == PERPETUAL_FINAL_SCHEMA:
        return ResearchPerpetualGraphFinal.model_validate_json(payload_json)
    raise ValueError("research_graph_final_schema_unsupported")


def authenticated_graph_recipe_identity(receipt: GraphFinalReceipt) -> dict[str, str] | None:
    """Derive recipe content after a caller has authenticated this exact receipt.

    This pure function does not grant publication or authenticate a declaration.
    Instance identifiers and clocks are excluded from the stable recipe.
    """
    if isinstance(receipt, ResearchGraphFinalProjectionV2):
        return {"recipe_version": receipt.recipe_version, "dependency_hash": receipt.recipe_hash}
    return None


@dataclass(frozen=True)
class FrozenGraphRound:
    reference: GraphRoundAdmissionRef
    receipt: GraphRoundReceipt
    state: AgenticResearchState


@dataclass(frozen=True)
class FrozenGraphFinal:
    reference: GraphFinalProjectionRef
    receipt: GraphFinalReceipt
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


def _recipe_selection(session: Session, run_id: str):
    schema = session.scalar(
        select(RunRoleOutputRecord.schema_version).where(
            RunRoleOutputRecord.run_id == run_id,
            RunRoleOutputRecord.role == "graph_recipe_selection",
        )
    )
    if schema == SELECTION_SCHEMA_V3:
        return read_graph_recipe_selection_v3_in_session(session, run_id)
    return read_graph_recipe_selection_in_session(session, run_id)


def _continue_for_round(frozen: FrozenGraphRound) -> bool:
    if isinstance(frozen.receipt, ResearchPerpetualGraphRound):
        return False
    if isinstance(frozen.receipt, ResearchGraphRoundAdmissionV3):
        return quality_projection.should_continue_after_round(frozen.state)
    return should_continue_after_round(frozen.state)


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
    if has_locator:
        _recipe_selection(session, request.run_id)
    original = _original_request(session, request.run_id)
    if request != original:
        raise ValueError("research_graph_checkpoint_task_changed")
    # Admitted rounds/finals retain their original late-persistence read path.
    if not has_locator:
        if utcnow() >= original.deadline_at:
            raise PermissionError("context_selection_deadline_elapsed")
        _recipe_selection(session, request.run_id)
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
            or not _continue_for_round(frozen_round)
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
    # The physical row boundary is still checked for malformed legacy NULLs.
    recorded = as_utc(cast(datetime | None, row.created_at))
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


@dataclass
class _LocalOriginalRead:
    """Function-local original reuse; never a current permission decision."""

    session: Session
    connection: Any
    driver: sqlite3.Connection | None
    transaction: Any
    nested: Any
    changes: int
    invalidated: bool = False

    def unchanged(self) -> bool:
        return bool(
            not self.invalidated
            and self.driver is not None
            and self.driver.in_transaction
            and self.driver.total_changes == self.changes
            and self.session.get_transaction() is self.transaction
            and self.session.get_nested_transaction() is self.nested
            and not self.session.new
            and not self.session.dirty
            and not self.session.deleted
            and self.session.connection() is self.connection
        )


@contextmanager
def _local_original_read(session: Session) -> Generator[_LocalOriginalRead]:
    connection = session.connection()
    native = connection.connection.driver_connection
    driver = native if isinstance(native, sqlite3.Connection) else None
    boundary = _LocalOriginalRead(
        session,
        connection,
        driver,
        session.get_transaction(),
        session.get_nested_transaction(),
        driver.total_changes if driver is not None else -1,
    )

    def flush_started(*_args: Any) -> None:
        boundary.invalidated = True

    def statement_started(
        _connection: Any,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        if not statement.lstrip().upper().startswith("SELECT "):
            boundary.invalidated = True

    event.listen(session, "before_flush", flush_started)
    event.listen(connection, "before_cursor_execute", statement_started)
    try:
        yield boundary
    finally:
        boundary.invalidated = True
        event.remove(connection, "before_cursor_execute", statement_started)
        event.remove(session, "before_flush", flush_started)


def _preparation_bytes(saved: FrozenExecutionPreparation) -> str:
    return observation_json(
        {
            "method": saved.method.model_dump(mode="json"),
            "link": saved.link.model_dump(mode="json"),
            "prompt": saved.prompt.model_dump(mode="json"),
            "execution": saved.execution.model_dump(mode="json"),
            "integrity": saved.integrity.model_dump(mode="json"),
        }
    )


@dataclass(frozen=True)
class _CompletedPreparation:
    context_json: str
    preparation_json: str
    ancestry: tuple[tuple[str, int], ...]
    active: bool
    preparation: FrozenExecutionPreparation


@dataclass(frozen=True)
class _CompletedMapping:
    reference_json: str
    mapped_json: str
    raw_json: str
    latest_json: str
    ancestry: tuple[tuple[str, int], ...]
    active: bool


def _original_ancestry() -> tuple[tuple[str, int], ...]:
    # The existing shared stack enforces strictly earlier same-Run dependencies.
    # A proof from another ancestry must not bypass a new recursive read.
    from .research_context_execution import _PROOF_STACK  # pyright: ignore[reportPrivateUsage]

    return _PROOF_STACK.get()


def _original_contract_signatures() -> tuple[bytes, ...] | None:
    # The same audited pure signature also detects mutable live canonical
    # schema/registry/formatter changes that filesystem bytes cannot detect.
    from packages.contracts_py.decision_hub_contracts import (
        ResearchCompiledPolicy,
        ResearchCompiledPolicyReceiptV1,
        ResearchRequestPackReceiptV2,
        ResearchTaskReceiptV2,
    )
    from packages.contracts_py.decision_hub_contracts import binding_contract_base as base
    from packages.contracts_py.decision_hub_contracts import policy_contract_base as policy

    pairs = (
        (base._validator, ResearchRequestPackReceiptV2),  # pyright: ignore[reportPrivateUsage]
        (base._validator, ResearchTaskReceiptV2),  # pyright: ignore[reportPrivateUsage]
        (base._validator, ResearchCompiledPolicyReceiptV1),  # pyright: ignore[reportPrivateUsage]
        (policy._validator, ResearchCompiledPolicy),  # pyright: ignore[reportPrivateUsage]
    )
    signatures: list[bytes] = []
    for validator, model in pairs:
        signature = base._shape_schema_signature(validator(model))  # pyright: ignore[reportPrivateUsage]
        if signature is None:
            return None  # Unknown extensions keep the original full reader.
        signatures.append(signature)
    return tuple(signatures)


def _original_inventory(root: Path) -> tuple[str, ...]:
    # Keep the complete fresh lexical inventory, including retained capsules.
    # DirEntry metadata avoids per-file pathlib stat/ancestor reconstruction;
    # no file hashes, runtime bindings or mutable schema checks are cached.
    names: list[str] = []
    for relative, suffixes in (
        ("packages", {".py"}),
        ("contracts/schemas", {".json", ".yaml", ".yml"}),
    ):
        directory = root / relative
        if not directory.exists():
            continue
        pending = [(os.fspath(directory), relative)]
        while pending:
            current, prefix = pending.pop()
            with os.scandir(current) as entries:
                for entry in entries:
                    name = prefix + "/" + entry.name
                    if entry.is_dir(follow_symlinks=False):
                        pending.append((entry.path, name))
                    elif entry.is_file() and os.path.splitext(entry.name)[1] in suffixes:
                        names.append(name)
    return tuple(sorted(names))


_BOUNDED_INVENTORY_PRUNES = frozenset({
    "packages/kernel/research_recipe_versions",
    "packages/kernel/market_component_versions",
})


def _bounded_current_inventory(root: Path) -> tuple[str, ...]:
    """Fresh current source names without descending into retained archives.

    Only the two content-addressed stores whose consumed selection is pinned by
    the callback lease are pruned. Other version directories remain current
    until their own exact selected resources have the same native protection.
    """
    names: list[str] = []
    for relative, suffixes in (
        ("packages", {".py"}),
        ("contracts/schemas", {".json", ".yaml", ".yml"}),
    ):
        directory = root / relative
        if not directory.exists():
            continue
        pending = [(os.fspath(directory), relative)]
        while pending:
            current, prefix = pending.pop()
            with os.scandir(current) as entries:
                for entry in entries:
                    name = prefix + "/" + entry.name
                    if entry.is_dir(follow_symlinks=False):
                        if name not in _BOUNDED_INVENTORY_PRUNES:
                            pending.append((entry.path, name))
                    elif entry.is_file() and os.path.splitext(entry.name)[1] in suffixes:
                        names.append(name)
    return tuple(sorted(names))


def _retained_original_files(root: Path) -> tuple[Path, ...]:
    # Dynamic private readers are not enumerated by static Graph imports.
    # Include their actual reviewed resources without altering their bytes.
    application = root / "packages/kernel/decision_hub_kernel/application"
    return tuple(
        sorted(
            path
            for folder in ("context_selector_versions", "research_recipe_versions")
            for path in (application / folder).rglob("*")
            if path.is_file() and path.suffix in {".py", ".json", ".yaml", ".yml"}
        )
    )


@dataclass(frozen=True)
class _ReadSourceGuard:
    root: Path
    files: tuple[tuple[str, str], ...]
    inventory: tuple[str, ...]
    runtime_versions: tuple[tuple[str, str], ...]
    selector_identity: str
    contract_signatures: tuple[bytes, ...]
    bounded_inventory: tuple[str, ...] = ()

    @classmethod
    def capture(cls) -> _ReadSourceGuard | None:
        from ..persistence.research_source_snapshot import active_source_snapshot

        scope = active_source_snapshot(cls)
        if scope is not None:
            scope.require_read(scope.session, scope.run_id)
            return scope.guard
        from . import research_graph_recipe as recipe
        from .research_context_selection import (
            _selector_identity,  # pyright: ignore[reportPrivateUsage]
        )

        try:
            signatures = _original_contract_signatures()
            if signatures is None:
                return None
            manifest = recipe.graph_recipe_manifest_v2()
            root = recipe._ROOT  # pyright: ignore[reportPrivateUsage]
            files = {
                item.path: item.payload_hash
                for item in (*manifest.source_files, *manifest.schema_files)
            }
            files.update(
                (path.relative_to(root).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest())
                for path in _retained_original_files(root)
            )
            return cls(
                root,
                tuple(sorted(files.items())),
                _original_inventory(root),
                tuple(sorted(manifest.runtime_versions.items())),
                observation_json(_selector_identity()),
                signatures,
                _bounded_current_inventory(root),
            )
        except (ValueError, OSError, LookupError, TypeError):
            return None

    def matches_identity(self) -> bool:
        import sys
        from importlib.metadata import version

        from . import research_graph_recipe as recipe
        from .research_context_selection import (
            _selector_identity,  # pyright: ignore[reportPrivateUsage]
        )

        try:
            known_files = {relative for relative, _ in self.files}
            if (
                recipe._ROOT != self.root  # pyright: ignore[reportPrivateUsage]
                or observation_json(_selector_identity()) != self.selector_identity
                or _original_inventory(self.root) != self.inventory
                or any(
                    path.relative_to(self.root).as_posix() not in known_files
                    for path in _retained_original_files(self.root)
                )
                or _original_contract_signatures() != self.contract_signatures
            ):
                return False
            current = {
                name: version(name)
                for name, _ in self.runtime_versions
                if name not in {"python", "sqlite"}
            }
            current.update(python=sys.version, sqlite=sqlite3.sqlite_version)
            return tuple(sorted(current.items())) == self.runtime_versions
        except (ValueError, OSError, LookupError, TypeError):
            return False

    def matches(self) -> bool:
        from ..persistence.research_source_snapshot import active_source_snapshot

        scope = active_source_snapshot(type(self))
        if scope is not None:
            return scope.matches_guard(self)
        # Default callers retain complete fresh bytes. Identity-only checks
        # gain meaning solely while a callback owns immutable OS byte handles.
        try:
            return self.matches_identity() and all(
                hashlib.sha256((self.root / relative).read_bytes()).hexdigest() == digest
                for relative, digest in self.files
            )
        except (ValueError, OSError, LookupError, TypeError):
            return False


    def matches_scope_identity(self) -> bool:
        """Fresh live bindings solely inside the V2 immutable HANDLE scope.

        Complete inventory/path identity stays at entry and at the final
        pre-seal cut. The callback lease may use this bounded helper only after
        the strict reader authenticated the selection and pinned the exact
        selected capsule/registry bytes with immutable native handles. This
        helper alone grants nothing; default identity checks remain full.
        """
        import sys
        from importlib.metadata import version

        from . import research_graph_recipe as recipe
        from .research_context_selection import _selector_identity

        try:
            if (recipe._ROOT != self.root
                    or observation_json(_selector_identity()) != self.selector_identity
                    or _bounded_current_inventory(self.root) != self.bounded_inventory
                    or _original_contract_signatures() != self.contract_signatures):
                return False
            current = {name: version(name) for name, _ in self.runtime_versions
                if name not in {"python", "sqlite"}}
            current.update(python=sys.version, sqlite=sqlite3.sqlite_version)
            return tuple(sorted(current.items())) == self.runtime_versions
        except (ValueError, OSError, LookupError, TypeError):
            return False


@dataclass
class _PublicationFinalRead:
    session: Session
    boundary: _LocalOriginalRead
    source: _ReadSourceGuard | None
    finals: dict[str, tuple[FrozenGraphFinal, str]]
    used: bool = False


_PUBLICATION_FINAL_READ: ContextVar[_PublicationFinalRead | None] = ContextVar(
    "research_publication_final_read", default=None
)


@contextmanager
def publication_final_read_scope(session: Session) -> Generator[None]:
    """Reuse a completed exact final only inside one clean physical read.

    Source identity is checked at the end before the caller can return a view.
    Writes, transaction changes, other sessions and active permission reads do
    not reuse it. The first final still undergoes the complete native proof.
    """
    with _local_original_read(session) as boundary:
        scope = _PublicationFinalRead(session, boundary, None, {})
        token = _PUBLICATION_FINAL_READ.set(scope)
        try:
            yield
            if scope.used and (
                not boundary.unchanged() or scope.source is None or not scope.source.matches()
            ):
                raise ValueError("research_graph_original_source_changed")
        finally:
            scope.finals.clear()
            _PUBLICATION_FINAL_READ.reset(token)


class _OriginalProofs:
    """Completed originals inside one physical read; no permission verdicts."""

    def __init__(self, session: Session, boundary: _LocalOriginalRead) -> None:
        self.session = session
        self.boundary = boundary
        self.preparations: list[_CompletedPreparation] = []
        self.mappings: list[_CompletedMapping] = []
        self.retained_bytes = 0
        self.source = _ReadSourceGuard.capture() if boundary.unchanged() else None
        self.reuse_count = 0

    def usable(self, session: Session) -> bool:
        return self.session is session and self.boundary.unchanged()

    def require_source_unchanged(self) -> None:
        if self.reuse_count and (self.source is None or not self.source.matches()):
            raise ValueError("research_graph_original_source_changed")

    def _reserve(self, size: int) -> bool:
        # These small function-local bounds are a fallback, never a proof gate.
        if len(self.preparations) + len(self.mappings) >= 16 or (
            size > 1024 * 1024 or self.retained_bytes + size > 4 * 1024 * 1024
        ):
            return False
        self.retained_bytes += size
        return True

    def preparation(
        self, session: Session, context: ResearchNativeObservationContext, *, active: bool
    ) -> FrozenExecutionPreparation | None:
        if not self.usable(session):
            return None
        context_json = observation_json(context.model_dump(mode="json"))
        ancestry = _original_ancestry()
        for proof in self.preparations:
            if (
                proof.active is active
                and proof.ancestry == ancestry
                and proof.context_json == context_json
                and _preparation_bytes(proof.preparation) == proof.preparation_json
            ):
                if self.source is None or not self.source.matches():
                    return None
                for part in (
                    proof.preparation.method,
                    proof.preparation.link,
                    proof.preparation.prompt,
                    proof.preparation.execution,
                    proof.preparation.integrity,
                ):
                    type(part).model_validate_json(part.model_dump_json())
                # Actual Run and original row references remain fresh on reuse.
                _context_binding_valid(session, context, proof.preparation, active=active)
                if (
                    self.usable(session)
                    and _preparation_bytes(proof.preparation) == proof.preparation_json
                    and observation_json(context.model_dump(mode="json")) == proof.context_json
                    and _original_ancestry() == proof.ancestry
                ):
                    self.reuse_count += 1
                    return proof.preparation
        return None

    def remember_preparation(
        self,
        context: ResearchNativeObservationContext,
        saved: FrozenExecutionPreparation,
        *,
        active: bool,
    ) -> None:
        if not self.usable(self.session) or self.source is None or not self.source.matches():
            return
        context_json = observation_json(context.model_dump(mode="json"))
        preparation_json = _preparation_bytes(saved)
        if self._reserve(len(context_json.encode()) + len(preparation_json.encode())):
            self.preparations.append(
                _CompletedPreparation(
                    context_json,
                    preparation_json,
                    _original_ancestry(),
                    active,
                    saved,
                )
            )

    def mapped(
        self,
        session: Session,
        reference: ObservedExecutionRef,
        observed: ResearchMappedObservation,
        raw: ResearchTransportObservation,
        latest: ResearchTransportObservation,
        *,
        active: bool,
    ) -> bool:
        if not self.usable(session):
            return False
        values = tuple(
            observation_json(item.model_dump(mode="json"))
            for item in (
                reference,
                observed,
                raw,
                latest,
            )
        )
        ancestry = _original_ancestry()
        found = any(
            proof.active is active
            and proof.ancestry == ancestry
            and (proof.reference_json, proof.mapped_json, proof.raw_json, proof.latest_json)
            == values
            for proof in self.mappings
        )
        if found and self.source is not None and self.source.matches():
            self.reuse_count += 1
            return True
        return False

    def remember_mapping(
        self,
        reference: ObservedExecutionRef,
        observed: ResearchMappedObservation,
        raw: ResearchTransportObservation,
        latest: ResearchTransportObservation,
        *,
        active: bool,
    ) -> None:
        if not self.usable(self.session) or self.source is None or not self.source.matches():
            return
        values = tuple(
            observation_json(item.model_dump(mode="json"))
            for item in (
                reference,
                observed,
                raw,
                latest,
            )
        )
        if self._reserve(sum(len(value.encode()) for value in values)):
            self.mappings.append(
                _CompletedMapping(
                    values[0],
                    values[1],
                    values[2],
                    values[3],
                    _original_ancestry(),
                    active,
                )
            )


def _context_valid(
    session: Session,
    context: ResearchNativeObservationContext,
    *,
    active: bool,
    originals: _OriginalProofs | None = None,
) -> FrozenExecutionPreparation:
    if originals is not None:
        saved = originals.preparation(session, context, active=active)
        if saved is not None:
            return saved
    saved = read_prepared_execution_in_session(session, context.run_id, context.generation)
    _context_binding_valid(session, context, saved, active=active)
    if originals is not None:
        originals.remember_preparation(context, saved, active=active)
    return saved


def _context_binding_valid(
    session: Session,
    context: ResearchNativeObservationContext,
    saved: FrozenExecutionPreparation,
    *,
    active: bool,
) -> None:
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
    session: Session,
    reference: ObservedExecutionRef,
    *,
    active: bool,
    originals: _OriginalProofs | None = None,
) -> ResearchMappedObservation | ResearchMappedObservationV2:
    if reference.schema_version == "research-mapped-observation.v2":
        from .research_publication_binding import read_mapped_perpetual_observation
        observed_v2 = read_mapped_perpetual_observation(session, reference)
        _context_valid(session, observed_v2.context, active=active, originals=originals)
        if synthesis_protocol(_original_request(session, observed_v2.context.run_id).output_schema_ref) != "decision_v2":
            raise ValueError("research_perpetual_graph_protocol_mismatch")
        return observed_v2
    with _local_original_read(session) as boundary:
        proofs = (
            originals
            if originals is not None and originals.usable(session)
            else (_OriginalProofs(session, boundary))
        )
        observed = read_mapped_observation(session, reference)
        saved = _context_valid(session, observed.context, active=active, originals=proofs)
        context_bytes = observation_json(observed.context.model_dump(mode="json"))
        preparation_bytes = _preparation_bytes(saved)
        raw = read_transport_observation(session, observed.mapping_input_ref)
        latest = read_transport_observation(session, observed.latest_transport_ref)
        for context in (raw.context, latest.context):
            if (
                boundary.unchanged()
                and observation_json(context.model_dump(mode="json")) == context_bytes
                and _preparation_bytes(saved) == preparation_bytes
            ):
                # Fresh Run and each original row/hash still bind this context.
                _context_binding_valid(session, context, saved, active=active)
            else:
                _context_valid(session, context, active=active, originals=proofs)
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
            or raw.final_response_hash
            != hashlib.sha256(payload.final_response.encode()).hexdigest()
            or observed.result.request_id != raw.context.request_id
            or observed.result.research_session_id != payload.dsh_session_id
        ):
            raise ValueError("research_graph_mapping_identity_invalid")
        if proofs.mapped(session, reference, observed, raw, latest, active=active):
            # All three original rows/whole hashes and native bindings were read
            # above. Only the exact completed selection/candidate proof is reused.
            proofs.require_source_unchanged()
            return observed
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
        proofs.remember_mapping(reference, observed, raw, latest, active=active)
        proofs.require_source_unchanged()
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


def _verify_material(session: Session, receipt: GraphRoundReceipt) -> None:
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


def _read[T: GraphReceipt](
    session: Session,
    reference: ObservedExecutionRef,
    model: type[T],
    run_id: str,
) -> T:
    row = session.get(RunRoleOutputRecord, reference.output_id, populate_existing=True)
    if row is None or row.run_id != run_id or _row_ref(row) != reference:
        raise ValueError("research_graph_locator_invalid")
    value = model.model_validate_json(row.payload_json)
    is_round = isinstance(value, (ResearchGraphRoundAdmission, ResearchGraphRoundAdmissionV2))
    prefix = "research_graph_round" if is_round else "research_graph_final"
    digest = observation_sha(value.model_dump(mode="json", exclude={"recorded_at"}))
    role = (
        f"{prefix}:{value.logical_round}"
        if isinstance(value, (ResearchGraphRoundAdmission, ResearchGraphRoundAdmissionV2))
        else prefix
    )
    selection = _recipe_selection(session, run_id)
    if isinstance(value, (ResearchPerpetualGraphRound, ResearchPerpetualGraphFinal)):
        from .research_perpetual_graph import recipe_hash
        valid_recipe = value.recipe_hash == recipe_hash()
        if synthesis_protocol(_original_request(session, run_id).output_schema_ref) != "decision_v2":
            raise ValueError("research_perpetual_graph_protocol_mismatch")
    elif isinstance(value, (ResearchGraphRoundAdmissionV2, ResearchGraphFinalProjectionV2)):
        if isinstance(
            value, (ResearchGraphRoundAdmissionV3, ResearchGraphFinalProjectionV3)
        ) != isinstance(selection, FrozenGraphRecipeSelectionV3):
            raise ValueError("research_graph_recipe_chain_mixed")
        if (
            selection is None
            or value.recipe_selection_ref != selection.reference
            or value.recipe_hash != selection.receipt.dependency_hash
            or value.recorded_at < selection.receipt.recorded_at
        ):
            raise ValueError("research_graph_recipe_binding_invalid")
        if value.recorded_at > utcnow():
            raise ValueError("research_graph_receipt_clock_invalid")
        if isinstance(value, ResearchGraphRoundAdmissionV2):
            references = [value.observation_ref, value.latest_observation_ref]
            if value.previous_ref is not None:
                references.append(value.previous_ref.row)
            if value.selected_synthesis_observation_ref is not None:
                references.append(value.selected_synthesis_observation_ref)
            if (
                value.writer_clock_at != value.recorded_at
                or value.lease_expires_at <= value.writer_clock_at
                or not value.admitted_lease_owner
                or any(ref.recorded_at > value.writer_clock_at for ref in references)
                or (value.selected_synthesis_observation_ref is None)
                != (value.selected_mapping_generation is None)
            ):
                raise ValueError("research_graph_round_clock_or_identity_invalid")
        elif (
            value.last_admission_ref.row.recorded_at > value.recorded_at
            or value.latest_observation_ref.recorded_at > value.recorded_at
        ):
            raise ValueError("research_graph_final_clock_invalid")
        valid_recipe = True
    else:
        valid_recipe = selection is None and value.recipe_hash == _recipe_hash()
    if (
        row.role != role
        or row.schema_version != value.schema_version
        or row.output_id != prefix + "_" + digest
        or value.run_id != run_id
        or as_utc(row.created_at) != value.recorded_at
        or observation_json(value.model_dump(mode="json")) != row.payload_json
        or not valid_recipe
    ):
        raise ValueError("research_graph_saved_receipt_invalid")
    return value


def _store(session: Session, value: GraphReceipt) -> ObservedExecutionRef:
    _require_writer(session)
    prefix = (
        "research_graph_round"
        if isinstance(value, (ResearchGraphRoundAdmission, ResearchGraphRoundAdmissionV2))
        else "research_graph_final"
    )
    role = (
        f"{prefix}:{value.logical_round}"
        if isinstance(value, (ResearchGraphRoundAdmission, ResearchGraphRoundAdmissionV2))
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
    if logical_round != frozen.receipt.logical_round + 1 or not _continue_for_round(frozen):
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


def _read_round_receipt(
    session: Session, reference: ObservedExecutionRef, run_id: str
) -> GraphRoundReceipt:
    if reference.schema_version == PERPETUAL_ROUND_SCHEMA:
        return _read(session, reference, ResearchPerpetualGraphRound, run_id)
    if reference.schema_version == ROUND_SCHEMA:
        return _read(session, reference, ResearchGraphRoundAdmission, run_id)
    if reference.schema_version == ROUND_SCHEMA_V2:
        return _read(session, reference, ResearchGraphRoundAdmissionV2, run_id)
    if reference.schema_version == ROUND_SCHEMA_V3:
        return _read(session, reference, ResearchGraphRoundAdmissionV3, run_id)
    raise ValueError("research_graph_round_schema_unsupported")


def _read_final_receipt(
    session: Session, reference: ObservedExecutionRef, run_id: str
) -> GraphFinalReceipt:
    if reference.schema_version == PERPETUAL_FINAL_SCHEMA:
        return _read(session, reference, ResearchPerpetualGraphFinal, run_id)
    if reference.schema_version == FINAL_SCHEMA:
        return _read(session, reference, ResearchGraphFinalProjection, run_id)
    if reference.schema_version == FINAL_SCHEMA_V2:
        return _read(session, reference, ResearchGraphFinalProjectionV2, run_id)
    if reference.schema_version == FINAL_SCHEMA_V3:
        return _read(session, reference, ResearchGraphFinalProjectionV3, run_id)
    raise ValueError("research_graph_final_schema_unsupported")


def _project_observed_v2(
    session: Session,
    *,
    state: AgenticResearchState,
    base: ResearchSessionRequest,
    actual: ResearchSessionRequest,
    observation_ref: ObservedExecutionRef,
    mapped: ResearchMappedObservation,
    evidence: list[EvidenceCandidate],
    facts: list[FactEnvelope],
    admitted: list[EvidenceCandidate],
    previous: FrozenGraphRound | None,
    active: bool,
    originals: _OriginalProofs | None = None,
) -> tuple[
    AgenticResearchState,
    CitationMembershipCheckV2,
    ObservedExecutionRef | None,
    int | None,
]:
    prior_result: ResearchSessionResult | None = None
    prior_ref: ObservedExecutionRef | None = None
    prior_generation: int | None = None
    if previous is not None:
        if not isinstance(previous.receipt, ResearchGraphRoundAdmissionV2):
            raise ValueError("research_graph_recipe_chain_mixed")
        prior_ref = previous.receipt.selected_synthesis_observation_ref
        prior_generation = previous.receipt.selected_mapping_generation
        if prior_ref is not None:
            prior = _mapped(session, prior_ref, active=active, originals=originals)
            if prior.selection.kind == "evidence_only" or prior.result.synthesis_failure_code:
                raise ValueError("research_graph_prior_synthesis_not_admissible")
            prior_result = prior.result
    candidate_clock = read_candidate_clock_in_session(
        session, mapped_ref=observation_ref, request=actual
    )
    delta, check = project_round_v2(
        state,
        base,
        actual,
        mapped.result,
        evidence,
        facts,
        admitted,
        prior_selected_result=prior_result,
        candidate_reference_at=candidate_clock.reference_at if candidate_clock else None,
    )
    if (
        synthesis_protocol(actual.output_schema_ref) == "decision"
        and actual.execution_mode == "live"
    ):
        delta["candidate_clock"] = candidate_clock_state(
            candidate_clock, mapped_ref=observation_ref
        )
    if mapped.selection.kind != "evidence_only" and eligible_synthesis(mapped.result, check):
        selected_ref = observation_ref
        selected_generation = mapped.mapping_input_generation
    else:
        selected_ref = prior_ref
        selected_generation = prior_generation
    return {**state, **delta}, check, selected_ref, selected_generation


type _QualityParts = tuple[
    QualityProjectionContextV1,
    QualityDecisionV1,
    tuple[ObservedExecutionRef, ...],
    str,
    tuple[str, ...],
]


class _ProjectionArguments(TypedDict):
    state: AgenticResearchState
    base: ResearchSessionRequest
    actual: ResearchSessionRequest
    observation_ref: ObservedExecutionRef
    mapped: ResearchMappedObservation
    evidence: list[EvidenceCandidate]
    facts: list[FactEnvelope]
    admitted: list[EvidenceCandidate]
    previous: FrozenGraphRound | None
    active: bool
    originals: _OriginalProofs | None


def _project_observed_v3(
    session: Session,
    *,
    state: AgenticResearchState,
    base: ResearchSessionRequest,
    actual: ResearchSessionRequest,
    observation_ref: ObservedExecutionRef,
    mapped: ResearchMappedObservation,
    evidence: list[EvidenceCandidate],
    facts: list[FactEnvelope],
    admitted: list[EvidenceCandidate],
    previous: FrozenGraphRound | None,
    active: bool,
    selection: FrozenGraphRecipeSelectionV3,
    assessed_at: datetime,
    originals: _OriginalProofs | None = None,
) -> tuple[
    AgenticResearchState,
    CitationMembershipCheckV2,
    ObservedExecutionRef | None,
    int | None,
    QualityProjectionContextV1,
    QualityDecisionV1,
    tuple[ObservedExecutionRef, ...],
    str,
    tuple[str, ...],
]:
    prior_result: ResearchSessionResult | None = None
    prior_ref: ObservedExecutionRef | None = None
    prior_generation: int | None = None
    if previous is not None:
        if not isinstance(previous.receipt, ResearchGraphRoundAdmissionV3):
            raise ValueError("research_graph_recipe_chain_mixed")
        prior_ref = previous.receipt.selected_synthesis_observation_ref
        prior_generation = previous.receipt.selected_mapping_generation
        if prior_ref is not None:
            prior = _mapped(session, prior_ref, active=active, originals=originals)
            if prior.selection.kind == "evidence_only" or prior.result.synthesis_failure_code:
                raise ValueError("research_graph_prior_synthesis_not_admissible")
            prior_result = prior.result
    if mapped.context.run_id != base.run_id:
        raise ValueError("research_graph_round_observation_mismatch")
    preparation = _context_valid(session, mapped.context, active=active, originals=originals)
    context, proofs, runtime_json, unknowns = build_server_quality_context(
        session,
        selection=selection.receipt,
        source_result=mapped.result,
        native_context=mapped.context,
        mapped_ref=observation_ref,
        previous_mapped_ref=(previous.receipt.observation_ref if previous is not None
                             and previous.receipt.observation_ref.schema_version
                             == "research-mapped-observation.v1" else None),
        preparation=preparation,
        assessed_at=assessed_at,
    )
    candidate_clock = read_candidate_clock_in_session(
        session, mapped_ref=observation_ref, request=actual
    )
    delta, check, quality = quality_projection.project_round_v3(
        state,
        base,
        actual,
        mapped.result,
        evidence,
        facts,
        admitted,
        prior_selected_result=prior_result,
        quality_context=context,
        candidate_reference_at=candidate_clock.reference_at if candidate_clock else None,
    )
    if (
        synthesis_protocol(actual.output_schema_ref) == "decision"
        and actual.execution_mode == "live"
    ):
        delta["candidate_clock"] = candidate_clock_state(
            candidate_clock, mapped_ref=observation_ref
        )
    if mapped.selection.kind != "evidence_only" and eligible_synthesis(mapped.result, check):
        selected_ref, selected_generation = observation_ref, mapped.mapping_input_generation
    else:
        selected_ref, selected_generation = prior_ref, prior_generation
    return (
        {**state, **delta},
        check,
        selected_ref,
        selected_generation,
        context,
        quality,
        proofs,
        runtime_json,
        unknowns,
    )


def _verify_quality_projection(
    session: Session,
    receipt: ResearchGraphRoundAdmissionV3,
    selection: object,
    calculated: Mapping[str, object],
    context: QualityProjectionContextV1,
    quality: QualityDecisionV1,
    proofs: tuple[ObservedExecutionRef, ...],
    runtime_json: str,
    unknowns: tuple[str, ...],
) -> ResearchQualitySidecarV1:
    if not isinstance(selection, FrozenGraphRecipeSelectionV3):
        raise ValueError("research_graph_recipe_binding_invalid")
    sidecar = read_quality_sidecar_in_session(
        session, receipt.quality_sidecar_id, selection=selection.receipt
    )
    if (
        sidecar.phase != "round"
        or sidecar.logical_round != receipt.logical_round
        or sidecar.recipe_selection_ref != selection.reference
        or sidecar.quality_context != context
        or sidecar.quality_decision != quality
        or sidecar.quality_context_hash != receipt.quality_context_hash
        or quality.decision_hash != receipt.quality_decision_hash
        or quality.source_result_hash != receipt.source_result_hash
        or calculated.get("quality_decision") != quality.model_dump(mode="json")
        or sidecar.proof_rows != proofs
        or sidecar.prepared_runtime_json != runtime_json
        or sidecar.unknown_receipt_codes != unknowns
    ):
        raise ValueError("research_graph_quality_projection_invalid")
    return sidecar


def _failure_quality_v3(
    session: Session,
    calculated: AgenticResearchState,
    selection: FrozenGraphRecipeSelectionV3,
    native_context: ResearchNativeObservationContext,
    assessed_at: datetime,
) -> tuple[
    AgenticResearchState,
    QualityProjectionContextV1,
    QualityDecisionV1,
    tuple[ObservedExecutionRef, ...],
    str,
    tuple[str, ...],
]:
    quality_state = cast(dict[str, object], calculated)
    source = ResearchSessionResult.model_validate(
        quality_state.get("quality_source_result") or calculated["latest_result"]
    )
    preparation = read_prepared_execution_in_session(
        session, selection.receipt.run_id, native_context.generation
    )
    context, proofs, runtime_json, unknowns = build_server_quality_context(
        session,
        selection=selection.receipt,
        source_result=source,
        native_context=native_context,
        preparation=preparation,
        assessed_at=assessed_at,
    )
    quality_state.pop("quality_decision", None)
    quality_state["quality_projection_context"] = context.model_dump(mode="json")
    quality = quality_projection.quality_for_state(calculated)
    quality_state["quality_decision"] = quality.model_dump(mode="json")
    return calculated, context, quality, proofs, runtime_json, unknowns


def _perpetual_saved_terminal(session: Session, run_id: str) -> bool:
    """Historical readers never continue a running Task or renew a writer lease."""
    row = session.connection().exec_driver_sql(
        "SELECT status,artifact_id,finished_at FROM runs WHERE run_id=?", (run_id,)
    ).one_or_none()
    return bool(row is not None and row[0] in {"completed", "degraded"}
                and isinstance(row[1], str) and row[1] and row[2] is not None)


def read_graph_round_in_session(
    session: Session,
    *,
    run_id: str,
    reference: GraphRoundAdmissionRef,
    expected_lease_owner: str | None,
    require_current_permission: bool,
) -> FrozenGraphRound:
    if reference.row.schema_version == PERPETUAL_ROUND_SCHEMA:
        if not require_current_permission and _perpetual_saved_terminal(session, run_id):
            retained = replay_retained_graph_round_in_session(
                session, run_id=run_id, reference_json=reference.model_dump_json()
            )
            if retained is not None:
                receipt = ResearchPerpetualGraphRound.model_validate_json(retained.receipt_json)
                state = cast(AgenticResearchState, json.loads(retained.projection_json))
                return FrozenGraphRound(reference, receipt, state)
        from .research_perpetual_graph import read_round
        return read_round(session, run_id=run_id, reference=reference, expected_lease_owner=expected_lease_owner, active=require_current_permission)
    if not require_current_permission:
        retained = replay_retained_graph_round_in_session(
            session, run_id=run_id, reference_json=reference.model_dump_json()
        )
        if retained is not None:
            receipt_model = (
                ResearchGraphRoundAdmissionV2
                if reference.row.schema_version == ROUND_SCHEMA_V2
                else ResearchGraphRoundAdmission
            )
            receipt = receipt_model.model_validate_json(retained.receipt_json)
            state = cast(AgenticResearchState, json.loads(retained.projection_json))
            return FrozenGraphRound(reference, receipt, state)
    return _read_graph_round_current_in_session(
        session,
        run_id=run_id,
        reference=reference,
        expected_lease_owner=expected_lease_owner,
        require_current_permission=require_current_permission,
        originals=None,
    )


def _read_graph_round_current_in_session(
    session: Session,
    *,
    run_id: str,
    reference: GraphRoundAdmissionRef,
    expected_lease_owner: str | None,
    require_current_permission: bool,
    originals: _OriginalProofs | None,
) -> FrozenGraphRound:
    with _local_original_read(session) as boundary:
        proofs = (
            originals
            if originals is not None and originals.usable(session)
            else (_OriginalProofs(session, boundary))
        )
        if require_current_permission:
            _permission(session, run_id, expected_lease_owner)
        receipt = _read_round_receipt(session, reference.row, run_id)
        saved = _context_valid(
            session, receipt.native_context, active=require_current_permission, originals=proofs
        )
        native_context_bytes = observation_json(receipt.native_context.model_dump(mode="json"))
        saved_preparation_bytes = _preparation_bytes(saved)
        _verify_material(session, receipt)
        base = _original_request(session, run_id)
        previous: FrozenGraphRound | None = None
        if receipt.previous_ref is not None:
            if receipt.previous_ref.row.role != f"research_graph_round:{receipt.logical_round - 1}":
                raise ValueError("research_graph_round_chain_invalid")
            previous = _read_graph_round_current_in_session(
                session,
                run_id=run_id,
                reference=receipt.previous_ref,
                expected_lease_owner=expected_lease_owner,
                require_current_permission=require_current_permission,
                originals=proofs,
            )
            if receipt.logical_round != previous.receipt.logical_round + 1:
                raise ValueError("research_graph_round_chain_invalid")
            if type(receipt) is not type(previous.receipt):
                raise ValueError("research_graph_recipe_chain_mixed")
            state: AgenticResearchState = {**previous.state, "current_round": receipt.logical_round}
        else:
            if receipt.logical_round != 1:
                raise ValueError("research_graph_round_chain_invalid")
            state = initial_research_state(base)
        if (
            not boundary.unchanged()
            or observation_json(receipt.native_context.model_dump(mode="json"))
            != native_context_bytes
            or _preparation_bytes(saved) != saved_preparation_bytes
        ):
            saved = read_prepared_execution_in_session(
                session, run_id, receipt.native_context.generation
            )
        actual = ResearchSessionRequest.model_validate(
            saved.execution.request.model_dump(mode="json")
        )
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
        mapped: ResearchMappedObservation | None = None
        mapped_bytes: str | None = None
        mapped_ref_bytes: str | None = None
        if receipt.observation_kind == "mapped":
            mapped = _mapped(
                session,
                receipt.observation_ref,
                active=require_current_permission,
                originals=proofs,
            )
            mapped_bytes = observation_json(mapped.model_dump(mode="json"))
            mapped_ref_bytes = observation_json(receipt.observation_ref.model_dump(mode="json"))
            if mapped.context != receipt.native_context:
                raise ValueError("research_graph_round_observation_mismatch")
            admitted = [
                e for e in receipt.evidence if e.evidence_id in receipt.admitted_evidence_ids
            ]
            if isinstance(receipt, ResearchGraphRoundAdmissionV2):
                # The new recipe preserves the actual admission order explicitly;
                # the historical v1 replay order and byte semantics stay untouched.
                evidence_by_id = {e.evidence_id: e for e in receipt.evidence}
                if len(set(receipt.admitted_evidence_ids)) != len(receipt.admitted_evidence_ids):
                    raise ValueError("research_graph_admitted_ids_invalid")
                try:
                    admitted = [
                        evidence_by_id[identifier] for identifier in receipt.admitted_evidence_ids
                    ]
                except KeyError as exc:
                    raise ValueError("research_graph_admitted_ids_invalid") from exc
                projection_args = _ProjectionArguments(
                    state=state,
                    base=base,
                    actual=actual,
                    observation_ref=receipt.observation_ref,
                    mapped=mapped,
                    evidence=receipt.evidence,
                    facts=receipt.facts,
                    admitted=admitted,
                    previous=previous,
                    active=require_current_permission,
                    originals=proofs,
                )
                if isinstance(receipt, ResearchGraphRoundAdmissionV3):
                    selected_recipe = _recipe_selection(session, run_id)
                    if not isinstance(selected_recipe, FrozenGraphRecipeSelectionV3):
                        raise ValueError("research_graph_recipe_binding_invalid")
                    projected_v3 = _project_observed_v3(
                        session,
                        **projection_args,
                        selection=selected_recipe,
                        assessed_at=receipt.writer_clock_at,
                    )
                    calculated, check, selected_ref, selected_generation = projected_v3[:4]
                    _verify_quality_projection(
                        session, receipt, selected_recipe, calculated, *projected_v3[4:]
                    )
                else:
                    calculated, check, selected_ref, selected_generation = _project_observed_v2(
                        session, **projection_args
                    )
                if (
                    receipt.citation_check != check
                    or receipt.citation_disposition != check.disposition
                    or receipt.selected_synthesis_observation_ref != selected_ref
                    or receipt.selected_mapping_generation != selected_generation
                ):
                    raise ValueError("research_graph_citation_projection_invalid")
            else:
                delta = project_round(
                    state, base, actual, mapped.result, receipt.evidence, receipt.facts, admitted
                )
                calculated = {**state, **delta}
            if (
                isinstance(receipt, ResearchGraphRoundAdmission)
                and receipt.selected_synthesis_observation_ref != receipt.observation_ref
            ):
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
                    originals=proofs,
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
            if isinstance(receipt, ResearchGraphRoundAdmissionV2) and (
                receipt.citation_check is not None
                or receipt.citation_disposition != "not_applicable"
            ):
                raise ValueError("research_graph_failure_citation_invalid")
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
            if isinstance(receipt, ResearchGraphRoundAdmissionV3):
                selection = _recipe_selection(session, run_id)
                if not isinstance(selection, FrozenGraphRecipeSelectionV3):
                    raise ValueError("research_graph_recipe_binding_invalid")
                failure_quality = _failure_quality_v3(
                    session,
                    cast(AgenticResearchState, calculated),
                    selection,
                    receipt.native_context,
                    receipt.writer_clock_at,
                )
                calculated = failure_quality[0]
                quality_parts = failure_quality[1:]
                _verify_quality_projection(session, receipt, selection, calculated, *quality_parts)
        if (
            observation_sha(calculated) != receipt.state_hash
            or calculated != receipt.projected_state
        ):
            raise ValueError("research_graph_projection_changed")

        def original_mapped(reference: ObservedExecutionRef) -> ResearchMappedObservation:
            if (
                receipt.observation_kind == "mapped"
                and mapped is not None
                and boundary.unchanged()
                and observation_json(reference.model_dump(mode="json")) == mapped_ref_bytes
                and observation_json(mapped.model_dump(mode="json")) == mapped_bytes
            ):
                if require_current_permission:
                    _permission(session, run_id, expected_lease_owner)
                return mapped
            return _mapped(session, reference, active=require_current_permission, originals=proofs)

        selected = (
            original_mapped(receipt.selected_synthesis_observation_ref)
            if receipt.selected_synthesis_observation_ref is not None
            else None
        )
        latest = read_transport_observation(
            session,
            receipt.latest_observation_ref
            if receipt.observation_kind == "failure"
            else original_mapped(receipt.observation_ref).latest_transport_ref,
        )
        if (
            (selected.mapping_input_generation if selected is not None else None)
            != receipt.selected_mapping_generation
            or latest.context.generation != receipt.latest_transport_generation
        ):
            raise ValueError("research_graph_projection_generations_invalid")
        proofs.require_source_unchanged()
        if require_current_permission:
            _permission(session, run_id, expected_lease_owner)
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
    if observed.observation_ref.schema_version == "research-mapped-observation.v2":
        from .research_perpetual_graph import admit_round
        return admit_round(session, run_id=run_id, expected_lease_owner=expected_lease_owner, observed=observed, database=database, previous=previous)
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
    selection = _recipe_selection(session, run_id)
    _check_round_request(base, actual, state)
    candidate_clock = read_candidate_clock_in_session(
        session, mapped_ref=observed.observation_ref, request=actual
    )
    cutoff = _round_cutoff(
        state, actual, mapped.result,
        candidate_reference_at=candidate_clock.reference_at if candidate_clock else None,
    )
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
    selected_ref: ObservedExecutionRef | None = observed.observation_ref
    selected_generation: int | None = mapped.mapping_input_generation
    citation_check: CitationMembershipCheckV2 | None = None
    quality_parts: _QualityParts | None = None
    v3_clock = utcnow() if isinstance(selection, FrozenGraphRecipeSelectionV3) else None
    if selection is not None:
        original = (
            read_graph_round_in_session(
                session,
                run_id=run_id,
                reference=previous,
                expected_lease_owner=expected_lease_owner,
                require_current_permission=True,
            )
            if previous is not None
            else None
        )
        projection_args = _ProjectionArguments(
            state=state,
            base=base,
            actual=actual,
            observation_ref=observed.observation_ref,
            mapped=mapped,
            evidence=evidence,
            facts=facts,
            admitted=accepted,
            previous=original,
            active=True,
            originals=None,
        )
        if isinstance(selection, FrozenGraphRecipeSelectionV3):
            assert v3_clock is not None
            from .research_review_effects import record_review_effects_in_session
            if expected_lease_owner is not None:
                record_review_effects_in_session(
                    session,
                    mapped_ref=observed.observation_ref,
                    source_result=mapped.result,
                    expected_lease_owner=expected_lease_owner,
                    known_at=v3_clock,
                )
            projected_v3 = _project_observed_v3(
                session, **projection_args, selection=selection, assessed_at=v3_clock
            )
            updated, citation_check, selected_ref, selected_generation = projected_v3[:4]
            quality_parts = projected_v3[4:]
        else:
            updated, citation_check, selected_ref, selected_generation = _project_observed_v2(
                session, **projection_args
            )
    else:
        updated = {
            **state,
            **project_round(state, base, actual, mapped.result, evidence, facts, accepted),
        }
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
            if not isinstance(original.receipt, ResearchGraphRoundAdmission):
                raise ValueError("research_graph_recipe_chain_mixed")
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
    now = v3_clock or utcnow()
    values: dict[str, Any] = dict(
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
    receipt: GraphRoundReceipt
    if isinstance(selection, FrozenGraphRecipeSelectionV3):
        assert citation_check is not None and quality_parts is not None
        context, quality, proofs, runtime_json, unknowns = quality_parts
        sidecar = persist_quality_sidecar_in_session(
            session,
            selection=selection.receipt,
            recipe_selection_ref=selection.reference,
            logical_round=actual.current_round,
            context=context,
            quality=quality,
            proof_rows=proofs,
            prepared_runtime_json=runtime_json,
            unknown_receipt_codes=unknowns,
            recorded_at=now,
        )
        receipt = ResearchGraphRoundAdmissionV3(
            **values,
            recipe_selection_ref=selection.reference,
            recipe_hash=selection.receipt.dependency_hash,
            citation_disposition=citation_check.disposition,
            citation_check=citation_check,
            quality_sidecar_id=sidecar.sidecar_id,
            quality_context_hash=sidecar.quality_context_hash,
            quality_decision_hash=sidecar.quality_decision_hash,
            source_result_hash=sidecar.source_result_hash,
        )
    elif selection is not None:
        assert citation_check is not None
        receipt = ResearchGraphRoundAdmissionV2(
            **values,
            recipe_selection_ref=selection.reference,
            recipe_hash=selection.receipt.dependency_hash,
            citation_disposition=citation_check.disposition,
            citation_check=citation_check,
        )
    else:
        receipt = ResearchGraphRoundAdmission(**values, recipe_hash=_recipe_hash())
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
    if old.receipt.selected_synthesis_observation_ref is not None:
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
    quality_parts: _QualityParts | None = None
    selection = None
    if isinstance(old.receipt, ResearchGraphRoundAdmissionV3):
        selection = _recipe_selection(session, run_id)
        if not isinstance(selection, FrozenGraphRecipeSelectionV3):
            raise ValueError("research_graph_recipe_binding_invalid")
        failure_quality = _failure_quality_v3(
            session, cast(AgenticResearchState, updated), selection, raw.context, now
        )
        updated = failure_quality[0]
        quality_parts = failure_quality[1:]
    values: dict[str, Any] = dict(
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
    receipt: GraphRoundReceipt
    if isinstance(old.receipt, ResearchGraphRoundAdmissionV3):
        assert isinstance(selection, FrozenGraphRecipeSelectionV3) and quality_parts is not None
        context, quality, proofs, runtime_json, unknowns = quality_parts
        sidecar = persist_quality_sidecar_in_session(
            session,
            selection=selection.receipt,
            recipe_selection_ref=selection.reference,
            logical_round=logical_round,
            context=context,
            quality=quality,
            proof_rows=proofs,
            prepared_runtime_json=runtime_json,
            unknown_receipt_codes=unknowns,
            recorded_at=now,
        )
        receipt = ResearchGraphRoundAdmissionV3(
            **values,
            recipe_selection_ref=selection.reference,
            recipe_hash=selection.receipt.dependency_hash,
            citation_disposition="not_applicable",
            citation_check=None,
            quality_sidecar_id=sidecar.sidecar_id,
            quality_context_hash=sidecar.quality_context_hash,
            quality_decision_hash=sidecar.quality_decision_hash,
            source_result_hash=sidecar.source_result_hash,
        )
    elif isinstance(old.receipt, ResearchGraphRoundAdmissionV2):
        selection = read_graph_recipe_selection_in_session(session, run_id)
        if selection is None:
            raise ValueError("research_graph_recipe_binding_invalid")
        receipt = ResearchGraphRoundAdmissionV2(
            **values,
            recipe_selection_ref=selection.reference,
            recipe_hash=selection.receipt.dependency_hash,
            citation_disposition="not_applicable",
            citation_check=None,
        )
    else:
        receipt = ResearchGraphRoundAdmission(**values, recipe_hash=_recipe_hash())
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
    if last_admission.row.schema_version == PERPETUAL_ROUND_SCHEMA:
        from .research_perpetual_graph import freeze_final
        return freeze_final(session, run_id=run_id, expected_lease_owner=expected_lease_owner, database=database, last_admission=last_admission)
    from .research_policy_checker import (
        execute_citation_policy_in_session,
        verify_finalization_policy_in_session,
    )

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
    base = _original_request(session, run_id)
    quality_bundle = None
    if isinstance(frozen.receipt, ResearchGraphRoundAdmissionV3):
        existing_v3 = session.scalar(
            select(RunRoleOutputRecord)
            .where(
                RunRoleOutputRecord.run_id == run_id,
                RunRoleOutputRecord.role == "research_graph_final",
            )
            .execution_options(populate_existing=True)
        )
        if existing_v3 is not None:
            prior = read_graph_final_in_session(
                session,
                run_id=run_id,
                reference=GraphFinalProjectionRef(row=_row_ref(existing_v3)),
                expected_lease_owner=expected_lease_owner,
                require_current_permission=True,
            )
            if prior.receipt.last_admission_ref != last_admission:
                raise ValueError("research_graph_final_original_conflict")
            verify_finalization_policy_in_session(
                session,
                run_id=run_id,
                graph_ref=prior.reference,
                expected_lease_owner=expected_lease_owner,
            )
            _fence(session, run_id, expected_lease_owner)
            return prior
        quality_bundle = quality_projection.finalize(frozen.state, base, frozen.receipt.evidence)
        final, citation_check = quality_bundle.result, quality_bundle.citation_check
    elif isinstance(frozen.receipt, ResearchGraphRoundAdmissionV2):
        final, citation_check = project_final_v2(frozen.state, base, frozen.receipt.evidence)
    else:
        final = project_final(frozen.state)
        citation_check = None
    snapshot = ResearchEvidenceService(database).freeze_decision_snapshot_in_session(
        session,
        run_id=run_id,
        evidence_refs=[e.evidence_id for e in frozen.receipt.evidence if e.quality == "accepted"],
        cutoff_at=_decision_cutoff(frozen.state, _original_request(session, run_id)),
    )
    row = session.get(SnapshotRecord, snapshot.snapshot_id, populate_existing=True)
    assert row is not None
    values: dict[str, Any] = dict(
        run_id=run_id,
        last_admission_ref=last_admission,
        selected_synthesis_observation_ref=frozen.receipt.selected_synthesis_observation_ref,
        latest_observation_ref=frozen.receipt.latest_observation_ref,
        native_context=frozen.receipt.native_context,
        decision_snapshot_id=row.snapshot_id,
        decision_snapshot_hash=row.snapshot_hash,
        decision_snapshot_row_hash=observation_sha(_columns(row)),
        decision_cutoff_at=as_utc(row.cutoff_at),
        projected_result=final,
        projected_result_hash=observation_sha(final.model_dump(mode="json")),
        projected_result_content_hash=research_result_content_hash(final),
        recorded_at=utcnow(),
    )
    receipt: GraphFinalReceipt
    if isinstance(frozen.receipt, ResearchGraphRoundAdmissionV3):
        assert citation_check is not None and quality_bundle is not None
        selection = _recipe_selection(session, run_id)
        if not isinstance(selection, FrozenGraphRecipeSelectionV3):
            raise ValueError("research_graph_recipe_binding_invalid")
        original_quality = read_quality_sidecar_in_session(
            session, frozen.receipt.quality_sidecar_id, selection=selection.receipt
        )
        sidecar = persist_quality_sidecar_in_session(
            session,
            selection=selection.receipt,
            recipe_selection_ref=selection.reference,
            logical_round=frozen.receipt.logical_round,
            context=original_quality.quality_context,
            quality=quality_bundle.quality,
            proof_rows=original_quality.proof_rows,
            prepared_runtime_json=original_quality.prepared_runtime_json,
            unknown_receipt_codes=original_quality.unknown_receipt_codes,
            recorded_at=values["recorded_at"],
            projected_result_hash=quality_bundle.projected_result_hash,
        )
        receipt = ResearchGraphFinalProjectionV3(
            **values,
            recipe_selection_ref=frozen.receipt.recipe_selection_ref,
            recipe_hash=frozen.receipt.recipe_hash,
            citation_disposition=citation_check.disposition,
            latest_citation_disposition=frozen.receipt.citation_disposition,
            citation_check=citation_check,
            quality_sidecar_id=sidecar.sidecar_id,
            round_quality_sidecar_id=frozen.receipt.quality_sidecar_id,
            quality_context_hash=sidecar.quality_context_hash,
            quality_decision_hash=sidecar.quality_decision_hash,
            source_result_hash=sidecar.source_result_hash,
        )
    elif isinstance(frozen.receipt, ResearchGraphRoundAdmissionV2):
        assert citation_check is not None
        receipt = ResearchGraphFinalProjectionV2(
            **values,
            recipe_selection_ref=frozen.receipt.recipe_selection_ref,
            recipe_hash=frozen.receipt.recipe_hash,
            citation_disposition=citation_check.disposition,
            latest_citation_disposition=frozen.receipt.citation_disposition,
            citation_check=citation_check,
        )
    else:
        receipt = ResearchGraphFinalProjection(**values, recipe_hash=_recipe_hash())
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
        verify_finalization_policy_in_session(
            session,
            run_id=run_id,
            graph_ref=prior.reference,
            expected_lease_owner=expected_lease_owner,
        )
        _fence(session, run_id, expected_lease_owner)
        return prior
    reference = GraphFinalProjectionRef(row=_store(session, receipt))
    _context_valid(session, frozen.receipt.native_context, active=True)
    _verify_material(session, frozen.receipt)
    _snapshot_valid(session, frozen, row)
    execute_citation_policy_in_session(
        session,
        run_id=run_id,
        graph_ref=reference,
        result=final,
        phase="finalization",
        assessed_at=receipt.recorded_at,
        expected_lease_owner=expected_lease_owner,
    )
    _fence(session, run_id, expected_lease_owner)
    return FrozenGraphFinal(reference, receipt, final)


def _snapshot_valid(session: Session, frozen: FrozenGraphRound, row: SnapshotRecord) -> None:
    base = _original_request(session, frozen.receipt.run_id)
    run = session.get(RunRecord, frozen.receipt.run_id, populate_existing=True)
    trigger = session.get(SnapshotRecord, base.trigger_snapshot_id, populate_existing=True)
    cutoff = _decision_cutoff(frozen.state, base)
    trigger_cutoff = (
        as_utc(cast(datetime | None, trigger.cutoff_at)) if trigger is not None else None
    )
    if (
        run is None
        or trigger is None
        or row.event_id != run.event_id
        or row.run_id != run.run_id
        or row.parent_snapshot_id != trigger.snapshot_id
        or row.snapshot_type != "decision"
        or row.generation != 2
        or as_utc(row.cutoff_at) != cutoff
        or trigger_cutoff is None
        or cutoff < trigger_cutoff
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
    if reference.row.schema_version == PERPETUAL_FINAL_SCHEMA:
        if not require_current_permission and _perpetual_saved_terminal(session, run_id):
            retained = replay_retained_graph_final_in_session(
                session, run_id=run_id, reference_json=reference.model_dump_json()
            )
            if retained is not None:
                receipt = parse_graph_final_projection_declaration(
                    retained.receipt_json, schema_version=PERPETUAL_FINAL_SCHEMA
                )
                result = ResearchSessionResult.model_validate_json(retained.projection_json)
                return FrozenGraphFinal(reference, receipt, result)
        from .research_perpetual_graph import read_final
        return read_final(session, run_id=run_id, reference=reference, expected_lease_owner=expected_lease_owner, active=require_current_permission)
    scope = _PUBLICATION_FINAL_READ.get()
    cacheable = (
        scope is not None and scope.session is session and scope.boundary.unchanged()
        and not require_current_permission and expected_lease_owner is None
    )
    key = observation_json({"run_id": run_id, "reference": reference.model_dump(mode="json")})
    if cacheable and scope is not None and key in scope.finals:
        frozen, digest = scope.finals[key]
        if observation_sha({"receipt": frozen.receipt.model_dump(mode="json"),
                            "result": frozen.result.model_dump(mode="json")}) != digest:
            raise ValueError("research_graph_cached_final_changed")
        _read_final_receipt(session, reference.row, run_id)
        scope.used = True
        return frozen
    if not require_current_permission:
        retained = replay_retained_graph_final_in_session(
            session, run_id=run_id, reference_json=reference.model_dump_json()
        )
        if retained is not None:
            receipt = parse_graph_final_projection_declaration(
                retained.receipt_json, schema_version=reference.row.schema_version
            )
            result = ResearchSessionResult.model_validate_json(retained.projection_json)
            final = FrozenGraphFinal(reference, receipt, result)
            if cacheable and scope is not None:
                scope.source = scope.source or _ReadSourceGuard.capture()
                if scope.source is not None:
                    scope.finals[key] = (final, observation_sha({
                        "receipt": final.receipt.model_dump(mode="json"),
                        "result": final.result.model_dump(mode="json")}))
            return final
    final = _read_graph_final_current_in_session(
        session,
        run_id=run_id,
        reference=reference,
        expected_lease_owner=expected_lease_owner,
        require_current_permission=require_current_permission,
    )
    if cacheable and scope is not None and scope.boundary.unchanged():
        scope.source = scope.source or _ReadSourceGuard.capture()
        if scope.source is not None:
            scope.finals[key] = (final, observation_sha({
                "receipt": final.receipt.model_dump(mode="json"),
                "result": final.result.model_dump(mode="json")}))
    return final


def _read_graph_final_current_in_session(
    session: Session,
    *,
    run_id: str,
    reference: GraphFinalProjectionRef,
    expected_lease_owner: str | None,
    require_current_permission: bool,
) -> FrozenGraphFinal:
    with _local_original_read(session) as boundary:
        proofs = _OriginalProofs(session, boundary)
        receipt = _read_final_receipt(session, reference.row, run_id)
        frozen = _read_graph_round_current_in_session(
            session,
            run_id=run_id,
            reference=receipt.last_admission_ref,
            expected_lease_owner=expected_lease_owner,
            require_current_permission=require_current_permission,
            originals=proofs,
        )
        link = session.get(DshSessionLinkRecord, run_id, populate_existing=True)
        current = (
            _context_valid(
                session, receipt.native_context, active=require_current_permission, originals=proofs
            )
            if link is not None and link.generation == receipt.native_context.generation
            else read_prepared_execution_in_session(session, run_id)
        )
        if isinstance(receipt, ResearchGraphFinalProjectionV3):
            if not isinstance(frozen.receipt, ResearchGraphRoundAdmissionV3):
                raise ValueError("research_graph_recipe_chain_mixed")
            bundle = quality_projection.finalize(
                frozen.state, _original_request(session, run_id), frozen.receipt.evidence
            )
            final, check = bundle.result, bundle.citation_check
            selection = _recipe_selection(session, run_id)
            if not isinstance(selection, FrozenGraphRecipeSelectionV3):
                raise ValueError("research_graph_recipe_binding_invalid")
            final_quality = read_quality_sidecar_in_session(
                session, receipt.quality_sidecar_id, selection=selection.receipt
            )
            original_quality = read_quality_sidecar_in_session(
                session, frozen.receipt.quality_sidecar_id, selection=selection.receipt
            )
            if (
                final_quality.phase != "final"
                or final_quality.logical_round != frozen.receipt.logical_round
                or receipt.round_quality_sidecar_id != frozen.receipt.quality_sidecar_id
                or final_quality.recipe_selection_ref != selection.reference
                or final_quality.quality_context != original_quality.quality_context
                or final_quality.quality_decision != bundle.quality
                or final_quality.source_result_hash != bundle.source_result_hash
                or final_quality.projected_result_hash != bundle.projected_result_hash
                or final_quality.proof_rows != original_quality.proof_rows
                or final_quality.prepared_runtime_json != original_quality.prepared_runtime_json
                or final_quality.unknown_receipt_codes != original_quality.unknown_receipt_codes
                or receipt.quality_context_hash != final_quality.quality_context_hash
                or receipt.quality_decision_hash != bundle.quality.decision_hash
                or receipt.source_result_hash != bundle.source_result_hash
                or receipt.recipe_selection_ref != frozen.receipt.recipe_selection_ref
                or receipt.recipe_hash != frozen.receipt.recipe_hash
                or receipt.citation_check != check
                or receipt.citation_disposition != check.disposition
                or receipt.latest_citation_disposition != frozen.receipt.citation_disposition
            ):
                raise ValueError("research_graph_final_quality_invalid")
        elif isinstance(receipt, ResearchGraphFinalProjectionV2):
            if not isinstance(frozen.receipt, ResearchGraphRoundAdmissionV2):
                raise ValueError("research_graph_recipe_chain_mixed")
            final, check = project_final_v2(
                frozen.state, _original_request(session, run_id), frozen.receipt.evidence
            )
            if (
                receipt.recipe_selection_ref != frozen.receipt.recipe_selection_ref
                or receipt.recipe_hash != frozen.receipt.recipe_hash
                or receipt.citation_check != check
                or receipt.citation_disposition != check.disposition
                or receipt.latest_citation_disposition != frozen.receipt.citation_disposition
            ):
                raise ValueError("research_graph_final_citation_invalid")
        else:
            if not isinstance(frozen.receipt, ResearchGraphRoundAdmission):
                raise ValueError("research_graph_recipe_chain_mixed")
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
        proofs.require_source_unchanged()
        if require_current_permission:
            _permission(session, run_id, expected_lease_owner)
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


def _publication_permission(
    session: Session, run_id: str, owner: str | None, artifact_id: str
) -> None:
    run = session.get(RunRecord, run_id, populate_existing=True)
    expires_at = as_utc(run.lease_expires_at) if run is not None else None
    if (
        run is None
        or not owner
        or not artifact_id
        or run.status != "running"
        or run.artifact_id != artifact_id
        or run.lease_owner != owner
        or expires_at is None
        or expires_at <= utcnow()
    ):
        raise PermissionError("research_graph_publication_writer_permission_lost")


def verify_graph_citation_membership_in_session(
    session: Session,
    *,
    run_id: str,
    reference: GraphFinalProjectionRef,
    expected_lease_owner: str | None,
    expected_artifact_id: str | None = None,
) -> FrozenGraphFinal:
    """Re-read the authenticated final and material in the existing writer.

    Before publication this retains the ordinary artifact-null Graph gate.
    After Commit's artifact CAS, only its exact Artifact/owner/live-lease
    predicate applies. This separate predicate never broadens Graph admission.
    Neither a saved pass nor a caller's Result is publication permission.
    """
    _require_writer(session)
    if expected_artifact_id is None:
        _permission(session, run_id, expected_lease_owner)
    else:
        _publication_permission(session, run_id, expected_lease_owner, expected_artifact_id)
    frozen = read_graph_final_in_session(
        session,
        run_id=run_id,
        reference=reference,
        expected_lease_owner=expected_lease_owner,
        require_current_permission=expected_artifact_id is None,
    )
    # The reader replays v2 membership against the original Task and current
    # exact material/snapshot. Its strict dispatch also preserves v1 semantics.
    if expected_artifact_id is None:
        _fence(session, run_id, expected_lease_owner)
    else:
        _publication_permission(session, run_id, expected_lease_owner, expected_artifact_id)
        updated = session.execute(
            update(RunRecord)
            .where(
                RunRecord.run_id == run_id,
                RunRecord.status == "running",
                RunRecord.artifact_id == expected_artifact_id,
                RunRecord.lease_owner == expected_lease_owner,
                RunRecord.lease_expires_at > utcnow(),
            )
            .values(lease_owner=expected_lease_owner)
            .execution_options(synchronize_session=False)
        )
        if getattr(updated, "rowcount", None) != 1:
            raise PermissionError("research_graph_publication_writer_fence_lost")
    return frozen

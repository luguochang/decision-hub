"""Immutable, request-correlated Runtime observations in the caller's writer transaction.

Raw observations survive native drift; mapped observations must revalidate the
original native context. Storage does not attest Provider/checker execution or
grant publication. There is no Result-only registration or read-latest mint API.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts import (
    DshSessionResult,
    ResearchSynthesisCandidate,
)
from packages.kernel.decision_hub_kernel.application.dsh_sessions import (
    execution_request_role,
    prompt_integrity_role,
)
from packages.kernel.decision_hub_kernel.application.research_binding import BUNDLE_ROLES
from packages.kernel.decision_hub_kernel.application.research_execution_binding import (
    FrozenExecutionPreparation,
    read_prepared_execution_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_method import ROLE as METHOD_ROLE
from packages.kernel.decision_hub_kernel.application.research_review import (
    research_result_content_hash,
)
from packages.kernel.decision_hub_kernel.application.research_tool_accounting import (
    normalize_generation_tool_accounting,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    RunRecord,
    RunRoleOutputRecord,
    as_utc,
)
from packages.kernel.decision_hub_kernel.ports.research_observation import (
    ObservedExecutionRef,
    ResearchMappedObservation,
    ResearchMappedObservationV2,
    ResearchNativeObservationContext,
    ResearchSynthesisSelectionWitness,
    ResearchTransportObservation,
)

MAX_OBSERVATION_BYTES = 64 * 1024 * 1024
type StoredObservation = (
    ResearchTransportObservation | ResearchMappedObservation | ResearchMappedObservationV2
)


def observation_json(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def observation_sha(value: object) -> str:
    return hashlib.sha256(observation_json(value).encode("utf-8")).hexdigest()


def _ref(row: RunRoleOutputRecord) -> ObservedExecutionRef:
    recorded = as_utc(row.created_at)
    if (
        recorded is None
        or row.payload_hash != hashlib.sha256(row.payload_json.encode()).hexdigest()
    ):
        raise ValueError("research_observation_original_row_invalid")
    return ObservedExecutionRef(
        output_id=row.output_id,
        role=row.role,
        schema_version=row.schema_version,
        payload_hash=row.payload_hash,
        recorded_at=recorded,
    )


def native_observation_context_in_session(
    session: Session,
    run_id: str,
    *,
    prepared: FrozenExecutionPreparation | None = None,
) -> ResearchNativeObservationContext:
    """Original authenticated SQL rows, not caller Task/Result declarations."""
    saved = prepared or read_prepared_execution_in_session(session, run_id)
    run = session.get(RunRecord, run_id, populate_existing=True)
    if run is None or run.status not in {"admitted", "running"}:
        raise ValueError("research_observation_run_not_active")
    generation = saved.execution.generation
    roles = (
        *BUNDLE_ROLES,
        METHOD_ROLE,
        execution_request_role(generation),
        prompt_integrity_role(generation),
    )
    rows = session.scalars(
        select(RunRoleOutputRecord)
        .where(
            RunRoleOutputRecord.run_id == run_id,
            RunRoleOutputRecord.role.in_(roles),
        )
        .execution_options(populate_existing=True)
    ).all()
    by_role = {row.role: row for row in rows}
    if set(by_role) != set(roles):
        raise ValueError("research_observation_native_partial")
    return ResearchNativeObservationContext(
        run_id=run_id,
        event_id=run.event_id,
        snapshot_id=run.snapshot_id,
        request_id=saved.execution.request.request_id,
        dsh_session_id=saved.execution.dsh_session_id,
        generation=generation,
        original_task_request_hash=saved.execution.task_request_hash,
        bundle_hash=saved.method.definition.bundle.bundle_hash,
        method_hash=saved.method.method_hash,
        execution_request_hash=saved.execution.request_hash,
        first_linked_request_hash=saved.method.request_hash,
        prompt_hash=hashlib.sha256(saved.prompt.prompt.encode()).hexdigest(),
        rows=tuple(_ref(by_role[role]) for role in roles),
    )


def _require_writer(session: Session) -> None:
    conn = session.connection()
    driver = conn.connection.driver_connection
    if (
        conn.dialect.name != "sqlite"
        or not isinstance(driver, sqlite3.Connection)
        or not driver.in_transaction
    ):
        raise ValueError("research_observation_writer_transaction_required")


def _encoded(value: BaseModel) -> str:
    # Exact historical receipts are not backfilled when a later optional
    # observation field is introduced. New writes explicitly freeze all fields.
    encoded = observation_json(value.model_dump(mode="json", exclude_unset=True))
    if len(encoded.encode("utf-8")) > MAX_OBSERVATION_BYTES:
        raise ValueError("research_observation_bytes_exceeded")
    return encoded


def _identity(value: BaseModel) -> str:
    # Receipt retry does not invent a later observation time for the same fact.
    return observation_sha(
        value.model_dump(mode="json", exclude={"recorded_at"}, exclude_unset=True)
    )


def _read[T: StoredObservation](
    session: Session,
    reference: ObservedExecutionRef,
    model: type[T],
) -> T:
    row = session.get(RunRoleOutputRecord, reference.output_id, populate_existing=True)
    if row is None or _ref(row) != reference:
        raise ValueError("research_observation_locator_invalid")
    try:
        value = model.model_validate_json(row.payload_json)
        prefix = (
            "research_transport" if model is ResearchTransportObservation else "research_observed"
        )
        digest = _identity(value)
        if (
            row.output_id != prefix + "_" + digest
            or row.role != prefix + ":" + digest
            or row.schema_version != value.schema_version
            or row.run_id != value.context.run_id
            or row.payload_json != _encoded(value)
            or as_utc(row.created_at) != as_utc(value.recorded_at)
        ):
            raise ValueError("research_observation_row_invalid")
        return value
    except (ValueError, TypeError) as exc:
        raise ValueError("research_observation_saved_invalid") from exc


def read_transport_observation(
    session: Session,
    reference: ObservedExecutionRef,
) -> ResearchTransportObservation:
    return _read(session, reference, ResearchTransportObservation)


def read_mapped_observation(
    session: Session,
    reference: ObservedExecutionRef,
) -> ResearchMappedObservation:
    return _read(session, reference, ResearchMappedObservation)


def _store(session: Session, value: StoredObservation, prefix: str) -> ObservedExecutionRef:
    _require_writer(session)
    value = type(value).model_validate_json(value.model_dump_json())
    digest = _identity(value)
    output_id, role = prefix + "_" + digest, prefix + ":" + digest
    existing = session.scalar(
        select(RunRoleOutputRecord)
        .where(
            (RunRoleOutputRecord.output_id == output_id)
            | (
                (RunRoleOutputRecord.run_id == value.context.run_id)
                & (RunRoleOutputRecord.role == role)
            ),
        )
        .execution_options(populate_existing=True)
    )
    if existing is not None:
        ref = _ref(existing)
        previous = _read(session, ref, type(value))
        if _identity(previous) != digest or previous.model_dump(
            mode="json", exclude={"recorded_at"}
        ) != value.model_dump(mode="json", exclude={"recorded_at"}):
            raise ValueError("research_observation_content_conflict")
        return ref
    encoded = _encoded(value)
    row = RunRoleOutputRecord(
        output_id=output_id,
        run_id=value.context.run_id,
        role=role,
        schema_version=value.schema_version,
        payload_json=encoded,
        payload_hash=hashlib.sha256(encoded.encode()).hexdigest(),
        created_at=value.recorded_at,
    )
    session.add(row)
    session.flush()
    return _ref(row)


def store_transport_observation(
    session: Session,
    value: ResearchTransportObservation,
) -> ObservedExecutionRef:
    """Retain original typed reply even when its fields or current native rows are invalid.

    The context is the original locally captured request correlation. Actual
    reply identity is never rewritten to match it. No Link, costs or permissions
    are changed. Decode-before-typed HTTP bodies are not available at this seam.
    """
    if session.get(RunRecord, value.context.run_id, populate_existing=True) is None:
        raise ValueError("research_observation_run_missing")
    if (value.phase == "error") != (value.reply is None):
        raise ValueError("research_observation_phase_invalid")
    if value.phase == "error" and value.error is None:
        raise ValueError("research_observation_error_missing")
    if isinstance(value.reply, DshSessionResult) and (
        value.events_json_hash != hashlib.sha256(value.reply.events_json.encode()).hexdigest()
        or value.final_response_hash
        != hashlib.sha256(value.reply.final_response.encode()).hexdigest()
    ):
        raise ValueError("research_observation_raw_hash_invalid")
    return _store(session, value, "research_transport")


def verify_selection_witness(
    payload: DshSessionResult,
    selection: ResearchSynthesisSelectionWitness,
) -> None:
    if selection.kind == "evidence_only":
        if any(
            (
                selection.candidate,
                selection.candidate_payload_hash,
                selection.call_id,
                selection.call_event_index,
                selection.result_event_index,
                selection.call_event_hash,
                selection.result_event_hash,
            )
        ):
            raise ValueError("research_observation_evidence_only_witness_invalid")
        return
    candidate = selection.candidate
    if candidate is None or selection.candidate_payload_hash != observation_sha(
        candidate.model_dump(mode="json")
    ):
        raise ValueError("research_observation_candidate_hash_invalid")
    if selection.kind == "structured_response":
        if (
            selection.final_response_hash
            != hashlib.sha256(payload.final_response.encode()).hexdigest()
            or ResearchSynthesisCandidate.model_validate_json(payload.final_response) != candidate
        ):
            raise ValueError("research_observation_response_hash_invalid")
        return
    try:
        events = json.loads(payload.events_json)
        call_index, result_index = selection.call_event_index, selection.result_event_index
        if (
            not isinstance(events, list)
            or call_index is None
            or result_index is None
            or not 0 <= call_index < result_index < len(events)
            or not selection.call_id
            or selection.payload_occurrence is None
            or selection.payload_path is None
            or selection.call_event_hash != observation_sha(events[call_index])
            or selection.result_event_hash != observation_sha(events[result_index])
        ):
            raise ValueError("witness")
        selected = events[result_index]
        for step in selection.payload_path:
            if step is None:
                if not isinstance(selected, str) or len(selected.encode()) > 1_000_000:
                    raise ValueError("payload_decode")
                selected = json.loads(selected)
            elif type(step) is int:
                if not isinstance(selected, list) or not 0 <= step < len(selected):
                    raise ValueError("payload_index")
                selected = selected[step]
            else:
                if not isinstance(selected, dict) or step not in selected:
                    raise ValueError("payload_key")
                selected = selected[step]
        if ResearchSynthesisCandidate.model_validate(selected) != candidate:
            raise ValueError("payload_identity")
    except (ValueError, TypeError, IndexError) as exc:
        raise ValueError("research_observation_event_witness_invalid") from exc


def store_mapped_observation(
    session: Session,
    value: ResearchMappedObservation,
    *,
    binding_validator: Callable[[Session], None],
) -> ObservedExecutionRef:
    """Runtime producer only: exact returned raw chain + original same-writer proof.

    The validator belongs to the Runtime checkpoint that actually made the RPC;
    neither a caller Result hash nor read-latest can construct that checkpoint.
    Future semantic writers must independently revalidate permission and lease.
    """
    _require_writer(session)
    binding_validator(session)
    if native_observation_context_in_session(session, value.context.run_id) != value.context:
        raise ValueError("research_observation_native_changed")
    raw = read_transport_observation(session, value.mapping_input_ref)
    latest = read_transport_observation(session, value.latest_transport_ref)
    payload = raw.reply
    if (
        raw.phase != "result"
        or not isinstance(payload, DshSessionResult)
        or raw.context.run_id != value.context.run_id
        or latest.context.run_id != value.context.run_id
        or payload.run_id != raw.context.run_id
        or payload.dsh_session_id != raw.context.dsh_session_id
        or payload.generation != raw.context.generation
        or value.mapping_input_generation != raw.context.generation
        or value.latest_transport_generation != latest.context.generation
        or value.context.generation != latest.context.generation
        or payload.result_hash
        != hashlib.sha256(
            (payload.final_response + "\0" + payload.events_json).encode()
        ).hexdigest()
        or value.result.request_id != raw.context.request_id
        or value.result.research_session_id != payload.dsh_session_id
        or value.result_content_hash != research_result_content_hash(value.result)
    ):
        raise ValueError("research_observation_mapping_identity_invalid")
    verify_selection_witness(payload, value.selection)
    candidate = value.selection.candidate
    if candidate is not None and (
        candidate.request_id != raw.context.request_id
        or value.result.causal_case != candidate.causal_case
        or value.result.horizons != candidate.horizons
    ):
        raise ValueError("research_observation_selected_projection_changed")
    if value.selection.kind == "evidence_only" and (
        value.result.causal_case or value.result.horizons
    ):
        raise ValueError("research_observation_evidence_only_projection_invalid")
    return _store(session, value, "research_observed")


def _perpetual_original_context(
    session: Session,
    declared: ResearchNativeObservationContext,
) -> FrozenExecutionPreparation:
    """Authenticate an original generation, including after its Run is terminal.

    This is archive/source proof only. The V2 writer separately checks the
    actual current Runtime checkpoint and active native context before storage.
    """
    saved = read_prepared_execution_in_session(session, declared.run_id, declared.generation)
    run = session.get(RunRecord, declared.run_id, populate_existing=True)
    if run is None:
        raise ValueError("research_perpetual_native_run_missing")
    generation = saved.execution.generation
    roles = (
        *BUNDLE_ROLES,
        METHOD_ROLE,
        execution_request_role(generation),
        prompt_integrity_role(generation),
    )
    rows = session.scalars(
        select(RunRoleOutputRecord)
        .where(
            RunRoleOutputRecord.run_id == declared.run_id,
            RunRoleOutputRecord.role.in_(roles),
        )
        .execution_options(populate_existing=True)
    ).all()
    by_role = {row.role: row for row in rows}
    if set(by_role) != set(roles):
        raise ValueError("research_perpetual_native_partial")
    actual = ResearchNativeObservationContext(
        run_id=declared.run_id,
        event_id=run.event_id,
        snapshot_id=run.snapshot_id,
        request_id=saved.execution.request.request_id,
        dsh_session_id=saved.execution.dsh_session_id,
        generation=generation,
        original_task_request_hash=saved.execution.task_request_hash,
        bundle_hash=saved.method.definition.bundle.bundle_hash,
        method_hash=saved.method.method_hash,
        execution_request_hash=saved.execution.request_hash,
        first_linked_request_hash=saved.method.request_hash,
        prompt_hash=hashlib.sha256(saved.prompt.prompt.encode()).hexdigest(),
        rows=tuple(_ref(by_role[role]) for role in roles),
    )
    if declared != actual:
        raise ValueError("research_perpetual_native_original_changed")
    return saved


def _verify_perpetual_mapping(session: Session, value: ResearchMappedObservationV2) -> None:
    """Replay actual native originals; never grant financial/action authorization."""
    from packages.runtime_adapters.dsh_runtime.event_mapper import map_notifications
    from packages.runtime_adapters.dsh_runtime.result_mapper import map_session_result_with_witness
    from packages.runtime_adapters.dsh_runtime.web_runtime import _sdk_run

    from ..ports.research_synthesis_protocol import synthesis_protocol
    from ..ports.runtime import AgentExecutionError
    from .research_transport_json import read_transport_events_json

    raw = read_transport_observation(session, value.mapping_input_ref)
    latest = read_transport_observation(session, value.latest_transport_ref)
    producer = _perpetual_original_context(session, raw.context)
    _perpetual_original_context(session, latest.context)
    request = producer.execution.request
    if synthesis_protocol(request.output_schema_ref) != "decision_v2":
        raise ValueError("research_perpetual_protocol_mismatch")
    payload = raw.reply
    if (
        raw.phase != "result"
        or not isinstance(payload, DshSessionResult)
        or raw.error is not None
        or raw.error_code is not None
        or raw.context.run_id != value.context.run_id
        or latest.context != value.context
        or payload.run_id != raw.context.run_id
        or payload.dsh_session_id != raw.context.dsh_session_id
        or payload.generation != raw.context.generation
        or value.mapping_input_generation != raw.context.generation
        or value.latest_transport_generation != latest.context.generation
        or value.result.request_id != request.request_id
        or value.result.research_session_id != payload.dsh_session_id
        or value.result.causal_case is not None
        or value.result.horizons
        or value.result_content_hash != research_result_content_hash(value.result)
    ):
        raise ValueError("research_perpetual_mapping_identity_invalid")
    if (
        raw.events_json_hash != hashlib.sha256(payload.events_json.encode()).hexdigest()
        or raw.final_response_hash != hashlib.sha256(payload.final_response.encode()).hexdigest()
        or payload.result_hash
        != hashlib.sha256(
            (payload.final_response + "\0" + payload.events_json).encode()
        ).hexdigest()
    ):
        raise ValueError("research_perpetual_transport_hash_invalid")
    if (
        not payload.started_at <= payload.finished_at <= raw.recorded_at <= value.recorded_at
        or latest.recorded_at > value.recorded_at
        or value.result.started_at != payload.started_at
        or value.result.finished_at != payload.finished_at
    ):
        raise ValueError("research_perpetual_mapping_time_invalid")
    events = read_transport_events_json(payload.events_json)
    try:
        sdk = _sdk_run(payload)
        traces = map_notifications(
            sdk.notifications,
            run_id=raw.context.run_id,
            research_session_id=raw.context.dsh_session_id,
            received_at=payload.finished_at,
            request=request,
        )
        remapped = map_session_result_with_witness(
            sdk,
            request,
            traces,
            runtime_version=producer.method.definition.runtime_version,
            profile_ref=producer.method.definition.profile_ref,
            started_at=payload.started_at,
            finished_at=payload.finished_at,
            accepted_synthesis_only=True,
        )
        native = remapped.perpetual_synthesis
    except (AgentExecutionError, ValueError, TypeError, KeyError, IndexError) as exc:
        raise ValueError("research_perpetual_native_mapping_invalid") from exc
    if native is None:
        raise ValueError("research_perpetual_native_candidate_absent")
    actual, declared = native.selection, value.perpetual_synthesis.selection
    call_index, result_index = actual.call_event_index, actual.result_event_index
    if (
        actual.kind != "tool_capture"
        or call_index is None
        or result_index is None
        or not 0 <= call_index < result_index < len(events)
        or declared.presence_generation not in (None, raw.context.generation)
        or (
            declared.presence_ref is not None
            and declared.presence_ref.model_dump(mode="json")
            != value.mapping_input_ref.model_dump(mode="json")
        )
    ):
        raise ValueError("research_perpetual_selection_binding_invalid")
    expected_selection = actual.model_dump(mode="json")
    expected_selection.update(
        call_event_hash=observation_sha(events[call_index]),
        result_event_hash=observation_sha(events[result_index]),
    )
    for name in ("presence_generation", "presence_ref"):
        expected_selection.pop(name, None)
    selected = declared.model_dump(mode="json", exclude={"presence_generation", "presence_ref"})
    if selected != expected_selection:
        raise ValueError("research_perpetual_selected_original_changed")
    nested = value.perpetual_synthesis
    for name in ("synthesis", "review_request", "authorization"):
        actual_dto, declared_dto = getattr(native, name), getattr(nested, name)
        actual_body = actual_dto.model_dump(mode="json") if actual_dto is not None else None
        declared_body = declared_dto.model_dump(mode="json") if declared_dto is not None else None
        if actual_body != declared_body:
            raise ValueError("research_perpetual_selected_packet_changed")
    for name in (
        "raw_candidate_hash",
        "review_wrapper_json",
        "review_call_id",
        "review_call_event_index",
        "review_result_event_index",
    ):
        if getattr(native, name) != getattr(nested, name):
            raise ValueError("research_perpetual_selected_review_original_changed")
    # Match the producer's canonical Gateway accounting using original rows,
    # never the count declared by the observation being verified. Native
    # notifications also contain denied/non-Gateway Tool attempts.
    normalized = normalize_generation_tool_accounting(
        session,
        remapped.result,
        run_id=raw.context.run_id,
        generation=raw.context.generation,
        research_session_id=raw.context.dsh_session_id,
        max_tool_calls=request.execution_budget.max_tool_calls,
    )
    if research_result_content_hash(normalized) != value.result_content_hash:
        raise ValueError("research_perpetual_evidence_projection_changed")


def store_mapped_perpetual_observation(
    session: Session,
    value: ResearchMappedObservationV2,
    *,
    binding_validator: Callable[[Session], None],
) -> ObservedExecutionRef:
    """Runtime producer's V2 branch; no permissive checkpoint or review default."""
    _require_writer(session)
    value = ResearchMappedObservationV2.model_validate_json(value.model_dump_json())
    if not callable(binding_validator):
        raise ValueError("research_perpetual_runtime_binding_required")
    binding_validator(session)
    if native_observation_context_in_session(session, value.context.run_id) != value.context:
        raise ValueError("research_observation_native_changed")
    _verify_perpetual_mapping(session, value)
    return _store(session, value, "research_observed")


def read_mapped_perpetual_observation(
    session: Session,
    reference: ObservedExecutionRef,
) -> ResearchMappedObservationV2:
    """Read/replay the exact archived V2 carrier and original transport proof."""
    value = _read(session, reference, ResearchMappedObservationV2)
    _verify_perpetual_mapping(session, value)
    return value

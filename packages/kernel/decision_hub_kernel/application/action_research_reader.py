"""Read committed native research originals for Action derivation.

This is a read boundary, not permission to publish an Action. Call it in a clean
physical snapshot (or via prepare_originals), then recheck that readset in the
Action writer. Legacy direction hypotheses remain inspectable; missing typed
native Action synthesis is explicitly unsupported and is never reconstructed
from a normalized Forecast or from free text.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts import DshSessionResult, ResearchSessionRequest

from ..persistence.db import EventRecord, RunRecord, RunRoleOutputRecord, as_utc
from ..ports.research_observation import (
    ObservedExecutionRef,
    ResearchMappedObservation,
    ResearchTransportObservation,
)
from ..ports.research_review_effects import ResearchReviewEffects
from ..ports.research_synthesis_protocol import synthesis_protocol
from .commit import (
    PUBLICATION_SOURCE_ROLE,
    _read_publication_source_in_session,  # pyright: ignore[reportPrivateUsage]
)
from .provider_input_receipts import row_reference, sha, strict_json
from .research_execution_binding import read_prepared_execution_in_session
from .research_graph_binding import (
    GraphFinalProjectionRef,
    parse_graph_final_projection_declaration,
    publication_final_read_scope,
    read_graph_final_in_session,
)
from .research_publication_binding import (
    observation_json,
    observation_sha,
    read_mapped_observation,
    read_transport_observation,
    verify_selection_witness,
)
from .research_quality_view import read_published_research_quality_in_session
from .research_review import _decode_review_row  # pyright: ignore[reportPrivateUsage]
from .research_review_effects import (
    _selected_gate,  # pyright: ignore[reportPrivateUsage]
    read_review_effects_in_session,
)
from .research_transport_json import read_transport_events_json


class ActionFinalSynthesisAbsent(ValueError):
    """Only the negative precheck raises this for an exact immutable null selection."""

    error_code = "action_final_selected_synthesis_absent"

    def __init__(self) -> None:
        super().__init__(self.error_code)


def reject_final_without_action_synthesis_in_session(
    session: Session,
    *,
    run_id: str,
    event_id: str,
    artifact_id: str,
    reference: GraphFinalProjectionRef,
) -> None:
    """Reject a proven missing producer cheaply; returning grants no permission.

    Authenticate only the immutable committed locator and exact final bytes here.
    Selected-present paths still require the complete original reader below.
    Missing, corrupt or mixed rows are not classified as terminal absence.
    """
    connection = session.connection()
    native = connection.connection.driver_connection
    if (
        connection.dialect.name != "sqlite"
        or not isinstance(native, sqlite3.Connection)
        or not native.in_transaction
        or session.new or session.dirty or session.deleted
    ):
        raise ValueError("action_research_clean_read_snapshot_required")
    run = session.get(RunRecord, run_id, populate_existing=True)
    if (
        run is None or run.event_id != event_id or run.artifact_id != artifact_id
        or run.status not in {"completed", "degraded"}
    ):
        raise ValueError("action_final_precheck_run_mismatch")
    publication = session.scalar(
        select(RunRoleOutputRecord).where(
            RunRoleOutputRecord.run_id == run_id,
            RunRoleOutputRecord.role == PUBLICATION_SOURCE_ROLE,
        ).execution_options(populate_existing=True)
    )
    if publication is None:
        raise ValueError("action_final_precheck_publication_missing")
    pub_ref = row_reference(publication)
    body = strict_json(publication.payload_json)
    finished_at = as_utc(run.finished_at)
    if (
        not isinstance(body, dict)
        or publication.schema_version not in {
            "research-publication-source.v1", "research-publication-source.v2",
            "research-publication-source.v3", "research-publication-source.v4",
        }
        or observation_json(body) != publication.payload_json
        or publication.output_id != "publication_" + publication.payload_hash[:40]
        or body.get("schema_version") != publication.schema_version
        or body.get("run_id") != run_id or body.get("event_id") != event_id
        or body.get("artifact_id") != artifact_id
        or body.get("graph_final_ref") != reference.model_dump(mode="json")
        or finished_at is None or pub_ref.recorded_at != finished_at
        or body.get("recorded_at") != finished_at.isoformat()
    ):
        raise ValueError("action_final_precheck_publication_mismatch")
    row = session.get(RunRoleOutputRecord, reference.row.output_id, populate_existing=True)
    if (
        row is None or row.run_id != run_id or row_reference(row) != reference.row
        or row.role != "research_graph_final"
    ):
        raise ValueError("action_final_precheck_final_mismatch")
    value = parse_graph_final_projection_declaration(
        row.payload_json, schema_version=row.schema_version
    )
    if (
        value.run_id != run_id or value.recorded_at != reference.row.recorded_at
        or value.recorded_at > finished_at
        or observation_json(value.model_dump(mode="json")) != row.payload_json
        or row.output_id != "research_graph_final_" + observation_sha(
            value.model_dump(mode="json", exclude={"recorded_at"})
        )
    ):
        raise ValueError("action_final_precheck_final_invalid")
    if value.selected_synthesis_observation_ref is None:
        raise ActionFinalSynthesisAbsent()


@dataclass(frozen=True)
class TrustedActionHorizon:
    """The exact selected raw horizon, not a cross-horizon probability proxy."""

    horizon: str
    candidate_json: str


@dataclass(frozen=True)
class TrustedNativeActionSource:
    """One selected native candidate, never a reusable publication permission."""

    synthesis_json: str
    raw_candidate_json: str
    raw_candidate_hash: str
    projected_research_candidate_hash: str
    selection_witness_json: str
    mapping_input_ref: ObservedExecutionRef
    producer_generation: int
    producer_cutoff_at: datetime
    task_request_hash: str
    execution_request_hash: str
    raw_context_hash: str
    execution_mode: Literal["live", "replay"]
    producer_input_evidence_json: str
    review_gate_status: Literal["matched", "unreviewed"]
    review_effect_ref: ObservedExecutionRef | None


@dataclass(frozen=True)
class TrustedActionResearchInputs:
    """Immutable values observed in one authenticated read; no reusable authority.

    JSON strings keep the old contract bytes/content separate from mutable DTOs.
    quality_binding describes only whether the saved quality assessed this exact
    selected raw Result. A newer rejected raw Result may have different quality.
    """

    run_id: str
    event_id: str
    artifact_id: str
    reference: GraphFinalProjectionRef
    publication_ref: ObservedExecutionRef
    published_at: datetime
    decision_cutoff_at: datetime
    request_id: str
    scope_id: str | None
    graph_result_json: str
    selected_observation_ref: ObservedExecutionRef | None
    selected_mapping_generation: int | None
    selected_request_hash: str | None
    selected_context_hash: str | None
    selected_result_json: str | None
    selected_candidate_json: str | None
    selection_witness_json: str | None
    horizons: tuple[TrustedActionHorizon, ...]
    quality_view_json: str
    quality_binding: Literal["selected_raw", "different_raw", "unavailable"]
    action_status: Literal["unsupported", "native_candidate"]
    action_reason_code: str
    native_action: TrustedNativeActionSource | None = None


def _native_action_from_originals(
    *,
    selected: ResearchMappedObservation,
    raw: ResearchTransportObservation,
    request: ResearchSessionRequest,
    decision_cutoff_at: datetime,
    task_created_at: datetime,
) -> TrustedNativeActionSource:
    """Replay the actual attestation mapper, after the caller authenticates rows.

    This private component has no SQL authority. The public reader additionally
    reads the frozen execution and durable review original in its snapshot.
    """
    from packages.runtime_adapters.dsh_runtime.event_mapper import map_notifications
    from packages.runtime_adapters.dsh_runtime.result_mapper import map_session_result_with_witness
    from packages.runtime_adapters.dsh_runtime.web_runtime import (
        _sdk_run,  # pyright: ignore[reportPrivateUsage]
    )

    payload = raw.reply
    context = raw.context
    if synthesis_protocol(request.output_schema_ref) != "decision":
        raise ValueError("action_native_frozen_protocol_mismatch")
    # Native context stores execution.request_hash (the complete frozen request),
    # not the DSH link hash which deliberately excludes round evidence/deadlines.
    execution_request_hash = observation_sha(request.model_dump(mode="json"))
    if (
        raw.phase != "result"
        or not isinstance(payload, DshSessionResult)
        or raw.error is not None
        or raw.error_code is not None
        or request.run_id != context.run_id
        or request.event_id != context.event_id
        or request.request_id != context.request_id
        or execution_request_hash != context.execution_request_hash
        or selected.context.run_id != context.run_id
        or selected.context.event_id != context.event_id
        or selected.context.request_id != context.request_id
        or selected.context.original_task_request_hash != context.original_task_request_hash
        or selected.mapping_input_generation != context.generation
        or payload.run_id != context.run_id
        or payload.dsh_session_id != context.dsh_session_id
        or payload.generation != context.generation
        or selected.result.request_id != context.request_id
        or selected.result.research_session_id != context.dsh_session_id
    ):
        raise ValueError("action_native_original_identity_mismatch")
    if (
        raw.recorded_at > selected.recorded_at
        or payload.finished_at > raw.recorded_at
        or payload.started_at > payload.finished_at
        or selected.result.finished_at != payload.finished_at
    ):
        raise ValueError("action_native_original_time_mismatch")
    if (
        raw.events_json_hash != sha(payload.events_json)
        or raw.final_response_hash != sha(payload.final_response)
        or payload.result_hash != sha(payload.final_response + "\0" + payload.events_json)
    ):
        raise ValueError("action_native_transport_hash_mismatch")
    events = read_transport_events_json(payload.events_json)
    verify_selection_witness(payload, selected.selection)
    sdk_run = _sdk_run(payload)
    traces = map_notifications(
        sdk_run.notifications,
        run_id=context.run_id,
        research_session_id=context.dsh_session_id,
        received_at=payload.finished_at,
        request=request,
    )
    remapped = map_session_result_with_witness(
        sdk_run,
        request,
        traces,
        runtime_version=selected.result.runtime_version,
        profile_ref=selected.result.profile_ref,
        started_at=selected.result.started_at,
        finished_at=payload.finished_at,
        accepted_synthesis_only=True,
    )
    action = remapped.action_synthesis
    witness = remapped.selection
    if action is None or witness.kind != "tool_capture":
        raise ValueError("action_native_attested_candidate_missing")
    declared_cutoffs = {
        *(item.as_of for item in action.synthesis.action_intents),
        *(item.cutoff for item in action.synthesis.mechanism_reviews),
    }
    if len(declared_cutoffs) != 1:
        raise ValueError("action_native_candidate_cutoff_mismatch")
    producer_cutoff = next(iter(declared_cutoffs))
    # PIT is an admission ceiling (live requests may set it to their deadline).
    # The reviewed wrapper declares its information time. Actual source PIT is
    # checked again by the publisher; this is not a user-visible/publication time.
    if (
        not task_created_at
        <= producer_cutoff
        <= min(
            request.pit_cutoff_at,
            payload.finished_at,
            decision_cutoff_at,
        )
    ):
        raise ValueError("action_native_candidate_cutoff_mismatch")
    call_index, result_index = witness.call_event_index, witness.result_event_index
    if call_index is None or result_index is None:
        raise ValueError("action_native_selection_index_missing")
    # The Host raw envelope may retain extra metadata. The runtime's durable
    # witness hashes that whole envelope, not the SDK's {method,payload} view.
    envelope_hashes = {
        "call_event_hash": observation_sha(events[call_index]),
        "result_event_hash": observation_sha(events[result_index]),
    }
    expected = witness.model_copy(update=envelope_hashes)
    persisted = selected.selection
    if (
        persisted.origin_generation is not None
        or persisted.presence_generation not in (None, context.generation)
        or persisted.presence_ref not in (None, selected.mapping_input_ref)
        or persisted.model_copy(update={"presence_generation": None, "presence_ref": None})
        != expected
    ):
        raise ValueError("action_native_full_selection_mismatch")
    if (
        selected.result.causal_case != remapped.result.causal_case
        or selected.result.horizons != remapped.result.horizons
    ):
        raise ValueError("action_native_projection_mismatch")
    gate = _selected_gate(events, persisted, context, decision=True)
    native_witness = action.selection.model_copy(
        update={
            **envelope_hashes,
            "presence_generation": context.generation,
            "presence_ref": selected.mapping_input_ref,
        }
    )
    candidate: Any = events[result_index]
    assert native_witness.payload_path is not None
    for step in native_witness.payload_path:
        candidate = strict_json(candidate) if step is None else candidate[step]
    # Preserve the actual decoded object: normalizing 1 to 1.0 must never change
    # which raw content hash is committed as the native candidate.
    candidate_json = observation_json(candidate)
    if sha(candidate_json) != action.raw_candidate_hash:
        raise ValueError("action_native_candidate_hash_mismatch")
    return TrustedNativeActionSource(
        synthesis_json=action.synthesis.model_dump_json(),
        raw_candidate_json=candidate_json,
        raw_candidate_hash=action.raw_candidate_hash,
        projected_research_candidate_hash=action.projected_research_candidate_hash,
        selection_witness_json=native_witness.model_dump_json(),
        mapping_input_ref=selected.mapping_input_ref,
        producer_generation=context.generation,
        producer_cutoff_at=producer_cutoff,
        task_request_hash=context.original_task_request_hash,
        execution_request_hash=context.execution_request_hash,
        raw_context_hash=observation_sha(context.model_dump(mode="json")),
        execution_mode=request.execution_mode,
        producer_input_evidence_json=observation_json(
            [item.model_dump(mode="json") for item in request.input_evidence]
        ),
        review_gate_status="matched" if gate is not None else "unreviewed",
        review_effect_ref=None,
    )


def _read_native_action_in_session(
    session: Session,
    *,
    selected_ref: ObservedExecutionRef,
    selected: ResearchMappedObservation,
    decision_cutoff_at: datetime,
) -> TrustedNativeActionSource | None:
    task_refs = [ref for ref in selected.context.rows if ref.role == "research_task_request"]
    if len(task_refs) != 1:
        raise ValueError("action_native_task_original_missing")
    task = session.get(RunRoleOutputRecord, task_refs[0].output_id, populate_existing=True)
    if (
        task is None
        or task.run_id != selected.context.run_id
        or row_reference(task) != task_refs[0]
    ):
        raise ValueError("action_native_task_original_mismatch")
    task_body = strict_json(task.payload_json)
    request_body = task_body.get("request") if isinstance(task_body, dict) else None
    if not isinstance(request_body, dict):
        raise ValueError("action_native_task_request_missing")
    output_schema_ref = request_body.get("output_schema_ref")
    if not isinstance(output_schema_ref, str):
        raise ValueError("action_native_task_protocol_missing")
    if synthesis_protocol(output_schema_ref) == "legacy":
        return None
    task_request = ResearchSessionRequest.model_validate(request_body)
    task_created_at = (
        task_request.context_manifest.created_at
        if task_request.context_manifest is not None
        else as_utc(task.created_at)
    )
    raw = read_transport_observation(session, selected.mapping_input_ref)
    prepared = read_prepared_execution_in_session(
        session,
        raw.context.run_id,
        raw.context.generation,
    )
    execution = prepared.execution
    if (
        execution.generation != raw.context.generation
        or execution.task_request_hash != raw.context.original_task_request_hash
        or execution.request_hash != raw.context.execution_request_hash
        or execution.dsh_session_id != raw.context.dsh_session_id
        or prepared.method.method_hash != raw.context.method_hash
        or prepared.method.definition.bundle.bundle_hash != raw.context.bundle_hash
        or prepared.method.request_hash != raw.context.first_linked_request_hash
        or sha(prepared.prompt.prompt) != raw.context.prompt_hash
    ):
        raise ValueError("action_native_frozen_execution_mismatch")
    native = _native_action_from_originals(
        selected=selected,
        raw=raw,
        request=ResearchSessionRequest.model_validate(execution.request.model_dump(mode="json")),
        decision_cutoff_at=decision_cutoff_at,
        task_created_at=task_created_at,
    )
    if native.review_gate_status == "matched":
        effects = read_review_effects_in_session(
            session,
            mapped_ref=selected_ref,
            source_result=selected.result,
        )
        native = _attach_review_effect(native, selected_ref, selected, effects)
    return native


def _attach_review_effect(
    native: TrustedNativeActionSource,
    selected_ref: ObservedExecutionRef,
    selected: ResearchMappedObservation,
    effects: ResearchReviewEffects | None,
) -> TrustedNativeActionSource:
    """Bind an already authenticated durable review to this exact full source."""
    if (
        effects is None
        or effects.review_effect_ref is None
        or effects.mapped_ref != selected_ref
        or effects.transport_ref != selected.mapping_input_ref
        or effects.selected_candidate_hash != native.projected_research_candidate_hash
        # Review effects bind the entire immutable source, including the
        # attached native reviews. result_content_hash intentionally excludes
        # reviews and is a different contract used by each public review.
        or effects.source_result_hash != observation_sha(selected.result.model_dump(mode="json"))
    ):
        raise ValueError("action_native_review_original_missing_or_mismatched")
    return replace(native, review_effect_ref=effects.review_effect_ref)


def read_trusted_research_in_session(
    session: Session,
    *,
    run_id: str,
    event_id: str,
    artifact_id: str,
    reference: GraphFinalProjectionRef,
) -> TrustedActionResearchInputs:
    """Fully authenticate the committed publication and its selected native raw.

    No caller-supplied Result, candidate, quality verdict or ``verified`` flag is
    accepted. Corrupt/mixed originals raise; a lawful old contract returns readable
    research with unsupported Action synthesis. The caller owns failure receipts.
    """
    if any(
        not isinstance(value, str) or not value  # pyright: ignore[reportUnnecessaryIsInstance]
        for value in (run_id, event_id, artifact_id)
    ):
        raise ValueError("action_research_identity_invalid")
    if not isinstance(reference, GraphFinalProjectionRef):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise ValueError("action_research_final_reference_invalid")
    reference = GraphFinalProjectionRef.model_validate_json(reference.model_dump_json())
    connection = session.connection()
    native = connection.connection.driver_connection
    if (
        connection.dialect.name != "sqlite"
        or not isinstance(native, sqlite3.Connection)
        or not native.in_transaction
        or session.new
        or session.dirty
        or session.deleted
    ):
        # Reject before an ORM get can autoflush a caller's pending writes.
        raise ValueError("action_research_clean_read_snapshot_required")
    # This existing scope tracks source changes and reuses only this invocation's
    # fully verified final, after the clean physical read check above.
    with publication_final_read_scope(session):
        run = session.get(RunRecord, run_id, populate_existing=True)
        if run is None or run.event_id != event_id or run.artifact_id != artifact_id:
            raise ValueError("action_research_run_identity_mismatch")
        if session.get(EventRecord, event_id, populate_existing=True) is None:
            raise ValueError("action_research_event_missing")
        if run.status not in {"completed", "degraded"}:
            raise ValueError("action_research_publication_not_completed")
        published_at = as_utc(run.finished_at)
        if published_at is None:
            raise ValueError("action_research_publication_time_missing")
        original = read_graph_final_in_session(
            session,
            run_id=run_id,
            reference=reference,
            expected_lease_owner=None,
            require_current_permission=False,
        )
        reviews = list(
            session.scalars(
                select(RunRoleOutputRecord)
                .where(
                    RunRoleOutputRecord.run_id == run_id,
                    RunRoleOutputRecord.role == "independent_reviewer",
                )
                .execution_options(populate_existing=True)
            )
        )
        if len(reviews) != 1:
            raise ValueError("action_research_review_original_missing")
        declared = original.result.model_copy(update={"reviews": [_decode_review_row(reviews[0])]})
        committed_artifact, _gate = _read_publication_source_in_session(
            session,
            run=run,
            reference=reference,
            declared=declared,
            expected_lease_owner=None,
            candidate=None,
            gate=None,
        )
        if committed_artifact != artifact_id:
            raise ValueError("action_research_artifact_mismatch")
        publication = session.scalar(
            select(RunRoleOutputRecord)
            .where(
                RunRoleOutputRecord.run_id == run_id,
                RunRoleOutputRecord.role == PUBLICATION_SOURCE_ROLE,
            )
            .execution_options(populate_existing=True)
        )
        if publication is None:
            raise ValueError("action_research_publication_original_missing")
        publication_ref = ObservedExecutionRef(
            output_id=publication.output_id,
            role=publication.role,
            schema_version=publication.schema_version,
            payload_hash=publication.payload_hash,
            recorded_at=published_at,
        )
        quality = read_published_research_quality_in_session(session, run_id, as_of=published_at)
        selected_ref = original.receipt.selected_synthesis_observation_ref
        selected = read_mapped_observation(session, selected_ref) if selected_ref else None
        candidate = selected.selection.candidate if selected is not None else None
        if selected is not None and (
            selected.context.run_id != run_id
            or selected.context.event_id != event_id
            or selected.result.request_id != selected.context.request_id
            or selected.result.research_session_id != selected.context.dsh_session_id
            or selected.recorded_at > original.receipt.recorded_at
            or candidate is not None
            and candidate.request_id != selected.context.request_id
        ):
            raise ValueError("action_research_selected_identity_mismatch")
        horizons = (
            tuple(
                TrustedActionHorizon(item.horizon, observation_json(item.model_dump(mode="json")))
                for item in candidate.horizons
            )
            if candidate is not None
            else ()
        )
        if len({item.horizon for item in horizons}) != len(horizons):
            raise ValueError("action_research_selected_horizon_duplicate")
        quality_binding: Literal["selected_raw", "different_raw", "unavailable"] = "unavailable"
        if quality.decision is not None and selected is not None:
            quality_binding = (
                "selected_raw"
                if quality.decision.source_result_hash
                == observation_sha(selected.result.model_dump(mode="json"))
                else "different_raw"
            )
        native_action = (
            _read_native_action_in_session(
                session,
                selected_ref=selected_ref,
                selected=selected,
                decision_cutoff_at=original.receipt.decision_cutoff_at,
            )
            if selected is not None and selected_ref is not None and candidate is not None
            else None
        )
        return TrustedActionResearchInputs(
            run_id=run_id,
            event_id=event_id,
            artifact_id=artifact_id,
            reference=reference,
            publication_ref=publication_ref,
            published_at=published_at,
            decision_cutoff_at=original.receipt.decision_cutoff_at,
            request_id=(selected.context.request_id if selected else original.result.request_id),
            scope_id=quality.decision.scope_id if quality.decision is not None else None,
            graph_result_json=observation_json(original.result.model_dump(mode="json")),
            selected_observation_ref=selected_ref,
            selected_mapping_generation=selected.mapping_input_generation if selected else None,
            selected_request_hash=(
                selected.context.original_task_request_hash if selected else None
            ),
            selected_context_hash=(
                observation_sha(selected.context.model_dump(mode="json")) if selected else None
            ),
            selected_result_json=(
                observation_json(selected.result.model_dump(mode="json")) if selected else None
            ),
            selected_candidate_json=(
                observation_json(candidate.model_dump(mode="json")) if candidate else None
            ),
            selection_witness_json=(
                observation_json(selected.selection.model_dump(mode="json")) if selected else None
            ),
            horizons=horizons,
            quality_view_json=quality.model_dump_json(),
            quality_binding=quality_binding,
            action_status="native_candidate" if native_action else "unsupported",
            action_reason_code=(
                "action_native_candidate_requires_publication_validation"
                if native_action
                else "action_native_typed_synthesis_unavailable"
                if candidate is not None
                else "action_selected_native_candidate_unavailable"
            ),
            native_action=native_action,
        )

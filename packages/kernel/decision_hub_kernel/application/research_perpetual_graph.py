"""One native perpetual slice through existing Graph writer, without financial grants."""

from __future__ import annotations

import hashlib
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..persistence.db import (
    Database,
    DshSessionLinkRecord,
    RunRecord,
    RunRoleOutputRecord,
    SnapshotRecord,
    as_utc,
    utcnow,
)
from ..ports.research_observation import ResearchMappedObservationV2
from ..ports.research_synthesis_protocol import synthesis_protocol
from . import research_graph_binding as g
from .fact_store import ResearchFactStore
from .research_evidence import ResearchEvidenceService
from .research_execution_binding import read_prepared_execution_in_session
from .research_review import research_result_content_hash


def recipe_hash():
    return hashlib.sha256(Path(__file__).read_bytes() + Path(g.__file__).read_bytes()).hexdigest()


def project_state(base, mapped, evidence, facts):
    """Evidence summary plus immutable carrier locator; no old horizon or quality score."""
    if synthesis_protocol(base.output_schema_ref) != "decision_v2" or not isinstance(
        mapped, ResearchMappedObservationV2
    ):
        raise ValueError("research_perpetual_graph_protocol_mismatch")
    result = mapped.result
    if result.horizons or result.causal_case is not None or result.reviews:
        raise ValueError("research_perpetual_graph_legacy_projection_forbidden")
    state = g.initial_research_state(base)
    cutoff = g._round_cutoff(state, base, result)
    return {
        **state,
        "evidence": [row.model_dump(mode="json") for row in evidence],
        "facts": [row.model_dump(mode="json") for row in facts],
        "rounds": [row.model_dump(mode="json") for row in result.rounds],
        "latest_result": result.model_dump(mode="json"),
        "coverage": result.final_coverage.model_dump(mode="json"),
        "total_tool_calls": result.total_tool_calls,
        "total_subagents": result.total_subagents,
        "total_tokens": result.total_tokens,
        "estimated_cost_usd": result.estimated_cost_usd,
        "decision_cutoff_at": cutoff.isoformat(),
        "loop_should_continue": False,
        "loop_blocks_completion": True,
        "loop_summary": "Native perpetual packet retained; financial qualification is independently assessed.",
        "financial_qualification": "unqualified",
    }


def _binding(session, run_id, mapped, *, active):
    if not isinstance(mapped, ResearchMappedObservationV2) or mapped.context.run_id != run_id:
        raise ValueError("research_perpetual_graph_mapping_required")
    base = g._original_request(session, run_id)
    producer = read_prepared_execution_in_session(session, run_id, mapped.context.generation)
    # The prepared V3 reader authenticates the full Selection/request/binding.
    # Its legal round projection differs from the immutable original Task.
    actual = g.ResearchSessionRequest.model_validate(
        producer.execution.request.model_dump(mode="json")
    )
    if base.current_round != 1 or actual.current_round != 1:
        raise ValueError("research_perpetual_graph_first_slice_required")
    if actual.model_dump(mode="json") != base.model_dump(mode="json"):
        g._check_round_request(base, actual, g.initial_research_state(base))
    if (
        active
        and read_prepared_execution_in_session(session, run_id).execution.generation
        != mapped.context.generation
    ):
        raise ValueError("research_graph_current_transport_changed")
    link = session.get(DshSessionLinkRecord, run_id, populate_existing=True)
    if (
        link is None
        or mapped.result.total_tool_calls > base.execution_budget.max_tool_calls
        or mapped.result.total_tool_calls > link.tool_calls_started
    ):
        raise ValueError("research_graph_original_tool_counter_invalid")
    return base


def admit_round(session, *, run_id, expected_lease_owner, observed, database, previous):
    g._require_writer(session)
    run = g._permission(session, run_id, expected_lease_owner)
    if previous is not None:
        raise ValueError("research_perpetual_graph_first_slice_required")
    mapped = g._mapped(session, observed.observation_ref, active=True)
    base = _binding(session, run_id, mapped, active=True)
    if mapped.result != observed.result:
        raise ValueError("research_graph_declared_runtime_result_mismatch")
    existing = session.scalar(
        select(RunRoleOutputRecord).where(
            RunRoleOutputRecord.run_id == run_id,
            RunRoleOutputRecord.role == "research_graph_round:1",
        )
    )
    if existing is not None:
        frozen = read_round(
            session,
            run_id=run_id,
            reference=g.GraphRoundAdmissionRef(row=g._row_ref(existing)),
            expected_lease_owner=expected_lease_owner,
            active=True,
        )
        if frozen.receipt.observation_ref != observed.observation_ref:
            raise ValueError("research_graph_original_receipt_conflict")
        g._fence(session, run_id, expected_lease_owner)
        return frozen
    cutoff = g._round_cutoff(g.initial_research_state(base), base, mapped.result)
    service = ResearchEvidenceService(database)
    accepted = service.accept_candidates_in_session(
        session,
        run_id=run_id,
        capability_id="dsh.research",
        candidates=mapped.result.evidence_candidates,
        requirements={row.requirement_id: row for row in base.evidence_requirements},
        cutoff_at=cutoff,
        facts=mapped.result.facts or [],
    )
    session.flush()
    ResearchFactStore(database).accept_facts_in_session(
        session,
        run_id=run_id,
        capability_id="dsh.research",
        facts=mapped.result.facts or [],
        cutoff_at=cutoff,
    )
    evidence = service.list_run_evidence_in_session(session, run_id)
    facts = ResearchFactStore(database).list_run_facts_in_session(session, run_id)
    state = project_state(base, mapped, evidence, facts)
    now = utcnow()
    from .research_review_effects import record_review_effects_in_session

    record_review_effects_in_session(
        session,
        mapped_ref=observed.observation_ref,
        source_result=mapped.result,
        expected_lease_owner=expected_lease_owner,
        known_at=now,
    )
    receipt = g.ResearchPerpetualGraphRound(
        run_id=run_id,
        logical_round=1,
        previous_ref=None,
        observation_ref=observed.observation_ref,
        observation_kind="mapped",
        observed_tool_calls=mapped.result.total_tool_calls,
        native_context=mapped.context,
        selected_synthesis_observation_ref=observed.observation_ref,
        latest_observation_ref=mapped.latest_transport_ref,
        selected_mapping_generation=mapped.mapping_input_generation,
        latest_transport_generation=mapped.context.generation,
        recipe_hash=recipe_hash(),
        evidence=evidence,
        facts=facts,
        admitted_evidence_ids=[row.evidence_id for row in accepted],
        material_rows=g._material_rows(
            session, run_id, [row.evidence_id for row in evidence], [row.fact_id for row in facts]
        ),
        projected_state=state,
        state_hash=g.observation_sha(state),
        admitted_lease_owner=expected_lease_owner,
        writer_clock_at=now,
        lease_expires_at=as_utc(run.lease_expires_at),
        recorded_at=now,
    )
    reference = g.GraphRoundAdmissionRef(row=g._store(session, receipt))
    g._context_valid(session, mapped.context, active=True)
    g._verify_material(session, receipt)
    g._fence(session, run_id, expected_lease_owner)
    return g.FrozenGraphRound(reference, receipt, state)


def read_round(session, *, run_id, reference, expected_lease_owner, active):
    if active:
        g._permission(session, run_id, expected_lease_owner)
    receipt = g._read(session, reference.row, g.ResearchPerpetualGraphRound, run_id)
    if (
        receipt.logical_round != 1
        or receipt.previous_ref is not None
        or receipt.observation_kind != "mapped"
    ):
        raise ValueError("research_perpetual_graph_first_slice_required")
    mapped = g._mapped(session, receipt.observation_ref, active=active)
    base = _binding(session, run_id, mapped, active=active)
    g._verify_material(session, receipt)
    state = project_state(base, mapped, receipt.evidence, receipt.facts)
    if (
        receipt.native_context != mapped.context
        or receipt.selected_synthesis_observation_ref != receipt.observation_ref
        or receipt.latest_observation_ref != mapped.latest_transport_ref
        or receipt.selected_mapping_generation != mapped.mapping_input_generation
        or receipt.latest_transport_generation != mapped.context.generation
        or receipt.observed_tool_calls != mapped.result.total_tool_calls
        or receipt.projected_state != state
        or receipt.state_hash != g.observation_sha(state)
        or receipt.writer_clock_at != receipt.recorded_at
        or receipt.lease_expires_at <= receipt.writer_clock_at
        or not receipt.admitted_lease_owner
        or receipt.recorded_at > utcnow()
        or any(
            ref.recorded_at > receipt.recorded_at
            for ref in [receipt.observation_ref, receipt.latest_observation_ref]
        )
    ):
        raise ValueError("research_perpetual_graph_round_binding_invalid")
    if active:
        g._permission(session, run_id, expected_lease_owner)
    return g.FrozenGraphRound(reference, receipt, state)


def freeze_final(session, *, run_id, expected_lease_owner, database, last_admission):
    g._require_writer(session)
    g._permission(session, run_id, expected_lease_owner)
    frozen = read_round(
        session,
        run_id=run_id,
        reference=last_admission,
        expected_lease_owner=expected_lease_owner,
        active=True,
    )
    existing = session.scalar(
        select(RunRoleOutputRecord).where(
            RunRoleOutputRecord.run_id == run_id, RunRoleOutputRecord.role == "research_graph_final"
        )
    )
    if existing is not None:
        value = read_final(
            session,
            run_id=run_id,
            reference=g.GraphFinalProjectionRef(row=g._row_ref(existing)),
            expected_lease_owner=expected_lease_owner,
            active=True,
        )
        if value.receipt.last_admission_ref != last_admission:
            raise ValueError("research_graph_final_original_conflict")
        g._fence(session, run_id, expected_lease_owner)
        return value
    return _freeze_authenticated_final(
        session, run_id=run_id, expected_lease_owner=expected_lease_owner,
        database=database, frozen=frozen,
    )


def _freeze_authenticated_final(
    session: Session, *, run_id: str, expected_lease_owner: str | None,
    database: Database, frozen: g.FrozenGraphRound,
) -> g.FrozenGraphFinal:
    """Internal producer body; callers must freshly fence their original proof."""
    g._require_writer(session)
    g._permission(session, run_id, expected_lease_owner)
    snapshot = ResearchEvidenceService(database).freeze_decision_snapshot_in_session(
        session,
        run_id=run_id,
        evidence_refs=[
            row.evidence_id for row in frozen.receipt.evidence if row.quality == "accepted"
        ],
        cutoff_at=g._decision_cutoff(frozen.state, g._original_request(session, run_id)),
    )
    row = session.get(SnapshotRecord, snapshot.snapshot_id, populate_existing=True)
    assert row is not None
    final = g.ResearchSessionResult.model_validate(frozen.state["latest_result"])
    receipt = g.ResearchPerpetualGraphFinal(
        run_id=run_id,
        last_admission_ref=frozen.reference,
        selected_synthesis_observation_ref=frozen.receipt.selected_synthesis_observation_ref,
        latest_observation_ref=frozen.receipt.latest_observation_ref,
        native_context=frozen.receipt.native_context,
        recipe_hash=recipe_hash(),
        decision_snapshot_id=row.snapshot_id,
        decision_snapshot_hash=row.snapshot_hash,
        decision_snapshot_row_hash=g.observation_sha(g._columns(row)),
        decision_cutoff_at=as_utc(row.cutoff_at),
        projected_result=final,
        projected_result_hash=g.observation_sha(final.model_dump(mode="json")),
        projected_result_content_hash=research_result_content_hash(final),
        recorded_at=utcnow(),
    )
    reference = g.GraphFinalProjectionRef(row=g._store(session, receipt))
    g._snapshot_valid(session, frozen, row)
    g._fence(session, run_id, expected_lease_owner)
    return g.FrozenGraphFinal(reference, receipt, final)


def read_final(session, *, run_id, reference, expected_lease_owner, active):
    if active:
        g._permission(session, run_id, expected_lease_owner)
    receipt = g._read(session, reference.row, g.ResearchPerpetualGraphFinal, run_id)
    frozen = read_round(
        session,
        run_id=run_id,
        reference=receipt.last_admission_ref,
        expected_lease_owner=expected_lease_owner,
        active=active,
    )
    final = g.ResearchSessionResult.model_validate(frozen.state["latest_result"])
    run = session.get(RunRecord, run_id, populate_existing=True)
    row = session.get(SnapshotRecord, receipt.decision_snapshot_id, populate_existing=True)
    if row is None:
        raise ValueError("research_graph_decision_snapshot_binding_invalid")
    g._snapshot_valid(session, frozen, row)
    if (
        run is None
        or run.decision_snapshot_id != row.snapshot_id
        or receipt.native_context != frozen.receipt.native_context
        or receipt.selected_synthesis_observation_ref
        != frozen.receipt.selected_synthesis_observation_ref
        or receipt.latest_observation_ref != frozen.receipt.latest_observation_ref
        or receipt.decision_snapshot_hash != row.snapshot_hash
        or receipt.decision_snapshot_row_hash != g.observation_sha(g._columns(row))
        or receipt.decision_cutoff_at != as_utc(row.cutoff_at)
        or final != receipt.projected_result
        or receipt.projected_result_hash != g.observation_sha(final.model_dump(mode="json"))
        or receipt.projected_result_content_hash != research_result_content_hash(final)
        or receipt.recorded_at
        < max(
            receipt.last_admission_ref.row.recorded_at, receipt.latest_observation_ref.recorded_at
        )
        or receipt.recorded_at > utcnow()
    ):
        raise ValueError("research_perpetual_graph_final_binding_invalid")
    if active:
        g._permission(session, run_id, expected_lease_owner)
    return g.FrozenGraphFinal(reference, receipt, final)

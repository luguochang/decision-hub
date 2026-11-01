"""One finalization's complete original proof, then its fresh fenced writer."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..persistence.callback_source_lease import callback_source_lease_scope
from ..persistence.db import Database, DshSessionLinkRecord, RunRoleOutputRecord
from ..persistence.original_preparation import (
    ExecutionBoundary,
    PreparedOriginals,
    begin_immediate,
    prepare_originals,
)
from . import research_graph_binding as g
from . import research_perpetual_graph as p
from .research_graph_projection import AgenticResearchState


@dataclass(frozen=True)
class _Finalization:
    reference_json: str
    round_json: str
    state_json: str
    boundary: ExecutionBoundary
    existing: g.FrozenGraphFinal | None


def _prepare(
    session: Session,
    state: AgenticResearchState,
    owner: str | None,
) -> _Finalization:
    native, checked = g.prepare_graph_state_in_session(session, state, owner)
    if not native or checked.get("graph_final_projection_ref"):
        raise ValueError("research_perpetual_graph_protocol_mismatch")
    reference = g.parse_graph_round_ref(checked["graph_round_admission_ref"])
    if reference.row.schema_version != g.PERPETUAL_ROUND_SCHEMA:
        raise ValueError("research_perpetual_graph_protocol_mismatch")
    request = g._original_request(session, checked["request"]["run_id"])
    run_id = request.run_id
    receipt = g._read(session, reference.row, g.ResearchPerpetualGraphRound, run_id)
    link = session.get(DshSessionLinkRecord, run_id, populate_existing=True)
    if link is None:
        raise PermissionError("original_preparation_generation_changed")
    boundary = ExecutionBoundary(
        link.generation,
        link.dsh_session_id,
        link.request_hash,
        link.plugin_build_hash,
        request.deadline_at,
    )
    boundary.require_current_link(session, run_id)
    row = session.scalar(
        select(RunRoleOutputRecord).where(
            RunRoleOutputRecord.run_id == run_id,
            RunRoleOutputRecord.role == "research_graph_final",
        )
    )
    existing = (
        None
        if row is None
        else p.read_final(
            session,
            run_id=run_id,
            reference=g.GraphFinalProjectionRef(row=g._row_ref(row)),
            expected_lease_owner=owner,
            active=True,
        )
    )
    if existing is not None and existing.receipt.last_admission_ref != reference:
        raise ValueError("research_graph_final_original_conflict")
    return _Finalization(
        reference.model_dump_json(),
        receipt.model_dump_json(),
        g.observation_json(receipt.projected_state),
        boundary,
        existing,
    )


def finalize_perpetual_graph(
    database: Database,
    state: AgenticResearchState,
    expected_lease_owner: str | None,
    *,
    checkpoint: Callable[[], None] | None = None,
) -> g.FrozenGraphFinal:
    """A synchronous call owns all Sessions and immutable source HANDLEs."""
    state = cast(AgenticResearchState, json.loads(g.observation_json(state)))
    run_id = state["request"]["run_id"]
    with callback_source_lease_scope(checkpoint) as lease:

        def authenticate(session: Session) -> _Finalization:
            value = _prepare(session, state, expected_lease_owner)
            if lease is not None:
                lease.extend_authenticated_selection(session, run_id)
            return value

        prepared = prepare_originals(
            database, authenticate, source_lease=lease, checkpoint=checkpoint
        )
        return _write_final(database, prepared, run_id, expected_lease_owner, checkpoint)


def _write_final(
    database: Database,
    prepared: PreparedOriginals[_Finalization],
    run_id: str,
    owner: str | None,
    checkpoint: Callable[[], None] | None,
) -> g.FrozenGraphFinal:
    with database.session() as session:
        begin_immediate(session)
        # The carrier never crosses a callback/RPC and must be issued for
        # this engine and exact value, not a caller-declared cached proof.
        prepared._require_issued(session)
        prepared.verify(session, checkpoint=checkpoint)
        plan = prepared.value
        g._permission(session, run_id, owner)
        plan.boundary.require_current_link(session, run_id)
        if plan.existing is not None:
            result = plan.existing
        else:
            receipt = g.ResearchPerpetualGraphRound.model_validate_json(plan.round_json)
            frozen = g.FrozenGraphRound(
                g.parse_graph_round_ref(json.loads(plan.reference_json)),
                receipt,
                cast(AgenticResearchState, json.loads(plan.state_json)),
            )
            result = p._freeze_authenticated_final(  # pyright: ignore[reportPrivateUsage]
                session,
                run_id=run_id,
                expected_lease_owner=owner,
                database=database,
                frozen=frozen,
            )
        if checkpoint is not None:
            checkpoint()
        # Readset passed before our own snapshot/receipt writes. Held source
        # bytes and the fresh final permission remain independent.
        if not prepared.source.matches():
            raise ValueError("original_preparation_source_changed")
        plan.boundary.require_current_link(session, run_id)
        g._fence(session, run_id, owner)
        plan.boundary.require_current_link(session, run_id)
        return result

"""Bounded recent research projection; original Run view remains authority."""
from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import and_, func, select
from sqlalchemy.orm import aliased, load_only

from packages.kernel.decision_hub_kernel.persistence.db import (
    ArtifactRecord, DshSessionLinkRecord, ObservationRecord, ResearchEvidenceRecord,
    ResearchFactRecord, ResearchResultRecord, ResearchTraceRecord, RunRecord,
)

if TYPE_CHECKING:
    from packages.contracts_py.decision_hub_contracts.models import ResearchRunView
    from .service import ResearchQueryService


def recent_research_runs(service: ResearchQueryService, *, limit: int = 8) -> list[ResearchRunView]:
    """Select recent research.v1 Runs before constructing their original views.

    This dedicated preview keeps the original priority/cursor list unchanged.
    It does not capture, publish, grant, mutate or delete any research material.
    """
    if type(limit) is not int or not 1 <= limit <= 8:
        raise ValueError("research_recent_limit_invalid")
    with service.database.session() as session:
        rows = (session.query(RunRecord)
                .filter(RunRecord.strategy_version == "research.v1")
                .order_by(RunRecord.updated_at.desc(), RunRecord.run_id.asc())
                .limit(limit).all())
        material = _read_recent_run_rows(session, rows)
        return [service._run_view(session, row, _read_rows=material[row.run_id]) for row in rows]


def _read_recent_run_rows(session, runs):
    """Batch raw list inputs, leaving task/submission/source validation authoritative."""
    from .service import _RunViewReadRows, _validated_saved_research_result

    if not runs:
        return {}
    ids = tuple(run.run_id for run in runs)
    if len(ids) > 8 or len(set(ids)) != len(ids):
        raise ValueError("research_recent_read_scope_invalid")
    results = {row.run_id: row for row in session.scalars(
        select(ResearchResultRecord).where(ResearchResultRecord.run_id.in_(ids))
    ).all()}
    saved = {key: _validated_saved_research_result(results.get(key), key) for key in ids}
    short = tuple(run.run_id for run in runs if saved[run.run_id].result is not None
                  and run.artifact_id and run.status in {"completed", "degraded"}
                  and not run.error_code)
    full = tuple(key for key in ids if key not in short)
    traces = {key: [] for key in ids}
    if full:
        for row in session.scalars(select(ResearchTraceRecord)
                                  .where(ResearchTraceRecord.run_id.in_(full))
                                  .order_by(ResearchTraceRecord.run_id,
                                            ResearchTraceRecord.sequence_no.asc())).all():
            traces[row.run_id].append(row)
    if short:
        latest = (select(ResearchTraceRecord.run_id,
                         func.max(ResearchTraceRecord.sequence_no).label("sequence_no"))
                  .where(ResearchTraceRecord.run_id.in_(short))
                  .group_by(ResearchTraceRecord.run_id).subquery())
        for row in session.scalars(select(ResearchTraceRecord).join(latest, and_(
            ResearchTraceRecord.run_id == latest.c.run_id,
            ResearchTraceRecord.sequence_no == latest.c.sequence_no,
        ))).all():
            traces[row.run_id].append(row)
    events = tuple({run.event_id for run in runs})
    first = (select(ObservationRecord, func.row_number().over(
        partition_by=ObservationRecord.event_id, order_by=ObservationRecord.received_at.asc(),
    ).label("recent_observation_rank"))
             .where(ObservationRecord.event_id.in_(events)).subquery())
    original_observation = aliased(ObservationRecord, first)
    observations = {row.event_id: row for row in session.scalars(
        select(original_observation).where(first.c.recent_observation_rank == 1)
    ).all()}
    artifact_ids = tuple({run.artifact_id for run in runs if run.artifact_id})
    artifacts = {row.artifact_id: row for row in session.scalars(
        select(ArtifactRecord).options(load_only(ArtifactRecord.artifact_id, ArtifactRecord.gate_status))
        .where(ArtifactRecord.artifact_id.in_(artifact_ids))
    ).all()} if artifact_ids else {}
    fallback = tuple(key for key in ids if saved[key].result is None)
    evidence, facts = {key: [] for key in ids}, {key: [] for key in ids}
    if fallback:
        for row in session.scalars(select(ResearchEvidenceRecord)
                                  .where(ResearchEvidenceRecord.run_id.in_(fallback))
                                  .order_by(ResearchEvidenceRecord.run_id,
                                            ResearchEvidenceRecord.round,
                                            ResearchEvidenceRecord.evidence_id)).all():
            evidence[row.run_id].append(row)
        for row in session.scalars(select(ResearchFactRecord)
                                  .where(ResearchFactRecord.run_id.in_(fallback))
                                  .order_by(ResearchFactRecord.run_id,
                                            ResearchFactRecord.accepted_at,
                                            ResearchFactRecord.fact_id)).all():
            facts[row.run_id].append(row)
    links = {row.run_id: row for row in session.scalars(
        select(DshSessionLinkRecord).where(DshSessionLinkRecord.run_id.in_(ids))
    ).all()}
    return {run.run_id: _RunViewReadRows(
        run.run_id, saved[run.run_id], traces[run.run_id], observations.get(run.event_id),
        artifacts.get(run.artifact_id), evidence[run.run_id], facts[run.run_id],
        links.get(run.run_id),
    ) for run in runs}

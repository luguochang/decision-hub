from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable
from datetime import datetime
from pathlib import Path
from typing import Literal, TypeGuard, cast

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts import (
    CoverageAssessment,
    HorizonOutcomeView,
    ResearchSessionResult,
    ResearchValueEvaluation,
)
from packages.kernel.decision_hub_kernel.application.fact_store import ResearchFactStore
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchEvidenceService,
)
from packages.kernel.decision_hub_kernel.application.research_value_basis import (
    saved_coverage_basis,
)
from packages.kernel.decision_hub_kernel.decision.sufficiency import (
    assess_evidence_sufficiency,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    ArtifactRecord,
    Database,
    EvaluationRecord,
    EventWatchRecord,
    FeedbackRecord,
    ForecastRecord,
    ObservationRecord,
    OutcomeRecord,
    ReportRevisionRecord,
    ResearchEvidenceRecord,
    ResearchFactRecord,
    ResearchResultRecord,
    ResearchValueEvaluationRecord,
    RevisionFeedbackEvaluationRecord,
    RunRecord,
    as_utc,
    utcnow,
)

type CoverageBasisStatus = Literal[
    "saved_result",
    "saved_request",
    "saved_task_request",
    "saved_checkpoint",
    "round_request_only",
    "unavailable",
    "invalid",
]


def _is_coverage_basis(value: str) -> TypeGuard[CoverageBasisStatus]:
    return value in (
        "saved_result",
        "saved_request",
        "saved_task_request",
        "saved_checkpoint",
        "round_request_only",
        "unavailable",
        "invalid",
    )


def _required_run_timestamp(value: datetime | None) -> datetime:
    normalized = as_utc(value)
    if normalized is None:
        raise ValueError("research_run_timestamp_missing")
    return normalized


def _feedback_for_run(run_id: str):
    revisions = select(ReportRevisionRecord.revision_id).where(
        ReportRevisionRecord.source_run_id == run_id
    )
    return or_(
        and_(FeedbackRecord.target_type == "run", FeedbackRecord.target_id == run_id),
        and_(
            FeedbackRecord.target_type == "report_revision", FeedbackRecord.target_id.in_(revisions)
        ),
    )


class ResearchValueEvaluationService:
    """Persist immutable product-value snapshots without inventing trading labels."""

    def __init__(
        self,
        database: Database,
        *,
        pack_root: Path,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.database = database
        self.clock = clock
        # pack_root remains a compatible constructor argument. Historical value
        # evaluation must never read mutable Pack files from disk.

    def evaluate_current(self, run_id: str) -> ResearchValueEvaluation:
        with self.database.session() as session:
            run = session.get(RunRecord, run_id)
            if run is None or run.strategy_version != "research.v1":
                raise KeyError(run_id)
            outcome_count = (
                session.query(OutcomeRecord)
                .join(ForecastRecord, OutcomeRecord.forecast_id == ForecastRecord.forecast_id)
                .filter(ForecastRecord.artifact_id == run.artifact_id)
                .count()
                if run.artifact_id
                else 0
            )
            feedback_count = session.query(FeedbackRecord).filter(_feedback_for_run(run_id)).count()
            evidence_count = session.query(ResearchEvidenceRecord).filter_by(run_id=run_id).count()
            fact_count = session.query(ResearchFactRecord).filter_by(run_id=run_id).count()
            result_row = session.get(ResearchResultRecord, run_id)
            result_hash = result_row.payload_hash if result_row is not None else None
            watch = session.query(EventWatchRecord).filter_by(event_id=run.event_id).one_or_none()
            event_family = _event_family_for_run(session, run, watch)
            assessed_at = as_utc(run.finished_at or run.updated_at)
            basis_identity: dict[str, str | None]
            if result_row is not None:
                basis_identity = {"source": "saved_result", "hash": result_hash}
            else:
                basis = saved_coverage_basis(session, run, assessed_at)
                basis_identity = {"source": basis.status, "hash": basis.reference}
            ledger_hash = _ledger_hash([
                *session.query(ResearchEvidenceRecord).filter_by(run_id=run_id).all(),
                *session.query(ResearchFactRecord).filter_by(run_id=run_id).all(),
            ])
        version = _evaluation_version(
            outcome_count=outcome_count,
            feedback_count=feedback_count,
            evidence_count=evidence_count,
            fact_count=fact_count,
            result_hash=result_hash,
            event_family=event_family,
            basis_identity=basis_identity,
            assessed_at=assessed_at,
            ledger_hash=ledger_hash,
        )
        return self.evaluate(run_id, evaluation_version=version)

    def evaluate(
        self,
        run_id: str,
        *,
        evaluation_version: str,
    ) -> ResearchValueEvaluation:
        with self.database.session() as session:
            run = session.get(RunRecord, run_id)
            if run is None or run.strategy_version != "research.v1":
                raise KeyError(run_id)
            existing = (
                session.query(ResearchValueEvaluationRecord)
                .filter_by(run_id=run_id, evaluation_version=evaluation_version)
                .one_or_none()
            )
            if existing is not None:
                return ResearchValueEvaluation.model_validate_json(existing.payload_json)

            result_row = session.get(ResearchResultRecord, run_id)
            result = (
                ResearchSessionResult.model_validate_json(result_row.payload_json)
                if result_row is not None
                else None
            )
            evidence_rows = session.query(ResearchEvidenceRecord).filter_by(run_id=run_id).all()
            fact_rows = session.query(ResearchFactRecord).filter_by(run_id=run_id).all()
            assessed_at = _required_run_timestamp(run.finished_at or run.updated_at)
            watch = session.query(EventWatchRecord).filter_by(event_id=run.event_id).one_or_none()
            coverage_basis: CoverageBasisStatus
            if result is not None:
                if result_row is None:
                    raise ValueError("research_result_record_missing")
                coverage = result.final_coverage
                coverage_basis = "saved_result"
                basis_ref = result_row.payload_hash
                coverage_cutoff = None  # assessed_at alone does not establish its PIT cutoff.
            else:
                # A terminal failure can still have durable, attested partial
                # evidence. Recompute against that ledger instead of erasing
                # the useful progress from the value snapshot.
                basis = saved_coverage_basis(session, run, assessed_at)
                basis_status = basis.status
                if not _is_coverage_basis(basis_status):
                    raise ValueError("research_coverage_basis_invalid")
                coverage_basis, basis_ref = basis_status, basis.reference
                coverage_cutoff = basis.cutoff_at
                coverage = None
                if basis.request is not None:
                    if coverage_cutoff is None:
                        raise ValueError("research_coverage_cutoff_missing")
                    durable_evidence = ResearchEvidenceService(self.database).list_run_evidence(
                        run_id
                    )
                    durable_facts = ResearchFactStore(self.database).list_run_facts(run_id)
                    coverage = assess_evidence_sufficiency(
                        basis.request.evidence_requirements,
                        durable_evidence,
                        cutoff_at=coverage_cutoff,
                        assessed_at=assessed_at,
                        facts=durable_facts,
                    )
            artifact = session.get(ArtifactRecord, run.artifact_id) if run.artifact_id else None
            forecasts = (
                session.query(ForecastRecord)
                .filter_by(artifact_id=run.artifact_id)
                .order_by(ForecastRecord.expires_at, ForecastRecord.forecast_id)
                .all()
                if run.artifact_id
                else []
            )
            mode = _mode(run, artifact, forecasts)
            outcomes = [_outcome_view(session, item) for item in forecasts]
            feedback = (
                session.query(FeedbackRecord)
                .filter(_feedback_for_run(run_id))
                .order_by(FeedbackRecord.created_at.desc(), FeedbackRecord.feedback_id.desc())
                .first()
            )
            evaluated_at = self.clock()
            evaluation_id = _stable_evaluation_id(run_id, evaluation_version)
            payload = ResearchValueEvaluation(
                schema_version="research-value-evaluation.v1",
                evaluation_id=evaluation_id,
                run_id=run_id,
                artifact_id=run.artifact_id,
                evaluation_version=evaluation_version,
                mode=mode,
                coverage_basis=coverage_basis,
                coverage_basis_ref=basis_ref,
                coverage_cutoff_at=coverage_cutoff,
                hard_coverage_ratio=coverage.hard_coverage_ratio if coverage else None,
                soft_coverage_ratio=coverage.soft_coverage_ratio if coverage else None,
                accepted_evidence_count=sum(item.quality == "accepted" for item in evidence_rows),
                rejected_evidence_count=(
                    sum(
                        item.quality in {"rejected", "stale", "conflicted"}
                        for item in result.evidence_candidates
                    )
                    if result is not None
                    else 0
                ),
                typed_fact_count=len(fact_rows),
                baseline_status=cast(
                    Literal["not_applicable", "pending", "ready", "unavailable"],
                    watch.baseline_status if watch is not None else "not_applicable",
                ),
                latency_ms=run.latency_ms,
                cost_status="partial" if run.cost_usd is not None else "unknown",
                known_cost_usd=run.cost_usd,
                citation_traceability=_citation_traceability(artifact, evidence_rows),
                usefulness=cast(
                    Literal["useful", "partial", "not_useful", "unlabeled"],
                    _usefulness(feedback),
                ),
                terminal_reason=(result.stop_reason.code if result is not None else run.error_code),
                outcomes=outcomes,
                evaluated_at=evaluated_at,
            )
            canonical = json.dumps(
                payload.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            session.add(
                ResearchValueEvaluationRecord(
                    evaluation_id=evaluation_id,
                    run_id=run_id,
                    artifact_id=run.artifact_id,
                    evaluation_version=evaluation_version,
                    payload_json=canonical,
                    payload_hash=hashlib.sha256(canonical.encode()).hexdigest(),
                    evaluated_at=evaluated_at,
                )
            )
            session.flush()
            return payload

    @staticmethod
    def append_revision_in_session(
        session: Session,
        *,
        run: RunRecord,
        artifact: ArtifactRecord,
        result: ResearchSessionResult | None,
        feedback: FeedbackRecord,
        coverage: CoverageAssessment | None,
        coverage_basis: str,
        coverage_basis_ref: str | None,
        coverage_cutoff_at: datetime | None,
        evidence_rows: list[ResearchEvidenceRecord],
        fact_rows: list[ResearchFactRecord],
        forecasts: list[ForecastRecord],
        watch: EventWatchRecord | None,
        evaluation_version: str,
        evaluated_at: datetime,
    ) -> ResearchValueEvaluation:
        """Project validated frozen inputs in the caller's physical writer.

        The binding service selects the exact feedback and validates source
        uniqueness before this projection. No latest lookup or owned Session.
        """
        evaluation_id = _stable_evaluation_id(run.run_id, evaluation_version)
        if session.get(ResearchValueEvaluationRecord, evaluation_id) is not None:
            raise ValueError("revision_feedback_evaluation_value_collision")
        payload = ResearchValueEvaluation.model_validate({
            "schema_version": "research-value-evaluation.v1",
            "evaluation_id": evaluation_id,
            "run_id": run.run_id,
            "artifact_id": artifact.artifact_id,
            "evaluation_version": evaluation_version,
            "mode": _mode(run, artifact, forecasts),
            "coverage_basis": coverage_basis,
            "coverage_basis_ref": coverage_basis_ref,
            "coverage_cutoff_at": coverage_cutoff_at,
            "hard_coverage_ratio": coverage.hard_coverage_ratio if coverage else None,
            "soft_coverage_ratio": coverage.soft_coverage_ratio if coverage else None,
            "accepted_evidence_count": sum(row.quality == "accepted" for row in evidence_rows),
            "rejected_evidence_count": (
                sum(row.quality in {"rejected", "stale", "conflicted"}
                    for row in result.evidence_candidates) if result is not None else 0
            ),
            "typed_fact_count": len(fact_rows),
            "baseline_status": cast(
                Literal["not_applicable", "pending", "ready", "unavailable"],
                watch.baseline_status if watch is not None else "not_applicable",
            ),
            "latency_ms": run.latency_ms,
            "cost_status": "partial" if run.cost_usd is not None else "unknown",
            "known_cost_usd": run.cost_usd,
            "citation_traceability": _citation_traceability(artifact, evidence_rows),
            "usefulness": cast(
                Literal["useful", "partial", "not_useful", "unlabeled"], _usefulness(feedback)
            ),
            "terminal_reason": result.stop_reason.code if result is not None else run.error_code,
            "outcomes": [_outcome_view(session, forecast) for forecast in forecasts],
            "evaluated_at": evaluated_at,
        })
        canonical = json.dumps(payload.model_dump(mode="json"), ensure_ascii=False,
                               sort_keys=True, separators=(",", ":"), allow_nan=False)
        session.add(ResearchValueEvaluationRecord(
            evaluation_id=evaluation_id, run_id=run.run_id, artifact_id=artifact.artifact_id,
            evaluation_version=evaluation_version, payload_json=canonical,
            payload_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            evaluated_at=evaluated_at,
        ))
        session.flush()
        return payload

    def latest(self, run_id: str) -> ResearchValueEvaluation | None:
        with self.database.session() as session:
            row = (
                session.query(ResearchValueEvaluationRecord)
                .outerjoin(
                    RevisionFeedbackEvaluationRecord,
                    RevisionFeedbackEvaluationRecord.evaluation_id
                    == ResearchValueEvaluationRecord.evaluation_id,
                )
                .filter(ResearchValueEvaluationRecord.run_id == run_id)
                .order_by(
                    ResearchValueEvaluationRecord.evaluated_at.desc(),
                    func.coalesce(RevisionFeedbackEvaluationRecord.binding_seq, 0).desc(),
                    ResearchValueEvaluationRecord.evaluation_id.desc(),
                )
                .first()
            )
            return (
                ResearchValueEvaluation.model_validate_json(row.payload_json)
                if row is not None
                else None
            )


def _mode(
    run: RunRecord,
    artifact: ArtifactRecord | None,
    forecasts: list[ForecastRecord],
) -> Literal["directional", "research_only", "failed"]:
    if run.status == "failed" and artifact is None:
        return "failed"
    if artifact is None or artifact.gate_status in {"research_only", "reject"}:
        return "research_only"
    has_direction = any(item.direction in {"long", "short"} for item in forecasts)
    return "directional" if has_direction else "research_only"


def _event_family_for_run(
    session: Session,
    run: RunRecord,
    watch: EventWatchRecord | None,
) -> str | None:
    if watch is not None:
        return watch.event_family
    observation = (
        session.query(ObservationRecord)
        .filter_by(event_id=run.event_id)
        .order_by(ObservationRecord.received_at.asc(), ObservationRecord.observation_id.asc())
        .first()
    )
    return observation.event_hint if observation is not None else None


def _outcome_view(session: Session, forecast: ForecastRecord) -> HorizonOutcomeView:
    outcome = session.query(OutcomeRecord).filter_by(forecast_id=forecast.forecast_id).first()
    evaluation = session.query(EvaluationRecord).filter_by(forecast_id=forecast.forecast_id).first()
    if forecast.direction not in {"long", "short"}:
        reason = "direction_not_applicable"
    elif outcome is None:
        reason = "outcome_pending"
    else:
        reason = "market_path_unavailable"
    return HorizonOutcomeView(
        forecast_id=forecast.forecast_id,
        horizon=cast(Literal["30m", "24h", "72h"], forecast.horizon),
        direction=cast(Literal["long", "short", "neutral", "no_trade"], forecast.direction),
        quality_status=(outcome.quality_status if outcome is not None else "unavailable"),
        return_pct=(outcome.return_pct if outcome is not None else None),
        net_return_pct=(evaluation.net_return_pct if evaluation is not None else None),
        brier_score=(evaluation.brier_score if evaluation is not None else None),
        direction_correct=(evaluation.direction_correct if evaluation is not None else None),
        mfe_pct=None,
        mae_pct=None,
        unavailable_reason=reason,
        observed_at=(as_utc(outcome.observed_at) if outcome is not None else None),
    )


def _citation_traceability(
    artifact: ArtifactRecord | None,
    evidence: list[ResearchEvidenceRecord],
) -> float:
    if artifact is None or not evidence:
        return 0.0
    payload = json.loads(artifact.payload_json)
    citations = {str(item).strip() for item in payload.get("citations", []) if str(item).strip()}
    if not citations:
        return 0.0
    eligible = [item for item in evidence if item.quality == "accepted"]
    if not eligible:
        return 0.0
    traceable_refs = {
        ref
        for item in eligible
        for ref in (item.evidence_id, item.content_ref, item.source_url, item.source_id)
        if ref
    }
    matched = sum(item in traceable_refs for item in citations)
    return min(1.0, matched / max(1, len(eligible)))


def _usefulness(feedback: FeedbackRecord | None) -> str:
    if feedback is None:
        return "unlabeled"
    return {
        "useful": "useful",
        "not_useful": "not_useful",
        "incorrect": "not_useful",
        "needs_review": "partial",
    }.get(feedback.verdict, "unlabeled")


def _stable_evaluation_id(run_id: str, version: str) -> str:
    digest = hashlib.sha256(f"{run_id}:{version}".encode()).hexdigest()[:32]
    return f"research_eval_{digest}"


def _evaluation_version(
    *,
    outcome_count: int,
    feedback_count: int,
    evidence_count: int,
    fact_count: int,
    result_hash: str | None,
    event_family: str | None,
    basis_identity: dict[str, str | None],
    assessed_at: datetime,
    ledger_hash: str,
) -> str:
    semantic = json.dumps(
        {
            "evidence_count": evidence_count,
            "fact_count": fact_count,
            "feedback_count": feedback_count,
            "event_family": event_family,
            "outcome_count": outcome_count,
            "result_hash": result_hash,
            "basis": basis_identity,
            "assessed_at": assessed_at.isoformat(),
            "ledger_hash": ledger_hash,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return f"research-value.v4:{hashlib.sha256(semantic).hexdigest()}"


def _ledger_hash(rows: Iterable[ResearchEvidenceRecord | ResearchFactRecord]) -> str:
    payloads = [
        {
            "table": row.__tablename__,
            **{column.name: getattr(row, column.name) for column in row.__table__.columns},
        }
        for row in rows
    ]
    encoded = sorted(json.dumps(item, sort_keys=True, default=str) for item in payloads)
    return hashlib.sha256(json.dumps(encoded).encode()).hexdigest()

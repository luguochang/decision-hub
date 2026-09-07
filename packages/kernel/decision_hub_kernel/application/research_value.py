from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Literal, cast

from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts import (
    HorizonOutcomeView,
    ResearchSessionResult,
    ResearchValueEvaluation,
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
    OutcomeRecord,
    ResearchEvidenceRecord,
    ResearchFactRecord,
    ResearchResultRecord,
    ResearchValueEvaluationRecord,
    RunRecord,
    as_utc,
    utcnow,
)
from packages.provider_adapters.research import CryptoMacroFactPack


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
        self.requirements = CryptoMacroFactPack.from_pack(
            pack_root
        ).all_contract_requirements()

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
            feedback_count = (
                session.query(FeedbackRecord)
                .filter_by(target_type="run", target_id=run_id)
                .count()
            )
        version = f"research-value.v1:o{outcome_count}:f{feedback_count}"
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
            evidence_rows = (
                session.query(ResearchEvidenceRecord).filter_by(run_id=run_id).all()
            )
            fact_rows = session.query(ResearchFactRecord).filter_by(run_id=run_id).all()
            assessed_at = as_utc(run.finished_at or run.updated_at)
            if assessed_at is None:
                raise ValueError("research_run_timestamp_missing")
            coverage = (
                result.final_coverage
                if result is not None
                else assess_evidence_sufficiency(
                    self.requirements,
                    [],
                    cutoff_at=assessed_at,
                    assessed_at=assessed_at,
                    facts=[],
                )
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
            watch = (
                session.query(EventWatchRecord).filter_by(event_id=run.event_id).one_or_none()
            )
            feedback = (
                session.query(FeedbackRecord)
                .filter_by(target_type="run", target_id=run_id)
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
                hard_coverage_ratio=coverage.hard_coverage_ratio,
                soft_coverage_ratio=coverage.soft_coverage_ratio,
                accepted_evidence_count=sum(
                    item.quality in {"candidate", "accepted"} for item in evidence_rows
                ),
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
                terminal_reason=(
                    result.stop_reason.code if result is not None else run.error_code
                ),
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

    def latest(self, run_id: str) -> ResearchValueEvaluation | None:
        with self.database.session() as session:
            row = (
                session.query(ResearchValueEvaluationRecord)
                .filter_by(run_id=run_id)
                .order_by(
                    ResearchValueEvaluationRecord.evaluated_at.desc(),
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


def _outcome_view(session: Session, forecast: ForecastRecord) -> HorizonOutcomeView:
    outcome = session.query(OutcomeRecord).filter_by(forecast_id=forecast.forecast_id).first()
    evaluation = (
        session.query(EvaluationRecord).filter_by(forecast_id=forecast.forecast_id).first()
    )
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
    source_urls = {item.source_url for item in evidence if item.source_url}
    source_ids = {item.source_id for item in evidence}
    matched = sum(item in source_urls or item in source_ids for item in citations)
    return min(1.0, matched / max(1, len(evidence)))


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

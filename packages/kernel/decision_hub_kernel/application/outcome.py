from __future__ import annotations

import uuid

from packages.contracts_py.decision_hub_contracts.models import EvaluationView, OutcomeCreate
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    EvaluationRecord,
    ForecastRecord,
    OutcomeRecord,
    utcnow,
)


class OutcomeService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def record(self, request: OutcomeCreate) -> EvaluationView:
        with self.database.session() as session:
            forecast = session.get(ForecastRecord, request.forecast_id)
            if not forecast:
                raise KeyError(request.forecast_id)
            existing_outcome = (
                session.query(OutcomeRecord).filter_by(forecast_id=request.forecast_id).first()
            )
            if existing_outcome:
                existing_evaluation = (
                    session.query(EvaluationRecord)
                    .filter_by(forecast_id=request.forecast_id)
                    .first()
                )
                if existing_evaluation:
                    return EvaluationView(
                        evaluation_id=existing_evaluation.evaluation_id,
                        forecast_id=existing_evaluation.forecast_id,
                        brier_score=existing_evaluation.brier_score,
                        net_return_pct=existing_evaluation.net_return_pct,
                        direction_correct=existing_evaluation.direction_correct,
                        label_status=existing_evaluation.label_status,
                        evaluated_at=existing_evaluation.evaluated_at,
                    )
            evaluation_id = f"eval_{uuid.uuid4().hex}"
            observed_at = utcnow()
            net_return = request.return_pct - request.fees - request.slippage
            brier = (forecast.probability - (1 if request.direction_correct else 0)) ** 2
            session.add(
                OutcomeRecord(
                    outcome_id=f"out_{uuid.uuid4().hex}",
                    forecast_id=request.forecast_id,
                    observed_at=observed_at,
                    direction_correct=request.direction_correct,
                    return_pct=request.return_pct,
                    fees=request.fees,
                    slippage=request.slippage,
                    quality_status=request.quality_status,
                )
            )
            session.add(
                EvaluationRecord(
                    evaluation_id=evaluation_id,
                    forecast_id=request.forecast_id,
                    brier_score=brier,
                    net_return_pct=net_return,
                    direction_correct=request.direction_correct,
                    label_status=request.quality_status,
                    evaluated_at=observed_at,
                )
            )
        return EvaluationView(
            evaluation_id=evaluation_id,
            forecast_id=request.forecast_id,
            brier_score=brier,
            net_return_pct=net_return,
            direction_correct=request.direction_correct,
            label_status=request.quality_status,
            evaluated_at=observed_at,
        )

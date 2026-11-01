from __future__ import annotations

import json
from typing import Literal

from packages.contracts_py.decision_hub_contracts import ForecastAuditView
from packages.contracts_py.decision_hub_contracts.models import Direction
from packages.kernel.decision_hub_kernel.persistence.db import (
    ArtifactRecord,
    Database,
    ForecastRecord,
    OutcomeRecord,
    RunRecord,
    as_utc,
)


def forecast_audit_for_run(database: Database, run_id: str) -> list[ForecastAuditView]:
    with database.session() as session:
        run = session.get(RunRecord, run_id)
        if run is None:
            raise KeyError(run_id)
        forecasts = (
            session.query(ForecastRecord)
            .join(ArtifactRecord, ArtifactRecord.artifact_id == ForecastRecord.artifact_id)
            .filter(ArtifactRecord.run_id == run_id)
            .order_by(ForecastRecord.expires_at.asc(), ForecastRecord.forecast_id.asc())
            .all()
        )
        result: list[ForecastAuditView] = []
        for forecast in forecasts:
            outcome = (
                session.query(OutcomeRecord).filter_by(forecast_id=forecast.forecast_id).first()
            )
            features = _feature_snapshot(forecast.feature_snapshot_json)
            trailing = _trailing_direction(features)
            same_as_trailing = _same_as_trailing(forecast.direction, trailing)
            forecasted_at = as_utc(forecast.forecasted_at)
            observed_at = as_utc(outcome.observed_at) if outcome is not None else None
            cutoff_at = as_utc(forecast.evidence_cutoff_at)
            result.append(
                ForecastAuditView(
                    forecast_id=forecast.forecast_id,
                    horizon=forecast.horizon,
                    forecast_direction=Direction(forecast.direction),
                    strategy_version=forecast.strategy_version,
                    forecasted_at=forecasted_at,
                    as_of=forecast.as_of,
                    input_snapshot_id=forecast.input_snapshot_id,
                    input_fingerprint=forecast.input_fingerprint,
                    evidence_cutoff_at=cutoff_at,
                    feature_snapshot=features,
                    trailing_direction=trailing,
                    same_as_trailing_trend=same_as_trailing,
                    forecasted_before_outcome=(
                        forecasted_at < observed_at
                        if forecasted_at is not None and observed_at is not None
                        else None
                    ),
                    immutable_snapshot=(
                        bool(forecast.input_fingerprint)
                        and forecasted_at is not None
                        and cutoff_at is not None
                        and cutoff_at <= forecasted_at
                    ),
                )
            )
        return result


def _feature_snapshot(raw: str | None) -> dict[str, float]:
    if raw is None:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(value, dict):
        return {}
    return {
        str(key): float(item)
        for key, item in value.items()
        if isinstance(item, (int, float)) and not isinstance(item, bool)
    }


def _trailing_direction(
    features: dict[str, float],
) -> Literal["bullish", "bearish", "flat", "unavailable"]:
    trailing_return = features.get("return_1d_pct")
    if trailing_return is None:
        trailing_return = features.get("return_short_pct")
    if trailing_return is None:
        return "unavailable"
    if trailing_return > 0:
        return "bullish"
    if trailing_return < 0:
        return "bearish"
    return "flat"


def _same_as_trailing(direction: str, trailing: str) -> bool | None:
    if direction not in {"long", "short"} or trailing not in {"bullish", "bearish"}:
        return None
    return (direction == "long" and trailing == "bullish") or (
        direction == "short" and trailing == "bearish"
    )

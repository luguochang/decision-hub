"""Versioned direction scores; observation facts never confer trading authority."""

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from decimal import Decimal, localcontext
from typing import Any

from .market_path import aware_utc
from .perpetual_forecast_experience import score_direction

SCHEMA = "perpetual-forecast-experience.v2"
ROLE = "perpetual_forecast_experience_v2"
SOURCE = "perpetual_forecast_experience.v2"
METHOD = {
    "version": "perpetual-direction-observation.v2",
    "return": "end_close/start_close-1",
    "brier": "sum((probability-onehot)**2);not_divided_by_class_count",
    "log_loss": "-ln(probability_of_observed_outcome);zero_is_explicit_infinite;no_clipping",
    "reliability": "one_prediction_and_onehot_per_outcome;not_a_calibration_claim",
    "precision": 50,
    "price": "confirmed_swap_trade_1m_close;open_ms+60000",
    "coverage": "all_frozen_window_boundary_closes;minutes+1",
    "paging": "exclusive_older_open_ms;limit300;horizon_bound_1_5_15;no_padding",
    "truth_adapter": "okx-perp-forecast-truth.v1",
    "financial_qualified": False,
}
HORIZON_MINUTES = {"4h": 240, "24h": 1440, "72h": 4320}


def horizon_policy(horizon: str) -> dict[str, int]:
    if horizon not in HORIZON_MINUTES:
        raise ValueError("perpetual_scoring_horizon_unsupported")
    minutes = HORIZON_MINUTES[horizon]
    return {"minutes": minutes, "required_closes": minutes + 1, "max_pages": (minutes + 300) // 300}


def expected_closes(start: datetime, end: datetime, horizon: str) -> tuple[datetime, ...]:
    policy = horizon_policy(horizon)
    start, end = aware_utc(start), aware_utc(end)
    if (
        start.second
        or start.microsecond
        or end.second
        or end.microsecond
        or end - start != timedelta(minutes=policy["minutes"])
    ):
        raise ValueError("perpetual_scoring_frozen_window_invalid")
    return tuple(start + timedelta(minutes=index) for index in range(policy["required_closes"]))


def score_forecast(
    probabilities: Sequence[Mapping[str, str]] | None,
    category: str,
) -> dict[str, Any]:
    brier = score_direction(probabilities, category)
    if probabilities is None:
        return {
            "multiclass_brier": None,
            "log_loss": {
                "status": "unavailable",
                "value": None,
                "reason": "direction_probabilities_unavailable",
            },
            "reliability_observations": [],
        }
    by_outcome = {row["outcome_id"]: row["value"] for row in probabilities}
    probability = Decimal(by_outcome[category])
    with localcontext() as context:
        context.prec = 50
        log_loss = (
            {"status": "infinite", "value": None, "reason": "observed_outcome_probability_zero"}
            if probability == 0
            else {"status": "finite", "value": str(-probability.ln()), "reason": None}
        )
    return {
        "multiclass_brier": brier,
        "log_loss": log_loss,
        "reliability_observations": [
            {
                "outcome_id": outcome,
                "probability": by_outcome[outcome],
                "observed": int(outcome == category),
            }
            for outcome in ("up", "neutral", "down")
        ],
    }

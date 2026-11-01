"""Private immutable forecast experience; no trade, control or skill authority."""

import json
from dataclasses import dataclass
from decimal import Decimal, localcontext

from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
    DecisionSourceRefV1,
)

from .market_path import canonical_hash

SCHEMA = "perpetual-forecast-experience.v1"
ROLE = "perpetual_forecast_experience_v1"
METHOD = {
    "version": "perpetual-direction-observation.v1",
    "spec": "02:5.2,10;08:7,10",
    "return": "end_close/start_close-1",
    "up": "return>frozen_threshold",
    "down": "return<-frozen_threshold",
    "neutral": "inclusive_between_thresholds",
    "brier": "sum((probability-onehot)**2);not_divided_by_class_count",
    "precision": 50,
    "price": "confirmed_swap_trade_1m_close;open_ms+60000",
    "coverage": "241_exact_consecutive_boundary_closes_for_4h",
    "financial_qualified": False,
}


def classify_forecast_return(start_price: str, end_price: str, threshold: str):
    values = tuple(Decimal(item) for item in (start_price, end_price, threshold))
    if any(not item.is_finite() or item <= 0 for item in values):
        raise ValueError("perpetual_experience_positive_price_threshold_required")
    with localcontext() as context:
        context.prec = 50
        result = values[1] / values[0] - 1
        category = "up" if result > values[2] else "down" if result < -values[2] else "neutral"
        return str(result), category


def score_direction(probabilities, category: str) -> str | None:
    if category not in {"up", "neutral", "down"}:
        raise ValueError("perpetual_experience_category_invalid")
    if probabilities is None:
        return None
    if len(probabilities) != 3 or {row["outcome_id"] for row in probabilities} != {
        "up",
        "neutral",
        "down",
    }:
        raise ValueError("perpetual_experience_probability_partition_invalid")
    values = {row["outcome_id"]: Decimal(row["value"]) for row in probabilities}
    if (
        any(not value.is_finite() or not 0 <= value <= 1 for value in values.values())
        or sum(values.values()) != 1
    ):
        raise ValueError("perpetual_experience_probability_partition_invalid")
    with localcontext() as context:
        context.prec = 50
        return str(
            sum(((value - int(key == category)) ** 2 for key, value in values.items()), Decimal(0))
        )


@dataclass(frozen=True)
class ForecastExperienceOriginal:
    ref: DecisionSourceRefV1
    body_json: str

    @property
    def body(self) -> dict:
        return json.loads(self.body_json)


def experience_original(body: dict) -> ForecastExperienceOriginal:
    digest = canonical_hash(body)
    return ForecastExperienceOriginal(
        DecisionSourceRefV1(ref="perp_experience_" + digest, hash=digest),
        json.dumps(
            body, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ),
    )

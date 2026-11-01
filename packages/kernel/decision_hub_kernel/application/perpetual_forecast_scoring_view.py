"""V2 score projection after the original owner/publication reader authenticates."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
    DecisionSourceRefV1,
    PublicationBoundTargetV1,
)

from ..ports.market_path import aware_utc
from .perpetual_forecast_result_view import ObservedForecastResult
from .perpetual_forecast_scoring import read_scoring_experience_in_session


class LogLoss(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["finite", "infinite", "unavailable"]
    value: str | None
    reason: (
        Literal["observed_outcome_probability_zero", "direction_probabilities_unavailable"] | None
    )

    @model_validator(mode="after")
    def exact_state(self):
        if (self.status == "finite") != (self.value is not None):
            raise ValueError("perpetual_scoring_log_loss_state_invalid")
        reason = {
            "finite": None,
            "infinite": "observed_outcome_probability_zero",
            "unavailable": "direction_probabilities_unavailable",
        }[self.status]
        if self.reason != reason:
            raise ValueError("perpetual_scoring_log_loss_state_invalid")
        return self


class ReliabilityObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outcome_id: Literal["up", "neutral", "down"]
    probability: str
    observed: Literal[0, 1]


class ScoreStrata(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asset: Literal["BTC", "ETH"]
    horizon: Literal["4h", "24h", "72h"]
    event_family: str | None
    market_regime: str | None


class BaseRateComparison(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_brier: str | None
    base_rate_brier: str | None
    brier_skill: str | None
    reason: Literal["base_rate_unavailable", "direction_probabilities_unavailable"] | None


class ObservedScoringResult(ObservedForecastResult):
    log_loss: LogLoss
    reliability_observations: list[ReliabilityObservation]
    strata: ScoreStrata
    # Read-time comparison with the admission's frozen base rate; None for runs
    # admitted before the base rate existed. Never part of the experience hash.
    base_rate_comparison: BaseRateComparison | None = None


class PerpetualForecastScoringView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["perpetual-forecast-result-view.v2"] = (
        "perpetual-forecast-result-view.v2"
    )
    status: Literal["not_due", "pending", "observed"]
    reason_code: Literal["forecast_target_not_due", "forecast_result_not_recorded"] | None
    owner_id: str
    run_id: str
    instrument_id: str
    horizon: Literal["4h", "24h", "72h"]
    read_at: datetime
    admission_ref: DecisionSourceRefV1
    target_ref: DecisionSourceRefV1
    target: dict[str, Any]
    result: ObservedScoringResult | None

    @field_validator("target")
    @classmethod
    def valid_target(cls, value: dict[str, Any]) -> dict[str, Any]:
        PublicationBoundTargetV1.model_validate(value)
        return value

    @model_validator(mode="after")
    def exact_state(self):
        reason = {
            "not_due": "forecast_target_not_due",
            "pending": "forecast_result_not_recorded",
            "observed": None,
        }[self.status]
        if self.reason_code != reason or (self.status == "observed") != (self.result is not None):
            raise ValueError("perpetual_scoring_result_state_invalid")
        return self


def _base_rate_comparison(session: Any, body: dict[str, Any]) -> dict[str, Any] | None:
    from ..ports.perpetual_base_rate import score_against_base_rate
    from .perpetual_research_binding import read_perpetual_admission_identity_in_session

    identity = read_perpetual_admission_identity_in_session(session, body["run_id"])
    frozen = None if identity is None else identity[0].get("direction_base_rate")
    if frozen is None:
        return None
    return score_against_base_rate(
        body["direction_estimate"]["probabilities"],
        frozen.get("window_90d") if frozen.get("status") == "available" else None,
        body["category"],
    )


def read_scoring_view(
    session: Any,
    *,
    base: dict[str, Any],
    publication: Any,
    row: Any,
    at: datetime,
) -> dict[str, Any]:
    base = {**base, "schema_version": "perpetual-forecast-result-view.v2"}
    target = PublicationBoundTargetV1.model_validate(publication["target"])
    if at < aware_utc(target.window_end):
        return PerpetualForecastScoringView(
            **base, status="not_due", reason_code="forecast_target_not_due", result=None
        ).model_dump(mode="json")
    if row is None:
        return PerpetualForecastScoringView(
            **base, status="pending", reason_code="forecast_result_not_recorded", result=None
        ).model_dump(mode="json")
    original = read_scoring_experience_in_session(
        session, {"ref": row.output_id, "hash": row.payload_hash}, cutoff_at=at
    )
    body, bindings = original.body, publication["snapshot"]["bindings"]
    expected = {
        "owner_id": base["owner_id"],
        "run_id": base["run_id"],
        "instrument_id": base["instrument_id"],
        "horizon": base["horizon"],
        "target": publication["target"],
        "snapshot_ref": {
            "ref": publication["snapshot"]["id"],
            "hash": publication["snapshot"]["snapshot_hash"],
        },
        "final_target_ref": bindings["final_target"],
        "frozen_forecast_ref": bindings["frozen_forecast"],
        "market_distribution_ref": publication["frozen_forecast"]["market_distribution_ref"],
        "method_ref": publication["target"]["method_ref"],
        "target_family_ref": publication["target"]["target_family_ref"],
        "label_rule_ref": publication["target"]["label_rule_ref"],
        "direction_estimate": publication["market_distribution"]["direction_distribution"],
    }
    if any(body.get(key) != value for key, value in expected.items()):
        raise ValueError("perpetual_forecast_result_binding_mismatch")
    fields = (
        "snapshot_ref",
        "final_target_ref",
        "frozen_forecast_ref",
        "market_distribution_ref",
        "method_ref",
        "target_family_ref",
        "label_rule_ref",
        "direction_estimate",
        "multiclass_brier",
        "known_at",
        "recorded_at",
        "gaps",
        "calculation_method_hash",
        "qualification",
        "financial_qualified",
        "skill_consumed",
        "trade_outcome",
        "log_loss",
        "reliability_observations",
        "strata",
    )
    result = ObservedScoringResult(
        experience_ref=original.ref,
        actual_category=body["category"],
        actual_return_fraction=body["return_fraction"],
        base_rate_comparison=_base_rate_comparison(session, body),
        **{key: body[key] for key in fields},
    )
    return PerpetualForecastScoringView(
        **base, status="observed", reason_code=None, result=result
    ).model_dump(mode="json")

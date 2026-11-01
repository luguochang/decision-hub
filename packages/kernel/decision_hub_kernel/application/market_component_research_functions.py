"""Installed JSON functions for fixed path forecasts and prepared spot configuration.

These functions perform no IO and never grant model or trading qualification.
Original authentication and installed-function selection belong to their callers.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Literal

from pydantic import AwareDatetime, Field, model_validator

from packages.contracts_py.decision_hub_contracts.generated.research_binding import (
    ResearchPackDefinitionV2,
)

from ..decision.path_forecast import PARAMETERS, predict_path_forecast, verify_path_forecast
from ..ports.market_path import FrozenContract, canonical_hash, spot_scope
from ..ports.path_forecast import FrozenPathForecastModel, RiskPathForecast
from .market_features import MarketFeatureSnapshot
from .spot_scope_admission import EffectiveSpotRecipe

PATH_COMPONENT_INPUT = "path-forecast-consumption-input.v1"
PATH_COMPONENT_OUTPUT = "risk-path-forecast.v1"
RECIPE_COMPONENT_INPUT = "research-spot-recipe-consumption-input.v1"
RECIPE_COMPONENT_OUTPUT = "research-prepared-pack-config.v1"
RECIPE_COMPONENT_REF = "spot.research_recipe.v3"


class PathForecastConsumptionInput(FrozenContract):
    schema_version: Literal["path-forecast-consumption-input.v1"] = PATH_COMPONENT_INPUT
    trial_id: str = Field(min_length=1)
    job_id: str = Field(min_length=1)
    scope_id: str = Field(min_length=1)
    model_version: Literal["BASE_EMPIRICAL_V1", "COND_VOL_V1"]
    model: FrozenPathForecastModel
    cutoff_at: AwareDatetime
    published_at: AwareDatetime
    features: MarketFeatureSnapshot | None
    feature_manifest_hash: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def scope_boundary(self) -> PathForecastConsumptionInput:
        if self.scope_id != self.model.scope.scope_id:
            raise ValueError("component_path_forecast_input_scope_mismatch")
        return self


class SpotResearchRecipeConsumptionInput(FrozenContract):
    schema_version: Literal["research-spot-recipe-consumption-input.v1"] = RECIPE_COMPONENT_INPUT
    run_id: str = Field(min_length=1)
    event_id: str = Field(min_length=1)
    scope_id: str = Field(min_length=1)
    admission_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    effective_binding_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    effective: EffectiveSpotRecipe
    definition: ResearchPackDefinitionV2
    definition_hash: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def original_configuration(self) -> SpotResearchRecipeConsumptionInput:
        if (
            canonical_hash(self.definition.model_dump(mode="json")) != self.definition_hash
            or canonical_hash(self.effective.model_dump(mode="json")) != self.effective_binding_hash
            or spot_scope(self.effective.instrument).scope_id != self.scope_id
        ):
            raise ValueError("component_spot_recipe_input_original_mismatch")
        return self


def predict_registered_path_forecast(
    payload: dict[str, Any], parameters: dict[str, Any] | None = None
) -> dict[str, Any]:
    if parameters not in (None, {}, PARAMETERS.model_dump(mode="json")):
        raise ValueError("component_path_forecast_parameters_not_supported")
    inputs = PathForecastConsumptionInput.model_validate(payload)
    return predict_path_forecast(
        inputs.model,
        cutoff_at=inputs.cutoff_at,
        published_at=inputs.published_at,
        features=inputs.features,
        model_version=inputs.model_version,
    ).model_dump(mode="json")


def select_registered_spot_research_recipe(
    payload: dict[str, Any], parameters: dict[str, Any] | None = None
) -> dict[str, Any]:
    if parameters not in (None, {}):
        raise ValueError("component_spot_recipe_parameters_not_supported")
    inputs = SpotResearchRecipeConsumptionInput.model_validate(payload)
    # Only return this installed, authenticated configuration. No arbitrary
    # registry text becomes Python, a pack path, an expanded permission or IO.
    return inputs.definition.model_dump(mode="json")


def verify_consumed_path_forecast(
    value: object, inputs: PathForecastConsumptionInput
) -> RiskPathForecast:
    result = RiskPathForecast.model_validate(value)
    verify_path_forecast(result)
    features = inputs.features
    if (
        result.scope,
        result.model_version,
        result.model_hash,
        result.parameter_hash,
        result.feature_hash,
        result.cutoff_at,
        result.published_at,
        result.window_start_at,
        result.window_end_at,
    ) != (
        inputs.model.scope,
        inputs.model_version,
        inputs.model.model_hash,
        canonical_hash(PARAMETERS.model_dump(mode="json")),
        canonical_hash(features.model_dump(mode="json")) if features is not None else None,
        inputs.cutoff_at,
        inputs.published_at,
        inputs.cutoff_at + timedelta(minutes=1),
        inputs.cutoff_at + timedelta(days=1, minutes=1),
    ):
        raise ValueError("component_path_forecast_output_identity_mismatch")
    return result

"""Installed pure DTO functions used by the component registry's actual consumers."""

from decimal import Decimal
from typing import Any, Literal

from pydantic import AwareDatetime, Field

from ..ports.market_path import FrozenContract, MarketCandle, MarketPathScope, MarketPricePath
from ..ports.risk_monitor import RiskConditionV1, RiskMonitorInput
from .market_features import build_market_features
from .risk_monitor import evaluate_condition


class PathComponentInput(FrozenContract):
    schema_version: Literal["market-path-component-input.v1"] = "market-path-component-input.v1"
    path: MarketPricePath


class FeatureComponentInput(FrozenContract):
    schema_version: Literal["market-feature-component-input.v1"] = (
        "market-feature-component-input.v1"
    )
    scope: MarketPathScope
    candles: tuple[MarketCandle, ...]
    as_of: AwareDatetime
    stale_after_seconds: int = Field(ge=60)


class RuleComponentInput(FrozenContract):
    schema_version: Literal["market-rule-component-input.v1"] = "market-rule-component-input.v1"
    rule: RiskConditionV1
    sample: RiskMonitorInput
    now: AwareDatetime


class RuleComponentOutput(FrozenContract):
    schema_version: Literal["market-rule-component-output.v1"] = "market-rule-component-output.v1"
    truth: Literal["true", "false", "unknown", "error"]
    value: Decimal | None
    reason: str | None


def _parameters(parameters: dict[str, Any] | None) -> None:
    if parameters not in (None, {}):
        raise ValueError("market_component_parameters_not_supported")


def normalize_market_path_component(
    payload: dict[str, Any], parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _parameters(parameters)
    return PathComponentInput.model_validate(payload).path.model_dump(mode="json")


def build_market_feature_component(
    payload: dict[str, Any], parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _parameters(parameters)
    value = FeatureComponentInput.model_validate(payload)
    return build_market_features(
        value.scope, value.candles, value.as_of,
        stale_after_seconds=value.stale_after_seconds,
    ).model_dump(mode="json")


def evaluate_market_rule_component(
    payload: dict[str, Any], parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _parameters(parameters)
    value = RuleComponentInput.model_validate(payload)
    truth, metric, reason = evaluate_condition(value.rule, value.sample, now=value.now)
    return RuleComponentOutput.model_validate({
        "truth": truth, "value": metric, "reason": reason,
    }).model_dump(mode="json")

"""Versioned monitor receipts; V1 remains readable without granting continuity."""

from packages.contracts_py.decision_hub_contracts.generated.decision_product_v1 import (
    DecisionConditionEvaluationV2,
    DecisionPerConditionResultV2,
)

ConditionEvaluationV2 = DecisionConditionEvaluationV2
PerConditionResultV2 = DecisionPerConditionResultV2

__all__ = ["ConditionEvaluationV2", "PerConditionResultV2"]

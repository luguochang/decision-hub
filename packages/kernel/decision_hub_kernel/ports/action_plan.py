"""Action DTO boundary. Domain evaluation and repository ownership stay outside."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from packages.contracts_py.decision_hub_contracts.generated.decision_product_v1 import (
    DecisionActionOutcomeV1,
    DecisionActionPlanHeadV1,
    DecisionActionPlanRevisionV1,
    DecisionConditionCompareV1,
    DecisionConditionEvaluationV1,
    DecisionConditionInputV1,
    DecisionConditionV1,
    DecisionOpportunityV1,
    DecisionProbabilityV1,
    DecisionSnapshotV1,
)
from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
    ActionPlanRevisionV2,
    DecisionSnapshotV2,
    DecisionOpportunityV2,
)

Opportunity = DecisionOpportunityV1
DecisionSnapshot = DecisionSnapshotV1
ActionPlanRevision = DecisionActionPlanRevisionV1
ActionPlanHead = DecisionActionPlanHeadV1
Condition = DecisionConditionV1
ConditionCompare = DecisionConditionCompareV1
ConditionInput = DecisionConditionInputV1
ConditionEvaluation = DecisionConditionEvaluationV1
ActionOutcome = DecisionActionOutcomeV1
Probability = DecisionProbabilityV1


def parse_opportunity(value: object) -> DecisionOpportunityV1 | DecisionOpportunityV2:
    """Select the stored opportunity version without widening the legacy alias."""
    version = value.get("schema_version") if isinstance(value, dict) else getattr(value, "schema_version", None)
    if version == "decision-opportunity.v1":
        return DecisionOpportunityV1.model_validate(value)
    if version == "decision-opportunity.v2":
        return DecisionOpportunityV2.model_validate(value)
    raise ValueError("decision_opportunity_schema_version_unsupported")


def parse_decision_snapshot(value: object) -> DecisionSnapshotV1 | DecisionSnapshotV2:
    """Explicit persisted wire routing; never adapts V1 into V2 authority."""
    version = value.get("schema_version") if isinstance(value, dict) else getattr(value, "schema_version", None)
    if version == "decision-snapshot.v1":
        return DecisionSnapshotV1.model_validate(value)
    if version == "decision-snapshot.v2":
        return DecisionSnapshotV2.model_validate(value)
    raise ValueError("decision_snapshot_schema_version_unsupported")


def parse_action_plan_revision(value: object) -> DecisionActionPlanRevisionV1 | ActionPlanRevisionV2:
    """Readers select a closed contract from stored schema_version."""
    version = value.get("schema_version") if isinstance(value, dict) else getattr(value, "schema_version", None)
    if version == "decision-action-plan-revision.v1":
        return DecisionActionPlanRevisionV1.model_validate(value)
    if version == "decision-action-plan-revision.v2":
        return ActionPlanRevisionV2.model_validate(value)
    raise ValueError("action_plan_revision_schema_version_unsupported")


class ActionClock(Protocol):
    def now(self) -> datetime: ...


class ActionPlanReader(Protocol):
    def get_revision(self, revision_id: str) -> ActionPlanRevision | None: ...

    def get_head(self, plan_id: str) -> ActionPlanHead | None: ...


def probability_from_reference(value: dict, *, target_id: str) -> Probability:
    """Explicit reference adapter: calibration_ref -> production provenance_ref.

    The caller resolves the reference target to its registered target_id. This
    adapter does not authenticate calibration data or grant qualified status.
    """
    if set(value) != {"kind", "target", "value", "calibration_ref"}:
        raise ValueError("decision_reference_probability_shape_invalid")
    if value["target"] not in {"return_positive", "target_before_stop"}:
        raise ValueError("decision_reference_probability_target_invalid")
    return Probability.model_validate(
        {
            "kind": value["kind"],
            "target_id": target_id,
            "value": value["value"],
            "provenance_ref": value["calibration_ref"],
        }
    )


__all__ = [
    "parse_opportunity",
    "Opportunity",
    "DecisionSnapshot",
    "ActionPlanRevision",
    "ActionPlanHead",
    "Condition",
    "ConditionCompare",
    "ConditionInput",
    "ConditionEvaluation",
    "ActionOutcome",
    "Probability",
    "ActionClock",
    "ActionPlanReader",
    "probability_from_reference",
    "parse_decision_snapshot",
    "parse_action_plan_revision",
]

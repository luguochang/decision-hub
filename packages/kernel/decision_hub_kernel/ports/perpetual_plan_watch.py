"""Explicit continuing consent; a legacy one-revision watch never implies it."""

from typing import Annotated, Literal

from pydantic import Field

from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
    DecisionSourceRefV1,
    PerpetualPlanReviewTriggerV1,
    PerpetualPlanWatchCommandV1,
    PerpetualPlanWatchReceiptV1,
)

from .perpetual_research_inputs import CurrentPaperPartition


class PerpetualPlanWatchCommandV2(PerpetualPlanWatchCommandV1):
    schema_version: Literal["perpetual-plan-watch-command.v2"]
    continuation: Literal["follow_published_revisions"]
    partition: CurrentPaperPartition
    input_policy: Literal["saved_preferences_current_paper_ledger.v1"]


class PerpetualPlanWatchReceiptV2(PerpetualPlanWatchReceiptV1):
    schema_version: Literal["perpetual-plan-watch-receipt.v2"]
    command: PerpetualPlanWatchCommandV2


class PerpetualPlanWatchCommandV3(PerpetualPlanWatchCommandV2):
    """Explicit condition research consent; V2 continues to mean expiry only."""

    schema_version: Literal["perpetual-plan-watch-command.v3"]
    condition_research: Annotated[bool, Field(strict=True)]


class PerpetualPlanWatchReceiptV3(PerpetualPlanWatchReceiptV2):
    schema_version: Literal["perpetual-plan-watch-receipt.v3"]
    command: PerpetualPlanWatchCommandV3


def parse_continuous_watch_command(value):
    if isinstance(value, (PerpetualPlanWatchCommandV2, PerpetualPlanWatchCommandV3)):
        return value
    model = (
        PerpetualPlanWatchCommandV3
        if isinstance(value, dict)
        and value.get("schema_version") == "perpetual-plan-watch-command.v3"
        else PerpetualPlanWatchCommandV2
    )
    return model.model_validate(value)


def condition_research_authorized(receipt):
    return (
        getattr(receipt, "schema_version", None) == "perpetual-plan-watch-receipt.v3"
        and receipt.command.schema_version == "perpetual-plan-watch-command.v3"
        and receipt.command.condition_research is True
        and receipt.command.mode == "remind_and_research"
    )


class PerpetualPlanReviewTriggerV2(PerpetualPlanReviewTriggerV1):
    """A real pre-expiry condition event, never an artificial expiry proof."""

    schema_version: Literal["perpetual-plan-review-trigger.v2"]
    motive: Literal["condition_confirmed", "condition_invalidated"]
    expired_at: None = None
    condition_event_ref: DecisionSourceRefV1


class PerpetualPlanReviewTriggerV3(PerpetualPlanReviewTriggerV1):
    """A committed uncovered event branch, with its own reviewed post window."""

    schema_version: Literal["perpetual-plan-review-trigger.v3"]
    motive: Literal["event_scenario_uncovered"]
    expired_at: None = None
    event_plan_ref: DecisionSourceRefV1
    selection_ref: DecisionSourceRefV1

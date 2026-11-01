"""Event-owned monitor receipts; no Run, account, fill or Action authority."""

from typing import Literal

from pydantic import AwareDatetime, Field

from .action_monitor import PerConditionResultV2
from .market_path import FrozenContract

EventMonitorState = Literal["waiting", "triggered", "invalidated", "expired", "missing"]


class EventOpportunityEvaluation(FrozenContract):
    schema_version: Literal["event-opportunity-evaluation.v1"] = "event-opportunity-evaluation.v1"
    evaluation_id: str
    monitor_id: str
    event_key: str
    forecast_id: str
    instrument: Literal["BTC-USDT", "ETH-USDT"]
    position_context: Literal["flat", "long"]
    activation_id: str | None
    opportunity_id: str | None
    opportunity_hash: str | None
    scenario_id: str | None
    tick_at: AwareDatetime
    as_of: AwareDatetime
    evaluated_at: AwareDatetime
    generation: int = Field(ge=1)
    state_version_before: int = Field(ge=0)
    state_version_after: int = Field(ge=1)
    state_before: EventMonitorState
    state_after: EventMonitorState
    data_status: Literal["available", "awaiting_release", "missing"]
    reason_codes: tuple[str, ...]
    per_condition: tuple[PerConditionResultV2, ...] = ()
    input_ref: str | None = None
    input_hash: str | None = None
    evaluation_hash: str


class EventConfirmationInputs(FrozenContract):
    scheduled_at: AwareDatetime
    anchor_close_at: AwareDatetime | None = None
    anchor_price: str | None = None
    baseline_start_at: AwareDatetime
    baseline_end_at: AwareDatetime
    baseline_minute_count: int = Field(ge=0, le=20)
    baseline_volume_median: str | None = None
    closed_bar_at: AwareDatetime | None = None
    current_close: str | None = None
    current_minute_volume: str | None = None
    event_return: str | None = None
    minute_volume_ratio: str | None = None
    input_refs: tuple[str, ...] = ()


class EventOpportunityEvaluationV2(EventOpportunityEvaluation):
    """New confirmation meaning; the V1 payload/hash remains unchanged."""

    schema_version: Literal["event-opportunity-evaluation.v2"] = "event-opportunity-evaluation.v2"
    confirmation_protocol: Literal["event-confirmation.v1"] = "event-confirmation.v1"
    strategy_target: Literal["cpi_event_anchored_policy_net_profit_event_24h.v1"] = (
        "cpi_event_anchored_policy_net_profit_event_24h.v1"
    )
    confirmation_policy_hash: str | None = None
    rule_summary: str
    strategy_probability_status: Literal["unknown"] = "unknown"
    strategy_probability_gap: str
    confirmation_inputs: EventConfirmationInputs | None = None


class EventOpportunityNotice(FrozenContract):
    schema_version: Literal["event-opportunity-notice.v1"] = "event-opportunity-notice.v1"
    notice_id: str
    event_key: str
    reference_period: str | None
    instrument: Literal["BTC-USDT", "ETH-USDT"] | None = None
    position_context: Literal["flat", "long"] | None = None
    transition: str
    forecast_id: str | None = None
    opportunity_id: str | None = None
    activation_id: str | None = None
    evaluation_id: str | None = None
    reason_codes: tuple[str, ...] = ()
    subject: str
    body: str
    created_at: AwareDatetime
    dedupe_key: str
    payload_hash: str

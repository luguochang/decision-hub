"""Separate event-label scores and authenticated paper price-path outcomes."""

from decimal import Decimal
from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from .market_path import FrozenContract, canonical_hash


class EventJointScore(FrozenContract):
    label: str
    brier: Decimal = Field(ge=0)
    log_loss: float = Field(ge=0, allow_inf_nan=False)
    target: Literal["event_outcome"] = "event_outcome"


class EventAssetOutcome(FrozenContract):
    instrument: Literal["BTC-USDT", "ETH-USDT"]
    state: Literal["waiting_horizon", "missing_path", "missing_plan", "scored"]
    opportunity_ref: str | None = None
    opportunity_hash: str | None = None
    policy_hash: str | None = None
    source_policy_hash: str | None = None
    reaction_start_at: AwareDatetime
    reaction_end_at: AwareDatetime
    activation_known_at: AwareDatetime
    return_24h: Decimal | None = None
    strategy_entered: bool | None = None
    strategy_entry_at: AwareDatetime | None = None
    strategy_exit_at: AwareDatetime | None = None
    strategy_net_return: Decimal | None = None
    held_long_reduce_at: AwareDatetime | None = None
    held_long_reduction_value_fraction: Decimal | None = None
    path_semantic_hash: str | None = None
    path_provenance_hash: str | None = None
    capture_originals: tuple[tuple[str, str], ...] = ()
    paper_replay: Literal[True] = True
    confirmation_protocol: Literal["event-confirmation.v1"] = "event-confirmation.v1"
    strategy_target: Literal["cpi_event_anchored_policy_net_profit_event_24h.v1"] = (
        "cpi_event_anchored_policy_net_profit_event_24h.v1"
    )
    simulated_price_availability_at_close: Literal[True] = True
    strategy_profit_probability: None = None
    gaps: tuple[str, ...] = ()

    @model_validator(mode="after")
    def results_require_path(self):
        values = (
            self.return_24h,
            self.strategy_net_return,
            self.held_long_reduction_value_fraction,
        )
        if any(v is not None and not v.is_finite() for v in values):
            raise ValueError("event_outcome_nonfinite_return")
        if self.state in {"waiting_horizon", "missing_path"} and any(v is not None for v in values):
            raise ValueError("event_outcome_result_without_complete_path")
        if self.return_24h is not None and not (
            self.path_semantic_hash and self.path_provenance_hash
        ):
            raise ValueError("event_outcome_result_path_hash_missing")
        if self.state == "scored" and (
            self.strategy_net_return is None or self.policy_hash is None
        ):
            raise ValueError("event_outcome_plan_result_missing")
        if self.strategy_entered is False and self.strategy_net_return != 0:
            raise ValueError("event_outcome_untriggered_not_profit")
        if self.strategy_entry_at and self.strategy_entry_at < self.activation_known_at:
            raise ValueError("event_outcome_entry_before_actual_known")
        if self.held_long_reduce_at and self.held_long_reduce_at < self.activation_known_at:
            raise ValueError("event_outcome_reduction_before_actual_known")
        return self


class EventOutcomeSnapshot(FrozenContract):
    schema_version: Literal["event-outcome.v1"] = "event-outcome.v1"
    outcome_id: str
    event_key: str
    forecast_id: str
    realization_id: str
    owner_forecast_hash: str
    realization_hash: str
    scheduled_at: AwareDatetime
    as_of: AwareDatetime
    recorded_at: AwareDatetime
    event_score: EventJointScore
    assets: tuple[EventAssetOutcome, ...]
    method_hash: str
    input_hash: str
    revision_parent_ref: str | None = None
    replay_protocol: Literal["event-anchored-known-activation-policy-paper.v1"] = (
        "event-anchored-known-activation-policy-paper.v1"
    )
    financial_qualified: Literal[False] = False
    actual_execution_or_account_profit: Literal[False] = False
    payload_hash: str

    @model_validator(mode="after")
    def identity(self):
        if not self.scheduled_at <= self.as_of <= self.recorded_at:
            raise ValueError("event_outcome_clock_invalid")
        if tuple(a.instrument for a in self.assets) != ("BTC-USDT", "ETH-USDT"):
            raise ValueError("event_outcome_asset_partition_invalid")
        body = self.model_dump(
            mode="json",
            include={
                "forecast_id",
                "realization_id",
                "owner_forecast_hash",
                "realization_hash",
                "event_score",
                "assets",
                "method_hash",
                "replay_protocol",
            },
        )
        if (
            self.input_hash != canonical_hash(body)
            or self.outcome_id != "event-outcome:" + self.input_hash
        ):
            raise ValueError("event_outcome_input_identity_invalid")
        return self


def seal_event_outcome(body):
    fields = {
        "forecast_id",
        "realization_id",
        "owner_forecast_hash",
        "realization_hash",
        "event_score",
        "assets",
        "method_hash",
        "replay_protocol",
    }
    body = dict(body, replay_protocol="event-anchored-known-activation-policy-paper.v1")
    identity = canonical_hash({key: value for key, value in body.items() if key in fields})
    draft = EventOutcomeSnapshot.model_validate(
        body
        | {
            "input_hash": identity,
            "outcome_id": "event-outcome:" + identity,
            "payload_hash": "0" * 64,
        }
    )
    canonical = draft.model_dump(mode="json", exclude={"payload_hash"})
    return EventOutcomeSnapshot.model_validate(
        canonical | {"payload_hash": canonical_hash(canonical)}
    )

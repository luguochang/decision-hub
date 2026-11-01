"""Explicit internal/manual wire versions; no canonical financial authority DTO."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
    DecisionSettingsPartitionV1,
    DecisionSourceRefV1,
)

from .market_path import aware_utc

MANUAL_INSTRUMENTS = ("BTC-USDT-SWAP", "ETH-USDT-SWAP")
MANUAL_INSTRUMENT_IDS = tuple("okx:" + value for value in MANUAL_INSTRUMENTS)
MANUAL_HORIZONS = ("4h", "24h", "72h")

Identity = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^\S(?:[\s\S]*\S)?$")]
Positive = Annotated[
    str, Field(max_length=80, pattern=r"^(?:[1-9][0-9]*(?:\.[0-9]*[1-9])?|0\.[0-9]*[1-9])$")
]


class FrozenManual(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, regex_engine="python-re", strict=True)


class ManualHorizon(FrozenManual):
    # Old 4h originals must retain their exact sealed shape.
    horizon: Literal["24h", "72h"] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @field_validator("horizon", mode="before")
    @classmethod
    def explicit_horizon(cls, value: object) -> object:
        if value is None:
            raise ValueError("perp_manual_horizon_explicit_null")
        return value


def manual_horizon(value: ManualHorizon | ManualPreparationCommand) -> str:
    return value.horizon or "4h"


class ManualIdentity(ManualHorizon):
    partition: DecisionSettingsPartitionV1
    settings_revision: DecisionSourceRefV1
    settings_state_version: int = Field(ge=1)
    revision: DecisionSourceRefV1
    expected_plan_state_version: int = Field(ge=1)
    account_revision: DecisionSourceRefV1
    account_state_version: int = Field(ge=1)
    protocol_ref: DecisionSourceRefV1


class ManualPreparationCommand(FrozenManual):
    schema_version: Literal["perpetual-manual-prepare.v1"]
    request_id: Identity
    partition: DecisionSettingsPartitionV1
    settings_revision: DecisionSourceRefV1
    settings_state_version: int = Field(ge=1)
    revision: DecisionSourceRefV1
    horizon: Literal["4h", "24h", "72h"]


class ManualDisplayCommand(ManualIdentity):
    schema_version: Literal["perpetual-manual-display.v1"]
    request_id: Identity


class ManualExecutionCommand(ManualIdentity):
    schema_version: Literal["perpetual-manual-intent-command.v1"]
    request_id: Identity
    display_receipt: DecisionSourceRefV1
    max_entry_notional_usdt: Positive
    stop_price_usdt: Positive
    take_profit_price_usdt: Positive
    exit_at: str
    simulation_acknowledged: Literal[True]

    @field_validator("exit_at")
    @classmethod
    def timestamp(cls, value):
        # Preserve wire spelling for the user's full-command content digest.
        aware_utc(datetime.fromisoformat(value))
        return value


class EventManualBinding(FrozenManual):
    event_plan_ref: DecisionSourceRefV1
    selection_ref: DecisionSourceRefV1
    branch_ref: DecisionSourceRefV1
    event_review_ref: DecisionSourceRefV1
    event_publication_ref: DecisionSourceRefV1


class EventManualIdentity(ManualIdentity, EventManualBinding):
    """The event choice and the current user PAPER account are separate originals."""


class EventManualPreparationCommand(ManualPreparationCommand, EventManualBinding):
    schema_version: Literal["perpetual-manual-prepare.v2"]


class EventManualDisplayCommand(EventManualIdentity):
    schema_version: Literal["perpetual-manual-display.v2"]
    request_id: Identity


class EventManualExecutionCommand(ManualExecutionCommand, EventManualBinding):
    schema_version: Literal["perpetual-manual-intent-command.v2"]


def parse_manual_preparation(value):
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return (
        EventManualPreparationCommand
        if isinstance(value, dict) and value.get("schema_version") == "perpetual-manual-prepare.v2"
        else ManualPreparationCommand
    ).model_validate(value)


def parse_manual_display(value):
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return (
        EventManualDisplayCommand
        if isinstance(value, dict) and value.get("schema_version") == "perpetual-manual-display.v2"
        else ManualDisplayCommand
    ).model_validate(value)


def parse_manual_execution(value):
    import json

    if isinstance(value, str):
        value = json.loads(value)
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return (
        EventManualExecutionCommand
        if isinstance(value, dict)
        and value.get("schema_version") == "perpetual-manual-intent-command.v2"
        else ManualExecutionCommand
    ).model_validate(value)


def parse_manual_identity(value):
    return (
        EventManualIdentity
        if isinstance(value, dict) and "event_plan_ref" in value
        else ManualIdentity
    ).model_validate(value)


class ManualInputGuidePendingQuote(FrozenManual):
    status: Literal["pending"]


class ManualInputGuideCapturedQuote(FrozenManual):
    status: Literal["fresh", "stale"]
    sampling_receipt: DecisionSourceRefV1
    capture_ref: DecisionSourceRefV1
    bid: Positive
    ask: Positive
    provider_observed_at: Annotated[AwareDatetime, Field(strict=False)]
    observed_local_lower: Annotated[AwareDatetime, Field(strict=False)]
    observed_local_upper: Annotated[AwareDatetime, Field(strict=False)]
    received_at: Annotated[AwareDatetime, Field(strict=False)]
    expires_at: Annotated[AwareDatetime, Field(strict=False)]


class ManualInputGuide(FrozenManual):
    schema_version: Literal["perpetual-manual-input-guide.v1"]
    owner_id: Identity
    prepare_request_id: Identity
    context: EventManualIdentity | ManualIdentity
    target_side: Literal["long", "short"]
    tick_sz: Positive
    latest_entry_at: Annotated[AwareDatetime, Field(strict=False)]
    latest_exit_at: Annotated[AwareDatetime, Field(strict=False)]
    server_time: Annotated[AwareDatetime, Field(strict=False)]
    quote: ManualInputGuidePendingQuote | ManualInputGuideCapturedQuote


class ManualExecutionIntent(ManualHorizon):
    schema_version: Literal["perpetual-manual-execution-intent.v1"]
    id: Identity
    owner_id: Identity
    account_id: Identity
    account_key: Identity
    protocol_ref: DecisionSourceRefV1
    scope_id: str
    event_key: Identity
    plan_id: Identity
    revision: DecisionSourceRefV1
    candidate_id: str
    target_side: Literal["long", "short"]
    phase: Literal["entry", "reduce", "exit"]
    command_request_id: Identity
    command_hash: str
    command_json: str
    accepted_at: Annotated[AwareDatetime, Field(strict=False)]
    eligible_at: Annotated[AwareDatetime, Field(strict=False)]
    quote_deadline: Annotated[AwareDatetime, Field(strict=False)]
    sampling_generation: int = Field(ge=0)
    episode_ref: DecisionSourceRefV1 | None
    trigger_ref: DecisionSourceRefV1 | None
    native_reviewed: Literal[False]
    financial_qualified: Literal[False]
    policy_authority: Literal["user_explicit_paper_simulation"]
    payload_hash: str


def manual_scope(instrument="BTC-USDT-SWAP"):
    if instrument not in MANUAL_INSTRUMENTS:
        raise ValueError("perp_manual_instrument_not_supported")
    return "perp-book:okx:" + instrument


@dataclass(frozen=True)
class PerpetualSamplingScope:
    scope_id: str = "perp-book:okx:BTC-USDT-SWAP"
    instrument: str = "BTC-USDT-SWAP"

    def validate(self):
        if self.scope_id != manual_scope(self.instrument):
            raise ValueError("perp_manual_sampling_scope_invalid")


class ManualExecutionArtifact(FrozenManual):
    """Sealed version envelope; body JSON remains the exact original text."""

    schema_version: Literal[
        "perpetual-manual-execution-receipt.v1",
        "perpetual-manual-fill.v1",
        "perpetual-manual-episode.v1",
        "perpetual-manual-outcome.v1",
    ]
    id: str
    account_key: str
    intent_id: str
    body_json: str
    payload_hash: str

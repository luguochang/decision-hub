"""Local product transport, embedding W6 contracts without redefining money."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from packages.contracts_py.decision_hub_contracts.generated.action_execution_v1 import (
    ActionExecutionEpisodeV1,
    ActionExecutionProtocolV1,
    ActionOutcomeV2,
    ActionPaperCheckpointV1,
)

Key = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_:.\-]+$")]
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Horizon = Literal["30m", "24h", "72h"]
Scenario = Literal["cash", "existing_long"]


class Wire(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PaperRegistrationCommand(Wire):
    request_id: Key
    source_opportunity_id: Key
    scenario: Scenario


class PaperMethodSource(Wire):
    opportunity_id: Key
    run_id: Key
    scope_id: str
    horizon: Horizon
    method_hash: Digest
    original_ref: Key
    original_hash: Digest


class PaperRegistrationReceipt(Wire):
    schema_version: Literal["action-paper-registration.v1"]
    owner_id: Key
    request_id: Key
    command_hash: Digest
    source: PaperMethodSource
    scenario: Scenario
    portfolio_id: Key
    protocol: ActionExecutionProtocolV1
    accepted_at: AwareDatetime
    payload_hash: Digest


class PaperOpeningReceipt(Wire):
    schema_version: Literal["action-paper-bootstrap.v1"]
    registration_hash: Digest
    portfolio_id: Key
    status: Literal["opened", "unknown"]
    reason: str | None
    checkpoint_ref: Key | None
    checkpoint_hash: Digest | None
    decided_at: AwareDatetime
    payload_hash: Digest


class PaperOptions(Wire):
    schema_version: Literal["action-paper-options.v1"]
    owner_id: Key
    scope_id: str
    horizon: Horizon
    enabled: bool
    source: PaperMethodSource | None
    registrations: list[PaperRegistrationReceipt]
    registrations_more: bool
    active_request_ids: list[Key]
    gap_codes: list[str]


class PaperStatus(Wire):
    schema_version: Literal["action-paper-status.v1"]
    registration: PaperRegistrationReceipt
    bootstrap: PaperOpeningReceipt | None
    state: Literal["waiting_start", "waiting_opening", "unknown", "running", "ended"]
    account: ActionPaperCheckpointV1 | None
    episode: ActionExecutionEpisodeV1 | None
    pending_intents: int
    outcomes_count: int
    outcomes: list[ActionOutcomeV2]
    execution_reasons: dict[str, str | None]
    opportunities_count: int
    unresolved_inventory: bool
    financial_qualified: Literal[False]
    observed_at: AwareDatetime

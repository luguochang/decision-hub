"""Internal versioned execution design original; never an execution grant."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
    DecisionSourceRefV1,
)


class PerpetualPaperExecutionProtocol(BaseModel):
    """Stored in the existing ExecutionProtocolRecord registry, V1 kept separate."""

    model_config = ConfigDict(extra="forbid", frozen=True, regex_engine="python-re")
    schema_version: Literal["perp-quote-paper-protocol.v1"]
    model: Literal["perp_quote_paper.v1"]
    id: str = Field(min_length=1, max_length=128)
    trial_id: str = Field(min_length=1, max_length=128)
    owner_id: str = Field(min_length=1, max_length=128)
    account_id: str = Field(min_length=1, max_length=128)
    account_revision: DecisionSourceRefV1
    instrument_refs: dict[str, DecisionSourceRefV1]
    profile: DecisionSourceRefV1
    execution_mode: Literal["manual_after_visible", "server_auto_paper"]
    registered_at: AwareDatetime
    T0: AwareDatetime
    T1: AwareDatetime
    reaction_delay_seconds: Literal[1]
    quote_max_age_seconds: Literal[5]
    entry_wait_seconds: Literal[30]
    top_size_participation: Literal["0.1"]
    position_mode: Literal["net"]
    margin_mode: Literal["isolated"]
    initial_notional_to_margin_ratio: Literal["1"]
    quote_selection: Literal["first_observed_interval_lower_strictly_after_eligible"]
    fill_basis: Literal["buy_ask_sell_bid_then_adverse_slippage"]
    order_kind: Literal["market_after_eligible"]
    size_unit: Literal["contracts"]
    same_account_quote_capacity: Literal["cumulative_no_reuse"]
    entry_partial_remainder: Literal["cancel"]
    exit_partial_remainder: Literal[
        "retain_position_and_protection_retry_new_quote_alarm_after_30s"
    ]
    stress_reaction_delays_seconds: tuple[Literal[5, 15], ...]
    fee_rate: Literal["0.0005"]
    fee_basis: Literal["abs_base_quantity_times_actual_simulated_fill_price"]
    fee_status: Literal["explicit_paper_assumption_not_authenticated_account_fee_tier"]
    slippage_bps: Literal["2"]
    slippage_accounting: Literal["included_in_fill_price_no_second_cash_deduction"]
    size_policy_ref: None
    exit_policy_ref: None
    required_reviewed_originals: tuple[Literal["size_policy", "exit_and_protection_policy"], ...]
    execution_authority: Literal[False]
    payload_hash: str = Field(pattern="^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def valid(self):
        if not self.registered_at <= self.T0 < self.T1:
            raise ValueError("perp_execution_protocol_window_invalid")
        if not self.instrument_refs or not set(self.instrument_refs) <= {
            "okx:BTC-USDT-SWAP",
            "okx:ETH-USDT-SWAP",
        }:
            raise ValueError("perp_execution_protocol_scope_invalid")
        self._require_policy_authority()
        if self.stress_reaction_delays_seconds != (5, 15):
            raise ValueError("perp_execution_stress_delays_invalid")
        if self.id != protocol_id(self.owner_id, self.account_id, self.trial_id, self.T0, self.T1):
            raise ValueError("perp_execution_protocol_identity_invalid")
        if any(
            value != value.strip()
            for value in (self.id, self.trial_id, self.owner_id, self.account_id)
        ) or self.owner_id.startswith("system:"):
            raise ValueError("perp_execution_protocol_identity_invalid")
        return self

    def _require_policy_authority(self):
        if tuple(self.required_reviewed_originals) != ("size_policy", "exit_and_protection_policy"):
            raise ValueError("perp_execution_reviewed_originals_required")

    @property
    def reference(self) -> DecisionSourceRefV1:
        # Existing execution protocol convention: seal excludes its own hash field.
        return DecisionSourceRefV1(ref=self.id, hash=self.payload_hash)


class PerpetualManualPaperProtocol(PerpetualPaperExecutionProtocol):
    """Independent user simulation policy, never a native review grant."""

    schema_version: Literal["perp-manual-paper-protocol.v1"]
    execution_mode: Literal["manual_after_visible"]
    required_reviewed_originals: tuple[()] = ()
    required_user_originals: tuple[Literal["manual_size", "manual_exit"], ...]
    policy_authority: Literal["user_explicit_paper_simulation"]
    native_reviewed: Literal[False]

    def _require_policy_authority(self):
        if self.required_reviewed_originals or self.required_user_originals != (
            "manual_size",
            "manual_exit",
        ):
            raise ValueError("perp_manual_policy_originals_required")


def protocol_id(
    owner_id: str, account_id: str, trial_id: str, start_at: datetime, end_at: datetime
) -> str:
    from .market_path import aware_utc, canonical_hash

    return "perp_exec_" + canonical_hash(
        [
            owner_id,
            account_id,
            trial_id,
            aware_utc(start_at).isoformat(),
            aware_utc(end_at).isoformat(),
        ]
    )

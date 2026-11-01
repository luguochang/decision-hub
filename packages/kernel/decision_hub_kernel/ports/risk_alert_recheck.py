"""One explicit historical alert recheck; acceptance conveys no Run permission."""

from datetime import datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RiskAlertRecheckCommand(_Closed):
    request_id: str = Field(min_length=1, max_length=128)
    expected_head_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class RiskAlertRecheckReceipt(_Closed):
    schema_version: Literal["risk-alert-recheck-receipt.v1"] = "risk-alert-recheck-receipt.v1"
    request_id: str
    owner_id: str
    alert_id: str
    scope_id: str
    original_request_id: str
    original_head_hash: str
    original_triggered_at: AwareDatetime
    original_resolved_at: AwareDatetime | None
    accepted_at: AwareDatetime
    research_request_id: str
    trigger_key: str
    admission_origin: Literal["automatic"] = "automatic"
    mode: Literal["historical_event_current_evidence"] = "historical_event_current_evidence"
    automatic_new_rounds: Literal[False] = False


class RiskAlertRecheckState(_Closed):
    schema_version: Literal["risk-alert-recheck-state.v1"] = "risk-alert-recheck-state.v1"
    alert_id: str
    scope_id: str
    instrument: Literal["BTC-USDT", "ETH-USDT"]
    observed_at: AwareDatetime
    original_triggered_at: AwareDatetime
    original_resolved_at: AwareDatetime | None
    original_request_id: str
    head_hash: str
    eligible: bool
    reason_code: str | None
    capacity: dict[str, int | str]
    receipt: RiskAlertRecheckReceipt | None
    research_request: dict[str, object] | None
    timeline: list[dict[str, object]]
    scope_preparation_required_on_claim: Literal[True] = True


class RiskAlertRecheckTrace(_Closed):
    schema_version: Literal["risk-alert-recheck-trace.v1"] = "risk-alert-recheck-trace.v1"
    alert_id: str
    research_request_id: str
    event: Literal["claimed", "budget_blocked", "admission_failed", "queued", "claim_expired"]
    recorded_at: AwareDatetime
    attempt: int = Field(ge=1, le=3)
    lease_generation: int = Field(ge=1, le=2_147_483_647)
    request_status: str
    reason_code: str | None
    capacity: dict[str, int | str]
    event_id: str | None
    run_id: str | None

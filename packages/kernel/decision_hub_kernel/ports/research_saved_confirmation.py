"""Explicit authorization to continue one exact Saved report; no report grant."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import (
    AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, StrictBool,
    StrictInt, StrictStr, field_validator, model_validator,
)

SAVED_CONFIRMATION_TERMINAL_REASONS = frozenset({
    "publication_execution_compute_budget_exceeded",
    "publication_full_frozen_original_changed",
})


def _exact_identity(value: str) -> str:
    if value != value.strip():
        raise ValueError("saved_confirmation_identity_invalid")
    return value


Identity = Annotated[StrictStr, Field(min_length=1, max_length=128), AfterValidator(_exact_identity)]
Digest = Annotated[StrictStr, Field(pattern=r"^[a-f0-9]{64}$")]
Count = Annotated[StrictInt, Field(ge=0, le=9_007_199_254_740_991)]
Version = Annotated[StrictInt, Field(ge=1, le=9_007_199_254_740_990)]
BundleBytes = Annotated[StrictInt, Field(ge=1, le=16 * 1024 * 1024)]
MaterialBytes = Annotated[StrictInt, Field(ge=1, le=192 * 1024 * 1024)]
CommandId = Annotated[StrictStr, Field(pattern=(
    r"^saved-confirmation:[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-"
    r"[a-f0-9]{4}-[a-f0-9]{12}$"))]


class _SavedConfirmationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class SavedConfirmationCommandV1(_SavedConfirmationModel):
    schema_version: Literal["research-saved-confirmation-command.v1"]
    request_id: CommandId
    owner_id: Identity
    expected_request_id: Identity
    expected_generation: Annotated[StrictInt, Field(ge=1, le=9_007_199_254_740_991)]
    expected_state_version: Version
    expected_bundle_hash: Digest
    expected_bundle_bytes: BundleBytes
    expected_material_hash: Digest
    expected_material_bytes: MaterialBytes
    expected_report_hash: Digest
    expected_report_bytes: BundleBytes
    expected_reader_hash: Digest


class SavedConfirmationStatusV1(_SavedConfirmationModel):
    schema_version: Literal["research-saved-confirmation-status.v1"]
    run_id: Identity
    request_id: Identity
    generation: Count
    state_version: Version
    state: Literal["queued", "prepared", "confirmed", "unconfirmed", "cancelled", "revoked"]
    reason_code: Annotated[StrictStr, Field(pattern=r"^[a-z0-9_]{1,160}$")] | None
    eligible: StrictBool
    bundle_hash: Digest
    bundle_bytes: BundleBytes
    material_hash: Digest
    material_bytes: MaterialBytes
    report_hash: Digest
    report_bytes: BundleBytes
    reader_hash: Digest
    saved_count: Literal[1]
    ack_count: Annotated[StrictInt, Field(ge=0, le=1)]
    fenced: StrictBool
    owner_lease_present: StrictBool
    authorization_pending: StrictBool

    @field_validator("saved_count", mode="before")
    @classmethod
    def exact_saved_count(cls, value):
        if type(value) is not int:
            raise ValueError("saved_confirmation_saved_count_invalid")
        return value

    @model_validator(mode="after")
    def eligible_head(self):
        if self.eligible and (
            self.state != "unconfirmed" or self.generation < 1
            or self.reason_code not in SAVED_CONFIRMATION_TERMINAL_REASONS
            or self.ack_count != 0 or self.fenced or self.owner_lease_present
            or self.authorization_pending
        ):
            raise ValueError("saved_confirmation_status_invalid")
        return self


class SavedConfirmationReplyV1(_SavedConfirmationModel):
    schema_version: Literal["research-saved-confirmation-reply.v1"]
    run_id: Identity
    request_id: CommandId
    expected_request_id: Identity
    generation: Annotated[StrictInt, Field(ge=1, le=9_007_199_254_740_991)]
    previous_state_version: Version
    authorized_state_version: Annotated[StrictInt, Field(ge=2, le=9_007_199_254_740_991)]
    state: Literal["unconfirmed"]
    authorization_status: Literal["accepted"]
    report_confirmed: Literal[False]
    automatic_retry: Literal[False]

    @field_validator("report_confirmed", "automatic_retry", mode="before")
    @classmethod
    def exact_false(cls, value):
        if type(value) is not bool or value:
            raise ValueError("saved_confirmation_reply_boolean_invalid")
        return value

    @model_validator(mode="after")
    def authorized_version(self):
        if self.authorized_state_version != self.previous_state_version + 1:
            raise ValueError("saved_confirmation_reply_version_invalid")
        return self


class SavedConfirmationReceiptV1(_SavedConfirmationModel):
    schema_version: Literal["research-saved-confirmation-receipt.v1"]
    request_id: CommandId
    owner_id: Identity
    run_id: Identity
    command: SavedConfirmationCommandV1
    result: SavedConfirmationReplyV1
    status: Literal["accepted"]
    payload_hash: Digest
    owner_native_sha256: Digest
    recorded_at: AwareDatetime

    @model_validator(mode="after")
    def exact_command_result(self):
        command, result = self.command, self.result
        if (self.request_id != command.request_id or self.owner_id != command.owner_id
            or result.run_id != self.run_id or result.request_id != self.request_id
            or result.expected_request_id != command.expected_request_id
            or result.generation != command.expected_generation
            or result.previous_state_version != command.expected_state_version
            or result.authorized_state_version != command.expected_state_version + 1):
            raise ValueError("saved_confirmation_receipt_identity_invalid")
        return self

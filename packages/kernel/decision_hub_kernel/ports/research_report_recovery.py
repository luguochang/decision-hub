"""Read-only recovery explanation; metadata never grants a write or report read."""
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictInt, StrictStr


class ReportRecoveryStatusV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    schema_version: Literal["research-report-recovery-status.v1"]
    run_id: Annotated[StrictStr, Field(min_length=1, max_length=128)]
    request_id: Annotated[StrictStr, Field(min_length=1, max_length=128)]
    generation: Annotated[StrictInt, Field(ge=0)]
    state_version: Annotated[StrictInt, Field(ge=1)]
    state: Literal["queued", "prepared", "confirmed", "unconfirmed", "cancelled", "revoked"]
    reason_code: Annotated[StrictStr, Field(pattern=r"^[a-z0-9_]{1,160}$")] | None
    source_abi: Literal["publication-source-snapshot-read.v2"] | None
    action: Literal["not_applicable", "owner_request_required", "new_research_required", "review_required"]
    proof_scope: Literal["metadata_prechecks_only"]


class ReportRecoveryCommandV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    schema_version: Literal["research-report-recovery-command.v1"]
    request_id: Annotated[StrictStr, Field(min_length=1, max_length=128)]
    expected_request_id: Annotated[StrictStr, Field(min_length=1, max_length=128)]
    expected_generation: Annotated[StrictInt, Field(ge=1)]
    expected_state_version: Annotated[StrictInt, Field(ge=1)]


class ReportRecoveryReplyV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    schema_version: Literal["research-report-recovery-reply.v1"]
    run_id: Annotated[StrictStr, Field(min_length=1, max_length=128)]
    request_id: Annotated[StrictStr, Field(min_length=1, max_length=128)]
    generation: Annotated[StrictInt, Field(ge=1)]
    state_version: Annotated[StrictInt, Field(ge=1)]
    state: Literal["queued"]
    report_confirmed: Literal[False]
    source_abi: Literal["publication-source-snapshot-read.v2"]
    automatic_retry: Literal[False]


class ReportRecoveryReceiptV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    schema_version: Literal["research-report-recovery-receipt.v1"]
    request_id: Annotated[StrictStr, Field(min_length=1, max_length=128)]
    owner_id: Annotated[StrictStr, Field(min_length=1, max_length=128)]
    run_id: Annotated[StrictStr, Field(min_length=1, max_length=128)]
    command: ReportRecoveryCommandV1
    result: ReportRecoveryReplyV1
    status: Literal["accepted", "unconfirmed"]
    payload_hash: Annotated[StrictStr, Field(pattern=r"^[a-f0-9]{64}$")]
    recorded_at: AwareDatetime


# Independent scheduled preparation command; V1 user recovery is unchanged.
from pydantic import StrictBool, model_validator

PreparationIdentity79 = Annotated[StrictStr, Field(min_length=1, max_length=128, pattern=r"^\S(?:.*\S)?$")]
PreparationDigest79 = Annotated[StrictStr, Field(pattern=r"^[a-f0-9]{64}$")]
PreparationCount79 = Annotated[StrictInt, Field(ge=1, le=9_007_199_254_740_990)]
PreparationKey79 = Annotated[StrictStr, Field(pattern=r"^preparation-recovery:[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$")]

class _PreparationRecovery79(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

class PreparationRecoveryStatusV2(_PreparationRecovery79):
    schema_version: Literal["research-preparation-recovery-status.v2"] = "research-preparation-recovery-status.v2"
    run_id: PreparationIdentity79
    request_id: PreparationIdentity79
    plan_id: PreparationIdentity79
    generation: PreparationCount79
    state_version: PreparationCount79
    state: Literal["queued", "prepared", "confirmed", "unconfirmed", "cancelled", "revoked"]
    reason_code: Annotated[StrictStr, Field(pattern=r"^[a-z0-9_]{1,160}$")] | None
    eligible: StrictBool
    native_sha256: PreparationDigest79 | None
    owner_native_sha256: PreparationDigest79
    source_abi: Literal["publication-source-snapshot-read.v2"] | None
    proof_scope: Literal["metadata_prechecks_only"] = "metadata_prechecks_only"
    automatic_retry: Literal[False] = False

    @model_validator(mode="after")
    def closed(self):
        if self.eligible and (self.state!="unconfirmed" or self.reason_code!="frozen_io_undeclared_authorization_resource"
                or self.native_sha256 is None or self.source_abi is None):
            raise ValueError("preparation_recovery_status_changed")
        return self

class PreparationRecoveryCommandV2(_PreparationRecovery79):
    schema_version: Literal["research-preparation-recovery-command.v2"] = "research-preparation-recovery-command.v2"
    request_id: PreparationKey79
    owner_id: PreparationIdentity79
    expected_request_id: PreparationIdentity79
    expected_plan_id: PreparationIdentity79
    expected_generation: PreparationCount79
    expected_state_version: PreparationCount79
    expected_reason: Literal["frozen_io_undeclared_authorization_resource"]
    expected_native_sha256: PreparationDigest79
    expected_owner_native_sha256: PreparationDigest79

class PreparationRecoveryReplyV2(_PreparationRecovery79):
    schema_version: Literal["research-preparation-recovery-reply.v2"] = "research-preparation-recovery-reply.v2"
    run_id: PreparationIdentity79
    request_id: PreparationIdentity79
    generation: PreparationCount79
    state_version: PreparationCount79
    state: Literal["queued"]
    report_confirmed: Literal[False]
    source_abi: Literal["publication-source-snapshot-read.v2"]
    automatic_retry: Literal[False]
    recovery_mode: Literal["diagnostic_gap_all_owned_archives.v1", "scheduled_preparation_queue_only_snapshot_ownership.v1"]
    event_history_sha256: PreparationDigest79
    archive_ownership_sha256: PreparationDigest79

class PreparationRecoveryReceiptV2(_PreparationRecovery79):
    schema_version: Literal["research-preparation-recovery-receipt.v2"] = "research-preparation-recovery-receipt.v2"
    request_id: PreparationKey79
    owner_id: PreparationIdentity79
    run_id: PreparationIdentity79
    plan_id: PreparationIdentity79
    command: PreparationRecoveryCommandV2
    result: PreparationRecoveryReplyV2
    status: Literal["accepted"]
    payload_hash: PreparationDigest79
    recorded_at: AwareDatetime

    @model_validator(mode="after")
    def matched(self):
        if (self.request_id!=self.command.request_id or self.owner_id!=self.command.owner_id
                or self.plan_id!=self.command.expected_plan_id or self.result.run_id!=self.run_id
                or self.result.request_id!=self.command.expected_request_id
                or self.result.generation!=self.command.expected_generation+1
                or self.result.state_version!=self.command.expected_state_version+1):
            raise ValueError("preparation_recovery_receipt_changed")
        return self

"""Durable, explicit full-original reads; independent of publication ACK."""
from __future__ import annotations

from typing import Annotated, Literal
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, model_validator

RESULT_BOUND = 16 * 1024 * 1024
AUDIT_TOTAL_SECONDS = 270
Identity = Annotated[StrictStr, Field(min_length=1, max_length=128, pattern=r"^\S(?:.*\S)?$")]
Digest = Annotated[StrictStr, Field(pattern=r"^[a-f0-9]{64}$")]
Key = Annotated[StrictStr, Field(pattern=r"^report-audit:[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$")]
Count = Annotated[StrictInt, Field(ge=1, le=9_007_199_254_740_991)]

class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

class ReportAuditHeadV1(_Model):
    run_id: Identity
    generation: Count
    state_version: Count
    identity_sha256: Digest
    bundle_sha256: Digest
    material_sha256: Digest
    report_sha256: Digest

class ReportAuditContextV1(_Model):
    schema_version: Literal["research-report-audit-context.v1"] = "research-report-audit-context.v1"
    head: ReportAuditHeadV1
    active_audit_id: Digest | None
    request_allowed: StrictBool
    automatic_retry: Literal[False] = False

class ReportAuditRequestV1(_Model):
    schema_version: Literal["research-report-audit-request.v1"] = "research-report-audit-request.v1"
    request_key: Key
    owner_id: Identity
    expected_head: ReportAuditHeadV1

class ReportAuditReceiptV1(_Model):
    schema_version: Literal["research-report-audit-receipt.v1"] = "research-report-audit-receipt.v1"
    audit_id: Digest
    request: ReportAuditRequestV1
    request_sha256: Digest
    accepted_at: AwareDatetime
    epoch: Digest
    status: Literal["accepted"] = "accepted"
    audit_confirmed: Literal[False] = False
    automatic_retry: Literal[False] = False

class ReportAuditStateV1(_Model):
    schema_version: Literal["research-report-audit-state.v1"] = "research-report-audit-state.v1"
    receipt: ReportAuditReceiptV1
    state: Literal["accepted", "running", "prepared", "completed", "failed", "unknown"]
    version: Count
    started_at: AwareDatetime | None
    deadline_at: AwareDatetime | None
    finished_at: AwareDatetime | None
    native_exit_observed_at: AwareDatetime | None
    native_exited: StrictBool
    reason_code: Annotated[StrictStr, Field(pattern=r"^[a-z0-9_]{1,160}$")] | None
    result_sha256: Digest | None
    result_bytes: Annotated[StrictInt, Field(ge=1, le=RESULT_BOUND)] | None
    audit_confirmed: StrictBool

    @model_validator(mode="after")
    def matched(self):
        if self.audit_confirmed != (self.state == "completed"):
            raise ValueError("report_audit_confirmation_state_changed")
        if self.native_exited != (self.native_exit_observed_at is not None):
            raise ValueError("report_audit_native_exit_changed")
        if self.state == "completed" and (not self.native_exited or self.finished_at is None
                or self.result_sha256 is None or self.result_bytes is None or self.reason_code is not None):
            raise ValueError("report_audit_result_unconfirmed")
        if (self.result_sha256 is None) != (self.result_bytes is None):
            raise ValueError("report_audit_result_metadata_changed")
        return self

class ReportAuditResultV1(_Model):
    schema_version: Literal["research-report-audit-result.v1"] = "research-report-audit-result.v1"
    audit_id: Digest
    head: ReportAuditHeadV1
    comparison: Literal["saved_bundle_full_quality"] = "saved_bundle_full_quality"
    checked_at: AwareDatetime
    quality_json: Annotated[StrictStr, Field(min_length=1, max_length=RESULT_BOUND)]

class ReportAuditResultReplyV1(_Model):
    schema_version: Literal["research-report-audit-result-reply.v1"] = "research-report-audit-result-reply.v1"
    status: ReportAuditStateV1
    result: ReportAuditResultV1

    @model_validator(mode="after")
    def matched(self):
        if (not self.status.audit_confirmed or self.result.audit_id != self.status.receipt.audit_id
                or self.result.head != self.status.receipt.request.expected_head):
            raise ValueError("report_audit_result_identity_changed")
        return self

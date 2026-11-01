"""Read a confirmed report snapshot without claiming a fresh material audit.

This data contract imports no application reader, ORM model, or private loader.
The publication owner must establish the Saved/ACK permission before issuing it.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class ConfirmedReportSnapshotV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["research-confirmed-report-snapshot.v1"] = (
        "research-confirmed-report-snapshot.v1"
    )
    read_scope: Literal["confirmed_report_snapshot"] = "confirmed_report_snapshot"
    material_audit: Literal["not_checked_this_read"] = "not_checked_this_read"
    run_id: str
    state_version: int = Field(ge=1, strict=True)
    source_bundle_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    published_at: AwareDatetime
    as_of: AwareDatetime
    report_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    report_bytes: int = Field(ge=1, strict=True)
    quality_json: str

    @model_validator(mode="after")
    def report_identity(self) -> ConfirmedReportSnapshotV1:
        # Application validation happens at the issuer boundary. These checks
        # bind the transport itself to its unchanged original report bytes.
        quality = json.loads(self.quality_json)
        decision = quality.get("decision") if isinstance(quality, dict) else None
        if (not isinstance(decision, dict) or quality.get("status") != "available"
                or quality.get("run_id") != self.run_id or decision.get("run_id") != self.run_id
                or not isinstance(decision.get("full_report_json"), str)):
            raise ValueError("report_snapshot_quality_identity_changed")
        report = decision["full_report_json"].encode("utf-8")
        if (hashlib.sha256(report).hexdigest() != self.report_sha256
                or decision.get("full_report_hash") != self.report_sha256
                or len(report) != self.report_bytes):
            raise ValueError("report_snapshot_report_bytes_changed")
        try:
            published = datetime.fromisoformat(quality["published_at"])
            read_at = datetime.fromisoformat(quality["as_of"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("report_snapshot_clock_invalid") from error
        if (published != self.published_at or read_at != self.as_of
                or self.as_of < self.published_at):
            raise ValueError("report_snapshot_clock_changed")
        return self

"""Immutable original qualification; independent append-only acceptance receipts."""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .history_observed_training import HistoryObservedContract

LOADED_SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
SHA = r"^[a-f0-9]{64}$"


class HistoryModelQualificationV3(HistoryObservedContract):
    schema_version: Literal["history-model-qualification.v3"] = "history-model-qualification.v3"
    qualification_id: str
    owner_claim: str = Field(min_length=1, max_length=128)
    request_id: str = Field(min_length=1, max_length=128)
    scope_id: str
    model_id: str
    model_hash: str = Field(pattern=SHA)
    model_original_ref: str
    model_original_hash: str = Field(pattern=SHA)
    model_known_at: datetime
    dataset_id: str
    dataset_hash: str = Field(pattern=SHA)
    dataset_original_ref: str
    dataset_original_hash: str = Field(pattern=SHA)
    data_information_asof: datetime
    source_files: tuple[tuple[str, str], ...]
    bundle_ref: str
    bundle_hash: str = Field(pattern=SHA)
    bundle_bytes: int = Field(ge=1, le=512 * 1024 * 1024)
    manifest_hash: str = Field(pattern=SHA)
    verified_at: datetime
    recorded_at: datetime
    state: Literal["prepared"] = "prepared"
    version: Literal[0] = 0
    confirmation_at: None = None
    fixture_mode: bool
    authority_scope: Literal["local_process_filesystem_access"] = "local_process_filesystem_access"
    financial_qualified: Literal[False] = False
    automatic_retry: Literal[False] = False

    @model_validator(mode="after")
    def clocks_and_sources(self):
        if (
            not self.data_information_asof
            <= self.model_known_at
            <= self.verified_at
            <= self.recorded_at
        ):
            raise ValueError("history_qualification_actual_clock_invalid")
        if not self.source_files or len(dict(self.source_files)) != len(self.source_files):
            raise ValueError("history_qualification_source_identity_invalid")
        return self


class HistoryModelQualificationAcceptanceV3(HistoryObservedContract):
    schema_version: Literal["history-model-qualification-acceptance.v3"] = (
        "history-model-qualification-acceptance.v3"
    )
    event_id: str
    qualification_id: str
    qualification_hash: str = Field(pattern=SHA)
    owner_claim: str = Field(min_length=1, max_length=128)
    request_id: str = Field(min_length=1, max_length=128)
    state: Literal["confirmed", "revoked", "unconfirmed"]
    version: int = Field(ge=1)
    confirmation_at: datetime
    recorded_at: datetime
    reason_code: str | None = Field(default=None, max_length=128)
    financial_qualified: Literal[False] = False
    automatic_retry: Literal[False] = False

    @model_validator(mode="after")
    def actual_confirmation(self):
        if self.confirmation_at > self.recorded_at:
            raise ValueError("history_qualification_confirmation_clock_invalid")
        if self.state != "confirmed" and not self.reason_code:
            raise ValueError("history_qualification_nonconfirmed_reason_required")
        return self


class HistoryQualifiedForecastBindingV3(HistoryObservedContract):
    schema_version: Literal["history-qualified-forecast-binding.v3"] = (
        "history-qualified-forecast-binding.v3"
    )
    forecast_id: str
    forecast_hash: str = Field(pattern=SHA)
    owner_claim: str
    request_id: str
    qualification_id: str
    qualification_hash: str = Field(pattern=SHA)
    acceptance_id: str
    acceptance_hash: str = Field(pattern=SHA)
    qualification_version: int = Field(ge=1)
    bundle_hash: str = Field(pattern=SHA)
    recorded_at: datetime
    financial_qualified: Literal[False] = False
    automatic_retry: Literal[False] = False


class HistoryQualifiedForecastAcceptanceV3(HistoryObservedContract):
    schema_version: Literal["history-qualified-forecast-acceptance.v3"] = (
        "history-qualified-forecast-acceptance.v3"
    )
    forecast_id: str
    forecast_hash: str = Field(pattern=SHA)
    binding_hash: str = Field(pattern=SHA)
    owner_claim: str
    confirmed_at: datetime
    recorded_at: datetime
    financial_qualified: Literal[False] = False
    automatic_retry: Literal[False] = False

    @model_validator(mode="after")
    def actual_ack_clocks(self):
        if self.confirmed_at > self.recorded_at:
            raise ValueError("history_qualified_forecast_ack_clock_invalid")
        return self

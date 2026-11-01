"""Independent fixed-window history originals, never a legacy trial authority."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from .history_observed_training import HistoryObservedContract
from .market_path import aware_utc, canonical_hash, spot_scope

RULE_VERSION = "fixed-90-day-cross-registration-closed-spot.v2"


def history_v2_id(kind: str, owner_claim: str, request_id: str) -> str:
    if (
        kind not in {"plan", "epoch", "dataset"}
        or not owner_claim.strip()
        or not request_id.strip()
    ):
        raise ValueError("history_v2_identity_invalid")
    return "hds2_" + kind + "_" + canonical_hash([kind, owner_claim, request_id])[:48]


class HistoryDatasetOriginalV2(HistoryObservedContract):
    schema_version: Literal["history-dataset-original.v2"] = "history-dataset-original.v2"
    kind: Literal["plan", "epoch", "dataset"]
    original_id: str = Field(min_length=1, max_length=96)
    owner_claim: str = Field(min_length=1, max_length=128)
    request_id: str = Field(min_length=1, max_length=128)
    plan_id: str = Field(min_length=1, max_length=96)
    rule_version: Literal["fixed-90-day-cross-registration-closed-spot.v2"] = RULE_VERSION
    known_asof: datetime
    produced_at: datetime
    recorded_at: datetime
    cutoffs: tuple[datetime, ...] = Field(min_length=90, max_length=90)
    scope_ids: tuple[str, str]
    source_files: tuple[tuple[str, str], ...]
    source_original_ref: str
    source_original_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    provenance: dict[str, Any]
    owner_authenticated: Literal[False] = False
    authority_scope: Literal["local_process_filesystem_access"] = "local_process_filesystem_access"
    financial_qualified: Literal[False] = False

    @field_validator("known_asof", "produced_at", "recorded_at")
    @classmethod
    def utc(cls, value: datetime) -> datetime:
        return aware_utc(value)

    @field_validator("cutoffs")
    @classmethod
    def utc_cutoffs(cls, values: tuple[datetime, ...]) -> tuple[datetime, ...]:
        return tuple(aware_utc(value) for value in values)

    @model_validator(mode="after")
    def fixed(self) -> HistoryDatasetOriginalV2:
        if self.original_id != history_v2_id(self.kind, self.owner_claim, self.request_id) or (
            self.kind == "plan" and self.plan_id != self.original_id
        ):
            raise ValueError("history_v2_original_request_identity_invalid")
        if self.scope_ids != (spot_scope("BTC-USDT").scope_id, spot_scope("ETH-USDT").scope_id):
            raise ValueError("history_v2_exact_two_spot_scopes_required")
        if any(value.timestamp() % 86400 for value in self.cutoffs) or any(
            right - left != timedelta(days=1)
            for left, right in zip(self.cutoffs, self.cutoffs[1:], strict=False)
        ):
            raise ValueError("history_v2_fixed_90_UTC_cutoffs_required")
        if not self.known_asof <= self.produced_at <= self.recorded_at:
            raise ValueError("history_v2_original_clock_invalid")
        if not self.source_files or len({name for name, _ in self.source_files}) != len(
            self.source_files
        ):
            raise ValueError("history_v2_source_identity_invalid")
        return self

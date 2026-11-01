"""Cross-registration history-at-fit models; no historical PIT promotion."""

from __future__ import annotations

import hashlib
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .history_observed_training import HistoryObservedContract
from .path_forecast import EmpiricalDistribution

LOADED_SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


class HistoryDatasetFitModelV2(HistoryObservedContract):
    schema_version: Literal["history-dataset-fit-model.v2"] = "history-dataset-fit-model.v2"
    method_version: Literal["BASE_EMPIRICAL_CROSSRID_V2+COND_VOL_CROSSRID_V2"] = (
        "BASE_EMPIRICAL_CROSSRID_V2+COND_VOL_CROSSRID_V2"
    )
    model_id: str
    owner_claim: str = Field(min_length=1, max_length=128)
    request_id: str = Field(min_length=1, max_length=128)
    plan_id: str
    dataset_id: str
    dataset_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    scope_id: str
    cutoffs: tuple[datetime, ...] = Field(min_length=90, max_length=90)
    data_information_asof: datetime
    dataset_recorded_at: datetime
    fit_started_at: datetime
    fit_completed_at: datetime
    model_known_at: datetime
    recorded_at: datetime
    baseline: EmpiricalDistribution
    conditional_low: EmpiricalDistribution
    conditional_high: EmpiricalDistribution
    training_vol_median: Decimal
    windows: tuple[dict, ...] = Field(min_length=90, max_length=90)
    source_files: tuple[tuple[str, str], ...]
    source_original_ref: str
    source_original_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    dataset_original_ref: str
    dataset_original_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    owner_authenticated: Literal[False] = False
    authority_scope: Literal["local_process_filesystem_access"] = "local_process_filesystem_access"
    financial_qualified: Literal[False] = False

    @model_validator(mode="after")
    def gates(self):
        if (
            not self.data_information_asof
            <= self.dataset_recorded_at
            <= self.fit_started_at
            <= self.fit_completed_at
            <= self.model_known_at
            == self.recorded_at
        ):
            raise ValueError("history_v2_fit_actual_clock_invalid")
        if (
            self.baseline.n != self.conditional_low.n + self.conditional_high.n
            or self.baseline.n < 60
            or min(self.conditional_low.n, self.conditional_high.n) < 30
        ):
            raise ValueError("history_v2_fit_60_30_30_gate_not_met")
        if not self.training_vol_median.is_finite() or self.training_vol_median < 0:
            raise ValueError("history_v2_fit_vol_median_invalid")
        return self

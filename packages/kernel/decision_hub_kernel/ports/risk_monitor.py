"""Typed, deterministic monitor input. It conveys no trading permission."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal, Protocol

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class RiskMetricV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    fn: Literal["abs", "identity"] = "abs"
    metric: Literal["close_return"] = "close_return"
    window_seconds: Annotated[int, Field(ge=60, le=86400, multiple_of=60)] = 1800
    unit: Literal["fraction"] = "fraction"


class RiskComparisonV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    op: Literal["gte", "gt", "lte", "lt"] = "gte"
    left: RiskMetricV1
    right: Annotated[Decimal, Field(ge=-1, le=1, allow_inf_nan=False)]


class RiskConditionV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["risk-condition.v1"] = "risk-condition.v1"
    condition_id: Annotated[str, Field(min_length=1, max_length=128)]
    scope_id: Annotated[str, Field(min_length=1, max_length=192)]
    expression: RiskComparisonV1
    evaluation_seconds: Annotated[int, Field(ge=60, le=3600, multiple_of=60)] = 60
    stale_after_seconds: Annotated[int, Field(ge=60, le=3600)] = 180
    enter_confirm_bars: Annotated[int, Field(ge=1, le=60)] = 1
    exit_confirm_bars: Annotated[int, Field(ge=1, le=60)] = 2
    deep_review_cooldown_seconds: Annotated[int, Field(ge=60, le=86400)] = 1800
    severity: Literal["elevated", "high"] = "elevated"


class RiskMonitorInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    scope_id: str
    as_of: AwareDatetime
    data_as_of: AwareDatetime | None
    window_seconds: int
    close_return: Annotated[Decimal | None, Field(allow_inf_nan=False)]
    data_state: Literal["fresh", "stale", "unknown", "error"]
    reason: str | None = None
    input_ref: str
    input_hash: Annotated[str, Field(pattern="^[a-f0-9]{64}$")]
    manifest_hash: Annotated[str, Field(pattern="^[a-f0-9]{64}$")]


class RiskSnapshotPort(Protocol):
    async def snapshot(self, scope_id: str, *, as_of: datetime) -> RiskMonitorInput: ...

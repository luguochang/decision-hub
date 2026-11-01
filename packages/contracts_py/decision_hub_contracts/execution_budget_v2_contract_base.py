"""Only the additive V2 wire relation; no execution permission or clock extension."""

from __future__ import annotations

from pydantic import BaseModel, model_validator


class ExecutionBudgetV2Contract(BaseModel):
    @model_validator(mode="before")
    @classmethod
    def validate_reserve(cls, value):
        if isinstance(value, BaseModel):
            value = value.model_dump(mode="python", by_alias=True)
        if not isinstance(value, dict):
            raise ValueError("research_closing_reserve_invalid")
        reserve = value.get("closing_reserve_seconds")
        total = value.get("total_deadline_seconds")
        if type(reserve) is not int or type(total) is not int or not 1 <= reserve < total:
            raise ValueError("research_closing_reserve_invalid")
        return value

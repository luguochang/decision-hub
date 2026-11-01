"""Strict metadata relations for the independent saved-forecast index."""

import re
from datetime import datetime, timedelta

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

UTC_TEXT = re.compile(r"^(?!0000)\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)$")


class HistoryForecastIndexContractV2(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    @field_validator(
        "owner_authenticated",
        "current_originals_verified",
        "financial_qualified",
        "historical_pit_verified",
        mode="before",
        check_fields=False,
    )
    @classmethod
    def exact_boolean(cls, value):
        if type(value) is not bool:
            raise ValueError("history_forecast_index_exact_boolean_required")
        return value

    @field_validator("observed_at", "cutoff_at", "recorded_at", mode="before", check_fields=False)
    @classmethod
    def exact_utc_text(cls, value):
        if isinstance(value, str):
            if UTC_TEXT.fullmatch(value) is None:
                raise ValueError("history_forecast_index_utc_timestamp_required")
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value

    @field_validator("observed_at", "cutoff_at", "recorded_at", check_fields=False)
    @classmethod
    def exact_utc_clock(cls, value):
        if value is not None and value.utcoffset() != timedelta(0):
            raise ValueError("history_forecast_index_utc_clock_required")
        return value

    @model_validator(mode="after")
    def metadata_relations(self):
        if "state" in type(self).model_fields:
            values = (self.forecast_id, self.cutoff_at, self.recorded_at)
            if self.state == "not_granted":
                valid = self.model_id is None and all(value is None for value in values)
            elif self.state == "not_generated":
                valid = self.model_id is not None and all(value is None for value in values)
            else:
                valid = self.model_id is not None and all(value is not None for value in values)
                if valid:
                    valid = (
                        self.cutoff_at.second == self.cutoff_at.microsecond == 0
                        and self.cutoff_at <= self.recorded_at
                    )
            if not valid:
                raise ValueError("history_forecast_index_state_identity_or_clock_invalid")
        else:
            if any(
                item.recorded_at is not None and item.recorded_at > self.observed_at
                for item in self.scopes
            ):
                raise ValueError("history_forecast_index_future_record_invalid")
        return self

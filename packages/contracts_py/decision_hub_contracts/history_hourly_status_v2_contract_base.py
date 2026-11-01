"""Independent strict, immutable base for hourly metadata status contracts."""

from pydantic import BaseModel, ConfigDict, field_validator


class HistoryHourlyContractV2(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    @field_validator(
        "owner_authenticated",
        "automatic_retry",
        "current_forecast_originals_verified",
        "financial_qualified",
        "historical_pit_verified",
        mode="before",
        check_fields=False,
    )
    @classmethod
    def literal_boolean_type(cls, value: bool) -> bool:
        if type(value) is not bool:
            raise ValueError("history_hourly_boolean_literal_type_required")
        return value

    @field_validator("cadence_seconds", mode="before", check_fields=False)
    @classmethod
    def literal_integer_type(cls, value: int) -> int:
        if type(value) is not int:
            raise ValueError("history_hourly_integer_literal_type_required")
        return value

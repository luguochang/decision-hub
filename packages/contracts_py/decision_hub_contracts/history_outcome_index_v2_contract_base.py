"""Exact metadata index relations; this index conveys no original qualification."""

import re
from datetime import datetime, timedelta

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

UTC = re.compile(r"^(?!0000)\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)$")
CLOCKS = frozenset(
    (
        "actual_as_of",
        "prepared_registered_at",
        "independently_confirmed_registered_at",
        "first_hour",
        "last_hour",
    )
)


class HistoryOutcomeIndexContractBaseV2(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    @field_validator("*", mode="before")
    @classmethod
    def clocks(cls, value, info):
        if info.field_name in CLOCKS and isinstance(value, str):
            if UTC.fullmatch(value) is None:
                raise ValueError("history_outcome_index_exact_utc_required")
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        if (
            info.field_name
            in (
                "owner_authenticated",
                "current_originals_verified",
                "financial_qualified",
                "promotion_allowed",
                "fixture_mode",
            )
            and type(value) is not bool
        ):
            raise ValueError("history_outcome_index_exact_boolean_required")
        return value

    @field_validator("*", mode="after")
    @classmethod
    def utc(cls, value, info):
        if info.field_name in CLOCKS and value is not None and value.utcoffset() != timedelta(0):
            raise ValueError("history_outcome_index_utc_required")
        return value

    @model_validator(mode="after")
    def relation(self):
        value = self.model_dump(mode="json")
        if "protocol_id" in value:
            if (
                self.last_hour != self.first_hour + timedelta(hours=23)
                or self.first_hour.minute
                or self.first_hour.second
                or self.first_hour.microsecond
                or self.prepared_registered_at >= self.first_hour
                or (
                    (self.state == "confirmed")
                    != (self.independently_confirmed_registered_at is not None)
                )
                or (
                    self.independently_confirmed_registered_at is not None
                    and not self.prepared_registered_at
                    <= self.independently_confirmed_registered_at
                    < self.first_hour
                )
            ):
                raise ValueError("history_outcome_index_protocol_clock_invalid")
        if "evaluations" in value:
            rows = self.evaluations
            if (
                len({row.protocol_id for row in rows}) != len(rows)
                or list(rows)
                != sorted(rows, key=lambda row: (-row.first_hour.timestamp(), row.protocol_id))
                or any(
                    row.prepared_registered_at > self.actual_as_of
                    or (
                        row.independently_confirmed_registered_at is not None
                        and row.independently_confirmed_registered_at > self.actual_as_of
                    )
                    for row in rows
                )
            ):
                raise ValueError("history_outcome_index_order_or_clock_invalid")
        return self

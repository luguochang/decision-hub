"""Validate untouched canonical wire shape before DTO date conversion; no authority."""

from __future__ import annotations

import math
from datetime import datetime
from functools import lru_cache
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import BaseModel, ConfigDict, model_validator


def _wire(value: object) -> object:
    if isinstance(value, BaseModel):
        return _wire(value.model_dump(mode="python"))
    if isinstance(value, datetime):
        if value.utcoffset() is None:
            raise ValueError("provider_input_datetime_timezone_required")
        return value.isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("provider_input_nonfinite_number")
    if isinstance(value, dict):
        return {key: _wire(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_wire(item) for item in value]
    return value


def provider_input_format_checker() -> FormatChecker:
    checker = FormatChecker()

    @checker.checks("date-time")
    def aware_calendar_time(value: object) -> bool:
        if not isinstance(value, str):
            return False
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).utcoffset() is not None
        except ValueError:
            return False

    return checker


@lru_cache(maxsize=1)
def _canonical_validators() -> dict[str, Draft202012Validator]:
    path = Path(__file__).resolve().parents[3] / "contracts/schemas/provider_input.schema.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    return {
        value["title"]: Draft202012Validator(
            {"$defs": document["$defs"], "$ref": f"#/$defs/{name}"},
            format_checker=provider_input_format_checker(),
        )
        for name, value in document["$defs"].items()
    }


class ProviderInputContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="before")
    @classmethod
    def canonical_wire_shape(cls, value: object) -> object:
        validator = _canonical_validators().get(cls.__name__)
        if validator is None:
            raise ValueError("provider_input_canonical_definition_required")
        error = next(validator.iter_errors(_wire(value)), None)
        if error is not None:
            # Do not put input contents or source credentials in the reason.
            path = ".".join(str(item) for item in error.absolute_path)
            raise ValueError(f"provider_input_shape_invalid:{error.validator}:{path}")
        return value

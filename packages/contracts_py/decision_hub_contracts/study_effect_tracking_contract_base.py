"""Validate effect tracking declarations against their local canonical schema."""
from __future__ import annotations

import json
import math
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from pydantic import BaseModel, model_validator

from .policy_contract_base import policy_format_checker


def _json_input(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.utcoffset() is None:
            raise ValueError("effect_tracking_datetime_must_be_aware")
        return value.isoformat()
    if isinstance(value, BaseModel):
        return _json_input(value.model_dump(mode="python"))
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("effect_tracking_non_string_key")
        return {key: _json_input(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_input(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("effect_tracking_non_finite_number")
    return value


@lru_cache(maxsize=8)
def _validator(name: str) -> Draft202012Validator:
    source = Path(__file__).resolve().parents[3] / "contracts/schemas/study_effect_tracking.schema.yaml"
    canonical = json.loads(source.read_text(encoding="utf-8"))
    definitions = [key for key, schema in canonical["$defs"].items() if schema.get("title") == name]
    if len(definitions) != 1:
        raise ValueError("effect_tracking_contract_definition_invalid")
    return Draft202012Validator({**canonical, "$ref": "#/$defs/" + definitions[0]}, format_checker=policy_format_checker())


class StudyEffectTrackingContract(BaseModel):
    @model_validator(mode="before")
    @classmethod
    def _strict_shape(cls, value: Any) -> Any:
        try:
            _validator(cls.__name__).validate(_json_input(value))
        except JsonSchemaValidationError as error:
            raise ValueError("effect_tracking_contract_invalid") from error
        return value

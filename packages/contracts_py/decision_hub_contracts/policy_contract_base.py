"""Strict input shape for new policy contracts; existing DTOs keep their base.

Pydantic Literal[True] also accepts integer 1. Validate the generated JSON
schema before coercion; this base contains no handwritten contract mirror.
"""

from __future__ import annotations

import json
import math
import re
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from pydantic import BaseModel, model_validator


def policy_format_checker() -> FormatChecker:
    """JSONschema's optional RFC3339 dependency may be absent in production."""
    checker = FormatChecker()

    @checker.checks("date-time")
    def aware_datetime(value: Any) -> bool:
        if not isinstance(value, str):
            return True  # JSONschema's type keyword handles other types.
        if (
            re.fullmatch(
                r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
            r"(?:\.[0-9]+)?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])",
                value,
            )
            is None
        ):
            return False
        try:
            return datetime.fromisoformat(value).utcoffset() is not None
        except ValueError:
            return False

    return checker


def _json_input(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.utcoffset() is None:
            raise ValueError("research_policy_datetime_must_be_aware")
        return value.isoformat()
    if isinstance(value, BaseModel):
        return _json_input(value.model_dump(mode="json"))
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("research_policy_non_string_key")
        return {key: _json_input(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_input(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("research_policy_non_finite_number")
    return value


@lru_cache(maxsize=128)
def _validator(model: type[BaseModel]) -> Draft202012Validator:
    canonical = _canonical_schema()
    key = next(
        (
            key
            for key, definition in canonical["$defs"].items()
            if (definition.get("title") or "".join(part.title() for part in key.split("_")))
            == model.__name__
        ),
        None,
    )
    # Generated params classes have no canonical title. The parent public DTO
    # still checks the complete original definition, including uniqueItems.
    schema = (
        model.model_json_schema()
        if key is None
        else {"$ref": f"#/$defs/{key}", "$defs": canonical["$defs"]}
    )
    return Draft202012Validator(schema, format_checker=policy_format_checker())


@lru_cache(maxsize=1)
def _canonical_schema() -> dict[str, Any]:
    path = Path(__file__).resolve().parents[3] / "contracts/schemas/research_policy.schema.yaml"
    with path.open("rb") as stream:
        raw = stream.read(65_537)
    if len(raw) > 65_536:
        raise ValueError("research_policy_canonical_schema_size_invalid")
    # This canonical source is JSON (also valid YAML); no copied constraint map.
    return json.loads(raw.decode("utf-8"))


class ResearchPolicyContract(BaseModel):
    @model_validator(mode="before")
    @classmethod
    def _strict_shape(cls, value: Any) -> Any:
        try:
            _validator(cls).validate(_json_input(value))
        except JsonSchemaValidationError as exc:
            raise ValueError("research_policy_contract_invalid") from exc
        return value

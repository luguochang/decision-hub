"""Validate the new receipt against its unchanged canonical dependency."""
from __future__ import annotations

import json
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from pydantic import BaseModel, model_validator
from referencing import Registry, Resource

from .policy_contract_base import policy_format_checker


def _json_input(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.utcoffset() is None:
            raise ValueError("read_command_receipt_datetime_must_be_aware")
        return value.isoformat()
    if isinstance(value, BaseModel):
        return _json_input(value.model_dump(mode="json"))
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("read_command_receipt_non_string_key")
        return {key: _json_input(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_input(item) for item in value]
    return value


@lru_cache(maxsize=16)
def _validator(model: type[BaseModel]) -> Draft202012Validator:
    directory = Path(__file__).resolve().parents[3] / "contracts/schemas"
    canonical = json.loads((directory / "read_command_receipt.schema.yaml").read_text("utf-8"))
    dependency = json.loads((directory / "product_delivery.schema.yaml").read_text("utf-8"))
    # Use local registered resources only. External refs keep the canonical
    # dependency unchanged and never trigger a network schema lookup.
    dependency_uri = canonical["$id"].rsplit("/", 1)[0] + "/product_delivery.schema.yaml"
    registry = Registry().with_resources([
        (dependency_uri, Resource.from_contents(dependency)),
        (dependency["$id"], Resource.from_contents(dependency)),
    ])
    if model.__name__ == "ReadCommandReceipt":
        schema = {**canonical, "$ref": "#/$defs/read_command_receipt"}
    else:
        # Nested generated models still receive strict scalar types; the
        # public receipt validates the complete original canonical shape.
        schema = model.model_json_schema()
    return Draft202012Validator(schema, registry=registry, format_checker=policy_format_checker())


class ReadCommandReceiptContract(BaseModel):
    @model_validator(mode="before")
    @classmethod
    def _strict_shape(cls, value: Any) -> Any:
        try:
            _validator(cls).validate(_json_input(value))
        except JsonSchemaValidationError as exc:
            raise ValueError("read_command_receipt_contract_invalid") from exc
        return value

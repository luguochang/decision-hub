"""Strict new execution declarations from allowlisted canonical sources.

Shape validity is not persisted provenance, checker execution, or permission.
Historical contract bases and generated leaves remain unchanged.
"""

from __future__ import annotations

import math
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from pydantic import BaseModel, ConfigDict, model_validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from .binding_contract_base import (
    _deny_retrieve,  # pyright: ignore[reportPrivateUsage] # Same canonical no-I/O retriever.
    _validate_shape,  # pyright: ignore[reportPrivateUsage] # Pure shape only.
)
from .policy_contract_base import policy_format_checker

SCHEMA_FILES = (
    "research_execution_binding.schema.yaml",
    "research_policy.schema.yaml",
    "agentic_research.schema.yaml",
    "dsh_host_bridge.schema.yaml",
    "research_fact.schema.yaml",
)
MAX_SCHEMA_BYTES = 262_144


def _json_input(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.utcoffset() is None:
            raise ValueError("research_execution_datetime_must_be_aware")
        return value.isoformat()
    if isinstance(value, BaseModel):
        return _json_input(value.model_dump(mode="json"))
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("research_execution_non_string_key")
        return {key: _json_input(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_input(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("research_execution_non_finite_number")
    return value


@lru_cache(maxsize=1)
def execution_schema_registry() -> tuple[dict[str, dict[str, Any]], Registry[Any]]:
    """Read only this fixed local graph; unknown refs never trigger IO."""
    root = Path(__file__).resolve().parents[3] / "contracts/schemas"
    documents: dict[str, dict[str, Any]] = {}
    registry: Registry[Any] = Registry(retrieve=_deny_retrieve)
    for name in SCHEMA_FILES:
        with (root / name).open("rb") as stream:
            raw = stream.read(MAX_SCHEMA_BYTES + 1)
        if len(raw) > MAX_SCHEMA_BYTES:
            raise ValueError("research_execution_canonical_schema_size_invalid")
        document = yaml.safe_load(raw.decode("utf-8", errors="strict"))
        Draft202012Validator.check_schema(document)
        documents[name] = document
        resource = Resource.from_contents(document, default_specification=DRAFT202012)
        registry = registry.with_resource(document["$id"], resource).with_resource(
            urljoin(document["$id"], name), resource
        )
    return documents, registry


def execution_binding_validator(definition: str) -> Draft202012Validator:
    documents, registry = execution_schema_registry()
    schema = documents[SCHEMA_FILES[0]]
    return Draft202012Validator(
        {"$ref": f"{schema['$id']}#/$defs/{definition}"},
        registry=registry,
        format_checker=policy_format_checker(),
    )


@lru_cache(maxsize=128)
def _validator(model: type[BaseModel]) -> Draft202012Validator:
    documents, registry = execution_schema_registry()
    for document in documents.values():
        for key, definition in document.get("$defs", {}).items():
            title = definition.get("title") or "".join(part.title() for part in key.split("_"))
            if title == model.__name__:
                return Draft202012Validator(
                    {"$ref": f"{document['$id']}#/$defs/{key}"},
                    registry=registry,
                    format_checker=policy_format_checker(),
                )
    # Anonymous generated fields remain strictly checked by their parent.
    return Draft202012Validator(model.model_json_schema(), format_checker=policy_format_checker())


class ResearchExecutionContract(BaseModel):
    model_config = ConfigDict(revalidate_instances="always")

    @model_validator(mode="before")
    @classmethod
    def _strict_shape(cls, value: Any) -> Any:
        projected = _json_input(value)
        try:
            _validate_shape(cls, _validator(cls), projected)
        except JsonSchemaValidationError as exc:
            raise ValueError("research_execution_contract_invalid") from exc
        return projected

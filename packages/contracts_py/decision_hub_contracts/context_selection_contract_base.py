"""Strict new time declarations; old bases and raw request contracts are unchanged.

Canonical shape, exact cross-field time relations, and Python canonical bytes
are separate checks. None authenticates SQL rows, a clock sample, or permission.
"""

from __future__ import annotations

import math
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import yaml
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from pydantic import BaseModel, ConfigDict, model_validator
from referencing import Registry, Resource
from referencing.exceptions import NoSuchResource
from referencing.jsonschema import DRAFT202012

from .context_selection_semantics import (
    canonical_payload_size,
    context_selection_time_issue_codes,
    timestamp_shape_valid,
)

SCHEMA_FILES = (
    "research_context_selection.schema.yaml",
    "research_execution_binding.schema.yaml",
    "research_policy.schema.yaml",
    "agentic_research.schema.yaml",
    "dsh_host_bridge.schema.yaml",
    "research_fact.schema.yaml",
)
MAX_SCHEMA_BYTES = 262_144
TOP_DEFINITIONS = {
    "research-context-selection.v1": "research_context_selection_receipt_v1",
    "research-context-selection-execution-link.v1": "research_context_selection_execution_link_v1",
}


def _json_input(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.utcoffset() is None:
            raise ValueError("context_selection_invalid_timestamp")
        return value.isoformat()
    if isinstance(value, BaseModel):
        return _json_input(value.model_dump(mode="python"))
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("context_selection_non_string_key")
        return {key: _json_input(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_input(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("context_selection_non_finite_number")
    return value


def context_selection_format_checker() -> FormatChecker:
    checker = FormatChecker()

    @checker.checks("date-time")
    def aware_gregorian(value: Any) -> bool:
        return not isinstance(value, str) or timestamp_shape_valid(value)

    return checker


def _deny_retrieve(uri: str) -> Resource[Any]:
    raise NoSuchResource(ref=uri)


@lru_cache(maxsize=1)
def context_selection_schema_registry() -> tuple[dict[str, dict[str, Any]], Registry[Any]]:
    root = Path(__file__).resolve().parents[3] / "contracts/schemas"
    documents: dict[str, dict[str, Any]] = {}
    registry: Registry[Any] = Registry(retrieve=_deny_retrieve)
    for name in SCHEMA_FILES:
        with (root / name).open("rb") as stream:
            raw = stream.read(MAX_SCHEMA_BYTES + 1)
        if len(raw) > MAX_SCHEMA_BYTES:
            raise ValueError("context_selection_canonical_schema_size_invalid")
        document = yaml.safe_load(raw.decode("utf-8", errors="strict"))
        Draft202012Validator.check_schema(document)
        documents[name] = document
        resource = Resource.from_contents(document, default_specification=DRAFT202012)
        registry = registry.with_resource(document["$id"], resource).with_resource(
            urljoin(document["$id"], name), resource
        )
    return documents, registry


def context_selection_validator(definition: str) -> Draft202012Validator:
    documents, registry = context_selection_schema_registry()
    schema = documents[SCHEMA_FILES[0]]
    return Draft202012Validator(
        {"$ref": f"{schema['$id']}#/$defs/{definition}"},
        registry=registry,
        format_checker=context_selection_format_checker(),
    )


@lru_cache(maxsize=128)
def _validator(model: type[BaseModel]) -> Draft202012Validator:
    documents, registry = context_selection_schema_registry()
    for document in documents.values():
        for key, definition in document.get("$defs", {}).items():
            title = definition.get("title") or "".join(part.title() for part in key.split("_"))
            if title == model.__name__:
                return Draft202012Validator(
                    {"$ref": f"{document['$id']}#/$defs/{key}"},
                    registry=registry,
                    format_checker=context_selection_format_checker(),
                )
    return Draft202012Validator(
        model.model_json_schema(), format_checker=context_selection_format_checker()
    )


class ResearchContextSelectionContract(BaseModel):
    model_config = ConfigDict(revalidate_instances="always")

    @model_validator(mode="before")
    @classmethod
    def _strict_shape(cls, value: Any) -> Any:
        projected = _json_input(value)
        try:
            _validator(cls).validate(projected)
        except JsonSchemaValidationError as exc:
            raise ValueError("context_selection_contract_invalid") from exc
        if isinstance(projected, dict) and projected.get("schema_version") in TOP_DEFINITIONS:
            issues = context_selection_time_issue_codes(projected)
            if issues:
                raise ValueError(issues[0])
            documents, _ = context_selection_schema_registry()
            definition = TOP_DEFINITIONS[projected["schema_version"]]
            limit = documents[SCHEMA_FILES[0]]["$defs"][definition]["x-max-canonical-utf8-bytes"]
            try:
                canonical_payload_size(projected, limit=limit)
            except ValueError as exc:
                if (
                    str(exc) == "context_selection_payload_too_large"
                    and definition == "research_context_selection_execution_link_v1"
                ):
                    raise ValueError("context_selection_link_payload_too_large") from exc
                raise
        return projected

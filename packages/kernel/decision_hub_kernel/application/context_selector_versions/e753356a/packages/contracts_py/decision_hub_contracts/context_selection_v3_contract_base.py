"""Strict V3 selection declarations, without clock, sorting or SQL authority.

The unchanged V1 helper supplies only identical integer time formulas. Its
schema discriminator is adapted on a shallow temporary mapping, never on the
real V3 declaration or its self-hash namespace. The raw before-input byte cap
does not bound a future default-filled, datetime-projected stored payload.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
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
    ContextSelectionSemanticError,
    canonical_payload_size,
    context_selection_time_issue_codes,
    parse_rfc3339_microseconds,
    timestamp_shape_valid,
)

SCHEMA_VERSION = "research-context-selection.v3"
TOP_DEFINITION = "research_context_selection_receipt_v3"
SCHEMA_FILES = (
    "research_context_selection_v3.schema.yaml",
    "research_context_selection_v2.schema.yaml",
    "research_context_selection.schema.yaml",
    "research_execution_binding.schema.yaml",
    "research_policy.schema.yaml",
    "agentic_research.schema.yaml",
    "dsh_host_bridge.schema.yaml",
    "research_fact.schema.yaml",
)
MAX_SCHEMA_BYTES = 262_144


def context_selection_v3_time_issue_codes(value: Mapping[str, Any]) -> tuple[str, ...]:
    """Check V3 time declarations only; do not validate or hash the V1 adapter."""
    if value.get("schema_version") != SCHEMA_VERSION:
        return ("context_selection_v3_schema_invalid",)
    issues = list(
        context_selection_time_issue_codes(
            {**value, "schema_version": "research-context-selection.v1"}
        )
    )
    prior = value.get("decision_basis_ref")
    current_round = value.get("graph_round")
    if current_round == 1:
        if prior is not None:
            issues.append("context_selection_initial_prior_unexpected")
    elif not isinstance(prior, Mapping):
        issues.append("context_selection_graph_prior_required")
    else:
        row = prior.get("row")
        if (
            prior.get("kind") != "graph_round_admission"
            or prior.get("run_id") != value.get("run_id")
            or not isinstance(current_round, int)
            or prior.get("graph_round") != current_round - 1
            or not isinstance(row, Mapping)
            or row.get("role") != f"research_graph_round:{current_round - 1}"
            or row.get("schema_version") != "research-graph-round-admission.v1"
        ):
            issues.append("context_selection_graph_prior_identity_mismatch")
        else:
            try:
                if parse_rfc3339_microseconds(row.get("recorded_at")) > parse_rfc3339_microseconds(
                    value.get("recorded_at")
                ):
                    issues.append("context_selection_graph_prior_after_selection")
            except ContextSelectionSemanticError as exc:
                issues.append(exc.code)
    return tuple(dict.fromkeys(issues))


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


def _format_checker() -> FormatChecker:
    checker = FormatChecker()

    @checker.checks("date-time")
    def aware_gregorian(value: Any) -> bool:
        return not isinstance(value, str) or timestamp_shape_valid(value)

    return checker


def _deny_retrieve(uri: str) -> Resource[Any]:
    raise NoSuchResource(ref=uri)


@lru_cache(maxsize=1)
def context_selection_v3_schema_registry() -> tuple[dict[str, dict[str, Any]], Registry[Any]]:
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


def context_selection_v3_validator(definition: str = TOP_DEFINITION) -> Draft202012Validator:
    documents, registry = context_selection_v3_schema_registry()
    schema = documents[SCHEMA_FILES[0]]
    return Draft202012Validator(
        {"$ref": f"{schema['$id']}#/$defs/{definition}"},
        registry=registry,
        format_checker=_format_checker(),
    )


@lru_cache(maxsize=128)
def _validator(model: type[BaseModel]) -> Draft202012Validator:
    documents, registry = context_selection_v3_schema_registry()
    for document in documents.values():
        for key, definition in document.get("$defs", {}).items():
            title = definition.get("title") or "".join(part.title() for part in key.split("_"))
            if title == model.__name__:
                return Draft202012Validator(
                    {"$ref": f"{document['$id']}#/$defs/{key}"},
                    registry=registry,
                    format_checker=_format_checker(),
                )
    raise ValueError("context_selection_v3_definition_unknown")


class ResearchContextSelectionV3Contract(BaseModel):
    model_config = ConfigDict(revalidate_instances="always")

    @model_validator(mode="before")
    @classmethod
    def _strict_shape(cls, value: Any) -> Any:
        projected = _json_input(value)
        try:
            _validator(cls).validate(projected)
        except JsonSchemaValidationError as exc:
            raise ValueError("context_selection_contract_invalid") from exc
        if isinstance(projected, dict) and projected.get("schema_version") == SCHEMA_VERSION:
            issues = context_selection_v3_time_issue_codes(projected)
            if issues:
                raise ValueError(issues[0])
            documents, _ = context_selection_v3_schema_registry()
            limit = documents[SCHEMA_FILES[0]]["$defs"][TOP_DEFINITION][
                "x-max-canonical-utf8-bytes"
            ]
            canonical_payload_size(projected, limit=limit)
        return projected

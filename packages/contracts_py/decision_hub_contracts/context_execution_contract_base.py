"""Strict new selection/execution declarations, not producer or permission proof."""

from __future__ import annotations

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
from .binding_contract_base import (
    context_selection_shape_format_checker as context_selection_format_checker,
)
from .context_selection_semantics import canonical_payload_size, context_selection_time_issue_codes
from .execution_contract_base import _json_input  # pyright: ignore[reportPrivateUsage]

SCHEMA_FILES = (
    "research_context_execution.schema.yaml",
    "research_execution_binding.schema.yaml",
    "research_context_selection.schema.yaml",
    "research_policy.schema.yaml",
    "agentic_research.schema.yaml",
    "dsh_host_bridge.schema.yaml",
    "research_fact.schema.yaml",
)


@lru_cache(maxsize=1)
def context_execution_registry() -> tuple[dict[str, dict[str, Any]], Registry[Any]]:
    root = Path(__file__).resolve().parents[3] / "contracts/schemas"
    documents = {}
    registry: Registry[Any] = Registry(retrieve=_deny_retrieve)
    for name in SCHEMA_FILES:
        with (root / name).open("rb") as stream:
            raw = stream.read(262_145)
        if len(raw) > 262_144:
            raise ValueError("context_execution_canonical_schema_size_invalid")
        document = yaml.safe_load(raw.decode("utf-8", errors="strict"))
        Draft202012Validator.check_schema(document)
        documents[name] = document
        resource = Resource.from_contents(document, default_specification=DRAFT202012)
        registry = registry.with_resource(document["$id"], resource).with_resource(
            urljoin(document["$id"], name), resource
        )
    return documents, registry


@lru_cache(maxsize=128)
def _validator(model: type[BaseModel]) -> Draft202012Validator:
    documents, registry = context_execution_registry()
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


class ResearchContextExecutionContract(BaseModel):
    model_config = ConfigDict(revalidate_instances="always")

    @model_validator(mode="before")
    @classmethod
    def _strict_shape(cls, value: Any) -> Any:
        projected = _json_input(value)
        try:
            _validate_shape(cls, _validator(cls), projected)
        except JsonSchemaValidationError as exc:
            raise ValueError("context_execution_contract_invalid") from exc
        if isinstance(projected, dict) and projected.get("schema_version") == (
            "research-context-selection-execution-link.v2"
        ):
            # Only identical pure time formulas see V1; real V2 bytes/hash do not.
            issues = context_selection_time_issue_codes(
                {**projected, "schema_version": "research-context-selection-execution-link.v1"}
            )
            if issues:
                raise ValueError(issues[0])
            canonical_payload_size(projected, limit=16_384)
        return projected

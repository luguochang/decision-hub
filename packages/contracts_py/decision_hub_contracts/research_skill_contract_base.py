"""Strict canonical skill shapes. Validation confers no execution or trust."""

from __future__ import annotations

from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import BaseModel, ConfigDict, field_validator, model_validator
from referencing import Registry, Resource

from .research_signal_contract_base import _signal_wire


@lru_cache(maxsize=1)
def validators():
    directory = Path(__file__).resolve().parents[3] / "contracts/schemas"

    def no_remote(uri):
        raise ValueError("skill_schema_remote_forbidden:" + uri)

    registry = Registry(retrieve=no_remote)
    documents = {}
    for path in directory.glob("*.yaml"):
        body = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(body, dict) or "$schema" not in body:
            continue
        documents[path.name] = body
        resource = Resource.from_contents(body)
        for key in (path.name, path.as_uri(), body.get("$id")):
            if key:
                registry = registry.with_resource(key, resource)
    source = directory / "research_skill.schema.yaml"
    document = documents[source.name]
    return {
        definition.get("title", "".join(part.capitalize() for part in key.split("_")) if key.startswith("research_method_") else key): Draft202012Validator(
            {"$id": source.as_uri(), "$defs": document["$defs"], "$ref": f"#/$defs/{key}"},
            registry=registry,
            format_checker=FormatChecker(),
        )
        for key, definition in document["$defs"].items()
    }


def validate_shape(name, value):
    wire = _signal_wire(value)
    error = next(validators()[name].iter_errors(wire), None)
    if error is not None:
        path = ".".join(map(str, error.absolute_path))
        raise ValueError(f"skill_shape_invalid:{error.validator}:{path}")
    # This also covers free JSON in tool input. bool is never accepted as an
    # integer by canonical validation; all numbers must remain finite.
    from .decision_product_contract_base import _wire

    _wire(wire)
    if name in {"ResearchSkillGenerationBindingV1", "ResearchSkillGenerationBindingV2"}:
        from .research_skill_generation_contract_relations import validate_generation_binding_relations
        validate_generation_binding_relations(wire)
    if name.startswith("ResearchMethod"):
        from .research_method_contract_relations import validate_method_relations
        validate_method_relations(name, wire)
    return value


class ResearchSkillContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    @model_validator(mode="before")
    @classmethod
    def canonical_shape(cls, value):
        name = next((item.__name__ for item in cls.__mro__ if item.__name__ in validators()), None)
        if name is not None:
            validate_shape(name, value)
        return value

    @field_validator("*", mode="after")
    @classmethod
    def own_times(cls, value):
        return value.astimezone(UTC) if isinstance(value, datetime) else value

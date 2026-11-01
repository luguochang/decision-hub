"""Canonical shapes and original relation checks; these do not grant trust.

Artifact integration draft. Generated models and their normal source registration
are still pending; this file is not installed in the running product.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from functools import lru_cache
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import BaseModel, ConfigDict, model_validator
from referencing import Registry, Resource


def _wire(value):
    if isinstance(value, BaseModel):
        return _wire(value.model_dump(mode="python"))
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("learning_aware_time_required")
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _wire(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_wire(item) for item in value]
    return value


def _hash(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


@lru_cache(maxsize=1)
def _validators():
    directory = Path(__file__).resolve().parents[3] / "contracts/schemas"

    def no_remote(uri):
        raise ValueError("learning_schema_remote_forbidden:" + uri)

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
    source = directory / "research_learning.schema.yaml"
    document = documents[source.name]
    return {
        definition.get("title", key): Draft202012Validator(
            {"$id": source.as_uri(), "$defs": document["$defs"], "$ref": f"#/$defs/{key}"},
            registry=registry,
            format_checker=FormatChecker(),
        )
        for key, definition in document["$defs"].items()
    }


class ResearchLearningContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    @model_validator(mode="before")
    @classmethod
    def canonical_shape(cls, value):
        name = next((item.__name__ for item in cls.__mro__ if item.__name__ in _validators()), None)
        if name is not None:
            wire = _wire(value)
            error = next(_validators()[name].iter_errors(wire), None)
            if error is not None:
                path = ".".join(map(str, error.absolute_path))
                raise ValueError(f"learning_shape_invalid:{error.validator}:{path}")
            # Reject non-finite numbers nested inside authenticated JSON fields.
            json.dumps(wire, allow_nan=False)
        return value

    @model_validator(mode="after")
    def original_relations(self):
        # These four checks are carried verbatim in meaning from the sealed
        # artifact DTOs. Preserve offsets/model_dump bytes; no time normalization
        # is added to the stored protocol hash algebra here.
        names = {item.__name__ for item in self.__class__.__mro__}
        if "LearningBinding" in names:
            if self.source_available_at > self.frozen_at:
                raise ValueError("learning_source_from_future")
        elif "ReflectionDelta" in names:
            if self.outcome == "candidate":
                if not self.hypotheses or not self.changes:
                    raise ValueError("reflection_candidate_requires_reasoned_change")
                for hypothesis in self.hypotheses:
                    if not hypothesis.supporting_refs or not hypothesis.positive_case_refs or not hypothesis.negative_case_refs:
                        raise ValueError("reflection_candidate_named_controls_required")
            elif self.changes:
                raise ValueError("reflection_non_candidate_cannot_change_method")
            if len({change.path for change in self.changes}) != len(self.changes):
                raise ValueError("reflection_duplicate_resource_change")
        elif "BehaviorProtocol" in names:
            if self.candidate == self.parent:
                raise ValueError("behavior_nonseed_candidate_required")
            if {item.purpose for item in self.cases} != {"applies", "excludes"}:
                raise ValueError("behavior_positive_and_negative_cases_required")
            if len({item.id for item in self.cases}) != len(self.cases):
                raise ValueError("behavior_duplicate_case")
            if any(item.known_at > self.registered_at for item in self.cases):
                raise ValueError("behavior_case_from_future")
            if self.payload_hash != _hash(self.model_dump(mode="json", exclude={"payload_hash"})):
                raise ValueError("behavior_protocol_hash_mismatch")
        elif "MethodTrialProtocol" in names:
            if self.candidate == self.parent:
                raise ValueError("trial_nonseed_required")
            if not self.registered_at <= self.start_at < self.end_at:
                raise ValueError("trial_prospective_window_required")
            if self.payload_hash != _hash(self.model_dump(mode="json", exclude={"payload_hash"})):
                raise ValueError("trial_protocol_hash_mismatch")
        return self

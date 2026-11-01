"""Closed feedback declarations; SQL provenance and stored hashes belong to the service."""

from __future__ import annotations

import json
import math
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from pydantic import BaseModel, ConfigDict, model_validator
from referencing import Registry, Resource

from .policy_contract_base import policy_format_checker


def _json_input(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.utcoffset() is None:
            raise ValueError("revision_feedback_evaluation_datetime_must_be_aware")
        return value.isoformat()
    if isinstance(value, BaseModel):
        # Python mode retains datetime awareness and scalar types before validation,
        # including unsafe instances constructed without their own validators.
        return _json_input(value.model_dump(mode="python"))
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("revision_feedback_evaluation_non_string_key")
        return {key: _json_input(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_input(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("revision_feedback_evaluation_non_finite_number")
    return value


@lru_cache(maxsize=1)
def _canonical_resources() -> tuple[dict[str, Any], Registry[Any]]:
    directory = Path(__file__).resolve().parents[3] / "contracts/schemas"
    canonical = json.loads(
        (directory / "revision_feedback_evaluation.schema.yaml").read_text("utf-8")
    )
    resources = []
    for name in ("workbench_assets.schema.yaml", "research_product_view.schema.yaml",
                 "agentic_research.schema.yaml", "research_fact.schema.yaml"):
        dependency = yaml.safe_load((directory / name).read_text("utf-8"))
        local_uri = canonical["$id"].rsplit("/", 1)[0] + "/" + name
        resource = Resource.from_contents(dependency)
        resources.extend(((local_uri, resource), (dependency["$id"], resource)))
    # Every permitted external reference is registered locally. No retrieve
    # callback exists, so a missing resource cannot perform HTTP retrieval.
    return canonical, Registry().with_resources(resources)


@lru_cache(maxsize=16)
def revision_feedback_evaluation_validator(
    definition: str = "revision_feedback_evaluation",
) -> Draft202012Validator:
    """Expose untouched canonical shape validation for the shared conformance corpus."""
    canonical, registry = _canonical_resources()
    if definition not in canonical["$defs"]:
        raise ValueError("revision_feedback_evaluation_unknown_definition")
    schema = {**canonical, "$ref": f"#/$defs/{definition}"}
    return Draft202012Validator(
        schema, registry=registry, format_checker=policy_format_checker()
    )


def _binding_issue_codes(binding: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if binding["evaluation_version"] != (
        "research-value.revision-feedback.v1:" + binding["basis_hash"]
    ):
        issues.append("revision_feedback_evaluation_version_basis_mismatch")
    identities = [
        (witness["source_table"], tuple(witness["primary_key"]))
        for witness in binding["source_witnesses"]
    ]
    if len(set(identities)) != len(identities):
        issues.append("revision_feedback_evaluation_duplicate_source_identity")
    return issues


def revision_feedback_evaluation_issue_codes(value: dict[str, Any]) -> list[str]:
    """Relations after canonical validation; no claim of stored-row/hash authentication."""
    issues: list[str] = []
    feedback = value["feedback"]
    if feedback["created_by"] != value["owner_id"]:
        issues.append("revision_feedback_evaluation_owner_mismatch")
    if (
        feedback["target_type"] != "report_revision"
        or feedback["target_id"] != value["revision_id"]
    ):
        issues.append("revision_feedback_evaluation_revision_mismatch")
    binding = value["binding"]
    if binding is None:
        return issues
    issues.extend(_binding_issue_codes(binding))
    if value["status"] == "unavailable":
        if value["reason"] not in binding["validation_issues"]:
            issues.append("revision_feedback_evaluation_reason_missing_from_issues")
    else:
        result = value["value"]
        if result["run_id"] != binding["source_run_id"]:
            issues.append("revision_feedback_evaluation_run_mismatch")
        if result["artifact_id"] != binding["artifact_id"]:
            issues.append("revision_feedback_evaluation_artifact_mismatch")
        if result["evaluation_version"] != binding["evaluation_version"]:
            issues.append("revision_feedback_evaluation_value_version_mismatch")
    return issues


@lru_cache(maxsize=32)
def _validator(model: type[BaseModel]) -> Draft202012Validator:
    canonical, _ = _canonical_resources()
    key = next(
        (key for key, schema in canonical["$defs"].items() if schema.get("title") == model.__name__),
        None,
    )
    if key is not None:
        return revision_feedback_evaluation_validator(key)
    # External generated dependency classes have strict scalar fields; the
    # public parent always validates their unchanged original canonical schema.
    return Draft202012Validator(
        model.model_json_schema(), format_checker=policy_format_checker()
    )


class RevisionFeedbackEvaluationContract(BaseModel):
    model_config = ConfigDict(extra="forbid", revalidate_instances="always")

    @model_validator(mode="before")
    @classmethod
    def _strict_shape(cls, value: Any) -> Any:
        raw = _json_input(value)
        try:
            _validator(cls).validate(raw)
        except JsonSchemaValidationError as exc:
            raise ValueError("revision_feedback_evaluation_contract_invalid") from exc
        if cls.__name__ == "RevisionFeedbackEvaluation":
            codes = revision_feedback_evaluation_issue_codes(raw)
        elif cls.__name__ == "RevisionFeedbackEvaluationBinding":
            codes = _binding_issue_codes(raw)
        else:
            codes = []
        if codes:
            raise ValueError(codes[0])
        # Convert old public DTO instances through JSON-compatible data instead
        # of relying on class identity of newly generated external copies.
        return raw

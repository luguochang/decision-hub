"""Pure B1 contract checks; no runtime/provenance or execution attestation."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError
from referencing.exceptions import Unresolvable

from packages.contracts_py.decision_hub_contracts.binding_contract_base import (
    binding_schema_registry,
    binding_validator,
)
from packages.contracts_py.decision_hub_contracts.generated import research_binding as generated
from packages.contracts_py.decision_hub_contracts.generated.agentic_research import (
    EventWatch,
    ResearchSessionRequest,
)
from packages.contracts_py.decision_hub_contracts.generated.research_binding import (
    ResearchFactoryEventSource,
    ResearchFactoryLibraryScopeSource,
    ResearchFactoryScopeSource,
    ResearchTaskReceiptV2,
)

ROOT = Path(__file__).resolve().parents[2]
CASES = json.loads((ROOT / "tests/fixtures/research_binding_contract_cases.json").read_text("utf8"))
CANONICAL = json.loads((ROOT / "contracts/schemas/research_binding.schema.yaml").read_text("utf8"))


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_python_and_canonical_share_actual_samples(case: dict[str, Any]) -> None:
    validator = binding_validator(case["definition"])
    assert validator.is_valid(case["value"]) is case["expected"]
    model = getattr(generated, CANONICAL["$defs"][case["definition"]]["title"])
    try:
        model.model_validate(case["value"])
        accepted = True
    except ValidationError:
        accepted = False
    assert accepted is case["expected"]


def test_aware_datetime_constructor_is_valid_without_numeric_coercion() -> None:
    body = dict(CASES[0]["value"])
    body["received_at"] = datetime(2026, 10, 1, 12, tzinfo=UTC)
    assert ResearchFactoryEventSource.model_validate(body).received_at.utcoffset() is not None


def test_constructed_model_instance_cannot_bypass_exact_consent_type() -> None:
    body = next(case["value"] for case in CASES if case["name"] == "library-numeric-consent")
    untrusted = ResearchFactoryLibraryScopeSource.model_construct(**body)
    with pytest.raises(ValidationError):
        ResearchFactoryLibraryScopeSource.model_validate(untrusted)


def test_original_evidence_source_caps_remain_above_kernel_forecast_cap() -> None:
    body = next(
        case["value"] for case in CASES if case["definition"] == "research_pack_evidence_policy_v3"
    )
    parsed = generated.ResearchPackEvidencePolicyV3.model_validate(body)
    assert max(item.confidence_cap for item in parsed.requirements) == 0.75


def test_actual_old_dto_instances_are_structurally_reused() -> None:
    original = next(case["value"] for case in CASES if case["name"] == "valid-watch-source")
    body = dict(original)
    body["watch"] = EventWatch.model_validate(body["watch"])
    parsed = ResearchFactoryScopeSource.model_validate(body)
    assert parsed.watch is not None
    assert parsed.watch.model_dump(mode="json") == body["watch"].model_dump(mode="json")
    task = next(case["value"] for case in CASES if case["definition"] == "research_task_receipt_v2")
    body = dict(task)
    body["request"] = ResearchSessionRequest.model_validate(task["request"])
    parsed_task = ResearchTaskReceiptV2.model_validate(body)
    assert parsed_task.request.model_dump(mode="json") == task["request"]


@pytest.mark.parametrize(
    "uri", ["https://example.test/private.schema.json", "file:///private.json"]
)
def test_external_registry_refuses_unlisted_retrieval(uri: str) -> None:
    _, registry = binding_schema_registry()
    validator = Draft202012Validator({"$ref": uri}, registry=registry)
    with pytest.raises(Unresolvable):
        validator.validate({})

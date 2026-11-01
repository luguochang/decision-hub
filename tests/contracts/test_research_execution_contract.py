"""B2 shape gates; no runtime, storage, provenance, or permission attestation."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError
from referencing.exceptions import Unresolvable

from packages.contracts_py.decision_hub_contracts import ResearchExecutionMethodReceiptV2
from packages.contracts_py.decision_hub_contracts.execution_contract_base import (
    execution_binding_validator,
    execution_schema_registry,
)
from packages.contracts_py.decision_hub_contracts.generated import (
    research_execution_binding as generated,
)
from packages.contracts_py.decision_hub_contracts.generated.agentic_research import (
    ResearchSessionRequest,
)
from packages.contracts_py.decision_hub_contracts.generated.dsh_host_bridge import (
    DshUpstreamIdentity,
)
from packages.contracts_py.decision_hub_contracts.generated.research_policy import (
    ResearchPolicyMethodBinding,
)

ROOT = Path(__file__).resolve().parents[2]
CASES = json.loads(
    (ROOT / "tests/fixtures/research_execution_contract_cases.json").read_text("utf-8")
)
SCHEMA = json.loads(
    (ROOT / "contracts/schemas/research_execution_binding.schema.yaml").read_text("utf-8")
)


def named(name: str) -> dict[str, Any]:
    return next(case["value"] for case in CASES if case["name"] == name)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_python_and_canonical_share_exact_samples(case: dict[str, Any]) -> None:
    valid = execution_binding_validator(case["definition"]).is_valid(case["value"])
    model = getattr(generated, SCHEMA["$defs"][case["definition"]]["title"])
    try:
        model.model_validate(case["value"])
        accepted = True
    except ValidationError:
        accepted = False
    assert valid is case["expected"], case["name"]
    assert accepted is case["expected"], case["name"]


def test_constructed_instance_cannot_coerce_numeric_provider_claim() -> None:
    raw = named("provider-numeric-one")
    untrusted = generated.ResearchExecutionMethodDefinitionV2.model_construct(**raw)
    with pytest.raises(ValidationError):
        generated.ResearchExecutionMethodDefinitionV2.model_validate(untrusted)


def test_aware_datetime_objects_are_valid_without_losing_exact_types() -> None:
    raw = dict(named("valid-shape-research_bundle_task_row_identity"))
    raw["recorded_at"] = datetime(2026, 10, 1, 12, tzinfo=UTC)
    parsed = generated.ResearchBundleTaskRowIdentity.model_validate(raw)
    assert parsed.recorded_at.utcoffset() is not None


def test_existing_typed_dependencies_are_structurally_reused() -> None:
    raw = dict(named("valid-shape-research_execution_method_definition_v2"))
    raw["upstream_identity"] = DshUpstreamIdentity.model_validate(raw["upstream_identity"])
    raw["policy_binding"] = ResearchPolicyMethodBinding.model_validate(raw["policy_binding"])
    parsed = generated.ResearchExecutionMethodDefinitionV2.model_validate(raw)
    assert parsed.upstream_identity.model_dump(mode="json") == raw["upstream_identity"].model_dump(
        mode="json"
    )
    original = dict(named("valid-shape-research_execution_request_receipt_v2"))
    original["request"] = ResearchSessionRequest.model_validate(original["request"])
    actual = generated.ResearchExecutionRequestReceiptV2.model_validate(original)
    assert actual.request.model_dump(mode="json") == original["request"].model_dump(mode="json")


def test_original_host_identity_accepts_empty_declared_packages() -> None:
    raw = named("original-host-empty-packages-compatible")
    assert DshUpstreamIdentity.model_validate(raw["upstream_identity"]).package_versions == {}
    parsed = generated.ResearchExecutionMethodDefinitionV2.model_validate(raw)
    assert parsed.upstream_identity.package_versions == {}


def test_method_public_hash_and_definition_abi_do_not_merge_identities() -> None:
    raw = named("valid-shape-research_execution_method_receipt_v2")
    parsed = ResearchExecutionMethodReceiptV2.model_validate(raw)
    public = parsed.model_dump(mode="json")
    assert {"run_id", "request_hash", "method_hash", "definition"}.issubset(public)
    assert "request_pack_hash" in public["definition"]
    assert parsed.definition.request_pack_hash == parsed.definition.policy_binding.request_pack_hash
    assert parsed.request_hash_kind == "dsh_link_identity.v1"
    assert parsed.request_hash != parsed.definition.policy_binding.first_execution_request_hash
    binding = parsed.definition.policy_binding
    assert binding.full_task_hash != binding.first_execution_request_hash
    assert parsed.definition.policy_binding.checker_activation == "prepared"
    assert parsed.definition.provider_configuration_attested is False


def test_repair_generation_is_independent_of_original_round_and_has_no_fake_tool_receipt() -> None:
    actual = generated.ResearchExecutionRequestReceiptV2.model_validate(
        named("shape-repair-generation-2-original-round-1")
    )
    assert actual.generation == 2 and actual.request.current_round == 1
    assert actual.request.execution_budget.max_tool_calls > 0
    assert "actual_tool_calls" not in actual.model_dump(mode="json")


@pytest.mark.parametrize("uri", ["https://example.test/private.json", "file:///private.json"])
def test_registry_never_retrieves_unknown_documents(uri: str) -> None:
    _, registry = execution_schema_registry()
    with pytest.raises(Unresolvable):
        Draft202012Validator({"$ref": uri}, registry=registry).validate({})

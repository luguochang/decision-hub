"""Closed new-policy contracts, without product runtime or database imports."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from packages.contracts_py.decision_hub_contracts.generated import research_policy as generated
from packages.contracts_py.decision_hub_contracts.generated.research_policy import (
    ResearchKernelSafeguards,
    ResearchTaskScope,
)
from packages.contracts_py.decision_hub_contracts.policy_contract_base import policy_format_checker

ROOT = Path(__file__).resolve().parents[2]
CASES = json.loads((ROOT / "tests/fixtures/research_policy_contract_cases.json").read_text("utf-8"))
CANONICAL = json.loads((ROOT / "contracts/schemas/research_policy.schema.yaml").read_text("utf-8"))


def safeguards() -> dict[str, object]:
    return {
        "pit_required": True,
        "hard_coverage_required": True,
        "open_hard_conflicts_rejected": True,
        "native_independent_review_required": True,
        "directional_calibration_required": True,
        "execution_permission_required": True,
        "existing_kernel_gate_required": True,
        "probability_cap": 0.65,
    }


def scope_body() -> dict[str, object]:
    digest = hashlib.sha256(b"controlled original full Task; no runtime execution").hexdigest()
    return {
        "schema_version": "research-task-scope.v1",
        "run_id": "run-contract",
        "event_id": "event-contract",
        "study_id": None,
        "trigger_snapshot_id": "snapshot-original",
        "trigger_snapshot_hash": digest,
        "market": "crypto",
        "instrument": "BTC",
        "horizon_scope": ["30m", "24h", "72h"],
        "domain_pack_ref": "crypto_macro.v1",
        "execution_mode": "replay",
        "research_mode": "current_decision",
        "scope_kind": "event",
        "event_family": "central_bank_speech",
        "event_family_basis": "event",
        "watch_id": None,
        "full_task_hash": digest,
        "requirements_hash": digest,
        "allowed_capabilities_hash": digest,
        "pit_cutoff_at": "2026-10-01T12:00:00Z",
        "deadline_at": "2026-10-01T12:30:00Z",
        "scope_hash": digest,
    }


@pytest.mark.parametrize("bad", [1, "true", False])
def test_kernel_guard_requires_exact_true(bad: object) -> None:
    body = safeguards()
    body["pit_required"] = bad
    with pytest.raises(ValidationError):
        ResearchKernelSafeguards.model_validate(body)


@pytest.mark.parametrize("bad", ["0.65", True, 0.7])
def test_probability_cap_has_exact_numeric_type_and_kernel_bound(bad: object) -> None:
    body = safeguards()
    body["probability_cap"] = bad
    with pytest.raises(ValidationError):
        ResearchKernelSafeguards.model_validate_json(json.dumps(body))


def test_scope_rejects_naive_datetime_instead_of_certifying_timezone() -> None:
    body = scope_body()
    body["pit_cutoff_at"] = "2026-10-01T12:00:00"
    with pytest.raises(ValidationError):
        ResearchTaskScope.model_validate(body)


def test_standard_aware_datetime_constructor_remains_legal() -> None:
    body = scope_body()
    body["pit_cutoff_at"] = datetime(2026, 10, 1, 12, tzinfo=UTC)
    parsed = ResearchTaskScope.model_validate(body)
    assert parsed.pit_cutoff_at.utcoffset() is not None


def test_canonical_unique_horizons_are_enforced_by_python() -> None:
    body = scope_body()
    body["horizon_scope"] = ["30m", "30m", "30m"]
    with pytest.raises(ValidationError):
        ResearchTaskScope.model_validate(body)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_python_and_original_jsonschema_use_the_same_explicit_samples(case: dict[str, Any]) -> None:
    schema = {"$ref": f"#/$defs/{case['definition']}", "$defs": CANONICAL["$defs"]}
    validator = Draft202012Validator(schema, format_checker=policy_format_checker())
    assert validator.is_valid(case["value"]) is case["expected"]
    title = CANONICAL["$defs"][case["definition"]].get("title")
    if title is None or not hasattr(generated, title):
        # RootModels are collapsed by the existing generator. Validate the
        # concrete rule selected by the actual literal ID for this oneOf test.
        title = next(
            (
                definition.get("title") or "".join(part.title() for part in key.split("_"))
                for key, definition in CANONICAL["$defs"].items()
                if definition.get("properties", {}).get("rule_id", {}).get("const")
                == case["value"].get("rule_id")
            ),
            "ResearchPitRule",
        )
    model = getattr(generated, title)
    try:
        model.model_validate(case["value"])
        accepted = True
    except ValidationError:
        accepted = False
    assert accepted is case["expected"]

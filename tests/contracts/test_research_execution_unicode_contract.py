"""Unicode shape equivalence only; declared fields do not authorize execution."""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from packages.contracts_py.decision_hub_contracts.execution_contract_base import (
    execution_binding_validator,
)
from packages.contracts_py.decision_hub_contracts.generated import (
    research_execution_binding as generated,
)

ROOT = Path(__file__).resolve().parents[2]
SAMPLES = json.loads(
    (ROOT / "tests/fixtures/research_execution_unicode_cases.json").read_text("utf-8")
)
SEEDS = json.loads(
    (ROOT / "tests/fixtures/research_execution_contract_cases.json").read_text("utf-8")
)
SCHEMA = json.loads(
    (ROOT / "contracts/schemas/research_execution_binding.schema.yaml").read_text("utf-8")
)


@pytest.mark.parametrize("sample", SAMPLES, ids=lambda sample: sample["name"])
def test_same_unicode_sample_in_python_and_canonical(sample: dict[str, Any]) -> None:
    value = deepcopy(next(case["value"] for case in SEEDS if case["name"] == sample["seed"]))
    current = value
    for key in sample["field_path"][:-1]:
        current = current[key]
    text = sample["character"] * sample["repeat"]
    current[sample["field_path"][-1]] = text
    assert len(text) == sample["repeat"]
    canonical = execution_binding_validator(sample["definition"]).is_valid(value)
    model = getattr(generated, SCHEMA["$defs"][sample["definition"]]["title"])
    try:
        model.model_validate(value)
        python = True
    except ValidationError:
        python = False
    assert canonical is sample["expected"]
    assert python is sample["expected"]

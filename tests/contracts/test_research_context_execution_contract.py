"""Same new declarations across canonical JSON Schema and Python; no admission."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TypeAlias, TypedDict

import pytest
from jsonschema import Draft202012Validator
from pydantic import BaseModel

from packages.contracts_py.decision_hub_contracts import models
from packages.contracts_py.decision_hub_contracts.context_execution_contract_base import (
    context_execution_registry,
)
from packages.contracts_py.decision_hub_contracts.context_selection_contract_base import (
    context_selection_format_checker,
)

CASES = json.loads(
    (Path(__file__).parents[1] / "fixtures/research_context_execution_cases.json").read_text(
        "utf-8"
    )
)

Json: TypeAlias = None | bool | int | float | str | list["Json"] | dict[str, "Json"]


class Sample(TypedDict):
    name: str
    definition: str
    value: Json
    expected: bool


@pytest.mark.parametrize("sample", CASES, ids=[sample["name"] for sample in CASES])
def test_context_execution_new_canonical_python_same_shape(sample: Sample) -> None:
    definition = str(sample["definition"])
    name = "".join(part.title() for part in definition.split("_"))
    model = getattr(models, name)
    assert issubclass(model, BaseModel)
    documents, registry = context_execution_registry()
    document = documents["research_context_execution.schema.yaml"]
    validator = Draft202012Validator(
        {"$ref": f"{document['$id']}#/$defs/{definition}"},
        registry=registry,
        format_checker=context_selection_format_checker(),
    )
    value = sample["value"]
    # Canonical shape has independent pure time checks in the typed base; for
    # those two link formulas, call that unchanged pure function explicitly.
    from packages.contracts_py.decision_hub_contracts.context_selection_semantics import (
        context_selection_time_issue_codes,
    )

    canonical_valid = validator.is_valid(value)
    if isinstance(value, dict) and definition == "research_context_selection_execution_link_v2":
        canonical_valid = canonical_valid and not context_selection_time_issue_codes(
            {
                **value,
                "schema_version": "research-context-selection-execution-link.v1",
            }
        )
    try:
        model.model_validate_json(json.dumps(value, ensure_ascii=False))
        python_valid = True
    except ValueError:
        python_valid = False
    assert python_valid == canonical_valid == sample["expected"]

from __future__ import annotations

import json
import os
import socket
from collections.abc import Iterator
from pathlib import Path
from typing import Literal

import pytest
import yaml
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import BaseModel, JsonValue, ValidationError

from packages.contracts_py.decision_hub_contracts.generated.agentic_research import (
    ErrorProvenance,
    ResearchCapabilityResult,
)

ROOT = Path(__file__).resolve().parents[2]


class Case(BaseModel):
    name: str
    definition: Literal["error_provenance", "research_capability_result"]
    expected_valid: bool
    value: dict[str, JsonValue]


CASES = [
    Case.model_validate(raw)
    for raw in json.loads(
        (ROOT / "tests/fixtures/provider_attempts_nullability_cases.json").read_text("utf-8")
    )
]
DOCUMENT = yaml.safe_load(
    (ROOT / "contracts/schemas/agentic_research.schema.yaml").read_text("utf-8")
)
MODELS: dict[str, type[BaseModel]] = {
    "error_provenance": ErrorProvenance,
    "research_capability_result": ResearchCapabilityResult,
}


@pytest.fixture(scope="session")
def observations() -> Iterator[list[dict[str, object]]]:
    records: list[dict[str, object]] = []
    yield records
    destination = os.environ.get("PROVIDER_ATTEMPTS_OBSERVATIONS")
    if destination:
        with Path(destination).open("x", encoding="utf-8") as output:
            json.dump(records, output, indent=2)
            output.write("\n")


@pytest.fixture(autouse=True)
def deny_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def denied(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("offline contract tests must not connect to a socket")

    monkeypatch.setattr(socket.socket, "connect", denied)


@pytest.mark.parametrize("case", CASES, ids=[case.name for case in CASES])
def test_provider_attempts_have_identical_canonical_and_python_values(
    case: Case, observations: list[dict[str, object]]
) -> None:
    validator = Draft202012Validator(
        {"$ref": f"#/$defs/{case.definition}", "$defs": DOCUMENT["$defs"]},
        format_checker=FormatChecker(),
    )
    canonical_valid = validator.is_valid(case.value)
    parsed: BaseModel | None = None
    try:
        parsed = MODELS[case.definition].model_validate(case.value)
    except ValidationError:
        python_valid = False
    else:
        python_valid = True
    observations.append(
        {
            "name": case.name,
            "expected_valid": case.expected_valid,
            "canonical_valid": canonical_valid,
            "python_valid": python_valid,
            "serialized": parsed.model_dump(mode="json") if parsed else None,
        }
    )
    assert canonical_valid is case.expected_valid
    assert python_valid is case.expected_valid
    if parsed is not None:
        serialized = parsed.model_dump(mode="json")
        if case.name.endswith("-omitted") or case.name.endswith("-empty"):
            assert serialized["provider_attempts"] == []
        if case.name.endswith("-component-null"):
            assert serialized["provider_attempts"] == case.value["provider_attempts"]

"""V3 declaration compatibility; no selector, clock, SQL or RPC proof."""

from __future__ import annotations

import copy
import json
import socket
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from packages.contracts_py.decision_hub_contracts import ResearchContextSelectionReceiptV2
from packages.contracts_py.decision_hub_contracts import context_selection_v3_contract_base as base
from packages.contracts_py.decision_hub_contracts.generated.research_context_selection_v3 import (
    ResearchContextSelectionReceiptV3,
)

ROOT = Path(__file__).resolve().parents[2]
CASES: list[dict[str, Any]] = json.loads(
    (ROOT / "tests/fixtures/research_context_selection_v3_cases.json").read_text("utf-8")
)


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def denied(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("offline contract test")

    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket, "create_connection", denied)


def expanded(case: dict[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(case["value"])
    if "expand_considered_count" in case:
        item = value["considered_inputs"][0]
        value["considered_inputs"] = []
        for index in range(case["expand_considered_count"]):
            witness = copy.deepcopy(item)
            witness["input"]["evidence_id"] = f"declared-count-{index}"
            value["considered_inputs"].append(witness)
    return value


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_shared_v3_canonical_and_python(case: dict[str, Any]) -> None:
    raw = expanded(case)
    before = copy.deepcopy(raw)
    shape = base.context_selection_v3_validator().is_valid(raw)
    assert shape is case["expected_shape"]
    if shape:
        codes = base.context_selection_v3_time_issue_codes(raw)
        assert list(codes) == case["expected_issue_codes"]
    try:
        ResearchContextSelectionReceiptV3.model_validate(raw)
        accepted = True
    except ValidationError:
        accepted = False
    assert accepted is (case["expected_shape"] and case["expected_time_semantics"])
    assert raw == before


def test_v2_original_is_not_upgraded_by_v3_shape() -> None:
    original = json.loads(
        (ROOT / "tests/fixtures/research_context_selection_v2_cases.json").read_text("utf-8")
    )
    v2 = next(sample["value"] for sample in original if sample["name"] == "live-clock-before-pit")
    ResearchContextSelectionReceiptV2.model_validate(v2)
    with pytest.raises(ValidationError):
        ResearchContextSelectionReceiptV3.model_validate(v2)
    v3 = next(sample["value"] for sample in CASES if sample["name"] == "v3-live-clock-before-pit")
    with pytest.raises(ValidationError):
        ResearchContextSelectionReceiptV2.model_validate(v3)


def test_constructed_instance_cannot_bypass_ref_identity() -> None:
    raw = copy.deepcopy(
        next(sample["value"] for sample in CASES if sample["name"] == "v3-graph-foreign-run")
    )
    constructed = ResearchContextSelectionReceiptV3.model_construct(**raw)
    with pytest.raises(ValidationError):
        ResearchContextSelectionReceiptV3.model_validate(constructed)


def test_declaration_does_not_claim_hash_or_execution_verification() -> None:
    raw = copy.deepcopy(
        next(sample["value"] for sample in CASES if sample["name"] == "v3-live-clock-before-pit")
    )
    raw["selection_payload_hash"] = "f" * 64
    raw["recorded_at"] = datetime(2026, 10, 2, 0, 0, 0, 3, tzinfo=UTC)
    parsed = ResearchContextSelectionReceiptV3.model_validate(raw)
    assert parsed.selection_payload_hash == "f" * 64
    assert (
        parsed.selector_identity.ranking_rules_ref
        == "research-context.relevance-authority-recency.v2"
    )
    assert parsed.selection_payload_hash_kind == "canonical_context_selection.v3"

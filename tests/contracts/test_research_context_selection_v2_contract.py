"""V2 shape/time declarations; no selector, server clock, SQL or hash proof."""
from __future__ import annotations

import copy
import json
import socket
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError
from referencing.exceptions import Unresolvable

from packages.contracts_py.decision_hub_contracts import (
    ResearchContextSelectionReceiptV1,
    ResearchContextSelectionReceiptV2,
    ResearchContextSelectorIdentityV2,
    ResearchSessionRequest,
)
from packages.contracts_py.decision_hub_contracts import context_selection_v2_contract_base as base
from packages.contracts_py.decision_hub_contracts.context_selection_semantics import (
    canonical_payload_size,
)

ROOT = Path(__file__).resolve().parents[2]
CASES: list[dict[str, Any]] = json.loads(
    (ROOT / "tests/fixtures/research_context_selection_v2_cases.json").read_text("utf-8")
)


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def denied(*args: object, **kwargs: object) -> None:
        raise AssertionError("offline contract stage does not connect sockets")

    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket, "create_connection", denied)


def expanded(case: dict[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(case["value"])
    if "expand_considered_count" in case:
        first = value["considered_inputs"][0]
        value["considered_inputs"] = []
        for index in range(case["expand_considered_count"]):
            witness = copy.deepcopy(first)
            witness["input"]["evidence_id"] = f"declared-count-{index}"
            value["considered_inputs"].append(witness)
    return value


def named(name: str) -> dict[str, Any]:
    return expanded(next(case for case in CASES if case["name"] == name))


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_shared_v2_shape_and_time_relations(case: dict[str, Any]) -> None:
    raw = expanded(case)
    before = copy.deepcopy(raw)
    shape = base.context_selection_v2_validator().is_valid(raw)
    assert shape is case["expected_shape"]
    if shape:
        issues = base.context_selection_v2_time_issue_codes(raw)
        assert list(issues) == case["expected_issue_codes"]
        assert (not issues) is case["expected_time_semantics"]
    try:
        ResearchContextSelectionReceiptV2.model_validate(raw)
        accepted = True
    except ValidationError:
        accepted = False
    assert accepted is (case["expected_shape"] and case["expected_time_semantics"])
    assert raw == before


def test_both_real_versions_keep_their_own_rules_and_hash_namespace() -> None:
    v2 = named("live-clock-before-pit")
    old_cases = json.loads(
        (ROOT / "tests/fixtures/research_context_selection_contract_cases.json").read_text("utf-8")
    )
    v1 = copy.deepcopy(
        next(case for case in old_cases if case["name"] == "live-clock-before-pit")["value"]
    )
    ResearchContextSelectionReceiptV1.model_validate(v1)
    ResearchContextSelectionReceiptV2.model_validate(v2)
    with pytest.raises(ValidationError):
        ResearchContextSelectionReceiptV1.model_validate(v2)
    with pytest.raises(ValidationError):
        ResearchContextSelectionReceiptV2.model_validate(v1)
    assert base.context_selection_v2_time_issue_codes(v1) == (
        "context_selection_v2_schema_invalid",
    )


def test_adapter_only_checks_time_and_does_not_mutate_or_validate_hashes() -> None:
    raw = named("live-clock-before-pit")
    raw["selection_payload_hash"] = "f" * 64
    before = copy.deepcopy(raw)
    assert base.context_selection_v2_time_issue_codes(raw) == ()
    parsed = ResearchContextSelectionReceiptV2.model_validate(raw)
    assert parsed.selection_payload_hash_kind == "canonical_context_selection.v2"
    assert (
        parsed.selector_identity.ranking_rules_ref
        == "research-context.relevance-authority-recency.v2"
    )
    # A syntactically valid arbitrary hash is accepted; shape is not self-hash proof.
    assert parsed.selection_payload_hash == "f" * 64
    assert raw == before


def test_external_inline_request_bridges_through_json_to_canonical_dto() -> None:
    parsed = ResearchContextSelectionReceiptV2.model_validate(named("live-clock-before-pit"))
    # Code generation creates separate inline classes for fixed external refs.
    assert type(parsed.projected_request) is not ResearchSessionRequest
    projection = parsed.projected_request.model_dump(mode="json")
    canonical = ResearchSessionRequest.model_validate(projection)
    assert isinstance(canonical, ResearchSessionRequest)
    assert canonical.model_dump(mode="json") == projection


def test_constructed_instance_does_not_bypass_strict_shape_or_time() -> None:
    for name in ("bool-round-not-int", "wrong-effective-one-microsecond", "v1-ranking-not-v2"):
        raw = named(name)
        constructed = ResearchContextSelectionReceiptV2.model_construct(**raw)
        with pytest.raises(ValidationError):
            ResearchContextSelectionReceiptV2.model_validate(constructed)


def test_aware_datetime_input_is_only_a_declaration_projection() -> None:
    raw = named("live-clock-before-pit")
    raw["recorded_at"] = datetime(2026, 10, 2, 0, 0, 0, 3, tzinfo=UTC)
    parsed = ResearchContextSelectionReceiptV2.model_validate(raw)
    assert parsed.recorded_at.utcoffset() is not None
    raw["recorded_at"] = datetime(2026, 10, 2)
    with pytest.raises(ValidationError, match="invalid_timestamp"):
        ResearchContextSelectionReceiptV2.model_validate(raw)


def test_nonfinite_is_rejected_before_coercion() -> None:
    raw = named("live-clock-before-pit")
    raw["projected_request"]["execution_budget"]["max_estimated_cost_usd"] = float("inf")
    with pytest.raises(ValidationError, match="non_finite_number"):
        ResearchContextSelectionReceiptV2.model_validate(raw)


def test_canonical_only_has_two_own_defs_and_selector_is_versioned() -> None:
    documents, _ = base.context_selection_v2_schema_registry()
    schema = documents[base.SCHEMA_FILES[0]]
    assert set(schema["$defs"]) == {
        "research_context_selector_identity_v2", "research_context_selection_receipt_v2"
    }
    assert len(base.SCHEMA_FILES) == 7
    selector = ResearchContextSelectorIdentityV2.model_validate(
        named("live-clock-before-pit")["selector_identity"]
    )
    assert selector.time_rule_ref == "research-context.received-cutoff.v1"
    assert selector.implementation_hash_kind == "canonical_context_selector_sources.v1"


def test_raw_before_cap_counts_actual_v2_not_the_time_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = named("live-clock-before-pit")
    calls: list[tuple[object, int]] = []

    def count(value: object, *, limit: int) -> int:
        calls.append((copy.deepcopy(value), limit))
        return canonical_payload_size(value, limit=limit)

    monkeypatch.setattr(base, "canonical_payload_size", count)
    ResearchContextSelectionReceiptV2.model_validate(raw)
    assert calls == [(raw, 16_777_216)]
    exact = len(
        json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    assert canonical_payload_size(raw, limit=exact) == exact
    with pytest.raises(ValueError, match="payload_too_large"):
        canonical_payload_size(raw, limit=exact - 1)
    # No writer exists here; defaults/datetime projection still require a final storage count.


@pytest.mark.parametrize("uri", ["https://example.invalid/private.json", "file:///private.json"])
def test_fixed_registry_denies_unknown_retrieve(uri: str) -> None:
    _, registry = base.context_selection_v2_schema_registry()
    with pytest.raises(Unresolvable):
        Draft202012Validator({"$ref": uri}, registry=registry).validate({})

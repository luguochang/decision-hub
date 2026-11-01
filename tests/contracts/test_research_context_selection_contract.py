"""Declaration shape, exact time semantics, and storage bytes are distinct gates."""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError
from referencing.exceptions import Unresolvable

from packages.contracts_py.decision_hub_contracts import (
    ResearchContextSelectionExecutionLinkV1,
    ResearchContextSelectionReceiptV1,
)
from packages.contracts_py.decision_hub_contracts.context_selection_contract_base import (
    context_selection_schema_registry,
    context_selection_validator,
)
from packages.contracts_py.decision_hub_contracts.context_selection_semantics import (
    ContextSelectionSemanticError,
    canonical_payload_size,
    context_selection_time_issue_codes,
    parse_rfc3339_microseconds,
    utf8_payload_size,
)

ROOT = Path(__file__).resolve().parents[2]
CASES: list[dict[str, Any]] = json.loads(
    (ROOT / "tests/fixtures/research_context_selection_contract_cases.json").read_text("utf-8")
)
MODELS = {
    "research_context_selection_receipt_v1": ResearchContextSelectionReceiptV1,
    "research_context_selection_execution_link_v1": ResearchContextSelectionExecutionLinkV1,
}


def expanded(case: dict[str, Any]) -> dict[str, Any]:
    body = copy.deepcopy(case["value"])
    count = case.get("expand_considered_count")
    if count is not None:
        original = body["considered_inputs"][0]
        body["considered_inputs"] = []
        for index in range(count):
            witness = copy.deepcopy(original)
            witness["input"]["evidence_id"] = f"declared-count-{index}"
            body["considered_inputs"].append(witness)
    return body


def named(name: str) -> dict[str, Any]:
    return expanded(next(case for case in CASES if case["name"] == name))


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_shared_shape_and_explicit_time_relations(case: dict[str, Any]) -> None:
    raw = expanded(case)
    assert context_selection_validator(case["definition"]).is_valid(raw) is case["expected_shape"]
    if case["expected_shape"]:
        issues = context_selection_time_issue_codes(raw)
        assert list(issues) == case["expected_issue_codes"]
        assert (not issues) is case["expected_time_semantics"]
    model = MODELS[case["definition"]]
    try:
        model.model_validate(raw)
        accepted = True
    except ValidationError:
        accepted = False
    assert accepted is (case["expected_shape"] and case["expected_time_semantics"])


def test_constructed_instance_does_not_bypass_raw_semantics_or_exact_bool() -> None:
    raw = named("bool-round-not-int")
    untrusted = ResearchContextSelectionReceiptV1.model_construct(**raw)
    with pytest.raises(ValidationError):
        ResearchContextSelectionReceiptV1.model_validate(untrusted)
    raw = named("wrong-effective-one-microsecond")
    untrusted = ResearchContextSelectionReceiptV1.model_construct(**raw)
    with pytest.raises(ValidationError, match="context_selection_effective_cutoff_mismatch"):
        ResearchContextSelectionReceiptV1.model_validate(untrusted)


def test_aware_datetime_declaration_input_is_valid_without_claiming_prior_raw_text() -> None:
    raw = named("live-clock-before-pit")
    raw["recorded_at"] = datetime(2026, 10, 2, 0, 0, 0, 3, tzinfo=UTC)
    parsed = ResearchContextSelectionReceiptV1.model_validate(raw)
    assert parsed.recorded_at.utcoffset() is not None


def test_nonfinite_nested_declaration_is_rejected_before_coercion() -> None:
    raw = named("live-clock-before-pit")
    raw["projected_request"]["execution_budget"]["max_estimated_cost_usd"] = float("inf")
    with pytest.raises(ValidationError, match="non_finite_number"):
        ResearchContextSelectionReceiptV1.model_validate(raw)


def test_raw_zero_tail_projection_preserves_the_callers_original_request() -> None:
    raw = named("old-pit-zero-tail-lossless")
    before = copy.deepcopy(raw)
    assert context_selection_time_issue_codes(raw) == ()
    assert raw == before
    assert raw["projected_request"]["pit_cutoff_at"].endswith("000Z")
    assert parse_rfc3339_microseconds(
        raw["projected_request"]["pit_cutoff_at"], inherited=True
    ) == parse_rfc3339_microseconds(raw["original_pit_cutoff_at"])
    # Model datetime projection is a declaration, not original raw FULL bytes.
    ResearchContextSelectionReceiptV1.model_validate(raw)
    assert raw == before


@pytest.mark.parametrize(
    "value", ["0001-01-01T00:00:00Z", "2000-02-29T00:00:00.000001Z", "9999-12-31T23:59:59.999999Z"]
)
def test_gregorian_extremes_use_exact_integer_microseconds(value: str) -> None:
    assert isinstance(parse_rfc3339_microseconds(value), int)


@pytest.mark.parametrize(
    "value", ["1900-02-29T00:00:00Z", "0001-01-01T00:00:00+00:01", "9999-12-31T23:59:59-00:01"]
)
def test_gregorian_invalid_or_utc_out_of_range_is_rejected(value: str) -> None:
    with pytest.raises(ContextSelectionSemanticError, match="invalid_timestamp"):
        parse_rfc3339_microseconds(value)


def test_microseconds_do_not_use_millisecond_rounding_or_float_timestamp() -> None:
    first = parse_rfc3339_microseconds("2026-10-02T00:00:00.000001Z")
    second = parse_rfc3339_microseconds("2026-10-02T08:00:00.000002+08:00")
    assert second - first == 1


def test_actual_canonical_bytes_limit_and_unicode_are_separate_from_shape() -> None:
    raw = named("count-2050-minimal-unique-declarations")
    assert canonical_payload_size(raw, limit=16_777_216) < 16_777_216
    assert len({item["input"]["evidence_id"] for item in raw["considered_inputs"]}) == 2050
    excerpt = "\U0001f9ea" * 4000
    for witness in raw["considered_inputs"]:
        witness["input"]["excerpt"] = excerpt
    assert context_selection_validator("research_context_selection_receipt_v1").is_valid(raw)
    with pytest.raises(ContextSelectionSemanticError, match="payload_too_large"):
        canonical_payload_size(raw, limit=16_777_216)
    with pytest.raises(ValidationError, match="payload_too_large"):
        ResearchContextSelectionReceiptV1.model_validate(raw)


def test_byte_utility_counts_same_final_text_at_exact_limit_and_limit_plus_one() -> None:
    serialized = json.dumps(
        {"text": "\U0001f9ea\x00"}, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    expected = len(serialized.encode("utf-8"))
    assert utf8_payload_size(serialized, limit=expected) == expected
    with pytest.raises(ContextSelectionSemanticError, match="payload_too_large"):
        utf8_payload_size(serialized, limit=expected - 1)
    with pytest.raises(ContextSelectionSemanticError, match="invalid_unicode"):
        utf8_payload_size("\ud800")


def test_link_real_fields_fit_cap_without_fabricating_illegal_padding() -> None:
    raw = named("normal-execution-link")
    assert canonical_payload_size(raw, limit=16_384) < 16_384
    raw["padding"] = "x" * 16_384
    assert not context_selection_validator("research_context_selection_execution_link_v1").is_valid(
        raw
    )


@pytest.mark.parametrize("uri", ["https://example.invalid/private.json", "file:///private.json"])
def test_new_registry_denies_unknown_external_retrieve(uri: str) -> None:
    _, registry = context_selection_schema_registry()
    with pytest.raises(Unresolvable):
        Draft202012Validator({"$ref": uri}, registry=registry).validate({})


def test_new_top_does_not_offer_executor_or_verified_prior_flags() -> None:
    raw = named("repair-generation2-round1-no-tool-proof")
    link = ResearchContextSelectionExecutionLinkV1.model_validate(raw)
    assert link.generation == 2 and link.graph_round == 1
    assert "actual_tool_calls" not in link.model_dump(mode="json")
    assert "allow_execution" not in link.model_dump(mode="json")


def test_first_current_original_full_and_link_reduced_remain_distinct_declarations() -> None:
    link = ResearchContextSelectionExecutionLinkV1.model_validate(named("normal-execution-link"))
    assert link.selection_full_request_hash == link.actual_execution_full_hash
    assert (
        len(
            {
                link.original_task_request_hash,
                link.first_execution_request_full_hash,
                link.actual_execution_full_hash,
                link.first_link_request_hash,
                link.actual_execution_reduced_hash,
            }
        )
        == 5
    )
    assert link.first_execution_request_full_hash_kind == "canonical_research_session_request.v1"
    assert link.first_link_request_hash_kind == "dsh_link_identity.v1"

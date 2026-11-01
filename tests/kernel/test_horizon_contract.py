from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone

import pytest

from packages.contracts_py.decision_hub_contracts.generated.agentic_research import HorizonDecision
from packages.kernel.decision_hub_kernel.decision.horizon_contract import (
    HORIZON_CONTRACT_VERSION,
    validate_horizon_contract,
)

REFERENCE = datetime(2024, 9, 18, 18, 5, tzinfo=UTC)


def _horizons(*, same_times: bool = False) -> list[HorizonDecision]:
    return [
        HorizonDecision(
            horizon=name,
            action="no_trade",
            subjective_probability=0.5,
            probability_status="uncalibrated",
            evidence_refs=["ev-a", "ev-b", "ev-c"],
            trigger="price stays above support",
            invalidation="support breaks",
            expires_at=REFERENCE + timedelta(hours=2 if same_times else 2 + index),
            next_review_at=REFERENCE + timedelta(hours=1 if same_times else 1 + index),
            missing_facts=[],
            confidence_cap_reason=None,
        )
        for index, name in enumerate(("30m", "24h", "72h"))
    ]


def test_real_time_differences_are_distinct_and_inputs_are_unchanged() -> None:
    horizons = _horizons()
    before = [item.model_dump(mode="json") for item in horizons]
    check = validate_horizon_contract(horizons, REFERENCE)
    assert check.valid and check.issue_codes == ()
    assert len(set(check.canonical_signatures)) == 3
    assert check.contract_version == HORIZON_CONTRACT_VERSION
    assert [item.model_dump(mode="json") for item in horizons] == before


@pytest.mark.parametrize("change", ["probability", "action", "ref_order", "ref_duplicates", "trim"])
def test_cosmetic_changes_cannot_create_distinct_windows(change: str) -> None:
    horizons = _horizons(same_times=True)
    for index, item in enumerate(horizons):
        if change == "probability":
            horizons[index] = item.model_copy(update={"subjective_probability": 0.1 * index})
        elif change == "action":
            horizons[index] = item.model_copy(
                update={
                    "action": ("long", "short", "no_trade")[index],
                }
            )
        elif change == "ref_order":
            refs = item.evidence_refs[index:] + item.evidence_refs[:index]
            horizons[index] = item.model_copy(update={"evidence_refs": refs})
        elif change == "ref_duplicates":
            refs = item.evidence_refs + [item.evidence_refs[0]] * index
            horizons[index] = item.model_copy(update={"evidence_refs": refs})
        else:
            horizons[index] = item.model_copy(
                update={
                    "trigger": " " * index + item.trigger,
                    "invalidation": item.invalidation + " " * index,
                }
            )
    check = validate_horizon_contract(horizons, REFERENCE)
    assert not check.valid
    assert check.issue_codes == ("horizon_signatures_duplicate",)


def test_canonical_signatures_ignore_input_order_and_labels() -> None:
    horizons = _horizons()
    original = validate_horizon_contract(horizons, REFERENCE)
    assert validate_horizon_contract(tuple(reversed(horizons)), REFERENCE) == original
    names = ("72h", "30m", "24h")
    renamed = [
        item.model_copy(update={"horizon": names[index]}) for index, item in enumerate(horizons)
    ]
    assert validate_horizon_contract(renamed, REFERENCE) == original


def test_offsets_representing_same_instants_do_not_add_distinctness() -> None:
    horizons = _horizons(same_times=True)
    for index, item in enumerate(horizons):
        offset = timezone(timedelta(hours=(0, 8, -5)[index]))
        horizons[index] = item.model_copy(
            update={
                "expires_at": item.expires_at.astimezone(offset),
                "next_review_at": item.next_review_at.astimezone(offset),
            }
        )
    check = validate_horizon_contract(horizons, REFERENCE.astimezone(timezone(timedelta(hours=8))))
    assert not check.valid and check.issue_codes == ("horizon_signatures_duplicate",)
    assert all(
        signature[3].tzinfo is UTC and signature[4].tzinfo is UTC
        for signature in check.canonical_signatures
    )


def test_review_equal_expiry_remains_valid() -> None:
    horizons = [item.model_copy(update={"next_review_at": item.expires_at}) for item in _horizons()]
    assert validate_horizon_contract(horizons, REFERENCE).valid


@pytest.mark.parametrize("relation", ["at_reference", "before_reference", "after_expiry"])
def test_invalid_review_boundaries_fail_closed(relation: str) -> None:
    horizons = _horizons()
    review = {
        "at_reference": REFERENCE,
        "before_reference": REFERENCE - timedelta(microseconds=1),
        "after_expiry": horizons[0].expires_at + timedelta(microseconds=1),
    }[relation]
    horizons[0] = horizons[0].model_copy(update={"next_review_at": review})
    expected = (
        "horizon_review_after_expiry"
        if relation == "after_expiry"
        else "horizon_review_not_after_reference"
    )
    check = validate_horizon_contract(horizons, REFERENCE)
    assert not check.valid and expected in check.issue_codes


@pytest.mark.parametrize("field", ["reference", "expires_at", "next_review_at"])
def test_naive_datetimes_return_issues_instead_of_comparison_errors(field: str) -> None:
    horizons = _horizons()
    reference = REFERENCE
    if field == "reference":
        reference = reference.replace(tzinfo=None)
    else:
        value = getattr(horizons[0], field).replace(tzinfo=None)
        horizons[0] = horizons[0].model_copy(update={field: value})
    expected = {
        "reference": "reference_timezone_invalid",
        "expires_at": "horizon_expiry_timezone_invalid",
        "next_review_at": "horizon_review_timezone_invalid",
    }[field]
    check = validate_horizon_contract(horizons, reference)
    assert not check.valid and expected in check.issue_codes


@pytest.mark.parametrize("case", ["missing", "duplicate", "extra", "unknown"])
def test_incomplete_or_duplicate_window_names_are_invalid(case: str) -> None:
    horizons = _horizons()
    if case == "missing":
        horizons.pop()
    elif case == "duplicate":
        horizons[2] = horizons[2].model_copy(update={"horizon": "30m"})
    elif case == "extra":
        horizons.append(horizons[0])
    else:
        horizons[2] = horizons[2].model_copy(update={"horizon": "7d"})
    check = validate_horizon_contract(horizons, REFERENCE)
    assert not check.valid and "horizon_names_invalid" in check.issue_codes


def test_empty_is_incomplete_and_does_not_invent_windows() -> None:
    check = validate_horizon_contract([], REFERENCE)
    assert not check.valid
    assert "empty_horizons" in check.issue_codes
    assert check.canonical_signatures == ()


@pytest.mark.parametrize("field", ["trigger", "invalidation"])
def test_blank_conditions_fail_even_with_real_time_differences(field: str) -> None:
    horizons = _horizons()
    horizons[0] = horizons[0].model_copy(update={field: " \t\r\n "})
    check = validate_horizon_contract(horizons, REFERENCE)
    assert not check.valid and f"horizon_{field}_blank" in check.issue_codes


@pytest.mark.parametrize("refs", [[], ["   "]])
def test_empty_or_blank_evidence_refs_are_invalid(refs: list[str]) -> None:
    horizons = _horizons()
    horizons[0] = horizons[0].model_copy(update={"evidence_refs": refs})
    check = validate_horizon_contract(horizons, REFERENCE)
    assert not check.valid and "horizon_evidence_refs_invalid" in check.issue_codes


def test_history_uses_explicit_cutoff_and_does_not_read_current_time() -> None:
    horizons = _horizons()
    assert validate_horizon_contract(horizons, REFERENCE).valid
    check = validate_horizon_contract(horizons, datetime(2026, 10, 1, tzinfo=UTC))
    assert not check.valid and "horizon_review_not_after_reference" in check.issue_codes


def test_result_and_canonical_refs_are_immutable() -> None:
    horizons = _horizons()
    check = validate_horizon_contract(horizons, REFERENCE)
    horizons[0].evidence_refs.append("new-evidence")
    assert all("new-evidence" not in signature[0] for signature in check.canonical_signatures)
    for attribute in ("valid", "issue_codes", "canonical_signatures", "contract_version"):
        with pytest.raises(FrozenInstanceError):
            setattr(check, attribute, None)
    assert hash(check)

"""Single publication-bound target for the actual unit-comparison input version."""

import json
from copy import deepcopy
from pathlib import Path
from runpy import run_path

import pytest

from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
    DecisionMethodInputsV1,
    DecisionMethodInputsV2,
)


def method_inputs_v2():
    f = run_path(
        str(Path(__file__).resolve().parents[1] / "decision_product/test_action_comparison.py")
    )
    value = f["fixture"]()
    old = value["target"]
    value["schema_version"] = "decision-method-inputs.v2"
    value["target"] = dict(
        schema_version="publication-bound-target.v1",
        candidate_target_ref=f["ref"]("pending-target"),
        anchor_rule_ref=f["ref"]("reviewed-rule"),
        assigned_at="2025-12-31T23:59:00Z",
        window_start=old["window_start"],
        window_end=old["window_end"],
        instrument_ref=old["instrument_ref"],
        horizon=old["horizon"],
        duration_seconds=14400,
        clock_policy_ref=f["ref"]("publication-clock"),
        price_basis="trade_1m_close",
        threshold_fraction=old["threshold_fraction"],
        method_ref=f["ref"]("threshold-method"),
        target_family_ref=old["target_family_ref"],
        label_rule_ref=old["label_rule_ref"],
        protocol_ref=old["protocol_ref"],
        conditioning_ref=old["conditioning_ref"],
    )
    return f["resign"](value)


def test_single_final_target_method_inputs_and_legacy_separation():
    value = method_inputs_v2()
    assert (
        DecisionMethodInputsV2.model_validate(value).target.schema_version
        == "publication-bound-target.v1"
    )
    with pytest.raises(ValueError):
        DecisionMethodInputsV1.model_validate(value)
    for key in ["instrument_ref", "conditioning_ref"]:
        bad = deepcopy(value)
        bad["target"][key] = None
        with pytest.raises(ValueError):
            DecisionMethodInputsV2.model_validate(bad)


def method_inputs_v2_with_account():
    value = method_inputs_v2()
    fixture = Path(__file__).resolve().parents[2] / "apps/decision-desk/src/api/decisionSettingsReceiptFixture.json"
    account = json.loads(fixture.read_text())["intent"]["command"]["values"]["account_snapshot"]
    account.update(as_of="2026-01-01T00:00:00Z", source="paper_policy", account_kind="paper", original={"ref": "ledger-original", "hash": "a" * 64})
    value["account_snapshot"] = account
    value["bindings"]["account"] = {"ref": "account-projection", "hash": "b" * 64}
    value["utility_policy"]["account_ref"] = account["original"]
    helpers = run_path(str(Path(__file__).resolve().parents[1] / "decision_product/test_action_comparison.py"))
    value["bindings"]["account"]["hash"] = helpers["content_hash"](account)
    return helpers["resign"](value)


def test_v2_utility_binds_account_original_not_projection_body_ref():
    value = method_inputs_v2_with_account()
    assert DecisionMethodInputsV2.model_validate(value).utility_policy.account_ref.ref == "ledger-original"
    bad = deepcopy(value)
    bad["utility_policy"]["account_ref"] = value["bindings"]["account"]
    with pytest.raises(ValueError):
        DecisionMethodInputsV2.model_validate(bad)

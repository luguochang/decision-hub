from __future__ import annotations

from copy import deepcopy
from importlib import import_module
from pathlib import Path
from runpy import run_path

import pytest
from pydantic import ValidationError


def test_comparison_v2_parses_real_unit_math_and_rejects_inconsistent_frozen_inputs() -> None:
    contracts = import_module(
        "packages.contracts_py.decision_hub_contracts.generated.decision_product_v2"
    )
    examples = run_path(
        str(Path(__file__).resolve().parents[1] / "decision_product/test_action_comparison.py")
    )
    for horizon in ("4h", "24h", "72h"):
        payload = examples["fixture"](horizon)
        parsed = contracts.DecisionMethodInputsV1.model_validate(payload)
        assert parsed.model_dump(mode="json") == payload
        result = examples["compare"](parsed.model_dump(mode="json"))
        output = contracts.ActionComparisonV2.model_validate(result)
        assert output.model_dump(mode="json") == result
        assert output.selected_action_id == "open_short"
        assert output.account_feasibility == "unknown" and output.executable_contracts is None
        assert output.financial_qualified is False

    # Cost and price changes inside the original window remain valid research.
    payload = examples["fixture"]()
    payload["comparison_as_of"] = "2026-01-01T02:00:00Z"
    payload["comparison_known_at_max"] = "2026-01-01T01:59:59Z"
    payload["cost_policy"]["trade_cost_multiplier"] = "2"
    payload = examples["resign"](payload)
    parsed = contracts.DecisionMethodInputsV1.model_validate(payload)
    assert contracts.ActionComparisonV2.model_validate(
        examples["compare"](parsed.model_dump(mode="json"))
    ).selected_action_id == "no_change"
    payload["price_bridge"]["comparison_price_ref"] = examples["ref"]("changed-price")
    payload["price_bridge"]["comparison_to_forecast_price_ratio"] = "1.02"
    parsed = contracts.DecisionMethodInputsV1.model_validate(payload)
    assert contracts.ActionComparisonV2.model_validate(
        examples["compare"](parsed.model_dump(mode="json"))
    ).selected_action_id == "open_short"

    for path, replacement in (
        (("path_distribution", "scenarios", 0, "probability"), "0.5"),
        (("path_distribution", "scenarios", 1, "scenario_id"), "up-path"),
        (("market_distribution", "direction_distribution", "probabilities", 0, "value"), "0.5"),
        (
            ("market_distribution", "market_target_ref"),
            {"ref": "synthetic:another-target", "hash": "a" * 64},
        ),
        (("scope_id",), "crypto:perp:okx:ETH-USDT-SWAP:trade:1m"),
        (("target", "window_end"), "2026-01-01T05:00:00Z"),
        (("forecast_known_at_max",), "2026-01-01T00:00:01Z"),
        (("comparison_as_of",), "2026-01-01T04:00:00Z"),
        (("comparison_as_of",), "2026-01-01T08:00:00+08:00"),
        (("unit_notional_usdt",), 100),
        (("price_bridge", "comparison_to_forecast_price_ratio"), "1.02"),
        (("action_target_refs", "open_long"), None),
        (("event_probability", "outcome_given_occurrence", "conditioning_ref"), None),
    ):
        malformed = examples["fixture"]()
        cursor = malformed
        for key in path[:-1]:
            cursor = cursor[key]
        cursor[path[-1]] = replacement
        examples["resign"](malformed)
        with pytest.raises(ValidationError):
            contracts.DecisionMethodInputsV1.model_validate(malformed)

    malformed = examples["fixture"]()
    malformed["bindings"]["cost"]["hash"] = "f" * 64
    parsed = contracts.DecisionMethodInputsV1.model_validate(malformed)
    with pytest.raises(ValueError, match="input_body_hash_mismatch:cost"):
        examples["compare"](parsed.model_dump(mode="json"))

    result = examples["compare"](examples["fixture"]())
    result["candidate_results"][0]["outcome"]["target_ref"] = deepcopy(result["target_ref"])
    result["payload_hash"] = examples["content_hash"](
        {key: value for key, value in result.items() if key != "payload_hash"}
    )
    with pytest.raises(ValidationError):
        contracts.ActionComparisonV2.model_validate(result)


def test_settings_v2_preserves_unknown_inputs_and_enforces_canonical_bindings() -> None:
    contracts = import_module(
        "packages.contracts_py.decision_hub_contracts.generated.decision_product_v2"
    )
    original = {"ref": "instrument:btc", "hash": "a" * 64}
    position = {
        "schema_version": "position-context.v2",
        "instrument_id": "okx:BTC-USDT-SWAP",
        "instrument_ref": original,
        "state": "unknown",
        "signed_contracts": None,
        "entry_price": None,
        "isolated_margin": None,
        "margin_mode": "unknown",
        "position_mode": "unknown",
        "hedge_leg": None,
        "leverage": None,
        "liquidation_estimate": None,
        "source": "unknown",
        "ref": None,
        "hash": None,
        "account_ref": None,
    }
    account = {
        "schema_version": "account-snapshot.v2",
        "owner_id": "alice",
        "account_kind": "user_declared",
        "account_id": "declared-account",
        "source": "unknown",
        "as_of": "2026-10-19T00:00:00Z",
        "account_mode": "unknown",
        "positions": None,
        "collateral": None,
        "valuation_ccy": "USDT",
        "equity": None,
        "available_margin": None,
        "fee_tier": None,
        "limits": dict.fromkeys(
            ["max_gross_notional", "max_reserved_margin", "max_fee_buffer", "max_stress_loss"]
        ),
        "original": None,
    }
    utility = {
        "schema_version": "utility-policy.v2",
        "policy_ref": "utility:explicit-default",
        "hash": "b" * 64,
        "source": "research_default",
        "objective": "mean_net",
        "risk_penalty": "0",
        "max_gross_notional": None,
        "max_planned_loss": None,
        "tail_constraints": None,
        "position_scenario": "unknown",
        "margin_buffer_policy": None,
        "allowed_leverage": None,
        "allowed_sides": None,
        "account_ref": None,
    }
    command = {
        "schema_version": "decision-settings-command.v1",
        "request_id": "settings-1",
        "expected_state_version": 0,
        "partition": {
            "account_kind": "user_declared",
            "account_id": "declared-account",
            "instrument_id": "okx:BTC-USDT-SWAP",
            "profile_ref": "paper:net-isolated-1",
        },
        "operation": "upsert",
        "values": {
            "instrument": deepcopy(original),
            "execution_profile": {"ref": "paper:net-isolated-1", "hash": "c" * 64},
            "account_snapshot": account,
            "position_context": position,
            "utility_policy": utility,
        },
    }
    parsed = contracts.DecisionSettingsCommandV1.model_validate(command)
    assert parsed.values.account_snapshot.equity is None
    assert parsed.values.position_context.state == "unknown"
    assert parsed.model_dump(mode="json") == command

    # These mutations break binding, unknown-value or strict wire guarantees.
    for path, value in (
        (("partition", "account_id"), "another-account"),
        (("partition", "instrument_id"), "okx:ETH-USDT-SWAP"),
        (("partition", "profile_ref"), "another-profile"),
        (("values", "instrument", "hash"), "d" * 64),
        (("values", "account_snapshot", "equity"), "10000"),
        (("values", "account_snapshot", "as_of"), "2026-02-30T00:00:00Z"),
        (("values", "account_snapshot", "as_of"), "2026-10-19T08:00:00+08:00"),
        (("values", "position_context", "entry_price"), "100"),
        (("values", "utility_policy", "allowed_sides"), ["long", "long"]),
        (("expected_state_version",), True),
        (("operation",), "remove"),
    ):
        malformed = deepcopy(command)
        cursor = malformed
        for key in path[:-1]:
            cursor = cursor[key]
        cursor[path[-1]] = value
        with pytest.raises(ValidationError):
            contracts.DecisionSettingsCommandV1.model_validate(malformed)

    nested = contracts.PositionContextV2.model_construct(
        **{
            **position,
            "instrument_ref": contracts.DecisionSourceRefV1.model_validate(original),
            "leverage": "10",
        }
    )
    with pytest.raises(ValidationError):
        contracts.DecisionSettingsCommandV1.model_validate(
            {**command, "values": {**command["values"], "position_context": nested}}
        )

    instrument = {
        "schema_version": "instrument-contract.v1",
        "venue": "okx",
        "inst_id": "BTC-USDT-SWAP",
        "instrument_kind": "perpetual",
        "linear_or_inverse": "linear",
        "base": "BTC",
        "quote": "USDT",
        "settle": "USDT",
        "ct_val": "0.01",
        "ct_mult": "1",
        "ct_val_ccy": "BTC",
        "lot_sz": "0.01",
        "min_sz": "0.01",
        "tick_sz": "0.1",
        "price_basis_refs": {"trade": None, "mark": None, "index": None},
        "rule_source_ref": "okx:rules:btc",
        "rule_source_hash": "e" * 64,
        "funding_schedule_ref": None,
        "margin_tier_ref": None,
        "effective_at": None,
        "effective_at_source": "unknown",
        "known_at": "2026-10-19T00:00:00Z",
        "state": "live",
    }
    assert contracts.InstrumentContractV1.model_validate(instrument).effective_at is None
    for patch in (
        {"effective_at_source": "venue_rule"},
        {"ct_val": 0.01},
        {"base": "ETH"},
    ):
        with pytest.raises(ValidationError):
            contracts.InstrumentContractV1.model_validate({**instrument, **patch})

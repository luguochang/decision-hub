"""Canonical shape and local consistency for shared settings contracts.

Canonical shape + local consistency. Never authenticates refs, owners or funds.
Legacy DecisionProductContract and its canonical source remain untouched.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta
from decimal import Decimal, localcontext
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, model_validator

from .decision_candidate_v2_semantics import decision_candidate_v2_issues
from .policy_contract_base import policy_format_checker


def _wire(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _wire(value.model_dump(mode="python"))
    if isinstance(value, datetime):
        if value.utcoffset() != timedelta(0):
            raise ValueError("decision_v2_utc_required")
        return value.isoformat(timespec="microseconds").replace("+00:00", "Z")
    if isinstance(value, dict):
        if any(type(key) is not str for key in value):
            raise ValueError("decision_v2_non_string_key")
        return {key: _wire(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_wire(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("decision_v2_non_finite_number")
    return value


@lru_cache(maxsize=1)
def _canonical() -> dict[str, Any]:
    path = Path(__file__).resolve().parents[3] / "contracts/schemas/decision_product_v2.schema.yaml"
    raw = path.read_bytes()
    if len(raw) > 262144:
        raise ValueError("decision_v2_schema_size_invalid")
    # JSON is valid YAML. No network resolver or copied shape map is needed:
    # the shared/settings document contains local refs only.
    return json.loads(raw)


@lru_cache(maxsize=128)
def _validator(model: type[BaseModel]) -> Draft202012Validator:
    canonical = _canonical()
    name = next(
        (
            key
            for key, definition in canonical["$defs"].items()
            if definition.get("title") == model.__name__
        ),
        None,
    )
    schema = (
        {"$defs": canonical["$defs"], "$ref": f"#/$defs/{name}"}
        if name is not None
        else model.model_json_schema()
    )
    # Anonymous generator helpers remain checked by their canonical parent.
    return Draft202012Validator(schema, format_checker=policy_format_checker())


def _comparison_issues(raw: Any) -> tuple[str, ...]:
    """Local comparison consistency; body hashes and financial math stay with consumers."""
    issues: list[str] = []
    seconds = {"4h": 14400, "24h": 86400, "72h": 259200}

    def check(condition: bool, code: str) -> None:
        if not condition:
            issues.append("decision_v2_comparison_" + code)

    def stamp(value: str) -> datetime:
        return datetime.fromisoformat(value)

    def mass(rows: list[dict], key: str) -> bool:
        with localcontext() as context:
            context.prec = 80
            return sum((Decimal(row[key]) for row in rows), Decimal(0)) == 1

    def visit(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                visit(item)
            return
        if not isinstance(value, dict):
            return
        kind = value.get("schema_version")
        if kind == "event-scenario-preparation-manifest.v1":
            # Packed fragments are transport data; validate complete financial
            # bodies only after exact manifest expansion in the native reader.
            return
        if {"target_ref", "probabilities", "as_of", "kind"} <= value.keys():
            probabilities = value["probabilities"]
            if probabilities is None:
                check(value["kind"] == "unavailable", "unknown_probability_kind")
            else:
                check(
                    len({row["outcome_id"] for row in probabilities}) == len(probabilities)
                    and mass(probabilities, "value"),
                    "probability_partition",
                )
        if kind == "target-spec.v1":
            check(
                (stamp(value["window_end"]) - stamp(value["window_start"])).total_seconds()
                == seconds[value["horizon"]],
                "target_window",
            )
        if kind == "event-probability.v1":
            check(
                value["occurrence_distribution"]["target_ref"] == value["occurrence_target_ref"]
                and value["outcome_given_occurrence"]["target_ref"] == value["outcome_target_ref"]
                and value["outcome_given_occurrence"]["conditioning_ref"]
                == value["event_definition_ref"],
                "event_target",
            )
        if kind == "conditional-market-distribution.v1":
            check(
                value["direction_distribution"]["target_ref"] == value["market_target_ref"]
                and value["direction_distribution"]["conditioning_ref"]
                == value["conditioning_ref"],
                "market_target",
            )
            probabilities = value["direction_distribution"]["probabilities"]
            if probabilities is not None:
                check(
                    {row["outcome_id"] for row in probabilities} == {"up", "neutral", "down"},
                    "direction_partition",
                )
        if value.get("kind") == "assumed_complete_paths":
            rows = value["scenarios"]
            check(
                len({row["scenario_id"] for row in rows}) == len(rows)
                and mass(rows, "probability")
                and all(Decimal(row["terminal_return_fraction"]) > -1 for row in rows),
                "path_partition",
            )
        if set(value) == {"net_profit", "zero", "net_loss"}:
            check(mass([{"value": item} for item in value.values()], "value"), "outcome_mass")
        if (
            "comparison_to_forecast_price_ratio" in value
            and "forecast_price_ref" in value
            and ("comparison_price_ref" in value or "quote_ref" in value)
        ):
            comparison_ref = value.get("comparison_price_ref", value.get("quote_ref"))
            check(
                Decimal(value["comparison_to_forecast_price_ratio"]) == 1
                or value["forecast_price_ref"] != comparison_ref,
                "price_bridge",
            )
        if kind in {
            "decision-method-inputs.v1", "decision-method-inputs.v2", "action-comparison.v2"
        }:
            bindings, market, event = (
                value["bindings"],
                value["market_distribution"],
                value["event_probability"],
            )
            forecast = stamp(value["forecast_as_of"])
            cutoff = stamp(
                value["comparison_as_of"]
                if kind in {"decision-method-inputs.v1", "decision-method-inputs.v2"}
                else value["as_of"]
            )
            check(forecast <= cutoff, "cutoff_order")
            check(stamp(value["price_bridge"]["known_at"]) <= cutoff, "price_known_at")
            check(
                market["instrument_ref"] == bindings["instrument"]
                and market["market_target_ref"] == bindings["target"]
                and market["path_distribution_ref"] == bindings["paths"]
                and market["event_scenario_ref"] == bindings["event_probability"],
                "market_bindings",
            )
            check(
                all(
                    stamp(row["as_of"]) == forecast
                    for row in (
                        event["occurrence_distribution"],
                        event["outcome_given_occurrence"],
                        market["direction_distribution"],
                    )
                ),
                "probability_cutoff",
            )
            ids = (
                {"open_long", "open_short", "no_change"}
                if value["position_branch"] == "flat"
                else {"no_change", "reduce_half", "close"}
            )
            if kind in {"decision-method-inputs.v1", "decision-method-inputs.v2"}:
                target, paths, utility = (
                    value["target"],
                    value["path_distribution"],
                    value["utility_policy"],
                )
                check(
                    value["scope_id"] == "crypto:perp:" + value["instrument_id"] + ":trade:1m",
                    "scope",
                )
                check(
                    target["instrument_ref"] == bindings["instrument"]
                    and market["conditioning_ref"] == target["conditioning_ref"]
                    and paths["market_target_ref"] == bindings["target"]
                    and paths["conditioning_ref"] == target["conditioning_ref"],
                    "target_bindings",
                )
                check(
                    stamp(value["forecast_known_at_max"]) <= forecast
                    and stamp(value["comparison_known_at_max"]) <= cutoff
                    and forecast <= stamp(target["window_start"])
                    and cutoff < stamp(target["window_end"]),
                    "target_clock",
                )
                check(
                    utility["policy_ref"] == bindings["utility"]["ref"]
                    and utility["hash"] == bindings["utility"]["hash"]
                    and utility["account_ref"] == (
                        value["account_snapshot"]["original"] if kind == "decision-method-inputs.v2"
                        and value["account_snapshot"] is not None else bindings["account"]
                    ),
                    "utility_bindings",
                )
                if value["account_snapshot"] is not None:
                    check(
                        bindings["account"] is not None
                        and stamp(value["account_snapshot"]["as_of"]) <= cutoff,
                        "account_cutoff",
                    )
                references = value["action_target_refs"]
                check(
                    set(references) == ids
                    and len({(r["ref"], r["hash"]) for r in references.values()}) == len(ids),
                    "action_targets",
                )
                if paths["supports_market_direction"]:
                    probabilities = market["direction_distribution"]["probabilities"]
                    grouped = dict.fromkeys(("up", "neutral", "down"), Decimal(0))
                    threshold = Decimal(target["threshold_fraction"])
                    for row in paths["scenarios"]:
                        terminal = Decimal(row["terminal_return_fraction"])
                        label = (
                            "up"
                            if terminal > threshold
                            else "down"
                            if terminal < -threshold
                            else "neutral"
                        )
                        grouped[label] += Decimal(row["probability"])
                    check(
                        probabilities is not None
                        and grouped
                        == {row["outcome_id"]: Decimal(row["value"]) for row in probabilities},
                        "direction_coherence",
                    )
            else:
                check(
                    value["scope_id"]
                    in {
                        "crypto:perp:okx:BTC-USDT-SWAP:trade:1m",
                        "crypto:perp:okx:ETH-USDT-SWAP:trade:1m",
                    }
                    and value["target_ref"] == bindings["target"],
                    "output_target",
                )
                rows = value["candidate_results"]
                check(
                    {row["candidate_id"] for row in rows} == ids and len(rows) == len(ids),
                    "candidate_partition",
                )
                selected = [value["selected_action_id"]] if value["selected_action_id"] else []
                ranked = selected + value["alternative_action_ids"]
                check(
                    len(set(ranked)) == len(ranked)
                    and set(ranked)
                    == {row["candidate_id"] for row in rows if row["feasibility"] == "unknown"},
                    "selected_candidates",
                )
                for row in rows:
                    outcome = row["outcome"]
                    check(
                        outcome["target_ref"] == row["action_target_ref"]
                        and outcome["source_ref"] == bindings["paths"]
                        and outcome["conditioning_ref"] == market["conditioning_ref"],
                        "outcome_bindings",
                    )
        for item in value.values():
            visit(item)

    visit(raw)
    return tuple(issues)


def decision_product_v2_issues(raw: Any) -> tuple[str, ...]:
    """Pure relations mirrored in TS; no original/hash/permission verification."""
    issues: list[str] = []

    def visit(value: Any) -> None:
        if isinstance(value, list):
            for child in value:
                visit(child)
            return
        if not isinstance(value, dict):
            return
        kind = value.get("schema_version")
        if kind == "event-scenario-preparation-manifest.v1":
            return
        if kind == "instrument-contract.v1":
            if (value["effective_at"] is None) != (value["effective_at_source"] == "unknown"):
                issues.append("decision_v2_instrument_effective_source_pair")
        if kind == "position-context.v2":
            if (value["ref"] is None) != (value["hash"] is None):
                issues.append("decision_v2_position_ref_pair")
            if value["source"] in {"verified", "paper_policy"} and value["ref"] is None:
                issues.append("decision_v2_position_original_required")
            if value["source"] == "unknown" and (
                value["state"] != "unknown"
                or value["ref"] is not None
                or any(
                    value[key] is not None
                    for key in (
                        "signed_contracts",
                        "entry_price",
                        "isolated_margin",
                        "leverage",
                        "liquidation_estimate",
                    )
                )
            ):
                issues.append("decision_v2_unknown_position_has_values")
            if value["position_mode"] == "hedge":
                if value["hedge_leg"] not in {"long", "short"} or value["state"] not in {
                    value["hedge_leg"],
                    "flat",
                    "unknown",
                }:
                    issues.append("decision_v2_hedge_leg_mismatch")
            elif value["hedge_leg"] is not None:
                issues.append("decision_v2_net_has_hedge_leg")
        if kind == "account-snapshot.v2":
            if value["source"] in {"verified", "paper_policy"} and value["original"] is None:
                issues.append("decision_v2_account_original_required")
        if kind in {"decision-settings-command.v1", "decision-settings-revision.v1"}:
            if (value["operation"] == "remove") != (value["values"] is None):
                issues.append("decision_v2_settings_operation_values")
            if value["values"] is not None:
                body, partition = value["values"], value["partition"]
                account, position = body["account_snapshot"], body["position_context"]
                if (account["account_kind"], account["account_id"]) != (
                    partition["account_kind"],
                    partition["account_id"],
                ):
                    issues.append("decision_v2_settings_account_mismatch")
                if position["instrument_id"] != partition["instrument_id"]:
                    issues.append("decision_v2_settings_instrument_mismatch")
                if body["execution_profile"]["ref"] != partition["profile_ref"]:
                    issues.append("decision_v2_settings_profile_mismatch")
                if (
                    kind == "decision-settings-revision.v1"
                    and account["owner_id"] != value["owner_id"]
                ):
                    issues.append("decision_v2_settings_owner_mismatch")
            if kind == "decision-settings-revision.v1" and (
                (value["revision_no"] == 1) != (value["parent"] is None)
            ):
                issues.append("decision_v2_settings_parent_mismatch")
        # Values have no discriminator in the existing settings artifact.
        if set(value) == {
            "instrument",
            "execution_profile",
            "account_snapshot",
            "position_context",
            "utility_policy",
        }:
            if value["instrument"] != value["position_context"]["instrument_ref"]:
                issues.append("decision_v2_settings_instrument_ref_mismatch")
        for child in value.values():
            visit(child)

    visit(raw)
    return tuple(issues)


class DecisionProductV2Contract(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        allow_inf_nan=False,
        revalidate_instances="always",
        regex_engine="python-re",
    )

    @model_validator(mode="before")
    @classmethod
    def canonical_shape(cls, value: Any) -> Any:
        projected = _wire(value)
        error = next(_validator(cls).iter_errors(projected), None)
        if error is not None:
            path = ".".join(str(part) for part in error.absolute_path)
            raise ValueError(f"decision_v2_shape_invalid:{error.validator}:{path}")
        # Check comparison relationships on the original wire representation.
        # Datetime parsing must not move the forecast or comparison cutoff.
        issues = _comparison_issues(projected)
        if issues:
            raise ValueError(issues[0])
        issues = decision_candidate_v2_issues(projected)
        if issues:
            raise ValueError(issues[0])
        # Return the checked shape so constructed nested instances are parsed
        # again, rather than retaining an unchecked embedded model object.
        return projected

    @model_validator(mode="after")
    def relations(self):
        issues = decision_product_v2_issues(self.model_dump(mode="json"))
        if issues:
            raise ValueError(issues[0])
        return self

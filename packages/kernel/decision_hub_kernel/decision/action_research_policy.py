"""Versioned BTC/ETH research inputs shared by producer and Action admission.

These policies describe an unqualified paper/research method. They are not
observed prices, verified account fees, calibrated probabilities, or orders.
Changing a definition changes its reference; old records retain the old hash.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Any

from .action_policy import validate_condition_expression


def policy_hash(body: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            body,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    ).hexdigest()


def _identified(kind: str, body: dict[str, Any]) -> dict[str, Any]:
    digest = policy_hash(body)
    return {"ref": f"decision-product:{kind}.v1:{digest}", "hash": digest, "definition": body}


def research_action_catalog(instrument: str) -> dict[str, Any]:
    """A new value per call; source selection must authenticate the instrument."""
    if instrument not in {"BTC-USDT", "ETH-USDT"}:
        raise ValueError("action_research_instrument_unsupported")
    asset = instrument.removesuffix("-USDT")
    scope = f"crypto:spot:okx:{instrument}:trade:1m"
    feature_units = {
        "close": f"USDT/{asset}",
        "box_high_6h": f"USDT/{asset}",
        "box_low_6h": f"USDT/{asset}",
        "risk_unit_20h": f"USDT/{asset}",
        "volume_ratio_1h": "ratio",
        "return_6h": "fraction",
    }
    size = _identified(
        "size",
        {
            "equity": "10000",
            "currency": "USDT",
            "risk_fraction": "0.005",
            "new_nominal_cap_fraction": "0.25",
            "existing_long_initial_fraction": "1",
            "reduce_fraction": "0.5",
            "leverage": False,
            "shorts": False,
            "orders": False,
            "widen_open_loss_budget": False,
        },
    )
    cost = _identified(
        "cost",
        {
            "kind": "explicit_assumption",
            "currency": "USDT",
            "fee_fraction_each_side": "0.001",
            "slippage_fraction_each_side": "0.0005",
            "stress_fee_fraction_each_side": "0.002",
            "stress_slippage_fraction_each_side": "0.001",
            "spread": "actual_bid_ask_once",
            "account_fee_verified": False,
        },
    )
    targets = {}
    for horizon, duration in (("30m", 1800), ("24h", 86400), ("72h", 259200)):
        targets[horizon] = _identified(
            "target",
            {
                "scope_id": scope,
                "horizon": horizon,
                "probability_target": "target_before_stop",
                "probability_unavailable": None,
                "risk_scale": "median_true_range_previous_20_complete_1h",
                "initial_stop_U": "1",
                "initial_target_U": "2",
                "max_hold_seconds": min(duration, 86400),
                "reaction_delay_seconds": 60,
                "quote_max_age_seconds": 10,
                "quote_wait_seconds": 60,
                "same_bar_stop_target": "ambiguous",
            },
        )
    return {
        "schema_version": "research-action-catalog.v1",
        "scope_id": scope,
        "instrument": instrument,
        "source": "okx",
        "calendar_ref": "crypto-24x7.v1",
        "features": [
            {"feature": feature, "scope_id": scope, "unit": unit}
            for feature, unit in feature_units.items()
        ],
        "feature_definitions": {
            "close": "last complete UTC 1m trade bar close",
            "box_high_6h": "maximum high of prior 360 complete UTC 1m trade bars",
            "box_low_6h": "minimum low of prior 360 complete UTC 1m trade bars",
            "risk_unit_20h": (
                "median true range of prior 20 complete UTC 1h bars; prior close required"
            ),
            "volume_ratio_1h": (
                "last complete 1h base volume / median of preceding 20 complete 1h base volumes"
            ),
            "return_6h": "close / close 360 complete UTC 1m bars earlier - 1",
        },
        "size_policy": size,
        "cost_policy": cost,
        "targets": targets,
        "condition_evaluation_seconds": 60,
        "minimum_entry_distinct_closed_bars": 2,
        "minimum_volume_ratio_assumption": "1.2",
        "qualification": "research_trial_only",
    }


def _feature_bindings(node: dict[str, Any]) -> set[tuple[str, str, str]]:
    if node["op"] == "compare":
        return {(node["feature"], node["scope_id"], node["unit"])}
    return set().union(*(_feature_bindings(child) for child in node["args"]))


def action_catalog_problems(candidate: Any, catalog: dict[str, Any]) -> list[dict[str, Any]]:
    """Field diagnostics shared by authoring and publication; no mutation/grant.

    Both arguments must already be typed/authenticated by their respective
    callers. Paths are relative to this Action, not the whole synthesis.
    """
    issues: list[dict[str, Any]] = []

    def mismatch(path: str, actual: Any, expected: Any, code: str) -> None:
        if actual != expected:
            issues.append(dict(path=path, code=code, actual=actual, expected=expected))

    target = catalog["targets"].get(candidate.horizon)
    mismatch(
        "scope_id",
        candidate.scope_id,
        catalog["scope_id"],
        "action_research_scope_horizon_mismatch",
    )
    if target is None or candidate.scope_id != catalog["scope_id"]:
        if target is None:
            issues.append(
                dict(
                    path="horizon",
                    code="action_research_scope_horizon_mismatch",
                    actual=candidate.horizon,
                    expected=list(catalog["targets"]),
                )
            )
    for field, expected in (
        ("size_policy_ref", catalog["size_policy"]["ref"]),
        ("cost_policy_ref", catalog["cost_policy"]["ref"]),
    ):
        mismatch(
            field, getattr(candidate, field), expected, "action_research_policy_reference_mismatch"
        )
    if target is not None:
        mismatch(
            "target_spec_ref",
            candidate.target_spec_ref,
            target["ref"],
            "action_research_policy_reference_mismatch",
        )
        mismatch(
            "probability.target_id",
            candidate.probability.target_id,
            target["ref"],
            "action_research_policy_reference_mismatch",
        )
    allowed = frozenset(
        (row["feature"], row["scope_id"], row["unit"]) for row in catalog["features"]
    )
    units = {row["feature"]: row["unit"] for row in catalog["features"]}

    def expression_issues(node: dict[str, Any], path: str) -> None:
        if node["op"] != "compare":
            for index, child in enumerate(node["args"]):
                expression_issues(child, f"{path}.args[{index}]")
            return
        feature = node["feature"]
        if feature not in units:
            issues.append(
                dict(
                    path=path + ".feature",
                    code="action_feature_not_admitted",
                    actual=feature,
                    expected=list(units),
                )
            )
        else:
            mismatch(path + ".unit", node["unit"], units[feature], "action_feature_not_admitted")
        mismatch(
            path + ".scope_id", node["scope_id"], catalog["scope_id"], "action_feature_not_admitted"
        )
        # Only positive spot price levels. Returns may be zero/negative;
        # volatility/volume conditions have different, independently defined semantics.
        if feature in {"close", "box_high_6h", "box_low_6h"} and Decimal(node["threshold"]) <= 0:
            issues.append(
                dict(
                    path=path + ".threshold",
                    code="action_research_price_threshold_nonpositive",
                    actual=node["threshold"],
                    expected=(
                        "positive decimal price level from retained evidence or explicitly "
                        "stated policy; never 0 as an unknown placeholder"
                    ),
                )
            )

    for purpose in ("entry", "exit", "invalidation"):
        for index, condition in enumerate(getattr(candidate, purpose)):
            path = f"{purpose}[{index}]"
            mismatch(
                path + ".purpose",
                condition.purpose,
                purpose,
                "action_research_condition_binding_mismatch",
            )
            mismatch(
                path + ".calendar_ref",
                condition.calendar_ref,
                catalog["calendar_ref"],
                "action_research_condition_binding_mismatch",
            )
            mismatch(
                path + ".evaluation_seconds",
                condition.evaluation_seconds,
                catalog["condition_evaluation_seconds"],
                "action_research_condition_cadence_unsupported",
            )
            if purpose == "entry" and condition.min_fresh_samples < 2:
                issues.append(
                    dict(
                        path=path + ".min_fresh_samples",
                        code="action_research_entry_confirmation_insufficient",
                        actual=condition.min_fresh_samples,
                        expected=">=2 distinct closed bars",
                    )
                )
            expression = condition.expression.model_dump(mode="json")
            try:
                validate_condition_expression(expression)
            except ValueError as error:
                issues.append(
                    dict(
                        path=path + ".expression",
                        code=str(error),
                        actual=expression,
                        expected="bounded condition-expression.v1 AST",
                    )
                )
                continue
            expression_issues(expression, path + ".expression")
            referenced = _feature_bindings(expression)
            declared = set()
            for offset, item in enumerate(condition.input_requirements):
                if (item.metric, item.scope, item.unit) not in allowed or item.source != "okx":
                    issues.append(
                        dict(
                            path=f"{path}.input_requirements[{offset}]",
                            code="action_research_condition_input_unregistered",
                            actual=dict(
                                metric=item.metric,
                                scope=item.scope,
                                unit=item.unit,
                                source=item.source,
                            ),
                            expected=dict(
                                metric=item.metric if item.metric in units else list(units),
                                scope=catalog["scope_id"],
                                unit=units.get(item.metric),
                                source="okx",
                            ),
                        )
                    )
                declared.add((item.metric, item.scope, item.unit))
            if declared != referenced:
                issues.append(
                    dict(
                        path=path + ".input_requirements",
                        code="action_research_condition_inputs_incomplete",
                        actual=[list(row) for row in sorted(declared)],
                        expected=[list(row) for row in sorted(referenced)],
                    )
                )
    return issues


def validate_action_catalog_candidate(candidate: Any, catalog: dict[str, Any]) -> None:
    """Same checks at publication; no source, probability or review authority."""
    issues = action_catalog_problems(candidate, catalog)
    if issues:
        raise ValueError(issues[0]["code"])

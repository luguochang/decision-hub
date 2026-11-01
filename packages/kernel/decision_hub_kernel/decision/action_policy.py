"""Deterministic action conditions and lifecycle rules.

Readers authenticate provenance and scope admission before constructing inputs.
These functions perform no network, persistence, model calls or order execution.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Literal

from ..ports.market_path import aware_utc, decimal_text

Truth = Literal["true", "false", "unknown"]
FeatureKey = tuple[str, str]
FeatureBinding = tuple[str, str, str]


class ActionPolicyError(ValueError):
    """Stable codes are suitable for a durable rejection/evaluation receipt."""


@dataclass(frozen=True)
class ActionFeatureValue:
    feature: str
    scope_id: str
    unit: str
    value: Decimal | None
    observed_at: datetime
    known_at: datetime
    quality: Literal["accepted", "unknown", "stale", "rejected"]
    source_ref: str
    source_hash: str


@dataclass(frozen=True)
class ActionConditionResult:
    truth: Truth
    status: Literal["ok", "error"]
    reasons: tuple[str, ...] = ()
    input_refs: tuple[str, ...] = ()


def _decimal(value: object) -> Decimal:
    if not isinstance(value, (str, Decimal)):
        raise ActionPolicyError("action_decimal_string_required")
    try:
        parsed = Decimal(value)
        # Reuse the shared finite precision policy; never silently round.
        decimal_text(parsed)
    except (InvalidOperation, ValueError, OverflowError) as exc:
        raise ActionPolicyError("action_decimal_invalid") from exc
    return parsed


def validate_condition_expression(
    expression: Mapping,
    *,
    allowed_features: frozenset[FeatureBinding] | None = None,
) -> None:
    """Enforce the bounded AST; None is structural-only, not scope admission."""
    leaves = 0

    def visit(node: object, depth: int) -> None:
        nonlocal leaves
        if depth > 8:
            raise ActionPolicyError("action_condition_complexity_exceeded")
        if not isinstance(node, Mapping):
            raise ActionPolicyError("action_condition_invalid")
        op = node.get("op")
        if not isinstance(op, str):
            raise ActionPolicyError("action_condition_invalid")
        if op in {"all", "any"}:
            if set(node) != {"op", "args"}:
                raise ActionPolicyError("action_condition_invalid")
            args = node["args"]
            if not isinstance(args, (list, tuple)) or not 1 <= len(args) <= 16:
                raise ActionPolicyError("action_condition_arity_invalid")
            for child in args:
                visit(child, depth + 1)
            return
        if op != "compare" or set(node) != {
            "op", "feature", "scope_id", "unit", "cmp", "threshold", "max_age_seconds"
        }:
            raise ActionPolicyError("action_condition_invalid")
        leaves += 1
        if leaves > 32:
            raise ActionPolicyError("action_condition_complexity_exceeded")
        if any(not isinstance(node[key], str) or not node[key].strip()
               or len(node[key]) > 256 for key in ("feature", "scope_id", "unit")):
            raise ActionPolicyError("action_feature_binding_invalid")
        if not isinstance(node["cmp"], str) or node["cmp"] not in {"gt", "gte", "lt", "lte"}:
            raise ActionPolicyError("action_comparator_invalid")
        if not isinstance(node["threshold"], str):
            raise ActionPolicyError("action_decimal_string_required")
        _decimal(node["threshold"])
        age = node["max_age_seconds"]
        if type(age) is not int or not 1 <= age <= 2_592_000:
            raise ActionPolicyError("action_feature_age_invalid")
        binding = (node["feature"], node["scope_id"], node["unit"])
        if allowed_features is not None and binding not in allowed_features:
            raise ActionPolicyError("action_feature_not_admitted")

    visit(expression, 1)


def evaluate_condition_expression(
    expression: Mapping,
    features: Mapping[FeatureKey, ActionFeatureValue],
    *,
    as_of: datetime,
    allowed_features: frozenset[FeatureBinding],
) -> ActionConditionResult:
    """Strong Kleene logic; malformed inputs retain error and cannot trigger.

    Every leaf is evaluated so a true OR branch cannot conceal an unauthorized
    scope, conflicting unit or bad original. Missing data is retained as a gap.
    """
    # The structural validator permits None for authoring, but the execution
    # boundary must never interpret a missing manifest as unrestricted access.
    if not isinstance(allowed_features, frozenset) or any(
        not isinstance(binding, tuple) or len(binding) != 3
        or any(not isinstance(part, str) or not part.strip() or len(part) > 256
               for part in binding)
        for binding in allowed_features
    ):
        return ActionConditionResult("unknown", "error", ("action_allowed_features_invalid",))
    if not isinstance(as_of, datetime):
        return ActionConditionResult("unknown", "error", ("action_evaluation_time_invalid",))
    if not isinstance(features, Mapping):
        return ActionConditionResult("unknown", "error", ("action_features_invalid",))
    try:
        cutoff = aware_utc(as_of)
        validate_condition_expression(expression, allowed_features=allowed_features)
    except (ActionPolicyError, ValueError) as exc:
        return ActionConditionResult("unknown", "error", (str(exc),))

    def leaf(node: Mapping) -> ActionConditionResult:
        key = (node["feature"], node["scope_id"])
        sample = features.get(key)
        if sample is None:
            if key in features:
                return ActionConditionResult("unknown", "error", ("action_input_type_invalid",))
            return ActionConditionResult("unknown", "ok", (f"{key[0]}:missing",))
        if not isinstance(sample, ActionFeatureValue):
            return ActionConditionResult("unknown", "error", ("action_input_type_invalid",))
        if (sample.feature, sample.scope_id, sample.unit) != (*key, node["unit"]):
            return ActionConditionResult("unknown", "error", ("action_feature_binding_mismatch",))
        if (not isinstance(sample.source_ref, str) or not sample.source_ref.strip()
                or not isinstance(sample.source_hash, str) or len(sample.source_hash) != 64 or any(
            char not in "0123456789abcdef" for char in sample.source_hash
        )):
            return ActionConditionResult("unknown", "error", ("action_input_original_missing",))
        refs = (sample.source_ref,)
        if (not isinstance(sample.observed_at, datetime)
                or not isinstance(sample.known_at, datetime)):
            return ActionConditionResult("unknown", "error", ("action_input_time_invalid",), refs)
        if (not isinstance(sample.quality, str)
                or sample.quality not in {"accepted", "unknown", "stale", "rejected"}):
            return ActionConditionResult(
                "unknown", "error", ("action_input_quality_invalid",), refs,
            )
        try:
            observed, known = aware_utc(sample.observed_at), aware_utc(sample.known_at)
        except ValueError as exc:
            return ActionConditionResult("unknown", "error", (str(exc),), refs)
        if known < observed:
            return ActionConditionResult(
                "unknown", "error", ("action_input_time_order_invalid",), refs,
            )
        if observed > cutoff or known > cutoff:
            return ActionConditionResult("unknown", "ok", (f"{key[0]}:future",), refs)
        if sample.quality != "accepted" or sample.value is None:
            return ActionConditionResult("unknown", "ok", (f"{key[0]}:unavailable",), refs)
        if (cutoff - observed).total_seconds() > node["max_age_seconds"]:
            return ActionConditionResult("unknown", "ok", (f"{key[0]}:stale",), refs)
        try:
            value, threshold = _decimal(sample.value), _decimal(node["threshold"])
        except ActionPolicyError as exc:
            return ActionConditionResult("unknown", "error", (str(exc),), refs)
        comparison = {
            "gt": value > threshold, "gte": value >= threshold,
            "lt": value < threshold, "lte": value <= threshold,
        }[node["cmp"]]
        return ActionConditionResult("true" if comparison else "false", "ok", (), refs)

    def evaluate(node: Mapping) -> ActionConditionResult:
        if node["op"] == "compare":
            return leaf(node)
        children = [evaluate(child) for child in node["args"]]
        reasons = tuple(dict.fromkeys(reason for child in children for reason in child.reasons))
        refs = tuple(dict.fromkeys(ref for child in children for ref in child.input_refs))
        if any(child.status == "error" for child in children):
            return ActionConditionResult("unknown", "error", reasons, refs)
        truths = {child.truth for child in children}
        if node["op"] == "all":
            truth = "false" if "false" in truths else "unknown" if "unknown" in truths else "true"
        else:
            truth = "true" if "true" in truths else "unknown" if "unknown" in truths else "false"
        return ActionConditionResult(truth, "ok", reasons, refs)

    return evaluate(expression)


_TRANSITIONS = {
    ("proposed", "publish"): "armed", ("proposed", "invalidate"): "invalidated",
    ("armed", "trigger"): "triggered", ("armed", "expire"): "expired",
    ("armed", "invalidate"): "invalidated", ("armed", "supersede"): "superseded",
    ("triggered", "settle"): "completed", ("triggered", "expire"): "completed",
    ("triggered", "invalidate"): "invalidated", ("triggered", "supersede"): "superseded",
}


def transition_action_plan(state: str, event: str) -> str:
    try:
        return _TRANSITIONS[state, event]
    except KeyError as exc:
        raise ActionPolicyError("action_plan_transition_invalid") from exc


def confirmed_bar_streak(
    previous: int, last_bar_at: datetime | None, closed_bar_at: datetime,
    truth: Truth, *, required: int, interval_seconds: int = 60,
) -> tuple[int, bool]:
    """The caller stores the watermark under its revision/fencing transaction."""
    if (type(previous) is not int or previous < 0 or type(required) is not int or required < 1
            or type(interval_seconds) is not int or interval_seconds < 1
            or truth not in {"true", "false", "unknown"}):
        raise ActionPolicyError("action_streak_input_invalid")
    bar = aware_utc(closed_bar_at)
    last = aware_utc(last_bar_at) if last_bar_at is not None else None
    if last is not None and bar <= last:
        return previous, False
    continuous = last is not None and (bar - last).total_seconds() == interval_seconds
    count = min((previous if continuous else 0) + 1, required) if truth == "true" else 0
    return count, count >= required


def select_plan_event(
    *, state: str, action: str, at: datetime, valid_until: datetime,
    qualification: str, invalidation: ActionConditionResult,
    exit_condition: ActionConditionResult, entry_condition: ActionConditionResult,
) -> str | None:
    """Inputs are already sample-confirmed. Expiry/known invalidation take priority."""
    if state not in {"armed", "triggered"}:
        return None
    if aware_utc(at) >= aware_utc(valid_until):
        return "expire"
    if invalidation.status == "ok" and invalidation.truth == "true":
        return "invalidate"
    if state == "triggered":
        return (
            "settle" if exit_condition.status == "ok" and exit_condition.truth == "true" else None
        )
    if action not in {"enter_long", "reduce_long", "exit_long", "hold", "wait"}:
        raise ActionPolicyError("action_kind_invalid")
    if qualification not in {"research_trial", "qualified"}:
        return None
    if action in {"hold", "wait"}:
        return None
    # Missing risk/exit protection cannot silently allow NEW exposure; an
    # already-triggered plan retains its existing protection and position.
    if action == "enter_long":
        if any(item.status == "error" or item.truth == "unknown"
               for item in (invalidation, exit_condition)):
            return None
        if exit_condition.truth == "true":
            return None
    return "trigger" if entry_condition.status == "ok" and entry_condition.truth == "true" else None

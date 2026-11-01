from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from packages.kernel.decision_hub_kernel.decision.action_policy import (
    ActionConditionResult,
    ActionFeatureValue,
    ActionPolicyError,
    confirmed_bar_streak,
    evaluate_condition_expression,
    select_plan_event,
    transition_action_plan,
    validate_condition_expression,
)

AT = datetime(2026, 1, 1, 12, tzinfo=UTC)
SCOPE = "crypto:spot:okx:BTC-USDT:trade:1m"
EXPR = {"op": "compare", "feature": "close", "scope_id": SCOPE,
        "unit": "USDT/BTC", "cmp": "gte", "threshold": "100", "max_age_seconds": 120}
ALLOWED = frozenset({("close", SCOPE, "USDT/BTC"), ("volume", SCOPE, "BTC")})
SAMPLE = ActionFeatureValue("close", SCOPE, "USDT/BTC", Decimal("101"), AT, AT,
                            "accepted", "raw://original", "a" * 64)


def evaluate(sample=SAMPLE, expression=EXPR, allowed=ALLOWED):
    features = {} if sample is None else {("close", SCOPE): sample}
    return evaluate_condition_expression(expression, features, as_of=AT, allowed_features=allowed)


@pytest.mark.parametrize("changes,truth,status,reason", [
    ({}, "true", "ok", None),
    ({"value": Decimal("99")}, "false", "ok", None),
    ({"known_at": AT+timedelta(seconds=1)}, "unknown", "ok", "close:future"),
    ({"observed_at": AT-timedelta(seconds=121)}, "unknown", "ok", "close:stale"),
    ({"quality": "rejected"}, "unknown", "ok", "close:unavailable"),
    ({"value": None}, "unknown", "ok", "close:unavailable"),
    ({"value": Decimal("NaN")}, "unknown", "error", "action_decimal_invalid"),
    ({"unit": "USD/BTC"}, "unknown", "error", "action_feature_binding_mismatch"),
    ({"scope_id": SCOPE.replace("spot", "perpetual_swap")},
     "unknown", "error", "action_feature_binding_mismatch"),
    ({"source_hash": "bad"}, "unknown", "error", "action_input_original_missing"),
    ({"known_at": AT-timedelta(seconds=1)}, "unknown", "error", "action_input_time_order_invalid"),
])
def test_knownness_originals_and_exact_binding(changes, truth, status, reason):
    result = evaluate(replace(SAMPLE, **changes))
    assert (result.truth, result.status) == (truth, status)
    if reason:
        assert reason in result.reasons


def test_missing_is_unknown_and_unadmitted_feature_is_error():
    assert evaluate(None).truth == "unknown"
    assert evaluate(allowed=frozenset()).reasons == ("action_feature_not_admitted",)


@pytest.mark.parametrize("op,value,expected", [
    ("all", "101", "unknown"), ("all", "99", "false"),
    ("any", "101", "true"), ("any", "99", "unknown"),
])
def test_three_value_logic_keeps_missing_branch(op, value, expected):
    missing = {**EXPR, "feature": "volume", "unit": "BTC"}
    result = evaluate(replace(SAMPLE, value=Decimal(value)), {"op": op, "args": [EXPR, missing]})
    assert result.truth == expected
    assert "volume:missing" in result.reasons


def test_true_or_cannot_conceal_illegal_scope():
    other = {**EXPR, "scope_id": "not-admitted"}
    result = evaluate(expression={"op": "any", "args": [EXPR, other]})
    assert result.status == "error"
    assert result.truth == "unknown"


def test_complexity_at_boundaries():
    expr = EXPR
    for _ in range(7):
        expr = {"op": "all", "args": [expr]}
    validate_condition_expression(expr)
    with pytest.raises(ActionPolicyError, match="complexity"):
        validate_condition_expression({"op": "all", "args": [expr]})
    thirty_two = {"op": "all", "args": [
        {"op": "any", "args": [EXPR] * 16}, {"op": "any", "args": [EXPR] * 16},
    ]}
    validate_condition_expression(thirty_two)
    with pytest.raises(ActionPolicyError, match="complexity"):
        validate_condition_expression({"op": "all", "args": [thirty_two, EXPR]})


@pytest.mark.parametrize("expr", [
    {"op": "all", "args": []}, {"op": "any", "args": [EXPR] * 17},
    {**EXPR, "threshold": 100}, {**EXPR, "threshold": "Infinity"},
    {**EXPR, "max_age_seconds": True}, {**EXPR, "code": "print('unsafe')"},
    {**EXPR, "op": []}, {**EXPR, "cmp": {}},
])
def test_invalid_ast_rejected(expr):
    with pytest.raises(ActionPolicyError):
        validate_condition_expression(expr)


def test_duplicate_gap_and_unknown_cannot_accumulate_confirmation():
    assert confirmed_bar_streak(1, AT, AT, "true", required=2) == (1, False)
    assert confirmed_bar_streak(1, AT, AT-timedelta(minutes=1), "true", required=2) == (1, False)
    assert confirmed_bar_streak(1, AT, AT+timedelta(minutes=1), "true", required=2) == (2, True)
    assert confirmed_bar_streak(1, AT, AT+timedelta(minutes=2), "true", required=2) == (1, False)
    assert confirmed_bar_streak(1, AT, AT+timedelta(minutes=1), "unknown", required=2) == (0, False)


def test_expiry_and_terminal_state_are_not_execution_claims():
    assert transition_action_plan("armed", "expire") == "expired"
    assert transition_action_plan("triggered", "expire") == "completed"
    with pytest.raises(ActionPolicyError):
        transition_action_plan("completed", "publish")


def event(**changes):
    inputs = dict(state="armed", action="enter_long", at=AT, valid_until=AT+timedelta(hours=1),
                  qualification="research_trial", invalidation=ActionConditionResult("false", "ok"),
                  exit_condition=ActionConditionResult("false", "ok"),
                  entry_condition=ActionConditionResult("true", "ok"))
    return select_plan_event(**(inputs | changes))


def test_valid_trial_can_trigger_without_calibrated_probability():
    assert event() == "trigger"
    assert event(qualification="blocked") is None
    assert event(qualification="future_typo") is None


def test_expiry_invalidation_exit_precede_new_exposure():
    assert event(valid_until=AT) == "expire"
    assert event(invalidation=ActionConditionResult("true", "ok")) == "invalidate"
    assert event(exit_condition=ActionConditionResult("true", "ok")) is None
    assert event(invalidation=ActionConditionResult("unknown", "ok")) is None
    assert event(state="triggered", exit_condition=ActionConditionResult("true", "ok")) == "settle"
    assert event(state="triggered", entry_condition=ActionConditionResult("unknown", "ok")) is None


def test_known_reduce_action_is_not_suppressed_by_unknown_entry_protection():
    assert event(
        action="reduce_long", exit_condition=ActionConditionResult("unknown", "ok"),
    ) == "trigger"
    assert event(
        action="exit_long", invalidation=ActionConditionResult("unknown", "ok"),
    ) == "trigger"
    assert event(action="hold") is None
    assert event(action="wait") is None


@pytest.mark.parametrize("allowed", [
    None, set(ALLOWED), list(ALLOWED), frozenset({"close"}),
    frozenset({("close", SCOPE)}), frozenset({("close", SCOPE, None)}),
])
def test_runtime_allowlist_must_be_explicit_and_typed(allowed):
    result = evaluate(allowed=allowed)
    assert (result.truth, result.status, result.reasons) == (
        "unknown", "error", ("action_allowed_features_invalid",),
    )


def test_structural_validation_can_omit_admission_but_runtime_cannot():
    validate_condition_expression(EXPR, allowed_features=None)
    assert evaluate(allowed=None).status == "error"
    assert evaluate(allowed=frozenset()).reasons == ("action_feature_not_admitted",)
    assert evaluate().truth == "true"


@pytest.mark.parametrize("as_of", [None, "2026-01-01T12:00:00Z", 123])
def test_runtime_invalid_evaluation_time_is_error(as_of):
    result = evaluate_condition_expression(
        EXPR, {("close", SCOPE): SAMPLE}, as_of=as_of, allowed_features=ALLOWED,
    )
    assert (result.truth, result.status, result.reasons) == (
        "unknown", "error", ("action_evaluation_time_invalid",),
    )


@pytest.mark.parametrize("features", [None, [], "facts"])
def test_runtime_invalid_feature_container_is_error(features):
    result = evaluate_condition_expression(EXPR, features, as_of=AT, allowed_features=ALLOWED)
    assert (result.truth, result.status, result.reasons) == (
        "unknown", "error", ("action_features_invalid",),
    )


@pytest.mark.parametrize("sample", [None, {}, "fact", 1])
def test_runtime_invalid_sample_object_is_error(sample):
    result = evaluate_condition_expression(
        EXPR, {("close", SCOPE): sample}, as_of=AT, allowed_features=ALLOWED,
    )
    assert (result.truth, result.status, result.reasons) == (
        "unknown", "error", ("action_input_type_invalid",),
    )


@pytest.mark.parametrize("changes,reason", [
    ({"observed_at": None}, "action_input_time_invalid"),
    ({"known_at": "2026-01-01T12:00:00Z"}, "action_input_time_invalid"),
    ({"source_hash": None}, "action_input_original_missing"),
    ({"source_hash": ["a"] * 64}, "action_input_original_missing"),
    ({"source_ref": []}, "action_input_original_missing"),
    ({"source_ref": "   "}, "action_input_original_missing"),
    ({"quality": []}, "action_input_quality_invalid"),
    ({"quality": None}, "action_input_quality_invalid"),
    ({"quality": [], "known_at": AT+timedelta(seconds=1)}, "action_input_quality_invalid"),
])
def test_runtime_invalid_sample_fields_are_auditable_errors(changes, reason):
    result = evaluate(replace(SAMPLE, **changes))
    assert (result.truth, result.status, result.reasons) == ("unknown", "error", (reason,))


def test_bad_sample_does_not_prevent_next_independent_evaluation():
    assert evaluate(replace(SAMPLE, source_hash=None)).status == "error"
    assert evaluate().truth == "true"

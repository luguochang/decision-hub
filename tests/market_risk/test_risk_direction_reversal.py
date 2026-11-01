"""Signed moves in an absolute-magnitude episode must remain observable."""

import json
from datetime import timedelta
from decimal import Decimal

import pytest

from packages.kernel.decision_hub_kernel.application.outbox import OutboxService
from packages.kernel.decision_hub_kernel.application.risk_monitor import RiskMonitorService
from packages.kernel.decision_hub_kernel.persistence.db import OutboxRecord, as_utc
from packages.kernel.decision_hub_kernel.persistence.market_case_records import (
    MarketComponentAuditRecord,
)
from packages.kernel.decision_hub_kernel.persistence.risk_records import (
    RiskAlertRecord,
    RiskConditionEvaluationRecord,
    RiskMonitorPlanRecord,
)
from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash
from packages.kernel.decision_hub_kernel.ports.risk_monitor import RiskConditionV1
from tests.market_risk.test_risk_monitor import commit, rule, sample
from tests.market_risk.test_risk_monitor import runtime as runtime


@pytest.mark.parametrize("first,reverse", [("-0.02", "0.02"), ("0.02", "-0.02")])
def test_confirmed_reverse_move_creates_new_episode(runtime, first, reverse):
    database, service, clock = runtime
    commit(service, clock, first)
    with database.session() as session:
        original = session.query(RiskAlertRecord).one()
        frozen_original = (original.payload_json, original.payload_hash)
    clock[0] += timedelta(seconds=60)
    commit(service, clock, reverse)
    with database.session() as session:
        alerts = session.query(RiskAlertRecord).order_by(RiskAlertRecord.created_at).all()
        assert len(alerts) == 2
        assert alerts[0].closed_at is not None
        assert alerts[1].closed_at is None
        assert alerts[0].episode_id != alerts[1].episode_id
        assert (alerts[0].payload_json, alerts[0].payload_hash) == frozen_original
        assert session.query(OutboxRecord).count() == 2
    clock[0] += timedelta(seconds=60)
    commit(service, clock, reverse)
    with database.session() as session:
        assert session.query(RiskAlertRecord).count() == 2


def submit(service, clock, condition, value, **updates):
    claim = service.claim(service.ensure_plan(condition))
    assert claim is not None
    assert service.complete(claim, sample(clock[0], value, **updates))
    clock[0] += timedelta(seconds=60)
    return claim


def alert_count(database):
    with database.session() as session:
        return session.query(RiskAlertRecord).count()


def trusted_always_true_component(session, **kwargs):
    """Write the real same-writer receipt consumed by the unchanged binding guard."""
    output = {"truth": "true", "value": "0.02", "reason": None}
    audit_id = "test-component-" + canonical_hash(kwargs["request_id"])
    receipt = dict(
        schema_version="market-component-consumption.v1", audit_id=audit_id,
        request_id="component-consume:" + kwargs["request_id"],
        input_hash=canonical_hash(kwargs["payload"]), output_hash=canonical_hash(output),
        output=output, state="selected", probability_qualified=False, trading_authority=False,
        selection={key: kwargs[key] for key in ("kind", "component_ref", "scope_id")},
    )
    session.add(MarketComponentAuditRecord(
        audit_id=audit_id, request_id=receipt["request_id"], pointer_id="test-always-true",
        created_at=kwargs["known_at"], payload_json=json.dumps(receipt),
        payload_hash=canonical_hash(receipt),
    ))
    return output, receipt


@pytest.mark.parametrize("interruption", ["below_threshold", "unknown", "stale"])
def test_component_true_cannot_count_native_invalid_bar_toward_reverse(runtime, interruption):
    database, service, clock = runtime
    service.component_consumer = trusted_always_true_component
    condition = rule().model_copy(update={"enter_confirm_bars": 2})
    submit(service, clock, condition, "-0.02")
    submit(service, clock, condition, "-0.02")
    updates = {} if interruption == "below_threshold" else {"data_state": interruption}
    submit(service, clock, condition, "0.005", **updates)
    submit(service, clock, condition, "0.02")
    assert alert_count(database) == 1, "only one native qualifying positive bar is confirmed"
    submit(service, clock, condition, "0.02")
    assert alert_count(database) == 2


@pytest.mark.parametrize("old_state", ["fresh", "unknown", "stale"])
def test_legacy_component_streak_is_rechecked_against_every_native_bar(runtime, old_state):
    database, service, clock = runtime
    service.component_consumer = trusted_always_true_component
    condition = rule().model_copy(update={"enter_confirm_bars": 3})
    for _ in range(3):
        submit(service, clock, condition, "-0.02")
    with database.session() as session:
        plan = session.query(RiskMonitorPlanRecord).one()
        for index, move in enumerate(("0.005", "0.02")):
            at = clock[0] + timedelta(seconds=index * 60)
            previous = sample(at, move, data_state=old_state if index == 0 else "fresh")
            session.add(RiskConditionEvaluationRecord(
                evaluation_id=f"legacy-component-true-{index}", plan_id=plan.plan_id,
                rule_version=plan.rule_version, tick_at=at, evaluated_at=at,
                truth_state="true", data_state=previous.data_state, value_json='"0.02"',
                input_json=previous.model_dump_json(), input_hash=previous.input_hash,
            ))
        plan.last_data_as_of = clock[0] + timedelta(seconds=60)
        plan.enter_streak = 5  # Persisted pre-fix component count, including the invalid bar.
        plan.next_due_at = clock[0] + timedelta(seconds=120)
    clock[0] += timedelta(seconds=120)
    submit(service, clock, condition, "0.02")
    assert alert_count(database) == 1
    submit(service, clock, condition, "0.02")
    assert alert_count(database) == 2


def test_component_initial_alert_keeps_its_selected_rule_semantics(runtime):
    database, service, clock = runtime
    service.component_consumer = trusted_always_true_component
    condition = rule().model_copy(update={"enter_confirm_bars": 2})
    submit(service, clock, condition, "0.005")
    submit(service, clock, condition, "0.005")
    assert alert_count(database) == 1


def test_native_confirmation_rechecks_history_at_each_evaluation_time(runtime):
    database, service, clock = runtime
    service.component_consumer = trusted_always_true_component
    condition = rule().model_copy(update={"enter_confirm_bars": 5})
    for _ in range(5):
        submit(service, clock, condition, "-0.02")
    for _ in range(4):
        submit(service, clock, condition, "0.02")
    assert alert_count(database) == 1
    submit(service, clock, condition, "0.02")
    assert alert_count(database) == 2


def test_component_true_with_missing_data_resets_native_reverse_confirmation(runtime):
    database, service, clock = runtime
    service.component_consumer = trusted_always_true_component
    condition = rule().model_copy(update={"enter_confirm_bars": 2})
    for move in ("-0.02", "-0.02", "0.02"):
        submit(service, clock, condition, move)
    submit(service, clock, condition, None, data_as_of=None)
    submit(service, clock, condition, "0.02")
    assert alert_count(database) == 1
    submit(service, clock, condition, "0.02")
    assert alert_count(database) == 2


@pytest.mark.parametrize("interruption", ["duplicate", "unknown", "stale"])
def test_reverse_confirmation_uses_only_consecutive_new_fresh_direction_bars(runtime, interruption):
    database, service, clock = runtime
    condition = rule().model_copy(update={"enter_confirm_bars": 2})
    submit(service, clock, condition, "-0.02")
    submit(service, clock, condition, "-0.02")
    assert alert_count(database) == 1
    first_reverse = clock[0]
    submit(service, clock, condition, "0.02")
    assert alert_count(database) == 1, "the old direction cannot confirm the reverse"
    if interruption == "duplicate":
        # A same-bar correction must neither increment nor change the streak direction.
        submit(service, clock, condition, "-0.02", data_as_of=first_reverse)
    else:
        submit(service, clock, condition, "0.02", data_state=interruption)
    assert alert_count(database) == 1
    reopened = RiskMonitorService(database, clock=lambda: clock[0])
    submit(reopened, clock, condition, "0.02")
    if interruption != "duplicate":
        assert alert_count(database) == 1
        submit(reopened, clock, condition, "0.02")
    assert alert_count(database) == 2


@pytest.mark.parametrize("bad_state", ["unknown", "stale", "error"])
def test_unavailable_reverse_never_closes_active_episode(runtime, bad_state):
    database, service, clock = runtime
    submit(service, clock, rule(), "-0.02")
    submit(service, clock, rule(), "0.02", data_state=bad_state)
    with database.session() as session:
        assert session.query(RiskAlertRecord).one().closed_at is None
        assert session.query(OutboxRecord).count() == 1


@pytest.mark.parametrize("expression", [
    {"left": {"fn": "identity"}, "op": "gte", "right": "-0.03"},
    {"left": {"fn": "abs"}, "op": "lte", "right": "0.03"},
    {"left": {"fn": "abs"}, "op": "gte", "right": "0"},
    {"left": {"fn": "abs"}, "op": "gte", "right": "-0.01"},
])
def test_non_magnitude_rules_keep_original_episode_semantics(runtime, expression):
    database, service, clock = runtime
    body = rule().model_dump(mode="json")
    body["expression"] = expression
    condition = RiskConditionV1.model_validate(body)
    submit(service, clock, condition, "-0.02")
    submit(service, clock, condition, "0.02")
    with database.session() as session:
        assert session.query(RiskAlertRecord).one().closed_at is None
        assert session.query(OutboxRecord).count() == 1


def test_same_bar_reverse_is_not_a_new_event(runtime):
    database, service, clock = runtime
    first_bar = clock[0]
    submit(service, clock, rule(), "-0.02")
    submit(service, clock, rule(), "0.02", data_as_of=first_bar)
    with database.session() as session:
        assert session.query(RiskAlertRecord).one().closed_at is None
        assert session.query(OutboxRecord).count() == 1


def test_strict_magnitude_rule_requires_exceeding_threshold_for_reverse(runtime):
    database, service, clock = runtime
    condition = rule().model_copy(update={
        "expression": rule().expression.model_copy(update={"op": "gt", "right": Decimal("0.015")})
    })
    submit(service, clock, condition, "-0.02")
    submit(service, clock, condition, "0.015")
    assert alert_count(database) == 1
    submit(service, clock, condition, "0.02")
    assert alert_count(database) == 2


def test_alternating_directions_do_not_bypass_reverse_confirmation(runtime):
    database, service, clock = runtime
    condition = rule().model_copy(update={"enter_confirm_bars": 2})
    for value in ("-0.02", "-0.02", "0.02", "-0.02", "0.02", "-0.02", "0.02"):
        submit(service, clock, condition, value)
    assert alert_count(database) == 1
    submit(service, clock, condition, "0.02")
    assert alert_count(database) == 2


def test_other_rule_version_cannot_confirm_reverse_streak(runtime):
    database, service, clock = runtime
    condition = rule().model_copy(update={"enter_confirm_bars": 2})
    submit(service, clock, condition, "-0.02")
    previous_bar = clock[0]
    submit(service, clock, condition, "-0.02")
    old_rule_sample = sample(previous_bar, "0.02")
    with database.session() as session:
        plan = session.query(RiskMonitorPlanRecord).one()
        session.add(RiskConditionEvaluationRecord(
            evaluation_id="reval_other_rule_version", plan_id=plan.plan_id,
            rule_version="obsolete-rule.v0", tick_at=previous_bar - timedelta(seconds=1),
            evaluated_at=previous_bar, truth_state="true", data_state="fresh",
            value_json='"0.02"', input_json=old_rule_sample.model_dump_json(),
            input_hash=old_rule_sample.input_hash,
        ))
    submit(service, clock, condition, "0.02")
    assert alert_count(database) == 1
    submit(service, clock, condition, "0.02")
    assert alert_count(database) == 2


def test_reverse_outbox_failure_rolls_back_old_close_and_new_episode_then_retries_once(
    runtime, monkeypatch
):
    database, service, clock = runtime
    submit(service, clock, rule(), "-0.02")
    claim = service.claim(service.ensure_plan(rule()))
    assert claim is not None
    reverse = sample(clock[0], "0.02")

    def fail(*_args, **_kwargs):
        raise RuntimeError("injected_reverse_outbox_failure")

    with monkeypatch.context() as patch:
        patch.setattr(OutboxService, "enqueue_subject", fail)
        with pytest.raises(RuntimeError, match="injected_reverse_outbox_failure"):
            service.complete(claim, reverse)
    with database.session() as session:
        assert session.query(RiskAlertRecord).one().closed_at is None
        assert session.query(RiskConditionEvaluationRecord).count() == 1
        assert session.query(OutboxRecord).count() == 1
    assert service.complete(claim, reverse)
    assert not service.complete(claim, reverse)
    with database.session() as session:
        alerts = session.query(RiskAlertRecord).order_by(RiskAlertRecord.created_at).all()
        assert len(alerts) == 2
        assert as_utc(alerts[0].closed_at) == clock[0]
        assert session.query(RiskMonitorPlanRecord).one().active_episode_id == alerts[1].episode_id
        assert session.query(RiskConditionEvaluationRecord).count() == 2
        assert session.query(OutboxRecord).count() == 2


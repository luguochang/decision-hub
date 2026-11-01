"""Finite protocol checks; engineered paths are not historical prediction evidence."""

from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from packages.kernel.decision_hub_kernel.decision.event_confirmation_policy import evaluate_event_confirmation, STRATEGY_TARGET
from packages.kernel.decision_hub_kernel.ports.event_opportunity_monitor import EventOpportunityEvaluation
from packages.kernel.decision_hub_kernel.ports.market_path import MarketCandle, spot_scope, canonical_hash
from packages.kernel.decision_hub_kernel.application.event_opportunity_monitor import (
    EventOpportunityMonitorService, _EventInputs, _missing_market,
)
from .action_fixtures import ASOF
from .test_event_opportunity_monitor import _engineered_opportunity


def candle(minute, *, price="100", volume="1", known=None):
    scope = spot_scope("BTC-USDT")
    close = ASOF + timedelta(minutes=minute)
    return MarketCandle(scope_id=scope.scope_id, venue=scope.venue, instrument=scope.instrument,
                        open_at=close - timedelta(minutes=1), close_at=close,
                        open=Decimal(price), high=Decimal(price), low=Decimal(price), close=Decimal(price),
                        volume_base=Decimal(volume) if volume is not None else None,
                        received_at=known or close, capture_ref=f"engineered:{minute}")


def call(bars, *, minute=1, position="flat", previous=(), activated=ASOF):
    policy = _engineered_opportunity().plans[0].policy
    return evaluate_event_confirmation(policy, position_context=position, scheduled_at=ASOF,
                                       as_of=ASOF + timedelta(minutes=minute, seconds=5),
                                       activation_known_at=activated, candles=bars, previous=previous)


def test_fixed_pre_event_volume_not_post_event_rolling_and_same_bar_idempotent():
    baseline = [candle(i) for i in range(-19, 1)]
    shock = [candle(i, price="101", volume="100") for i in range(1, 21)]
    value = call([*baseline, *shock, candle(21, price="101", volume="2")], minute=21)
    assert value.inputs.baseline_volume_median == "1"
    assert value.inputs.minute_volume_ratio == "2"
    assert value.inputs.event_return == "0.01"
    assert value.strategy_target == STRATEGY_TARGET
    assert value.entry.truth == "false" and value.per_condition[0].confirmation_count == 1
    again = call([*baseline, *shock, candle(21, price="101", volume="2")], minute=21, previous=value.per_condition)
    assert again.per_condition == value.per_condition
    next_value = call([*baseline, candle(22, price="101", volume="2")], minute=22, previous=again.per_condition)
    assert next_value.per_condition[0].confirmation_count == 2
    done = call([*baseline, candle(23, price="101", volume="2")], minute=23, previous=next_value.per_condition)
    assert done.entry.truth == "true"


@pytest.mark.parametrize("missing,reason", [(0, "anchor_missing"), (-4, "baseline_minutes_missing")])
def test_missing_fixed_anchor_or_minute_is_unknown(missing, reason):
    bars = [candle(i) for i in range(-19, 1) if i != missing]
    value = call([*bars, candle(1, price="101", volume="2")])
    assert value.entry.truth == "unknown" and value.invalidation.truth == "unknown"
    assert any(reason in gap for gap in value.gaps)


def test_volume_known_cutoff_and_actual_known_are_not_backfilled():
    bars = [candle(i) for i in range(-19, 1)]
    future_bar = candle(1, price="101", volume="2", known=ASOF + timedelta(minutes=2))
    assert call([*bars, future_bar]).entry.truth == "unknown"
    late_actual = call([*bars, candle(1, price="101", volume="2")], activated=ASOF + timedelta(minutes=1, seconds=3))
    assert late_actual.entry.truth == "unknown"
    bad_volume = call([*bars[:-1], candle(0, volume=None), candle(1, price="101", volume="2")])
    assert bad_volume.entry.truth == "unknown" and "event_confirmation_baseline_volume_missing" in bad_volume.gaps


def test_old_evaluation_payload_hash_still_reads_without_new_protocol_defaults():
    value = EventOpportunityEvaluation(
        evaluation_id="old-eval", monitor_id="old-monitor", event_key="old-event", forecast_id="old-forecast",
        instrument="BTC-USDT", position_context="flat", activation_id=None, opportunity_id=None,
        opportunity_hash=None, scenario_id=None, tick_at=ASOF, as_of=ASOF, evaluated_at=ASOF,
        generation=1, state_version_before=0, state_version_after=1, state_before="waiting",
        state_after="waiting", data_status="awaiting_release", reason_codes=(), evaluation_hash="0" * 64,
    )
    value = value.model_copy(update={"evaluation_hash": canonical_hash(value.model_dump(mode="json", exclude={"evaluation_hash"}))})
    row = SimpleNamespace(evaluation_id=value.evaluation_id, monitor_id=value.monitor_id, event_key=value.event_key,
                          tick_at=ASOF, payload_json=value.model_dump_json(), payload_hash=canonical_hash(value.model_dump(mode="json")))
    read = EventOpportunityMonitorService._evaluation(row)
    assert type(read) is EventOpportunityEvaluation
    assert read.model_dump(mode="json") == value.model_dump(mode="json")
    old_trigger = value.model_copy(update={"state_after": "triggered"})
    claim = SimpleNamespace(monitor_id="old-monitor", position="flat", state="triggered", previous=old_trigger,
                            activation_id=None, opportunity_id=None, opportunity_hash=None, generation=2, version=1)
    event = _EventInputs("old-event", "2026-09", ASOF, None, None, None)
    now = ASOF + timedelta(minutes=3)
    current = EventOpportunityMonitorService._evaluate(
        None, claim, event, _missing_market(spot_scope("BTC-USDT").scope_id, now, "missing"),
        "old-forecast", "BTC-USDT", now, now, now, "engineering-input",
    )
    assert current.state_after == "invalidated"
    assert current.reason_codes == ("event_monitor_confirmation_protocol_changed",)
    assert current.strategy_probability_status == "unknown"

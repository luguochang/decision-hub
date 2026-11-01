"""Recover signed confirmation from the existing immutable evaluation ledger."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Protocol

from sqlalchemy.orm import Session

from ..persistence.db import as_utc
from ..persistence.risk_records import (
    RiskAlertRecord,
    RiskConditionEvaluationRecord,
    RiskMonitorPlanRecord,
)
from ..ports.risk_monitor import RiskConditionV1, RiskMonitorInput


class NativeConditionEvaluator(Protocol):
    def __call__(
        self, rule: RiskConditionV1, sample: RiskMonitorInput, *, now: datetime
    ) -> tuple[str, Decimal | None, str | None]: ...


def supports_directional_episodes(rule: RiskConditionV1) -> bool:
    expression = rule.expression
    return (
        expression.left.fn == "abs"
        and expression.op in ("gte", "gt")
        and expression.right > 0
    )


def _direction(value: Decimal | None) -> int:
    return 0 if value is None or value == 0 else (1 if value > 0 else -1)


def _recent_inputs(
    session: Session, row: RiskMonitorPlanRecord, *, bars: int
) -> list[tuple[RiskConditionEvaluationRecord, RiskMonitorInput]]:
    """Keep complete duplicate-bar groups for a bounded confirmation window."""
    inputs: list[tuple[RiskConditionEvaluationRecord, RiskMonitorInput]] = []
    observed: set[datetime] = set()
    evaluations = (
        session.query(RiskConditionEvaluationRecord)
        .filter_by(plan_id=row.plan_id, rule_version=row.rule_version)
        .order_by(RiskConditionEvaluationRecord.tick_at.desc())
        .yield_per(20)
    )
    for evaluation in evaluations:
        previous = RiskMonitorInput.model_validate_json(evaluation.input_json)
        if previous.data_as_of is not None:
            if previous.data_as_of not in observed and len(observed) >= bars:
                break
            observed.add(previous.data_as_of)
        inputs.append((evaluation, previous))
    return inputs[::-1]


def directional_enter_streak(
    session: Session, row: RiskMonitorPlanRecord, sample: RiskMonitorInput,
    *, rule: RiskConditionV1, now: datetime, evaluate: NativeConditionEvaluator,
) -> int:
    """Recheck each consumed bar at its original cutoff, retaining component entry rules.

    The persisted count caps replay across pause/resume or unknown resets, but
    cannot establish native confirmation itself: older components may have
    counted below-threshold bars. Same-bar corrections never add or replace bars.
    """
    if evaluate(rule, sample, now=now)[0] != "true":
        return 0
    if row.enter_streak == 0 or rule.enter_confirm_bars == 1:
        return 1
    streak = 0
    consumed_at: datetime | None = None
    for evaluation, previous in _recent_inputs(session, row, bars=rule.enter_confirm_bars):
        native = evaluate(rule, previous, now=as_utc(evaluation.evaluated_at))[0]
        if evaluation.truth_state in ("unknown", "error") or native in ("unknown", "error"):
            streak = 0
        if evaluation.truth_state in ("unknown", "error") or previous.data_as_of is None:
            continue
        if consumed_at is not None and previous.data_as_of <= consumed_at:
            continue
        consumed_at = previous.data_as_of
        if (
            evaluation.truth_state == native == "true"
            and _direction(previous.close_return) == _direction(sample.close_return)
        ):
            streak += 1
        else:
            streak = 0
    return min(streak, row.enter_streak, rule.enter_confirm_bars - 1) + 1


def reverses_active_episode(
    session: Session, row: RiskMonitorPlanRecord, sample: RiskMonitorInput
) -> bool:
    alert = session.query(RiskAlertRecord).filter_by(
        plan_id=row.plan_id, episode_id=row.active_episode_id, closed_at=None
    ).one_or_none()
    if alert is None:
        return False
    evaluation = session.get(RiskConditionEvaluationRecord, alert.evaluation_id)
    if (
        evaluation is None
        or evaluation.plan_id != row.plan_id
        or evaluation.rule_version != row.rule_version
    ):
        return False
    origin = RiskMonitorInput.model_validate_json(evaluation.input_json)
    return _direction(origin.close_return) * _direction(sample.close_return) == -1


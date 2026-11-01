"""Deterministic risk conditions, durable claims and atomic local alert intents."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal

from sqlalchemy import or_, text
from sqlalchemy.orm import Session

from ..persistence.db import Database, as_utc, utcnow
from ..persistence.risk_records import (
    RiskAlertDeliveryReceiptRecord,
    RiskAlertRecord,
    RiskConditionEvaluationRecord,
    RiskMonitorCommandRecord,
    RiskMonitorPlanRecord,
)
from ..ports.market_component_consumer import MarketComponentConsumer
from ..ports.risk_monitor import RiskConditionV1, RiskMonitorInput, RiskSnapshotPort
from .component_consumption_binding import component_consumption_reference_in_session
from .risk_direction_reversal import (
    directional_enter_streak,
    reverses_active_episode,
    supports_directional_episodes,
)


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def content_hash(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _writer(session: Session) -> None:
    # Lock before the first read. Never hold this short transaction over I/O.
    if session.bind is not None and session.bind.dialect.name == "sqlite":
        session.execute(text("BEGIN IMMEDIATE"))


@dataclass(frozen=True)
class RiskMonitorClaim:
    plan_id: str
    rule: RiskConditionV1
    owner: str
    generation: int
    tick_at: datetime
    as_of: datetime


def evaluate_condition(
    rule: RiskConditionV1, sample: RiskMonitorInput, *, now: datetime
) -> tuple[str, Decimal | None, str | None]:
    if (
        sample.scope_id != rule.scope_id
        or sample.window_seconds != rule.expression.left.window_seconds
    ):
        return "error", None, "risk_input_basis_mismatch"
    if sample.as_of > now or (sample.data_as_of is not None and sample.data_as_of > sample.as_of):
        return "error", None, "risk_input_future_time"
    if sample.data_state == "error":
        return "error", None, sample.reason or "risk_input_error"
    if sample.data_state != "fresh" or sample.close_return is None or sample.data_as_of is None:
        return "unknown", None, sample.reason or "risk_input_unavailable"
    if (now - sample.data_as_of).total_seconds() > rule.stale_after_seconds:
        return "unknown", None, "risk_input_stale"
    value = abs(sample.close_return) if rule.expression.left.fn == "abs" else sample.close_return
    threshold = rule.expression.right
    truth = {
        "gte": value >= threshold,
        "gt": value > threshold,
        "lte": value <= threshold,
        "lt": value < threshold,
    }[rule.expression.op]
    return "true" if truth else "false", value, None


class RiskMonitorService:
    def __init__(
        self,
        database: Database,
        snapshots: RiskSnapshotPort | None = None,
        *,
        clock: Callable[[], datetime] = utcnow,
        lease_seconds: int = 90,
        component_consumer: MarketComponentConsumer | None = None,
        channel: str = "local",
    ) -> None:
        if lease_seconds < 1:
            raise ValueError("risk_lease_must_be_positive")
        self.database = database
        self.snapshots = snapshots
        self.clock = clock
        self.lease_seconds = lease_seconds
        self.component_consumer = component_consumer
        self.channel = channel

    def ensure_plan(self, rule: RiskConditionV1, *, expires_at: datetime | None = None) -> str:
        now = self.clock()
        payload = rule.model_dump(mode="json")
        rule_hash = content_hash(payload)
        plan_id = "rplan_" + content_hash([rule.scope_id, rule.condition_id])[:48]
        with self.database.session() as session:
            _writer(session)
            row = session.get(RiskMonitorPlanRecord, plan_id)
            if row is not None:
                if row.rule_hash != rule_hash:
                    raise ValueError("risk_plan_rule_version_conflict")
                return plan_id
            session.add(
                RiskMonitorPlanRecord(
                    plan_id=plan_id,
                    scope_id=rule.scope_id,
                    rule_version=rule.condition_id,
                    rule_json=canonical_json(payload),
                    rule_hash=rule_hash,
                    state="active",
                    next_due_at=now,
                    expires_at=expires_at,
                    lease_generation=0,
                    enter_streak=0,
                    exit_streak=0,
                    consecutive_errors=0,
                    created_at=now,
                    updated_at=now,
                )
            )
        return plan_id

    def expire_due(self) -> int:
        now = self.clock()
        with self.database.session() as session:
            return (
                session.query(RiskMonitorPlanRecord)
                .filter(
                    RiskMonitorPlanRecord.state != "expired",
                    RiskMonitorPlanRecord.expires_at <= now,
                )
                .update(
                    {
                        "state": "expired",
                        "updated_at": now,
                        "lease_owner": None,
                        "lease_expires_at": None,
                        "lease_generation": RiskMonitorPlanRecord.lease_generation + 1,
                    },
                    synchronize_session=False,
                )
            )

    def claim(self, plan_id: str) -> RiskMonitorClaim | None:
        now = self.clock()
        with self.database.session() as session:
            _writer(session)
            row = session.get(RiskMonitorPlanRecord, plan_id)
            if row is None or row.state not in ("active", "degraded"):
                return None
            if as_utc(row.next_due_at) > now:
                return None
            if row.expires_at is not None and as_utc(row.expires_at) <= now:
                return None
            if row.lease_expires_at is not None and as_utc(row.lease_expires_at) > now:
                return None
            owner = uuid.uuid4().hex
            row.lease_owner = owner
            row.lease_generation += 1
            row.lease_expires_at = now + timedelta(seconds=self.lease_seconds)
            row.updated_at = now
            return RiskMonitorClaim(
                plan_id=row.plan_id,
                rule=RiskConditionV1.model_validate_json(row.rule_json),
                owner=owner,
                generation=row.lease_generation,
                tick_at=as_utc(row.next_due_at),
                as_of=now,
            )

    def complete(self, claim: RiskMonitorClaim, sample: RiskMonitorInput) -> bool:
        from ..ports.sources import make_notification_envelope
        from .outbox import OutboxService

        now = self.clock()
        truth, value, reason = evaluate_condition(claim.rule, sample, now=now)
        native_truth = truth
        if sample.as_of != claim.as_of:
            truth, value, reason = "error", None, "risk_input_cutoff_mismatch"
        evaluation_id = "reval_" + content_hash([
            claim.plan_id, claim.rule.condition_id, claim.tick_at.isoformat(),
        ])[:48]
        with self.database.session() as session:
            _writer(session)
            row = session.get(RiskMonitorPlanRecord, claim.plan_id)
            if (
                row is None
                or row.lease_owner != claim.owner
                or row.lease_generation != claim.generation
                or row.lease_expires_at is None
                or as_utc(row.lease_expires_at) <= now
                or (row.expires_at is not None and as_utc(row.expires_at) <= now)
                or row.state not in ("active", "degraded")
                or row.rule_hash != content_hash(claim.rule.model_dump(mode="json"))
            ):
                return False
            if self.component_consumer is not None:
                from .market_component_functions import (
                    RuleComponentInput,
                    RuleComponentOutput,
                    evaluate_market_rule_component,
                )

                payload = RuleComponentInput(
                    rule=claim.rule, sample=sample, now=now,
                ).model_dump(mode="json")
                request_id = f"monitor:{evaluation_id}:{claim.generation}"
                selected, receipt = self.component_consumer(
                    session, kind="monitor_rule", component_ref="spot.move30.v1",
                    scope_id=sample.scope_id,
                    request_id=request_id, known_at=now, cutoff_at=claim.as_of,
                    payload=payload, fallback=evaluate_market_rule_component,
                )
                result = RuleComponentOutput.model_validate(selected)
                component_consumption_reference_in_session(
                    session, receipt, kind="monitor_rule", component_ref="spot.move30.v1",
                    scope_id=sample.scope_id, request_id=request_id, known_at=now,
                    payload=payload, value=selected,
                )
                truth, value, reason = result.truth, result.value, result.reason
                if sample.as_of != claim.as_of:
                    truth, value, reason = "error", None, "risk_input_cutoff_mismatch"
            if (
                sample.data_as_of is not None
                and row.last_data_as_of is not None
                and sample.data_as_of < as_utc(row.last_data_as_of)
            ):
                truth, value, reason = "unknown", None, "risk_input_time_regressed"
            new_bar = sample.data_as_of is not None and (
                row.last_data_as_of is None or sample.data_as_of > as_utc(row.last_data_as_of)
            )
            if truth in ("unknown", "error"):
                row.consecutive_errors += 1
                if row.consecutive_errors >= 2 or sample.data_state == "stale":
                    row.state = "degraded"
                # An interrupted streak never proves consecutive fresh bars.
                row.enter_streak = 0
                row.exit_streak = 0
            else:
                row.consecutive_errors = 0
                row.state = "active"
                if (
                    row.active_episode_id is not None
                    and supports_directional_episodes(claim.rule)
                    and native_truth in ("unknown", "error")
                ):
                    row.enter_streak = 0
                if new_bar:
                    enter_streak = row.enter_streak + 1 if truth == "true" else 0
                    if (
                        truth == "true"
                        and row.active_episode_id is not None
                        and supports_directional_episodes(claim.rule)
                    ):
                        enter_streak = directional_enter_streak(
                            session, row, sample, rule=claim.rule, now=now,
                            evaluate=evaluate_condition,
                        )
                    row.last_data_as_of = sample.data_as_of
                    row.enter_streak = enter_streak
                    row.exit_streak = row.exit_streak + 1 if truth == "false" else 0
                if (
                    new_bar
                    and truth == native_truth == "true"
                    and row.active_episode_id is not None
                    and row.enter_streak >= claim.rule.enter_confirm_bars
                    and supports_directional_episodes(claim.rule)
                    and reverses_active_episode(session, row, sample)
                ):
                    session.query(RiskAlertRecord).filter_by(
                        episode_id=row.active_episode_id
                    ).update({"closed_at": now}, synchronize_session=False)
                    row.active_episode_id = None
                if (
                    truth == "true"
                    and row.active_episode_id is None
                    and (row.enter_streak >= claim.rule.enter_confirm_bars)
                ):
                    episode_id = "rep_" + content_hash([claim.plan_id, evaluation_id])[:48]
                    alert_id = "ralert_" + content_hash([episode_id])[:48]
                    alert_payload = {
                        "schema_version": "risk-alert.v1",
                        "alert_id": alert_id,
                        "scope_id": row.scope_id,
                        "condition": claim.rule.model_dump(mode="json"),
                        "alarm_kind": "fact_change",
                        "prediction_hit": False,
                        "input": sample.model_dump(mode="json"),
                        "value": str(value),
                        "review_state": "awaiting_research_admission",
                        "created_at": now.astimezone(UTC).isoformat(),
                    }
                    session.add(
                        RiskAlertRecord(
                            alert_id=alert_id,
                            episode_id=episode_id,
                            plan_id=row.plan_id,
                            evaluation_id=evaluation_id,
                            scope_id=row.scope_id,
                            rule_version=row.rule_version,
                            alarm_kind="fact_change",
                            severity=claim.rule.severity,
                            observed_at=sample.data_as_of,
                            created_at=now,
                            published_at=now,
                            payload_json=canonical_json(alert_payload),
                            payload_hash=content_hash(alert_payload),
                        )
                    )
                    message = make_notification_envelope(
                        subject_kind="risk_alert",
                        subject_id=alert_id,
                        channel=self.channel,
                        dedupe_key="risk-alert:" + episode_id,
                        subject="市场风险变化提醒",
                        body=f"条件 {claim.rule.condition_id} 已触发；变化 {sample.close_return}。"
                        "这是已观察变化，研究复查状态请查看原告警。",
                        created_at=now,
                    )
                    OutboxService(self.database).enqueue_subject(message, session=session)
                    row.active_episode_id = episode_id
                elif (
                    truth == "false"
                    and row.active_episode_id is not None
                    and (row.exit_streak >= claim.rule.exit_confirm_bars)
                ):
                    session.query(RiskAlertRecord).filter_by(
                        episode_id=row.active_episode_id
                    ).update(
                        {"closed_at": now},
                        synchronize_session=False,
                    )
                    row.active_episode_id = None
            session.add(
                RiskConditionEvaluationRecord(
                    evaluation_id=evaluation_id,
                    plan_id=row.plan_id,
                    rule_version=row.rule_version,
                    tick_at=claim.tick_at,
                    evaluated_at=now,
                    truth_state=truth,
                    data_state=sample.data_state,
                    value_json=canonical_json(str(value) if value is not None else None),
                    reason=reason,
                    input_json=sample.model_dump_json(),
                    input_hash=sample.input_hash,
                    episode_id=row.active_episode_id,
                )
            )
            row.last_truth = truth
            row.last_error_code = reason
            row.last_evaluated_at = now
            row.next_due_at = now + timedelta(seconds=claim.rule.evaluation_seconds)
            row.updated_at = now
            row.lease_owner = None
            row.lease_expires_at = None
        return True

    async def process_due(self, *, limit: int = 100) -> int:
        if self.snapshots is None:
            return 0
        self.expire_due()
        now = self.clock()
        with self.database.session() as session:
            ids = [
                item[0]
                for item in session.query(RiskMonitorPlanRecord.plan_id)
                .filter(
                    RiskMonitorPlanRecord.state.in_(("active", "degraded")),
                    RiskMonitorPlanRecord.next_due_at <= now,
                    or_(
                        RiskMonitorPlanRecord.lease_expires_at.is_(None),
                        RiskMonitorPlanRecord.lease_expires_at <= now,
                    ),
                )
                .order_by(RiskMonitorPlanRecord.next_due_at)
                .limit(limit)
                .all()
            ]
        count = 0
        for plan_id in ids:
            claim = self.claim(plan_id)
            if claim is None:
                continue
            try:
                sample = await self.snapshots.snapshot(claim.rule.scope_id, as_of=claim.as_of)
            except Exception:
                # Preserve a durable unknown/error instead of leaving a green old result.
                sample = RiskMonitorInput(
                    scope_id=claim.rule.scope_id,
                    as_of=claim.as_of,
                    data_as_of=None,
                    window_seconds=claim.rule.expression.left.window_seconds,
                    close_return=None,
                    data_state="error",
                    reason="risk_snapshot_unavailable",
                    input_ref="unavailable",
                    input_hash=content_hash([plan_id, claim.as_of.isoformat(), "unavailable"]),
                    manifest_hash=content_hash(["unavailable"]),
                )
            count += int(self.complete(claim, sample))
        return count

    def command(
        self,
        plan_id: str,
        action: Literal["pause", "resume"],
        *,
        request_id: str,
    ) -> dict[str, object]:
        if action not in ("pause", "resume") or not request_id or len(request_id) > 128:
            raise ValueError("risk_command_invalid")
        now = self.clock()
        digest = content_hash([plan_id, action])
        with self.database.session() as session:
            _writer(session)
            previous = session.get(RiskMonitorCommandRecord, request_id)
            if previous is not None:
                if previous.payload_hash != digest:
                    raise ValueError("risk_command_request_conflict")
                return json.loads(previous.result_json)
            row = session.get(RiskMonitorPlanRecord, plan_id)
            if row is None:
                raise KeyError(plan_id)
            if row.expires_at is not None and as_utc(row.expires_at) <= now:
                raise ValueError("risk_plan_expired")
            row.state = "paused" if action == "pause" else "active"
            row.lease_generation += 1
            row.lease_owner = None
            row.lease_expires_at = None
            row.enter_streak = row.exit_streak = 0
            row.updated_at = now
            if action == "resume":
                row.next_due_at = now
            receipt: dict[str, object] = {
                "request_id": request_id,
                "plan_id": plan_id,
                "action": action,
                "state": row.state,
                "accepted_at": now.isoformat(),
            }
            session.add(
                RiskMonitorCommandRecord(
                    request_id=request_id,
                    plan_id=plan_id,
                    payload_hash=digest,
                    result_json=canonical_json(receipt),
                    accepted_at=now,
                )
            )
            return receipt

    def record_receipt(
        self,
        alert_id: str,
        kind: Literal["display", "ack"],
        *,
        request_id: str,
        client_time: datetime | None = None,
    ) -> dict[str, object]:
        if kind not in ("display", "ack") or not request_id or len(request_id) > 128:
            raise ValueError("risk_receipt_invalid")
        now = self.clock()
        receipt_id = "rrcp_" + content_hash([alert_id, kind, request_id])[:48]
        with self.database.session() as session:
            _writer(session)
            if session.get(RiskAlertRecord, alert_id) is None:
                raise KeyError(alert_id)
            row = session.get(RiskAlertDeliveryReceiptRecord, receipt_id)
            if row is not None:
                return json.loads(row.payload_json)
            payload: dict[str, object] = {
                "receipt_id": receipt_id,
                "alert_id": alert_id,
                "kind": kind,
                "request_id": request_id,
                "received_at": now.isoformat(),
                "client_time": client_time.isoformat() if client_time is not None else None,
            }
            session.add(
                RiskAlertDeliveryReceiptRecord(
                    receipt_id=receipt_id,
                    alert_id=alert_id,
                    receipt_kind=kind,
                    channel="pc",
                    request_id=request_id,
                    received_at=now,
                    payload_json=canonical_json(payload),
                )
            )
            return payload

"""Permanent, once-only owner command for an original budget-ended alert.

This creates a separate automatic intent. The original trigger and failed request
are never rewritten. Run creation remains in the existing leased admission path.
"""

import hashlib
import json
from datetime import datetime, timedelta

from sqlalchemy import select

from ..persistence.db import ProductCommandRecord, RunRecord, StudyRecord, as_utc
from ..persistence.risk_records import (
    RiskAlertRecord,
    RiskConditionEvaluationRecord,
    RiskMonitorPlanRecord,
)
from ..persistence.risk_research_records import (
    RiskResearchRequestRecord,
    RiskResearchTriggerRecord,
)
from ..ports.market_path import canonical_hash, spot_scope
from ..ports.risk_alert_recheck import (
    RiskAlertRecheckCommand,
    RiskAlertRecheckReceipt,
    RiskAlertRecheckState,
    RiskAlertRecheckTrace,
)
from ..ports.risk_monitor import RiskConditionV1, RiskMonitorInput
from .research_tracking import research_admission_guard_in_session
from .risk_monitor import _writer, canonical_json, content_hash
from .study import stable_id

KIND = "risk_alert_recheck"
TRACE_KIND = "risk_alert_recheck_trace"


class RecheckAdmissionBlocked(ValueError):
    def __init__(self, reason, capacity):
        super().__init__(reason)
        self.capacity = dict(capacity)


def _native(row):
    return {
        column.name: (as_utc(value).isoformat() if isinstance(value, datetime) else value)
        for column in row.__table__.columns
        for value in [getattr(row, column.name)]
    }


def _original(session, alert_id, *, now):
    """Read the complete original tuple, rejecting unsupported/corrupt ancestry."""
    alert = session.get(RiskAlertRecord, alert_id, populate_existing=True)
    if alert is None:
        raise KeyError("risk_alert_not_found")
    instrument = next((x for x in ("BTC-USDT", "ETH-USDT")
                       if spot_scope(x).scope_id == alert.scope_id), None)
    trigger = session.get(RiskResearchTriggerRecord, f"risk_alert:{alert_id}", populate_existing=True)
    original = session.get(RiskResearchRequestRecord, trigger.request_id, populate_existing=True) if trigger else None
    plan = session.get(RiskMonitorPlanRecord, alert.plan_id, populate_existing=True)
    evaluation = session.get(RiskConditionEvaluationRecord, alert.evaluation_id, populate_existing=True)
    if instrument is None or original is None or plan is None or evaluation is None:
        raise ValueError("risk_alert_recheck_original_invalid")
    try:
        payload = json.loads(alert.payload_json)
        rule = RiskConditionV1.model_validate(payload["condition"])
        sample = RiskMonitorInput.model_validate(payload["input"])
        expected_source = {
            "payload_hash": alert.payload_hash, "severity": alert.severity,
            "observed_at": as_utc(alert.observed_at).isoformat(),
        }
        trigger_payload = json.loads(trigger.payload_json)
        triggers = list(session.scalars(select(RiskResearchTriggerRecord).where(
            RiskResearchTriggerRecord.request_id == original.request_id,
        ).order_by(RiskResearchTriggerRecord.trigger_key).limit(101).execution_options(populate_existing=True)))
        if (
            content_hash(payload) != alert.payload_hash
            or payload["schema_version"] != "risk-alert.v1"
            or payload["alert_id"] != alert_id or payload["scope_id"] != alert.scope_id
            or payload["prediction_hit"] is not False
            or payload["alarm_kind"] != alert.alarm_kind or alert.alarm_kind != "fact_change"
            or rule.scope_id != alert.scope_id or sample.scope_id != alert.scope_id
            or sample.data_state != "fresh" or sample.data_as_of != as_utc(alert.observed_at)
            or sample.input_ref.startswith("fixture://")
            or sample.as_of > as_utc(alert.created_at) or as_utc(alert.created_at) > now
            or as_utc(alert.published_at) != as_utc(alert.created_at)
            or alert.episode_id != "rep_" + content_hash([alert.plan_id, alert.evaluation_id])[:48]
            or alert_id != "ralert_" + content_hash([alert.episode_id])[:48]
            or plan.scope_id != alert.scope_id or plan.rule_version != alert.rule_version
            or plan.rule_hash != content_hash(rule.model_dump(mode="json"))
            or json.loads(plan.rule_json) != rule.model_dump(mode="json")
            or evaluation.plan_id != alert.plan_id or evaluation.rule_version != alert.rule_version
            or evaluation.episode_id != alert.episode_id or evaluation.truth_state != "true"
            or evaluation.input_hash != sample.input_hash
            or json.loads(evaluation.input_json) != sample.model_dump(mode="json")
            or trigger.kind != "risk_alert" or trigger.scope_id != alert.scope_id
            or trigger.source_ref != alert_id or trigger_payload != {
                "instrument": instrument, "scope_id": alert.scope_id, "kind": "risk_alert",
                "source_ref": alert_id, "source": expected_source,
            }
            or content_hash(trigger_payload) != trigger.payload_hash
            or original.scope_id != alert.scope_id or original.instrument != instrument
            or not as_utc(original.created_at) <= as_utc(trigger.recorded_at) <= now
            or len(triggers) > 100
            or any(t.kind not in {"daily", "risk_alert"} for t in triggers)
        ):
            raise ValueError("risk_alert_recheck_original_invalid")
        from .risk_research_admission import _recognized_request_triggers

        _recognized_request_triggers(session, original, now=now)
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("risk_alert_recheck_original_invalid") from error
    snapshot = {
        "alert": _native(alert), "evaluation": _native(evaluation),
        "request": _native(original), "triggers": [_native(t) for t in triggers],
        "plan_identity": {name: getattr(plan, name) for name in (
            "plan_id", "scope_id", "rule_version", "rule_json", "rule_hash",
        )},
    }
    return alert, original, plan, instrument, canonical_hash(snapshot)


def _accepted(session, alert_id):
    return session.scalar(select(ProductCommandRecord).where(
        ProductCommandRecord.target_id == alert_id, ProductCommandRecord.kind == KIND,
    ).order_by(ProductCommandRecord.created_at, ProductCommandRecord.request_id).limit(1)
      .execution_options(populate_existing=True))


def _receipt(record, *, alert_id, owner=None, command=None):
    receipt = RiskAlertRecheckReceipt.model_validate_json(record.result_json)
    saved_command = RiskAlertRecheckCommand(
        request_id=receipt.request_id, expected_head_hash=receipt.original_head_hash,
    )
    digest = canonical_hash([receipt.owner_id, receipt.alert_id, KIND,
                             saved_command.model_dump(mode="json")])
    if (
        record.request_id != receipt.request_id or record.target_id != alert_id
        or receipt.alert_id != alert_id or record.owner_id != receipt.owner_id
        or record.kind != KIND or record.payload_hash != digest
        or as_utc(record.created_at) != receipt.accepted_at
        or receipt.trigger_key != f"risk_alert_recheck:{receipt.request_id}"
        or receipt.research_request_id != "riskresearch_" + canonical_hash([receipt.scope_id, receipt.trigger_key])[:40]
        or receipt.original_triggered_at > receipt.accepted_at
        or receipt.original_resolved_at is not None and not receipt.original_triggered_at <= receipt.original_resolved_at <= receipt.accepted_at
        or (owner is not None and receipt.owner_id != owner)
        or (command is not None and saved_command != command)
    ):
        raise ValueError("risk_alert_recheck_command_conflict")
    return receipt


def recognized_recheck(session, row, triggers, *, now):
    """No caller-supplied manual identity can change this intent's origin."""
    from .risk_admission_wait import admission_accounting_valid
    selected = [t for t in triggers if t.kind == KIND]
    if not selected:
        return None
    if len(triggers) != 1:
        raise ValueError("risk_alert_recheck_trigger_mixed")
    trigger = selected[0]
    payload = json.loads(trigger.payload_json)
    source = payload.get("source", {})
    command_id = source.get("command_request_id")
    record = session.get(ProductCommandRecord, command_id, populate_existing=True) if isinstance(command_id, str) else None
    if record is None:
        raise ValueError("risk_alert_recheck_command_missing")
    receipt = _receipt(record, alert_id=trigger.source_ref)
    alert, old, _plan, instrument, head = _original(session, receipt.alert_id, now=now)
    if (
        receipt.scope_id != row.scope_id or instrument != row.instrument
        or receipt.original_request_id != old.request_id or receipt.original_head_hash != head
        or old.status != "failed" or old.attempts != 3
        or not admission_accounting_valid(session, old, now=now)
        or old.run_id is not None or old.event_id is not None
        or old.error_code != "research_admission_budget_exhausted"
        or old.lease_owner is not None or old.lease_expires_at is not None
        or receipt.original_triggered_at != as_utc(alert.created_at)
        or receipt.original_resolved_at != as_utc(alert.closed_at)
        or receipt.research_request_id != row.request_id or receipt.trigger_key != trigger.trigger_key
        or trigger.trigger_key != f"risk_alert_recheck:{command_id}"
        or row.request_id != "riskresearch_" + canonical_hash([row.scope_id, trigger.trigger_key])[:40]
        or receipt.accepted_at != as_utc(row.created_at) or receipt.accepted_at != as_utc(trigger.recorded_at)
        or payload != {
            "instrument": instrument, "scope_id": row.scope_id, "kind": KIND,
            "source_ref": receipt.alert_id, "source": {
                "command_request_id": command_id, "original_request_id": old.request_id,
                "original_head_hash": head, "original_triggered_at": receipt.original_triggered_at.isoformat(),
                "original_resolved_at": receipt.original_resolved_at.isoformat() if receipt.original_resolved_at else None,
                "accepted_at": receipt.accepted_at.isoformat(), "mode": receipt.mode,
            },
        }
        or content_hash(payload) != trigger.payload_hash
    ):
        raise ValueError("risk_alert_recheck_trigger_corrupt")
    return receipt


def record_recheck_trace(session, row, triggers, *, now, event, reason=None, capacity=None):
    # Recording a failure does not grant admission and must also work when the
    # current original tuple has changed. Verify the immutable command binding.
    selected = [t for t in triggers if t.kind == KIND]
    if not selected:
        return
    trigger = selected[0]
    command_id = json.loads(trigger.payload_json).get("source", {}).get("command_request_id")
    record = session.get(ProductCommandRecord, command_id) if isinstance(command_id, str) else None
    if record is None:
        return  # No fabricated trace for an unrecognizable journal identity.
    receipt = _receipt(record, alert_id=trigger.source_ref)
    if receipt.research_request_id != row.request_id or receipt.trigger_key != trigger.trigger_key:
        raise ValueError("risk_alert_recheck_trace_identity_invalid")
    trace = RiskAlertRecheckTrace(
        alert_id=receipt.alert_id, research_request_id=row.request_id, event=event,
        recorded_at=now, attempt=row.attempts, lease_generation=row.lease_generation,
        request_status=row.status, reason_code=reason, capacity=capacity or {},
        event_id=row.event_id, run_id=row.run_id,
    )
    digest = content_hash(trace.model_dump(mode="json"))
    key = "risk-recheck-trace:" + canonical_hash([row.request_id, row.lease_generation, event])[:48]
    old = session.get(ProductCommandRecord, key)
    if old is not None:
        if old.kind != TRACE_KIND or old.target_id != receipt.alert_id or old.payload_hash != digest:
            raise ValueError("risk_alert_recheck_trace_conflict")
        return
    session.add(ProductCommandRecord(
        request_id=key, owner_id=receipt.owner_id, target_id=receipt.alert_id, kind=TRACE_KIND,
        payload_hash=digest, result_json=trace.model_dump_json(), created_at=now,
    ))


class RiskAlertRecheckService:
    def __init__(self, admission):
        self.admission, self.database, self.clock = admission, admission.database, admission.clock

    def _conditions(self, session, alert_id, *, now):
        from .risk_admission_wait import admission_accounting_valid
        alert, old, plan, instrument, head = _original(session, alert_id, now=now)
        record = _accepted(session, alert_id)
        receipt = _receipt(record, alert_id=alert_id) if record else None
        scope = json.dumps(["crypto", instrument, "30m,24h,72h", "spot-market-risk.v1"], ensure_ascii=False)
        study_id = stable_id("study", scope)
        study = session.get(StudyRecord, study_id, populate_existing=True)
        guard = research_admission_guard_in_session(session, study_id=study_id, now=now, admission_origin="automatic")
        reason = None
        if not self.admission.enabled:
            reason = "spot_research_disabled"
        elif receipt is not None:
            reason = "risk_alert_recheck_already_accepted"
        elif (old.status != "failed" or old.attempts != 3
              or not admission_accounting_valid(session, old, now=now)
              or old.error_code != "research_admission_budget_exhausted" or old.run_id is not None
              or old.event_id is not None or old.lease_owner is not None or old.lease_expires_at is not None):
            reason = "risk_alert_recheck_original_not_budget_ended"
        elif study and (study.status != "active" or study.scope_key != hashlib.sha256(scope.encode()).hexdigest()
                       or study.market != "crypto" or study.instrument != instrument
                       or study.horizon_scope != "30m,24h,72h" or study.question_key != "spot-market-risk.v1"
                       or study.identity_basis != "exact_spot_monitor"):
            reason = "risk_alert_recheck_study_identity_invalid"
        elif (plan.state not in {"active", "degraded"} or plan.last_evaluated_at is None
              or not now - timedelta(seconds=180) <= as_utc(plan.last_evaluated_at) <= now
              or plan.last_data_as_of is None or not now - timedelta(seconds=180) <= as_utc(plan.last_data_as_of) <= now
              or plan.expires_at is not None and as_utc(plan.expires_at) <= now):
            reason = "risk_alert_recheck_monitor_not_fresh"
        elif session.scalar(select(RiskResearchRequestRecord.request_id).where(RiskResearchRequestRecord.protocol_version == "risk-research-request.spot.v1").where(
            RiskResearchRequestRecord.scope_id == alert.scope_id,
            RiskResearchRequestRecord.status.in_(["pending_admission", "claiming"]),
        ).limit(1)) is not None:
            reason = "risk_alert_recheck_request_active"
        elif guard.reason_code is not None:
            reason = guard.reason_code
        row = session.get(RiskResearchRequestRecord, receipt.research_request_id, populate_existing=True) if receipt else None
        timeline = []
        if receipt:
            for trace_row in session.scalars(select(ProductCommandRecord).where(
                ProductCommandRecord.target_id == alert_id, ProductCommandRecord.kind == TRACE_KIND,
            ).order_by(ProductCommandRecord.created_at, ProductCommandRecord.request_id).limit(16)):
                trace = RiskAlertRecheckTrace.model_validate_json(trace_row.result_json)
                if trace.research_request_id != receipt.research_request_id or content_hash(trace.model_dump(mode="json")) != trace_row.payload_hash:
                    raise ValueError("risk_alert_recheck_trace_corrupt")
                timeline.append(trace.model_dump(mode="json"))
        event_order = {"claimed": 0, "budget_blocked": 1, "admission_failed": 1, "claim_expired": 1, "queued": 1}
        timeline.sort(key=lambda t: (t["recorded_at"], t["attempt"], event_order[t["event"]]))
        request_view = self.admission._view_in_session(session, row) if row else None
        if row is not None:
            run = session.get(RunRecord, row.run_id, populate_existing=True) if row.run_id else None
            request_view.update({
                "updated_at": as_utc(row.updated_at).isoformat(),
                "lease_generation": row.lease_generation,
                "lease_expires_at": as_utc(row.lease_expires_at).isoformat() if row.lease_expires_at else None,
                "run_created_at": as_utc(run.created_at).isoformat() if run else None,
                "run_finished_at": as_utc(run.finished_at).isoformat() if run and run.finished_at else None,
            })
        return RiskAlertRecheckState(
            alert_id=alert_id, scope_id=alert.scope_id, instrument=instrument, observed_at=now,
            original_triggered_at=as_utc(alert.created_at), original_resolved_at=as_utc(alert.closed_at),
            original_request_id=old.request_id, head_hash=head, eligible=reason is None,
            reason_code=reason, capacity=guard.details, receipt=receipt,
            research_request=request_view, timeline=timeline,
        )

    def conditions(self, alert_id):
        with self.database.session() as session:
            return self._conditions(session, alert_id, now=as_utc(self.clock()))

    def get_receipt(self, alert_id, owner, command):
        with self.database.session() as session:
            record = session.get(ProductCommandRecord, command.request_id, populate_existing=True)
            return _receipt(record, alert_id=alert_id, owner=owner, command=command) if record else None

    def accept(self, alert_id, owner, command):
        digest = canonical_hash([owner, alert_id, KIND, command.model_dump(mode="json")])
        with self.database.session() as session:
            if session.bind is None or session.bind.dialect.name != "sqlite":
                raise ValueError("risk_alert_recheck_sqlite_writer_required")
            _writer(session)
            record = session.get(ProductCommandRecord, command.request_id, populate_existing=True)
            if record is not None:
                return _receipt(record, alert_id=alert_id, owner=owner, command=command)
            now = as_utc(self.clock())
            state = self._conditions(session, alert_id, now=now)
            if command.expected_head_hash != state.head_hash:
                raise ValueError("risk_alert_recheck_head_changed")
            if not state.eligible:
                raise ValueError(state.reason_code)
            key = f"risk_alert_recheck:{command.request_id}"
            request_id = "riskresearch_" + canonical_hash([state.scope_id, key])[:40]
            receipt = RiskAlertRecheckReceipt(
                request_id=command.request_id, owner_id=owner, alert_id=alert_id, scope_id=state.scope_id,
                original_request_id=state.original_request_id, original_head_hash=state.head_hash,
                original_triggered_at=state.original_triggered_at, original_resolved_at=state.original_resolved_at,
                accepted_at=now, research_request_id=request_id, trigger_key=key,
            )
            payload = {
                "instrument": state.instrument, "scope_id": state.scope_id, "kind": KIND, "source_ref": alert_id,
                "source": {
                    "command_request_id": command.request_id, "original_request_id": state.original_request_id,
                    "original_head_hash": state.head_hash, "original_triggered_at": state.original_triggered_at.isoformat(),
                    "original_resolved_at": state.original_resolved_at.isoformat() if state.original_resolved_at else None,
                    "accepted_at": now.isoformat(), "mode": receipt.mode,
                },
            }
            session.add(RiskResearchRequestRecord(
                request_id=request_id, scope_id=state.scope_id, instrument=state.instrument,
                status="pending_admission", created_at=now, updated_at=now, available_at=now,
                attempts=0, lease_generation=0,
            ))
            session.add(RiskResearchTriggerRecord(
                trigger_key=key, request_id=request_id, kind=KIND, scope_id=state.scope_id,
                source_ref=alert_id, payload_json=canonical_json(payload), payload_hash=content_hash(payload), recorded_at=now,
            ))
            session.add(ProductCommandRecord(
                request_id=command.request_id, owner_id=owner, target_id=alert_id, kind=KIND,
                payload_hash=digest, result_json=receipt.model_dump_json(), created_at=now,
            ))
            return receipt

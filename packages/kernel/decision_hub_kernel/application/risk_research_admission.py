"""Persist automatic spot research, then admit into the existing durable Run queue.

Preparing the effective Host/source admission happens outside the writer. A
continuously owned transaction creates the exact Study/Run and records all merged
triggers. Model execution and later report rechecks stay with existing workers.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import sqlite3
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Literal, cast

from sqlalchemy import or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts.models import ObservationCreate, RunStatus

from ..persistence.db import Database, RunRecord, StudyRecord, as_utc, utcnow
from ..persistence.risk_records import RiskAlertRecord
from ..persistence.risk_research_records import (
    SPOT_REQUEST_PROTOCOL,
    RiskResearchPlanRecord,
    RiskResearchRequestRecord,
    RiskResearchTriggerRecord,
)
from ..ports.market_path import aware_utc, canonical_hash, spot_scope
from .admission import AdmissionService
from .research_run_scope import SpotAdmissionResolver
from .research_tracking import host_work_unsettled_in_session, research_admission_guard_in_session
from .risk_admission_wait import (
    MAX_GENERATION,
    SOURCE_REASON,
    AdmissionConditionWait,
    ContinuousAdmissionWriter,
    admission_accounting_valid,
    defer_owned_condition,
    observe_owned,
)
from .risk_monitor import _writer, canonical_json, content_hash
from .run import RunService
from .spot_scope_admission import SpotSourceFreshnessWait
from .study import ensure_study, stable_id

log = logging.getLogger(__name__)

INSTRUMENTS = ("BTC-USDT", "ETH-USDT")
CLAIM_RENEW_INTERVAL_SECONDS = 10.0
TERMINAL = {
    item.value
    for item in (
        RunStatus.completed,
        RunStatus.degraded,
        RunStatus.failed,
        RunStatus.cancelled,
    )
}
Kind = Literal["manual", "daily", "risk_alert"]


def _trigger_admission_origin(row, triggers, *, now: datetime) -> Literal["manual", "automatic"]:
    """Classify the immutable server-saved trigger, never a caller's kind alone.

    The existing owner API writes the identity after its owner/header checks.
    This binds that saved command identity; it does not add account attestation.
    """
    if any(t.kind == "risk_alert_recheck" for t in triggers) and len(triggers) != 1:
        raise ValueError("risk_alert_recheck_trigger_mixed")
    origin: Literal["manual", "automatic"] = "automatic"
    for trigger in triggers:
        try:
            payload = json.loads(trigger.payload_json)
        except (ValueError, TypeError) as error:
            raise ValueError("risk_research_trigger_corrupt") from error
        if (
            not isinstance(payload, dict)
            or set(payload) != {"instrument", "scope_id", "kind", "source_ref", "source"}
            or not isinstance(payload["source"], dict)
            or row.scope_id != spot_scope(row.instrument).scope_id
            or trigger.request_id != row.request_id
            or trigger.scope_id != row.scope_id
            or payload["instrument"] != row.instrument
            or payload["scope_id"] != row.scope_id
            or payload["kind"] != trigger.kind
            or payload["source_ref"] != trigger.source_ref
            or content_hash(payload) != trigger.payload_hash
            or not as_utc(row.created_at) <= as_utc(trigger.recorded_at) <= now
        ):
            raise ValueError("risk_research_trigger_corrupt")
        if trigger.kind != "manual" or not trigger.source_ref.startswith("owner-command:"):
            continue
        command_id = trigger.source_ref.removeprefix("owner-command:")
        owner = payload["source"].get("owner_id")
        if (
            not command_id
            or len(command_id) > 128
            or trigger.trigger_key != f"manual:{command_id}"
            or set(payload["source"]) != {"owner_id"}
            or not isinstance(owner, str)
            or not owner.strip()
            or len(owner) > 128
        ):
            raise ValueError("risk_research_manual_authority_invalid")
        origin = "manual"
    return origin


def _recognized_request_triggers(
    session: Session,
    row: RiskResearchRequestRecord,
    *,
    now: datetime,
) -> list[RiskResearchTriggerRecord]:
    """Resolve persisted scope/trigger/plan identity without granting admission."""
    triggers = list(
        session.scalars(
            select(RiskResearchTriggerRecord)
            .where(
                RiskResearchTriggerRecord.request_id == row.request_id,
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    if (
        row.protocol_version != SPOT_REQUEST_PROTOCOL
        or row.instrument not in INSTRUMENTS
        or row.scope_id != spot_scope(row.instrument).scope_id
        or not triggers
        or not as_utc(row.created_at) <= as_utc(row.updated_at) <= now
        or any(
            trigger.kind
            not in {"manual", "daily", "risk_alert", "risk_alert_recheck", "action_review"}
            or (trigger.kind == "manual" and not trigger.source_ref.startswith("owner-command:"))
            for trigger in triggers
        )
    ):
        raise ValueError("risk_research_trigger_corrupt")
    _trigger_admission_origin(row, triggers, now=now)
    from .risk_alert_recheck import recognized_recheck

    recognized_recheck(session, row, triggers, now=now)
    from .action_review import recognized_action_review

    recognized_action_review(session, row, triggers, now=now)
    for trigger in triggers:
        if trigger.kind != "daily":
            continue
        payload = json.loads(trigger.payload_json)
        source = payload["source"]
        plan = session.get(RiskResearchPlanRecord, trigger.source_ref, populate_existing=True)
        if (
            plan is None
            or plan.state != "active"
            or plan.scope_id != row.scope_id
            or plan.instrument != row.instrument
            or plan.interval_seconds != 86400
            or set(source) != {"scheduled_at", "interval_seconds"}
            or source["interval_seconds"] != 86400
            or trigger.trigger_key != f"daily:{plan.plan_id}:{source['scheduled_at']}"
            or not as_utc(plan.created_at)
            <= datetime.fromisoformat(
                source["scheduled_at"],
            )
            <= as_utc(trigger.recorded_at)
        ):
            raise ValueError("risk_research_trigger_corrupt")
    if not any(
        row.request_id
        == "riskresearch_"
        + canonical_hash(
            [
                row.scope_id,
                trigger.trigger_key,
            ]
        )[:40]
        and as_utc(trigger.recorded_at) == as_utc(row.created_at)
        for trigger in triggers
    ):
        raise ValueError("risk_research_trigger_corrupt")
    return triggers


class RiskResearchAdmissionService:
    def __init__(
        self,
        database: Database,
        *,
        prepare_scope: Callable[[str], Awaitable[None]] | None = None,
        spot_admission_resolver: SpotAdmissionResolver | None = None,
        clock: Callable[[], datetime] = utcnow,
        enabled: bool = False,
        owner_id: str | None = None,
        action_review_enabled: bool = False,
        perpetual_driver=None,
    ) -> None:
        self.database, self.clock, self.enabled = database, clock, enabled
        self.perpetual_driver = perpetual_driver
        self.request_protocol = perpetual_driver.protocol_version if perpetual_driver is not None else SPOT_REQUEST_PROTOCOL
        self.request_protocols = tuple(getattr(perpetual_driver, "supported_protocols", (self.request_protocol,)))
        self.prepare_scope = prepare_scope
        from .action_review import ActionReviewService

        self.action_review = ActionReviewService(
            database, clock=clock, enabled=enabled and action_review_enabled
        )
        self._action_review_cursor = None
        self.runs = RunService(
            database, clock=clock, spot_admission_resolver=spot_admission_resolver
        )
        self.admission = AdmissionService(database, clock=clock)
        self.owner_id = owner_id or f"risk-research:{uuid.uuid4().hex}"

    def ensure_daily_plans(self) -> None:
        if not self.enabled:
            return
        now = aware_utc(self.clock())
        with self.database.session() as session:
            _writer(session)
            for instrument in INSTRUMENTS:
                key = f"{instrument.lower()}-spot-daily-research.v1"
                if session.get(RiskResearchPlanRecord, key) is None:
                    session.add(
                        RiskResearchPlanRecord(
                            plan_id=key,
                            scope_id=spot_scope(instrument).scope_id,
                            instrument=instrument,
                            next_due_at=now,
                            created_at=now,
                        )
                    )

    def stage(
        self,
        instrument: str,
        *,
        request_id: str,
        kind: Kind = "manual",
        source_ref: str | None = None,
        source: dict[str, object] | None = None,
    ) -> dict[str, object]:
        if not self.enabled:
            raise ValueError("spot_research_disabled")
        if (
            kind not in {"manual", "daily", "risk_alert"}
            or instrument not in INSTRUMENTS
            or not request_id
            or len(request_id) > 128
        ):
            raise ValueError("risk_research_request_invalid")
        now = aware_utc(self.clock())
        with self.database.session() as session:
            _writer(session)
            key = f"{kind}:{request_id}"
            row = self._stage_in_session(
                session,
                instrument,
                key=key,
                kind=kind,
                source_ref=source_ref or key,
                source=source or {},
                now=now,
            )
            session.flush()
            return self._view_in_session(session, row)

    def _stage_in_session(self, session, instrument, *, key, kind, source_ref, source, now):
        scope_id = spot_scope(instrument).scope_id
        payload = {
            "instrument": instrument,
            "scope_id": scope_id,
            "kind": kind,
            "source_ref": source_ref,
            "source": source,
        }
        digest = content_hash(payload)
        prior = session.get(RiskResearchTriggerRecord, key)
        if prior is not None:
            if (
                prior.scope_id != scope_id
                or prior.payload_hash != digest
                or content_hash(json.loads(prior.payload_json)) != digest
            ):
                raise ValueError("risk_research_request_identity_mismatch")
            original = session.get(RiskResearchRequestRecord, prior.request_id)
            if original is None or original.protocol_version != SPOT_REQUEST_PROTOCOL:
                raise ValueError("risk_research_protocol_unsupported")
            return original
        self._expire_final_claims(session, now=now, scope_id=scope_id)
        # All trigger originals are kept, including a more severe alert that
        # arrives during a session. An unsettled Run cannot spawn another one.
        active = session.scalar(
            select(RiskResearchRequestRecord).where(RiskResearchRequestRecord.protocol_version.in_(self.request_protocols))
            .where(
                RiskResearchRequestRecord.scope_id == scope_id,
                RiskResearchRequestRecord.status.in_(["pending_admission", "claiming"]),
                ~RiskResearchRequestRecord.request_id.in_(
                    select(
                        RiskResearchTriggerRecord.request_id,
                    ).where(
                        RiskResearchTriggerRecord.kind.in_(("risk_alert_recheck", "action_review"))
                    )
                ),
            )
            .order_by(RiskResearchRequestRecord.created_at.desc())
            .limit(1)
        )
        if active is None:
            previous = session.scalar(
                select(RiskResearchRequestRecord).where(RiskResearchRequestRecord.protocol_version.in_(self.request_protocols))
                .where(
                    RiskResearchRequestRecord.scope_id == scope_id,
                    RiskResearchRequestRecord.run_id.is_not(None),
                )
                .order_by(RiskResearchRequestRecord.created_at.desc())
                .limit(1)
            )
            previous_run = session.get(RunRecord, previous.run_id) if previous else None
            earliest = (
                max(now, as_utc(previous_run.created_at) + timedelta(minutes=30))
                if previous_run
                else now
            )
            active = RiskResearchRequestRecord(
                request_id=f"riskresearch_{canonical_hash([scope_id, key])[:40]}",
                scope_id=scope_id,
                instrument=instrument,
                status="pending_admission",
                created_at=now,
                updated_at=now,
                available_at=earliest,
                attempts=0,
                lease_generation=0,
            )
            session.add(active)
        session.add(
            RiskResearchTriggerRecord(
                trigger_key=key,
                request_id=active.request_id,
                kind=kind,
                scope_id=scope_id,
                source_ref=source_ref,
                payload_json=canonical_json(payload),
                payload_hash=digest,
                recorded_at=now,
            )
        )
        return active

    def discover(self) -> None:
        if not self.enabled:
            return
        now = aware_utc(self.clock())
        with self.database.session() as session:
            _writer(session)
            plans = session.scalars(
                select(RiskResearchPlanRecord).where(
                    RiskResearchPlanRecord.state == "active",
                    RiskResearchPlanRecord.next_due_at <= now,
                )
            ).all()
            for plan in plans:
                slot = as_utc(plan.next_due_at)
                self._stage_in_session(
                    session,
                    plan.instrument,
                    key=f"daily:{plan.plan_id}:{slot.isoformat()}",
                    kind="daily",
                    source_ref=plan.plan_id,
                    source={"scheduled_at": slot.isoformat(), "interval_seconds": 86400},
                    now=now,
                )
                # Missed days are explicitly coalesced at the present time, not
                # backfilled into fictional historical analyses.
                plan.next_due_at = now + timedelta(seconds=plan.interval_seconds)
            alerts = session.scalars(
                select(RiskAlertRecord)
                .outerjoin(
                    RiskResearchTriggerRecord,
                    RiskResearchTriggerRecord.trigger_key
                    == "risk_alert:" + RiskAlertRecord.alert_id,
                )
                .where(
                    RiskResearchTriggerRecord.trigger_key.is_(None),
                    RiskAlertRecord.created_at <= now,
                )
                .order_by(
                    RiskAlertRecord.created_at.asc(),
                )
                .limit(20)
            ).all()
            for alert in alerts:
                instrument = next(
                    (x for x in INSTRUMENTS if spot_scope(x).scope_id == alert.scope_id), None
                )
                payload = json.loads(alert.payload_json)
                if content_hash(payload) != alert.payload_hash:
                    raise ValueError("risk_alert_payload_corrupt")
                # Isolated acceptance inputs can never start real paid research.
                fixture = str(payload.get("input", {}).get("input_ref", "")).startswith(
                    "fixture://"
                )
                if fixture or instrument is None:
                    excluded = {
                        "schema_version": "risk-research-excluded-trigger.v1",
                        "source_ref": alert.alert_id,
                        "scope_id": alert.scope_id,
                        "payload_hash": alert.payload_hash,
                        "reason": (
                            "fixture_cannot_start_live_research"
                            if fixture
                            else "scope_has_no_spot_research_admission"
                        ),
                    }
                    session.add(
                        RiskResearchTriggerRecord(
                            trigger_key=f"risk_alert:{alert.alert_id}",
                            request_id=f"excluded:{alert.alert_id}",
                            kind="excluded_fixture" if fixture else "excluded_scope",
                            scope_id=alert.scope_id,
                            source_ref=alert.alert_id,
                            payload_json=canonical_json(excluded),
                            payload_hash=content_hash(excluded),
                            recorded_at=now,
                        )
                    )
                    continue
                self._stage_in_session(
                    session,
                    instrument,
                    key=f"risk_alert:{alert.alert_id}",
                    kind="risk_alert",
                    source_ref=alert.alert_id,
                    source={
                        "payload_hash": alert.payload_hash,
                        "severity": alert.severity,
                        "observed_at": as_utc(alert.observed_at).isoformat(),
                    },
                    now=now,
                )

    def _expire_final_claims(
        self,
        session: Session,
        *,
        now: datetime,
        scope_id: str | None = None,
    ) -> int:
        """Close expired final claims, including a prior process's lease.

        This retires the exact recorded lease. It cannot acquire a new lease,
        retry, assert Host inactivity, or create a Run. Its retired owner and
        expiry remain available on the terminal request for audit.
        """
        statement = select(RiskResearchRequestRecord).where(RiskResearchRequestRecord.protocol_version.in_(self.request_protocols)).where(
            RiskResearchRequestRecord.status == "claiming",
            RiskResearchRequestRecord.attempts == 3,
            RiskResearchRequestRecord.lease_generation >= 3,
            RiskResearchRequestRecord.run_id.is_(None),
            RiskResearchRequestRecord.event_id.is_(None),
            RiskResearchRequestRecord.lease_expires_at <= now,
        )
        if scope_id is not None:
            statement = statement.where(RiskResearchRequestRecord.scope_id == scope_id)
        closed = 0
        for row in session.scalars(
            statement.order_by(
                RiskResearchRequestRecord.lease_expires_at,
                RiskResearchRequestRecord.request_id,
            )
            .limit(8)
            .execution_options(populate_existing=True)
        ).all():
            try:
                if (
                    not admission_accounting_valid(session, row, now=now)
                    or row.lease_expires_at is None
                    or re.fullmatch(r"risk-research:[0-9a-f]{32}", row.lease_owner or "") is None
                    or as_utc(row.lease_expires_at)
                    != as_utc(row.updated_at) + timedelta(seconds=60)
                    or not as_utc(row.available_at) <= as_utc(row.updated_at) <= now
                ):
                    continue
                if self.perpetual_driver is not None:
                    self.perpetual_driver.recognize_in_session(session, row, now)
                else:
                    _recognized_request_triggers(session, row, now=now)
            except (ValueError, TypeError, KeyError):
                continue
            result = session.execute(
                update(RiskResearchRequestRecord)
                .where(
                    RiskResearchRequestRecord.request_id == row.request_id,
                    RiskResearchRequestRecord.status == "claiming",
                    RiskResearchRequestRecord.attempts == 3,
                    RiskResearchRequestRecord.lease_generation == row.lease_generation,
                    RiskResearchRequestRecord.lease_owner == row.lease_owner,
                    RiskResearchRequestRecord.lease_expires_at == row.lease_expires_at,
                    RiskResearchRequestRecord.updated_at == row.updated_at,
                    RiskResearchRequestRecord.run_id.is_(None),
                    RiskResearchRequestRecord.event_id.is_(None),
                )
                .values(
                    status="failed",
                    error_code="risk_research_claim_lease_expired",
                    updated_at=now,
                )
                .execution_options(synchronize_session=False)
            )
            if cast(CursorResult[tuple[object, ...]], result).rowcount == 1:
                closed += 1
                session.refresh(row)
                from .risk_alert_recheck import record_recheck_trace

                triggers = list(
                    session.scalars(
                        select(RiskResearchTriggerRecord).where(
                            RiskResearchTriggerRecord.request_id == row.request_id,
                        )
                    )
                )
                record_recheck_trace(
                    session,
                    row,
                    triggers,
                    now=now,
                    event="claim_expired",
                    reason="risk_research_claim_lease_expired",
                )
                log.info(
                    "risk_research_condition_pending request_id=%s generation=%s "
                    "stage=lease_expiry reason=risk_research_claim_lease_expired",
                    row.request_id, row.lease_generation,
                )
        return closed

    def _claim(self):
        with self.database.session() as session:
            _writer(session)
            now = aware_utc(self.clock())
            self._expire_final_claims(session, now=now)
            candidates = session.scalars(
                select(RiskResearchRequestRecord).where(RiskResearchRequestRecord.protocol_version.in_(self.request_protocols))
                .where(
                    RiskResearchRequestRecord.available_at <= now,
                    RiskResearchRequestRecord.attempts < 3,
                    or_(
                        RiskResearchRequestRecord.status == "pending_admission",
                        (RiskResearchRequestRecord.status == "claiming")
                        & (RiskResearchRequestRecord.lease_expires_at <= now),
                    ),
                )
                .order_by(RiskResearchRequestRecord.available_at)
                .limit(8)
            ).all()
            row = None
            for candidate in candidates:
                if (candidate.lease_generation >= MAX_GENERATION
                        or not admission_accounting_valid(session, candidate, now=now)):
                    continue
                if self.perpetual_driver is not None:
                    if self.perpetual_driver.claimable_in_session(session, candidate, now):
                        row = candidate
                        break
                    continue
                if (
                    not self.action_review.enabled
                    and session.scalar(
                        select(RiskResearchTriggerRecord.trigger_key)
                        .where(
                            RiskResearchTriggerRecord.request_id == candidate.request_id,
                            RiskResearchTriggerRecord.kind == "action_review",
                        )
                        .limit(1)
                    )
                    is not None
                ):
                    continue
                prior_runs = session.scalars(
                    select(RunRecord)
                    .join(
                        RiskResearchRequestRecord,
                        RiskResearchRequestRecord.run_id == RunRecord.run_id,
                    )
                    .where(RiskResearchRequestRecord.scope_id == candidate.scope_id, RiskResearchRequestRecord.protocol_version.in_(self.request_protocols))
                ).all()
                # A condition lease lets the collector obtain fresh Host proof;
                # it never grants Run admission or settles a previous Host link.
                # _fresh_preflight and _admit both retain the original Study guard.
                if any(run.status not in TERMINAL for run in prior_runs):
                    continue
                # The Study guard must cover every unsettled historical link.
                # Legacy or corrupt cross-Study identity cannot take this route.
                scope = json.dumps(
                    [
                        "crypto",
                        candidate.instrument,
                        "30m,24h,72h",
                        "spot-market-risk.v1",
                    ],
                    ensure_ascii=False,
                )
                expected_study_id = stable_id("study", scope)
                expected_study = session.get(StudyRecord, expected_study_id, populate_existing=True)
                if any(
                    host_work_unsettled_in_session(session, run.run_id)
                    and (
                        run.study_id != expected_study_id
                        or expected_study is None
                        or expected_study.scope_key
                        != hashlib.sha256(scope.encode("utf-8")).hexdigest()
                        or expected_study.status != "active"
                        or expected_study.market != "crypto"
                        or expected_study.instrument != candidate.instrument
                        or expected_study.horizon_scope != "30m,24h,72h"
                        or expected_study.question_key != "spot-market-risk.v1"
                        or expected_study.identity_basis != "exact_spot_monitor"
                    )
                    for run in prior_runs
                ):
                    continue
                row = candidate
                break
            if row is None:
                return None
            row.status, row.lease_owner = "claiming", self.owner_id
            row.lease_generation += 1
            row.attempts += 1
            row.lease_expires_at = now + timedelta(seconds=60)
            row.updated_at = now
            from .risk_alert_recheck import record_recheck_trace

            triggers = list(
                session.scalars(
                    select(RiskResearchTriggerRecord).where(
                        RiskResearchTriggerRecord.request_id == row.request_id,
                    )
                )
            )
            record_recheck_trace(session, row, triggers, now=now, event="claimed")
            return row.request_id, row.instrument, row.lease_generation

    def _owns(self, row, generation, now) -> bool:
        return bool(
            row
            and row.protocol_version in self.request_protocols
            and row.status == "claiming"
            and row.lease_owner == self.owner_id
            and row.lease_generation == generation
            and row.lease_expires_at
            and as_utc(row.lease_expires_at) > now
        )

    def _renew_claim(self, key: str, generation: int) -> bool | None:
        """Only the current unexpired claim can renew its existing 60s window."""
        with self.database.session() as session:
            try:
                _writer(session)
            except OperationalError as error:
                if (isinstance(error.orig, sqlite3.OperationalError)
                        and getattr(error.orig, "sqlite_errorcode", None) == sqlite3.SQLITE_BUSY
                        and error.statement == "BEGIN IMMEDIATE"):
                    return None  # No write occurred; keep observing the same operation.
                raise
            now = aware_utc(self.clock())
            row = session.get(RiskResearchRequestRecord, key, populate_existing=True)
            if (not self._owns(row, generation, now)
                    or row.run_id is not None or row.event_id is not None
                    or not admission_accounting_valid(session, row, now=now)):
                return False
            row.updated_at = now
            row.lease_expires_at = now + timedelta(seconds=60)
            return True

    _observe_owned = staticmethod(observe_owned)

    async def _keep_claim(self, key, generation, stop, lost):
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=CLAIM_RENEW_INTERVAL_SECONDS)
            except TimeoutError:
                try:
                    renewed = await self._observe_owned(
                        asyncio.to_thread(self._renew_claim, key, generation))
                except Exception as error:
                    log.warning(
                        "risk_research_lease_renew_failed request_id=%s generation=%s "
                        "error_class=%s", key, generation, type(error).__name__,
                    )
                    lost.set()
                    return
                if renewed is False:
                    lost.set()
                    return

    def _admit(self, key: str, generation: int) -> bool:
        with self.database.session() as session:
            try:
                _writer(session)
            except OperationalError as error:
                # This exact failed first BEGIN has no business reads or writes.
                # Errors after acquisition, especially COMMIT, remain unknown.
                if (
                    session.bind is not None
                    and session.bind.dialect.name == "sqlite"
                    and isinstance(error.orig, sqlite3.OperationalError)
                    and getattr(error.orig, "sqlite_errorcode", None) == sqlite3.SQLITE_BUSY
                    and error.statement == "BEGIN IMMEDIATE"
                ):
                    raise ValueError("risk_research_admission_writer_busy") from error
                raise
            now = aware_utc(self.clock())
            row = session.get(RiskResearchRequestRecord, key)
            if not self._owns(row, generation, now):
                return False
            with ContinuousAdmissionWriter(
                session, row, owner=self.owner_id, generation=generation, acquired_at=now,
            ) as writer:
                triggers = session.scalars(
                    select(RiskResearchTriggerRecord)
                    .where(
                        RiskResearchTriggerRecord.request_id == key,
                    )
                    .order_by(
                        RiskResearchTriggerRecord.recorded_at, RiskResearchTriggerRecord.trigger_key
                    )
                ).all()
                admission_origin = _trigger_admission_origin(row, triggers, now=now)
                from .risk_alert_recheck import (
                    RecheckAdmissionBlocked,
                    recognized_recheck,
                    record_recheck_trace,
                )

                recheck = recognized_recheck(session, row, triggers, now=now)
                from .action_review import recognized_action_review, require_action_review_current

                review = recognized_action_review(session, row, triggers, now=now)
                if review is not None:
                    if not self.action_review.enabled:
                        raise ValueError("action_review_disabled")
                    require_action_review_current(session, review, now=now)
                label = "比特币" if row.instrument == "BTC-USDT" else "以太坊"
                text = (
                    f"研究当前{label}({row.instrument})现货未来24小时的宏观驱动、市场风险及最强反方。"
                    "核对真实数据时点，区分已发生的变化和预测，保留缺失数据与不确定性。"
                    "\n本次触发依据："
                    + canonical_json([json.loads(t.payload_json) for t in triggers])
                )
                if recheck is not None:
                    text += (
                        f"\n本次为历史事件补查：原告警触发于{recheck.original_triggered_at.isoformat()}，"
                        f"本次接受补查于{recheck.accepted_at.isoformat()}，当前取证时间为{now.isoformat()}。"
                        + (
                            f"原变化条件已于{recheck.original_resolved_at.isoformat()}解除。"
                            if recheck.original_resolved_at
                            else "原告警的条件是否解除须以最新监测记录为准。"
                        )
                        + "分析使用本次可获得的证据，不能作为原触发时点的预测、事前预警或回测成绩。"
                    )
                if review is None:
                    request = ObservationCreate(
                        text=text,
                        source_id="market-risk-automatic-research",
                        source_type="manual",
                        language="zh",
                        event_hint="crypto_market_snapshot",
                    )
                    event_id, _envelope, _created = self.admission.admit(
                        request,
                        idempotency_key=key,
                        session=session,
                    )
                else:
                    event_id = review.event_id
                study_id = ensure_study(
                    session,
                    question_key="spot-market-risk.v1",
                    title=f"{label}现货持续研究",
                    market="crypto",
                    instrument=row.instrument,
                    horizon_scope="30m,24h,72h",
                    identity_basis="exact_spot_monitor",
                    now=now,
                )
                study = session.get(StudyRecord, study_id)
                if review is not None and study_id != review.study_id:
                    raise ValueError("action_review_study_identity_changed")
                if study is None or study.status != "active":
                    raise ValueError("study_not_active")
                guard = research_admission_guard_in_session(
                    session, study_id=study_id, now=now, admission_origin=admission_origin
                )
                if guard.reason_code == "study_host_terminal_unknown":
                    raise AdmissionConditionWait("durable_admission")
                if guard.reason_code is not None:
                    if recheck is not None:
                        raise RecheckAdmissionBlocked(guard.reason_code, guard.details)
                    raise ValueError(guard.reason_code)
                run_id, _created = self.runs.create(
                    event_id,
                    strategy_version="research.v1",
                    idempotency_key=f"risk-research:{key}",
                    parent_run_id=review.parent_run_id if review is not None else None,
                    admission_origin=admission_origin,
                    study_id=study_id,
                    session=session,
                )
                if review is not None:
                    child = session.get(RunRecord, run_id, populate_existing=True)
                    if child is None or (
                        child.parent_run_id,
                        child.event_id,
                        child.study_id,
                        child.admission_origin,
                    ) != (
                        review.parent_run_id,
                        review.event_id,
                        review.study_id,
                        "automatic",
                    ):
                        raise ValueError("action_review_child_binding_invalid")
                # The claim entered while valid and never released its writer.
                # Wall-clock expiry outside this lock still forbids entry or renewal.
                completed_at = aware_utc(self.clock())
                writer.complete(row, completed_at=completed_at)
                row.status, row.run_id, row.event_id = "queued", run_id, event_id
                row.updated_at, row.error_code = completed_at, None
                row.lease_owner = row.lease_expires_at = None
                record_recheck_trace(
                    session, row, triggers, now=completed_at, event="queued", capacity=guard.details
                )
                return True

    def _fresh_preflight(self, key: str, generation: int) -> bool:
        """Observe the original Study guard outside the Run writer.

        This never asserts Host inactivity or consumes another owner's lease.
        _admit repeats the authoritative guard in its uninterrupted writer.
        """
        now = aware_utc(self.clock())
        with self.database.session() as session:
            row = session.get(RiskResearchRequestRecord, key, populate_existing=True)
            if row is None or not self._owns(row, generation, now):
                return False
            triggers = _recognized_request_triggers(session, row, now=now)
            origin = _trigger_admission_origin(row, triggers, now=now)
            scope = json.dumps(
                [
                    "crypto",
                    row.instrument,
                    "30m,24h,72h",
                    "spot-market-risk.v1",
                ],
                ensure_ascii=False,
            )
            study = session.get(StudyRecord, stable_id("study", scope), populate_existing=True)
            if study is None:
                return True  # First Study is created by the original writer.
            if study.status != "active":
                raise ValueError("study_not_active")
            guard = research_admission_guard_in_session(
                session,
                study_id=study.study_id,
                now=now,
                admission_origin=origin,
            )
            if guard.reason_code == "study_host_terminal_unknown":
                raise AdmissionConditionWait("fresh_host_preflight")
            if guard.reason_code is not None:
                if any(t.kind == "risk_alert_recheck" for t in triggers):
                    from .risk_alert_recheck import RecheckAdmissionBlocked

                    raise RecheckAdmissionBlocked(guard.reason_code, guard.details)
                raise ValueError(guard.reason_code)
            return True

    def _defer_condition(self, key, generation, error: AdmissionConditionWait) -> bool:
        with self.database.session() as session:
            _writer(session)
            now = aware_utc(self.clock())
            row = session.get(RiskResearchRequestRecord, key, populate_existing=True)
            if not self._owns(row, generation, now):
                return False
            if row.run_id is not None or row.event_id is not None:
                return False
            triggers = _recognized_request_triggers(session, row, now=now)
            defer_owned_condition(session, row, triggers, error, now=now)
            return True

    def _failure(self, key, generation, error):
        with self.database.session() as session:
            _writer(session)
            now = aware_utc(self.clock())
            row = session.get(RiskResearchRequestRecord, key)
            if not self._owns(row, generation, now):
                return
            code = getattr(error, "error_code", None) or str(error).splitlines()[0]
            row.error_code = code[:160] or "risk_research_admission_failed"
            row.status = "failed" if row.attempts >= 3 else "pending_admission"
            row.available_at = now + timedelta(seconds=60 * 2 ** (row.attempts - 1))
            row.updated_at, row.lease_owner, row.lease_expires_at = now, None, None
            from .risk_alert_recheck import record_recheck_trace

            triggers = list(
                session.scalars(
                    select(RiskResearchTriggerRecord).where(
                        RiskResearchTriggerRecord.request_id == row.request_id,
                    )
                )
            )
            record_recheck_trace(
                session,
                row,
                triggers,
                now=now,
                event="budget_blocked"
                if row.error_code == "research_admission_budget_exhausted"
                else "admission_failed",
                reason=row.error_code,
                capacity=getattr(error, "capacity", {}),
            )

    async def process_due(self, *, limit: int = 2) -> int:
        if not self.enabled:
            return 0
        claim_limit = max(0, min(limit, 8))

        async def discover_sources() -> None:
            if self.perpetual_driver is None:
                self.ensure_daily_plans()
                self.discover()
            else:
                collect_conditions = getattr(self.perpetual_driver, "collect_condition_evidence", None)
                if collect_conditions is not None:
                    await self._observe_owned(collect_conditions())
                await self._observe_owned(asyncio.to_thread(self.perpetual_driver.discover))
            if self.action_review.enabled:
                reviews = self.action_review.discover(
                    max_opportunities=2, after=self._action_review_cursor
                )
                self._action_review_cursor = reviews["next_cursor"]
                for error in reviews["errors"]:
                    log.warning(
                        "action_review_discovery_failed opportunity=%s reason=%s",
                        error["opportunity_id"],
                        error["reason"],
                    )

        # Claim durable backlog before another potentially expensive source pass.
        # Discovery still runs after this finite batch, so new slots cannot starve.
        first_claim = (
            self._claim()
            if self.perpetual_driver is not None and claim_limit
            else None
        )
        claimed_backlog = first_claim is not None
        if not claimed_backlog:
            await discover_sources()
        processed = 0
        for _ in range(claim_limit):
            claim = first_claim if first_claim is not None else self._claim()
            first_claim = None
            if claim is None:
                break
            key, instrument, generation = claim
            stop_renewal, lost_claim = asyncio.Event(), asyncio.Event()
            renewer = asyncio.create_task(self._keep_claim(
                key, generation, stop_renewal, lost_claim))
            stage = "scope_prepare"
            try:
                if self.perpetual_driver is not None:
                    stage = "perpetual_prepare"
                    prepared = await self._observe_owned(self.perpetual_driver.prepare(key, generation, self.owner_id))
                    if not lost_claim.is_set() and await self._observe_owned(asyncio.to_thread(self.perpetual_driver.admit, key, generation, self, prepared)):
                        processed += 1
                    continue
                if self.prepare_scope is None:
                    raise ValueError("spot_effective_preparer_not_configured")
                await self._observe_owned(self.prepare_scope(instrument))
                if lost_claim.is_set():
                    continue
                stage = "fresh_host_preflight"
                if not await self._observe_owned(
                        asyncio.to_thread(self._fresh_preflight, key, generation)):
                    log.info(
                        "risk_research_condition_pending request_id=%s generation=%s "
                        "stage=lease_ownership",
                        key,
                        generation,
                    )
                    continue
                stage = "durable_admission"
                if not lost_claim.is_set() and await self._observe_owned(
                        asyncio.to_thread(self._admit, key, generation)):
                    processed += 1
            except AdmissionConditionWait as exc:
                # A completed guard result, after any admission writer rollback.
                # Pending operations, unknown commits and lost leases cannot credit.
                deferred = await self._observe_owned(
                    asyncio.to_thread(self._defer_condition, key, generation, exc))
                log.warning(
                    "risk_research_condition_wait request_id=%s generation=%s "
                    "stage=%s deferred=%s reason=%s",
                    key, generation, exc.stage, deferred, exc.error_code,
                )
            except SpotSourceFreshnessWait as exc:
                if (
                    type(exc) is not SpotSourceFreshnessWait
                    or stage != "scope_prepare"
                    or not exc.matches_condition(instrument, aware_utc(self.clock()))
                ):
                    await self._observe_owned(
                        asyncio.to_thread(self._failure, key, generation, exc))
                else:
                    condition = AdmissionConditionWait("scope_prepare", reason_code=SOURCE_REASON)
                    deferred = await self._observe_owned(
                        asyncio.to_thread(self._defer_condition, key, generation, condition))
                    log.warning(
                        "risk_research_condition_wait request_id=%s generation=%s "
                        "stage=scope_prepare deferred=%s reason=%s",
                        key, generation, deferred, condition.error_code,
                    )
            except (ValueError, OSError, RuntimeError) as exc:
                # The error string may contain provider details. Stages are a
                # fixed local vocabulary; no URL, credential or body is logged.
                log.warning(
                    "risk_research_condition_pending request_id=%s generation=%s "
                    "stage=%s error_class=%s",
                    key,
                    generation,
                    stage,
                    type(exc).__name__,
                )
                await self._observe_owned(asyncio.to_thread(self._failure, key, generation, exc))
            finally:
                stop_renewal.set()
                await self._observe_owned(renewer)
        if claimed_backlog:
            await discover_sources()
        return processed

    def _view_in_session(self, session, row) -> dict[str, object]:
        if row is None:
            raise ValueError("risk_research_request_missing")
        if row.protocol_version not in self.request_protocols:
            raise ValueError("risk_research_protocol_unsupported")
        run = session.get(RunRecord, row.run_id) if row.run_id else None
        status = row.status
        if run and run.status in TERMINAL:
            status = "completed" if run.status in {"completed", "degraded"} else "failed"
        return {
            "schema_version": "risk-research-request.v1",
            "request_id": row.request_id,
            "scope_id": row.scope_id,
            "instrument": row.instrument,
            "status": status,
            "run_id": row.run_id,
            "run_status": run.status if run else None,
            "created_at": as_utc(row.created_at).isoformat(),
            "available_at": as_utc(row.available_at).isoformat(),
            "attempts": row.attempts,
            "error_code": (run.error_code if run else row.error_code),
            "trigger_refs": [
                t.source_ref
                for t in session.scalars(
                    select(
                        RiskResearchTriggerRecord,
                    ).where(RiskResearchTriggerRecord.request_id == row.request_id)
                ).all()
            ],
        }

    def latest(self, scope_id: str) -> dict[str, object] | None:
        with self.database.session() as session:
            row = session.scalar(
                select(RiskResearchRequestRecord).where(RiskResearchRequestRecord.protocol_version.in_(self.request_protocols))
                .where(
                    RiskResearchRequestRecord.scope_id == scope_id,
                )
                .order_by(RiskResearchRequestRecord.created_at.desc())
                .limit(1)
            )
            return self._view_in_session(session, row) if row else None

    def owner_command_status(self, scope_id: str, owner_id: str, command_id: str):
        """Read the saved manual binding; a newer automatic request cannot replace it."""
        now = aware_utc(self.clock())
        with self.database.session() as session:
            trigger = session.get(RiskResearchTriggerRecord, f"manual:{command_id}")
            if trigger is None or trigger.scope_id != scope_id:
                return None
            payload = json.loads(trigger.payload_json)
            if payload.get("source", {}).get("owner_id") != owner_id:
                return None
            row = session.get(RiskResearchRequestRecord, trigger.request_id)
            if row is None or row.scope_id != scope_id:
                raise ValueError("risk_research_owner_receipt_binding_invalid")
            if _trigger_admission_origin(row, [trigger], now=now) != "manual":
                raise ValueError("risk_research_owner_receipt_binding_invalid")
            if trigger.source_ref != f"owner-command:{command_id}":
                raise ValueError("risk_research_owner_receipt_binding_invalid")
            automatic = None
            candidates = session.scalars(
                select(RiskResearchRequestRecord).where(RiskResearchRequestRecord.protocol_version.in_(self.request_protocols))
                .where(
                    RiskResearchRequestRecord.scope_id == scope_id,
                    RiskResearchRequestRecord.request_id != row.request_id,
                    RiskResearchRequestRecord.created_at <= now,
                    RiskResearchRequestRecord.status.in_(
                        ("pending_admission", "claiming", "queued")
                    ),
                )
                .order_by(
                    RiskResearchRequestRecord.created_at.desc(),
                    RiskResearchRequestRecord.request_id,
                )
            )
            for candidate in candidates:
                triggers = list(
                    session.scalars(
                        select(RiskResearchTriggerRecord).where(
                            RiskResearchTriggerRecord.request_id == candidate.request_id
                        )
                    )
                )
                if not triggers or any(
                    t.kind not in {"daily", "risk_alert", "action_review"} for t in triggers
                ):
                    continue
                _trigger_admission_origin(candidate, triggers, now=now)
                projected = self._view_in_session(session, candidate)
                if projected["status"] in {"pending_admission", "claiming", "queued"}:
                    automatic = projected
                    break
            return {
                "schema_version": "risk-research-owner-receipt.v1",
                "owner_id": owner_id,
                "scope_id": scope_id,
                "command_id": command_id,
                "accepted_at": as_utc(trigger.recorded_at),
                "as_of": now,
                "request": self._view_in_session(session, row),
                "next_automatic_request": automatic,
            }

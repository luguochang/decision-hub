from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from contextlib import nullcontext
from datetime import UTC, datetime
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select, text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts import ReviewScheduleView
from packages.kernel.decision_hub_kernel.application.market_scope import research_scope_enabled
from packages.kernel.decision_hub_kernel.application.research_run_scope import (
    SpotAdmissionResolver,
    require_run_research_scope_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_tracking import (
    ConfirmedReportReader,
    TrackingBasis,
    host_work_unsettled_in_session,
    research_admission_guard_in_session,
    tracking_basis_in_session,
)
from packages.kernel.decision_hub_kernel.application.review_recovery import (
    review_recovery_candidates_in_session,
    validate_review_recovery_in_session,
)
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    DshSessionLinkRecord,
    EvaluationRecord,
    ForecastRecord,
    MacroEventRecord,
    ResearchFactRecord,
    ReviewScheduleRecord,
    RunEventRecord,
    RunRecord,
    RunStatus,
    StudyRecord,
    as_utc,
    utcnow,
)


class ScheduledRecheckPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    review_plan_id: str
    parent_run_id: str
    child_run_id: str
    event_id: str
    horizon: Literal["30m", "24h", "72h"]
    trigger_type: Literal["next_review", "macro_event", "invalidation_evidence", "outcome_due"]
    trigger_at: datetime
    idempotency_key: str


class ReviewScheduleService:
    """Discover, persist and admit deterministic recheck triggers.

    A review plan exists before it is due and is the scheduling authority. Its
    trigger hash makes discovery restart-safe; the child Run idempotency key
    makes admission retry-safe.
    """

    TERMINAL = {RunStatus.completed.value, RunStatus.degraded.value}
    CHILD_TERMINAL = {
        RunStatus.completed.value: "completed",
        RunStatus.degraded.value: "completed",
        RunStatus.failed.value: "failed",
        RunStatus.cancelled.value: "skipped",
    }

    def __init__(
        self,
        database: Database,
        *,
        clock: Callable[[], datetime] = utcnow,
        max_parents: int = 100,
        spot_admission_resolver: SpotAdmissionResolver | None = None,
        confirmed_report_reader: ConfirmedReportReader | None = None,
    ) -> None:
        self.database = database
        self.clock = clock
        self.runs = RunService(
            database, clock=clock, spot_admission_resolver=spot_admission_resolver,
        )
        self.max_parents = max_parents
        self.confirmed_report_reader = confirmed_report_reader

    def bind_confirmed_report_reader(self, reader: ConfirmedReportReader) -> None:
        """Normal server composition only, before the worker starts ticking."""
        if self.confirmed_report_reader is not None:
            raise ValueError("review_report_reader_already_bound")
        self.confirmed_report_reader = reader

    def plan_due(self, *, now: datetime | None = None) -> tuple[ScheduledRecheckPlan, ...]:
        current = now or self.clock()
        if current.tzinfo is None:
            raise ValueError("review_schedule_clock_timezone_required")
        current = current.astimezone(UTC)
        self._sync_child_statuses(current)
        active_parent_ids = self._discover_plans(current)
        self._retire_superseded_plans(current, active_parent_ids)
        return self._admit_due(current)

    def list_for_run(self, parent_run_id: str) -> list[ReviewScheduleView]:
        with self.database.session() as session:
            rows = (
                session.query(ReviewScheduleRecord)
                .filter_by(parent_run_id=parent_run_id)
                .order_by(ReviewScheduleRecord.trigger_at.asc())
                .all()
            )
            associations = {
                row.review_plan_id: self._associations_in_session(session, row) for row in rows
            }
        views: list[ReviewScheduleView] = []
        for row in rows:
            # Preserve legacy/corrupt physical NULL checks despite ORM types.
            trigger_at = as_utc(cast(datetime | None, row.trigger_at))
            updated_at = as_utc(cast(datetime | None, row.updated_at))
            if trigger_at is None or updated_at is None:  # pragma: no cover
                continue
            views.append(
                ReviewScheduleView.model_validate(
                    {
                        "review_plan_id": row.review_plan_id,
                        "parent_run_id": row.parent_run_id,
                        "horizon": row.horizon,
                        "trigger_type": row.trigger_type,
                        "trigger_ref": row.trigger_ref,
                        "trigger_at": trigger_at,
                        "status": row.status,
                        "child_run_id": row.child_run_id,
                        "failure_code": row.failure_code,
                        "updated_at": updated_at,
                        **associations[row.review_plan_id],
                    }
                )
            )
        return views

    @staticmethod
    def _associations_in_session(
        session: Session, plan: ReviewScheduleRecord
    ) -> dict[str, str | int | None]:
        values: dict[str, str | int | None] = {
            "replacement_run_id": None,
            "replacement_review_plan_id": None,
            "retry_of_plan_id": None,
            "retry_attempt": 0,
        }
        parent = session.get(RunRecord, plan.parent_run_id)
        if parent is None or parent.study_id is None:
            return values
        events = (
            session.query(RunEventRecord)
            .filter(
                RunEventRecord.run_id == parent.run_id,
                RunEventRecord.event_type.in_(
                    (
                        "research.review_basis.transferred",
                        "research.scheduled_recheck.recovery_planned",
                    )
                ),
            )
            .order_by(RunEventRecord.sequence_no.asc())
            .all()
        )
        for event in events:
            try:
                payload = json.loads(event.payload_json)
            except (ValueError, TypeError):
                continue
            if not isinstance(payload, dict):
                continue
            if event.event_type == "research.review_basis.transferred" and plan.status == "skipped":
                if (
                    payload.get("old_review_plan_id") != plan.review_plan_id
                    or payload.get("old_parent_run_id") != parent.run_id
                ):
                    continue
                replacement_id = payload.get("new_review_plan_id")
                replacement_run_id = payload.get("new_parent_run_id")
                if not isinstance(replacement_id, str) or not isinstance(replacement_run_id, str):
                    continue
                replacement = session.get(ReviewScheduleRecord, replacement_id)
                target = session.get(RunRecord, replacement_run_id)
                if (
                    replacement is not None
                    and target is not None
                    and target.run_id != parent.run_id
                    and target.study_id == parent.study_id
                    and replacement.parent_run_id == target.run_id
                    and replacement.horizon == plan.horizon == payload.get("horizon")
                    and payload.get("reason_code")
                    == plan.failure_code
                    == "superseded_by_newer_tracking_basis"
                ):
                    values["replacement_run_id"] = target.run_id
                    values["replacement_review_plan_id"] = replacement.review_plan_id
            if event.event_type == "research.scheduled_recheck.recovery_planned":
                base_id, attempt = payload.get("base_plan_id"), payload.get("attempt_no")
                if (
                    payload.get("review_plan_id") != plan.review_plan_id
                    or not isinstance(base_id, str)
                    or type(attempt) is not int
                    or attempt not in (1, 2)
                    or plan.trigger_ref != f"retry:v1:{base_id}:{attempt}"
                ):
                    continue
                base = session.get(ReviewScheduleRecord, base_id)
                if (
                    base is not None
                    and base.parent_run_id == parent.run_id
                    and base.horizon == plan.horizon
                ):
                    values["retry_of_plan_id"], values["retry_attempt"] = base_id, attempt
        return values

    def _discover_plans(self, now: datetime) -> set[str]:
        bases: list[TrackingBasis] = []
        with self.database.session() as session:
            # These IDs are discovery hints, never the authority to write a
            # plan. Empty or ineligible Studies must not reserve SQLite's one
            # writer merely because this periodic maintenance cycle ran.
            study_ids = [
                study_id
                for study_id in session.scalars(select(StudyRecord.study_id)).all()
                if tracking_basis_in_session(
                    session, study_id=study_id, now=now,
                    confirmed_report_reader=self.confirmed_report_reader,
                ) is not None
            ]
        for study_id in study_ids:
            with self.database.session() as session:
                session.execute(text("BEGIN IMMEDIATE"))
                # A manual update, pause, or corrupt original may have changed
                # since discovery. Re-read the existing authority verifier in
                # this Study's physical writer; do not reuse the read-only hint.
                basis = tracking_basis_in_session(
                    session, study_id=study_id, now=now,
                    confirmed_report_reader=self.confirmed_report_reader,
                )
                if basis is None:
                    continue
                bases.append(basis)
                for horizon in basis.result.horizons:
                    if (
                        now >= horizon.expires_at
                        or horizon.next_review_at < basis.result.finished_at
                        or horizon.next_review_at >= horizon.expires_at
                    ):
                        continue  # Only the current saved, unexpired window.
                    if basis.command_request_id is None:
                        self._ensure_plan(
                            parent_run_id=basis.run.run_id,
                            horizon=horizon.horizon,
                            trigger_type="next_review",
                            trigger_ref=_trigger_token(horizon.next_review_at),
                            trigger_at=horizon.next_review_at,
                            now=now,
                            session=session,
                        )
                    else:
                        self._ensure_tracking_plan_in_session(session, basis, horizon.horizon, now)
                self._ensure_recoveries_in_session(session, basis, now)

        # Event discovery can use its existing short sessions. Admission always
        # rechecks authority under the writer lock, including a concurrent update.
        for basis in bases:
            parent, result = basis.run, basis.result
            parent_finished = as_utc(cast(datetime | None, parent.finished_at or parent.updated_at))
            if parent_finished is None:  # pragma: no cover - non-null database invariant
                continue
            for horizon in result.horizons:
                active = now < horizon.expires_at
                if active:
                    self._discover_macro_plans(
                        parent_run_id=parent.run_id,
                        horizon=horizon.horizon,
                        window_start=parent_finished,
                        window_end=horizon.expires_at,
                        now=now,
                    )
                self._discover_outcome_plan(parent=parent, horizon=horizon.horizon, now=now)
                if active:
                    self._discover_invalidation_plans(
                        parent_run_id=parent.run_id,
                        horizon=horizon.horizon,
                        invalidation=horizon.invalidation,
                        since=parent_finished,
                        now=now,
                    )
        return {basis.run.run_id for basis in bases}

    def _ensure_recoveries_in_session(
        self, session: Session, basis: TrackingBasis, now: datetime
    ) -> None:
        if basis.run.study_id is None:
            return
        for candidate in review_recovery_candidates_in_session(
            session, basis_run_id=basis.run.run_id, study_id=basis.run.study_id, now=now
        ):
            digest = hashlib.sha256(
                f"review-recovery.v1|{candidate.parent_run_id}|{candidate.trigger_ref}".encode()
            ).hexdigest()
            plan_id = f"review_plan_{digest}"
            if session.get(ReviewScheduleRecord, plan_id) is not None:
                continue
            session.add(
                ReviewScheduleRecord(
                    review_plan_id=plan_id,
                    parent_run_id=candidate.parent_run_id,
                    horizon=candidate.horizon,
                    trigger_type="next_review",
                    trigger_ref=candidate.trigger_ref,
                    trigger_at=candidate.trigger_at,
                    trigger_hash=digest,
                    status="planned",
                    created_at=now,
                    updated_at=now,
                )
            )
            session.flush()
            self._audit_in_session(
                session,
                basis.run.run_id,
                "research.scheduled_recheck.recovery_planned",
                {
                    "review_plan_id": plan_id,
                    "base_plan_id": candidate.base_plan_id,
                    "source_plan_id": candidate.source_plan_id,
                    "source_failed_run_id": candidate.source_failed_run_id,
                    "attempt_no": candidate.attempt_no,
                    "trigger_ref": candidate.trigger_ref,
                    "trigger_at": candidate.trigger_at.isoformat(),
                    "expires_at": candidate.expires_at.isoformat(),
                    "failure_trace_hash": candidate.failure_trace_hash,
                    "terminal_receipt_ref": candidate.terminal_receipt_ref,
                    "terminal_receipt_hash": candidate.terminal_receipt_hash,
                },
                now,
            )

    def _ensure_tracking_plan_in_session(
        self, session: Session, basis: TrackingBasis, horizon_name: str, now: datetime
    ) -> None:
        horizon = next(item for item in basis.result.horizons if item.horizon == horizon_name)
        ref = f"tracking_next_review:{basis.run.run_id}:{horizon_name}"
        digest = hashlib.sha256(f"tracking-next-review.v1|{ref}".encode()).hexdigest()
        plan_id = f"review_plan_{digest}"
        if session.get(ReviewScheduleRecord, plan_id) is not None:
            return  # Frozen once, including skipped/failed plans after a restart.
        trigger_at = horizon.next_review_at
        old_plans = (
            session.query(ReviewScheduleRecord)
            .join(RunRecord, RunRecord.run_id == ReviewScheduleRecord.parent_run_id)
            .filter(
                RunRecord.study_id == basis.run.study_id,
                ReviewScheduleRecord.parent_run_id != basis.run.run_id,
                ReviewScheduleRecord.horizon == horizon_name,
                ReviewScheduleRecord.trigger_type == "next_review",
                ReviewScheduleRecord.status == "planned",
                ReviewScheduleRecord.trigger_at > now,
                ReviewScheduleRecord.trigger_at < horizon.expires_at,
                ReviewScheduleRecord.trigger_at >= basis.result.finished_at,
            )
            .all()
        )
        old_times = [
            value
            for old in old_plans
            if (value := as_utc(cast(datetime | None, old.trigger_at))) is not None
        ]
        if trigger_at <= now:
            # A late current update is not a replay of its expired parent.
            # Keep a legitimate old future slot, otherwise freeze actual now.
            trigger_at = min(old_times) if old_times else now
        elif old_times:
            trigger_at = min(trigger_at, *old_times)
        session.add(
            ReviewScheduleRecord(
                review_plan_id=plan_id,
                parent_run_id=basis.run.run_id,
                horizon=horizon_name,
                trigger_type="next_review",
                trigger_ref=ref,
                trigger_at=trigger_at,
                trigger_hash=digest,
                status="planned",
                created_at=now,
                updated_at=now,
            )
        )
        session.flush()
        self._audit_in_session(
            session,
            basis.run.run_id,
            "research.review_basis.activated",
            {
                "review_plan_id": plan_id,
                "authority_request_id": basis.command_request_id,
                "authority_intent_at": basis.intent_at.isoformat(),
                "horizon": horizon_name,
                "suggested_review_at": horizon.next_review_at.isoformat(),
                "trigger_at": trigger_at.isoformat(),
                "expires_at": horizon.expires_at.isoformat(),
                "late_activation": horizon.next_review_at <= now,
            },
            now,
        )

    def _retire_superseded_plans(self, now: datetime, active_parent_ids: set[str]) -> None:
        """Stop obsolete review trees before they consume the live worker queue.

        Only queued children are cancelled.  A child already executing keeps
        its lease and finishes normally so no in-flight provider call is
        orphaned.  The plan remains an audit row with an explicit reason.
        """

        with self.database.session() as session:
            plan_ids = list(
                session.scalars(
                    select(ReviewScheduleRecord.review_plan_id).where(
                        ReviewScheduleRecord.status.in_(("planned", "admitted"))
                    )
                )
            )
        for plan_id in plan_ids:
            with self.database.session() as session:
                session.execute(text("BEGIN IMMEDIATE"))
                # Discovery IDs and active_parent_ids do not authorize
                # retirement. Each short writer re-reads this plan's status,
                # latest Study basis, and actual child's lease/Host footprint.
                self._retire_in_session(session, now, plan_id=plan_id)

    def _retire_in_session(
        self, session: Session, now: datetime, *, plan_id: str | None = None
    ) -> None:
        query = session.query(ReviewScheduleRecord).filter(
            ReviewScheduleRecord.status.in_(("planned", "admitted"))
        )
        if plan_id is not None:
            query = query.filter(ReviewScheduleRecord.review_plan_id == plan_id)
        plans = query.all()
        bases: dict[str, TrackingBasis | None] = {}
        for plan in plans:
            parent = session.get(RunRecord, plan.parent_run_id)
            study = (
                session.get(StudyRecord, parent.study_id) if parent and parent.study_id else None
            )
            scoped = study is not None and research_scope_enabled(study)
            if study is not None and parent is not None and not scoped:
                try:
                    require_run_research_scope_in_session(session, parent)
                    scoped = True
                except ValueError:
                    scoped = False
            if not scoped:
                reason, basis = "study_scope_not_enabled", None
            elif study.status != "active":
                reason, basis = "study_tracking_paused", None
            else:
                if study.study_id not in bases:
                    bases[study.study_id] = tracking_basis_in_session(
                        session, study_id=study.study_id, now=now,
                        confirmed_report_reader=self.confirmed_report_reader,
                    )
                basis = bases[study.study_id]
                if basis is not None and basis.run.run_id == plan.parent_run_id:
                    continue
                reason = (
                    "superseded_by_newer_tracking_basis" if basis else "tracking_basis_unavailable"
                )
            child = session.get(RunRecord, plan.child_run_id) if plan.child_run_id else None
            if child is not None and (
                child.status == "running"
                or child.lease_owner is not None
                or session.get(DshSessionLinkRecord, child.run_id) is not None
            ):
                continue  # Do not cancel dispatched, pending, or unknown Host work.
            replacement = None
            if basis is not None:
                replacement = (
                    session.query(ReviewScheduleRecord)
                    .filter_by(
                        parent_run_id=basis.run.run_id,
                        horizon=plan.horizon,
                        trigger_type="next_review",
                    )
                    .order_by(ReviewScheduleRecord.created_at.asc())
                    .first()
                )
            old_at = as_utc(cast(datetime | None, plan.trigger_at))
            if (
                basis is not None
                and replacement is None
                and plan.status == "planned"
                and old_at is not None
                and old_at > now
            ):
                reason = "review_basis_replacement_unavailable"
                if plan.failure_code != reason:
                    plan.failure_code, plan.updated_at = reason, now
                    self._audit_in_session(
                        session,
                        plan.parent_run_id,
                        "research.review_basis.transfer_deferred",
                        {
                            "old_parent_run_id": plan.parent_run_id,
                            "old_review_plan_id": plan.review_plan_id,
                            "new_parent_run_id": basis.run.run_id,
                            "new_review_plan_id": None,
                            "horizon": plan.horizon,
                            "reason_code": reason,
                        },
                        now,
                    )
                continue
            if child is not None and child.status == "admitted":
                child.status = "cancelled"
                child.error_code = "scheduled_recheck_superseded" if basis else reason
                child.finished_at = now
                child.updated_at = now
            plan.status, plan.failure_code, plan.updated_at = "skipped", reason, now
            self._audit_in_session(
                session,
                plan.parent_run_id,
                "research.review_basis.transferred",
                {
                    "old_parent_run_id": plan.parent_run_id,
                    "old_review_plan_id": plan.review_plan_id,
                    "new_parent_run_id": basis.run.run_id if basis else None,
                    "new_review_plan_id": replacement.review_plan_id if replacement else None,
                    "horizon": plan.horizon,
                    "reason_code": reason,
                    "authority_request_id": basis.command_request_id if basis else None,
                },
                now,
            )

    def _discover_macro_plans(
        self,
        *,
        parent_run_id: str,
        horizon: str,
        window_start: datetime,
        window_end: datetime,
        now: datetime,
    ) -> None:
        with self.database.session() as session:
            rows = (
                session.query(MacroEventRecord)
                .filter(
                    MacroEventRecord.scheduled_at >= window_start,
                    MacroEventRecord.scheduled_at <= window_end,
                    MacroEventRecord.time_precision == "exact",
                    MacroEventRecord.invalidated_at.is_(None),
                    MacroEventRecord.status != "cancelled",
                )
                .all()
            )
        for event in rows:
            status = event.status if event.status in {"released", "revised"} else "scheduled"
            trigger_at = event.updated_at if status != "scheduled" else event.scheduled_at
            self._ensure_plan(
                parent_run_id=parent_run_id,
                horizon=horizon,
                trigger_type="macro_event",
                trigger_ref=f"{event.macro_event_id}:{status}",
                trigger_at=trigger_at,
                now=now,
            )

    def _discover_outcome_plan(self, *, parent: RunRecord, horizon: str, now: datetime) -> None:
        if not parent.artifact_id:
            return
        with self.database.session() as session:
            row = (
                session.query(EvaluationRecord)
                .join(ForecastRecord, ForecastRecord.forecast_id == EvaluationRecord.forecast_id)
                .filter(
                    ForecastRecord.artifact_id == parent.artifact_id,
                    ForecastRecord.horizon == horizon,
                )
                .order_by(EvaluationRecord.evaluated_at.desc())
                .first()
            )
        if row is None:
            return
        self._ensure_plan(
            parent_run_id=parent.run_id,
            horizon=horizon,
            trigger_type="outcome_due",
            trigger_ref=row.evaluation_id,
            trigger_at=row.evaluated_at,
            now=now,
        )

    def _discover_invalidation_plans(
        self,
        *,
        parent_run_id: str,
        horizon: str,
        invalidation: str,
        since: datetime,
        now: datetime,
    ) -> None:
        """Consume explicit fact metadata instead of guessing from free text."""

        with self.database.session() as session:
            parent = session.get(RunRecord, parent_run_id)
            if parent is None:
                return
            scoped_runs = (
                select(RunRecord.run_id).where(RunRecord.study_id == parent.study_id)
                if parent.study_id
                else select(RunRecord.run_id).where(RunRecord.event_id == parent.event_id)
            )
            facts = (
                session.query(ResearchFactRecord)
                .filter(
                    ResearchFactRecord.accepted_at > since,
                    ResearchFactRecord.quality == "accepted",
                    ResearchFactRecord.run_id.in_(scoped_runs),
                    ResearchFactRecord.accepted_at <= now,
                )
                .order_by(ResearchFactRecord.accepted_at.asc())
                .all()
            )
        accepted_tokens = {horizon, f"horizon:{horizon}", invalidation, "all"}
        for fact in facts:
            try:
                attributes = json.loads(fact.attributes_json)
            except (TypeError, json.JSONDecodeError):
                continue
            triggers = (
                attributes.get("triggered_invalidations", [])
                if isinstance(attributes, dict)
                else []
            )
            if not isinstance(triggers, list) or not accepted_tokens.intersection(
                str(item) for item in triggers
            ):
                continue
            self._ensure_plan(
                parent_run_id=parent_run_id,
                horizon=horizon,
                trigger_type="invalidation_evidence",
                trigger_ref=fact.fact_id,
                trigger_at=fact.accepted_at,
                now=now,
            )

    def _ensure_plan(
        self,
        *,
        parent_run_id: str,
        horizon: str,
        trigger_type: str,
        trigger_ref: str,
        trigger_at: datetime,
        now: datetime,
        session: Session | None = None,
    ) -> None:
        normalized_at = as_utc(cast(datetime | None, trigger_at))
        if normalized_at is None:
            raise ValueError("review_schedule_trigger_missing")
        canonical = (
            f"{parent_run_id}|{horizon}|{trigger_type}|{trigger_ref}|{normalized_at.isoformat()}"
        )
        trigger_hash = hashlib.sha256(canonical.encode()).hexdigest()
        with self.database.session() if session is None else nullcontext(session) as session:
            # Discovery may run in more than one process. A read before insert
            # races on trigger_hash and can terminate a worker on SQLite.
            statement = sqlite_insert(ReviewScheduleRecord).values(
                review_plan_id=f"review_plan_{uuid.uuid4().hex}",
                parent_run_id=parent_run_id,
                horizon=horizon,
                trigger_type=trigger_type,
                trigger_ref=trigger_ref,
                trigger_at=normalized_at,
                trigger_hash=trigger_hash,
                status="planned",
                created_at=now,
                updated_at=now,
            )
            session.execute(statement.on_conflict_do_nothing(index_elements=["trigger_hash"]))

    def _admit_due(self, now: datetime) -> tuple[ScheduledRecheckPlan, ...]:
        with self.database.session() as session:
            plan_ids = [
                row.review_plan_id
                for row in session.query(ReviewScheduleRecord)
                .filter(
                    ReviewScheduleRecord.status == "planned",
                    ReviewScheduleRecord.trigger_at <= now,
                )
                .order_by(ReviewScheduleRecord.trigger_at.asc())
                .all()
            ]
        admitted: list[ScheduledRecheckPlan] = []
        for plan_id in plan_ids:
            with self.database.session() as session:
                session.execute(text("BEGIN IMMEDIATE"))
                plan = session.get(ReviewScheduleRecord, plan_id)
                parent = session.get(RunRecord, plan.parent_run_id) if plan else None
                if plan is None or parent is None or plan.status != "planned":
                    continue
                study_id = parent.study_id
                basis = (
                    tracking_basis_in_session(
                        session, study_id=study_id, now=now,
                        confirmed_report_reader=self.confirmed_report_reader,
                    )
                    if study_id
                    else None
                )
                if basis is None or study_id is None or basis.run.run_id != parent.run_id:
                    plan.status = "skipped"
                    plan.failure_code = (
                        "tracking_basis_unavailable"
                        if basis is None
                        else "superseded_by_newer_tracking_basis"
                    )
                    plan.updated_at = now
                    self._audit_in_session(
                        session,
                        parent.run_id,
                        "research.scheduled_recheck.skipped",
                        {
                            "review_plan_id": plan.review_plan_id,
                            "reason_code": plan.failure_code,
                        },
                        now,
                    )
                    continue
                saved_horizon = next(
                    (item for item in basis.result.horizons if item.horizon == plan.horizon), None
                )
                if saved_horizon is None or (
                    plan.trigger_type != "outcome_due" and now >= saved_horizon.expires_at
                ):
                    plan.status, plan.failure_code, plan.updated_at = (
                        "skipped",
                        "research_review_window_expired",
                        now,
                    )
                    self._audit_in_session(
                        session,
                        parent.run_id,
                        "research.scheduled_recheck.skipped",
                        {
                            "review_plan_id": plan.review_plan_id,
                            "reason_code": plan.failure_code,
                        },
                        now,
                    )
                    continue
                if self._trigger_consumed_in_session(session, plan, parent):
                    plan.status, plan.failure_code, plan.updated_at = (
                        "skipped",
                        "review_trigger_already_consumed",
                        now,
                    )
                    self._audit_in_session(
                        session,
                        parent.run_id,
                        "research.scheduled_recheck.skipped",
                        {
                            "review_plan_id": plan.review_plan_id,
                            "reason_code": plan.failure_code,
                        },
                        now,
                    )
                    continue
                if plan.trigger_ref.startswith("retry:v1:"):
                    candidates = review_recovery_candidates_in_session(
                        session, basis_run_id=parent.run_id, study_id=study_id, now=now
                    )
                    candidate = next(
                        (item for item in candidates if item.trigger_ref == plan.trigger_ref), None
                    )
                    reason = (
                        "review_recovery_proof_unavailable"
                        if candidate is None
                        else validate_review_recovery_in_session(session, candidate, now=now)
                    )
                    if reason is not None:
                        plan.status, plan.failure_code, plan.updated_at = "skipped", reason, now
                        self._audit_in_session(
                            session,
                            parent.run_id,
                            "research.scheduled_recheck.skipped",
                            {
                                "review_plan_id": plan.review_plan_id,
                                "reason_code": reason,
                            },
                            now,
                        )
                        continue
                guard = research_admission_guard_in_session(
                    session, study_id=study_id, now=now, admission_origin="scheduled_recheck"
                )
                if guard.reason_code is not None:
                    policy_version = guard.details.get("policy_version")
                    policy_recorded = (
                        session.query(RunEventRecord.id)
                        .filter(
                            RunEventRecord.run_id == parent.run_id,
                            RunEventRecord.event_type == "research.scheduled_recheck.deferred",
                            func.json_extract(RunEventRecord.payload_json, "$.review_plan_id")
                            == plan.review_plan_id,
                            func.json_extract(RunEventRecord.payload_json, "$.policy_version")
                            == policy_version,
                        )
                        .first()
                        is not None
                    )
                    if plan.failure_code != guard.reason_code or not policy_recorded:
                        plan.failure_code, plan.updated_at = guard.reason_code, now
                        self._audit_in_session(
                            session,
                            parent.run_id,
                            "research.scheduled_recheck.deferred",
                            {
                                "review_plan_id": plan.review_plan_id,
                                "reason_code": guard.reason_code,
                                **guard.details,
                            },
                            now,
                        )
                    continue
                parent_run_id = parent.run_id
                event_id = parent.event_id
                horizon = plan.horizon
                trigger_type = plan.trigger_type
                trigger_at = plan.trigger_at
                idempotency_key = f"research-review-plan:{plan_id}"
                try:
                    with session.begin_nested():
                        child_id, _created = self.runs.create(
                            event_id,
                            idempotency_key=idempotency_key,
                            strategy_version="research.v1",
                            available_at=now,
                            parent_run_id=parent_run_id,
                            admission_origin="scheduled_recheck",
                            session=session,
                        )
                except (KeyError, ValueError) as exc:
                    waiting = str(exc) in {
                        "spot_current_admission_not_prepared", "spot_current_admission_unavailable",
                        "spot_effective_host_unavailable", "spot_effective_host_not_ready",
                        "spot_admission_expired", "spot_current_original_path_not_fresh",
                    }
                    plan.status = (
                        "planned" if waiting else
                        "skipped" if str(exc) == "study_not_active" else "failed"
                    )
                    plan.failure_code = str(exc)[:128]
                    plan.updated_at = now
                    continue
                plan.status = "admitted"
                plan.child_run_id = child_id
                plan.failure_code = None
                plan.updated_at = now
                self._record_parent_event(
                    parent_run_id,
                    plan_id=plan_id,
                    horizon=horizon,
                    child_run_id=child_id,
                    trigger_type=trigger_type,
                    trigger_at=trigger_at,
                    idempotency_key=idempotency_key,
                    now=now,
                    session=session,
                )
            admitted.append(
                ScheduledRecheckPlan.model_validate(
                    {
                        "review_plan_id": plan_id,
                        "parent_run_id": parent_run_id,
                        "child_run_id": child_id,
                        "event_id": event_id,
                        "horizon": horizon,
                        "trigger_type": trigger_type,
                        "trigger_at": trigger_at,
                        "idempotency_key": idempotency_key,
                    }
                )
            )
        return tuple(admitted)

    def _trigger_consumed_in_session(
        self, session: Session, plan: ReviewScheduleRecord, parent: RunRecord
    ) -> bool:
        if plan.trigger_type == "next_review":
            return False
        others = (
            session.query(ReviewScheduleRecord)
            .join(RunRecord, RunRecord.run_id == ReviewScheduleRecord.parent_run_id)
            .filter(
                RunRecord.study_id == parent.study_id,
                ReviewScheduleRecord.review_plan_id != plan.review_plan_id,
                ReviewScheduleRecord.horizon == plan.horizon,
                ReviewScheduleRecord.trigger_type == plan.trigger_type,
                ReviewScheduleRecord.trigger_ref == plan.trigger_ref,
            )
            .all()
        )
        for other in others:
            child = session.get(RunRecord, other.child_run_id) if other.child_run_id else None
            if other.status == "completed" or (
                child is not None
                and (
                    child.status in {"admitted", "running", "completed", "degraded"}
                    or host_work_unsettled_in_session(session, child.run_id)
                )
            ):
                return True
        return False

    def _sync_child_statuses(self, now: datetime) -> None:
        with self.database.session() as session:
            rows = (
                session.query(ReviewScheduleRecord, RunRecord)
                .join(RunRecord, RunRecord.run_id == ReviewScheduleRecord.child_run_id)
                .filter(ReviewScheduleRecord.status == "admitted")
                .all()
            )
            for plan, child in rows:
                status = self.CHILD_TERMINAL.get(child.status)
                if status is not None:
                    plan.status = status
                    plan.failure_code = child.error_code if status == "failed" else None
                    plan.updated_at = now

    def _record_parent_event(
        self,
        parent_run_id: str,
        *,
        plan_id: str,
        horizon: str,
        child_run_id: str,
        trigger_type: str,
        trigger_at: datetime,
        idempotency_key: str,
        now: datetime,
        session: Session | None = None,
    ) -> None:
        normalized_at = as_utc(cast(datetime | None, trigger_at))
        if normalized_at is None:
            raise ValueError("review_schedule_trigger_missing")
        with self.database.session() if session is None else nullcontext(session) as session:
            if cast(Session | None, session) is None:  # pragma: no cover
                raise ValueError("review_schedule_session_missing")
            self._audit_in_session(
                session,
                parent_run_id,
                "research.scheduled_recheck.admitted",
                {
                    "review_plan_id": plan_id,
                    "child_run_id": child_run_id,
                    "horizon": horizon,
                    "trigger_type": trigger_type,
                    "trigger_at": normalized_at.isoformat(),
                    "idempotency_key": idempotency_key,
                },
                now,
            )

    @staticmethod
    def _audit_in_session(
        session: Session, run_id: str, event_type: str, payload: dict[str, object], now: datetime
    ) -> None:
        latest = (
            session.query(RunEventRecord)
            .filter_by(run_id=run_id)
            .order_by(RunEventRecord.sequence_no.desc())
            .first()
        )
        session.add(
            RunEventRecord(
                run_id=run_id,
                sequence_no=(latest.sequence_no if latest else 0) + 1,
                event_type=event_type,
                occurred_at=now,
                payload_json=json.dumps(payload, ensure_ascii=False, sort_keys=True),
            )
        )
        session.flush()


def _trigger_token(value: datetime) -> str:
    normalized = as_utc(cast(datetime | None, value))
    if normalized is None:
        raise ValueError("review_schedule_trigger_missing")
    return normalized.isoformat().replace("+00:00", "Z").replace(":", "").replace("-", "")

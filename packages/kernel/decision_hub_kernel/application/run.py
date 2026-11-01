from __future__ import annotations

import uuid
from collections.abc import Callable
from contextlib import nullcontext
from datetime import datetime, timedelta
from typing import Any, cast

from pydantic import BaseModel, ConfigDict
from sqlalchemy import and_, or_, text, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from packages.kernel.decision_hub_kernel.application.market_scope import require_research_scope
from packages.kernel.decision_hub_kernel.application.research_run_scope import (
    SpotAdmissionResolver,
    freeze_run_spot_admission_in_session,
    require_run_research_scope_in_session,
)
from packages.kernel.decision_hub_kernel.application.study import (
    bind_run_study,
    ensure_occurrence,
    study_for_event,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    DshSessionLinkRecord,
    RunRecord,
    RunStatus,
    StudyOccurrenceRecord,
    StudyRecord,
    utcnow,
)

RUN_ADMISSION_PRIORITIES = {
    "legacy": 0,
    "scheduled_recheck": 10,
    "automatic": 50,
    "manual": 100,
}


class RunClaim(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str
    run_id: str
    strategy_version: str
    worker_id: str
    lease_expires_at: datetime
    recovered: bool = False


class RunService:
    def __init__(
        self,
        database: Database,
        *,
        clock: Callable[[], datetime] = utcnow,
        spot_admission_resolver: SpotAdmissionResolver | None = None,
        perpetual_admission_resolver: Callable[[Session, StudyRecord, datetime], Any] | None = None,
    ) -> None:
        self.database = database
        self.clock = clock
        self.spot_admission_resolver = spot_admission_resolver
        self.perpetual_admission_resolver = perpetual_admission_resolver

    def create(
        self,
        event_id: str,
        idempotency_key: str | None = None,
        *,
        strategy_version: str = "baseline.v1",
        available_at: datetime | None = None,
        parent_run_id: str | None = None,
        admission_origin: str = "manual",
        priority: int | None = None,
        study_id: str | None = None,
        session: Session | None = None,
    ) -> tuple[str, bool]:
        expected_priority = RUN_ADMISSION_PRIORITIES.get(admission_origin)
        if expected_priority is None:
            raise ValueError("run_admission_origin_invalid")
        if priority is not None and priority != expected_priority:
            raise ValueError("run_priority_origin_mismatch")
        effective_priority = expected_priority
        run_id = f"run_{uuid.uuid4().hex}"
        now = self.clock()
        if available_at is not None and available_at.tzinfo is None:
            raise ValueError("run_available_at_timezone_required")
        owns_transaction = session is None
        with self.database.session() if session is None else nullcontext(session) as session:
            if owns_transaction and (
                admission_origin != "manual" or strategy_version == "research.v1"
            ):
                session.execute(text("BEGIN IMMEDIATE"))
            if idempotency_key:
                existing = (
                    session.query(RunRecord).filter_by(idempotency_key=idempotency_key).first()
                )
                if existing:
                    if (
                        existing.event_id != event_id
                        or existing.strategy_version != strategy_version
                        or (study_id is not None and existing.study_id != study_id)
                    ):
                        raise ValueError("run_idempotency_scope_mismatch")
                    if strategy_version == "research.v1":
                        require_run_research_scope_in_session(session, existing)
                    return existing.run_id, False
            if parent_run_id is not None:
                parent = session.get(RunRecord, parent_run_id)
                if parent is None:
                    raise ValueError("parent_run_not_found")
                if parent.event_id != event_id:
                    raise ValueError("parent_run_event_mismatch")
                parent_study = bind_run_study(session, parent, now, legacy=not parent.study_id)
                if study_id is not None and study_id != parent_study:
                    raise ValueError("parent_study_mismatch")
                study_id = parent_study
            if study_id is None:
                study_id = study_for_event(session, event_id, now)
            study = session.get(StudyRecord, study_id)
            if study is None:
                raise ValueError("study_not_found")
            current_spot = None
            current_perpetual = None
            if strategy_version == "research.v1":
                if (self.perpetual_admission_resolver is not None
                    and study.market == "crypto" and study.instrument in {"BTC-USDT-SWAP", "ETH-USDT-SWAP"}
                    and study.horizon_scope in {"4h", "24h", "72h"} and study.identity_basis == "exact_perpetual_decision_v2"):
                    from .perpetual_research_entry import authorize_perpetual_new_run_in_session
                    current_perpetual = self.perpetual_admission_resolver(session, study, now)
                    run_id = authorize_perpetual_new_run_in_session(session, study, current_perpetual, at=now)
                elif (
                    study.market == "crypto"
                    and study.instrument in {"BTC-USDT", "ETH-USDT"}
                    and self.spot_admission_resolver is not None
                ):
                    current_spot = self.spot_admission_resolver(session, study, now)
                else:
                    require_research_scope(study)
            if study.status != "active" and admission_origin != "manual":
                raise ValueError("study_not_active")
            occurrence_id = ensure_occurrence(session, event_id, now)
            session.execute(
                insert(StudyOccurrenceRecord)
                .values(
                    study_id=study_id,
                    occurrence_id=occurrence_id,
                    linked_at=now,
                )
                .on_conflict_do_nothing()
            )
            inserted = session.execute(
                insert(RunRecord)
                .values(
                    run_id=run_id,
                    event_id=event_id,
                    study_id=study_id,
                    idempotency_key=idempotency_key,
                    strategy_version=strategy_version,
                    runtime_version="pending" if strategy_version == "research.v1" else "fake.v1",
                    available_at=available_at,
                    parent_run_id=parent_run_id,
                    admission_origin=admission_origin,
                    priority=effective_priority,
                    created_at=now,
                    updated_at=now,
                )
                .on_conflict_do_nothing(index_elements=["idempotency_key"])
            )
            if int(getattr(inserted, "rowcount", 0)) != 1:
                existing = session.query(RunRecord).filter_by(idempotency_key=idempotency_key).one()
                if (
                    existing.event_id != event_id
                    or existing.strategy_version != strategy_version
                    or existing.study_id != study_id
                ):
                    raise ValueError("run_idempotency_scope_mismatch")
                if strategy_version == "research.v1":
                    require_run_research_scope_in_session(session, existing)
                return existing.run_id, False
            if current_spot is not None:
                created_run = session.get(RunRecord, run_id)
                assert created_run is not None
                freeze_run_spot_admission_in_session(
                    session, run=created_run, study=study, current=current_spot
                )
            if current_perpetual is not None:
                from .perpetual_research_entry import install_perpetual_new_run_in_session
                created_run = session.get(RunRecord, run_id, populate_existing=True)
                assert created_run is not None
                install_perpetual_new_run_in_session(session, created_run, study, current_perpetual, at=now)
        return run_id, True

    def for_event(self, event_id: str, *, strategy_version: str | None = None) -> str | None:
        with self.database.session() as session:
            query = session.query(RunRecord).filter_by(event_id=event_id)
            if strategy_version is not None:
                query = query.filter_by(strategy_version=strategy_version)
            existing = query.order_by(RunRecord.created_at.desc()).first()
            return existing.run_id if existing else None

    def claim_for_execution(
        self,
        run_id: str,
        *,
        worker_id: str | None = None,
        lease_seconds: int | None = None,
        strategy_version: str | None = None,
    ) -> bool:
        """Claim an admitted Run, or reclaim an expired durable lease.

        The no-worker form preserves the synchronous R0/R1 compatibility path;
        durable workers must provide both identity and a positive lease.
        """
        if (worker_id is None) != (lease_seconds is None):
            raise ValueError("run_lease_configuration_required")
        if lease_seconds is not None and lease_seconds <= 0:
            raise ValueError("run_lease_invalid")
        now = self.clock()
        predicates = [
            RunRecord.run_id == run_id,
            RunRecord.status == RunStatus.admitted.value,
            or_(RunRecord.available_at.is_(None), RunRecord.available_at <= now),
        ]
        if strategy_version is not None:
            predicates.append(RunRecord.strategy_version == strategy_version)
        values: dict[str, object] = {"status": RunStatus.running.value, "updated_at": now}
        if worker_id is not None and lease_seconds is not None:
            predicates[1] = or_(
                RunRecord.status == RunStatus.admitted.value,
                and_(
                    RunRecord.status == RunStatus.running.value,
                    RunRecord.lease_expires_at <= now,
                ),
            )
            values.update(
                lease_owner=worker_id,
                lease_expires_at=now + timedelta(seconds=lease_seconds),
            )
        with self.database.session() as session:
            result = session.execute(update(RunRecord).where(*predicates).values(**values))
            claimed = cast(Any, result).rowcount
        return claimed == 1

    def claim_next(
        self,
        *,
        strategy_version: str,
        worker_id: str,
        lease_seconds: int,
        min_priority: int | None = None,
    ) -> RunClaim | None:
        if not strategy_version:
            raise ValueError("run_strategy_required")
        if not worker_id:
            raise ValueError("run_worker_identity_required")
        if lease_seconds <= 0:
            raise ValueError("run_lease_invalid")
        now = self.clock()
        expires = now + timedelta(seconds=lease_seconds)
        priority_filter = () if min_priority is None else (RunRecord.priority >= min_priority,)
        with self.database.session() as session:
            candidates = (
                session.query(RunRecord)
                .filter(
                    RunRecord.strategy_version == strategy_version,
                    *priority_filter,
                    or_(RunRecord.available_at.is_(None), RunRecord.available_at <= now),
                    or_(
                        RunRecord.status == RunStatus.admitted.value,
                        and_(
                            RunRecord.status == RunStatus.running.value,
                            RunRecord.lease_expires_at <= now,
                        ),
                    ),
                )
                .order_by(
                    RunRecord.priority.desc(),
                    RunRecord.created_at.asc(),
                    RunRecord.run_id.asc(),
                )
                .limit(10)
                .all()
            )
            for candidate in candidates:
                was_running = candidate.status == RunStatus.running.value
                changed = session.execute(
                    update(RunRecord)
                    .where(
                        RunRecord.run_id == candidate.run_id,
                        RunRecord.strategy_version == strategy_version,
                        *priority_filter,
                        or_(
                            RunRecord.status == RunStatus.admitted.value,
                            and_(
                                RunRecord.status == RunStatus.running.value,
                                RunRecord.lease_expires_at <= now,
                            ),
                        ),
                    )
                    .values(
                        status=RunStatus.running.value,
                        lease_owner=worker_id,
                        lease_expires_at=expires,
                        updated_at=now,
                    )
                    .execution_options(synchronize_session=False)
                )
                if int(getattr(changed, "rowcount", 0)) == 1:
                    return RunClaim(
                        event_id=candidate.event_id,
                        run_id=candidate.run_id,
                        strategy_version=strategy_version,
                        worker_id=worker_id,
                        lease_expires_at=expires,
                        recovered=was_running,
                    )
        return None

    def renew_lease(self, run_id: str, worker_id: str, *, lease_seconds: int) -> bool:
        if not worker_id:
            raise ValueError("run_worker_identity_required")
        if lease_seconds <= 0:
            raise ValueError("run_lease_invalid")
        now = self.clock()
        with self.database.session() as session:
            changed = session.execute(
                update(RunRecord)
                .where(
                    RunRecord.run_id == run_id,
                    RunRecord.status == RunStatus.running.value,
                    RunRecord.lease_owner == worker_id,
                    or_(RunRecord.lease_expires_at.is_(None), RunRecord.lease_expires_at > now),
                )
                .values(lease_expires_at=now + timedelta(seconds=lease_seconds), updated_at=now)
            )
            return int(getattr(changed, "rowcount", 0)) == 1

    def admitted_targets(
        self, *, strategy_version: str | None = None, recover_expired: bool = False
    ) -> list[tuple[str, str]]:
        """Return durable event/run pairs that still need their first execution.

        A strategy filter keeps independent worker roles from claiming each
        other's admitted runs. The unfiltered form preserves the R0/R1
        scheduler behavior.
        """
        with self.database.session() as session:
            eligible = RunRecord.status == RunStatus.admitted.value
            if recover_expired:
                eligible = or_(
                    eligible,
                    and_(
                        RunRecord.status == RunStatus.running.value,
                        RunRecord.lease_expires_at <= self.clock(),
                    ),
                )
            query = session.query(RunRecord).filter(eligible)
            query = query.filter(
                or_(RunRecord.available_at.is_(None), RunRecord.available_at <= self.clock())
            )
            if strategy_version is not None:
                query = query.filter(RunRecord.strategy_version == strategy_version)
            rows = query.order_by(
                RunRecord.priority.desc(),
                RunRecord.created_at.asc(),
                RunRecord.run_id.asc(),
            ).all()
            return [(row.event_id, row.run_id) for row in rows]

    def fail_if_owned(
        self,
        run_id: str,
        worker_id: str | None,
        *,
        error_code: str,
        require_current_lease: bool = False,
    ) -> bool:
        now = self.clock()
        lease_guard = (RunRecord.lease_expires_at > now,) if require_current_lease else ()
        with self.database.session() as session:
            changed = session.execute(
                update(RunRecord)
                .where(
                    RunRecord.run_id == run_id,
                    RunRecord.lease_owner == worker_id,
                    RunRecord.status == RunStatus.running.value,
                    RunRecord.artifact_id.is_(None),
                    *lease_guard,
                )
                .values(
                    status=RunStatus.failed.value,
                    error_code=error_code,
                    updated_at=now,
                    finished_at=now,
                    lease_owner=None,
                    lease_expires_at=None,
                )
            )
            if int(getattr(changed, "rowcount", 0)) != 1:
                return False
            # The Worker owns the Run terminal CAS.  If native execution has
            # not already reached a terminal state, close its durable Link in
            # the same transaction so a failed Run cannot remain "running".
            # An authenticated native terminal is immutable and is therefore
            # never rewritten by a later product/mapping failure.
            session.execute(
                update(DshSessionLinkRecord)
                .where(
                    DshSessionLinkRecord.run_id == run_id,
                    DshSessionLinkRecord.terminal_at.is_(None),
                    DshSessionLinkRecord.state.not_in(
                        ("completed", "failed", "cancelled")
                    ),
                )
                .values(
                    state="failed",
                    error_code=error_code,
                    terminal_at=now,
                    last_seen_at=now,
                    updated_at=now,
                )
            )
            return True

    def set_status(self, run_id: str, status: RunStatus, *, error_code: str | None = None) -> None:
        with self.database.session() as session:
            run = session.get(RunRecord, run_id)
            if not run:
                raise KeyError(run_id)
            run.status = status.value
            run.updated_at = self.clock()
            run.error_code = error_code
            if status in {
                RunStatus.completed,
                RunStatus.degraded,
                RunStatus.failed,
                RunStatus.cancelled,
            }:
                run.finished_at = run.updated_at
                run.lease_owner = None
                run.lease_expires_at = None

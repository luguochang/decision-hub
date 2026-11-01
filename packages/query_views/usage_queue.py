"""Small SQL aggregates for PC operations; never scans model archives."""
from datetime import timedelta
from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field
from sqlalchemy import func

from packages.kernel.decision_hub_kernel.persistence.db import RunRecord, as_utc
from packages.kernel.decision_hub_kernel.persistence.db import (
    UsageCollectionRecord as Job,
)


class UsageQueueSummary(BaseModel):
    schema_version: Literal["usage-queue-summary.v1"] = "usage-queue-summary.v1"
    observed_at: AwareDatetime
    session_versions: int = Field(ge=0)
    unique_runs: int = Field(ge=0)
    statuses: dict[str, int]
    due: int = Field(ge=0)
    overdue_10m: int = Field(ge=0)
    checking: int = Field(ge=0)
    oldest_due_at: AwareDatetime | None
    latest_check_at: AwareDatetime | None


UsageQueueFilter = Literal[
    "all", "pending", "captured", "partial", "waiting_archive", "retry_wait",
    "overdue", "checking",
]


class UsageQueueItem(BaseModel):
    job_id: str
    run_id: str
    run_created_at: AwareDatetime | None
    run_available: bool
    generation: int
    status: str
    checked_at: AwareDatetime | None
    next_check_at: AwareDatetime
    checking: bool
    attempts: int
    error_code: str | None


class UsageQueuePage(BaseModel):
    schema_version: Literal["usage-queue-page.v1"] = "usage-queue-page.v1"
    observed_at: AwareDatetime
    filter: UsageQueueFilter
    offset: int
    limit: int
    total: int
    has_more: bool
    items: list[UsageQueueItem]


def usage_queue_page(database, now, *, filter="all", offset=0, limit=20):
    """Bounded DB-only navigation; never reads archives or schedules work."""
    with database.session() as session:
        query = session.query(Job, RunRecord).outerjoin(RunRecord, Job.run_id == RunRecord.run_id)
        if filter == "overdue":
            query = query.filter(Job.next_check_at < now - timedelta(minutes=10))
        elif filter == "checking":
            query = query.filter(Job.lease_until > now)
        elif filter != "all":
            query = query.filter(Job.status == filter)
        total = query.count()
        offset = min(offset, ((total - 1) // limit) * limit) if total else 0
        rows = query.order_by(Job.created_at.desc(), Job.job_id).offset(offset).limit(limit).all()
        items = [UsageQueueItem(
            job_id=job.job_id, run_id=job.run_id,
            run_created_at=as_utc(run.created_at) if run else None, run_available=run is not None,
            generation=job.generation, status=job.status, checked_at=as_utc(job.checked_at),
            next_check_at=as_utc(job.next_check_at),
            checking=bool(job.lease_until and as_utc(job.lease_until) > as_utc(now)),
            attempts=job.attempts, error_code=job.error_code,
        ) for job, run in rows]
    return UsageQueuePage(observed_at=now, filter=filter, offset=offset, limit=limit,
                          total=total, has_more=offset + len(items) < total, items=items)


def usage_queue_summary(database, now):
    with database.session() as session:
        total, runs, latest = session.query(
            func.count(Job.job_id), func.count(func.distinct(Job.run_id)), func.max(Job.checked_at)
        ).one()
        statuses = dict(
            session.query(Job.status, func.count(Job.job_id)).group_by(Job.status).all()
        )
        due, oldest = session.query(func.count(Job.job_id), func.min(Job.next_check_at)).filter(
            Job.next_check_at <= now
        ).one()
        overdue = session.query(Job).filter(Job.next_check_at < now - timedelta(minutes=10)).count()
        checking = session.query(Job).filter(Job.lease_until > now).count()
    return UsageQueueSummary(
        observed_at=now, session_versions=total, unique_runs=runs, statuses=statuses,
        due=due, overdue_10m=overdue, checking=checking, oldest_due_at=as_utc(oldest),
        latest_check_at=as_utc(latest),
    )

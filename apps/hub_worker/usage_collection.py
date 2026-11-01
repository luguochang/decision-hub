"""Retryable usage collection independent of research execution and report commits."""

import asyncio
import hashlib
import logging
import time
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import case, exists, or_, select, update
from sqlalchemy.exc import IntegrityError

from packages.contracts_py.decision_hub_contracts.generated.research_product_view import (
    ResearchUsageView,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    DshSessionLinkRecord,
    DshSessionPromptRecord,
    ResearchUsageSnapshotRecord,
    RunRecord,
    UsageCollectionRecord,
    as_utc,
    utcnow,
)
from packages.query_views.research_usage import usage_for_session
from packages.workbench_adapters.usage_ledger import _canonical, capture_usage

log = logging.getLogger(__name__)
ACTIVE_RUN_STATES = {"admitted", "queued", "running", "researching", "retry_wait"}


def fingerprint(view):
    view = ResearchUsageView.model_validate(view).model_dump(mode="json", exclude_unset=True)
    content = {key: value for key, value in view.items() if key != "observed_at"}
    content["sessions"] = sorted(content["sessions"], key=lambda item: item["session_id"])
    return hashlib.sha256(_canonical(content).encode()).hexdigest()


class UsageCollector:
    def __init__(self, database, sessions_root, *, clock=utcnow):
        self.database = database
        self.sessions_root = sessions_root
        self.clock = clock
        self.owner = uuid4().hex

    def discover(self):
        now = self.clock()
        bindings = set()
        for model in (DshSessionLinkRecord, DshSessionPromptRecord):
            with self.database.session() as session:
                rows = session.execute(
                    select(model.run_id, model.dsh_session_id, model.generation)
                    .where(~exists().where(
                        UsageCollectionRecord.run_id == model.run_id,
                        UsageCollectionRecord.session_id == model.dsh_session_id,
                        UsageCollectionRecord.generation == model.generation,
                    ))
                    .order_by(model.created_at.desc()).limit(25)
                ).all()
                bindings.update(tuple(row) for row in rows)
        for run_id, sid, generation in sorted(bindings):
            job_id = "usagejob_" + hashlib.sha256(
                _canonical([run_id, sid, generation]).encode()
            ).hexdigest()
            try:
                with self.database.session() as session:
                    session.add(UsageCollectionRecord(
                        job_id=job_id, run_id=run_id, session_id=sid, generation=generation,
                        status="pending", next_check_at=now, attempts=0, created_at=now,
                    ))
            except IntegrityError:
                with self.database.session() as session:
                    if session.get(UsageCollectionRecord, job_id) is None:
                        raise

    def claim(self, *, prioritize=True):
        now = self.clock()
        available = or_(
            UsageCollectionRecord.lease_until.is_(None),
            UsageCollectionRecord.lease_until <= now,
        )
        with self.database.session() as session:
            query = (
                select(UsageCollectionRecord.job_id)
                .outerjoin(RunRecord, RunRecord.run_id == UsageCollectionRecord.run_id)
                .where(UsageCollectionRecord.next_check_at <= now, available)
            )
            if prioritize:
                query = query.order_by(
                    case(
                        (UsageCollectionRecord.checked_at.is_(None), 0),
                        (RunRecord.status.in_(ACTIVE_RUN_STATES), 1),
                        else_=2,
                    )
                )
            identity = session.scalar(
                query.order_by(UsageCollectionRecord.next_check_at, UsageCollectionRecord.job_id)
                .limit(1)
            )
            if identity is None:
                return None
            claimed = session.execute(
                update(UsageCollectionRecord)
                .where(
                    UsageCollectionRecord.job_id == identity,
                    UsageCollectionRecord.next_check_at <= now,
                    available,
                )
                .values(lease_owner=self.owner, lease_until=now + timedelta(minutes=5))
            )
            if not claimed.rowcount:
                return None
            row = session.get(UsageCollectionRecord, identity)
            row.request_id = row.request_id or uuid4().hex
            row.attempts += 1
            return {column.name: getattr(row, column.name) for column in row.__table__.columns}

    def collect(self, job):
        try:
            view = usage_for_session(self.sessions_root, job["run_id"], job["session_id"])
            digest = fingerprint(view)
            receipt_id = job["last_snapshot_id"]
            changed = digest != job["last_view_hash"] or not receipt_id
            if changed:
                receipt = capture_usage(
                    self.database, self.sessions_root, job["run_id"], job["request_id"],
                    binding=(job["session_id"], job["generation"]), prepared_view=view,
                )
                view = receipt.view.model_dump(mode="json", exclude_unset=True)
                digest, receipt_id = fingerprint(view), receipt.snapshot_id
            status = "waiting_archive" if view["status"] == "unavailable" else "captured"
            if any(item["status"] != "accounted" for item in view["sessions"]):
                status = "partial"
            if "some_archives_unreadable" in view["limitations"]:
                status = "partial"
            with self.database.session() as session:
                run = session.get(RunRecord, job["run_id"])
                snapshot = session.get(ResearchUsageSnapshotRecord, receipt_id)
                active = run is not None and run.status in ACTIVE_RUN_STATES
                # A saved fingerprint supplies a durable age across worker restarts.
                # No new metadata is written into an old receipt to track backoff.
                stable_age = max(0, (self.clock() - as_utc(snapshot.recorded_at)).total_seconds())
            if active:
                delay = 30
            elif changed:
                delay = 120  # Allow child archives and final usage to arrive after completion.
            else:
                delay = min(21600, max(120, int(stable_age / 2)))
            error = "usage_archive_unavailable" if status == "waiting_archive" else None
            if error and not active:
                delay = min(3600, 60 * 2 ** min(job["attempts"], 6))
            changes = dict(
                status=status, error_code=error, last_view_hash=digest,
                last_snapshot_id=receipt_id, request_id=None,
            )
        except Exception as exc:
            # Error details can contain paths or provider data. Persist only the type.
            changes = dict(status="retry_wait", error_code=f"usage_{type(exc).__name__}"[:64])
            delay = min(3600, 60 * 2 ** min(job["attempts"], 6))
        now = self.clock()
        with self.database.session() as session:
            session.execute(
                update(UsageCollectionRecord)
                .where(
                    UsageCollectionRecord.job_id == job["job_id"],
                    UsageCollectionRecord.lease_owner == self.owner,
                )
                .values(
                    **changes, checked_at=now, next_check_at=now + timedelta(seconds=delay),
                    lease_owner=None, lease_until=None,
                )
            )

    def tick(self, *, limit=16, time_budget_seconds=20):
        started = time.monotonic()
        self.discover()
        processed = 0
        for index in range(limit):
            if time.monotonic() - started >= time_budget_seconds:
                break
            # Reserve one in four slots for the oldest due job, even under a
            # stream of fresh Runs. Leases still arbitrate concurrent workers.
            job = self.claim(prioritize=index % 4 != 3)
            if job is None:
                break
            self.collect(job)
            processed += 1
        return processed

    async def run_forever(self, *, heavy_work_admission=None):
        while True:
            try:
                if heavy_work_admission is None:
                    await asyncio.to_thread(self.tick)
                else:
                    await heavy_work_admission.run_thread(self.tick)
            except Exception as exc:
                log.warning("Usage collection cycle failed: %s", type(exc).__name__)
            await asyncio.sleep(30)

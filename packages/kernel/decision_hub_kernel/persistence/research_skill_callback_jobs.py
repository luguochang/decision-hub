"""Dedicated durable callbacks; queue acceptance grants no skill permission."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import (
    DDL,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    event,
    func,
    select,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..application.research_skill_package import decoded, encoded, sha
from .db import Base, Database, DshSessionLinkRecord, RunEventRecord, RunRecord, as_utc, utcnow
from .original_preparation import begin_immediate


class ResearchSkillCallbackJobRecord(Base):
    __tablename__ = "research_skill_callback_jobs"
    __table_args__ = (
        CheckConstraint(
            "generation >= 1 AND attempt >= 0 AND max_attempts >= 1 AND lease_epoch >= 0 "
            "AND cancel_version >= 0",
            name="ck_skill_callback_counters",
        ),
        CheckConstraint("kind IN ('tool','records')", name="ck_skill_callback_kind"),
        CheckConstraint(
            "status IN ('queued','running','retry_wait','completed','rejected',"
            "'cancelled','expired','superseded')",
            name="ck_skill_callback_status",
        ),
        CheckConstraint(
            "status != 'running' OR (lease_owner IS NOT NULL AND lease_until IS NOT NULL)",
            name="ck_skill_callback_running_lease",
        ),
        CheckConstraint(
            "status != 'completed' OR (response_json IS NOT NULL "
            "AND response_hash IS NOT NULL AND completed_at IS NOT NULL)",
            name="ck_skill_callback_completed_response",
        ),
        Index("ix_skill_callback_due", "status", "next_attempt_at", "lease_until"),
        Index("ix_skill_callback_run_generation", "run_id", "generation"),
    )
    job_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    submission_key: Mapped[str] = mapped_column(String(64), unique=True)
    semantic_operation_key: Mapped[str] = mapped_column(String(64), index=True)
    protocol_version: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(16))
    run_id: Mapped[str] = mapped_column(String(128))
    generation: Mapped[int] = mapped_column(Integer)
    root_session_id: Mapped[str] = mapped_column(String(256))
    execution_session_id: Mapped[str] = mapped_column(String(256))
    request_hash: Mapped[str] = mapped_column(String(64))
    proof_request_hash: Mapped[str] = mapped_column(String(64))
    original_occurrence_json: Mapped[str] = mapped_column(Text)
    stage_request_id: Mapped[str] = mapped_column(String(128))
    operation: Mapped[str] = mapped_column(String(32))
    request_json: Mapped[str] = mapped_column(Text)
    producer_identity: Mapped[str] = mapped_column(String(64))
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16))
    phase: Mapped[str] = mapped_column(String(32))
    attempt: Mapped[int] = mapped_column(Integer)
    max_attempts: Mapped[int] = mapped_column(Integer)
    lease_epoch: Mapped[int] = mapped_column(Integer)
    cancel_version: Mapped[int] = mapped_column(Integer)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    checkpoint_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_work_progress_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


IMMUTABLE_FIELDS = (
    "job_id",
    "submission_key",
    "semantic_operation_key",
    "protocol_version",
    "kind",
    "run_id",
    "generation",
    "root_session_id",
    "execution_session_id",
    "request_hash",
    "proof_request_hash",
    "original_occurrence_json",
    "stage_request_id",
    "operation",
    "request_json",
    "producer_identity",
    "deadline_at",
    "accepted_at",
    "max_attempts",
)
TRIGGERS = (
    "CREATE TRIGGER skill_callback_identity_immutable BEFORE UPDATE "
    "ON research_skill_callback_jobs "
    "WHEN "
    + " OR ".join(f"OLD.{column} IS NOT NEW.{column}" for column in IMMUTABLE_FIELDS)
    + " BEGIN SELECT RAISE(ABORT, 'skill_callback_identity_immutable'); END",
    "CREATE TRIGGER skill_callback_completed_immutable BEFORE UPDATE "
    "ON research_skill_callback_jobs "
    "WHEN OLD.status = 'completed' "
    "BEGIN SELECT RAISE(ABORT, 'skill_callback_completed_immutable'); END",
    "CREATE TRIGGER skill_callback_no_delete BEFORE DELETE ON research_skill_callback_jobs "
    "BEGIN SELECT RAISE(ABORT, 'skill_callback_no_delete'); END",
)
for trigger in TRIGGERS:
    event.listen(
        ResearchSkillCallbackJobRecord.__table__,
        "after_create",
        DDL(trigger).execute_if(dialect="sqlite"),
    )


@dataclass(frozen=True)
class CallbackLeaseObservation:
    """Liveness only; never an authorization substitute for the final writer."""

    lease_until: datetime
    run_lease_until: datetime
    deadline_at: datetime
    confirmed_at: datetime

    @property
    def valid_until(self):
        return min(self.lease_until, self.run_lease_until, self.deadline_at)


@dataclass(frozen=True)
class CallbackLease:
    job_id: str
    owner: str
    attempt: int
    epoch: int
    cancel_version: int
    initial_observation: CallbackLeaseObservation | None = None


class ResearchSkillCallbackJobs:
    def __init__(
        self,
        database: Database,
        *,
        clock=utcnow,
        lease_seconds=120,
        queue_limit=256,
        max_attempts=8,
    ):
        if lease_seconds < 30 or queue_limit < 1 or max_attempts < 1:
            raise ValueError("skill_callback_policy_invalid")
        self.database, self.clock = database, clock
        self.lease_seconds, self.queue_limit, self.max_attempts = (
            lease_seconds,
            queue_limit,
            max_attempts,
        )

    @staticmethod
    def _raw(row):
        if sha(row.request_json.encode()) != row.proof_request_hash:
            raise ValueError("skill_callback_saved_request_changed")
        return decoded(row.request_json)

    def execution_gate(self, session, row):
        now = self.clock()
        run = session.get(RunRecord, row.run_id, populate_existing=True)
        link = session.get(DshSessionLinkRecord, row.run_id, populate_existing=True)
        if run is None or link is None:
            raise ValueError("skill_callback_run_missing")
        if run.status == "cancelled" or link.state == "cancelled":
            raise ValueError("skill_callback_cancelled")
        if (
            link.generation != row.generation
            or link.dsh_session_id != row.root_session_id
            or row.execution_session_id != row.root_session_id
            or link.request_hash != row.request_hash
            or as_utc(link.deadline_at) != as_utc(row.deadline_at)
        ):
            raise ValueError("skill_callback_generation_changed")
        if as_utc(row.deadline_at) <= now:
            raise ValueError("skill_callback_deadline_elapsed")
        if (run.status != "running" or link.state not in {"admitted", "running", "idle"}
                or link.terminal_at is not None):
            raise ValueError("skill_callback_run_terminal")
        if (
            not run.lease_owner
            or as_utc(run.lease_expires_at) is None
            or as_utc(run.lease_expires_at) <= now
        ):
            raise ValueError("skill_callback_run_lease_unavailable")

    def accept(self, raw: bytes, producer_identity: str):
        value = decoded(raw)
        body, kind = value["body"], value["kind"]
        identity = body["identity"]
        proof = body.get("proof") if kind == "records" else body.get("research_occurrence")
        selected_input = (
            kind == "records" and proof.get("kind") == "selected_skill_provider_input.v1"
        )
        if selected_input:
            from ..application.research_skill_selected_delivery import selected_stage_key

            if (body["stage"] != "loaded"
                    or body["stage_request_id"] != selected_stage_key(identity, proof)):
                raise ValueError("skill_selected_stage_key_invalid")
        # A selected input has no fabricated model tool call. Its exact stage
        # identity occupies the semantic operation key only, never native proof.
        call = (body["stage_request_id"] if selected_input else
                proof["call_id"] if kind == "records" else body["call_id"])
        operation = body["stage"] if kind == "records" else body["arguments"]["operation"]
        stage_key = (
            body["stage_request_id"]
            if kind == "records"
            else "skill_step_" + sha(encoded([identity, call]))
        )
        semantic = sha(encoded([identity, kind, call, operation, stage_key]))
        locator = proof if proof is not None else {"native_call_id": call}
        # Proof identity is independent of caller-provided business arguments.
        # Replacing operation/stage_request_id under the same proof is a conflict.
        submission = sha(encoded([identity, kind, call, locator]))
        digest, now = sha(raw), self.clock()
        with self.database.session() as session:
            begin_immediate(session)
            existing = session.scalar(
                select(ResearchSkillCallbackJobRecord).where(
                    ResearchSkillCallbackJobRecord.submission_key == submission
                )
            )
            if existing is not None:
                if existing.proof_request_hash != digest or existing.request_json.encode() != raw:
                    raise ValueError("skill_callback_submission_conflict")
                return self.view(existing)
            queued = session.scalars(
                select(ResearchSkillCallbackJobRecord.job_id)
                .where(
                    ResearchSkillCallbackJobRecord.status.in_(["queued", "running", "retry_wait"])
                )
                .limit(self.queue_limit)
            ).all()
            if len(queued) >= self.queue_limit:
                raise ValueError("skill_callback_queue_full")
            link = session.get(DshSessionLinkRecord, identity["run_id"], populate_existing=True)
            if link is None or as_utc(link.deadline_at) is None:
                raise ValueError("skill_callback_run_missing")
            row = ResearchSkillCallbackJobRecord(
                job_id="skill_cb_" + submission,
                submission_key=submission,
                semantic_operation_key=semantic,
                protocol_version=value["protocol_version"],
                kind=kind,
                **identity,
                proof_request_hash=digest,
                original_occurrence_json=encoded(locator).decode(),
                stage_request_id=stage_key,
                operation=operation,
                request_json=raw.decode(),
                producer_identity=producer_identity,
                deadline_at=as_utc(link.deadline_at),
                status="queued",
                phase="accepted_unverified",
                attempt=0,
                max_attempts=self.max_attempts,
                lease_epoch=0,
                cancel_version=0,
                lease_owner=None,
                lease_until=None,
                next_attempt_at=now,
                accepted_at=now,
                updated_at=now,
            )
            self.execution_gate(session, row)
            session.add(row)
            session.flush()
            self._event(session, row, "accepted")
            return self.view(row)

    def get(self, job_id, *, run_id, generation, execution_session_id, request_hash):
        with self.database.session() as session:
            row = session.get(ResearchSkillCallbackJobRecord, job_id)
            if (
                row is None
                or row.run_id != run_id
                or row.generation != generation
                or row.execution_session_id != execution_session_id
                or row.request_hash != request_hash
            ):
                raise ValueError("skill_callback_query_identity_mismatch")
            self._raw(row)
            return self.view(row)

    @staticmethod
    def view(row):
        value = dict(
            schema_version="research-skill-callback-job.v1",
            job_id=row.job_id,
            run_id=row.run_id,
            generation=row.generation,
            request_hash=row.request_hash,
            proof_request_hash=row.proof_request_hash,
            status=row.status,
            poll_after_ms=1000,
        )
        if row.status == "completed":
            if row.response_json is None or sha(row.response_json.encode()) != row.response_hash:
                raise ValueError("skill_callback_first_result_changed")
            value.update(
                result=decoded(row.response_json),
                result_json=row.response_json,
                result_hash=row.response_hash,
            )
        elif row.error_code is not None:
            value["error_code"] = row.error_code
        return value

    def has_pending(self):
        """A cheap work hint only; claim/source/original fences remain mandatory."""
        with self.database.session() as session:
            now = self.clock()
            return (
                session.scalar(
                    select(ResearchSkillCallbackJobRecord.job_id)
                    .where(
                        (
                            (ResearchSkillCallbackJobRecord.status.in_(["queued", "retry_wait"]))
                            & (ResearchSkillCallbackJobRecord.next_attempt_at <= now)
                        )
                        | (
                            (ResearchSkillCallbackJobRecord.status == "running")
                            & (ResearchSkillCallbackJobRecord.lease_until <= now)
                        )
                    )
                    .limit(1)
                )
                is not None
            )

    def claim(self, owner, producer_identity):
        with self.database.session() as session:
            begin_immediate(session)
            now = self.clock()
            row = session.scalar(
                select(ResearchSkillCallbackJobRecord)
                .where(
                    (
                        (ResearchSkillCallbackJobRecord.status.in_(["queued", "retry_wait"]))
                        & (ResearchSkillCallbackJobRecord.next_attempt_at <= now)
                    )
                    | (
                        (ResearchSkillCallbackJobRecord.status == "running")
                        & (ResearchSkillCallbackJobRecord.lease_until <= now)
                    )
                )
                .order_by(ResearchSkillCallbackJobRecord.accepted_at)
                .limit(1)
            )
            if row is None:
                return None
            try:
                self._raw(row)
                self.execution_gate(session, row)
                if row.producer_identity != producer_identity:
                    raise ValueError("skill_callback_producer_changed")
                if row.attempt >= row.max_attempts:
                    raise ValueError("skill_callback_attempts_exhausted")
            except ValueError as error:
                if str(error) == "skill_callback_run_lease_unavailable":
                    row.status, row.phase = "retry_wait", "waiting_run_lease"
                    row.next_attempt_at = now + timedelta(seconds=2)
                    row.lease_owner = row.lease_until = None
                    row.updated_at = now
                    return None
                self._terminal(row, str(error), now)
                self._event(session, row, "stopped")
                return None
            row.status, row.phase = "running", "authenticating_originals"
            row.attempt += 1
            row.lease_epoch += 1
            row.lease_owner, row.lease_until = owner, now + timedelta(seconds=self.lease_seconds)
            row.updated_at = now
            self._event(session, row, "claimed")
            return CallbackLease(
                row.job_id,
                owner,
                row.attempt,
                row.lease_epoch,
                row.cancel_version,
                self._observation(session, row),
            )

    def fence(self, session, lease, *, require_execution=True):
        row = session.get(ResearchSkillCallbackJobRecord, lease.job_id, populate_existing=True)
        if (
            row is None
            or row.status != "running"
            or row.lease_owner != lease.owner
            or row.attempt != lease.attempt
            or row.lease_epoch != lease.epoch
            or row.cancel_version != lease.cancel_version
            or as_utc(row.lease_until) is None
            or as_utc(row.lease_until) <= self.clock()
        ):
            raise ValueError("skill_callback_lease_lost")
        self._raw(row)
        if require_execution:
            self.execution_gate(session, row)
        return row

    def check(self, lease):
        with self.database.session() as session:
            self.fence(session, lease)

    def _observation(self, session, row):
        run = session.get(RunRecord, row.run_id, populate_existing=True)
        return CallbackLeaseObservation(
            as_utc(row.lease_until),
            as_utc(run.lease_expires_at),
            as_utc(row.deadline_at),
            self.clock(),
        )

    def observe(self, lease):
        with self.database.session() as session:
            # A physical RO transaction binds job/currentRun/link to one snapshot.
            session.connection().exec_driver_sql("BEGIN")
            row = self.fence(session, lease)
            return self._observation(session, row)

    def read_claimed(self, lease):
        with self.database.session() as session:
            return self._raw(self.fence(session, lease))

    def renew(self, lease):
        with self.database.session() as session:
            begin_immediate(session)
            row = self.fence(session, lease)
            row.lease_until = self.clock() + timedelta(seconds=self.lease_seconds)
            row.updated_at = self.clock()  # Liveness only; never work progress/deadline.
            observation = self._observation(session, row)
        # COMMIT really returned. A failed/unknown commit never confirms renewal.
        return observation

    def checkpoint(self, lease, phase, originals):
        if phase not in {"binding_authenticated", "originals_prepared"}:
            raise ValueError("skill_callback_checkpoint_phase_invalid")
        raw = encoded(originals)
        if len(raw) > 16_384:
            raise ValueError("skill_callback_checkpoint_too_large")
        with self.database.session() as session:
            begin_immediate(session)
            row = self.fence(session, lease)
            row.phase, row.checkpoint_json = phase, raw.decode()
            row.updated_at = row.last_work_progress_at = self.clock()
            self._event(session, row, "checkpoint")

    def complete_in_session(self, session, lease, result, *, checkpoint=None):
        row = self.fence(session, lease)
        raw = encoded(result)
        if len(raw) > 1_000_000:
            raise ValueError("skill_callback_result_too_large")
        row.response_json, row.response_hash = raw.decode(), sha(raw)
        row.status, row.phase = "completed", "first_result_committed"
        row.completed_at = row.updated_at = row.last_work_progress_at = self.clock()
        row.checkpoint_json = encoded(checkpoint).decode() if checkpoint is not None else None
        row.lease_owner = row.lease_until = None
        session.flush()
        self._event(session, row, "completed")
        return result

    @staticmethod
    def _terminal(row, code, now):
        row.status = (
            "cancelled"
            if code == "skill_callback_cancelled"
            else "superseded"
            if code in {"skill_callback_generation_changed", "skill_callback_producer_changed"}
            else "expired"
            if code == "skill_callback_deadline_elapsed"
            else "rejected"
        )
        if row.status == "cancelled":
            row.cancel_version += 1
        row.error_code, row.phase, row.updated_at = code, "stopped_before_new_commit", now
        row.lease_owner = row.lease_until = None

    def reject(self, lease, code):
        with self.database.session() as session:
            begin_immediate(session)
            row = self.fence(session, lease, require_execution=False)
            self._terminal(row, code, self.clock())
            self._event(session, row, "stopped")

    def requeue(self, lease, code):
        with self.database.session() as session:
            begin_immediate(session)
            row = self.fence(session, lease, require_execution=False)
            row.status, row.phase = "retry_wait", "reauthenticate_on_next_attempt"
            row.error_code, row.updated_at = code, self.clock()
            row.next_attempt_at = self.clock() + timedelta(seconds=2)
            row.lease_owner = row.lease_until = None
            self._event(session, row, "retry_scheduled")

    @staticmethod
    def _event(session, row, event):
        sequence = (
            session.scalar(
                select(func.max(RunEventRecord.sequence_no)).where(
                    RunEventRecord.run_id == row.run_id
                )
            )
            or 0
        )
        session.add(
            RunEventRecord(
                run_id=row.run_id,
                sequence_no=sequence + 1,
                event_type="research_skill_callback_" + event,
                occurred_at=row.updated_at,
                payload_json=encoded(
                    dict(
                        job_id=row.job_id,
                        attempt=row.attempt,
                        lease_epoch=row.lease_epoch,
                        phase=row.phase,
                        status=row.status,
                        error_code=row.error_code,
                        checkpoint_hash=(
                            sha(row.checkpoint_json.encode()) if row.checkpoint_json else None
                        ),
                    )
                ).decode(),
            )
        )

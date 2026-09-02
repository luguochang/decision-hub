from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, or_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts.models import (
    CandidateProposal,
    EvolutionJobCountsView,
    EvolutionJobCreate,
    EvolutionJobView,
    EvolutionPlanningContext,
    ExperienceCreate,
    ExperienceView,
    ServiceHeartbeatView,
)
from packages.kernel.decision_hub_kernel.application.evolution import EvolutionAssetService
from packages.kernel.decision_hub_kernel.persistence.db import (
    ActivePointerRecord,
    ArtifactRecord,
    CandidateVersionRecord,
    Database,
    EvaluationRecord,
    EvolutionJobRecord,
    FailurePatternRecord,
    FeedbackRecord,
    ForecastRecord,
    ObservationRecord,
    OutcomeRecord,
    PromotionDecisionRecord,
    ServiceHeartbeatRecord,
    SnapshotRecord,
    utcnow,
)

JOB_SCHEMA = "evolution-job.v1"
JOB_STAGES = {"discover", "plan", "candidate", "replay", "holdout", "shadow", "review"}
TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
NEGATIVE_FEEDBACK = {"incorrect", "not_useful", "needs_review"}


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class EvolutionJobPolicy(BaseModel):
    """Versioned deterministic trigger and retry policy."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_version: str = "evolution-job-policy.v1"
    domain_pack_ref: str = "crypto_macro.v1"
    failure_occurrence_threshold: int = Field(default=2, ge=1)
    evaluation_batch_size: int = Field(default=10, ge=1)
    max_attempts: int = Field(default=3, ge=1, le=10)
    scheduled_scan_enabled: bool = True


class EvolutionJobService:
    def __init__(self, database: Database, *, clock: Callable[[], datetime] = utcnow) -> None:
        self.database = database
        self.clock = clock

    def enqueue(self, request: EvolutionJobCreate) -> EvolutionJobView:
        now = _aware(self.clock())
        job_id = f"evolution_job_{hashlib.sha256(request.trigger_key.encode()).hexdigest()[:24]}"
        try:
            with self.database.session() as session:
                existing = (
                    session.query(EvolutionJobRecord)
                    .filter_by(trigger_key=request.trigger_key)
                    .one_or_none()
                )
                if existing is not None:
                    return self._matching(existing, request)
                row = EvolutionJobRecord(
                    job_id=job_id,
                    schema_version=JOB_SCHEMA,
                    trigger_key=request.trigger_key,
                    trigger_type=request.trigger_type,
                    domain_pack_ref=request.domain_pack_ref,
                    status="queued",
                    stage="discover",
                    input_refs_json=json.dumps(request.input_refs),
                    experiment_refs_json="[]",
                    result_refs_json="[]",
                    attempt=0,
                    max_attempts=request.max_attempts,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
                session.flush()
                return self._view(row)
        except IntegrityError:
            # A concurrent scanner can win the unique trigger insert. Re-read and
            # validate the immutable request instead of surfacing a false failure.
            with self.database.session() as session:
                existing = (
                    session.query(EvolutionJobRecord)
                    .filter_by(trigger_key=request.trigger_key)
                    .one()
                )
                return self._matching(existing, request)

    def claim(self, worker_id: str, *, lease_seconds: int) -> EvolutionJobView | None:
        if not worker_id:
            raise ValueError("evolution_worker_identity_required")
        if lease_seconds <= 0:
            raise ValueError("evolution_lease_invalid")
        now = _aware(self.clock())
        expires = now + timedelta(seconds=lease_seconds)
        with self.database.session() as session:
            candidates = (
                session.query(EvolutionJobRecord)
                .filter(
                    EvolutionJobRecord.status.in_(("queued", "retry_wait", "running")),
                    or_(
                        EvolutionJobRecord.status.in_(("queued", "retry_wait")),
                        and_(
                            EvolutionJobRecord.status == "running",
                            EvolutionJobRecord.lease_expires_at <= now,
                        ),
                    ),
                    or_(
                        EvolutionJobRecord.next_attempt_at.is_(None),
                        EvolutionJobRecord.next_attempt_at <= now,
                    ),
                )
                .order_by(EvolutionJobRecord.created_at.asc())
                .limit(10)
                .all()
            )
            for candidate in candidates:
                changed = session.execute(
                    update(EvolutionJobRecord)
                    .where(
                        EvolutionJobRecord.job_id == candidate.job_id,
                        or_(
                            EvolutionJobRecord.status.in_(("queued", "retry_wait")),
                            and_(
                                EvolutionJobRecord.status == "running",
                                EvolutionJobRecord.lease_expires_at <= now,
                            ),
                        ),
                    )
                    .values(
                        status="running",
                        attempt=EvolutionJobRecord.attempt + 1,
                        lease_owner=worker_id,
                        lease_expires_at=expires,
                        next_attempt_at=None,
                        updated_at=now,
                    )
                    .execution_options(synchronize_session=False)
                )
                if int(getattr(changed, "rowcount", 0)) == 1:
                    session.expire_all()
                    return self._view(session.get(EvolutionJobRecord, candidate.job_id))
        return None

    def renew(self, job_id: str, worker_id: str, *, lease_seconds: int) -> EvolutionJobView:
        if lease_seconds <= 0:
            raise ValueError("evolution_lease_invalid")
        now = _aware(self.clock())
        with self.database.session() as session:
            changed = session.execute(
                update(EvolutionJobRecord)
                .where(
                    EvolutionJobRecord.job_id == job_id,
                    EvolutionJobRecord.status == "running",
                    EvolutionJobRecord.lease_owner == worker_id,
                )
                .values(
                    lease_expires_at=now + timedelta(seconds=lease_seconds),
                    updated_at=now,
                )
                .execution_options(synchronize_session=False)
            )
            if int(getattr(changed, "rowcount", 0)) != 1:
                raise PermissionError("evolution_job_lease_not_owned")
            session.expire_all()
            return self._view(session.get(EvolutionJobRecord, job_id))

    def fail(
        self,
        job_id: str,
        worker_id: str,
        *,
        error_code: str,
        retryable: bool,
        backoff_seconds: int = 60,
    ) -> EvolutionJobView:
        now = _aware(self.clock())
        with self.database.session() as session:
            row = self._owned(session, job_id, worker_id)
            if retryable and row.attempt < row.max_attempts:
                row.status = "retry_wait"
                row.next_attempt_at = now + timedelta(seconds=backoff_seconds)
            else:
                row.status = "failed"
                row.finished_at = now
                row.next_attempt_at = None
            row.last_error_code = error_code
            row.lease_owner = None
            row.lease_expires_at = None
            row.updated_at = now
            return self._view(row)

    def advance(
        self,
        job_id: str,
        worker_id: str,
        *,
        stage: str,
        status: str = "running",
        candidate_id: str | None = None,
        experiment_refs: list[str] | None = None,
        result_refs: list[str] | None = None,
    ) -> EvolutionJobView:
        if stage not in JOB_STAGES:
            raise ValueError("evolution_stage_invalid")
        if status not in {"running", "pending_owner_review", "completed", "cancelled"}:
            raise ValueError("evolution_status_invalid")
        now = _aware(self.clock())
        with self.database.session() as session:
            row = self._owned(session, job_id, worker_id)
            row.stage = stage
            row.status = status
            if candidate_id is not None:
                row.candidate_id = candidate_id
            if experiment_refs is not None:
                row.experiment_refs_json = json.dumps(experiment_refs)
            if result_refs is not None:
                row.result_refs_json = json.dumps(result_refs)
            if status in TERMINAL_STATUSES:
                row.finished_at = now
            if status in TERMINAL_STATUSES or status == "pending_owner_review":
                row.lease_owner = None
                row.lease_expires_at = None
            row.updated_at = now
            return self._view(row)

    def list_jobs(self, limit: int = 100) -> list[EvolutionJobView]:
        with self.database.session() as session:
            rows = (
                session.query(EvolutionJobRecord)
                .order_by(EvolutionJobRecord.created_at.desc())
                .limit(max(1, min(limit, 500)))
                .all()
            )
            return [self._view(row) for row in rows]

    def counts(self) -> EvolutionJobCountsView:
        values = {
            status: 0
            for status in (
                "queued",
                "running",
                "retry_wait",
                "pending_owner_review",
                "completed",
                "failed",
                "cancelled",
            )
        }
        with self.database.session() as session:
            for row in session.query(EvolutionJobRecord.status).all():
                if row[0] in values:
                    values[row[0]] += 1
        return EvolutionJobCountsView(**values)

    def settle_owner_reviews(self) -> int:
        """Complete review jobs only after an owner decision exists."""
        now = _aware(self.clock())
        with self.database.session() as session:
            decided = {
                row[0]
                for row in session.query(PromotionDecisionRecord.candidate_id).distinct().all()
            }
            if not decided:
                return 0
            changed = session.execute(
                update(EvolutionJobRecord)
                .where(
                    EvolutionJobRecord.status == "pending_owner_review",
                    EvolutionJobRecord.candidate_id.in_(decided),
                )
                .values(status="completed", finished_at=now, updated_at=now)
                .execution_options(synchronize_session=False)
            )
            return int(getattr(changed, "rowcount", 0) or 0)

    @classmethod
    def _matching(
        cls, row: EvolutionJobRecord, request: EvolutionJobCreate
    ) -> EvolutionJobView:
        stored = cls._view(row)
        if (
            stored.trigger_type != request.trigger_type
            or stored.domain_pack_ref != request.domain_pack_ref
            or stored.input_refs != request.input_refs
            or stored.max_attempts != request.max_attempts
        ):
            raise ValueError("evolution_trigger_reused")
        return stored

    @staticmethod
    def _owned(session: Session, job_id: str, worker_id: str) -> EvolutionJobRecord:
        row = session.get(EvolutionJobRecord, job_id)
        if row is None:
            raise ValueError("evolution_job_not_found")
        if row.status != "running" or row.lease_owner != worker_id:
            raise PermissionError("evolution_job_lease_not_owned")
        return row

    @staticmethod
    def _view(row: EvolutionJobRecord | None) -> EvolutionJobView:
        if row is None:
            raise ValueError("evolution_job_not_found")
        return EvolutionJobView.model_validate(
            {
                "job_id": row.job_id,
                "schema_version": row.schema_version,
                "trigger_key": row.trigger_key,
                "trigger_type": row.trigger_type,
                "domain_pack_ref": row.domain_pack_ref,
                "status": row.status,
                "stage": row.stage,
                "input_refs": json.loads(row.input_refs_json),
                "candidate_id": row.candidate_id,
                "experiment_refs": json.loads(row.experiment_refs_json),
                "result_refs": json.loads(row.result_refs_json),
                "attempt": row.attempt,
                "max_attempts": row.max_attempts,
                "lease_owner": row.lease_owner,
                "lease_expires_at": _aware(row.lease_expires_at) if row.lease_expires_at else None,
                "next_attempt_at": _aware(row.next_attempt_at) if row.next_attempt_at else None,
                "last_error_code": row.last_error_code,
                "created_at": _aware(row.created_at),
                "updated_at": _aware(row.updated_at),
                "finished_at": _aware(row.finished_at) if row.finished_at else None,
            }
        )


class ServiceHeartbeatService:
    def __init__(self, database: Database, *, clock: Callable[[], datetime] = utcnow) -> None:
        self.database = database
        self.clock = clock

    def beat(
        self,
        *,
        service_id: str,
        role: str,
        instance_id: str,
        version: str,
        mode: str,
        interval_seconds: float,
        started_at: datetime | None = None,
        last_error_code: str | None = None,
    ) -> ServiceHeartbeatView:
        now = _aware(self.clock())
        with self.database.session() as session:
            row = session.get(ServiceHeartbeatRecord, service_id)
            if row is None:
                row = ServiceHeartbeatRecord(
                    service_id=service_id,
                    role=role,
                    instance_id=instance_id,
                    version=version,
                    mode=mode,
                    interval_seconds=interval_seconds,
                    started_at=_aware(started_at or now),
                    heartbeat_at=now,
                    last_error_code=last_error_code,
                )
                session.add(row)
            else:
                row.role = role
                row.instance_id = instance_id
                row.version = version
                row.mode = mode
                row.interval_seconds = interval_seconds
                row.heartbeat_at = now
                row.last_error_code = last_error_code
            session.flush()
            return self._view(row, now)

    def list_views(self) -> list[ServiceHeartbeatView]:
        now = _aware(self.clock())
        with self.database.session() as session:
            return [self._view(row, now) for row in session.query(ServiceHeartbeatRecord).all()]

    @staticmethod
    def _view(row: ServiceHeartbeatRecord, now: datetime) -> ServiceHeartbeatView:
        age = max(0.0, (now - _aware(row.heartbeat_at)).total_seconds())
        if age <= 2 * row.interval_seconds:
            status = "online"
        elif age <= 5 * row.interval_seconds:
            status = "stale"
        else:
            status = "offline"
        return ServiceHeartbeatView(
            service_id=row.service_id,
            role=cast(
                Literal["api", "realtime_worker", "research_worker", "evolution_worker"],
                row.role,
            ),
            instance_id=row.instance_id,
            version=row.version,
            mode=row.mode,
            status=status,
            interval_seconds=row.interval_seconds,
            started_at=_aware(row.started_at),
            heartbeat_at=_aware(row.heartbeat_at),
            last_error_code=row.last_error_code,
        )


class EvolutionTriggerScanner:
    """Translate durable feedback/failure/evaluation facts into idempotent jobs."""

    def __init__(
        self,
        database: Database,
        jobs: EvolutionJobService,
        assets: EvolutionAssetService,
        *,
        policy: EvolutionJobPolicy | None = None,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.database = database
        self.jobs = jobs
        self.assets = assets
        self.policy = policy or EvolutionJobPolicy()
        self.clock = clock

    def scan(self) -> list[EvolutionJobView]:
        self.jobs.settle_owner_reviews()
        self._refresh_failure_patterns()
        requests = [
            *self._feedback_requests(),
            *self._failure_requests(),
            *self._evaluation_requests(),
        ]
        jobs = [self.jobs.enqueue(request) for request in requests]
        scheduled = self._scheduled_request()
        if scheduled is not None:
            jobs.append(self.jobs.enqueue(scheduled))
        return jobs

    def _refresh_failure_patterns(self) -> None:
        try:
            self.assets.refresh_failure_patterns()
        except IntegrityError:
            # Concurrent scanners can race the first unique failure-code insert.
            # The winning transaction is now durable, so a single re-read/update
            # pass converges without introducing a process-local lock.
            self.assets.refresh_failure_patterns()

    def _feedback_requests(self) -> list[EvolutionJobCreate]:
        with self.database.session() as session:
            rows = (
                session.query(FeedbackRecord)
                .filter(FeedbackRecord.verdict.in_(NEGATIVE_FEEDBACK))
                .order_by(FeedbackRecord.created_at, FeedbackRecord.feedback_id)
                .all()
            )
            return [
                EvolutionJobCreate(
                    trigger_key=f"feedback:{row.feedback_id}",
                    trigger_type="feedback",
                    domain_pack_ref=self.policy.domain_pack_ref,
                    input_refs=[f"feedback:{row.feedback_id}"],
                    max_attempts=self.policy.max_attempts,
                )
                for row in rows
            ]

    def _failure_requests(self) -> list[EvolutionJobCreate]:
        with self.database.session() as session:
            rows = (
                session.query(FailurePatternRecord)
                .filter(
                    or_(
                        FailurePatternRecord.occurrence_count
                        >= self.policy.failure_occurrence_threshold,
                        FailurePatternRecord.status == "regressed",
                    )
                )
                .order_by(FailurePatternRecord.pattern_id)
                .all()
            )
            return [
                EvolutionJobCreate(
                    trigger_key=(
                        f"failure_pattern:{row.pattern_id}:{row.occurrence_count}"
                    ),
                    trigger_type="failure_pattern",
                    domain_pack_ref=self.policy.domain_pack_ref,
                    input_refs=[f"failure_pattern:{row.pattern_id}"],
                    max_attempts=self.policy.max_attempts,
                )
                for row in rows
            ]

    def _evaluation_requests(self) -> list[EvolutionJobCreate]:
        with self.database.session() as session:
            rows = (
                session.query(EvaluationRecord)
                .order_by(EvaluationRecord.evaluated_at, EvaluationRecord.evaluation_id)
                .all()
            )
        size = self.policy.evaluation_batch_size
        requests: list[EvolutionJobCreate] = []
        for end in range(size, len(rows) + 1, size):
            batch = rows[end - size : end]
            last = batch[-1]
            requests.append(
                EvolutionJobCreate(
                    trigger_key=f"evaluation_batch:{end // size}:{last.evaluation_id}",
                    trigger_type="evaluation_batch",
                    domain_pack_ref=self.policy.domain_pack_ref,
                    input_refs=[f"evaluation:{row.evaluation_id}" for row in batch],
                    max_attempts=self.policy.max_attempts,
                )
            )
        return requests

    def _scheduled_request(self) -> EvolutionJobCreate | None:
        if not self.policy.scheduled_scan_enabled:
            return None
        with self.database.session() as session:
            consumed = {
                ref
                for (raw_refs,) in session.query(EvolutionJobRecord.input_refs_json).all()
                for ref in json.loads(raw_refs)
            }
            candidates: set[str] = set()
            for row in session.query(FailurePatternRecord).all():
                candidates.update(json.loads(row.source_refs_json))
        unconsumed = sorted(candidates - consumed)
        if not unconsumed:
            return None
        watermark = hashlib.sha256("\n".join(unconsumed).encode()).hexdigest()[:16]
        day = _aware(self.clock()).date().isoformat()
        return EvolutionJobCreate(
            trigger_key=(
                f"scheduled:{self.policy.domain_pack_ref}:{day}:{watermark}"
            ),
            trigger_type="scheduled",
            domain_pack_ref=self.policy.domain_pack_ref,
            input_refs=unconsumed,
            max_attempts=self.policy.max_attempts,
        )


class EvolutionContextService:
    """Build a bounded typed planning context from immutable job references."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def build(self, job: EvolutionJobView) -> EvolutionPlanningContext:
        context: list[str] = []
        with self.database.session() as session:
            for reference in job.input_refs:
                if reference.startswith("feedback:"):
                    row = session.get(FeedbackRecord, reference.removeprefix("feedback:"))
                    if row is not None:
                        context.append(
                            f"feedback {row.verdict} on {row.target_type}:{row.target_id}: "
                            f"{row.notes}"
                        )
                        continue
                if reference.startswith("failure_pattern:"):
                    row = session.get(
                        FailurePatternRecord,
                        reference.removeprefix("failure_pattern:"),
                    )
                    if row is not None:
                        context.append(
                            f"failure {row.failure_code} occurred {row.occurrence_count} times; "
                            f"impact={row.impact}; status={row.status}"
                        )
                        continue
                if reference.startswith("evaluation:"):
                    row = session.get(
                        EvaluationRecord, reference.removeprefix("evaluation:")
                    )
                    if row is not None:
                        context.append(
                            f"evaluation {row.evaluation_id}: brier={row.brier_score:.6f}, "
                            f"net_return_pct={row.net_return_pct:.6f}, "
                            f"direction_correct={row.direction_correct}"
                        )
                        continue
                context.append(f"referenced fact: {reference}")
            pointer = (
                session.query(ActivePointerRecord)
                .filter_by(domain_pack_ref=job.domain_pack_ref)
                .one_or_none()
            )
            active_candidate = (
                session.get(CandidateVersionRecord, pointer.candidate_id)
                if pointer is not None
                else None
            )
            if pointer is not None and active_candidate is None:
                raise ValueError("evolution_active_candidate_not_found")
        return EvolutionPlanningContext(
            job_id=job.job_id,
            domain_pack_ref=job.domain_pack_ref,
            context_items=context or ["No actionable context was resolved."],
            evidence_refs=list(job.input_refs),
            active_candidate_id=pointer.candidate_id if pointer is not None else None,
            active_candidate_version=(
                active_candidate.version if active_candidate is not None else None
            ),
            active_candidate_content_hash=(
                active_candidate.content_hash if active_candidate is not None else None
            ),
        )


class EvolutionExperienceService:
    """Create experience only when canonical Outcome/Evaluation lineage exists."""

    def __init__(self, database: Database, assets: EvolutionAssetService) -> None:
        self.database = database
        self.assets = assets

    def create_from_job(
        self, job: EvolutionJobView, proposal: CandidateProposal
    ) -> ExperienceView | None:
        evaluation_ids = [
            ref.removeprefix("evaluation:")
            for ref in job.input_refs
            if ref.startswith("evaluation:")
        ]
        if not evaluation_ids:
            return None
        with self.database.session() as session:
            evaluations = [session.get(EvaluationRecord, ref) for ref in evaluation_ids]
            if any(item is None for item in evaluations):
                return None
            typed_evaluations = [item for item in evaluations if item is not None]
            outcomes: list[OutcomeRecord] = []
            evidence_refs: set[str] = set()
            event_families: set[str] = set()
            for evaluation in typed_evaluations:
                outcome = (
                    session.query(OutcomeRecord)
                    .filter_by(forecast_id=evaluation.forecast_id)
                    .one_or_none()
                )
                forecast = session.get(ForecastRecord, evaluation.forecast_id)
                artifact = (
                    session.get(ArtifactRecord, forecast.artifact_id)
                    if forecast is not None
                    else None
                )
                if outcome is None or artifact is None:
                    return None
                outcomes.append(outcome)
                observation = (
                    session.query(ObservationRecord)
                    .filter_by(event_id=artifact.event_id)
                    .order_by(ObservationRecord.received_at.desc())
                    .first()
                )
                event_families.add(
                    observation.event_hint
                    if observation is not None and observation.event_hint
                    else "unknown"
                )
                snapshots = session.query(SnapshotRecord).filter_by(
                    event_id=artifact.event_id
                )
                for snapshot in snapshots:
                    for evidence in json.loads(snapshot.evidence_json):
                        if isinstance(evidence, dict) and isinstance(
                            evidence.get("evidence_id"), str
                        ):
                            evidence_refs.add(evidence["evidence_id"])
        if not outcomes or not evidence_refs:
            return None
        event_family = (
            next(iter(event_families)) if len(event_families) == 1 else "mixed"
        )
        return self.assets.create_experience(
            ExperienceCreate(
                request_id=f"evolution:{job.job_id}:experience",
                domain_pack_ref=job.domain_pack_ref,
                event_family=event_family,
                lesson=f"{proposal.summary} {proposal.rationale}",
                applicable_conditions=list(proposal.changes),
                evidence_refs=sorted(evidence_refs),
                outcome_refs=sorted(item.outcome_id for item in outcomes),
                evaluation_refs=sorted(
                    item.evaluation_id for item in typed_evaluations
                ),
                source_type="agent",
                created_by="evolution_supervisor",
            )
        )

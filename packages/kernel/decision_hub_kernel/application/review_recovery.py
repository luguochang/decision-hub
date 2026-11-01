"""Read narrowly recoverable review failures without reviving any old Run.

The scheduler owns new plan/Run creation and must call validation under the
same writer reservation as its authority, window and shared budget checks.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts import ErrorProvenance, ResearchTraceEvent
from packages.kernel.decision_hub_kernel.application.dsh_terminal_receipts import (
    VerifiedHostTerminal,
    read_authenticated_terminal_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_method import canonical_recipe
from packages.kernel.decision_hub_kernel.application.research_tracking import (
    tracking_basis_in_session,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    ArtifactRecord,
    DshSessionLinkRecord,
    ResearchResultRecord,
    ResearchToolCallReservationRecord,
    ResearchTraceRecord,
    ReviewScheduleRecord,
    RunRecord,
    as_utc,
)

RETRY_DELAYS_SECONDS = (60, 300)
RECOVERABLE_ERROR = "research_observation_unavailable"
RECOVERABLE_CAUSE = "cancellation_projection_db_busy"


@dataclass(frozen=True)
class ReviewRecoveryCandidate:
    parent_run_id: str
    study_id: str
    horizon: str
    base_plan_id: str
    source_plan_id: str
    source_failed_run_id: str
    attempt_no: int
    trigger_ref: str
    trigger_at: datetime
    expires_at: datetime
    source_finished_at: datetime
    failure_trace_hash: str
    terminal_receipt_ref: str
    terminal_receipt_hash: str


@dataclass(frozen=True)
class ReviewRecoveryDecision:
    base_plan_id: str
    reason_code: str | None
    candidate: ReviewRecoveryCandidate | None = None


def _time(value: datetime | None) -> datetime:
    current = as_utc(value)
    if current is None:
        raise ValueError("review_recovery_original_time_missing")
    return current


def _failed_source(
    session: Session, plan: ReviewScheduleRecord, *, study_id: str, now: datetime,
) -> tuple[RunRecord, str, VerifiedHostTerminal]:
    if plan.status != "failed" or not plan.child_run_id:
        raise ValueError("review_recovery_source_not_failed")
    run = session.get(RunRecord, plan.child_run_id, populate_existing=True)
    if (
        run is None or run.status != "failed" or run.strategy_version != "research.v1"
        or run.admission_origin != "scheduled_recheck" or run.study_id != study_id
        or run.parent_run_id != plan.parent_run_id
        or run.idempotency_key != f"research-review-plan:{plan.review_plan_id}"
        or run.error_code != RECOVERABLE_ERROR or plan.failure_code != run.error_code
    ):
        raise ValueError("review_recovery_source_identity_invalid")
    finished = as_utc(run.finished_at)
    if finished is None or not _time(run.created_at) <= finished <= now:
        raise ValueError("review_recovery_source_time_invalid")
    # An expired lease owner is still an unconfirmed worker. Require explicit
    # release, rather than assuming the process died from a timestamp alone.
    if run.lease_owner is not None or run.lease_expires_at is not None:
        raise ValueError("review_recovery_worker_lease_unsettled")
    if (
        run.artifact_id is not None
        or session.query(ArtifactRecord).filter_by(run_id=run.run_id).first() is not None
        or session.get(ResearchResultRecord, run.run_id, populate_existing=True) is not None
    ):
        raise ValueError("review_recovery_result_already_present")
    reservations = session.query(ResearchToolCallReservationRecord).filter_by(
        run_id=run.run_id,
    ).populate_existing().all()
    if any(item.status not in {"completed", "failed", "cancelled"} for item in reservations):
        raise ValueError("review_recovery_tool_activity_unknown")
    link = session.get(DshSessionLinkRecord, run.run_id, populate_existing=True)
    if (
        link is None or link.state != "failed"
        or link.terminal_at is None or link.result_ref is not None or link.result_hash is not None
    ):
        raise ValueError("review_recovery_host_terminal_unknown")
    terminal = read_authenticated_terminal_in_session(session, run.run_id, now=now)
    if terminal is None:
        raise ValueError("review_recovery_host_authentication_missing")
    if (
        terminal.completion.terminal_status != "failed"
        or terminal.completion.result_ref is not None or terminal.completion.result_hash is not None
        or terminal.completion.completed_at > now
        or link.accepted_at is None
        or not _time(link.created_at) <= _time(link.accepted_at) <= terminal.completion.completed_at
    ):
        raise ValueError("review_recovery_host_terminal_invalid")
    rows = session.query(ResearchTraceRecord).filter_by(
        run_id=run.run_id, event_type="session_stopped",
    ).order_by(ResearchTraceRecord.sequence_no).populate_existing().all()
    if len(rows) != 1:
        raise ValueError("review_recovery_failure_trace_ambiguous")
    row = rows[0]
    if row.error_provenance_json is None:
        raise ValueError("review_recovery_failure_provenance_missing")
    original_error = json.loads(row.error_provenance_json)
    # Missing/None provider_attempts must never become a default empty array.
    if not isinstance(original_error, dict) or original_error.get("provider_attempts") != []:
        raise ValueError("review_recovery_provider_attempts_unverified")
    error = ErrorProvenance.model_validate(original_error)
    trace = ResearchTraceEvent.model_validate({
        "schema_version": "research-trace-event.v1", "run_id": row.run_id,
        "research_session_id": row.research_session_id, "sequence_no": row.sequence_no,
        "event_type": row.event_type, "occurred_at": _time(row.occurred_at), "stage": row.stage,
        "summary": row.summary, "reference_type": row.reference_type,
        "reference_id": row.reference_id,
        "status": row.status, "error_code": row.error_code, "error": error,
    })
    encoded = canonical_recipe(trace.model_dump(
        mode="json", exclude={"sequence_no", "error_verification"},
    ))
    if row.event_hash != hashlib.sha256(encoded.encode()).hexdigest():
        raise ValueError("review_recovery_failure_trace_hash_invalid")
    if (
        trace.research_session_id != f"product:{run.run_id}" or trace.stage != "done"
        or trace.reference_type != "run" or trace.reference_id != run.run_id
        or trace.status != "failed" or trace.error_code != run.error_code
        or error.error_code != RECOVERABLE_ERROR or error.origin != "orchestration"
        or error.cause_code != RECOVERABLE_CAUSE or error.retryable is not True
        or error.capability_id is not None or error.tool_call_id is not None
        or not finished <= trace.occurred_at <= now
        or not trace.occurred_at <= _time(row.created_at) <= now
    ):
        raise ValueError("review_recovery_failure_not_transient")
    return run, row.event_hash, terminal


def _candidate_for_base(
    session: Session, base: ReviewScheduleRecord, *, study_id: str,
    basis_run_id: str, now: datetime,
) -> ReviewRecoveryCandidate:
    basis = tracking_basis_in_session(session, study_id=study_id, now=now)
    if basis is None or basis.run.run_id != basis_run_id or base.parent_run_id != basis_run_id:
        raise ValueError("review_recovery_tracking_basis_inactive")
    horizon = next((item for item in basis.result.horizons if item.horizon == base.horizon), None)
    if horizon is None or now >= horizon.expires_at:
        raise ValueError("review_recovery_window_expired")
    base_at = _time(base.trigger_at)
    if base.trigger_type != "next_review" or base.trigger_ref.startswith("retry:"):
        raise ValueError("review_recovery_base_plan_invalid")
    if not basis.result.finished_at <= base_at < horizon.expires_at:
        raise ValueError("review_recovery_base_window_invalid")
    legacy_hash = hashlib.sha256((
        f"{basis_run_id}|{base.horizon}|next_review|{base.trigger_ref}|{base_at.isoformat()}"
    ).encode()).hexdigest()
    tracking_ref = f"tracking_next_review:{basis_run_id}:{base.horizon}"
    tracking_hash = hashlib.sha256(f"tracking-next-review.v1|{tracking_ref}".encode()).hexdigest()
    if base.trigger_hash != legacy_hash and not (
        base.trigger_ref == tracking_ref and base.trigger_hash == tracking_hash
        and base.review_plan_id == f"review_plan_{tracking_hash}"
    ):
        raise ValueError("review_recovery_base_plan_hash_invalid")
    # The failed original remains required even after a later attempt failed.
    _failed_source(session, base, study_id=study_id, now=now)
    prefix = f"retry:v1:{base.review_plan_id}:"
    related = session.query(ReviewScheduleRecord).filter(
        ReviewScheduleRecord.parent_run_id == basis_run_id,
        ReviewScheduleRecord.trigger_ref.like(f"retry:%:{base.review_plan_id}:%"),
    ).populate_existing().all()
    attempts: dict[int, ReviewScheduleRecord] = {}
    for row in related:
        suffix = row.trigger_ref.removeprefix(prefix)
        if (
            row.trigger_type != "next_review" or row.horizon != base.horizon
            or row.trigger_ref not in {prefix + "1", prefix + "2"}
            or not suffix.isdecimal() or int(suffix) in attempts
        ):
            raise ValueError("review_recovery_attempt_chain_invalid")
        digest = hashlib.sha256((
            f"review-recovery.v1|{basis_run_id}|{row.trigger_ref}"
        ).encode()).hexdigest()
        if row.trigger_hash != digest or row.review_plan_id != f"review_plan_{digest}":
            raise ValueError("review_recovery_attempt_plan_hash_invalid")
        attempts[int(suffix)] = row
    source = base
    for attempt_no, delay in enumerate(RETRY_DELAYS_SECONDS, 1):
        row = attempts.get(attempt_no)
        if row is None and any(number > attempt_no for number in attempts):
            raise ValueError("review_recovery_attempt_chain_invalid")
        source_run, trace_hash, terminal = _failed_source(
            session, source, study_id=study_id, now=now,
        )
        finished = as_utc(source_run.finished_at)
        assert finished is not None
        trigger_at = finished + timedelta(seconds=delay)
        if trigger_at >= horizon.expires_at:
            raise ValueError("review_recovery_backoff_outside_window")
        if row is None or (row.status == "planned" and row.child_run_id is None):
            if row is not None and as_utc(row.trigger_at) != trigger_at:
                raise ValueError("review_recovery_attempt_time_invalid")
            return ReviewRecoveryCandidate(
                basis_run_id, study_id, base.horizon, base.review_plan_id,
                source.review_plan_id, source_run.run_id, attempt_no,
                prefix + str(attempt_no), trigger_at, horizon.expires_at, finished,
                trace_hash, terminal.receipt_ref, terminal.receipt_hash,
            )
        if row.status != "failed":
            raise ValueError("review_recovery_attempt_already_consumed")
        if as_utc(row.trigger_at) != trigger_at:
            raise ValueError("review_recovery_attempt_time_invalid")
        source = row
    raise ValueError("review_recovery_attempt_limit_reached")


def review_recovery_decisions_in_session(
    session: Session, *, basis_run_id: str, study_id: str, now: datetime,
) -> tuple[ReviewRecoveryDecision, ...]:
    """Report explicit denial reasons without creating or modifying records."""
    plans = session.query(ReviewScheduleRecord).filter_by(
        parent_run_id=basis_run_id, trigger_type="next_review", status="failed",
    ).order_by(ReviewScheduleRecord.review_plan_id).populate_existing().all()
    decisions = []
    for plan in plans:
        if plan.trigger_ref.startswith("retry:"):
            continue
        try:
            candidate = _candidate_for_base(
                session, plan, study_id=study_id, basis_run_id=basis_run_id, now=now,
            )
        except (ValueError, TypeError, KeyError) as exc:
            decisions.append(ReviewRecoveryDecision(plan.review_plan_id, str(exc)))
        else:
            decisions.append(ReviewRecoveryDecision(plan.review_plan_id, None, candidate))
    return tuple(decisions)


def review_recovery_candidates_in_session(
    session: Session, *, basis_run_id: str, study_id: str, now: datetime,
) -> tuple[ReviewRecoveryCandidate, ...]:
    return tuple(item.candidate for item in review_recovery_decisions_in_session(
        session, basis_run_id=basis_run_id, study_id=study_id, now=now,
    ) if item.candidate is not None)


def validate_review_recovery_in_session(
    session: Session, candidate: ReviewRecoveryCandidate, *, now: datetime,
) -> str | None:
    """Re-read the proof and due time inside the scheduler's admission writer."""
    base = session.get(ReviewScheduleRecord, candidate.base_plan_id, populate_existing=True)
    if base is None:
        return "review_recovery_base_plan_missing"
    try:
        current = _candidate_for_base(
            session, base, study_id=candidate.study_id,
            basis_run_id=candidate.parent_run_id, now=now,
        )
    except (ValueError, TypeError, KeyError) as exc:
        return str(exc)
    if current != candidate:
        return "review_recovery_candidate_changed"
    if now < current.trigger_at:
        return "review_recovery_backoff_pending"
    return None

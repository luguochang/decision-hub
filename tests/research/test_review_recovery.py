"""Real SQLite originals and local authenticated API callbacks; no Host calls."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.orm import Session

from apps.hub_api.main import create_app
from apps.hub_worker.research import ResearchRequestFactory
from packages.contracts_py.decision_hub_contracts import (
    DshBridgeError,
    DshSessionAccepted,
    DshSessionCompletion,
    DshSessionPrompt,
    DshSessionSubmit,
    ErrorProvenance,
    ResearchSessionRequest,
    ResearchTraceEvent,
)
from packages.kernel.decision_hub_kernel.application.dsh_sessions import DshSessionLinkService
from packages.kernel.decision_hub_kernel.application.dsh_terminal_receipts import (
    read_authenticated_terminal_in_session,
    terminal_receipt_role,
)
from packages.kernel.decision_hub_kernel.application.research_binding import (
    FrozenResearchBundle,
    PreparedResearchPolicyRefs,
    read_research_bundle_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_execution_binding import (
    ResearchExecutionPreparationService,
)
from packages.kernel.decision_hub_kernel.application.research_method import canonical_recipe
from packages.kernel.decision_hub_kernel.application.research_observability import (
    ResearchObservabilityService,
)
from packages.kernel.decision_hub_kernel.application.research_policy_render_v2 import (
    render_prepared_research_policy,
)
from packages.kernel.decision_hub_kernel.application.research_request_identity import (
    research_request_hash,
)
from packages.kernel.decision_hub_kernel.application.research_task import ResearchTaskService
from packages.kernel.decision_hub_kernel.application.review_recovery import (
    RECOVERABLE_CAUSE,
    RECOVERABLE_ERROR,
    ReviewRecoveryCandidate,
    review_recovery_candidates_in_session,
    review_recovery_decisions_in_session,
    validate_review_recovery_in_session,
)
from packages.kernel.decision_hub_kernel.application.review_schedule import ReviewScheduleService
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    DshSessionLinkRecord,
    DshSessionPromptRecord,
    ResearchResultRecord,
    ResearchToolCallReservationRecord,
    ResearchTraceRecord,
    ReviewScheduleRecord,
    RunEventRecord,
    RunRecord,
    RunRoleOutputRecord,
    StudyRecord,
)
from tests.dsh_native.test_dsh_session_links import identity
from tests.evolution.test_research_worker import (
    NOW,
    PACK_ROOT,
    _admitted_run,  # pyright: ignore[reportPrivateUsage]
    _result,  # pyright: ignore[reportPrivateUsage]
)
from tests.research.test_full_method_v2_bundle import (
    _prepared_pack,  # pyright: ignore[reportPrivateUsage]
    forbid_external_connections,
)

pytestmark = pytest.mark.usefixtures("forbid_external_connections")
assert forbid_external_connections is not None


@dataclass(frozen=True)
class RecoveryCase:
    database: Database
    parent_run_id: str
    study_id: str
    base_plan_id: str
    failed_run_id: str
    completion: DshSessionCompletion
    request: ResearchSessionRequest
    now: datetime


def _terminal_row(database: Database, run_id: str) -> tuple[str, str]:
    with database.session() as session:
        row = session.query(RunRoleOutputRecord).filter_by(
            run_id=run_id, role=terminal_receipt_role(1),
        ).one()
        return row.payload_json, row.payload_hash


def _failed_child(
    database: Database, *, parent_run_id: str, plan_id: str,
    started_at: datetime, authenticated: bool = True, via_api: bool = False,
    prepared_directory: Path | None = None, terminalize: bool = True,
) -> tuple[str, DshSessionCompletion, ResearchSessionRequest]:
    with database.session() as session:
        parent = session.get(RunRecord, parent_run_id)
        assert parent is not None
        event_id, study_id = parent.event_id, parent.study_id
    run_id, created = RunService(database, clock=lambda: started_at).create(
        event_id, idempotency_key=f"research-review-plan:{plan_id}",
        strategy_version="research.v1", parent_run_id=parent_run_id,
        admission_origin="scheduled_recheck", study_id=study_id,
    )
    assert created
    pack_root = PACK_ROOT
    refs = None
    if prepared_directory is not None:
        prepared_directory.mkdir(parents=True, exist_ok=True)
        pack_root, manifest = _prepared_pack(prepared_directory)
        refs = PreparedResearchPolicyRefs(
            gate_policy_ref=manifest["gate_policy_ref"],
            rubric_policy_ref=manifest["evaluation_policy_ref"],
            role_profile_refs=tuple(manifest["role_profile_refs"]),
        )
    request = ResearchRequestFactory(
        database, pack_root=pack_root, clock=lambda: started_at,
        execution_mode="replay", allowed_capabilities=("replay.research",),
        prepared_policy_refs=refs,
    ).build(event_id, run_id)
    if prepared_directory is None:
        ResearchTaskService(database, clock=lambda: started_at).freeze(request)
    links = DshSessionLinkService(database, clock=lambda: started_at)
    digest = research_request_hash(request)
    session_id, request_id = links.deterministic_ids(run_id, digest)
    submit = DshSessionSubmit(
        schema_version="dsh-session-submit.v1", run_id=run_id, request_hash=digest,
        deterministic_session_id=session_id, deterministic_request_id=request_id,
        workspace_ref="decision-hub://workspace/default",
        prompt_ref=f"hub://runs/{run_id}/prompts/1", agent_preset="decision-research",
        permission_ref="decision-hub://permissions/research-only",
        deadline_at=request.deadline_at, max_tool_calls=request.execution_budget.max_tool_calls,
        model_step_timeout_ms=request.execution_budget.per_model_step_timeout_seconds * 1000,
        generation=1,
    )
    if prepared_directory is None:
        links.reserve(submit, identity())
        links.store_prompt(DshSessionPrompt(
            schema_version="dsh-session-prompt.v1", run_id=run_id,
            dsh_session_id=session_id, request_id=request_id, request_hash=digest,
            generation=1, prompt="Frozen original research turn for the SQLite recovery control.",
        ), execution_request=request)
    else:
        with database.session() as session:
            bundle = read_research_bundle_in_session(session, run_id)
        assert isinstance(bundle, FrozenResearchBundle)
        upstream = identity()
        runtime_version = (
            f"dsh-web-{upstream.source_version}+{upstream.plugin_build_hash[:12]}"
        )
        ResearchExecutionPreparationService(database, clock=lambda: started_at).prepare(
            request, submit, upstream,
            render_prepared_research_policy(bundle, request.allowed_capabilities)
            + "\n\nFrozen V2 recovery turn.",
            runtime_id="dsh", runtime_version=runtime_version,
            profile_ref="decision-research.web.v1", prompt_policy_hash="4" * 64,
        )
    links.accepted(DshSessionAccepted(
        schema_version="dsh-session-accepted.v1", run_id=run_id,
        dsh_session_id=session_id, generation=1, accepted_at=started_at + timedelta(seconds=2),
    ))
    finished_at = started_at + timedelta(seconds=20)
    with database.session() as session:
        run = session.get(RunRecord, run_id)
        assert run is not None
        run.status = "failed"
        run.error_code = RECOVERABLE_ERROR
        run.finished_at = finished_at
        run.updated_at = finished_at
    ResearchObservabilityService(
        database, clock=lambda: finished_at + timedelta(seconds=1),
    ).append_trace(ResearchTraceEvent(
        schema_version="research-trace-event.v1", run_id=run_id,
        research_session_id=f"product:{run_id}", sequence_no=0,
        event_type="session_stopped", occurred_at=finished_at + timedelta(seconds=1),
        stage="done", summary="Cancellation projection could not reserve the busy writer.",
        reference_type="run", reference_id=run_id, status="failed",
        error_code=RECOVERABLE_ERROR,
        error=ErrorProvenance(
            error_code=RECOVERABLE_ERROR, origin="orchestration", cause_code=RECOVERABLE_CAUSE,
            capability_id=None, tool_call_id=None, retryable=True, deadline_ms=None,
            provider_attempts=[],
        ),
    ))
    # Hub failure can precede the real Host termination; do not infer activity
    # from that order or deny a later authenticated failed callback.
    completion = DshSessionCompletion(
        schema_version="dsh-session-completion.v1", run_id=run_id,
        dsh_session_id=session_id, terminal_status="failed", generation=1, last_seq=1,
        trace_ref=f"dsh://sessions/{session_id}/trace", result_ref=None, result_hash=None,
        completed_at=finished_at + timedelta(seconds=5),
        error=DshBridgeError(
            code="host_turn_failed", message="The Host turn stopped.", retryable=True,
        ),
    )
    recorded_at = completion.completed_at + timedelta(seconds=1)
    links.clock = lambda: recorded_at
    if not terminalize:
        return run_id, completion, request
    if via_api:
        app = create_app(database, source_connectors=[], dsh_callback_secret="recovery-secret")
        app.state.dsh_sessions.clock = lambda: recorded_at
        with TestClient(app) as client:
            response = client.put(
                f"/v1/dsh/sessions/{run_id}/terminal", json=completion.model_dump(mode="json"),
                headers={"X-Decision-Hub-Bridge-Key": "recovery-secret"},
            )
        assert response.status_code == 200, response.text
    else:
        links.complete(completion, authenticated_host=authenticated)
    return run_id, completion, request


def _case(
    tmp_path: Path, *, authenticated: bool = True, via_api: bool = False,
    prepared: bool = False, terminalize: bool = True, legacy_plan: bool = False,
) -> RecoveryCase:
    database, event_id, parent_run_id = _admitted_run(tmp_path)
    request = ResearchRequestFactory(
        database, pack_root=PACK_ROOT, clock=lambda: NOW, execution_mode="replay",
        allowed_capabilities=("replay.research",),
    ).build(event_id, parent_run_id)
    with database.session() as session:
        parent = session.get(RunRecord, parent_run_id)
        assert parent is not None and parent.study_id is not None
        study_id = parent.study_id
        parent.created_at = NOW - timedelta(seconds=1)
        parent.status = "completed"
        finished_at = NOW + timedelta(seconds=1)
        parent.finished_at = finished_at
        parent.updated_at = finished_at
        ResearchObservabilityService(database, clock=lambda: finished_at).save_result_in_session(
            session, parent_run_id, _result(request),
        )
    trigger_at = NOW + timedelta(minutes=10)
    ref = f"tracking_next_review:{parent_run_id}:30m"
    digest = hashlib.sha256(f"tracking-next-review.v1|{ref}".encode()).hexdigest()
    if legacy_plan:
        # A root's real scheduler uses its original time token/canonical key.
        # Freeze that same key so plan_due sees this failed original, rather
        # than discovering a second independent next_review for this horizon.
        ref = trigger_at.isoformat().replace("+00:00", "Z").replace(":", "").replace("-", "")
        digest = hashlib.sha256((
            f"{parent_run_id}|30m|next_review|{ref}|{trigger_at.isoformat()}"
        ).encode()).hexdigest()
    plan_id = f"review_plan_{digest}"
    run_id, completion, child_request = _failed_child(
        database, parent_run_id=parent_run_id, plan_id=plan_id, started_at=trigger_at,
        authenticated=authenticated, via_api=via_api,
        prepared_directory=tmp_path / "frozen-v2" if prepared else None,
        terminalize=terminalize,
    )
    completed_at = completion.completed_at
    assert completed_at is not None
    with database.session() as session:
        session.add(ReviewScheduleRecord(
            review_plan_id=plan_id, parent_run_id=parent_run_id, horizon="30m",
            trigger_type="next_review", trigger_ref=ref, trigger_at=trigger_at,
            trigger_hash=digest, status="failed", child_run_id=run_id,
            failure_code=RECOVERABLE_ERROR, created_at=NOW, updated_at=completed_at,
        ))
    return RecoveryCase(
        database, parent_run_id, study_id, plan_id, run_id, completion, child_request,
        trigger_at + timedelta(seconds=90),
    )


def _candidates(
    case: RecoveryCase, *, now: datetime | None = None,
) -> tuple[ReviewRecoveryCandidate, ...]:
    with case.database.session() as session:
        return review_recovery_candidates_in_session(
            session, basis_run_id=case.parent_run_id, study_id=case.study_id, now=now or case.now,
        )


def _retry_plan(
    case: RecoveryCase, candidate: ReviewRecoveryCandidate, *, status: str = "planned",
) -> str:
    digest = hashlib.sha256((
        f"review-recovery.v1|{candidate.parent_run_id}|{candidate.trigger_ref}"
    ).encode()).hexdigest()
    plan_id = f"review_plan_{digest}"
    with case.database.session() as session:
        session.add(ReviewScheduleRecord(
            review_plan_id=plan_id, parent_run_id=candidate.parent_run_id,
            horizon=candidate.horizon, trigger_type="next_review",
            trigger_ref=candidate.trigger_ref,
            trigger_at=candidate.trigger_at, trigger_hash=digest, status=status,
            created_at=case.now, updated_at=case.now,
        ))
    return plan_id


def test_authenticated_callback_produces_real_recovery_proof_without_mutating_source(
    tmp_path: Path,
) -> None:
    case = _case(tmp_path, via_api=True)
    original = _terminal_row(case.database, case.failed_run_id)
    before: list[tuple[str, str, str | None, datetime | None, float | None]] = []
    with case.database.session() as session:
        for row in session.query(RunRecord).all():
            before.append((row.run_id, row.status, row.error_code, row.finished_at, row.cost_usd))
    checkouts: list[int] = []

    def checked_out(*_args: object) -> None:
        checkouts.append(1)

    event.listen(case.database.engine, "checkout", checked_out)
    try:
        with case.database.session() as session:
            candidates = review_recovery_candidates_in_session(
                session, basis_run_id=case.parent_run_id, study_id=case.study_id, now=case.now,
            )
            assert len(candidates) == 1
            candidate = candidates[0]
            assert candidate.attempt_no == 1
            assert candidate.trigger_ref == f"retry:v1:{case.base_plan_id}:1"
            assert candidate.trigger_at == NOW + timedelta(minutes=10, seconds=80)
            assert validate_review_recovery_in_session(session, candidate, now=case.now) is None
            assert session.query(ReviewScheduleRecord).count() == 1
            assert session.query(RunRecord).count() == 2
            assert [(row.run_id, row.status, row.error_code, row.finished_at, row.cost_usd)
                    for row in session.query(RunRecord).all()] == before
        assert len(checkouts) == 1
    finally:
        event.remove(case.database.engine, "checkout", checked_out)
    assert _terminal_row(case.database, case.failed_run_id) == original


def test_backoff_is_frozen_and_revalidated_inside_the_borrowed_writer(tmp_path: Path) -> None:
    case = _case(tmp_path)
    candidate = _candidates(case)[0]
    _retry_plan(case, candidate)
    assert _candidates(case) == (candidate,)
    with case.database.session() as session:
        assert validate_review_recovery_in_session(
            session, candidate, now=candidate.trigger_at - timedelta(microseconds=1),
        ) == "review_recovery_backoff_pending"
        assert validate_review_recovery_in_session(
            session, candidate, now=candidate.trigger_at,
        ) is None
        trace = session.query(ResearchTraceRecord).filter_by(run_id=case.failed_run_id).one()
        trace.event_hash = "0" * 64
        session.flush()
        assert validate_review_recovery_in_session(
            session, candidate, now=case.now,
        ) == "review_recovery_failure_trace_hash_invalid"


@pytest.mark.parametrize("status", ["skipped", "admitted", "completed"])
def test_consumed_retry_is_not_recreated_after_restart(tmp_path: Path, status: str) -> None:
    case = _case(tmp_path)
    _retry_plan(case, _candidates(case)[0], status=status)
    assert _candidates(case) == ()
    assert _candidates(case) == ()
    with case.database.session() as session:
        assert session.query(ReviewScheduleRecord).count() == 2


def test_second_attempt_requires_another_real_failed_child_and_stops_at_two(tmp_path: Path) -> None:
    case = _case(tmp_path)
    first = _candidates(case)[0]
    first_id = _retry_plan(case, first)
    run_id, completion, _ = _failed_child(
        case.database, parent_run_id=case.parent_run_id, plan_id=first_id,
        started_at=first.trigger_at,
    )
    with case.database.session() as session:
        row = session.get(ReviewScheduleRecord, first_id)
        assert row is not None
        row.status, row.child_run_id, row.failure_code = "failed", run_id, RECOVERABLE_ERROR
        row.updated_at = completion.completed_at
    second_now = first.trigger_at + timedelta(seconds=330)
    second = _candidates(case, now=second_now)[0]
    assert second.attempt_no == 2
    assert second.source_failed_run_id == run_id
    assert second.trigger_at == first.trigger_at + timedelta(seconds=320)
    assert second.trigger_ref == f"retry:v1:{case.base_plan_id}:2"
    second_id = _retry_plan(case, second)
    second_run_id, second_completion, _ = _failed_child(
        case.database, parent_run_id=case.parent_run_id, plan_id=second_id,
        started_at=second.trigger_at,
    )
    with case.database.session() as session:
        row = session.get(ReviewScheduleRecord, second_id)
        assert row is not None
        row.status, row.child_run_id = "failed", second_run_id
        row.failure_code, row.updated_at = RECOVERABLE_ERROR, second_completion.completed_at
    assert _candidates(case, now=second.trigger_at + timedelta(minutes=1)) == ()
    with case.database.session() as session:
        decisions = review_recovery_decisions_in_session(
            session, basis_run_id=case.parent_run_id, study_id=case.study_id,
            now=second.trigger_at + timedelta(minutes=1),
        )
        assert decisions[0].reason_code == "review_recovery_attempt_limit_reached"
        assert session.query(RunRecord).count() == 4


def test_transport_only_terminal_does_not_mint_host_authentication(tmp_path: Path) -> None:
    case = _case(tmp_path, authenticated=False)
    assert _candidates(case) == ()
    with case.database.session() as session:
        assert read_authenticated_terminal_in_session(
            session, case.failed_run_id, now=case.now,
        ) is None
        assert session.query(RunRoleOutputRecord).filter_by(
            run_id=case.failed_run_id, role=terminal_receipt_role(1),
        ).count() == 0
        decision = review_recovery_decisions_in_session(
            session, basis_run_id=case.parent_run_id, study_id=case.study_id, now=case.now,
        )[0]
        assert decision.reason_code == "review_recovery_host_authentication_missing"


@pytest.mark.parametrize("damage", [None, "request_pack", "compiled_policy", "execution_method"])
def test_prepared_v2_recovery_revalidates_the_original_bundle(
    tmp_path: Path, damage: str | None,
) -> None:
    case = _case(tmp_path, prepared=True)
    assert len(_candidates(case)) == 1
    if damage is None:
        return
    roles = {
        "request_pack": "research_request_pack",
        "compiled_policy": "research_compiled_policy",
        "execution_method": "research_execution_method",
    }
    with case.database.session() as session:
        row = session.query(RunRoleOutputRecord).filter_by(
            run_id=case.failed_run_id, role=roles[damage],
        ).one()
        row.payload_hash = "e" * 64
    assert _candidates(case) == ()


def test_authenticated_receipt_failure_rolls_back_the_original_link_transition(
    tmp_path: Path,
) -> None:
    case = _case(tmp_path, terminalize=False)

    def reject_receipt(session: Session, *_args: object) -> None:
        if any(isinstance(row, RunRoleOutputRecord)
               and row.role.startswith("dsh_authenticated_terminal:") for row in session.new):
            raise RuntimeError("controlled receipt insert failure")

    event.listen(Session, "before_flush", reject_receipt)
    try:
        links = DshSessionLinkService(case.database, clock=lambda: case.now)
        with pytest.raises(RuntimeError, match="controlled receipt insert failure"):
            links.complete(case.completion, authenticated_host=True)
    finally:
        event.remove(Session, "before_flush", reject_receipt)
    with case.database.session() as session:
        link = session.get(DshSessionLinkRecord, case.failed_run_id)
        assert link is not None and link.state == "admitted" and link.terminal_at is None
        assert session.query(RunRoleOutputRecord).filter_by(
            run_id=case.failed_run_id, role=terminal_receipt_role(1),
        ).count() == 0


def test_new_authorized_tracking_root_invalidates_a_previously_valid_candidate(
    tmp_path: Path,
) -> None:
    case = _case(tmp_path)
    candidate = _candidates(case)[0]
    with case.database.session() as session:
        old = session.get(RunRecord, case.parent_run_id)
        assert old is not None
        event_id = old.event_id
    new_intent_at = case.now - timedelta(seconds=2)
    new_run_id, _ = RunService(case.database, clock=lambda: new_intent_at).create(
        event_id, strategy_version="research.v1", study_id=case.study_id,
    )
    request = ResearchRequestFactory(
        case.database, pack_root=PACK_ROOT, clock=lambda: new_intent_at,
        execution_mode="replay", allowed_capabilities=("replay.research",),
    ).build(event_id, new_run_id)
    result = _result(request).model_copy(update={
        "finished_at": new_intent_at + timedelta(seconds=1),
    })
    with case.database.session() as session:
        new = session.get(RunRecord, new_run_id)
        assert new is not None
        new.status = "completed"
        new.finished_at, new.updated_at = result.finished_at, result.finished_at
        ResearchObservabilityService(
            case.database, clock=lambda: result.finished_at,
        ).save_result_in_session(
            session, new_run_id, result,
        )
    with case.database.session() as session:
        assert validate_review_recovery_in_session(
            session, candidate, now=case.now,
        ) == "review_recovery_tracking_basis_inactive"
    assert _candidates(case) == ()


@pytest.mark.parametrize("damage", [
    "receipt_hash", "prompt", "task_hash", "execution_hash", "missing_attempts", "null_attempts",
    "different_cause", "nonretryable", "lease", "tool_running", "host_unknown", "host_cancelled",
    "result", "partial_result", "expired", "paused", "source_cancelled", "base_hash",
])
def test_missing_corrupt_or_unsafe_originals_are_explicitly_unrecoverable(
    tmp_path: Path, damage: str,
) -> None:
    case = _case(tmp_path)
    now = case.now
    with case.database.session() as session:
        run = session.get(RunRecord, case.failed_run_id)
        link = session.get(DshSessionLinkRecord, case.failed_run_id)
        assert run is not None and link is not None
        if damage == "receipt_hash":
            row = session.query(RunRoleOutputRecord).filter_by(
                run_id=run.run_id, role=terminal_receipt_role(1),
            ).one()
            row.payload_hash = "f" * 64
        elif damage == "prompt":
            prompt = session.get(DshSessionPromptRecord, (run.run_id, 1))
            assert prompt is not None
            prompt.prompt += " changed after execution"
        elif damage in {"task_hash", "execution_hash"}:
            role = "research_task_request" if damage == "task_hash" else "dsh_execution_request:1"
            row = session.query(RunRoleOutputRecord).filter_by(run_id=run.run_id, role=role).one()
            row.payload_hash = "f" * 64
        elif damage in {"missing_attempts", "null_attempts", "different_cause", "nonretryable"}:
            trace = session.query(ResearchTraceRecord).filter_by(run_id=run.run_id).one()
            error = json.loads(trace.error_provenance_json or "{}")
            if damage == "missing_attempts":
                del error["provider_attempts"]
            elif damage == "null_attempts":
                error["provider_attempts"] = None
            elif damage == "different_cause":
                error["cause_code"] = "provider_timeout"
            else:
                error["retryable"] = False
            trace.error_provenance_json = canonical_recipe(error)
            # Preserve a truthful new hash for semantic changes; retry eligibility
            # must still reject a non-narrow error, not merely a hash mismatch.
            if damage in {"different_cause", "nonretryable"}:
                semantic = ResearchTraceEvent.model_validate({
                    "schema_version": "research-trace-event.v1", "run_id": trace.run_id,
                    "research_session_id": trace.research_session_id, "sequence_no": 0,
                    "event_type": trace.event_type,
                    "occurred_at": trace.occurred_at.replace(tzinfo=NOW.tzinfo),
                    "stage": trace.stage, "summary": trace.summary,
                    "reference_type": trace.reference_type, "reference_id": trace.reference_id,
                    "status": trace.status, "error_code": trace.error_code, "error": error,
                })
                trace.event_hash = hashlib.sha256(canonical_recipe(semantic.model_dump(
                    mode="json", exclude={"sequence_no", "error_verification"},
                )).encode()).hexdigest()
        elif damage == "lease":
            run.lease_owner, run.lease_expires_at = "unconfirmed-worker", NOW - timedelta(days=1)
        elif damage == "tool_running":
            session.add(ResearchToolCallReservationRecord(
                run_id=run.run_id, request_id="pending-tool",
                research_session_id=link.dsh_session_id,
                generation=1, capability_id="replay.research", query_hash="a" * 64,
                status="running", reserved_at=NOW,
            ))
        elif damage in {"host_unknown", "host_cancelled"}:
            link.state = "unknown" if damage == "host_unknown" else "cancelled"
        elif damage == "result":
            ResearchObservabilityService(
                case.database, clock=lambda: case.now,
            ).save_result_in_session(
                session, run.run_id, _result(case.request, fail=True),
            )
        elif damage == "partial_result":
            link.result_ref, link.result_hash = "dsh://late/result", "a" * 64
        elif damage == "expired":
            now = NOW + timedelta(minutes=30)
        elif damage == "paused":
            study = session.get(StudyRecord, case.study_id)
            assert study is not None
            study.status = "paused"
        elif damage == "source_cancelled":
            run.status = "cancelled"
        else:
            plan = session.get(ReviewScheduleRecord, case.base_plan_id)
            assert plan is not None
            plan.trigger_hash = "0" * 64
    assert _candidates(case, now=now) == ()
    with case.database.session() as session:
        decisions = review_recovery_decisions_in_session(
            session, basis_run_id=case.parent_run_id, study_id=case.study_id, now=now,
        )
        assert len(decisions) == 1 and decisions[0].reason_code
        assert session.query(ReviewScheduleRecord).count() == 1
        assert session.query(RunRecord).count() == 2


@pytest.mark.parametrize("secret,header,expected", [
    ("receipt-secret", None, 401), ("receipt-secret", "wrong", 401), ("", "any", 503),
])
def test_terminal_endpoint_denies_unauthenticated_callbacks_without_writing(
    tmp_path: Path, secret: str, header: str | None, expected: int,
) -> None:
    case = _case(tmp_path, authenticated=False)
    app = create_app(case.database, source_connectors=[], dsh_callback_secret=secret)
    headers = {} if header is None else {"X-Decision-Hub-Bridge-Key": header}
    with TestClient(app) as client:
        response = client.put(
            f"/v1/dsh/sessions/{case.failed_run_id}/terminal",
            json=case.completion.model_dump(mode="json"), headers=headers,
        )
    assert response.status_code == expected
    with case.database.session() as session:
        assert session.query(RunRoleOutputRecord).filter_by(
            run_id=case.failed_run_id, role=terminal_receipt_role(1),
        ).count() == 0


def test_terminal_endpoint_wrong_identity_rolls_back_link_and_receipt(tmp_path: Path) -> None:
    case = _case(tmp_path, authenticated=False)
    app = create_app(case.database, source_connectors=[], dsh_callback_secret="receipt-secret")
    with TestClient(app) as client:
        response = client.put(
            f"/v1/dsh/sessions/{case.failed_run_id}/terminal",
            json=case.completion.model_copy(
                update={"dsh_session_id": "wrong-session"},
            ).model_dump(mode="json"),
            headers={"X-Decision-Hub-Bridge-Key": "receipt-secret"},
        )
    assert response.status_code == 409
    with case.database.session() as session:
        assert session.query(RunRoleOutputRecord).filter_by(
            run_id=case.failed_run_id, role=terminal_receipt_role(1),
        ).count() == 0
        link = session.get(DshSessionLinkRecord, case.failed_run_id)
        assert link is not None and link.state == "failed"
        assert link.dsh_session_id == case.completion.dsh_session_id


def test_authenticated_replay_is_immutable_and_enrichment_disables_recovery(tmp_path: Path) -> None:
    case = _case(tmp_path)
    before = _terminal_row(case.database, case.failed_run_id)
    links = DshSessionLinkService(case.database, clock=lambda: case.now)
    links.complete(case.completion, authenticated_host=True)
    assert _terminal_row(case.database, case.failed_run_id) == before
    assert case.completion.error is not None
    conflicting = case.completion.model_copy(update={
        "error": case.completion.error.model_copy(update={"message": "different original payload"}),
    })
    with pytest.raises(ValueError, match="dsh_authenticated_terminal_conflict"):
        links.complete(conflicting, authenticated_host=True)
    assert _terminal_row(case.database, case.failed_run_id) == before
    enriched = case.completion.model_copy(update={
        "trace_ref": "dsh://late/trace", "result_ref": "dsh://late/result", "result_hash": "b" * 64,
    })
    links.complete(enriched, authenticated_host=True)
    links.complete(enriched, authenticated_host=True)
    assert _terminal_row(case.database, case.failed_run_id) == before
    assert _candidates(case) == ()


def test_legal_legacy_authenticated_terminal_without_original_prompt_remains_compatible(
    tmp_path: Path,
) -> None:
    case = _case(tmp_path, authenticated=False)
    with case.database.session() as session:
        session.query(DshSessionPromptRecord).filter_by(run_id=case.failed_run_id).delete()
        session.query(RunRoleOutputRecord).filter(
            RunRoleOutputRecord.run_id == case.failed_run_id,
        ).delete()
    app = create_app(case.database, source_connectors=[], dsh_callback_secret="receipt-secret")
    app.state.dsh_sessions.clock = lambda: case.now
    with TestClient(app) as client:
        response = client.put(
            f"/v1/dsh/sessions/{case.failed_run_id}/terminal",
            json=case.completion.model_dump(mode="json"),
            headers={"X-Decision-Hub-Bridge-Key": "receipt-secret"},
        )
    assert response.status_code == 200, response.text
    body, _ = _terminal_row(case.database, case.failed_run_id)
    assert json.loads(body)["prompt_hash"] is None
    assert json.loads(body)["request_id"] is None
    assert _candidates(case) == ()
    with case.database.session() as session:
        with pytest.raises(ValueError, match="dsh_terminal_original_prompt_missing"):
            read_authenticated_terminal_in_session(session, case.failed_run_id, now=case.now)
        assert session.get(ResearchResultRecord, case.failed_run_id) is None


def test_scheduler_admits_one_new_retry_run_and_parent_audit_from_authenticated_failure(
    tmp_path: Path,
) -> None:
    case = _case(tmp_path, via_api=True, legacy_plan=True)
    original_receipt = _terminal_row(case.database, case.failed_run_id)
    candidate = _candidates(case)[0]
    with case.database.session() as session:
        failed = session.get(RunRecord, case.failed_run_id)
        original_plan = session.get(ReviewScheduleRecord, case.base_plan_id)
        assert failed is not None and original_plan is not None
        failed_before = (failed.status, failed.finished_at, failed.error_code, failed.cost_usd)
        plan_before = (
            original_plan.status, original_plan.child_run_id, original_plan.failure_code,
            original_plan.trigger_at, original_plan.updated_at,
        )
    scheduler = ReviewScheduleService(case.database, clock=lambda: case.now)
    admitted = scheduler.plan_due()
    assert len(admitted) == 1
    new_child_id, new_plan_id = admitted[0].child_run_id, admitted[0].review_plan_id
    assert new_child_id != case.failed_run_id and new_plan_id != case.base_plan_id
    assert scheduler.plan_due() == ()
    with case.database.session() as session:
        retries = session.query(ReviewScheduleRecord).filter_by(
            parent_run_id=case.parent_run_id, trigger_ref=candidate.trigger_ref,
        ).all()
        assert len(retries) == 1
        retry = retries[0]
        assert retry.status == "admitted" and retry.child_run_id == new_child_id
        assert retry.review_plan_id == new_plan_id
        assert retry.trigger_at.replace(tzinfo=NOW.tzinfo) == candidate.trigger_at
        child = session.get(RunRecord, new_child_id)
        assert child is not None and child.status == "admitted"
        assert child.admission_origin == "scheduled_recheck"
        assert child.parent_run_id == case.parent_run_id
        assert child.idempotency_key == f"research-review-plan:{new_plan_id}"
        assert child.available_at is not None
        assert child.available_at.replace(tzinfo=NOW.tzinfo) == case.now
        assert session.get(DshSessionLinkRecord, new_child_id) is None
        assert session.query(RunRecord).count() == 3
        events = session.query(RunEventRecord).filter_by(run_id=case.parent_run_id).order_by(
            RunEventRecord.sequence_no,
        ).all()
        relevant = [(row.event_type, json.loads(row.payload_json)) for row in events
                    if json.loads(row.payload_json).get("review_plan_id") == new_plan_id]
        assert [kind for kind, _ in relevant] == [
            "research.scheduled_recheck.recovery_planned", "research.scheduled_recheck.admitted",
        ]
        planned_payload, admitted_payload = relevant[0][1], relevant[1][1]
        assert planned_payload["base_plan_id"] == case.base_plan_id
        assert planned_payload["source_failed_run_id"] == case.failed_run_id
        assert planned_payload["attempt_no"] == 1
        assert planned_payload["terminal_receipt_hash"] == original_receipt[1]
        assert admitted_payload["child_run_id"] == new_child_id
        failed = session.get(RunRecord, case.failed_run_id)
        original_plan = session.get(ReviewScheduleRecord, case.base_plan_id)
        assert failed is not None and original_plan is not None
        assert (
            failed.status, failed.finished_at, failed.error_code, failed.cost_usd,
        ) == failed_before
        assert (
            original_plan.status, original_plan.child_run_id, original_plan.failure_code,
            original_plan.trigger_at, original_plan.updated_at,
        ) == plan_before
    assert _terminal_row(case.database, case.failed_run_id) == original_receipt


def test_scheduler_keeps_backoff_planned_then_rejects_changed_proof_before_admission(
    tmp_path: Path,
) -> None:
    case = _case(tmp_path, via_api=True, legacy_plan=True)
    candidate = _candidates(case)[0]
    clock_values = [candidate.trigger_at - timedelta(microseconds=1)]
    scheduler = ReviewScheduleService(case.database, clock=lambda: clock_values[0])
    assert scheduler.plan_due() == ()
    with case.database.session() as session:
        retry = session.query(ReviewScheduleRecord).filter_by(
            parent_run_id=case.parent_run_id, trigger_ref=candidate.trigger_ref,
        ).one()
        assert retry.status == "planned" and retry.child_run_id is None
        assert session.query(RunRecord).count() == 2
        receipt = session.query(RunRoleOutputRecord).filter_by(
            run_id=case.failed_run_id, role=terminal_receipt_role(1),
        ).one()
        receipt.payload_hash = "0" * 64
        new_plan_id = retry.review_plan_id
    damaged_receipt = _terminal_row(case.database, case.failed_run_id)
    clock_values[0] = candidate.trigger_at
    assert scheduler.plan_due() == ()
    assert scheduler.plan_due() == ()
    with case.database.session() as session:
        retry = session.get(ReviewScheduleRecord, new_plan_id)
        failed = session.get(RunRecord, case.failed_run_id)
        original_plan = session.get(ReviewScheduleRecord, case.base_plan_id)
        assert retry is not None and failed is not None and original_plan is not None
        assert retry.status == "skipped" and retry.child_run_id is None
        assert retry.failure_code == "review_recovery_proof_unavailable"
        assert failed.status == "failed" and failed.error_code == RECOVERABLE_ERROR
        assert original_plan.status == "failed" and original_plan.child_run_id == case.failed_run_id
        assert session.query(RunRecord).count() == 2
        assert session.query(ReviewScheduleRecord).filter_by(
            trigger_ref=candidate.trigger_ref,
        ).count() == 1
        events = session.query(RunEventRecord).filter_by(run_id=case.parent_run_id).order_by(
            RunEventRecord.sequence_no,
        ).all()
        relevant = [row.event_type for row in events
                    if json.loads(row.payload_json).get("review_plan_id") == new_plan_id]
        assert relevant == [
            "research.scheduled_recheck.recovery_planned", "research.scheduled_recheck.skipped",
        ]
    assert _terminal_row(case.database, case.failed_run_id) == damaged_receipt

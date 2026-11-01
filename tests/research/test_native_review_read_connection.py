# pyright: reportPrivateUsage=false
from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Lock

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Connection
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from packages.contracts_py.decision_hub_contracts import (
    ResearchReview,
    ResearchSessionResult,
    ResearchTraceEvent,
)
from packages.kernel.decision_hub_kernel.application.research_observability import (
    ResearchObservabilityService,
)
from packages.kernel.decision_hub_kernel.application.research_review import (
    independent_review_receipt_hash,
    research_result_content_hash,
)
from packages.kernel.decision_hub_kernel.application.research_task import ResearchTaskService
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    ResearchResultRecord,
    RunRecord,
    RunRoleOutputRecord,
)
from packages.query_views.research import ResearchQueryService
from packages.query_views.research.service import _durable_native_review_matches
from tests.evolution.test_research_worker import (
    NOW,
    PACK_ROOT,
    ResearchRequestFactory,
    _admitted_run,
    _result,
)


def _saved_native_review(tmp_path: Path) -> tuple[Database, str, ResearchSessionResult]:
    database, event_id, run_id = _admitted_run(tmp_path)
    request = ResearchRequestFactory(database, pack_root=PACK_ROOT, clock=lambda: NOW).build(
        event_id, run_id
    )
    ResearchTaskService(database, clock=lambda: NOW).freeze(request)
    base = _result(request)
    base = base.model_copy(update={
        "evidence_candidates": [
            item.model_copy(update={"quality": "accepted", "freshness_status": "fresh"})
            for item in base.evidence_candidates
        ],
        "final_coverage": base.final_coverage.model_copy(update={
            "status": "sufficient", "covered_requirement_ids": ["event_identity"],
            "hard_coverage_ratio": 1.0,
        }),
    })
    assert base.causal_case is not None
    review_body = {
        "role": "independent_reviewer",
        "target_claim": base.causal_case.thesis,
        "evidence_refs": [base.evidence_candidates[0].evidence_id],
        "challenge": "The independent source confirms the bounded event claim.",
        "resolution": "accepted",
        "limitations": [],
    }
    digest = independent_review_receipt_hash(
        role="independent_reviewer", target_claim=base.causal_case.thesis,
        evidence_refs=[base.evidence_candidates[0].evidence_id],
        challenge="The independent source confirms the bounded event claim.",
        resolution="accepted", limitations=[],
    )
    review = ResearchReview.model_validate({
        **review_body, "review_id": f"review_{digest[:32]}",
        "result_hash": research_result_content_hash(base), "created_at": NOW,
    })
    result = base.model_copy(update={"reviews": [review]})
    database.save_role_output(
        run_id, "independent_reviewer", review.model_dump(mode="json"),
        "research-review.native-attested.v1",
    )
    with database.session() as session:
        ResearchObservabilityService(database, clock=lambda: NOW).save_result_in_session(
            session, run_id, result
        )
        run = session.get(RunRecord, run_id)
        assert run is not None
        run.status = "completed"
        run.finished_at = result.finished_at
        run.updated_at = result.finished_at
    return database, run_id, result


def _single_connection(database: Database) -> None:
    url = database.engine.url
    database.engine.dispose()
    database.engine = create_engine(
        url, connect_args={"check_same_thread": False},
        pool_size=1, max_overflow=0, pool_timeout=2,
    )
    database.factory.configure(bind=database.engine)


def test_process_projection_orders_review_after_synthesis_and_runs_gate_only_at_gate_stage(
    tmp_path: Path,
) -> None:
    (tmp_path / "completed").mkdir()
    completed_database, completed_run_id, _result = _saved_native_review(
        tmp_path / "completed"
    )
    try:
        completed = ResearchQueryService(
            completed_database, pack_root=PACK_ROOT
        ).get(completed_run_id)
        assert completed is not None
        assert [step.kind for step in completed.process_steps] == [
            "intake",
            "plan",
            "acquire",
            "assess",
            "synthesize",
            "review",
            "replan",
            "gate",
        ]
    finally:
        completed_database.engine.dispose()

    (tmp_path / "running").mkdir()
    running_database, _event_id, running_run_id = _admitted_run(tmp_path / "running")
    try:
        query = ResearchQueryService(running_database, pack_root=PACK_ROOT)
        before = query.get(running_run_id)
        assert before is not None
        assert next(step for step in before.process_steps if step.kind == "gate").status == "waiting"

        ResearchObservabilityService(running_database, clock=lambda: NOW).append_trace(
            ResearchTraceEvent.model_validate(
                {
                    "schema_version": "research-trace-event.v1",
                    "run_id": running_run_id,
                    "research_session_id": f"product:{running_run_id}",
                    "sequence_no": 0,
                    # Projection uses the saved stage rather than guessing from
                    # the event type; this is the minimal explicit gate-stage
                    # control without starting a Worker or API service.
                    "event_type": "model_step_started",
                    "occurred_at": NOW,
                    "stage": "gate",
                    "summary": "Gate execution started.",
                    "reference_type": "run",
                    "reference_id": running_run_id,
                    "status": "running",
                    "error_code": None,
                }
            )
        )
        during = query.get(running_run_id)
        assert during is not None
        assert next(step for step in during.process_steps if step.kind == "gate").status == "running"
    finally:
        running_database.engine.dispose()


def test_detail_reads_native_review_once_with_concurrent_single_connection_pool(
    tmp_path: Path,
) -> None:
    database, run_id, result = _saved_native_review(tmp_path)
    _single_connection(database)
    query = ResearchQueryService(database, pack_root=PACK_ROOT)
    assert _durable_native_review_matches(database, run_id, result)

    # The former production shape holds the first connection and needs a second
    # for the review; demonstrate the actual pool timeout rather than mocking it.
    with database.session() as held:
        assert held.get(RunRecord, run_id) is not None
        with pytest.raises(PoolTimeoutError):
            _durable_native_review_matches(database, run_id, result)

    review_reads = []
    reads_lock = Lock()

    def record_review_read(
        conn: Connection,
        cursor: object,
        statement: str,
        parameters: tuple[object, ...] | dict[str, object],
        context: object,
        executemany: bool,
    ) -> None:
        if "FROM run_role_outputs" in statement and "independent_reviewer" in parameters:
            with reads_lock:
                review_reads.append(1)

    event.listen(database.engine, "before_cursor_execute", record_review_read)
    ready = Barrier(4)

    def read_detail():
        ready.wait(timeout=5)
        detail = query.get(run_id)
        assert detail is not None
        review = next(step for step in detail.process_steps if step.step_id == "review")
        assert review.status == "completed"
        assert "independent_review_not_accepted" not in detail.action_summary.warning_codes
        assert detail.reviews == result.reviews
        return detail

    try:
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(read_detail) for _ in range(4)]
            details = [future.result(timeout=10) for future in futures]
        assert all(detail == details[0] for detail in details)
        assert len(review_reads) == 4
    finally:
        event.remove(database.engine, "before_cursor_execute", record_review_read)
        database.engine.dispose()


@pytest.mark.parametrize("damage", [
    "missing", "automated", "unknown_schema", "payload_hash", "payload_mismatch",
    "malformed_payload", "result_hash", "review_identity", "result_reviews_missing",
])
def test_detail_does_not_accept_unverified_native_review(tmp_path: Path, damage: str) -> None:
    database, run_id, result = _saved_native_review(tmp_path)
    with database.session() as session:
        row = session.query(RunRoleOutputRecord).filter_by(
            run_id=run_id, role="independent_reviewer"
        ).one()
        if damage == "missing":
            session.delete(row)
        elif damage in {"automated", "unknown_schema"}:
            row.schema_version = (
                "research-review.automated-check.v1" if damage == "automated"
                else "research-review.unknown.v1"
            )
        elif damage == "payload_hash":
            row.payload_hash = "0" * 64
        elif damage in {"payload_mismatch", "malformed_payload"}:
            body = json.loads(row.payload_json)
            body["challenge"] = "A different original review."
            row.payload_json = json.dumps(body) if damage == "payload_mismatch" else "{"
            row.payload_hash = hashlib.sha256(row.payload_json.encode("utf-8")).hexdigest()
        elif damage == "result_reviews_missing":
            result_row = session.get(ResearchResultRecord, run_id)
            assert result_row is not None
            changed = result.model_copy(update={"reviews": None})
            result_row.payload_json = changed.model_dump_json()
            result_row.payload_hash = hashlib.sha256(
                result_row.payload_json.encode("utf-8")
            ).hexdigest()
        else:
            assert result.reviews
            field = "result_hash" if damage == "result_hash" else "review_id"
            wrong = "0" * 64 if field == "result_hash" else "review_wrong_identity"
            review = result.reviews[0].model_copy(update={field: wrong})
            changed = result.model_copy(update={"reviews": [review]})
            result_row = session.get(ResearchResultRecord, run_id)
            assert result_row is not None
            result_row.payload_json = changed.model_dump_json()
            result_row.payload_hash = hashlib.sha256(
                result_row.payload_json.encode("utf-8")
            ).hexdigest()
            # Even a matching durable envelope cannot grant a bad result hash
            # or review identity the accepted-review status.
            row.payload_json = review.model_dump_json()
            row.payload_hash = hashlib.sha256(row.payload_json.encode("utf-8")).hexdigest()
    try:
        detail = ResearchQueryService(database, pack_root=PACK_ROOT).get(run_id)
        assert detail is not None
        review = next(step for step in detail.process_steps if step.step_id == "review")
        assert review.status == "limited"
        assert "independent_review_not_accepted" in detail.action_summary.warning_codes
    finally:
        database.engine.dispose()

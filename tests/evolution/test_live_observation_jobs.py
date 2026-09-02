from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from packages.contracts_py.decision_hub_contracts.models import EvolutionJobCreate, EvolutionJobView
from packages.kernel.decision_hub_kernel.application.evolution import EvolutionAssetService
from packages.kernel.decision_hub_kernel.application.live_observation import (
    EvolutionJobPolicy,
    EvolutionJobService,
    EvolutionTriggerScanner,
    ServiceHeartbeatService,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    EvaluationRecord,
    FailurePatternRecord,
    FeedbackRecord,
    PromotionDecisionRecord,
)

NOW = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)


def job_request(trigger_key: str = "feedback:feedback-1") -> EvolutionJobCreate:
    return EvolutionJobCreate(
        trigger_key=trigger_key,
        trigger_type="feedback",
        domain_pack_ref="crypto_macro.v1",
        input_refs=["feedback:feedback-1"],
        max_attempts=3,
    )


def test_enqueue_is_idempotent_and_rejects_trigger_reuse(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'jobs.sqlite3'}")
    database.create_all()
    jobs = EvolutionJobService(database, clock=lambda: NOW)

    first = jobs.enqueue(job_request())
    second = jobs.enqueue(job_request())

    assert first == second
    assert first.status == "queued"
    assert first.stage == "discover"

    try:
        jobs.enqueue(job_request().model_copy(update={"input_refs": ["feedback:other"]}))
    except ValueError as exc:
        assert str(exc) == "evolution_trigger_reused"
    else:  # pragma: no cover - assertion helper
        raise AssertionError("changed trigger payload must be rejected")


def test_claim_is_atomic_and_expired_lease_is_recoverable(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'claim.sqlite3'}")
    database.create_all()
    current = NOW
    jobs = EvolutionJobService(database, clock=lambda: current)
    created = jobs.enqueue(job_request())

    def claim(worker_id: str):
        return EvolutionJobService(database, clock=lambda: current).claim(
            worker_id, lease_seconds=30
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        claimed = list(executor.map(claim, ("worker-a", "worker-b")))

    winners = [item for item in claimed if item is not None]
    assert len(winners) == 1
    assert winners[0].job_id == created.job_id
    assert winners[0].attempt == 1

    current = NOW + timedelta(seconds=31)
    recovered = jobs.claim("worker-c", lease_seconds=30)
    assert recovered is not None
    assert recovered.job_id == created.job_id
    assert recovered.lease_owner == "worker-c"
    assert recovered.attempt == 2


def test_only_current_lease_owner_can_renew(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'renew.sqlite3'}")
    database.create_all()
    current = NOW
    jobs = EvolutionJobService(database, clock=lambda: current)
    created = jobs.enqueue(job_request())
    claimed = jobs.claim("worker-a", lease_seconds=30)
    assert claimed is not None

    current = NOW + timedelta(seconds=10)
    renewed = jobs.renew(created.job_id, "worker-a", lease_seconds=60)
    assert renewed.lease_expires_at == current + timedelta(seconds=60)
    with pytest.raises(PermissionError, match="evolution_job_lease_not_owned"):
        jobs.renew(created.job_id, "worker-b", lease_seconds=60)


def test_failure_retry_and_owner_review_never_promote(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'transitions.sqlite3'}")
    database.create_all()
    current = NOW
    jobs = EvolutionJobService(database, clock=lambda: current)
    jobs.enqueue(job_request())
    claimed = jobs.claim("worker-a", lease_seconds=30)
    assert claimed is not None

    retry = jobs.fail(
        claimed.job_id,
        "worker-a",
        error_code="provider_timeout",
        retryable=True,
        backoff_seconds=60,
    )
    assert retry.status == "retry_wait"
    assert retry.next_attempt_at == NOW + timedelta(seconds=60)
    assert jobs.claim("worker-b", lease_seconds=30) is None

    current = NOW + timedelta(seconds=61)
    claimed_again = jobs.claim("worker-b", lease_seconds=30)
    assert claimed_again is not None
    review = jobs.advance(
        claimed_again.job_id,
        "worker-b",
        stage="review",
        status="pending_owner_review",
        candidate_id="candidate-1",
        experiment_refs=["experiment-replay", "experiment-holdout", "experiment-shadow"],
        result_refs=["result-replay", "result-holdout", "result-shadow"],
    )
    assert review.status == "pending_owner_review"
    assert review.finished_at is None
    assert jobs.claim("worker-c", lease_seconds=30) is None

    other = jobs.enqueue(job_request("failure:pattern-1:3"))
    current = NOW + timedelta(seconds=62)
    failed_claim = jobs.claim("worker-c", lease_seconds=30)
    assert failed_claim is not None and failed_claim.job_id == other.job_id
    failed = jobs.fail(
        failed_claim.job_id,
        "worker-c",
        error_code="evaluation_leakage",
        retryable=False,
    )
    assert failed.status == "failed"
    assert failed.finished_at == current


def test_owner_decision_completes_pending_review(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'owner-review.sqlite3'}")
    database.create_all()
    current = NOW
    jobs = EvolutionJobService(database, clock=lambda: current)
    created = jobs.enqueue(job_request())
    claimed = jobs.claim("worker-a", lease_seconds=30)
    assert claimed is not None
    jobs.advance(
        created.job_id,
        "worker-a",
        stage="review",
        status="pending_owner_review",
        candidate_id="candidate-1",
    )
    with database.session() as session:
        session.add(
            PromotionDecisionRecord(
                decision_id="decision-1",
                request_id="owner-decision-1",
                domain_pack_ref="crypto_macro.v1",
                candidate_id="candidate-1",
                owner="owner",
                decision="reject",
                reason="holdout evidence is insufficient",
                evaluation_refs_json="[]",
                previous_candidate_id=None,
                resulting_candidate_id=None,
                resulting_generation=0,
                created_at=current,
            )
        )

    current = NOW + timedelta(minutes=1)
    assert jobs.settle_owner_reviews() == 1
    completed = jobs.list_jobs()[0]
    assert completed.status == "completed"
    assert completed.finished_at == current


def _insert_feedback(database: Database, feedback_id: str = "feedback-1") -> None:
    with database.session() as session:
        session.add(
            FeedbackRecord(
                feedback_id=feedback_id,
                request_id=f"request-{feedback_id}",
                target_type="run",
                target_id="run-1",
                created_by="owner",
                verdict="incorrect",
                notes="The causal chain missed the policy transmission mechanism.",
                created_at=NOW,
            )
        )


def _scanner(
    database: Database,
    *,
    failure_threshold: int = 2,
    evaluation_batch_size: int = 10,
    scheduled: bool = False,
) -> EvolutionTriggerScanner:
    return EvolutionTriggerScanner(
        database,
        EvolutionJobService(database, clock=lambda: NOW),
        EvolutionAssetService(database, clock=lambda: NOW),
        policy=EvolutionJobPolicy(
            failure_occurrence_threshold=failure_threshold,
            evaluation_batch_size=evaluation_batch_size,
            scheduled_scan_enabled=scheduled,
        ),
        clock=lambda: NOW,
    )


def test_trigger_scan_empty_database_is_noop(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'empty-scan.sqlite3'}")
    database.create_all()

    assert _scanner(database).scan() == []


def test_feedback_scan_is_idempotent_across_two_scanners(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'feedback-scan.sqlite3'}")
    database.create_all()
    _insert_feedback(database)

    def scan(_: int) -> list[EvolutionJobView]:
        return _scanner(database).scan()

    with ThreadPoolExecutor(max_workers=2) as executor:
        scans = list(executor.map(scan, range(2)))

    assert all(any(job.trigger_key == "feedback:feedback-1" for job in scan) for scan in scans)
    jobs = EvolutionJobService(database).list_jobs()
    assert [job.trigger_key for job in jobs].count("feedback:feedback-1") == 1


def test_failure_pattern_requires_threshold_or_regression(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'failure-scan.sqlite3'}")
    database.create_all()
    with database.session() as session:
        session.add(
            FailurePatternRecord(
                pattern_id="pattern-1",
                failure_code="missing_transmission_chain",
                occurrence_count=1,
                impact="weak decision evidence",
                source_refs_json=json.dumps(["run:run-1"]),
                remediation_refs_json="[]",
                status="open",
                updated_at=NOW,
            )
        )

    assert _scanner(database, failure_threshold=2).scan() == []
    with database.session() as session:
        row = session.get(FailurePatternRecord, "pattern-1")
        assert row is not None
        row.occurrence_count = 2

    jobs = _scanner(database, failure_threshold=2).scan()
    assert [job.trigger_key for job in jobs] == ["failure_pattern:pattern-1:2"]


def test_evaluation_batch_and_scheduled_watermark_are_deterministic(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'batch-scan.sqlite3'}")
    database.create_all()
    with database.session() as session:
        for index in range(2):
            session.add(
                EvaluationRecord(
                    evaluation_id=f"evaluation-{index}",
                    forecast_id=f"forecast-{index}",
                    brier_score=0.1 + index / 100,
                    net_return_pct=0.5,
                    direction_correct=True,
                    label_status="observed",
                    evaluated_at=NOW + timedelta(seconds=index),
                )
            )
        session.add(
            FailurePatternRecord(
                pattern_id="scheduled-pattern",
                failure_code="scheduled-only",
                occurrence_count=1,
                impact="new evidence",
                source_refs_json=json.dumps(["run:scheduled-1"]),
                remediation_refs_json="[]",
                status="open",
                updated_at=NOW,
            )
        )

    first = _scanner(
        database,
        failure_threshold=99,
        evaluation_batch_size=2,
        scheduled=True,
    ).scan()
    assert {job.trigger_type for job in first} == {"evaluation_batch", "scheduled"}

    second = _scanner(
        database,
        failure_threshold=99,
        evaluation_batch_size=2,
        scheduled=True,
    ).scan()
    assert [job.trigger_type for job in second] == ["evaluation_batch"]
    assert len(EvolutionJobService(database).list_jobs()) == 2


def test_heartbeat_status_is_derived_from_age(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'heartbeats.sqlite3'}")
    database.create_all()
    current = NOW
    heartbeats = ServiceHeartbeatService(database, clock=lambda: current)
    heartbeats.beat(
        service_id="realtime",
        role="realtime_worker",
        instance_id="worker-a",
        version="0.1.0",
        mode="fake",
        interval_seconds=10,
    )
    assert heartbeats.list_views()[0].status == "online"

    current = NOW + timedelta(seconds=25)
    assert heartbeats.list_views()[0].status == "stale"

    current = NOW + timedelta(seconds=51)
    assert heartbeats.list_views()[0].status == "offline"

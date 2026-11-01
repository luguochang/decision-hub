from datetime import UTC, datetime

import pytest

from packages.contracts_py.decision_hub_contracts.models import EvolutionJobCreate
from packages.kernel.decision_hub_kernel.application.evolution import EvolutionAssetService
from packages.kernel.decision_hub_kernel.application.evolution_context import (
    EvolutionContextService,
)
from packages.kernel.decision_hub_kernel.application.live_observation import EvolutionJobService
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    EvolutionContextSnapshotRecord,
    RunRecord,
)

NOW = datetime(2026, 10, 1, tzinfo=UTC)


def setup_case(tmp_path, runs):
    db = Database(f"sqlite+pysqlite:///{tmp_path / 'context.sqlite3'}")
    db.create_all()
    EvolutionAssetService(db).ensure_release_baseline("crypto_macro.v1")
    with db.session() as session:
        for identity, status, error in runs:
            session.add(
                RunRecord(
                    run_id=identity,
                    event_id="event",
                    status=status,
                    error_code=error,
                    created_at=NOW,
                    updated_at=NOW,
                    runtime_version="legacy.unknown",
                )
            )
    jobs = EvolutionJobService(db)
    job = jobs.enqueue(
        EvolutionJobCreate(
            trigger_key="scheduled:test",
            trigger_type="scheduled",
            domain_pack_ref="crypto_macro.v1",
            input_refs=[f"run:{r[0]}" for r in runs],
            max_attempts=3,
        )
    )
    return db, jobs, job


def test_all_refs_resolve_cancelled_are_excluded_and_failures_are_grouped(tmp_path):
    db, _, job = setup_case(
        tmp_path,
        [
            ("a", "failed", "provider_timeout"),
            ("b", "failed", "provider_timeout"),
            ("c", "cancelled", "scheduled_recheck_superseded"),
        ],
    )
    service = EvolutionContextService(db)
    context = service.build(job)
    assert context.evidence_refs == ["run:a"]
    assert '"run_count":2' in context.context_items[0]
    assert "provider_timeout" in context.context_items[0]
    body = service.read(job.job_id)
    assert len(body["input_dispositions"]) == 3
    assert body["counts"] == {"included_failure_group": 2, "cancelled_not_method_failure": 1}
    assert body["input_dispositions"][1]["group_reference"] == "run:a"
    with db.session() as session:
        session.get(RunRecord, "a").error_code = "changed-after-freeze"
    assert EvolutionContextService(db).build(job) == context


def test_no_actionable_refs_do_not_become_invented_facts(tmp_path):
    db, _, job = setup_case(tmp_path, [("cancelled", "cancelled", "cancelled_by_owner")])
    job = job.model_copy(update={"input_refs": [*job.input_refs, "run:missing", "unknown:x"]})
    context = EvolutionContextService(db).build(job)
    assert context.evidence_refs == []
    assert all("referenced fact" not in item for item in context.context_items)
    assert EvolutionContextService(db).read(job.job_id)["counts"]["missing_record"] == 1


def test_corrupt_or_changed_input_is_rejected_and_legacy_candidate_not_backfilled(tmp_path):
    db, _, job = setup_case(tmp_path, [("a", "failed", "provider_timeout")])
    service = EvolutionContextService(db)
    with pytest.raises(ValueError, match="legacy_unrecorded"):
        service.build(job.model_copy(update={"candidate_id": "old-candidate"}))
    assert service.read(job.job_id) is None
    service.build(job)
    with pytest.raises(ValueError, match="input_changed"):
        service.build(job.model_copy(update={"input_refs": ["run:other"]}))
    with db.session() as session:
        session.get(EvolutionContextSnapshotRecord, job.job_id).content_json = "{}"
    with pytest.raises(ValueError, match="snapshot_corrupt"):
        service.build(job)


def test_budget_refuses_instead_of_dropping_unprocessed_groups(tmp_path):
    db, _, job = setup_case(tmp_path, [(str(i), "failed", f"error_{i}") for i in range(33)])
    service = EvolutionContextService(db)
    with pytest.raises(ValueError, match="budget_exceeded"):
        service.build(job)
    body = service.read(job.job_id)
    assert len(body["input_dispositions"]) == 33
    assert body["summary_count"] == 33


def test_recovery_is_new_idempotent_job_and_preserves_original_failure(tmp_path):
    db, jobs, original = setup_case(tmp_path, [("a", "failed", "provider_timeout")])
    jobs.claim("worker", lease_seconds=120)
    failed = jobs.fail(
        original.job_id, "worker", error_code="research_coverage_missing", retryable=False
    )
    recovery = jobs.enqueue_context_recovery(original.job_id)
    assert jobs.enqueue_context_recovery(original.job_id) == recovery
    assert recovery.job_id != original.job_id and recovery.input_refs == original.input_refs
    assert next(x for x in jobs.list_jobs() if x.job_id == original.job_id) == failed
    EvolutionContextService(db).build(recovery)
    assert EvolutionContextService(db).read(recovery.job_id)["recovery_of"] == original.job_id


def test_duplicate_references_keep_disposition_without_double_counting(tmp_path):
    db, _, job = setup_case(tmp_path, [("a", "failed", "provider_timeout")])
    context_job = job.model_copy(update={"input_refs": ["run:a", "run:a"]})
    context = EvolutionContextService(db).build(context_job)
    assert context.evidence_refs == ["run:a"]
    counts = EvolutionContextService(db).read(job.job_id)["counts"]
    assert counts == {"included_failure_group": 1, "duplicate_reference": 1}


def test_cancelled_only_job_finishes_without_dispatching_model(tmp_path, monkeypatch):
    import asyncio

    from apps.hub_worker.composition import build_evolution_worker
    from packages.kernel.decision_hub_kernel.application.live_observation import EvolutionJobPolicy
    from packages.runtime_adapters.fake_runtime.runtime import FakeAgentRuntime

    db, _, job = setup_case(tmp_path, [("a", "cancelled", "cancelled_by_owner")])

    class NoDispatch(FakeAgentRuntime):
        async def execute(self, request):
            raise AssertionError("no actionable input must not dispatch a model")

    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "artifacts"))
    worker = build_evolution_worker(
        db,
        runtime=NoDispatch(),
        planning_runtime=NoDispatch(),
        policy=EvolutionJobPolicy(scheduled_scan_enabled=False, failure_occurrence_threshold=999),
    )
    result = asyncio.run(worker.executor.tick("worker"))
    assert result.job_id == job.job_id and result.status == "completed"
    assert result.candidate_id is None
    assert EvolutionContextService(db).read(job.job_id)["counts"] == {
        "cancelled_not_method_failure": 1
    }


def test_context_http_summary_and_opt_in_full_input_ledger(tmp_path):
    from fastapi.testclient import TestClient

    from apps.hub_api.main import create_app

    db, _, job = setup_case(tmp_path, [("a", "failed", "provider_timeout")])
    client = TestClient(create_app(db))
    path = f"/v1/evolution/jobs/{job.job_id}/planning-context"
    assert client.get(path).json() is None
    EvolutionContextService(db).build(job)
    summary = client.get(path).json()
    assert summary["input_count"] == 1 and summary["summary_count"] == 1
    assert "input_dispositions" not in summary
    assert (
        client.get(path + "?include_inputs=true").json()["input_dispositions"][0]["record"][
            "error_code"
        ]
        == "provider_timeout"
    )
    assert client.get("/v1/evolution/jobs/missing/planning-context").status_code == 404

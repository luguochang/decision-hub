import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest

from packages.kernel.decision_hub_kernel.application.live_observation import EvolutionJobService
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError, AgentRequest
from packages.orchestration.langgraph.evolution_diagnostics import (
    AuditedPlanningRuntime,
    planning_calls,
)
from packages.runtime_adapters.fake_runtime.runtime import FakeAgentRuntime
from tests.evolution.test_live_observation_jobs import job_request


@pytest.fixture
def audit_case(tmp_path):
    db = Database(f"sqlite+pysqlite:///{tmp_path / 'audit.sqlite3'}")
    db.create_all()
    jobs = EvolutionJobService(db)
    job = jobs.enqueue(job_request())
    jobs.claim("test", lease_seconds=120)
    request = AgentRequest(
        role="counter_thesis",
        text="private prompt should not persist",
        evidence=("private evidence",),
        deadline_at=datetime.now(UTC) + timedelta(seconds=60),
    )
    return db, job, request


def test_success_is_visible_before_dispatch_and_survives_new_reader(audit_case):
    db, job, request = audit_case

    class Runtime(FakeAgentRuntime):
        async def execute(self, req):
            assert planning_calls(db, job.job_id)[0]["status"] == "running"
            return await super().execute(req)

    asyncio.run(AuditedPlanningRuntime(db, Runtime(), job.job_id).execute(request))
    row = planning_calls(db, job.job_id)[0]
    assert row["status"] == "succeeded" and row["job_attempt"] == 1
    assert row["started_at"] <= row["finished_at"]
    assert len(row["details"]["request_hash"]) == len(row["details"]["result_hash"]) == 64
    assert row["details"]["evidence_count"] == 1
    assert "private" not in json.dumps(row)


def test_failure_records_safe_classification_without_provider_body(audit_case):
    db, job, request = audit_case

    class ProviderFailure(Exception):
        status_code = 400
        body = {
            "error": {"code": "invalid_json_schema", "message": "secret-key", "param": "secret-key"}
        }

    class Runtime:
        async def execute(self, _request):
            raise AgentExecutionError(
                "structured_output_invalid", "secret-key"
            ) from ProviderFailure()

    with pytest.raises(AgentExecutionError):
        asyncio.run(AuditedPlanningRuntime(db, Runtime(), job.job_id).execute(request))
    row = planning_calls(db, job.job_id)[0]
    assert row["status"] == "failed"
    assert row["details"]["http_status"] == 400
    assert row["details"]["provider_error_code"] == "invalid_json_schema"
    assert "secret-key" not in json.dumps(row)


def test_cancelled_and_unconfirmed_calls_are_not_success(audit_case, monkeypatch):
    db, job, request = audit_case

    class Cancelled:
        async def execute(self, _request):
            raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(AuditedPlanningRuntime(db, Cancelled(), job.job_id).execute(request))
    assert planning_calls(db, job.job_id)[0]["status"] == "cancelled"
    runtime = AuditedPlanningRuntime(db, FakeAgentRuntime(), job.job_id)

    def interrupted(*args):
        raise RuntimeError("simulate process dying before final write")

    monkeypatch.setattr(runtime, "_finish", interrupted)
    with pytest.raises(RuntimeError):
        asyncio.run(runtime.execute(request))
    rows = planning_calls(db, job.job_id)
    assert rows[0]["status"] == "running" and rows[0]["finished_at"] is None
    assert rows[1]["status"] == "cancelled"


def test_unknown_errors_are_redacted_and_calls_are_bounded(audit_case):
    db, job, request = audit_case

    class Unknown:
        async def execute(self, _request):
            raise AgentExecutionError("sk-private-secret", "private")

    with pytest.raises(AgentExecutionError):
        asyncio.run(AuditedPlanningRuntime(db, Unknown(), job.job_id).execute(request))
    assert planning_calls(db, job.job_id)[0]["details"]["error_code"] == "planning_call_failed"
    with pytest.raises(KeyError):
        planning_calls(db, "missing")


def test_http_readback_is_bounded_and_missing_job_is_404(audit_case):
    from fastapi.testclient import TestClient

    from apps.hub_api.main import create_app

    db, job, request = audit_case
    asyncio.run(AuditedPlanningRuntime(db, FakeAgentRuntime(), job.job_id).execute(request))
    client = TestClient(create_app(db))
    response = client.get(f"/v1/evolution/jobs/{job.job_id}/planning-calls?limit=1")
    assert response.status_code == 200 and len(response.json()) == 1
    assert response.json()[0]["status"] == "succeeded"
    assert client.get("/v1/evolution/jobs/missing/planning-calls").status_code == 404


def test_permanent_missing_role_does_not_become_retryable():
    from packages.orchestration.langgraph.graphs.supervisor_graph import (
        build_supervisor_candidate_graph,
    )
    from tests.orchestration.test_supervisor_graph import candidate_input

    class Permanent(FakeAgentRuntime):
        async def execute(self, request):
            if request.role == "counter_thesis":
                raise AgentExecutionError("candidate_evidence_out_of_scope", "not in evidence")
            return await super().execute(request)

    graph = build_supervisor_candidate_graph(
        Permanent(),
        available_capabilities={"counter_thesis", "data_quality"},
        required_capabilities=("counter_thesis", "data_quality"),
    )
    with pytest.raises(AgentExecutionError) as raised:
        asyncio.run(graph.ainvoke(candidate_input()))
    assert raised.value.error_code == "research_coverage_missing"
    assert raised.value.cause_code == "candidate_evidence_out_of_scope"
    assert raised.value.retryable is False


def test_coverage_timeout_retries_same_job_and_keeps_each_attempt(
    audit_case, monkeypatch, tmp_path
):
    from apps.hub_worker.composition import build_evolution_worker
    from packages.kernel.decision_hub_kernel.application.live_observation import EvolutionJobPolicy
    from packages.kernel.decision_hub_kernel.persistence.db import EvolutionJobRecord
    from packages.runtime_adapters.fake_runtime.runtime import FakeEvolutionAgentRuntime

    db, job, _ = audit_case
    with db.session() as session:
        row = session.get(EvolutionJobRecord, job.job_id)
        row.status, row.lease_owner, row.lease_expires_at, row.attempt = "queued", None, None, 0

    class Flaky(FakeEvolutionAgentRuntime):
        failing = True

        async def execute(self, request):
            if request.role == "counter_thesis" and self.failing:
                raise AgentExecutionError("provider_timeout", "test only", retryable=True)
            return await super().execute(request)

    from packages.kernel.decision_hub_kernel.persistence.db import FeedbackRecord

    with db.session() as session:
        session.add(
            FeedbackRecord(
                feedback_id="feedback-1",
                request_id="context-test-feedback",
                target_type="report_revision",
                target_id="revision",
                created_by="owner",
                verdict="needs_review",
                notes="Check the source basis",
                created_at=datetime.now(UTC),
            )
        )
    runtime = Flaky()
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "artifacts"))
    worker = build_evolution_worker(
        db,
        runtime=FakeAgentRuntime(),
        planning_runtime=runtime,
        policy=EvolutionJobPolicy(scheduled_scan_enabled=False),
    )
    first = asyncio.run(worker.executor.tick("first", retry_backoff_seconds=0))
    assert first.status == "retry_wait" and first.last_error_code == "research_coverage_missing"
    old = planning_calls(db, job.job_id)
    assert sum(r["status"] == "failed" for r in old) == 2
    runtime.failing = False
    second = asyncio.run(worker.executor.tick("second"))
    assert second.status == "waiting_inputs" and second.attempt == 2
    rows = planning_calls(db, job.job_id)
    assert {r["job_attempt"] for r in rows} == {1, 2}
    assert all(r in rows for r in old)
    assert all(r["status"] == "succeeded" for r in rows if r["job_attempt"] == 2)

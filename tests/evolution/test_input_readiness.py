import asyncio
import json
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.hub_api.asset_routes import asset_router
from apps.hub_worker.composition import build_evolution_worker
from packages.evals.fixture_audit import (
    audit_fixture_splits,
    default_evolution_fixtures,
    validate_fixture_splits,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    EvolutionJobRecord,
    FeedbackRecord,
)
from packages.runtime_adapters.fake_runtime.runtime import (
    FakeAgentRuntime,
    FakeEvolutionAgentRuntime,
)


def test_repository_samples_overlap_despite_different_ids_and_source_names():
    audit = audit_fixture_splits(default_evolution_fixtures())
    assert audit.cross_split_groups == 1
    assert audit.counts == {"replay": 1, "holdout": 1, "shadow": 1}
    with pytest.raises(ValueError, match="evaluation_cross_split_overlap"):
        validate_fixture_splits(default_evolution_fixtures())


def test_duplicate_identity_ignores_source_ids_labels_case_and_whitespace(tmp_path):
    paths = default_evolution_fixtures()
    payload = json.loads(paths["replay"][0].read_text(encoding="utf-8"))
    payload["fixture_id"] = "renamed"
    payload["observation"]["source_id"] = "another-source"
    payload["observation"]["text"] = "  " + payload["observation"]["text"].upper() + "\n"
    duplicate = tmp_path / "renamed.json"
    duplicate.write_text(json.dumps(payload), encoding="utf-8")
    paths["replay"] += (duplicate,)
    audit = audit_fixture_splits(paths)
    assert audit.cross_split_groups == audit.within_split_groups == 1
    assert "evaluation_duplicate_sample" in audit.issues
    duplicate.write_text("invalid", encoding="utf-8")
    assert "evaluation_fixture_unreadable" in audit_fixture_splits(paths).issues


def test_readiness_is_read_only_and_overlap_waits_after_candidate_before_evaluation(
    tmp_path,
    monkeypatch,
):
    db = Database(f"sqlite+pysqlite:///{tmp_path / 'evolution.sqlite3'}")
    db.create_all()
    now = datetime(2026, 9, 30, tzinfo=UTC)
    with db.session() as session:
        for verdict in ("needs_review", "useful"):
            session.add(
                FeedbackRecord(
                    feedback_id=verdict,
                    request_id=verdict,
                    target_type="report_revision",
                    target_id="revision-one",
                    created_by="owner",
                    verdict=verdict,
                    notes="private feedback body",
                    created_at=now,
                )
            )
    app = FastAPI()
    app.include_router(asset_router(db, tmp_path, clock=lambda: now))
    client = TestClient(app)
    response = client.get("/v1/evolution/readiness")
    assert response.status_code == 200
    view = response.json()
    assert view["feedback_count"] == 2
    assert view["actionable_feedback"] == view["unregistered_feedback"] == 1
    assert view["jobs"] == view["candidates"] == 0
    assert view["observed_at"].endswith("Z")
    assert not view["prospective_collector_available"]
    assert "private" not in response.text and str(tmp_path) not in response.text
    with db.session() as session:
        assert session.query(EvolutionJobRecord).count() == 0

    class MustNotRun(FakeAgentRuntime):
        async def execute(self, request):
            pytest.fail("overlapping configured samples must not enter evaluation")

    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "artifacts"))

    class Planner(FakeEvolutionAgentRuntime):
        calls = 0

        async def execute(self, request):
            self.calls += 1
            return await super().execute(request)

    planner = Planner()
    worker = build_evolution_worker(db, runtime=MustNotRun(), planning_runtime=planner)
    before = worker.executor.assets.active_pointer("crypto_macro.v1")
    job = asyncio.run(worker.tick())
    assert (
        job.status == "waiting_inputs" and job.last_error_code == "evaluation_cross_split_overlap"
    )
    assert job.candidate_id is not None and job.result_refs == []
    assert job.finished_at is None and job.lease_owner is None
    calls = planner.calls
    assert calls > 0
    for _ in range(2):
        assert asyncio.run(worker.tick()) is None
    persisted = worker.executor.jobs.list_jobs()[0]
    assert persisted.attempt == job.attempt
    assert planner.calls == calls
    assert worker.executor.jobs.counts().waiting_inputs == 1
    # A recreated worker must not repeat planning while inputs remain invalid.
    recovered = build_evolution_worker(db, runtime=MustNotRun(), planning_runtime=planner)
    assert asyncio.run(recovered.tick()) is None
    assert planner.calls == calls
    assert worker.executor.assets.active_pointer("crypto_macro.v1") == before
    assert worker.executor.assets.overview().experiments == []
    after = client.get("/v1/evolution/readiness").json()
    assert after["jobs"] == 1 and after["unregistered_feedback"] == 0
    assert after["feedback_count"] == 2


def test_waiting_candidate_resumes_without_replanning_after_inputs_fixed(tmp_path, monkeypatch):
    from packages.kernel.decision_hub_kernel.application.live_observation import EvolutionJobPolicy
    from tests.evolution.test_evolution_executor import _independent_test_fixtures

    db = Database(f"sqlite+pysqlite:///{tmp_path / 'resume.sqlite3'}")
    db.create_all()
    with db.session() as session:
        session.add(
            FeedbackRecord(
                feedback_id="resume",
                request_id="resume",
                target_type="report_revision",
                target_id="revision",
                created_by="owner",
                verdict="needs_review",
                notes="Check evidence",
                created_at=datetime(2026, 9, 30, tzinfo=UTC),
            )
        )
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "data"))
    policy = EvolutionJobPolicy(scheduled_scan_enabled=False)
    first = build_evolution_worker(db, runtime=FakeAgentRuntime(), policy=policy)
    waiting = asyncio.run(first.tick())
    assert waiting.status == "waiting_inputs"

    class NoReplanning(FakeEvolutionAgentRuntime):
        async def execute(self, request):
            pytest.fail("saved candidate must survive recovery without replanning")

    recovered = build_evolution_worker(
        db, runtime=FakeAgentRuntime(), planning_runtime=NoReplanning(), policy=policy
    )
    root = _independent_test_fixtures(tmp_path / "synthetic-test-only")
    recovered.executor.evaluation_plans.fixtures = {
        stage: (root / "fixtures/evolution" / stage / "powell-higher-for-longer.json",)
        for stage in ("replay", "holdout", "shadow")
    }
    result = asyncio.run(recovered.tick())
    assert result.candidate_id == waiting.candidate_id
    # Distinct historical text resumes the saved candidate, but must not pass
    # the separate real-prospective gate just because it sits in a shadow folder.
    assert result.status == "waiting_inputs"
    assert result.last_error_code == "prospective_evaluation_required"
    assert sorted(item.status for item in recovered.executor.assets.overview().experiments) == [
        "completed",
        "completed",
    ]
    assert recovered.executor.jobs.counts().waiting_inputs == 1
    assert asyncio.run(recovered.tick()) is None

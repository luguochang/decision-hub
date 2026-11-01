import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from apps.hub_worker.composition import build_evolution_worker
from packages.evals.runner import EvaluationRunner
from packages.kernel.decision_hub_kernel.application.live_observation import EvolutionJobPolicy
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    EvaluationStageReceiptRecord,
    ExperimentResultRecord,
    FeedbackRecord,
)
from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError
from packages.runtime_adapters.fake_runtime.runtime import FakeAgentRuntime
from tests.evolution.test_evolution_executor import _independent_test_fixtures


class FailHoldoutOnce(EvaluationRunner):
    def __init__(self, root):
        super().__init__(root)
        self.calls = []

    async def run(self, experiment, dataset, *args, **kwargs):
        self.calls.append(dataset.split)
        if dataset.split == "holdout" and self.calls.count("holdout") == 1:
            raise AgentExecutionError("provider_timeout", "test-only interruption", retryable=True)
        return await super().run(experiment, dataset, *args, **kwargs)


@pytest.fixture
def stage_case(tmp_path, monkeypatch):
    db = Database(f"sqlite+pysqlite:///{tmp_path / 'stage.sqlite3'}")
    db.create_all()
    with db.session() as session:
        session.add(
            FeedbackRecord(
                feedback_id="stages",
                request_id="stages",
                target_type="report_revision",
                target_id="revision",
                created_by="owner",
                verdict="needs_review",
                notes="Check claims",
                created_at=datetime(2026, 9, 30, tzinfo=UTC),
            )
        )
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "data"))
    runtime = FakeAgentRuntime()
    worker = build_evolution_worker(
        db,
        runtime=runtime,
        policy=EvolutionJobPolicy(scheduled_scan_enabled=False),
    )
    root = _independent_test_fixtures(tmp_path / "synthetic-only")
    worker.executor.evaluation_plans.fixtures = {
        stage: (root / "fixtures/evolution" / stage / "powell-higher-for-longer.json",)
        for stage in ("replay", "holdout", "shadow")
    }
    runner = FailHoldoutOnce(tmp_path / "evaluations")
    worker.executor.evaluation_runner = runner
    first = asyncio.run(worker.executor.tick("first", retry_backoff_seconds=0))
    assert first.status == "retry_wait"
    assert len(first.experiment_refs) == len(first.result_refs) == 1
    return db, worker, runtime, runner, first


def test_completed_stage_survives_next_stage_failure_without_rerunning(stage_case):
    db, worker, _, runner, first = stage_case
    resumed = asyncio.run(worker.executor.tick("recovered"))
    assert resumed.status == "waiting_inputs" and resumed.stage == "shadow"
    assert resumed.last_error_code == "prospective_evaluation_required"
    assert resumed.candidate_id == first.candidate_id
    assert runner.calls == ["replay", "holdout", "holdout"]
    assert len(resumed.result_refs) == len(resumed.experiment_refs) == 2
    with db.session() as session:
        receipts = session.query(EvaluationStageReceiptRecord).all()
        assert len(receipts) == 2 and all(r.completion_hash for r in receipts)
    assert asyncio.run(worker.tick()) is None
    assert runner.calls == ["replay", "holdout", "holdout"]


def test_sealed_results_recover_interrupted_ledger_projection(stage_case):
    db, worker, _, runner, _ = stage_case
    with db.session() as session:
        session.delete(session.query(ExperimentResultRecord).first())
    resumed = asyncio.run(worker.executor.tick("recovered"))
    assert resumed.status == "waiting_inputs" and resumed.stage == "shadow"
    assert len(worker.executor.assets.overview().results) == 4
    assert runner.calls == ["replay", "holdout", "holdout"]


@pytest.mark.parametrize("tamper", ["artifact", "recipe", "completion", "scorer"])
def test_completed_stage_reuse_refuses_changed_evidence_or_configuration(
    stage_case, tamper, monkeypatch
):
    db, worker, runtime, runner, _ = stage_case
    expected = {
        "artifact": "evaluation_stage_artifact_changed",
        "recipe": "evaluation_stage_binding_changed",
        "completion": "evaluation_stage_completion_changed",
        "scorer": "evaluation_stage_binding_changed",
    }[tamper]
    if tamper == "artifact":
        result = worker.executor.assets.overview().results[0]
        Path(result.raw_artifact_refs[0]).write_text("{}", encoding="utf-8")
    elif tamper == "recipe":
        runtime.runtime_version = "changed.test-only"
    elif tamper == "scorer":
        monkeypatch.setattr("packages.evals.stage_receipts.SCORER_VERSION", "changed.test-only")
    else:
        with db.session() as session:
            row = (
                session.query(EvaluationStageReceiptRecord)
                .filter(
                    EvaluationStageReceiptRecord.completion_hash.is_not(None),
                )
                .one()
            )
            row.completion_hash = "0" * 64
    result = asyncio.run(worker.executor.tick("recovered"))
    assert result.status == "failed" and result.last_error_code == expected
    assert runner.calls == ["replay", "holdout"]

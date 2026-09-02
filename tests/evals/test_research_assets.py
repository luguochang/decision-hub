from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from packages.contracts_py.decision_hub_contracts import ExecutionBudget
from packages.evals.research_assets import ResearchComparisonAssetRecorder
from packages.evals.research_comparison import ResearchComparisonRunner
from packages.evals.research_dataset import load_research_evaluation_dataset
from packages.kernel.decision_hub_kernel.application.evolution import EvolutionAssetService
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.runtime_adapters.fake_runtime.runtime import FakeAgentRuntime
from packages.runtime_adapters.fixed_research_runtime import FixedResearchRuntime
from tests.evals.test_research_comparison import CandidateRuntimeFixture

DATASET = Path("packs/crypto_macro/evaluations/r2r_pit_v1/manifest.json")
NOW = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_research_comparison_registers_existing_evolution_assets(tmp_path: Path) -> None:
    dataset = load_research_evaluation_dataset(DATASET)
    output_dir = tmp_path / "raw"
    comparison = await ResearchComparisonRunner(
        dataset,
        budget=ExecutionBudget(
            max_evidence_rounds=3,
            max_tool_calls=12,
            max_subagents=6,
            total_deadline_seconds=30,
            per_tool_timeout_seconds=20,
            per_model_step_timeout_seconds=20,
            max_structured_repairs=1,
            max_estimated_cost_usd=1.0,
        ),
        output_dir=output_dir,
    ).run(
        experiment_id="r2-r-06c-assets",
        baseline=FixedResearchRuntime(FakeAgentRuntime()),
        candidate=CandidateRuntimeFixture(),
    )
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'assets.sqlite3'}")
    database.create_all()
    experiment, results = ResearchComparisonAssetRecorder(
        EvolutionAssetService(database, clock=lambda: NOW)
    ).record(
        dataset=dataset,
        comparison=comparison,
        output_dir=output_dir,
        provider_id="test-provider",
        model="test-model",
        profile_ref="test-profile.v1",
        deadline_seconds=30,
        max_cost_usd=1.0,
        created_at=NOW,
    )

    assert experiment.status == "completed"
    assert experiment.randomness_policy == "provider_default"
    assert experiment.random_seed is None
    assert {item.sample_count for item in results} == {12}
    assert all(item.brier_score is None for item in results)
    assert all(item.directional_accuracy is None for item in results)
    assert all(len(item.raw_artifact_refs) == 12 for item in results)

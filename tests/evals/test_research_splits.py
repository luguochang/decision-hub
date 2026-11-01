from __future__ import annotations

from pathlib import Path

import pytest

from packages.contracts_py.decision_hub_contracts import ExecutionBudget
from packages.evals.research_comparison import ResearchComparisonRunner
from packages.evals.research_dataset import load_research_evaluation_dataset
from packages.runtime_adapters.fake_runtime.runtime import FakeAgentRuntime
from packages.runtime_adapters.fixed_research_runtime import FixedResearchRuntime
from tests.evals.test_research_comparison import CandidateRuntimeFixture

DEV = Path("packs/crypto_macro/evaluations/r2r_dev_v1/manifest.json")
HOLDOUT = Path("packs/crypto_macro/evaluations/r2r_holdout_v1/manifest.json")


def test_dev_and_holdout_are_frozen_six_case_partitions() -> None:
    development = load_research_evaluation_dataset(DEV)
    holdout = load_research_evaluation_dataset(HOLDOUT)
    assert len(development.cases) == 6
    assert len(holdout.cases) == 6
    assert set(item.case_id for item in development.cases).isdisjoint(
        item.case_id for item in holdout.cases
    )
    assert set(development.manifest.event_family_counts) == set(
        holdout.manifest.event_family_counts
    )
    assert development.manifest.manifest_hash != holdout.manifest.manifest_hash


@pytest.mark.parametrize("manifest", [DEV, HOLDOUT], ids=["development", "holdout"])
@pytest.mark.asyncio
async def test_quality_comparison_keeps_degraded_results_in_denominator(
    tmp_path: Path,
    manifest: Path,
) -> None:
    dataset = load_research_evaluation_dataset(manifest)
    comparison = await ResearchComparisonRunner(
        dataset,
        budget=ExecutionBudget(
            max_evidence_rounds=1,
            max_tool_calls=2,
            max_subagents=1,
            total_deadline_seconds=20,
            per_tool_timeout_seconds=10,
            per_model_step_timeout_seconds=10,
            max_structured_repairs=1,
            max_estimated_cost_usd=0.1,
        ),
        output_dir=tmp_path,
    ).run(
        experiment_id=f"cv4-{dataset.manifest.split}-quality",
        baseline=FixedResearchRuntime(FakeAgentRuntime()),
        candidate=CandidateRuntimeFixture(),
    )
    candidate = next(item for item in comparison.summaries if item.runtime_id == "candidate-test")
    assert candidate.sample_count == 6
    assert candidate.completed_count == 6
    assert candidate.quality_success_count == 0
    assert candidate.manual_rework_count == 6
    assert all(item.manual_rework_flags for item in comparison.case_reports)
    assert all(item.pit_violations == 0 for item in comparison.case_reports)

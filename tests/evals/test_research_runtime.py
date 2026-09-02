from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from packages.contracts_py.decision_hub_contracts import ExecutionBudget
from packages.evals.research_comparison import ResearchComparisonRunner
from packages.evals.research_dataset import load_research_evaluation_dataset
from packages.evals.research_runtime import CaseScopedDshResearchRuntime
from packages.runtime_adapters.dsh_runtime import DshRuntimeConfig
from packages.runtime_adapters.fake_runtime.runtime import FakeAgentRuntime
from packages.runtime_adapters.fixed_research_runtime import FixedResearchRuntime
from tests.evals.test_research_comparison import CandidateRuntimeFixture

DATASET = Path("packs/crypto_macro/evaluations/r2r_pit_v1/manifest.json")


@pytest.mark.asyncio
async def test_case_scoped_runtime_injects_an_isolated_ready_mcp(tmp_path: Path) -> None:
    dataset = load_research_evaluation_dataset(DATASET)
    selected = replace(dataset, cases=dataset.cases[:1], case_paths=dataset.case_paths[:1])
    observed_urls: list[str] = []

    def runtime_factory(config: DshRuntimeConfig):
        assert config.research_mcp_url is not None
        observed_urls.append(str(config.research_mcp_url))
        return CandidateRuntimeFixture()

    candidate = CaseScopedDshResearchRuntime(
        {selected.cases[0].case_id: selected.case_paths[0]},
        config=DshRuntimeConfig(
            workspace=tmp_path,
            session_root=tmp_path / "sessions",
        ),
        runtime_factory=runtime_factory,
    )
    comparison = await ResearchComparisonRunner(
        selected,
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
        output_dir=tmp_path / "raw",
    ).run(
        experiment_id="r2-r-06c-case-mcp",
        baseline=FixedResearchRuntime(FakeAgentRuntime()),
        candidate=candidate,
    )

    assert len(observed_urls) == 1
    assert observed_urls[0].startswith("http://127.0.0.1:")
    assert comparison.summaries[1].completed_count == 1

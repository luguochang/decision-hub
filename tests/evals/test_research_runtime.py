from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from packages.contracts_py.decision_hub_contracts import ExecutionBudget
from packages.evals import research_runtime as runtime_module
from packages.evals.research_comparison import ResearchComparisonRunner, _request_for
from packages.evals.research_dataset import load_research_evaluation_dataset
from packages.evals.research_runtime import CaseScopedDshResearchRuntime
from packages.runtime_adapters.dsh_runtime import DshRuntimeConfig, DshWebHostConfig
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


@pytest.mark.asyncio
async def test_case_scoped_web_runtime_can_use_the_product_graph_and_review_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    dataset = load_research_evaluation_dataset(DATASET)
    case = dataset.cases[0]
    request = _request_for(
        case,
        "r2-r-product-graph-canary",
        "dsh-web-graph",
        ExecutionBudget(
            max_evidence_rounds=1,
            max_tool_calls=2,
            max_subagents=1,
            total_deadline_seconds=20,
            per_tool_timeout_seconds=10,
            per_model_step_timeout_seconds=10,
            max_structured_repairs=1,
            max_estimated_cost_usd=0.1,
        ),
    )
    database = runtime_module.Database(f"sqlite+pysqlite:///{tmp_path / 'product-graph.sqlite3'}")
    database.initialize()
    fixture = tmp_path / "case.json"
    fixture.write_text("{}", encoding="utf-8")
    calls: list[str] = []

    class FakeProcess:
        def poll(self) -> int:
            return 0

    class FakeWebRuntime:
        runtime_version = "dsh-web-test"

        async def close(self) -> None:
            return None

    class FakeGraphExecutor:
        def __init__(self, database: object, runtime: object) -> None:
            del database, runtime

        async def execute(self, value: object) -> dict[str, object]:
            calls.append("graph")
            result = await CandidateRuntimeFixture().execute(value)  # type: ignore[arg-type]
            return {"final_result": result.model_dump(mode="json")}

    async def fake_wait_ready(*args: object, **kwargs: object) -> None:
        del args, kwargs

    monkeypatch.setattr(runtime_module, "_start_mcp", lambda *args, **kwargs: FakeProcess())
    monkeypatch.setattr(runtime_module, "_wait_ready", fake_wait_ready)
    monkeypatch.setattr(
        runtime_module, "DshWebResearchRuntime", lambda *args, **kwargs: FakeWebRuntime()
    )
    monkeypatch.setattr(runtime_module, "LangGraphResearchExecutor", FakeGraphExecutor)

    candidate = runtime_module.CaseScopedDshWebResearchRuntime(
        {case.case_id: fixture},
        database=database,
        host_config=DshWebHostConfig(base_url="http://dsh.local", bridge_key="host-secret"),
        product_graph=True,
    )
    result = await candidate.execute(request)

    assert candidate.runtime_id == "dsh-web-graph"
    assert calls == ["graph"]
    assert len(result.reviews) == 1
    assert result.reviews[0].role == "independent_reviewer"


def test_case_scoped_mcp_selects_durable_accounting_for_web(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}

    def fake_popen(*args: object, **kwargs: object) -> object:
        captured["args"] = args
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(runtime_module.subprocess, "Popen", fake_popen)
    fixture = tmp_path / "case.json"
    fixture.write_text("{}", encoding="utf-8")

    runtime_module._start_mcp(fixture, 8002, durable_progress=True)

    env = captured["env"]
    assert isinstance(env, dict)
    assert env["DECISION_HUB_RESEARCH_DURABLE_PROGRESS"] == "1"
    assert env["DECISION_HUB_DSH_RESEARCH_TOOL_KEY"]

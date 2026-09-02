from __future__ import annotations

import argparse
import asyncio
import json
import os
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from packages.contracts_py.decision_hub_contracts import ExecutionBudget
from packages.evals.research_assets import ResearchComparisonAssetRecorder
from packages.evals.research_comparison import ResearchComparisonRunner
from packages.evals.research_dataset import (
    ResearchEvaluationDataset,
    load_research_evaluation_dataset,
)
from packages.evals.research_runtime import CaseScopedDshResearchRuntime
from packages.kernel.decision_hub_kernel.application.evolution import EvolutionAssetService
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.runtime_adapters.dsh_runtime import DshRuntimeConfig
from packages.runtime_adapters.fixed_research_runtime import FixedResearchRuntime
from packages.runtime_adapters.langgraph_agent.provider_config import ProviderConfig
from packages.runtime_adapters.langgraph_agent.runtime import LangGraphAgentRuntime

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATASET = ROOT / "packs/crypto_macro/evaluations/r2r_pit_v1/manifest.json"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the authorized R2-R Fixed-vs-DSH PIT comparison."
    )
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--case-id", help="Run one canary case without ledger writes.")
    selection.add_argument(
        "--all", action="store_true", help="Run all immutable cases and register assets."
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--experiment-id")
    parser.add_argument(
        "--database-url",
        help="Evolution ledger URL; defaults to the configured Decision Hub database.",
    )
    parser.add_argument("--deadline-seconds", type=int, default=180)
    parser.add_argument("--max-cost-usd", type=float, default=1.0)
    return parser.parse_args()


def require_live_authorization() -> None:
    if os.getenv("DECISION_HUB_R2R_LIVE_EVAL") != "1":
        raise RuntimeError(
            "set DECISION_HUB_R2R_LIVE_EVAL=1 to authorize external Provider evaluation"
        )
    if os.getenv("DECISION_HUB_LLM_ENABLED") != "1":
        raise RuntimeError("set DECISION_HUB_LLM_ENABLED=1 for the Fixed Provider baseline")
    if not any(
        os.getenv(name)
        for name in (
            "DECISION_HUB_DSH_API_KEY",
            "DEEPSEEK_API_KEY",
            "SUB2API_API_KEY",
            "OPENAI_API_KEY",
        )
    ):
        raise RuntimeError("an external Provider API key is required in the process environment")


def select_case(
    dataset: ResearchEvaluationDataset, case_id: str | None
) -> ResearchEvaluationDataset:
    if case_id is None:
        return dataset
    selected = [
        (case, path)
        for case, path in zip(dataset.cases, dataset.case_paths, strict=True)
        if case.case_id == case_id
    ]
    if len(selected) != 1:
        raise ValueError("research_evaluation_case_not_found")
    case, path = selected[0]
    return replace(dataset, cases=(case,), case_paths=(path,))


def _budget(deadline_seconds: int, max_cost_usd: float) -> ExecutionBudget:
    return ExecutionBudget(
        max_evidence_rounds=3,
        max_tool_calls=12,
        max_subagents=6,
        total_deadline_seconds=deadline_seconds,
        per_tool_timeout_seconds=min(20, deadline_seconds),
        per_model_step_timeout_seconds=min(60, deadline_seconds),
        max_structured_repairs=1,
        max_estimated_cost_usd=max_cost_usd,
    )


async def run(args: argparse.Namespace) -> dict[str, object]:
    require_live_authorization()
    if args.deadline_seconds < 1 or args.deadline_seconds > 3600:
        raise ValueError("deadline_seconds_out_of_range")
    if args.max_cost_usd < 0:
        raise ValueError("max_cost_usd_out_of_range")
    full_dataset = load_research_evaluation_dataset(args.dataset)
    dataset = select_case(full_dataset, args.case_id)
    now = datetime.now(UTC)
    experiment_id = args.experiment_id or f"r2-r-06c-{now:%Y%m%dT%H%M%SZ}"
    output_dir = args.output_dir or (
        ROOT / "data/decision-hub/evaluations" / experiment_id
    )
    budget = _budget(args.deadline_seconds, args.max_cost_usd)
    provider = ProviderConfig.from_env()
    dsh_config = DshRuntimeConfig.from_env().model_copy(
        update={
            "workspace": ROOT,
            "session_root": output_dir / "dsh-sessions",
            "request_timeout_seconds": min(float(args.deadline_seconds), 600.0),
        }
    )
    baseline = FixedResearchRuntime(LangGraphAgentRuntime(provider_config=provider))
    candidate = CaseScopedDshResearchRuntime(
        {case.case_id: path for case, path in zip(dataset.cases, dataset.case_paths, strict=True)},
        config=dsh_config,
    )
    try:
        comparison = await ResearchComparisonRunner(
            dataset, budget=budget, output_dir=output_dir
        ).run(
            experiment_id=experiment_id,
            baseline=baseline,
            candidate=candidate,
        )
    finally:
        await baseline.close()
        await candidate.close()

    recorded = False
    result_refs: list[str] = []
    if args.all:
        if len(dataset.cases) != len(full_dataset.cases):
            raise RuntimeError("full_dataset_required_for_asset_registration")
        database = Database(args.database_url)
        database.initialize()
        _, results = ResearchComparisonAssetRecorder(EvolutionAssetService(database)).record(
            dataset=full_dataset,
            comparison=comparison,
            output_dir=output_dir,
            provider_id=provider.provider_id,
            model=provider.model,
            profile_ref=candidate.profile_ref,
            deadline_seconds=args.deadline_seconds,
            max_cost_usd=args.max_cost_usd,
            created_at=now,
        )
        recorded = True
        result_refs = [item.result_id for item in results]

    summaries = {item.runtime_id: item for item in comparison.summaries}
    return {
        "status": "completed",
        "experiment_id": experiment_id,
        "dataset_id": comparison.dataset_id,
        "case_count": len(dataset.cases),
        "baseline": summaries[comparison.baseline_runtime_id].model_dump(mode="json"),
        "candidate": summaries[comparison.candidate_runtime_id].model_dump(mode="json"),
        "output_dir": str(output_dir),
        "assets_recorded": recorded,
        "result_refs": result_refs,
        "active_pointer_changed": False,
    }


def main() -> int:
    args = _parse_args()
    try:
        result = asyncio.run(run(args))
    except Exception as exc:
        error_code = getattr(exc, "error_code", None) or type(exc).__name__
        print(json.dumps({"status": "error", "error_code": error_code}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import time
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, cast

from packages.contracts_py.decision_hub_contracts import (
    EvidenceCandidate,
    ExecutionBudget,
    ResearchEvaluationCase,
    ResearchRuntimeCaseReport,
    ResearchRuntimeComparison,
    ResearchRuntimeSummary,
    ResearchSessionRequest,
    ResearchSessionResult,
)
from packages.evals.research_dataset import ResearchEvaluationDataset
from packages.kernel.decision_hub_kernel.decision.sufficiency import (
    assess_evidence_sufficiency,
)
from packages.kernel.decision_hub_kernel.ports.research import ResearchHarnessRuntime
from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError


class ResearchComparisonRunner:
    """Run two research runtimes on identical immutable cases and budgets."""

    def __init__(
        self,
        dataset: ResearchEvaluationDataset,
        *,
        budget: ExecutionBudget,
        output_dir: Path | None = None,
    ) -> None:
        self.dataset = dataset
        self.budget = budget
        self.output_dir = output_dir

    async def run(
        self,
        *,
        experiment_id: str,
        baseline: ResearchHarnessRuntime,
        candidate: ResearchHarnessRuntime,
    ) -> ResearchRuntimeComparison:
        reports: list[ResearchRuntimeCaseReport] = []
        for case in self.dataset.cases:
            reports.append(
                await self._run_case(case, experiment_id, baseline, baseline.runtime_id)
            )
            reports.append(
                await self._run_case(case, experiment_id, candidate, candidate.runtime_id)
            )
        summaries = [
            _summarize(runtime_id, reports)
            for runtime_id in (baseline.runtime_id, candidate.runtime_id)
        ]
        comparison = ResearchRuntimeComparison(
            schema_version="research-runtime-comparison.v1",
            experiment_id=experiment_id,
            dataset_id=self.dataset.manifest.dataset_id,
            dataset_manifest_hash=self.dataset.manifest.manifest_hash,
            baseline_runtime_id=baseline.runtime_id,
            candidate_runtime_id=candidate.runtime_id,
            case_reports=reports,
            summaries=summaries,
            conclusion="pending",
        )
        if self.output_dir is not None:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            (self.output_dir / "comparison.json").write_text(
                comparison.model_dump_json(indent=2), encoding="utf-8"
            )
        return comparison

    async def _run_case(
        self,
        case: ResearchEvaluationCase,
        experiment_id: str,
        runtime: ResearchHarnessRuntime,
        runtime_id: str,
    ) -> ResearchRuntimeCaseReport:
        request = _request_for(case, experiment_id, runtime_id, self.budget)
        started_at = datetime.now(UTC)
        started = time.perf_counter()
        result: ResearchSessionResult | None = None
        failure_code: str | None = None
        status: Literal["completed", "degraded", "failed", "timeout"]
        try:
            async with asyncio.timeout(self.budget.total_deadline_seconds):
                result = await runtime.execute(request)
            status = _result_status(result.status)
        except TimeoutError:
            status = "timeout"
            failure_code = "provider_timeout"
        except AgentExecutionError as exc:
            status = "failed"
            failure_code = exc.error_code
        except BaseException:
            status = "failed"
            failure_code = "comparison_runtime_failed"
        finished_at = datetime.now(UTC)
        report = _case_report(
            self.dataset.manifest.dataset_id,
            case,
            runtime,
            result,
            status,
            failure_code,
            started_at,
            finished_at,
            round((time.perf_counter() - started) * 1000),
        )
        if self.output_dir is not None:
            runtime_dir = self.output_dir / case.case_id
            runtime_dir.mkdir(parents=True, exist_ok=True)
            (runtime_dir / f"{runtime_id}.json").write_text(
                report.model_dump_json(indent=2), encoding="utf-8"
            )
        return report


def _request_for(
    case: ResearchEvaluationCase,
    experiment_id: str,
    runtime_id: str,
    budget: ExecutionBudget,
) -> ResearchSessionRequest:
    now = datetime.now(UTC)
    capabilities = tuple(
        sorted({item.capability_id for item in case.archived_capability_fixtures})
    )
    return ResearchSessionRequest(
        schema_version="research-session-request.v1",
        request_id=f"{experiment_id}:{case.case_id}:{runtime_id}",
        run_id=f"eval:{experiment_id}:{case.case_id}:{runtime_id}",
        event_id=case.case_id,
        trigger_snapshot_id=f"trigger:{case.case_id}",
        domain_pack_ref="crypto_macro.v1",
        role_profile_ref="crypto_macro.manager.v1",
        execution_mode="replay",
        pit_cutoff_at=case.cutoff_at,
        current_round=1,
        evidence_refs=[case.trigger_evidence.evidence_id],
        input_evidence=[case.trigger_evidence],
        evidence_requirements=case.evidence_requirements,
        target_gaps=[],
        allowed_capabilities=list(capabilities),
        execution_budget=budget,
        deadline_at=now + timedelta(seconds=budget.total_deadline_seconds),
        output_schema_ref="research-session-result.v1",
        repair_instructions=None,
    )


def _result_status(value: str) -> Literal["completed", "degraded", "failed", "timeout"]:
    if value in {"completed", "degraded", "failed"}:
        return cast(Literal["completed", "degraded", "failed"], value)
    return "failed"


def _case_report(
    dataset_id: str,
    case: ResearchEvaluationCase,
    runtime: ResearchHarnessRuntime,
    result: ResearchSessionResult | None,
    status: Literal["completed", "degraded", "failed", "timeout"],
    failure_code: str | None,
    started_at: datetime,
    finished_at: datetime,
    latency_ms: int,
) -> ResearchRuntimeCaseReport:
    evidence = result.evidence_candidates if result is not None else []
    pit_violations = sum(
        1
        for item in evidence
        if (
            (item.published_at is not None and item.published_at > item.observed_at)
            or item.observed_at > item.received_at
            or item.received_at > case.cutoff_at
        )
    )
    try:
        coverage = assess_evidence_sufficiency(
            case.evidence_requirements,
            _evidence_for_scoring(case, evidence),
            cutoff_at=case.cutoff_at,
        )
        hard_gap_count = sum(1 for item in coverage.gaps if item.importance == "hard")
        hard_ratio = coverage.hard_coverage_ratio
    except (ValueError, TypeError):
        hard_gap_count = len(case.expected_hard_requirement_ids)
        hard_ratio = 0.0
        pit_violations += 1
    horizon_distinct = _horizons_distinct(result)
    result_hash = None
    if result is not None:
        result_hash = hashlib.sha256(
            json.dumps(
                result.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
    return ResearchRuntimeCaseReport(
        schema_version="research-runtime-case-report.v1",
        dataset_id=dataset_id,
        case_id=case.case_id,
        runtime_id=runtime.runtime_id,
        runtime_version=runtime.runtime_version,
        status=status,
        cutoff_at=case.cutoff_at,
        started_at=started_at,
        finished_at=finished_at,
        latency_ms=latency_ms,
        result_hash=result_hash,
        result=result,
        hard_coverage_ratio=hard_ratio,
        hard_gap_count=hard_gap_count,
        evidence_count=len(evidence),
        unattested_evidence_count=1 if failure_code == "dsh_evidence_unattested" else 0,
        pit_violations=pit_violations,
        horizon_count=len(result.horizons) if result is not None else 0,
        horizon_distinct=horizon_distinct,
        tool_calls=result.total_tool_calls if result is not None else 0,
        subagents=result.total_subagents if result is not None else 0,
        total_tokens=result.total_tokens if result is not None else None,
        estimated_cost_usd=result.estimated_cost_usd if result is not None else None,
        failure_code=failure_code,
    )


def _evidence_for_scoring(
    case: ResearchEvaluationCase,
    evidence: Sequence[EvidenceCandidate],
) -> list[EvidenceCandidate]:
    requirements = {item.requirement_id: item for item in case.evidence_requirements}
    normalized: list[EvidenceCandidate] = []
    for item in evidence:
        requirement = requirements.get(item.requirement_id)
        if requirement is None:
            continue
        anchor = item.published_at or item.observed_at
        age_seconds = (case.cutoff_at - anchor).total_seconds()
        freshness = (
            "fresh"
            if 0 <= age_seconds <= requirement.freshness_seconds
            else "stale"
        )
        normalized.append(
            item.model_copy(
                update={
                    "quality": "accepted" if freshness == "fresh" else "stale",
                    "freshness_status": freshness,
                }
            )
        )
    return normalized


def _horizons_distinct(result: ResearchSessionResult | None) -> bool:
    if result is None or len(result.horizons) != 3:
        return False
    horizons = {item.horizon for item in result.horizons}
    if horizons != {"30m", "24h", "72h"}:
        return False
    semantic = {
        (
            item.action,
            item.subjective_probability,
            tuple(item.evidence_refs),
            item.trigger,
            item.invalidation,
        )
        for item in result.horizons
    }
    return len(semantic) == len(result.horizons)


def _summarize(
    runtime_id: str,
    reports: Sequence[ResearchRuntimeCaseReport],
) -> ResearchRuntimeSummary:
    selected = [item for item in reports if item.runtime_id == runtime_id]
    latencies = sorted(item.latency_ms for item in selected if item.latency_ms is not None)
    costs = [item.estimated_cost_usd for item in selected]
    known_costs = [float(item) for item in costs if item is not None]
    failures = Counter(
        item.failure_code for item in selected if item.failure_code is not None
    )
    return ResearchRuntimeSummary(
        runtime_id=runtime_id,
        runtime_version=selected[0].runtime_version if selected else "unknown",
        sample_count=len(selected),
        completed_count=sum(item.status in {"completed", "degraded"} for item in selected),
        failed_count=sum(item.status in {"failed", "timeout"} for item in selected),
        hard_coverage_mean=(
            sum(item.hard_coverage_ratio for item in selected) / len(selected)
            if selected
            else 0.0
        ),
        evidence_count=sum(item.evidence_count for item in selected),
        unattested_evidence_count=sum(item.unattested_evidence_count for item in selected),
        pit_violations=sum(item.pit_violations for item in selected),
        horizon_distinct_count=sum(item.horizon_distinct for item in selected),
        total_tool_calls=sum(item.tool_calls for item in selected),
        total_subagents=sum(item.subagents for item in selected),
        total_tokens=(
            sum(item.total_tokens or 0 for item in selected)
            if any(item.total_tokens is not None for item in selected)
            else None
        ),
        estimated_cost_usd=(sum(known_costs) if len(known_costs) == len(selected) else None),
        p95_latency_ms=(
            latencies[min(len(latencies) - 1, math.ceil(len(latencies) * 0.95) - 1)]
            if latencies
            else None
        ),
        failure_counts=dict(failures),
    )

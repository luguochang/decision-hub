from __future__ import annotations

import asyncio
import hashlib
import json
import math
import time
from collections import Counter, defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, cast

from packages.contracts_py.decision_hub_contracts import (
    EvidenceCandidate,
    ExecutionBudget,
    FactEnvelope,
    ResearchEvaluationCase,
    ResearchRuntimeCaseReport,
    ResearchRuntimeComparison,
    ResearchRuntimeSummary,
    ResearchSessionRequest,
    ResearchSessionResult,
)
from packages.evals.research_dataset import ResearchEvaluationDataset
from packages.kernel.decision_hub_kernel.decision.horizon_contract import validate_horizon_contract
from packages.kernel.decision_hub_kernel.decision.sufficiency import (
    assess_evidence_sufficiency,
)
from packages.kernel.decision_hub_kernel.ports.research import ResearchHarnessRuntime
from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError

LEGACY_SCORER_VERSION = "research-runtime-comparison.admission.v2"
SCORER_VERSION = "research-runtime-comparison.horizon.v3"


def _validate_scorer_version(scorer_version: str) -> None:
    if scorer_version not in {LEGACY_SCORER_VERSION, SCORER_VERSION}:
        raise ValueError("research_comparison_scorer_unsupported")


class ResearchComparisonRunner:
    """Run two research runtimes on identical immutable cases and budgets."""

    def __init__(
        self,
        dataset: ResearchEvaluationDataset,
        *,
        budget: ExecutionBudget,
        output_dir: Path | None = None,
        scorer_version: str = SCORER_VERSION,
    ) -> None:
        _validate_scorer_version(scorer_version)
        self.dataset = dataset
        self.budget = budget
        self.output_dir = output_dir
        self.scorer_version = scorer_version

    async def run(
        self,
        *,
        experiment_id: str,
        baseline: ResearchHarnessRuntime,
        candidate: ResearchHarnessRuntime,
    ) -> ResearchRuntimeComparison:
        if self.output_dir is not None:
            targets = [self.output_dir / "comparison.json"] + [
                self.output_dir / case.case_id / f"{runtime_id}.json"
                for case in self.dataset.cases
                for runtime_id in (baseline.runtime_id, candidate.runtime_id)
            ]
            # Frozen v1 JSON has no scorer identity. It must not be overwritten
            # before the downstream ledger can reject an old experiment ID.
            if any(path.exists() for path in targets):
                raise ValueError("research_comparison_output_exists")
        reports: list[ResearchRuntimeCaseReport] = []
        for case in self.dataset.cases:
            reports.append(await self._run_case(case, experiment_id, baseline, baseline.runtime_id))
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
            conclusion=_g1_conclusion(summaries),
        )
        if self.output_dir is not None:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            _write_new_report(
                self.output_dir / "comparison.json", comparison.model_dump_json(indent=2),
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
            # The runtime stops model/tool work at the canonical deadline. A
            # short outer grace lets it reconcile already-written evidence
            # after the Host emits the cancellation terminal event; it does
            # not extend the research execution budget.
            async with asyncio.timeout(self.budget.total_deadline_seconds + 5):
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
            runtime.runtime_id,
            runtime.runtime_version,
            result,
            status,
            failure_code,
            started_at,
            finished_at,
            round((time.perf_counter() - started) * 1000),
            scorer_version=self.scorer_version,
        )
        if self.output_dir is not None:
            runtime_dir = self.output_dir / case.case_id
            runtime_dir.mkdir(parents=True, exist_ok=True)
            _write_new_report(
                runtime_dir / f"{runtime_id}.json", report.model_dump_json(indent=2),
            )
        return report


def _write_new_report(path: Path, content: str) -> None:
    # Preflight avoids starting on an old output; exclusive creation also
    # protects a file created by another writer while this runtime executes.
    try:
        with path.open("x", encoding="utf-8") as output:
            output.write(content)
    except FileExistsError as exc:
        raise ValueError("research_comparison_output_exists") from exc


def _request_for(
    case: ResearchEvaluationCase,
    experiment_id: str,
    runtime_id: str,
    budget: ExecutionBudget,
) -> ResearchSessionRequest:
    now = datetime.now(UTC)
    capabilities = tuple(sorted({item.capability_id for item in case.archived_capability_fixtures}))
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
    runtime_id: str,
    runtime_version: str,
    result: ResearchSessionResult | None,
    status: Literal["completed", "degraded", "failed", "timeout"],
    failure_code: str | None,
    started_at: datetime,
    finished_at: datetime,
    latency_ms: int | None,
    *,
    scorer_version: str = SCORER_VERSION,
) -> ResearchRuntimeCaseReport:
    _validate_scorer_version(scorer_version)
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
        result_facts = (result.facts or []) if result is not None else []
        scored_evidence = _evidence_for_scoring(case, evidence, result_facts)
        coverage = assess_evidence_sufficiency(
            case.evidence_requirements,
            scored_evidence,
            cutoff_at=case.cutoff_at,
            facts=_facts_for_scoring(result_facts, scored_evidence),
        )
        hard_gap_count = sum(1 for item in coverage.gaps if item.importance == "hard")
        hard_ratio = coverage.hard_coverage_ratio
    except (ValueError, TypeError):
        hard_gap_count = len(case.expected_hard_requirement_ids)
        hard_ratio = 0.0
        pit_violations += 1
    horizon_distinct = (
        _horizons_distinct(result)
        if scorer_version == LEGACY_SCORER_VERSION
        else result is not None and validate_horizon_contract(
            result.horizons, reference_at=case.cutoff_at,
        ).valid
    )
    citation_support_ratio = _citation_support_ratio(case, result)
    manual_rework_flags = _manual_rework_flags(
        result=result,
        status=status,
        failure_code=failure_code,
        hard_gap_count=hard_gap_count,
        pit_violations=pit_violations,
        citation_support_ratio=citation_support_ratio,
        horizon_distinct=horizon_distinct,
    )
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
        runtime_id=runtime_id,
        runtime_version=runtime_version,
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
        citation_support_ratio=citation_support_ratio,
        quality_success=not manual_rework_flags,
        manual_rework_flags=manual_rework_flags,
    )


def build_case_report(
    dataset_id: str,
    case: ResearchEvaluationCase,
    runtime: ResearchHarnessRuntime,
    result: ResearchSessionResult | None,
    status: Literal["completed", "degraded", "failed", "timeout"],
    failure_code: str | None,
    started_at: datetime,
    finished_at: datetime,
    latency_ms: int,
    *,
    scorer_version: str = SCORER_VERSION,
) -> ResearchRuntimeCaseReport:
    """Rebuild with an explicit scorer; old JSON's schema does not imply v3."""

    return _case_report(
        dataset_id,
        case,
        runtime.runtime_id,
        runtime.runtime_version,
        result,
        status,
        failure_code,
        started_at,
        finished_at,
        latency_ms,
        scorer_version=scorer_version,
    )


def validate_comparison_scores(
    dataset: ResearchEvaluationDataset,
    comparison: ResearchRuntimeComparison,
    *,
    scorer_version: str = SCORER_VERSION,
) -> None:
    """Verify selected scoring semantics without relabelling or rewriting history."""
    _validate_scorer_version(scorer_version)
    if (
        comparison.dataset_id != dataset.manifest.dataset_id
        or comparison.dataset_manifest_hash != dataset.manifest.manifest_hash
        or comparison.baseline_runtime_id == comparison.candidate_runtime_id
    ):
        raise ValueError("research_comparison_scoring_identity_mismatch")
    cases = {case.case_id: case for case in dataset.cases}
    runtimes = (comparison.baseline_runtime_id, comparison.candidate_runtime_id)
    expected = {(case_id, runtime_id) for case_id in cases for runtime_id in runtimes}
    actual = [(report.case_id, report.runtime_id) for report in comparison.case_reports]
    if set(actual) != expected or len(actual) != len(expected):
        raise ValueError("research_comparison_scoring_cohort_mismatch")
    for report in comparison.case_reports:
        rebuilt = _case_report(
            comparison.dataset_id, cases[report.case_id], report.runtime_id,
            report.runtime_version, report.result, report.status, report.failure_code,
            report.started_at, report.finished_at, report.latency_ms,
            scorer_version=scorer_version,
        )
        if rebuilt.model_dump() != report.model_dump():
            raise ValueError("research_comparison_scoring_result_mismatch")
    summaries = [_summarize(runtime_id, comparison.case_reports) for runtime_id in runtimes]
    if (
        [summary.model_dump() for summary in comparison.summaries]
        != [summary.model_dump() for summary in summaries]
        or comparison.conclusion != _g1_conclusion(summaries)
    ):
        raise ValueError("research_comparison_scoring_summary_mismatch")


def _evidence_for_scoring(
    case: ResearchEvaluationCase,
    evidence: Sequence[EvidenceCandidate],
    facts: Sequence[FactEnvelope] = (),
) -> list[EvidenceCandidate]:
    """Reassess accepted inputs at the case cutoff without granting admission."""
    requirements = {item.requirement_id: item for item in case.evidence_requirements}
    fact_offsets_by_evidence: dict[str, set[tuple[str, str | None]]] = defaultdict(set)
    for fact in facts:
        if (
            fact.quality == "accepted"
            and fact.observed_at <= fact.received_at <= case.cutoff_at
            and (fact.published_at is None or fact.published_at <= fact.observed_at)
        ):
            fact_offsets_by_evidence[fact.evidence_id].add(
                (fact.requirement_id, fact.event_offset)
            )
    normalized: list[EvidenceCandidate] = []
    for item in evidence:
        requirement = requirements.get(item.requirement_id)
        if requirement is None:
            continue
        if item.quality != "accepted":
            normalized.append(item)
            continue
        if item.kind != "market" and item.published_at is None:
            normalized.append(item.model_copy(update={
                "quality": "candidate", "freshness_status": "unknown",
            }))
            continue
        anchor = item.published_at or item.observed_at
        age_seconds = (case.cutoff_at - anchor).total_seconds()
        is_required_baseline = (
            fact_offsets_by_evidence.get(item.evidence_id, set())
            == {(item.requirement_id, "baseline")}
            and "baseline" in (requirement.required_event_offsets or [])
        )
        freshness = (
            "fresh"
            if item.observed_at <= item.received_at <= case.cutoff_at
            and (item.published_at is None or item.published_at <= item.observed_at)
            and (is_required_baseline or 0 <= age_seconds <= requirement.freshness_seconds)
            else "stale"
        )
        normalized.append(
            item.model_copy(
                update={
                    "quality": (
                        "candidate" if item.authority == "search_derived"
                        else "accepted" if freshness == "fresh" else "stale"
                    ),
                    "freshness_status": freshness,
                }
            )
        )
    return normalized


def evidence_for_scoring(
    case: ResearchEvaluationCase,
    evidence: Sequence[EvidenceCandidate],
    facts: Sequence[FactEnvelope] = (),
) -> list[EvidenceCandidate]:
    """Public wrapper for deterministic audit tooling."""

    return _evidence_for_scoring(case, evidence, facts)


def _facts_for_scoring(
    facts: Sequence[FactEnvelope],
    evidence: Sequence[EvidenceCandidate],
) -> list[FactEnvelope]:
    """Only downgrade accepted facts; an accepted parent cannot admit a fact."""
    quality_by_evidence = {item.evidence_id: item.quality for item in evidence}
    normalized: list[FactEnvelope] = []
    for item in facts:
        quality = quality_by_evidence.get(item.evidence_id)
        if quality is None:
            continue
        normalized.append(
            item.model_copy(update={"quality": quality})
            if item.quality == "accepted" else item
        )
    return normalized


def facts_for_scoring(
    facts: Sequence[FactEnvelope],
    evidence: Sequence[EvidenceCandidate],
) -> list[FactEnvelope]:
    """Public wrapper for deterministic audit tooling."""

    return _facts_for_scoring(facts, evidence)


def _horizons_distinct(result: ResearchSessionResult | None) -> bool:
    """Frozen admission.v2 definition, retained only for explicit legacy scoring."""
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


def _citation_support_ratio(
    case: ResearchEvaluationCase,
    result: ResearchSessionResult | None,
) -> float:
    """Score every emitted citation against the immutable input/result set."""

    if result is None:
        return 0.0
    allowed = {case.trigger_evidence.evidence_id}
    allowed.update(item.evidence_id for item in result.evidence_candidates)
    refs: list[str] = []
    if result.causal_case is not None:
        refs.extend(result.causal_case.evidence_refs)
        refs.extend(ref for link in result.causal_case.main_chain for ref in link.evidence_refs)
        refs.extend(ref for link in result.causal_case.opposite_chain for ref in link.evidence_refs)
    refs.extend(ref for horizon in result.horizons for ref in horizon.evidence_refs)
    if not refs:
        return 0.0
    return sum(ref in allowed for ref in refs) / len(refs)


def _manual_rework_flags(
    *,
    result: ResearchSessionResult | None,
    status: str,
    failure_code: str | None,
    hard_gap_count: int,
    pit_violations: int,
    citation_support_ratio: float,
    horizon_distinct: bool,
) -> list[str]:
    flags: list[str] = []
    if result is None:
        flags.append(failure_code or "missing_result")
        return flags
    if status != "completed" or result.status != "completed":
        flags.append("degraded_or_failed_status")
    if hard_gap_count:
        flags.append("hard_evidence_gap")
    if pit_violations:
        flags.append("pit_violation")
    if citation_support_ratio < 1.0:
        flags.append("unsupported_citation")
    if not horizon_distinct:
        flags.append("horizon_contract")
    if result.runtime_id == "dsh" and not result.reviews:
        flags.append("independent_review_receipt")
    if result.stop_reason.code != "sufficient":
        flags.append("bounded_stop")
    return list(dict.fromkeys(flags))


def _summarize(
    runtime_id: str,
    reports: Sequence[ResearchRuntimeCaseReport],
) -> ResearchRuntimeSummary:
    selected = [item for item in reports if item.runtime_id == runtime_id]
    latencies = sorted(item.latency_ms for item in selected if item.latency_ms is not None)
    costs = [item.estimated_cost_usd for item in selected]
    known_costs = [float(item) for item in costs if item is not None]
    failures = Counter(item.failure_code for item in selected if item.failure_code is not None)
    return ResearchRuntimeSummary(
        runtime_id=runtime_id,
        runtime_version=selected[0].runtime_version if selected else "unknown",
        sample_count=len(selected),
        completed_count=sum(item.status in {"completed", "degraded"} for item in selected),
        failed_count=sum(item.status in {"failed", "timeout"} for item in selected),
        hard_coverage_mean=(
            sum(item.hard_coverage_ratio for item in selected) / len(selected) if selected else 0.0
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
        citation_support_mean=(
            sum(item.citation_support_ratio or 0.0 for item in selected) / len(selected)
            if selected
            else 0.0
        ),
        quality_success_count=sum(bool(item.quality_success) for item in selected),
        manual_rework_count=sum(bool(item.manual_rework_flags) for item in selected),
    )


def _g1_conclusion(
    summaries: Sequence[ResearchRuntimeSummary],
) -> Literal["pending", "promotion_recommended", "retain_baseline"]:
    """Apply the conservative G1 decision after both runtimes finish.

    A candidate must improve an outcome that represents usable work (quality
    success or manual rework), while preserving sample coverage and safety
    invariants. A larger evidence count alone cannot promote a degraded run.
    """

    if len(summaries) != 2:
        return "pending"
    by_id = {item.runtime_id: item for item in summaries}
    baseline = by_id.get("fixed-baseline") or summaries[0]
    candidate = by_id.get("dsh") or summaries[1]
    if baseline.sample_count == 0 or candidate.sample_count != baseline.sample_count:
        return "pending"
    safety_preserved = (
        candidate.pit_violations <= baseline.pit_violations
        and candidate.unattested_evidence_count <= baseline.unattested_evidence_count
        and candidate.failed_count <= baseline.failed_count
        and candidate.hard_coverage_mean >= baseline.hard_coverage_mean
    )
    usable_improvement = (candidate.quality_success_count or 0) > (
        baseline.quality_success_count or 0
    ) or (candidate.manual_rework_count or 0) < (baseline.manual_rework_count or 0)
    return "promotion_recommended" if safety_preserved and usable_improvement else "retain_baseline"

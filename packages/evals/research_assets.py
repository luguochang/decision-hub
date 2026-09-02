from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

from packages.contracts_py.decision_hub_contracts import (
    CandidateVersion,
    ExperimentManifest,
    ExperimentResultView,
    ResearchRuntimeComparison,
)
from packages.evals.research_dataset import ResearchEvaluationDataset
from packages.kernel.decision_hub_kernel.application.evolution import (
    EvolutionAssetService,
)

SCORER_VERSION = "research-runtime-comparison.v1"
BASELINE_CANDIDATE_PREFIX = "runtime.fixed-baseline"
DSH_CANDIDATE_PREFIX = "runtime.dsh.decision-research"


class ResearchComparisonAssetRecorder:
    """Project one completed research comparison into the existing Evolution ledger."""

    def __init__(self, assets: EvolutionAssetService) -> None:
        self.assets = assets

    def record(
        self,
        *,
        dataset: ResearchEvaluationDataset,
        comparison: ResearchRuntimeComparison,
        output_dir: Path,
        provider_id: str,
        model: str,
        profile_ref: str,
        deadline_seconds: int,
        max_cost_usd: float | None,
        created_at: datetime,
    ) -> tuple[ExperimentManifest, tuple[ExperimentResultView, ExperimentResultView]]:
        self.assets.register_dataset(dataset.manifest)
        baseline = self.assets.register_candidate(
            _candidate(
                candidate_prefix=BASELINE_CANDIDATE_PREFIX,
                runtime_id=comparison.baseline_runtime_id,
                runtime_version=(
                    _summary(comparison, comparison.baseline_runtime_id).runtime_version
                ),
                profile_ref="fixed-policy-counter-synthesis.v1",
                provider_id=provider_id,
                model=model,
                created_at=created_at,
            )
        )
        candidate = self.assets.register_candidate(
            _candidate(
                candidate_prefix=DSH_CANDIDATE_PREFIX,
                runtime_id=comparison.candidate_runtime_id,
                runtime_version=(
                    _summary(comparison, comparison.candidate_runtime_id).runtime_version
                ),
                profile_ref=profile_ref,
                provider_id=provider_id,
                model=model,
                created_at=created_at,
            )
        )
        experiment = self.assets.register_experiment(
            ExperimentManifest(
                experiment_id=comparison.experiment_id,
                dataset_id=dataset.manifest.dataset_id,
                baseline_ref=baseline.candidate_id,
                candidate_refs=[candidate.candidate_id],
                status="running",
                created_at=created_at,
                strategy_version="crypto_macro.v1",
                runtime_id="fixed-vs-dsh",
                runtime_version="research-runtime-comparison.v1",
                provider_id=provider_id,
                model=model,
                schema_version="experiment.v1",
                random_seed=None,
                randomness_policy="provider_default",
                deadline_seconds=deadline_seconds,
                max_cost_usd=max_cost_usd,
            )
        )
        baseline_result = self.assets.record_result(
            _result(
                dataset,
                comparison,
                runtime_id=comparison.baseline_runtime_id,
                candidate_id=baseline.candidate_id,
                output_dir=output_dir,
                created_at=created_at,
            )
        )
        candidate_result = self.assets.record_result(
            _result(
                dataset,
                comparison,
                runtime_id=comparison.candidate_runtime_id,
                candidate_id=candidate.candidate_id,
                output_dir=output_dir,
                created_at=created_at,
            )
        )
        completed = self.assets.set_experiment_status(experiment.experiment_id, "completed")
        return completed, (baseline_result, candidate_result)


def _candidate(
    *,
    candidate_prefix: str,
    runtime_id: str,
    runtime_version: str,
    profile_ref: str,
    provider_id: str,
    model: str,
    created_at: datetime,
) -> CandidateVersion:
    semantic = {
        "model": model,
        "profile_ref": profile_ref,
        "provider_id": provider_id,
        "runtime_id": runtime_id,
        "runtime_version": runtime_version,
    }
    content_hash = hashlib.sha256(
        json.dumps(semantic, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return CandidateVersion(
        candidate_id=f"{candidate_prefix}.{content_hash[:16]}",
        candidate_type="runtime",
        content_hash=content_hash,
        version=runtime_version,
        parent_version=None,
        status="experimental",
        created_at=created_at,
        content_ref=profile_ref,
        source="r2-r-06c",
    )


def _result(
    dataset: ResearchEvaluationDataset,
    comparison: ResearchRuntimeComparison,
    *,
    runtime_id: str,
    candidate_id: str,
    output_dir: Path,
    created_at: datetime,
) -> ExperimentResultView:
    summary = _summary(comparison, runtime_id)
    cases = {item.case_id: item for item in dataset.cases}
    reports = [item for item in comparison.case_reports if item.runtime_id == runtime_id]
    family_counts = Counter(cases[item.case_id].event_family for item in reports)
    raw_refs = [str(output_dir / item.case_id / f"{runtime_id}.json") for item in reports]
    return ExperimentResultView(
        result_id=f"{comparison.experiment_id}:{candidate_id}",
        experiment_id=comparison.experiment_id,
        candidate_id=candidate_id,
        sample_count=summary.sample_count,
        brier_score=None,
        cost_usd=summary.estimated_cost_usd,
        p95_latency_ms=summary.p95_latency_ms,
        safety_violations=summary.pit_violations + summary.unattested_evidence_count,
        event_family_counts=dict(family_counts),
        created_at=created_at,
        stage="replay",
        failure_counts=summary.failure_counts,
        evidence_coverage=summary.hard_coverage_mean,
        directional_accuracy=None,
        raw_artifact_refs=raw_refs,
        scorer_version=SCORER_VERSION,
    )


def _summary(comparison: ResearchRuntimeComparison, runtime_id: str):
    return next(item for item in comparison.summaries if item.runtime_id == runtime_id)

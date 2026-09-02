from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path

from packages.contracts_py.decision_hub_contracts.models import (
    EvaluationDatasetManifest,
    ExperimentManifest,
    ExperimentResultView,
)
from packages.kernel.decision_hub_kernel.ports.runtime import AgentRuntime
from tools.replay.run_fixture import ReplayFixture, run_fixture

SCORER_VERSION = "evaluation.aggregate.v1"


class EvaluationRun:
    """Immutable result of executing one experiment's candidates."""

    def __init__(self, results: Sequence[ExperimentResultView], report_paths: Sequence[Path]):
        self.results = tuple(results)
        self.report_paths = tuple(report_paths)


class EvaluationRunner:
    """Run fair candidate comparisons using the existing PIT replay boundary.

    The runner deliberately does not know how to train, tune or publish a candidate.
    It executes every runtime against the same fixture list in an independent SQLite
    database, writes raw reports first, and only then produces aggregate result DTOs.
    """

    def __init__(self, work_dir: Path) -> None:
        self.work_dir = work_dir

    async def run(
        self,
        experiment: ExperimentManifest,
        dataset: EvaluationDatasetManifest,
        fixture_paths: Sequence[Path],
        runtimes: Mapping[str, AgentRuntime],
        *,
        strategy_versions: Mapping[str, str] | None = None,
    ) -> EvaluationRun:
        self._validate_manifest(experiment, dataset, fixture_paths, runtimes)
        candidate_ids = (experiment.baseline_ref, *experiment.candidate_refs)
        resolved_versions = strategy_versions or {
            candidate_id: experiment.strategy_version for candidate_id in candidate_ids
        }
        if set(resolved_versions) != set(candidate_ids) or any(
            not version for version in resolved_versions.values()
        ):
            raise ValueError("evaluation_strategy_version_set_mismatch")
        experiment_dir = self.work_dir / experiment.experiment_id
        experiment_dir.mkdir(parents=True, exist_ok=True)
        results: list[ExperimentResultView] = []
        report_paths: list[Path] = []
        for candidate_id in candidate_ids:
            runtime = runtimes[candidate_id]
            reports: list[dict[str, object]] = []
            database_path = experiment_dir / f"{candidate_id}.sqlite3"
            for index, fixture_path in enumerate(fixture_paths):
                report = await run_fixture(
                    fixture_path,
                    database_path,
                    strategy_version=resolved_versions[candidate_id],
                    runtime=runtime,
                )
                reports.append(report)
                raw_path = experiment_dir / f"{candidate_id}.{index}.json"
                raw_path.write_text(
                    json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2)
                )
                report_paths.append(raw_path)
            results.append(
                self._aggregate(
                    experiment,
                    dataset,
                    candidate_id,
                    reports,
                    report_paths[-len(fixture_paths) :],
                )
            )
        return EvaluationRun(results, report_paths)

    def _validate_manifest(
        self,
        experiment: ExperimentManifest,
        dataset: EvaluationDatasetManifest,
        fixture_paths: Sequence[Path],
        runtimes: Mapping[str, AgentRuntime],
    ) -> None:
        if dataset.dataset_id != experiment.dataset_id:
            raise ValueError("evaluation_dataset_mismatch")
        if dataset.split == "shadow" and dataset.source_mode != "prospective":
            raise ValueError("shadow_requires_prospective_source")
        if dataset.split in {"replay", "holdout"} and dataset.source_mode != "fixture":
            raise ValueError("offline_split_requires_fixture_source")
        if not fixture_paths:
            raise ValueError("evaluation_fixture_required")
        required = {experiment.baseline_ref, *experiment.candidate_refs}
        if set(runtimes) != required:
            raise ValueError("evaluation_runtime_set_mismatch")
        for path in fixture_paths:
            if not path.exists():
                raise ValueError(f"evaluation_fixture_not_found:{path}")
        if tuple(dataset.fixture_refs) != tuple(str(path) for path in fixture_paths):
            raise ValueError("evaluation_leakage")
        family_counts = Counter[str]()
        for path in fixture_paths:
            expected_hash = dataset.fixture_hashes.get(str(path))
            actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            if expected_hash != actual_hash:
                raise ValueError("evaluation_leakage")
            fixture = ReplayFixture.model_validate_json(path.read_text())
            if fixture.split != dataset.split:
                raise ValueError("evaluation_leakage")
            received_at = fixture.received_at
            observed_at = fixture.observation.observed_at
            published_at = fixture.observation.published_at
            if (
                received_at.tzinfo is None
                or observed_at is None
                or observed_at.tzinfo is None
                or not dataset.window_start_at <= received_at <= dataset.window_end_at
                or observed_at > received_at
                or (published_at is not None and published_at > observed_at)
            ):
                raise ValueError("evaluation_leakage")
            if any(
                outcome.available_at.tzinfo is None or outcome.available_at <= received_at
                for outcome in fixture.outcomes
            ):
                raise ValueError("evaluation_leakage")
            family_counts[fixture.observation.event_hint or "unknown"] += 1
        if dict(family_counts) != dataset.event_family_counts:
            raise ValueError("evaluation_leakage")

    @staticmethod
    def _aggregate(
        experiment: ExperimentManifest,
        dataset: EvaluationDatasetManifest,
        candidate_id: str,
        reports: Sequence[dict[str, object]],
        raw_artifact_paths: Sequence[Path],
    ) -> ExperimentResultView:
        evaluations = [
            value for report in reports if isinstance(value := report.get("evaluation_count"), int)
        ]
        sample_count = sum(evaluations)
        briers = [
            float(value)
            for report in reports
            if isinstance(value := report.get("mean_brier_score"), (int, float))
        ]
        latencies = sorted(
            int(value) for report in reports if isinstance(value := report.get("latency_ms"), int)
        )
        costs: list[float] = []
        cost_known = True
        for report in reports:
            value = report.get("cost_usd")
            if isinstance(value, (int, float)):
                costs.append(float(value))
            else:
                cost_known = False
        failures = Counter[str]()
        family_counts = Counter[str]()
        coverages: list[float] = []
        accuracies: list[float] = []
        for report in reports:
            event_family = str(report.get("event_family") or "unknown")
            raw_count = report.get("evaluation_count")
            family_counts[event_family] += max(1, raw_count if isinstance(raw_count, int) else 0)
            raw_failures = report.get("failure_counts")
            if isinstance(raw_failures, dict):
                failures.update({str(key): int(value) for key, value in raw_failures.items()})
            coverage = report.get("evidence_coverage")
            if isinstance(coverage, (int, float)):
                coverages.append(float(coverage))
            accuracy = report.get("directional_accuracy")
            if isinstance(accuracy, (int, float)):
                accuracies.append(float(accuracy))
        p95 = None
        if latencies:
            p95 = latencies[min(len(latencies) - 1, math.ceil(len(latencies) * 0.95) - 1)]
        return ExperimentResultView.model_validate(
            {
                "result_id": f"{experiment.experiment_id}:{candidate_id}",
                "experiment_id": experiment.experiment_id,
                "candidate_id": candidate_id,
                "sample_count": sample_count,
                "brier_score": sum(briers) / len(briers) if briers else None,
                "cost_usd": sum(costs) if cost_known and costs else None,
                "p95_latency_ms": p95,
                "safety_violations": sum(
                    1 for report in reports if report.get("status") == "failed"
                ),
                "event_family_counts": dict(family_counts),
                "created_at": dataset.created_at,
                "stage": (
                    dataset.split if dataset.split in {"replay", "holdout", "shadow"} else "replay"
                ),
                "failure_counts": dict(failures),
                "evidence_coverage": (sum(coverages) / len(coverages) if coverages else None),
                "directional_accuracy": (sum(accuracies) / len(accuracies) if accuracies else None),
                "raw_artifact_refs": [str(path) for path in raw_artifact_paths],
                "scorer_version": SCORER_VERSION,
            }
        )

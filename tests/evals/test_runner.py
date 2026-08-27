from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from packages.contracts_py.decision_hub_contracts.models import (
    EvaluationDatasetManifest,
    ExperimentManifest,
)
from packages.evals.runner import EvaluationRunner
from packages.kernel.decision_hub_kernel.application.evolution import dataset_manifest_hash
from packages.runtime_adapters.candidate_runtime import CandidateAgentRuntime
from packages.runtime_adapters.fake_runtime.runtime import FakeAgentRuntime


def evaluation_dataset(
    fixture_path: Path, *, fixture_hash: str | None = None
) -> EvaluationDatasetManifest:
    now = datetime(2026, 8, 28, 12, tzinfo=UTC)
    fixture_hashes = {
        str(fixture_path): fixture_hash or hashlib.sha256(fixture_path.read_bytes()).hexdigest()
    }
    return EvaluationDatasetManifest(
        dataset_id="holdout.runner.v1",
        split="holdout",
        manifest_hash=dataset_manifest_hash(
            "holdout",
            (str(fixture_path),),
            "published_at <= observed_at <= received_at",
            fixture_hashes=fixture_hashes,
            window_start_at=datetime(2026, 8, 24, tzinfo=UTC),
            window_end_at=now,
            event_family_counts={"powell": 1},
        ),
        fixture_refs=[str(fixture_path)],
        fixture_hashes=fixture_hashes,
        cutoff_rule="published_at <= observed_at <= received_at",
        label_rule="outcomes.available_at > received_at",
        leakage_audit="passed",
        authorization="owner",
        visibility="owner_only",
        source_mode="fixture",
        window_start_at=datetime(2026, 8, 24, tzinfo=UTC),
        window_end_at=now,
        event_family_counts={"powell": 1},
        created_at=now,
    )


def evaluation_experiment(dataset: EvaluationDatasetManifest) -> ExperimentManifest:
    return ExperimentManifest(
        experiment_id="runner.exp.v1",
        dataset_id=dataset.dataset_id,
        baseline_ref="baseline",
        candidate_refs=["candidate"],
        status="registered",
        created_at=dataset.created_at,
        strategy_version="baseline.v1",
        runtime_id="evaluation-runner",
        runtime_version="evaluation-runner.v1",
        provider_id=None,
        model=None,
        schema_version="experiment.v1",
        random_seed=0,
        randomness_policy="deterministic",
        deadline_seconds=300,
        max_cost_usd=5,
    )


def test_runner_keeps_candidates_in_independent_databases(tmp_path: Path) -> None:
    fixture_path = Path("fixtures/replay/powell-higher-for-longer.json")
    dataset = evaluation_dataset(fixture_path)
    experiment = evaluation_experiment(dataset)
    baseline = FakeAgentRuntime()
    candidate = CandidateAgentRuntime(
        baseline.execute,
        runtime_id="candidate",
        runtime_version="candidate.v1",
    )
    result = asyncio.run(
        EvaluationRunner(tmp_path).run(
            experiment,
            dataset,
            (fixture_path,),
            {"baseline": baseline, "candidate": candidate},
        )
    )
    assert {item.candidate_id for item in result.results} == {"baseline", "candidate"}
    assert len(result.report_paths) == 2
    assert (tmp_path / experiment.experiment_id / "baseline.sqlite3").exists()
    assert (tmp_path / experiment.experiment_id / "candidate.sqlite3").exists()
    assert result.results[0].sample_count == 3
    assert result.results[0].raw_artifact_refs
    assert result.results[0].scorer_version == "evaluation.aggregate.v1"


@pytest.mark.parametrize("leakage", ["tampered_hash", "future_label", "wrong_split"])
def test_runner_rejects_leaky_or_tampered_fixtures(tmp_path: Path, leakage: str) -> None:
    source = Path("fixtures/replay/powell-higher-for-longer.json")
    fixture = tmp_path / "candidate.json"
    payload = json.loads(source.read_text())
    if leakage == "future_label":
        payload["outcomes"][0]["available_at"] = payload["received_at"]
    elif leakage == "wrong_split":
        payload["split"] = "replay"
    fixture.write_text(json.dumps(payload))
    stale_hash = "0" * 64 if leakage == "tampered_hash" else None
    dataset = evaluation_dataset(fixture, fixture_hash=stale_hash)
    experiment = evaluation_experiment(dataset)
    baseline = FakeAgentRuntime()
    candidate = CandidateAgentRuntime(
        baseline.execute, runtime_id="candidate", runtime_version="candidate.v1"
    )

    with pytest.raises(ValueError, match="evaluation_leakage"):
        asyncio.run(
            EvaluationRunner(tmp_path / "reports").run(
                experiment,
                dataset,
                (fixture,),
                {"baseline": baseline, "candidate": candidate},
            )
        )


def test_raw_report_survives_scorer_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = Path("fixtures/replay/powell-higher-for-longer.json")
    dataset = evaluation_dataset(fixture)
    experiment = evaluation_experiment(dataset)
    baseline = FakeAgentRuntime()
    candidate = CandidateAgentRuntime(
        baseline.execute, runtime_id="candidate", runtime_version="candidate.v1"
    )

    def fail_score(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("scorer_failed")

    monkeypatch.setattr(EvaluationRunner, "_aggregate", fail_score)
    with pytest.raises(RuntimeError, match="scorer_failed"):
        asyncio.run(
            EvaluationRunner(tmp_path).run(
                experiment,
                dataset,
                (fixture,),
                {"baseline": baseline, "candidate": candidate},
            )
        )
    assert (tmp_path / experiment.experiment_id / "baseline.0.json").is_file()


def test_runner_rejects_runtime_set_or_missing_fixture(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    cutoff = "published_at <= observed_at <= received_at"
    fixture_hashes = {"fixture": "0" * 64}
    dataset = EvaluationDatasetManifest(
        dataset_id="runner.invalid.v1",
        split="holdout",
        manifest_hash=dataset_manifest_hash(
            "holdout",
            ("fixture",),
            cutoff,
            fixture_hashes=fixture_hashes,
            window_start_at=now - timedelta(days=60),
            window_end_at=now - timedelta(days=30),
            event_family_counts={"powell": 1},
        ),
        fixture_refs=["fixture"],
        fixture_hashes=fixture_hashes,
        cutoff_rule=cutoff,
        label_rule="outcomes.available_at > received_at",
        leakage_audit="passed",
        authorization="owner",
        visibility="owner_only",
        source_mode="fixture",
        window_start_at=now - timedelta(days=60),
        window_end_at=now - timedelta(days=30),
        event_family_counts={"powell": 1},
        created_at=now,
    )
    experiment = ExperimentManifest(
        experiment_id="runner.invalid.exp",
        dataset_id=dataset.dataset_id,
        baseline_ref="baseline",
        candidate_refs=["candidate"],
        status="registered",
        created_at=now,
        strategy_version="baseline.v1",
        runtime_id="evaluation-runner",
        runtime_version="evaluation-runner.v1",
        provider_id=None,
        model=None,
        schema_version="experiment.v1",
        random_seed=0,
        randomness_policy="deterministic",
        deadline_seconds=300,
        max_cost_usd=5,
    )
    try:
        asyncio.run(EvaluationRunner(tmp_path).run(experiment, dataset, (), {}))
    except ValueError as exc:
        assert str(exc) == "evaluation_fixture_required"
    else:  # pragma: no cover
        raise AssertionError("runner accepted an empty fixture set")

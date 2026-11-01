"""Scoring consumes admission decisions; it never admits raw replay inputs."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import pytest

from packages.contracts_py.decision_hub_contracts import (
    ResearchCapabilityQuery,
    ResearchRuntimeComparison,
)
from packages.evals.research_assets import SCORER_VERSION, ResearchComparisonAssetRecorder
from packages.evals.research_comparison import (
    ResearchComparisonRunner,
    evidence_for_scoring,
    facts_for_scoring,
)
from packages.evals.research_dataset import (
    ResearchEvaluationDataset,
    load_research_evaluation_dataset,
)
from packages.kernel.decision_hub_kernel.application.evolution import EvolutionAssetService
from packages.kernel.decision_hub_kernel.decision.sufficiency import assess_evidence_sufficiency
from packages.kernel.decision_hub_kernel.persistence.db import (
    CandidateVersionRecord,
    Database,
    EvaluationDatasetRecord,
    ExperimentRecord,
    ExperimentResultRecord,
)
from packages.provider_adapters.research import ReplayResearchCapabilityAdapter, load_replay_archive
from tests.evals.test_research_comparison import BUDGET, DATASET, CandidateRuntimeFixture

RejectedQuality = Literal["candidate", "stale", "rejected", "conflicted"]
QUALITIES = ("candidate", "stale", "rejected", "conflicted")
NOW = datetime(2026, 10, 1, tzinfo=UTC)


def _official_case():
    case = load_research_evaluation_dataset(DATASET).cases[0]
    fixture = next(item for item in case.archived_capability_fixtures
                   if item.requirement_id == "event_identity")
    return case, fixture.evidence_candidates[0]


def _market_case():
    dataset = load_research_evaluation_dataset(
        Path("packs/crypto_macro/evaluations/r2r_dev_core_v2/manifest.json")
    )
    case = next(item for item in dataset.cases if item.case_id == "fomc_cut_25bp_20241218")
    fixture = next(item for item in case.archived_capability_fixtures
                   if item.requirement_id == "expectation_pricing")
    return case, fixture.evidence_candidates[0], fixture.facts or []


@pytest.mark.parametrize("quality", QUALITIES)
def test_recent_unadmitted_material_never_becomes_accepted(quality: RejectedQuality) -> None:
    case, template = _official_case()
    evidence = template.model_copy(update={"quality": quality, "freshness_status": "unknown"})
    original = evidence.model_dump(mode="json")

    scored = evidence_for_scoring(case, [evidence])
    requirement = next(item for item in case.evidence_requirements
                       if item.requirement_id == evidence.requirement_id)
    coverage = assess_evidence_sufficiency([requirement], scored, cutoff_at=case.cutoff_at)

    assert scored[0].model_dump(mode="json") == original
    assert coverage.hard_coverage_ratio == 0
    assert evidence.model_dump(mode="json") == original


def test_accepted_historical_input_reassesses_freshness_without_changing_identity() -> None:
    case, template = _official_case()
    evidence = template.model_copy(update={"quality": "accepted", "freshness_status": "stale"})
    scored = evidence_for_scoring(case, [evidence])[0]

    assert scored.quality == "accepted"
    assert scored.freshness_status == "fresh"
    assert scored.model_dump(exclude={"quality", "freshness_status"}) == evidence.model_dump(
        exclude={"quality", "freshness_status"}
    )
    assert evidence.freshness_status == "stale"


def test_accepted_official_content_without_publication_is_unknown() -> None:
    case, template = _official_case()
    evidence = template.model_copy(update={
        "quality": "accepted", "published_at": None,
        "observed_at": case.cutoff_at - timedelta(seconds=1),
    })
    scored = evidence_for_scoring(case, [evidence])[0]

    assert scored.quality == "candidate"
    assert scored.freshness_status == "unknown"


def test_accepted_market_observation_keeps_observation_time_semantics() -> None:
    case, template, _ = _market_case()
    evidence = template.model_copy(update={
        "quality": "accepted", "published_at": None,
        "observed_at": case.cutoff_at - timedelta(seconds=1), "received_at": case.cutoff_at,
    })
    scored = evidence_for_scoring(case, [evidence])[0]

    assert scored.quality == "accepted"
    assert scored.freshness_status == "fresh"


def test_search_locator_does_not_become_admitted_content() -> None:
    case, template = _official_case()
    scored = evidence_for_scoring(case, [template.model_copy(update={
        "quality": "accepted", "authority": "search_derived",
    })])[0]

    assert scored.quality == "candidate"


@pytest.mark.parametrize("quality", QUALITIES)
def test_accepted_parent_cannot_admit_rejected_fact(quality: RejectedQuality) -> None:
    _, template, facts = _market_case()
    parent = template.model_copy(update={"quality": "accepted"})
    fact = facts[0].model_copy(update={"quality": quality})

    assert facts_for_scoring([fact], [parent])[0].model_dump() == fact.model_dump()


@pytest.mark.parametrize("quality", QUALITIES)
def test_unadmitted_parent_downgrades_previously_accepted_fact(quality: RejectedQuality) -> None:
    _, template, facts = _market_case()
    parent = template.model_copy(update={"quality": quality})
    fact = facts[0].model_copy(update={"quality": "accepted"})
    scored = facts_for_scoring([fact], [parent])[0]

    assert scored.quality == quality
    assert fact.quality == "accepted"
    assert scored.payload_hash == fact.payload_hash


@pytest.mark.parametrize("quality", QUALITIES)
def test_baseline_exception_cannot_admit_unadmitted_material(quality: RejectedQuality) -> None:
    case, template, facts = _market_case()
    evidence = template.model_copy(update={"quality": quality})
    baseline = next(item for item in facts if item.event_offset == "baseline").model_copy(
        update={"quality": "accepted"}
    )

    assert evidence_for_scoring(case, [evidence], [baseline])[0].quality == quality


@pytest.mark.parametrize("quality", QUALITIES)
def test_unadmitted_baseline_fact_cannot_freshen_old_accepted_parent(
    quality: RejectedQuality,
) -> None:
    case, template, facts = _market_case()
    old = case.cutoff_at - timedelta(hours=24)
    evidence = template.model_copy(update={
        "quality": "accepted", "published_at": old, "observed_at": old,
        "received_at": case.cutoff_at,
    })
    baseline = next(item for item in facts if item.event_offset == "baseline").model_copy(
        update={"quality": quality, "observed_at": old, "received_at": case.cutoff_at}
    )

    assert evidence_for_scoring(case, [evidence], [baseline])[0].quality == "stale"


def test_valid_accepted_baseline_survives_but_future_baseline_does_not() -> None:
    case, template, facts = _market_case()
    old = case.cutoff_at - timedelta(hours=24)
    evidence = template.model_copy(update={
        "quality": "accepted", "published_at": old, "observed_at": old,
        "received_at": case.cutoff_at,
    })
    baseline = next(item for item in facts if item.event_offset == "baseline").model_copy(
        update={"quality": "accepted", "published_at": old, "observed_at": old,
                "received_at": case.cutoff_at}
    )

    assert evidence_for_scoring(case, [evidence], [baseline])[0].quality == "accepted"
    late = baseline.model_copy(update={"received_at": case.cutoff_at + timedelta(seconds=1)})
    assert evidence_for_scoring(case, [evidence], [late])[0].quality == "stale"
    future = evidence.model_copy(update={"received_at": case.cutoff_at + timedelta(seconds=1)})
    assert evidence_for_scoring(case, [future], [baseline])[0].quality == "stale"


@pytest.mark.asyncio
async def test_actual_replay_adapter_requires_kernel_admission_before_scoring() -> None:
    dataset = load_research_evaluation_dataset(DATASET)
    case = dataset.cases[0]
    fixture = case.archived_capability_fixtures[0]
    archive = load_replay_archive(dataset.case_paths[0])
    adapter = ReplayResearchCapabilityAdapter(
        capability_id="replay.research", fixtures=archive.queries,
        requirement_fixtures=archive.requirements,
    )
    result = await adapter.execute(ResearchCapabilityQuery(
        schema_version="research-capability-query.v1", request_id="pure-admission-test",
        capability_id="replay.research", requirement_id=fixture.requirement_id,
        query=fixture.query_aliases[0], target_url=None, symbols=[], fields=[], allowed_domains=[],
        max_results=20, max_cost_usd=0, research_session_id="pure-admission-test", round=1,
        mode="replay", observed_at=case.cutoff_at, cutoff_at=case.cutoff_at,
    ))
    scored = evidence_for_scoring(case, result.evidence_candidates, result.facts or [])
    coverage = assess_evidence_sufficiency(case.evidence_requirements, scored,
                                         cutoff_at=case.cutoff_at)

    assert scored and all(item.quality == "candidate" for item in scored)
    assert coverage.hard_coverage_ratio == 0


async def _comparison(output: Path):
    class Baseline(CandidateRuntimeFixture):
        runtime_id = "fixed-baseline"

    dataset = load_research_evaluation_dataset(DATASET)
    comparison = await ResearchComparisonRunner(dataset, budget=BUDGET, output_dir=output).run(
        experiment_id="admission-score-test", baseline=Baseline(),
        candidate=CandidateRuntimeFixture(),
    )
    return dataset, comparison


def _record(
    assets: EvolutionAssetService, dataset: ResearchEvaluationDataset,
    comparison: ResearchRuntimeComparison, output: Path,
):
    return ResearchComparisonAssetRecorder(assets).record(
        dataset=dataset, comparison=comparison, output_dir=output, provider_id="test",
        model="test", profile_ref="test.v1", deadline_seconds=30, max_cost_usd=1,
        created_at=NOW,
    )


def _rows(database: Database):
    with database.session() as session:
        return {
            model.__tablename__: [
                tuple(getattr(row, column.name) for column in model.__table__.columns)
                for row in session.query(model).order_by(list(model.__table__.primary_key)[0]).all()
            ]
            for model in (EvaluationDatasetRecord, CandidateVersionRecord, ExperimentRecord,
                          ExperimentResultRecord)
        }


@pytest.mark.asyncio
async def test_v2_asset_version_is_bound_and_repeated_recording_is_idempotent(
    tmp_path: Path,
) -> None:
    dataset, comparison = await _comparison(tmp_path / "reports")
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'assets.sqlite3'}")
    database.create_all()
    assets = EvolutionAssetService(database)
    experiment, results = _record(assets, dataset, comparison, tmp_path / "reports")
    before = _rows(database)
    repeated = _record(assets, dataset, comparison, tmp_path / "reports")

    assert experiment.runtime_version == SCORER_VERSION
    assert {result.scorer_version for result in results} == {SCORER_VERSION}
    assert repeated == (experiment, results)
    assert _rows(database) == before


@pytest.mark.asyncio
async def test_old_v1_experiment_cannot_be_silently_relabelled_as_v2(tmp_path: Path) -> None:
    dataset, comparison = await _comparison(tmp_path / "reports")
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'assets.sqlite3'}")
    database.create_all()
    assets = EvolutionAssetService(database)
    _record(assets, dataset, comparison, tmp_path / "reports")
    with database.session() as session:
        session.query(ExperimentRecord).update({
            "runtime_version": "research-runtime-comparison.v1",
        })
        session.query(ExperimentResultRecord).update({
            "scorer_version": "research-runtime-comparison.v1",
        })
    before = _rows(database)

    with pytest.raises(ValueError, match="experiment_request_reused"):
        _record(assets, dataset, comparison, tmp_path / "reports")

    assert _rows(database) == before


@pytest.mark.asyncio
async def test_wrong_admission_scores_are_rejected_before_any_asset_write(tmp_path: Path) -> None:
    dataset, comparison = await _comparison(tmp_path / "reports")
    report = comparison.case_reports[0]
    changed = comparison.model_copy(update={"case_reports": [
        report.model_copy(update={"hard_coverage_ratio": 1.0}), *comparison.case_reports[1:],
    ]})
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'assets.sqlite3'}")
    database.create_all()
    before = _rows(database)

    with pytest.raises(ValueError, match="research_comparison_scoring_result_mismatch"):
        _record(EvolutionAssetService(database), dataset, changed, tmp_path / "reports")

    assert _rows(database) == before


@pytest.mark.asyncio
async def test_raw_report_mismatch_is_rejected_before_asset_write(tmp_path: Path) -> None:
    dataset, comparison = await _comparison(tmp_path / "reports")
    report = comparison.case_reports[0]
    path = tmp_path / "reports" / report.case_id / f"{report.runtime_id}.json"
    body = json.loads(path.read_text(encoding="utf-8"))
    body["latency_ms"] += 1
    path.write_text(json.dumps(body), encoding="utf-8")
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'assets.sqlite3'}")
    database.create_all()
    before = _rows(database)

    with pytest.raises(ValueError, match="research_comparison_report_mismatch"):
        _record(EvolutionAssetService(database), dataset, comparison, tmp_path / "reports")

    assert _rows(database) == before

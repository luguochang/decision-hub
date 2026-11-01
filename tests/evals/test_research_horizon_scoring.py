"""Actual report/recorder boundaries; historical cutoff and explicit scorer versions."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path

import pytest

from packages.contracts_py.decision_hub_contracts import (
    HorizonDecision,
    ResearchEvaluationCase,
    ResearchRuntimeCaseReport,
    ResearchRuntimeComparison,
    ResearchSessionRequest,
    ResearchSessionResult,
)
from packages.evals import research_assets
from packages.evals.research_comparison import (
    SCORER_VERSION,
    ResearchComparisonRunner,
    build_case_report,
    validate_comparison_scores,
)
from packages.evals.research_dataset import (
    ResearchEvaluationDataset,
    load_research_evaluation_dataset,
)
from packages.kernel.decision_hub_kernel.application.evolution import EvolutionAssetService
from packages.kernel.decision_hub_kernel.persistence.db import (
    CandidateVersionRecord,
    Database,
    EvaluationDatasetRecord,
    ExperimentRecord,
    ExperimentResultRecord,
)
from packages.kernel.decision_hub_kernel.ports.research import ResearchTraceSink
from packages.runtime_adapters.fake_runtime.runtime import FakeAgentRuntime
from packages.runtime_adapters.fixed_research_runtime import FixedResearchRuntime
from tests.evals.test_research_comparison import BUDGET, DATASET, CandidateRuntimeFixture

LEGACY = "research-runtime-comparison.admission.v2"
CURRENT = "research-runtime-comparison.horizon.v3"
FINISHED = datetime(2026, 10, 2, tzinfo=UTC)
NOW = datetime(2026, 10, 1, tzinfo=UTC)


async def _material() -> tuple[
    ResearchEvaluationDataset, ResearchEvaluationCase, ResearchSessionResult,
]:
    dataset = load_research_evaluation_dataset(DATASET)
    case = dataset.cases[0]
    request = ResearchSessionRequest.model_validate({
        "schema_version": "research-session-request.v1", "request_id": "horizon-test",
        "run_id": "eval:horizon-test", "event_id": case.case_id,
        "trigger_snapshot_id": "trigger:horizon-test", "domain_pack_ref": "crypto_macro.v1",
        "role_profile_ref": "crypto_macro.manager.v1", "execution_mode": "replay",
        "pit_cutoff_at": case.cutoff_at, "current_round": 1,
        "evidence_refs": [case.trigger_evidence.evidence_id],
        "input_evidence": [case.trigger_evidence],
        "evidence_requirements": case.evidence_requirements,
        "target_gaps": [], "allowed_capabilities": sorted({
            fixture.capability_id for fixture in case.archived_capability_fixtures
        }), "execution_budget": BUDGET,
        "deadline_at": FINISHED, "output_schema_ref": "research-session-result.v1",
        "repair_instructions": None,
    })
    result = await CandidateRuntimeFixture().execute(request)
    return dataset, case, result


def _horizons(case: ResearchEvaluationCase, result: ResearchSessionResult) -> list[HorizonDecision]:
    first = result.horizons[0].model_copy(update={
        "action": "no_trade", "subjective_probability": 0.5,
        "evidence_refs": [
            case.trigger_evidence.evidence_id, result.evidence_candidates[0].evidence_id,
        ],
        "trigger": "Same confirmation", "invalidation": "Same invalidation",
        "next_review_at": case.cutoff_at + timedelta(minutes=5),
        "expires_at": case.cutoff_at + timedelta(minutes=30),
    })
    return [first.model_copy(update={"horizon": horizon}) for horizon in ("30m", "24h", "72h")]


def _report(case: ResearchEvaluationCase, result: ResearchSessionResult, **kwargs: str):
    return build_case_report(
        "fixture-horizon-dataset", case, CandidateRuntimeFixture(), result, "degraded", None,
        FINISHED - timedelta(seconds=1), FINISHED, 1_000, **kwargs,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("variant", ["probability", "direction", "refs"])
async def test_cosmetic_changes_do_not_create_three_scored_horizons(variant: str) -> None:
    _, case, result = await _material()
    horizons = _horizons(case, result)
    for index, horizon in enumerate(horizons):
        updates = (
            {"subjective_probability": 0.5 + index * 0.01} if variant == "probability"
            else {"action": ("no_trade", "long", "short")[index]} if variant == "direction"
            else {"evidence_refs": (
                horizon.evidence_refs, list(reversed(horizon.evidence_refs)),
                [*horizon.evidence_refs, horizon.evidence_refs[0]],
            )[index]}
        )
        horizons[index] = horizon.model_copy(update=updates)
    changed = result.model_copy(update={"horizons": horizons})
    original = changed.model_dump(mode="json")
    report = _report(case, changed)

    assert report.horizon_distinct is False
    assert "horizon_contract" in (report.manual_rework_flags or [])
    assert changed.model_dump(mode="json") == original


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["next_review_at", "expires_at"])
async def test_real_time_differences_score_at_case_cutoff_not_execution_finish(field: str) -> None:
    _, case, result = await _material()
    horizons = _horizons(case, result)
    for index, horizon in enumerate(horizons):
        horizons[index] = horizon.model_copy(update={
            field: getattr(horizon, field) + timedelta(minutes=index),
        })
    changed = result.model_copy(update={"horizons": horizons})
    assert all(horizon.expires_at < FINISHED for horizon in horizons)

    report = _report(case, changed)

    assert report.horizon_distinct is True
    assert "horizon_contract" not in (report.manual_rework_flags or [])
    assert report.cutoff_at == case.cutoff_at


@pytest.mark.asyncio
@pytest.mark.parametrize("variant", ["review_at_cutoff", "review_after_expiry", "blank_trigger"])
async def test_invalid_horizon_times_or_blank_basis_are_flagged(variant: str) -> None:
    _, case, result = await _material()
    horizons = _horizons(case, result)
    for index, horizon in enumerate(horizons):
        updates: dict[str, object] = {"trigger": f"confirmation {index}"}
        if variant == "review_at_cutoff":
            updates["next_review_at"] = case.cutoff_at
        elif variant == "review_after_expiry":
            updates["next_review_at"] = horizon.expires_at + timedelta(seconds=1)
        else:
            updates["trigger"] = " " * (index + 1)
        horizons[index] = horizon.model_copy(update=updates)

    report = _report(case, result.model_copy(update={"horizons": horizons}))

    assert report.horizon_distinct is False
    assert "horizon_contract" in (report.manual_rework_flags or [])


@pytest.mark.asyncio
async def test_legacy_version_rebuild_keeps_original_admission_v2_definition() -> None:
    _, case, result = await _material()
    horizons = [horizon.model_copy(update={"subjective_probability": 0.5 + index * 0.01})
                for index, horizon in enumerate(_horizons(case, result))]
    changed = result.model_copy(update={"horizons": horizons})
    legacy = _report(case, changed, scorer_version=LEGACY)
    saved = legacy.model_dump_json()

    assert legacy.horizon_distinct is True
    assert _report(case, changed).horizon_distinct is False
    assert ResearchRuntimeCaseReport.model_validate_json(saved).model_dump_json() == saved
    with pytest.raises(ValueError, match="research_comparison_scorer_unsupported"):
        _report(case, changed, scorer_version="unknown.v999")


@pytest.mark.asyncio
async def test_existing_comparison_output_rejected_before_runtime_or_file_changes(
    tmp_path: Path,
) -> None:
    dataset = load_research_evaluation_dataset(DATASET)
    output = tmp_path / "old-admission-v2"
    output.mkdir()
    original = b'{"legacy_scorer":"research-runtime-comparison.admission.v2"}\n'
    saved = output / "comparison.json"
    saved.write_bytes(original)

    class Uncalled(CandidateRuntimeFixture):
        calls = 0

        async def execute(
            self, request: ResearchSessionRequest, trace_sink: ResearchTraceSink | None = None,
        ) -> ResearchSessionResult:
            self.calls += 1
            raise AssertionError("old output must block before runtime execution")

    baseline = Uncalled()
    baseline.runtime_id = "baseline"
    candidate = Uncalled()
    with pytest.raises(ValueError, match="research_comparison_output_exists"):
        await ResearchComparisonRunner(dataset, budget=BUDGET, output_dir=output).run(
            experiment_id="existing-old-id", baseline=baseline, candidate=candidate,
        )

    assert saved.read_bytes() == original
    assert list(output.iterdir()) == [saved]
    assert baseline.calls == candidate.calls == 0
    assert hashlib.sha256(saved.read_bytes()).digest() == hashlib.sha256(original).digest()


@pytest.mark.asyncio
async def test_legacy_cohort_validates_only_under_explicit_original_scorer(tmp_path: Path) -> None:
    dataset = load_research_evaluation_dataset(DATASET)

    class ProbabilityOnly(CandidateRuntimeFixture):
        async def execute(
            self, request: ResearchSessionRequest, trace_sink: ResearchTraceSink | None = None,
        ) -> ResearchSessionResult:
            result = await super().execute(request, trace_sink)
            case = next(case for case in dataset.cases if case.case_id == request.event_id)
            return result.model_copy(update={"horizons": [
                horizon.model_copy(update={"subjective_probability": 0.5 + index * 0.01})
                for index, horizon in enumerate(_horizons(case, result))
            ]})

    baseline = ProbabilityOnly()
    baseline.runtime_id = "fixed-baseline"
    output = tmp_path / "legacy-rebuilt"
    comparison = await ResearchComparisonRunner(
        dataset, budget=BUDGET, output_dir=output, scorer_version=LEGACY,
    ).run(experiment_id="legacy-audit-only", baseline=baseline, candidate=ProbabilityOnly())
    before = {path: path.read_bytes() for path in output.rglob("*.json")}

    validate_comparison_scores(dataset, comparison, scorer_version=LEGACY)
    assert all(report.horizon_distinct for report in comparison.case_reports)
    with pytest.raises(ValueError, match="research_comparison_scoring_result_mismatch"):
        validate_comparison_scores(dataset, comparison)
    with pytest.raises(ValueError, match="research_comparison_scorer_unsupported"):
        validate_comparison_scores(dataset, comparison, scorer_version="unknown.v999")
    assert {path: path.read_bytes() for path in output.rglob("*.json")} == before
    assert SCORER_VERSION == CURRENT


@pytest.mark.asyncio
async def test_legacy_fixed_baseline_keeps_zero_for_time_only_distinction(tmp_path: Path) -> None:
    dataset = load_research_evaluation_dataset(DATASET)
    comparison = await ResearchComparisonRunner(
        dataset, budget=BUDGET, output_dir=tmp_path / "legacy-fixed", scorer_version=LEGACY,
    ).run(
        experiment_id="legacy-fixed-audit", baseline=FixedResearchRuntime(FakeAgentRuntime()),
        candidate=CandidateRuntimeFixture(),
    )

    baseline = next(summary for summary in comparison.summaries
                    if summary.runtime_id == "fixed-baseline")
    assert baseline.horizon_distinct_count == 0
    assert baseline.quality_success_count == 0
    validate_comparison_scores(dataset, comparison, scorer_version=LEGACY)


@pytest.mark.asyncio
async def test_stored_v2_assets_keep_identity_status_and_authority_on_v3_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset = load_research_evaluation_dataset(DATASET)
    baseline = CandidateRuntimeFixture()
    baseline.runtime_id = "fixed-baseline"
    output = tmp_path / "v2-history"
    comparison = await ResearchComparisonRunner(
        dataset, budget=BUDGET, output_dir=output, scorer_version=LEGACY,
    ).run(experiment_id="v2-frozen-history", baseline=baseline, candidate=CandidateRuntimeFixture())
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'assets.sqlite3'}")
    database.create_all()
    assets = EvolutionAssetService(database, clock=lambda: NOW)
    try:
        # Controlled v2 producer fixture: actual recorder/validator/SQLite run
        # with the historical version selected. Current production recorder
        # has no legacy-write mode and remains unchanged.
        with monkeypatch.context() as historical:
            historical.setattr(research_assets, "SCORER_VERSION", LEGACY)
            historical.setattr(
                research_assets, "validate_comparison_scores",
                partial(validate_comparison_scores, scorer_version=LEGACY),
            )
            experiment, results = _record_fixture(assets, dataset, comparison, output)
        before = _asset_rows(database)
        old_files = {path: path.read_bytes() for path in output.rglob("*.json")}
        assert experiment.runtime_version == LEGACY
        assert experiment.status == "completed"
        assert {result.scorer_version for result in results} == {LEGACY}
        assert {item.status for item in assets.overview().candidates} == {"experimental"}
        assert assets.overview().experiments[0] == experiment

        # These reports coincide under v2/v3, so the real blocker is the old
        # experiment version identity, rather than an earlier score mismatch.
        validate_comparison_scores(dataset, comparison)
        with pytest.raises(ValueError, match="experiment_request_reused"):
            _record_fixture(assets, dataset, comparison, output)

        assert _asset_rows(database) == before
        assert {path: path.read_bytes() for path in output.rglob("*.json")} == old_files
        assert assets.overview().experiments[0] == experiment
        assert {item.result_id: item for item in assets.overview().results} == {
            item.result_id: item for item in results
        }
    finally:
        database.engine.dispose()


@pytest.mark.asyncio
async def test_output_created_after_preflight_is_not_overwritten(tmp_path: Path) -> None:
    full_dataset = load_research_evaluation_dataset(DATASET)
    dataset = replace(
        full_dataset, cases=full_dataset.cases[:1], case_paths=full_dataset.case_paths[:1],
    )
    output = tmp_path / "output-race"
    saved = output / "comparison.json"
    original = b'{"concurrent_original":"must survive"}\n'

    class ConcurrentWriter(CandidateRuntimeFixture):
        async def execute(
            self, request: ResearchSessionRequest, trace_sink: ResearchTraceSink | None = None,
        ) -> ResearchSessionResult:
            output.mkdir(exist_ok=True)
            saved.write_bytes(original)
            return await super().execute(request, trace_sink)

    baseline = CandidateRuntimeFixture()
    baseline.runtime_id = "fixed-baseline"
    with pytest.raises(ValueError, match="research_comparison_output_exists"):
        await ResearchComparisonRunner(dataset, budget=BUDGET, output_dir=output).run(
            experiment_id="new-race-id", baseline=baseline, candidate=ConcurrentWriter(),
        )

    assert saved.read_bytes() == original


def _record_fixture(
    assets: EvolutionAssetService, dataset: ResearchEvaluationDataset,
    comparison: ResearchRuntimeComparison, output: Path,
):
    return research_assets.ResearchComparisonAssetRecorder(assets).record(
        dataset=dataset, comparison=comparison, output_dir=output, provider_id="test",
        model="test", profile_ref="test.v1", deadline_seconds=30, max_cost_usd=1,
        created_at=NOW,
    )


def _asset_rows(database: Database):
    with database.session() as session:
        return {
            model.__tablename__: [
                tuple(getattr(row, column.name) for column in model.__table__.columns)
                for row in session.query(model).order_by(list(model.__table__.primary_key)[0]).all()
            ]
            for model in (EvaluationDatasetRecord, CandidateVersionRecord, ExperimentRecord,
                          ExperimentResultRecord)
        }

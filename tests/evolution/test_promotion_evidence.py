"""Corrupt/incomplete evaluation ledgers must never authorize a new pointer."""

import json

import pytest

from packages.contracts_py.decision_hub_contracts.models import (
    PromotionDecisionCreate,
    PromotionReviewRequest,
)
from packages.kernel.decision_hub_kernel.application.evolution import EvolutionAssetService
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    EvaluationDatasetRecord,
    ExperimentResultRecord,
    PromotionDecisionRecord,
)
from tests.evolution.test_asset_lifecycle import NOW, add_stage, candidate


@pytest.fixture
def review_context(tmp_path):
    db = Database(f"sqlite+pysqlite:///{tmp_path / 'review.sqlite3'}")
    db.create_all()
    service = EvolutionAssetService(db, clock=lambda: NOW)
    baseline = service.register_candidate(candidate("baseline", "baseline.v1", artifact_root=tmp_path))
    challenger = service.register_candidate(candidate("challenger", "challenger.v1", artifact_root=tmp_path))
    refs = [
        *add_stage(service, baseline, challenger, "replay"),
        *add_stage(service, baseline, challenger, "holdout"),
        *add_stage(service, baseline, challenger, "shadow", source_mode="prospective"),
    ]
    assert service.promotion_eligible(challenger.candidate_id, refs)
    return db, service, refs


@pytest.mark.parametrize(("side", "field", "value", "reason"), [
    ("baseline", "cost_usd", None, "promotion_cost_unknown_or_invalid"),
    ("challenger", "cost_usd", None, "promotion_cost_unknown_or_invalid"),
    ("baseline", "cost_usd", float("inf"), "promotion_cost_unknown_or_invalid"),
    ("baseline", "sample_count", 99, "promotion_sample_count_insufficient"),
    ("baseline", "sample_count", 101, "promotion_sample_cohort_mismatch"),
    ("baseline", "safety_violations", 1, "promotion_safety_violation"),
    ("baseline", "brier_score", -0.1, "promotion_brier_invalid"),
    ("baseline", "p95_latency_ms", -1, "promotion_latency_exceeded"),
    ("challenger", "p95_latency_ms", -1, "promotion_latency_exceeded"),
    ("challenger", "scorer_version", "arbitrary.v99", "promotion_scorer_unrecognized"),
    ("baseline", "event_family_counts_json", '{"policy":0}', "promotion_event_family_insufficient"),
    ("baseline", "event_family_counts_json", '[]', "promotion_event_family_insufficient"),
])
def test_incomplete_comparison_cannot_be_approved(review_context, side, field, value, reason):
    db, service, refs = review_context
    with db.session() as session:
        setattr(session.get(ExperimentResultRecord, f"result.replay.v1.{side}"), field, value)
    review = service.review_promotion("crypto_macro.v1", PromotionReviewRequest(
        candidate_id="challenger", evaluation_refs=refs,
    ))
    assert not review.eligible
    assert reason in {check.reason_code for check in review.checks}
    json.dumps(review.model_dump(mode="json"), allow_nan=False)
    with pytest.raises(PermissionError, match="promotion_gate_failed"):
        service.decide("crypto_macro.v1", PromotionDecisionCreate(
            request_id="incomplete-review", candidate_id="challenger", owner="owner",
            decision="promote", reason="isolated verification", evaluation_refs=refs,
            expected_generation=0,
        ))
    assert service.active_pointer("crypto_macro.v1") is None
    with db.session() as session:
        assert session.query(PromotionDecisionRecord).count() == 0


@pytest.mark.parametrize("corruption", ["hash", "naive_time", "invalid_json", "future_end"])
def test_bad_manifest_returns_failed_review_instead_of_approval_or_500(review_context, corruption):
    db, service, refs = review_context
    with db.session() as session:
        row = session.get(EvaluationDatasetRecord, "shadow.v1")
        data = json.loads(row.manifest_json)
        if corruption == "hash":
            data["fixture_hashes"][data["fixture_refs"][0]] = "f" * 64
        elif corruption == "naive_time":
            data["window_start_at"] = "2026-06-01T00:00:00"
        elif corruption == "future_end":
            data["window_end_at"] = "2030-01-01T00:00:00Z"
        row.manifest_json = "{broken" if corruption == "invalid_json" else json.dumps(data)
    review = service.review_promotion("crypto_macro.v1", PromotionReviewRequest(
        candidate_id="challenger", evaluation_refs=refs,
    ))
    assert not review.eligible
    assert "promotion_dataset_integrity_invalid" in {c.reason_code for c in review.checks}


def test_ambiguous_baseline_is_rejected_without_server_error(review_context):
    db, service, refs = review_context
    with db.session() as session:
        row = session.get(ExperimentResultRecord, "result.replay.v1.baseline")
        values = {column.name: getattr(row, column.name) for column in row.__table__.columns}
        values["result_id"] = "duplicate-baseline"
        session.add(ExperimentResultRecord(**values))
    review = service.review_promotion("crypto_macro.v1", PromotionReviewRequest(
        candidate_id="challenger", evaluation_refs=refs,
    ))
    assert not review.eligible
    assert "experiment_lineage_invalid" in {c.reason_code for c in review.checks}


@pytest.mark.parametrize(("baseline", "challenger", "eligible"), [
    (0, 0, True), (0, 0.01, False), (1, 1.25, True), (1, 1.251, False), (None, None, False),
])
def test_cost_boundary_and_unknown_costs(review_context, baseline, challenger, eligible):
    db, service, refs = review_context
    with db.session() as session:
        session.get(ExperimentResultRecord, "result.replay.v1.baseline").cost_usd = baseline
        session.get(ExperimentResultRecord, "result.replay.v1.challenger").cost_usd = challenger
    assert service.promotion_eligible("challenger", refs) is eligible

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts.models import (
    CandidateVersion,
    EvaluationDatasetManifest,
    ExperienceCreate,
    ExperimentManifest,
    ExperimentResultView,
    FeedbackCreate,
    PromotionDecisionCreate,
    PromotionReviewRequest,
)
from packages.kernel.decision_hub_kernel.application.evolution import (
    EvolutionAssetService,
    dataset_manifest_hash,
)
from packages.kernel.decision_hub_kernel.application.workbench import WorkbenchAssetService
from packages.kernel.decision_hub_kernel.persistence.db import (
    ActivePointerRecord,
    CandidateVersionRecord,
    Database,
    EvaluationRecord,
    FailurePatternRecord,
    OutcomeRecord,
    PromotionDecisionRecord,
    SnapshotRecord,
)
from tools.replay.run_fixture import run_fixture

NOW = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)


def candidate(candidate_id: str, version: str) -> CandidateVersion:
    return CandidateVersion(
        candidate_id=candidate_id,
        candidate_type="strategy",
        content_hash=hashlib.sha256(candidate_id.encode()).hexdigest(),
        version=version,
        parent_version=None,
        status="candidate",
        created_at=NOW - timedelta(days=40),
        content_ref=None,
        source="owner",
    )


def make_dataset(
    split: Literal["replay", "holdout", "shadow"],
    dataset_id: str,
    *,
    source_mode: Literal["fixture", "prospective"] = "fixture",
    start: datetime = NOW - timedelta(days=60),
    end: datetime = NOW - timedelta(days=30),
    families: dict[str, int] | None = None,
) -> EvaluationDatasetManifest:
    families = families or {"policy": 30}
    cutoff_rule = "published_at <= observed_at <= received_at"
    fixture_ref = f"fixture:{dataset_id}"
    fixture_hashes = {fixture_ref: hashlib.sha256(fixture_ref.encode()).hexdigest()}
    return EvaluationDatasetManifest(
        dataset_id=dataset_id,
        split=split,
        manifest_hash=dataset_manifest_hash(
            split,
            (fixture_ref,),
            cutoff_rule,
            fixture_hashes=fixture_hashes,
            source_mode=source_mode,
            window_start_at=start,
            window_end_at=end,
            event_family_counts=families,
        ),
        fixture_refs=[fixture_ref],
        fixture_hashes=fixture_hashes,
        cutoff_rule=cutoff_rule,
        label_rule="outcomes.available_at > received_at",
        leakage_audit="passed",
        authorization="owner",
        visibility="owner_only",
        source_mode=source_mode,
        window_start_at=start,
        window_end_at=end,
        event_family_counts=families,
        created_at=end,
    )


def add_stage(
    service: EvolutionAssetService,
    baseline: CandidateVersion,
    challenger: CandidateVersion,
    split: Literal["replay", "holdout", "shadow"],
    *,
    source_mode: Literal["fixture", "prospective"] = "fixture",
    tag: str = "v1",
) -> list[str]:
    dataset = service.register_dataset(
        make_dataset(split, f"{split}.{tag}", source_mode=source_mode)
    )
    experiment = service.register_experiment(
        ExperimentManifest(
            experiment_id=f"exp.{split}.{tag}",
            dataset_id=dataset.dataset_id,
            baseline_ref=baseline.candidate_id,
            candidate_refs=[challenger.candidate_id],
            status="completed",
            created_at=NOW - timedelta(days=30),
            strategy_version="baseline.v1",
            runtime_id="fixture",
            runtime_version="fixture.v1",
            provider_id=None,
            model=None,
            schema_version="experiment.v1",
            random_seed=0,
            randomness_policy="deterministic",
            deadline_seconds=300,
            max_cost_usd=5,
        )
    )
    refs: list[str] = []
    for item in (baseline, challenger):
        result_id = f"result.{split}.{tag}.{item.candidate_id}"
        service.record_result(
            ExperimentResultView(
                result_id=result_id,
                experiment_id=experiment.experiment_id,
                candidate_id=item.candidate_id,
                sample_count=100,
                brier_score=0.20 if item is baseline else 0.21,
                cost_usd=1.0 if item is baseline else 1.2,
                p95_latency_ms=10_000,
                safety_violations=0,
                event_family_counts={"policy": 30},
                created_at=NOW - timedelta(days=29),
                stage=split,
                failure_counts={},
                evidence_coverage=1,
                directional_accuracy=1,
                raw_artifact_refs=[f"artifact:{result_id}"],
                scorer_version="fixture.v1",
            )
        )
        if item is challenger:
            refs.append(result_id)
    return refs


def test_dataset_is_immutable_and_requires_pit_rule(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'eval.sqlite3'}")
    database.create_all()
    service = EvolutionAssetService(database, clock=lambda: NOW)
    invalid = make_dataset("holdout", "holdout.invalid").model_copy(
        update={"cutoff_rule": "cutoff_at", "manifest_hash": "0" * 64}
    )
    with pytest.raises(ValueError, match="dataset_manifest_hash_mismatch"):
        service.register_dataset(invalid)

    pit_invalid = make_dataset("holdout", "holdout.pit-invalid").model_copy(
        update={
            "cutoff_rule": "cutoff_at",
            "manifest_hash": dataset_manifest_hash(
                "holdout",
                ("fixture:holdout.pit-invalid",),
                "cutoff_at",
                fixture_hashes={
                    "fixture:holdout.pit-invalid": hashlib.sha256(
                        b"fixture:holdout.pit-invalid"
                    ).hexdigest()
                },
                source_mode="fixture",
                window_start_at=NOW - timedelta(days=60),
                window_end_at=NOW - timedelta(days=30),
                event_family_counts={"policy": 30},
            ),
        }
    )
    with pytest.raises(ValueError, match="dataset_pit_rule_missing"):
        service.register_dataset(pit_invalid)


def test_promotion_requires_replay_holdout_shadow_and_is_idempotent(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'promotion.sqlite3'}")
    database.create_all()
    service = EvolutionAssetService(database, clock=lambda: NOW)
    baseline = service.register_candidate(candidate("baseline", "baseline.v1"))
    challenger = service.register_candidate(candidate("challenger", "challenger.v1"))

    replay_refs = add_stage(service, baseline, challenger, "replay")
    holdout_refs = add_stage(service, baseline, challenger, "holdout")
    shadow_refs = add_stage(service, baseline, challenger, "shadow", source_mode="prospective")
    request = PromotionDecisionCreate(
        request_id="promote-request-1",
        candidate_id=challenger.candidate_id,
        owner="owner",
        decision="promote",
        reason="Non-inferior replay, holdout and shadow result",
        evaluation_refs=[*replay_refs, *holdout_refs, *shadow_refs],
        expected_generation=0,
    )
    review = service.review_promotion(
        "crypto_macro.v1",
        PromotionReviewRequest(
            candidate_id=challenger.candidate_id,
            evaluation_refs=request.evaluation_refs,
        ),
    )
    assert review.eligible is True
    assert {item.stage for item in review.deltas} == {"replay", "holdout", "shadow"}
    assert all(item.status == "pass" for item in review.checks)
    first = service.decide("crypto_macro.v1", request)
    second = service.decide("crypto_macro.v1", request)
    assert first == second
    assert first.active_pointer is not None
    assert first.active_pointer.candidate_id == "challenger"
    assert first.active_pointer.generation == 1

    with pytest.raises(ValueError, match="promotion_request_reused"):
        service.decide(
            "crypto_macro.v1",
            request.model_copy(update={"candidate_id": "baseline"}),
        )

    with pytest.raises(RuntimeError, match="active_pointer_generation_conflict"):
        service.decide(
            "crypto_macro.v1",
            PromotionDecisionCreate(
                request_id="promote-request-2",
                candidate_id="challenger",
                owner="owner",
                decision="promote",
                reason="stale generation",
                evaluation_refs=request.evaluation_refs,
                expected_generation=0,
            ),
        )


def test_reject_does_not_change_pointer_and_rollback_requires_history(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'rollback.sqlite3'}")
    database.create_all()
    service = EvolutionAssetService(database, clock=lambda: NOW)
    seed = service.register_candidate(candidate("seed", "seed.v1"))
    baseline = service.register_candidate(candidate("baseline", "baseline.v1"))
    challenger = service.register_candidate(candidate("challenger", "challenger.v1"))
    seed_replay = add_stage(service, seed, baseline, "replay", tag="seed")
    seed_holdout = add_stage(service, seed, baseline, "holdout", tag="seed")
    seed_shadow = add_stage(
        service, seed, baseline, "shadow", source_mode="prospective", tag="seed"
    )
    seeded = service.decide(
        "crypto_macro.v1",
        PromotionDecisionCreate(
            request_id="promote-seed",
            candidate_id="baseline",
            owner="owner",
            decision="promote",
            reason="bootstrap baseline",
            evaluation_refs=[*seed_replay, *seed_holdout, *seed_shadow],
            expected_generation=0,
        ),
    )
    assert seeded.active_pointer is not None
    replay_refs = add_stage(service, baseline, challenger, "replay", tag="challenger")
    holdout_refs = add_stage(service, baseline, challenger, "holdout", tag="challenger")
    shadow_refs = add_stage(
        service, baseline, challenger, "shadow", source_mode="prospective", tag="challenger"
    )
    promoted = service.decide(
        "crypto_macro.v1",
        PromotionDecisionCreate(
            request_id="promote-rollback-1",
            candidate_id="challenger",
            owner="owner",
            decision="promote",
            reason="validated",
            evaluation_refs=[*replay_refs, *holdout_refs, *shadow_refs],
            expected_generation=1,
        ),
    )
    assert promoted.active_pointer is not None
    assert promoted.active_pointer.candidate_id == "challenger"

    rejected = service.decide(
        "crypto_macro.v1",
        PromotionDecisionCreate(
            request_id="reject-1",
            candidate_id="baseline",
            owner="owner",
            decision="reject",
            reason="keep as rollback reference",
            evaluation_refs=[],
            expected_generation=2,
        ),
    )
    assert rejected.active_pointer == promoted.active_pointer

    rolled_back = service.decide(
        "crypto_macro.v1",
        PromotionDecisionCreate(
            request_id="rollback-1",
            candidate_id="baseline",
            owner="owner",
            decision="rollback",
            reason="candidate regression",
            evaluation_refs=[],
            expected_generation=2,
        ),
    )
    assert rolled_back.active_pointer is not None
    assert rolled_back.active_pointer.candidate_id == "baseline"
    assert rolled_back.active_pointer.generation == 3
    with database.session() as session:
        rolled_back_candidate = session.get(CandidateVersionRecord, "baseline")
        prior_candidate = session.get(CandidateVersionRecord, "challenger")
        assert rolled_back_candidate is not None and rolled_back_candidate.status == "active"
        assert prior_candidate is not None and prior_candidate.status == "eligible"

    unknown = service.register_candidate(candidate("unknown", "unknown.v1"))
    with pytest.raises(PermissionError, match="rollback_target_not_verified"):
        service.decide(
            "crypto_macro.v1",
            PromotionDecisionCreate(
                request_id="rollback-unknown",
                candidate_id=unknown.candidate_id,
                owner="owner",
                decision="rollback",
                reason="not historical",
                evaluation_refs=[],
                expected_generation=3,
            ),
        )


def test_concurrent_first_promotion_has_one_atomic_cas_winner(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'concurrent-promotion.sqlite3'}")
    database.create_all()
    barrier = threading.Barrier(2)

    class ConcurrentPromotionService(EvolutionAssetService):
        def _promotion_eligible(
            self, session: Session, candidate_id: str, result_refs: list[str]
        ) -> bool:
            eligible = super()._promotion_eligible(session, candidate_id, result_refs)
            if eligible:
                barrier.wait(timeout=5)
            return eligible

    service = ConcurrentPromotionService(database, clock=lambda: NOW)
    baseline = service.register_candidate(candidate("concurrent-baseline", "baseline.v1"))
    challenger = service.register_candidate(candidate("concurrent-challenger", "challenger.v1"))
    refs = [
        *add_stage(service, baseline, challenger, "replay", tag="concurrent"),
        *add_stage(service, baseline, challenger, "holdout", tag="concurrent"),
        *add_stage(
            service,
            baseline,
            challenger,
            "shadow",
            source_mode="prospective",
            tag="concurrent",
        ),
    ]

    def promote(request_id: str) -> str:
        try:
            service.decide(
                "crypto_macro.v1",
                PromotionDecisionCreate(
                    request_id=request_id,
                    candidate_id=challenger.candidate_id,
                    owner="owner",
                    decision="promote",
                    reason="concurrent owner approval",
                    evaluation_refs=refs,
                    expected_generation=0,
                ),
            )
            return "promoted"
        except RuntimeError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(promote, ("concurrent-1", "concurrent-2")))

    assert sorted(outcomes) == ["active_pointer_generation_conflict", "promoted"]
    with database.session() as session:
        pointer = session.query(ActivePointerRecord).one()
        stored_candidate = session.get(CandidateVersionRecord, challenger.candidate_id)
        assert pointer.candidate_id == challenger.candidate_id
        assert pointer.generation == 1
        assert stored_candidate is not None and stored_candidate.status == "active"
        assert session.query(PromotionDecisionRecord).count() == 1


def test_promotion_transaction_rolls_back_pointer_candidate_and_audit(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'promotion-rollback.sqlite3'}")
    database.create_all()
    service = EvolutionAssetService(database, clock=lambda: NOW)
    baseline = service.register_candidate(candidate("failure-baseline", "baseline.v1"))
    challenger = service.register_candidate(candidate("failure-challenger", "challenger.v1"))
    refs = [
        *add_stage(service, baseline, challenger, "replay", tag="failure"),
        *add_stage(service, baseline, challenger, "holdout", tag="failure"),
        *add_stage(
            service,
            baseline,
            challenger,
            "shadow",
            source_mode="prospective",
            tag="failure",
        ),
    ]

    def interrupt_before_flush(
        _session: Session, _flush_context: object, _instances: object
    ) -> None:
        raise RuntimeError("injected_before_flush")

    event.listen(database.factory.class_, "before_flush", interrupt_before_flush)
    try:
        with pytest.raises(RuntimeError, match="injected_before_flush"):
            service.decide(
                "crypto_macro.v1",
                PromotionDecisionCreate(
                    request_id="interrupted-promotion",
                    candidate_id=challenger.candidate_id,
                    owner="owner",
                    decision="promote",
                    reason="must roll back as one transaction",
                    evaluation_refs=refs,
                    expected_generation=0,
                ),
            )
    finally:
        event.remove(database.factory.class_, "before_flush", interrupt_before_flush)

    with database.session() as session:
        stored_candidate = session.get(CandidateVersionRecord, challenger.candidate_id)
        assert stored_candidate is not None and stored_candidate.status == "candidate"
        assert session.query(ActivePointerRecord).count() == 0
        assert session.query(PromotionDecisionRecord).count() == 0


def test_negative_feedback_creates_and_regresses_failure_pattern(tmp_path: Path) -> None:
    database_path = tmp_path / "feedback.sqlite3"
    asyncio.run(run_fixture(Path("fixtures/replay/powell-higher-for-longer.json"), database_path))
    database = Database(f"sqlite+pysqlite:///{database_path}")
    run_id = database.latest_runs(1)[0].run_id
    workbench = WorkbenchAssetService(database)
    evolution = EvolutionAssetService(database, clock=lambda: NOW)
    workbench.create_feedback(
        FeedbackCreate(
            request_id="feedback-incorrect-1",
            target_type="run",
            target_id=run_id,
            created_by="owner",
            verdict="incorrect",
            notes="Transmission chain missed the policy timing.",
        )
    )
    first = {item.failure_code: item for item in evolution.refresh_failure_patterns()}
    assert first["feedback_incorrect"].source_refs
    assert first["feedback_incorrect"].status == "open"

    with database.session() as session:
        pattern = session.get(FailurePatternRecord, "failure:feedback_incorrect")
        assert pattern is not None
        pattern.status = "closed"
    workbench.create_feedback(
        FeedbackCreate(
            request_id="feedback-incorrect-2",
            target_type="run",
            target_id=run_id,
            created_by="owner",
            verdict="incorrect",
            notes="The same failure recurred after remediation.",
        )
    )
    refreshed = {item.failure_code: item for item in evolution.refresh_failure_patterns()}
    assert refreshed["feedback_incorrect"].occurrence_count == 2
    assert refreshed["feedback_incorrect"].status == "regressed"


def test_experience_requires_traceable_evidence_outcome_and_evaluation(tmp_path: Path) -> None:
    database_path = tmp_path / "experience.sqlite3"
    asyncio.run(run_fixture(Path("fixtures/replay/powell-higher-for-longer.json"), database_path))
    database = Database(f"sqlite+pysqlite:///{database_path}")
    with database.session() as session:
        outcome = session.query(OutcomeRecord).first()
        assert outcome is not None
        evaluation = (
            session.query(EvaluationRecord).filter_by(forecast_id=outcome.forecast_id).one()
        )
        snapshot = session.query(SnapshotRecord).first()
        assert snapshot is not None
        evidence_id = json.loads(snapshot.evidence_json)[0]["evidence_id"]
        available_at = max(outcome.observed_at, evaluation.evaluated_at).replace(tzinfo=UTC)
    service = EvolutionAssetService(database, clock=lambda: available_at + timedelta(seconds=1))
    request = ExperienceCreate(
        request_id="experience-1",
        domain_pack_ref="crypto_macro.v1",
        event_family="powell",
        lesson="Policy timing must be tied to the first tradable market window.",
        applicable_conditions=["fed_speech"],
        evidence_refs=[evidence_id],
        outcome_refs=[outcome.outcome_id],
        evaluation_refs=[evaluation.evaluation_id],
        source_type="evaluation",
        created_by="owner",
    )
    created = service.create_experience(request)
    assert created.status == "candidate"
    assert created.available_at == available_at

    with pytest.raises(ValueError, match="experience_evidence_not_found"):
        service.create_experience(
            request.model_copy(
                update={
                    "request_id": "experience-invalid",
                    "evidence_refs": ["missing-evidence"],
                }
            )
        )

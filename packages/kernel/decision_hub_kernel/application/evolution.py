from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from typing import Any, Literal, cast

from sqlalchemy import exists, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts.models import (
    ActivePointerView,
    CandidateVersion,
    EvaluationDatasetManifest,
    EvolutionOverviewView,
    ExperienceCreate,
    ExperienceView,
    ExperimentManifest,
    ExperimentResultView,
    FailurePatternView,
    PromotionDecisionCreate,
    PromotionDecisionResult,
    PromotionDecisionView,
    PromotionGateCheckView,
    PromotionMetricDeltaView,
    PromotionReviewRequest,
    PromotionReviewView,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    ActivePointerRecord,
    ArtifactRecord,
    CandidateVersionRecord,
    Database,
    EvaluationDatasetRecord,
    EvaluationRecord,
    ExperienceRecord,
    ExperimentRecord,
    ExperimentResultRecord,
    FailurePatternRecord,
    FeedbackRecord,
    ForecastRecord,
    OutcomeRecord,
    PromotionDecisionRecord,
    RunRecord,
    SnapshotRecord,
    as_utc,
    utcnow,
)


def dataset_manifest_hash(
    split: str,
    fixture_refs: Sequence[str],
    cutoff_rule: str,
    *,
    fixture_hashes: dict[str, str] | None = None,
    label_rule: str = "outcomes.available_at > received_at",
    leakage_audit: str = "passed",
    authorization: str = "owner",
    visibility: str = "owner_only",
    source_mode: str = "fixture",
    window_start_at: datetime | None = None,
    window_end_at: datetime | None = None,
    event_family_counts: dict[str, int] | None = None,
) -> str:
    payload = {
        "split": split,
        "fixture_refs": fixture_refs,
        "fixture_hashes": fixture_hashes or {},
        "cutoff_rule": cutoff_rule,
        "label_rule": label_rule,
        "leakage_audit": leakage_audit,
        "authorization": authorization,
        "visibility": visibility,
        "source_mode": source_mode,
        "window_start_at": window_start_at.isoformat() if window_start_at else None,
        "window_end_at": window_end_at.isoformat() if window_end_at else None,
        "event_family_counts": event_family_counts or {},
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def dataset_manifest_event_families(row: EvaluationDatasetRecord) -> dict[str, int]:
    """Read immutable family counts from the canonical manifest projection."""
    payload = json.loads(row.manifest_json)
    families = payload.get("event_family_counts", {})
    return families if isinstance(families, dict) else {}


def dataset_manifest_value(row: EvaluationDatasetRecord, key: str) -> object | None:
    return json.loads(row.manifest_json).get(key)


class EvolutionAssetService:
    def __init__(self, database: Database, *, clock: Callable[[], datetime] = utcnow) -> None:
        self.database = database
        self.clock = clock

    def register_dataset(self, manifest: EvaluationDatasetManifest) -> EvaluationDatasetManifest:
        expected = dataset_manifest_hash(
            manifest.split,
            manifest.fixture_refs,
            manifest.cutoff_rule,
            fixture_hashes=manifest.fixture_hashes,
            label_rule=manifest.label_rule,
            leakage_audit=manifest.leakage_audit,
            authorization=manifest.authorization,
            visibility=manifest.visibility,
            source_mode=manifest.source_mode,
            window_start_at=manifest.window_start_at,
            window_end_at=manifest.window_end_at,
            event_family_counts=manifest.event_family_counts,
        )
        if manifest.manifest_hash != expected:
            raise ValueError("dataset_manifest_hash_mismatch")
        if manifest.cutoff_rule != "published_at <= observed_at <= received_at":
            raise ValueError("dataset_pit_rule_missing")
        if manifest.label_rule != "outcomes.available_at > received_at":
            raise ValueError("dataset_label_rule_missing")
        if manifest.leakage_audit != "passed":
            raise ValueError("evaluation_leakage")
        if not manifest.fixture_refs:
            raise ValueError("dataset_fixture_required")
        if set(manifest.fixture_hashes) != set(manifest.fixture_refs):
            raise ValueError("dataset_fixture_hashes_mismatch")
        if not manifest.event_family_counts:
            raise ValueError("dataset_event_family_required")
        if manifest.window_end_at <= manifest.window_start_at:
            raise ValueError("dataset_window_invalid")
        if any(count < 1 for count in manifest.event_family_counts.values()):
            raise ValueError("dataset_event_family_count_invalid")
        with self.database.session() as session:
            existing = session.get(EvaluationDatasetRecord, manifest.dataset_id)
            if existing:
                stored = EvaluationDatasetManifest.model_validate_json(existing.manifest_json)
                if stored.manifest_hash != manifest.manifest_hash:
                    raise ValueError("dataset_request_reused")
                return stored
            session.add(
                EvaluationDatasetRecord(
                    dataset_id=manifest.dataset_id,
                    split=manifest.split,
                    manifest_hash=manifest.manifest_hash,
                    manifest_json=manifest.model_dump_json(),
                    created_at=manifest.created_at,
                )
            )
        return manifest

    def register_candidate(self, candidate: CandidateVersion) -> CandidateVersion:
        if candidate.status not in {"candidate", "experimental"}:
            raise PermissionError("candidate_status_not_allowed")
        with self.database.session() as session:
            existing = session.get(CandidateVersionRecord, candidate.candidate_id)
            if existing:
                stored = self._candidate(existing)
                if stored.content_hash != candidate.content_hash:
                    raise ValueError("candidate_request_reused")
                return stored
            session.add(CandidateVersionRecord(**candidate.model_dump()))
        return candidate

    def register_experiment(self, experiment: ExperimentManifest) -> ExperimentManifest:
        if len(set(experiment.candidate_refs)) != len(experiment.candidate_refs):
            raise ValueError("experiment_candidate_duplicate")
        if experiment.baseline_ref in experiment.candidate_refs:
            raise ValueError("experiment_baseline_overlap")
        with self.database.session() as session:
            if session.get(EvaluationDatasetRecord, experiment.dataset_id) is None:
                raise ValueError("experiment_dataset_not_found")
            refs = (experiment.baseline_ref, *experiment.candidate_refs)
            if any(session.get(CandidateVersionRecord, ref) is None for ref in refs):
                raise ValueError("experiment_candidate_not_found")
            existing = session.get(ExperimentRecord, experiment.experiment_id)
            if existing:
                stored = self._experiment(existing)
                if stored.model_dump(exclude={"created_at"}) != experiment.model_dump(
                    exclude={"created_at"}
                ):
                    raise ValueError("experiment_request_reused")
                return stored
            session.add(
                ExperimentRecord(
                    experiment_id=experiment.experiment_id,
                    dataset_id=experiment.dataset_id,
                    baseline_ref=experiment.baseline_ref,
                    candidate_refs_json=json.dumps(experiment.candidate_refs),
                    status=experiment.status,
                    created_at=experiment.created_at,
                    strategy_version=experiment.strategy_version,
                    runtime_id=experiment.runtime_id,
                    runtime_version=experiment.runtime_version,
                    provider_id=experiment.provider_id,
                    model=experiment.model,
                    schema_version=experiment.schema_version,
                    random_seed=experiment.random_seed,
                    randomness_policy=experiment.randomness_policy,
                    deadline_seconds=experiment.deadline_seconds,
                    max_cost_usd=experiment.max_cost_usd,
                )
            )
        return experiment

    def record_result(self, result: ExperimentResultView) -> ExperimentResultView:
        with self.database.session() as session:
            experiment = session.get(ExperimentRecord, result.experiment_id)
            if experiment is None:
                raise ValueError("experiment_not_found")
            dataset = session.get(EvaluationDatasetRecord, experiment.dataset_id)
            if dataset is None:
                raise ValueError("experiment_dataset_not_found")
            allowed = {experiment.baseline_ref, *json.loads(experiment.candidate_refs_json)}
            if result.candidate_id not in allowed:
                raise ValueError("experiment_candidate_mismatch")
            existing = session.get(ExperimentResultRecord, result.result_id)
            if existing:
                stored = self._result(existing)
                if stored.model_dump(exclude={"created_at"}) != result.model_dump(
                    exclude={"created_at"}
                ):
                    raise ValueError("experiment_result_request_reused")
                return stored
            stage = result.stage or dataset.split
            normalized = result.model_copy(update={"stage": stage})
            session.add(
                ExperimentResultRecord(
                    result_id=normalized.result_id,
                    experiment_id=normalized.experiment_id,
                    candidate_id=normalized.candidate_id,
                    sample_count=normalized.sample_count,
                    brier_score=normalized.brier_score,
                    cost_usd=normalized.cost_usd,
                    p95_latency_ms=normalized.p95_latency_ms,
                    safety_violations=normalized.safety_violations,
                    event_family_counts_json=json.dumps(
                        normalized.event_family_counts, sort_keys=True
                    ),
                    created_at=normalized.created_at,
                    stage=normalized.stage,
                    failure_counts_json=json.dumps(normalized.failure_counts, sort_keys=True),
                    evidence_coverage=normalized.evidence_coverage,
                    directional_accuracy=normalized.directional_accuracy,
                    raw_artifact_refs_json=json.dumps(normalized.raw_artifact_refs, sort_keys=True),
                    scorer_version=normalized.scorer_version,
                )
            )
        return normalized

    def promotion_eligible(self, candidate_id: str, result_refs: list[str]) -> bool:
        with self.database.session() as session:
            return self._promotion_eligible(session, candidate_id, result_refs)

    def review_promotion(
        self, domain_pack_ref: str, request: PromotionReviewRequest
    ) -> PromotionReviewView:
        with self.database.session() as session:
            if session.get(CandidateVersionRecord, request.candidate_id) is None:
                raise ValueError("candidate_not_found")
            return self._promotion_review(
                session,
                domain_pack_ref,
                request.candidate_id,
                request.evaluation_refs,
            )

    def _promotion_eligible(
        self, session: Session, candidate_id: str, result_refs: list[str]
    ) -> bool:
        return self._promotion_review(session, "unscoped", candidate_id, result_refs).eligible

    def _promotion_review(
        self,
        session: Session,
        domain_pack_ref: str,
        candidate_id: str,
        result_refs: list[str],
    ) -> PromotionReviewView:
        checks: list[PromotionGateCheckView] = []
        deltas: list[PromotionMetricDeltaView] = []

        def record(rule_id: str, passed: bool, reason_code: str, detail: str) -> None:
            checks.append(
                PromotionGateCheckView(
                    rule_id=rule_id,
                    status="pass" if passed else "fail",
                    reason_code="passed" if passed else reason_code,
                    detail=detail,
                )
            )

        candidate = session.get(CandidateVersionRecord, candidate_id)
        candidate_ready = candidate is not None and candidate.status in {
            "candidate",
            "experimental",
            "eligible",
        }
        record(
            "candidate.status",
            candidate_ready,
            "candidate_status_not_eligible",
            f"candidate status is {candidate.status if candidate is not None else 'missing'}",
        )
        refs_valid = bool(result_refs) and len(set(result_refs)) == len(result_refs)
        results = [session.get(ExperimentResultRecord, ref) for ref in result_refs]
        refs_valid = refs_valid and all(result is not None for result in results)
        typed_results = [result for result in results if result is not None]
        candidate_results = [r for r in typed_results if r.candidate_id == candidate_id]
        refs_valid = (
            refs_valid and bool(candidate_results) and len(candidate_results) == len(typed_results)
        )
        record(
            "evaluation.references",
            refs_valid,
            "evaluation_refs_invalid",
            f"{len(candidate_results)} candidate result(s) selected",
        )
        stages: set[str] = set()
        if refs_valid:
            for result in candidate_results:
                self._review_promotion_result(
                    session,
                    result,
                    stages=stages,
                    checks=checks,
                    deltas=deltas,
                )
        coverage = {"replay", "holdout", "shadow"}.issubset(stages)
        record(
            "evaluation.stage_coverage",
            coverage,
            "promotion_stage_coverage_missing",
            f"covered stages: {', '.join(sorted(stages)) or 'none'}",
        )
        return PromotionReviewView(
            domain_pack_ref=domain_pack_ref,
            candidate_id=candidate_id,
            evaluation_refs=result_refs,
            eligible=all(item.status == "pass" for item in checks),
            checks=checks,
            deltas=deltas,
        )

    def _review_promotion_result(
        self,
        session: Session,
        result: ExperimentResultRecord,
        *,
        stages: set[str],
        checks: list[PromotionGateCheckView],
        deltas: list[PromotionMetricDeltaView],
    ) -> None:
        def record(suffix: str, passed: bool, reason_code: str, detail: str) -> None:
            checks.append(
                PromotionGateCheckView(
                    rule_id=f"{suffix}:{result.result_id}",
                    status="pass" if passed else "fail",
                    reason_code="passed" if passed else reason_code,
                    detail=detail,
                )
            )

        experiment = session.get(ExperimentRecord, result.experiment_id)
        if experiment is None:
            record("experiment.lineage", False, "experiment_not_found", "experiment missing")
            return
        dataset = session.get(EvaluationDatasetRecord, experiment.dataset_id)
        baseline = (
            session.query(ExperimentResultRecord)
            .filter_by(
                experiment_id=experiment.experiment_id,
                candidate_id=experiment.baseline_ref,
            )
            .one_or_none()
        )
        lineage_valid = (
            dataset is not None
            and baseline is not None
            and result.candidate_id in json.loads(experiment.candidate_refs_json)
            and dataset.split in {"replay", "holdout", "shadow"}
            and (result.stage is None or result.stage == dataset.split)
        )
        record(
            "experiment.lineage",
            lineage_valid,
            "experiment_lineage_invalid",
            f"experiment={experiment.experiment_id}, dataset={experiment.dataset_id}",
        )
        if not lineage_valid or dataset is None or baseline is None:
            return
        stage = cast(Literal["replay", "holdout", "shadow"], dataset.split)
        stages.add(stage)
        record(
            "experiment.completed",
            experiment.status == "completed",
            "experiment_not_completed",
            f"{stage} experiment status is {experiment.status}",
        )
        record(
            "sample.minimum",
            result.sample_count >= 100,
            "promotion_sample_count_insufficient",
            f"{stage} sample count is {result.sample_count}; minimum is 100",
        )
        record(
            "safety.zero",
            result.safety_violations == 0,
            "promotion_safety_violation",
            f"{stage} safety violations: {result.safety_violations}",
        )
        result_families = json.loads(result.event_family_counts_json)
        family_coverage = all(
            result_families.get(family, 0) >= 30
            for family, count in dataset_manifest_event_families(dataset).items()
            if count >= 30
        )
        record(
            "event_family.minimum",
            family_coverage,
            "promotion_event_family_insufficient",
            f"{stage} event-family counts: {result_families}",
        )
        source_mode = dataset_manifest_value(dataset, "source_mode")
        window_start_raw = dataset_manifest_value(dataset, "window_start_at")
        window_end_raw = dataset_manifest_value(dataset, "window_end_at")
        window_valid = isinstance(window_start_raw, str) and isinstance(window_end_raw, str)
        prospective_valid = True
        completed_window = False
        if window_valid:
            window_start = datetime.fromisoformat(cast(str, window_start_raw))
            window_end = datetime.fromisoformat(cast(str, window_end_raw))
            if stage == "shadow":
                prospective_valid = (
                    source_mode == "prospective" and window_end - window_start >= timedelta(days=30)
                )
            elif stage == "holdout":
                prospective_valid = source_mode in {"fixture", "prospective"}
            now = self.clock()
            if now.tzinfo is not None and window_end.tzinfo is None:
                window_end = window_end.replace(tzinfo=now.tzinfo)
            completed_window = window_end <= now
        record(
            "dataset.mode_window",
            window_valid and prospective_valid and completed_window,
            "promotion_dataset_window_invalid",
            f"{stage} source={source_mode}, window={window_start_raw}..{window_end_raw}",
        )
        brier_valid = (
            result.brier_score is not None
            and baseline.brier_score is not None
            and result.brier_score <= baseline.brier_score + 0.02
        )
        record(
            "metric.brier_noninferior",
            brier_valid,
            "promotion_brier_inferior",
            f"{stage} candidate={result.brier_score}, baseline={baseline.brier_score}",
        )
        latency_valid = result.p95_latency_ms is not None and result.p95_latency_ms <= 180_000
        record(
            "latency.deadline",
            latency_valid,
            "promotion_latency_exceeded",
            f"{stage} p95={result.p95_latency_ms}ms; deadline=180000ms",
        )
        cost_valid = baseline.cost_usd is None or (
            result.cost_usd is not None and result.cost_usd <= baseline.cost_usd * 1.25
        )
        record(
            "cost.ceiling",
            cost_valid,
            "promotion_cost_exceeded",
            f"{stage} candidate={result.cost_usd}, baseline={baseline.cost_usd}",
        )
        brier_delta = (
            result.brier_score - baseline.brier_score
            if result.brier_score is not None and baseline.brier_score is not None
            else None
        )
        cost_ratio = None
        if baseline.cost_usd is not None:
            if baseline.cost_usd > 0 and result.cost_usd is not None:
                cost_ratio = result.cost_usd / baseline.cost_usd
            elif baseline.cost_usd == 0 and result.cost_usd == 0:
                cost_ratio = 1.0
        deltas.append(
            PromotionMetricDeltaView(
                stage=stage,
                result_id=result.result_id,
                baseline_result_id=baseline.result_id,
                sample_count=result.sample_count,
                candidate_brier_score=result.brier_score,
                baseline_brier_score=baseline.brier_score,
                brier_delta=brier_delta,
                candidate_cost_usd=result.cost_usd,
                baseline_cost_usd=baseline.cost_usd,
                cost_ratio=cost_ratio,
                p95_latency_ms=result.p95_latency_ms,
                safety_violations=result.safety_violations,
            )
        )

    def decide(
        self, domain_pack_ref: str, request: PromotionDecisionCreate
    ) -> PromotionDecisionResult:
        try:
            return self._decide_transaction(domain_pack_ref, request)
        except RuntimeError as exc:
            if str(exc) != "active_pointer_generation_conflict":
                raise
            prior = self._existing_decision(domain_pack_ref, request)
            if prior is not None:
                return prior
            raise
        except IntegrityError as exc:
            # Concurrent first promotion or a duplicate request can race a unique insert.
            # Resolve an identical request idempotently; otherwise expose the CAS conflict.
            prior = self._existing_decision(domain_pack_ref, request)
            if prior is not None:
                return prior
            pointer = self.active_pointer(domain_pack_ref)
            if pointer is not None and pointer.generation != request.expected_generation:
                raise RuntimeError("active_pointer_generation_conflict") from exc
            raise

    def _decide_transaction(
        self, domain_pack_ref: str, request: PromotionDecisionCreate
    ) -> PromotionDecisionResult:
        with self.database.session() as session:
            prior = (
                session.query(PromotionDecisionRecord)
                .filter_by(request_id=request.request_id)
                .one_or_none()
            )
            if prior is not None:
                if (
                    prior.domain_pack_ref != domain_pack_ref
                    or prior.candidate_id != request.candidate_id
                    or prior.decision != request.decision
                    or prior.owner != request.owner
                    or prior.reason != request.reason
                    or json.loads(prior.evaluation_refs_json) != request.evaluation_refs
                    or prior.resulting_generation
                    != (
                        request.expected_generation + 1
                        if request.decision in {"promote", "rollback"}
                        else request.expected_generation
                    )
                ):
                    raise ValueError("promotion_request_reused")
                return self._decision_result(prior)
            candidate = session.get(CandidateVersionRecord, request.candidate_id)
            if candidate is None:
                raise ValueError("candidate_not_found")
            pointer = (
                session.query(ActivePointerRecord)
                .filter_by(domain_pack_ref=domain_pack_ref)
                .one_or_none()
            )
            generation = pointer.generation if pointer else 0
            if generation != request.expected_generation:
                raise RuntimeError("active_pointer_generation_conflict")
            previous = pointer.candidate_id if pointer else None
            resulting_candidate_id: str | None = previous
            resulting_generation = generation
            now = self.clock()
            if request.decision == "promote":
                if not request.evaluation_refs or not self._promotion_eligible(
                    session, request.candidate_id, request.evaluation_refs
                ):
                    raise PermissionError("promotion_gate_failed")
                if pointer is not None and pointer.candidate_id == candidate.candidate_id:
                    raise PermissionError("candidate_already_active")
                candidate_update = session.execute(
                    update(CandidateVersionRecord)
                    .where(
                        CandidateVersionRecord.candidate_id == request.candidate_id,
                        CandidateVersionRecord.status.in_(
                            ("candidate", "experimental", "eligible")
                        ),
                    )
                    .values(status="active")
                    .execution_options(synchronize_session=False)
                )
                if cast(Any, candidate_update).rowcount != 1:
                    current_status = session.execute(
                        select(CandidateVersionRecord.status).where(
                            CandidateVersionRecord.candidate_id == request.candidate_id
                        )
                    ).scalar_one()
                    if current_status == "active":
                        raise RuntimeError("active_pointer_generation_conflict")
                    raise PermissionError("promotion_gate_failed")
                self._cas_pointer(
                    session,
                    domain_pack_ref=domain_pack_ref,
                    candidate_id=request.candidate_id,
                    expected_generation=generation,
                    updated_at=now,
                    pointer_exists=pointer is not None,
                )
                resulting_candidate_id = request.candidate_id
                resulting_generation = generation + 1
                if previous and previous != candidate.candidate_id:
                    session.execute(
                        update(CandidateVersionRecord)
                        .where(
                            CandidateVersionRecord.candidate_id == previous,
                            CandidateVersionRecord.status == "active",
                        )
                        .values(status="eligible")
                        .execution_options(synchronize_session=False)
                    )
            elif request.decision == "reject":
                pointer_guard = (
                    ~exists().where(ActivePointerRecord.domain_pack_ref == domain_pack_ref)
                    if generation == 0
                    else exists().where(
                        ActivePointerRecord.domain_pack_ref == domain_pack_ref,
                        ActivePointerRecord.generation == generation,
                        ActivePointerRecord.candidate_id != request.candidate_id,
                    )
                )
                rejected = session.execute(
                    update(CandidateVersionRecord)
                    .where(
                        CandidateVersionRecord.candidate_id == request.candidate_id,
                        CandidateVersionRecord.status.in_(
                            ("candidate", "experimental", "eligible")
                        ),
                        pointer_guard,
                    )
                    .values(status="rejected")
                    .execution_options(synchronize_session=False)
                )
                if cast(Any, rejected).rowcount != 1:
                    current_pointer = (
                        session.query(ActivePointerRecord)
                        .filter_by(domain_pack_ref=domain_pack_ref)
                        .one_or_none()
                    )
                    if current_pointer is not None and (
                        current_pointer.generation != request.expected_generation
                    ):
                        raise RuntimeError("active_pointer_generation_conflict")
                    if current_pointer is not None and (
                        current_pointer.candidate_id == request.candidate_id
                    ):
                        raise PermissionError("active_candidate_cannot_be_rejected")
                    raise PermissionError("candidate_not_rejectable")
            else:
                if pointer is None:
                    raise PermissionError("rollback_pointer_not_found")
                if request.candidate_id == pointer.candidate_id:
                    raise PermissionError("rollback_target_is_active")
                historical = (
                    session.query(PromotionDecisionRecord)
                    .filter(
                        PromotionDecisionRecord.domain_pack_ref == domain_pack_ref,
                        PromotionDecisionRecord.decision.in_(["promote", "rollback"]),
                    )
                    .all()
                )
                known_targets = {row.candidate_id for row in historical} | {
                    row.previous_candidate_id for row in historical if row.previous_candidate_id
                }
                if request.candidate_id not in known_targets:
                    raise PermissionError("rollback_target_not_verified")
                target_update = session.execute(
                    update(CandidateVersionRecord)
                    .where(
                        CandidateVersionRecord.candidate_id == request.candidate_id,
                        CandidateVersionRecord.status != "active",
                    )
                    .values(status="active")
                    .execution_options(synchronize_session=False)
                )
                if cast(Any, target_update).rowcount != 1:
                    raise PermissionError("rollback_target_not_available")
                self._cas_pointer(
                    session,
                    domain_pack_ref=domain_pack_ref,
                    candidate_id=request.candidate_id,
                    expected_generation=generation,
                    updated_at=now,
                    pointer_exists=True,
                )
                resulting_candidate_id = request.candidate_id
                resulting_generation = generation + 1
                session.execute(
                    update(CandidateVersionRecord)
                    .where(
                        CandidateVersionRecord.candidate_id == previous,
                        CandidateVersionRecord.status == "active",
                    )
                    .values(status="eligible")
                    .execution_options(synchronize_session=False)
                )
            decision_id = hashlib.sha256(request.request_id.encode()).hexdigest()[:24]
            session.add(
                PromotionDecisionRecord(
                    decision_id=f"promotion_{decision_id}",
                    request_id=request.request_id,
                    domain_pack_ref=domain_pack_ref,
                    candidate_id=request.candidate_id,
                    owner=request.owner,
                    decision=request.decision,
                    reason=request.reason,
                    evaluation_refs_json=json.dumps(request.evaluation_refs),
                    previous_candidate_id=previous,
                    resulting_candidate_id=resulting_candidate_id,
                    resulting_generation=resulting_generation,
                    created_at=now,
                )
            )
            session.flush()
            audit = (
                session.query(PromotionDecisionRecord)
                .filter_by(request_id=request.request_id)
                .one()
            )
            return self._decision_result(audit)

    @staticmethod
    def _cas_pointer(
        session: Session,
        *,
        domain_pack_ref: str,
        candidate_id: str,
        expected_generation: int,
        updated_at: datetime,
        pointer_exists: bool,
    ) -> None:
        if not pointer_exists:
            session.execute(
                insert(ActivePointerRecord).values(
                    pointer_id=f"pointer:{domain_pack_ref}",
                    domain_pack_ref=domain_pack_ref,
                    candidate_id=candidate_id,
                    generation=expected_generation + 1,
                    updated_at=updated_at,
                )
            )
            return
        updated = session.execute(
            update(ActivePointerRecord)
            .where(
                ActivePointerRecord.domain_pack_ref == domain_pack_ref,
                ActivePointerRecord.generation == expected_generation,
            )
            .values(
                candidate_id=candidate_id,
                generation=expected_generation + 1,
                updated_at=updated_at,
            )
            .execution_options(synchronize_session=False)
        )
        if cast(Any, updated).rowcount != 1:
            raise RuntimeError("active_pointer_generation_conflict")

    def _existing_decision(
        self, domain_pack_ref: str, request: PromotionDecisionCreate
    ) -> PromotionDecisionResult | None:
        with self.database.session() as session:
            prior = (
                session.query(PromotionDecisionRecord)
                .filter_by(request_id=request.request_id)
                .one_or_none()
            )
            if prior is None:
                return None
            expected_resulting_generation = (
                request.expected_generation + 1
                if request.decision in {"promote", "rollback"}
                else request.expected_generation
            )
            if (
                prior.domain_pack_ref != domain_pack_ref
                or prior.candidate_id != request.candidate_id
                or prior.decision != request.decision
                or prior.owner != request.owner
                or prior.reason != request.reason
                or json.loads(prior.evaluation_refs_json) != request.evaluation_refs
                or prior.resulting_generation != expected_resulting_generation
            ):
                raise ValueError("promotion_request_reused")
            return self._decision_result(prior)

    def refresh_failure_patterns(self) -> list[FailurePatternView]:
        with self.database.session() as session:
            sources: dict[str, list[str]] = defaultdict(list)
            impacts: dict[str, str] = {}
            for run in session.query(RunRecord).filter(RunRecord.error_code.is_not(None)):
                assert run.error_code is not None
                sources[run.error_code].append(f"run:{run.run_id}")
                impacts[run.error_code] = "run_failed_or_degraded"
            for feedback in session.query(FeedbackRecord).filter(
                FeedbackRecord.verdict.in_(("incorrect", "not_useful", "needs_review"))
            ):
                code = f"feedback_{feedback.verdict}"
                sources[code].append(f"feedback:{feedback.feedback_id}")
                impacts[code] = f"owner_marked_{feedback.verdict}"
            for error_code, source_refs in sources.items():
                record = (
                    session.query(FailurePatternRecord)
                    .filter_by(failure_code=error_code)
                    .one_or_none()
                )
                if record is None:
                    record = FailurePatternRecord(
                        pattern_id=f"failure:{error_code}",
                        failure_code=error_code,
                        occurrence_count=0,
                        impact=impacts[error_code],
                        source_refs_json="[]",
                        remediation_refs_json="[]",
                        status="open",
                        updated_at=self.clock(),
                    )
                    session.add(record)
                count = len(source_refs)
                if record.status in {"closed", "mitigated"} and count > record.occurrence_count:
                    record.status = "regressed"
                record.occurrence_count = count
                record.impact = impacts[error_code]
                record.source_refs_json = json.dumps(sorted(source_refs))
                record.updated_at = self.clock()
            session.flush()
            return [self._failure(row) for row in session.query(FailurePatternRecord).all()]

    def create_experience(self, request: ExperienceCreate) -> ExperienceView:
        """Persist a structured lesson without promoting it to a strategy."""
        payload = request.model_dump(mode="json")
        content_hash = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        experience_id = f"experience_{hashlib.sha256(request.request_id.encode()).hexdigest()[:24]}"
        with self.database.session() as session:
            existing = (
                session.query(ExperienceRecord)
                .filter_by(request_id=request.request_id)
                .one_or_none()
            )
            if existing is not None:
                if existing.content_hash != content_hash:
                    raise ValueError("experience_request_reused")
                return self._experience(existing)
            available_at = self._validate_experience_refs(session, request)
            now = self.clock()
            if available_at > now:
                raise ValueError("experience_reference_not_available")
            session.add(
                ExperienceRecord(
                    experience_id=experience_id,
                    request_id=request.request_id,
                    domain_pack_ref=request.domain_pack_ref,
                    event_family=request.event_family,
                    lesson=request.lesson,
                    applicable_conditions_json=json.dumps(
                        request.applicable_conditions, ensure_ascii=False
                    ),
                    evidence_refs_json=json.dumps(request.evidence_refs, ensure_ascii=False),
                    outcome_refs_json=json.dumps(request.outcome_refs, ensure_ascii=False),
                    evaluation_refs_json=json.dumps(request.evaluation_refs, ensure_ascii=False),
                    source_type=request.source_type,
                    created_by=request.created_by,
                    content_hash=content_hash,
                    status="candidate",
                    available_at=available_at,
                    created_at=now,
                )
            )
            session.flush()
            created = session.get(ExperienceRecord, experience_id)
            assert created is not None
            return self._experience(created)

    @staticmethod
    def _validate_experience_refs(session: Session, request: ExperienceCreate) -> datetime:
        outcomes = [session.get(OutcomeRecord, ref) for ref in request.outcome_refs]
        if any(row is None for row in outcomes):
            raise ValueError("experience_outcome_not_found")
        typed_outcomes = [row for row in outcomes if row is not None]
        outcome_forecasts = {row.forecast_id for row in typed_outcomes}

        available_values: list[datetime] = []
        for row in typed_outcomes:
            available = as_utc(row.observed_at)
            assert available is not None
            available_values.append(available)

        for ref in request.evaluation_refs:
            evaluation = session.get(EvaluationRecord, ref)
            if evaluation is not None:
                if evaluation.forecast_id not in outcome_forecasts:
                    raise ValueError("experience_evaluation_outcome_mismatch")
                available = as_utc(evaluation.evaluated_at)
            else:
                result = session.get(ExperimentResultRecord, ref)
                if result is None:
                    raise ValueError("experience_evaluation_not_found")
                available = as_utc(result.created_at)
            assert available is not None
            available_values.append(available)

        event_ids: set[str] = set()
        for forecast_id in outcome_forecasts:
            forecast = session.get(ForecastRecord, forecast_id)
            if forecast is None:
                raise ValueError("experience_forecast_not_found")
            artifact = session.get(ArtifactRecord, forecast.artifact_id)
            if artifact is None:
                raise ValueError("experience_artifact_not_found")
            event_ids.add(artifact.event_id)

        found_evidence: set[str] = set()
        snapshots = session.query(SnapshotRecord).filter(SnapshotRecord.event_id.in_(event_ids))
        for snapshot in snapshots:
            for item in json.loads(snapshot.evidence_json):
                if isinstance(item, dict) and isinstance(item.get("evidence_id"), str):
                    found_evidence.add(item["evidence_id"])
        if not set(request.evidence_refs).issubset(found_evidence):
            raise ValueError("experience_evidence_not_found")
        if not available_values:
            raise ValueError("experience_reference_required")
        return max(available_values)

    def list_experiences(self, limit: int = 100) -> list[ExperienceView]:
        with self.database.session() as session:
            rows = (
                session.query(ExperienceRecord)
                .order_by(ExperienceRecord.created_at.desc())
                .limit(max(1, min(limit, 500)))
                .all()
            )
            return [self._experience(row) for row in rows]

    def set_experience_status(self, experience_id: str, status: str) -> ExperienceView:
        if status not in {"verified", "retired"}:
            raise ValueError("experience_status_invalid")
        with self.database.session() as session:
            record = session.get(ExperienceRecord, experience_id)
            if record is None:
                raise ValueError("experience_not_found")
            record.status = status
            session.flush()
            return self._experience(record)

    def list_failures(self) -> list[FailurePatternView]:
        with self.database.session() as session:
            return [
                self._failure(row)
                for row in session.query(FailurePatternRecord)
                .order_by(FailurePatternRecord.updated_at.desc())
                .all()
            ]

    def list_decisions(self, limit: int = 100) -> list[PromotionDecisionView]:
        with self.database.session() as session:
            rows = (
                session.query(PromotionDecisionRecord)
                .order_by(PromotionDecisionRecord.created_at.desc())
                .limit(max(1, min(limit, 500)))
                .all()
            )
            return [self._decision_view(row) for row in rows]

    def set_experiment_status(self, experiment_id: str, status: str) -> ExperimentManifest:
        if status not in {"registered", "running", "completed", "failed"}:
            raise ValueError("experiment_status_invalid")
        with self.database.session() as session:
            row = session.get(ExperimentRecord, experiment_id)
            if row is None:
                raise ValueError("experiment_not_found")
            row.status = status
            session.flush()
            return self._experiment(row)

    def active_pointer(self, domain_pack_ref: str) -> ActivePointerView | None:
        with self.database.session() as session:
            row = (
                session.query(ActivePointerRecord)
                .filter_by(domain_pack_ref=domain_pack_ref)
                .one_or_none()
            )
            return self._pointer(row) if row is not None else None

    def overview(self) -> EvolutionOverviewView:
        with self.database.session() as session:
            return EvolutionOverviewView(
                datasets=[
                    EvaluationDatasetManifest.model_validate_json(row.manifest_json)
                    for row in session.query(EvaluationDatasetRecord).all()
                ],
                candidates=[
                    self._candidate(row) for row in session.query(CandidateVersionRecord).all()
                ],
                experiments=[
                    self._experiment(row) for row in session.query(ExperimentRecord).all()
                ],
                results=[self._result(row) for row in session.query(ExperimentResultRecord).all()],
                pointers=[self._pointer(row) for row in session.query(ActivePointerRecord).all()],
                failures=[self._failure(row) for row in session.query(FailurePatternRecord).all()],
                experiences=[
                    self._experience(row) for row in session.query(ExperienceRecord).all()
                ],
                decisions=[
                    self._decision_view(row)
                    for row in session.query(PromotionDecisionRecord)
                    .order_by(PromotionDecisionRecord.created_at.desc())
                    .all()
                ],
            )

    @staticmethod
    def _candidate(row: CandidateVersionRecord) -> CandidateVersion:
        created_at = as_utc(row.created_at)
        assert created_at is not None
        return CandidateVersion.model_validate(
            {
                "candidate_id": row.candidate_id,
                "candidate_type": row.candidate_type,
                "content_hash": row.content_hash,
                "version": row.version,
                "parent_version": row.parent_version,
                "status": row.status,
                "created_at": created_at,
                "content_ref": row.content_ref,
                "source": row.source,
            }
        )

    @staticmethod
    def _experiment(row: ExperimentRecord) -> ExperimentManifest:
        created_at = as_utc(row.created_at)
        assert created_at is not None
        return ExperimentManifest.model_validate(
            {
                "experiment_id": row.experiment_id,
                "dataset_id": row.dataset_id,
                "baseline_ref": row.baseline_ref,
                "candidate_refs": json.loads(row.candidate_refs_json),
                "status": row.status,
                "created_at": created_at,
                "strategy_version": row.strategy_version,
                "runtime_id": row.runtime_id,
                "runtime_version": row.runtime_version,
                "provider_id": row.provider_id,
                "model": row.model,
                "schema_version": row.schema_version,
                "random_seed": row.random_seed,
                "randomness_policy": row.randomness_policy,
                "deadline_seconds": row.deadline_seconds,
                "max_cost_usd": row.max_cost_usd,
            }
        )

    @staticmethod
    def _result(row: ExperimentResultRecord) -> ExperimentResultView:
        created_at = as_utc(row.created_at)
        assert created_at is not None
        return ExperimentResultView.model_validate(
            {
                "result_id": row.result_id,
                "experiment_id": row.experiment_id,
                "candidate_id": row.candidate_id,
                "sample_count": row.sample_count,
                "brier_score": row.brier_score,
                "cost_usd": row.cost_usd,
                "p95_latency_ms": row.p95_latency_ms,
                "safety_violations": row.safety_violations,
                "event_family_counts": json.loads(row.event_family_counts_json),
                "created_at": created_at,
                "stage": row.stage,
                "failure_counts": json.loads(row.failure_counts_json),
                "evidence_coverage": row.evidence_coverage,
                "directional_accuracy": row.directional_accuracy,
                "raw_artifact_refs": json.loads(row.raw_artifact_refs_json),
                "scorer_version": row.scorer_version,
            }
        )

    def _decision_result(self, row: PromotionDecisionRecord) -> PromotionDecisionResult:
        created_at = as_utc(row.created_at)
        assert created_at is not None
        decision = self._decision_view(row)
        pointer = None
        if row.resulting_candidate_id is not None:
            pointer = ActivePointerView(
                pointer_id=f"pointer:{row.domain_pack_ref or 'legacy'}",
                domain_pack_ref=row.domain_pack_ref or "legacy",
                candidate_id=row.resulting_candidate_id,
                generation=row.resulting_generation,
                updated_at=created_at,
            )
        return PromotionDecisionResult(decision=decision, active_pointer=pointer)

    @staticmethod
    def _decision_view(row: PromotionDecisionRecord) -> PromotionDecisionView:
        created_at = as_utc(row.created_at)
        assert created_at is not None
        return PromotionDecisionView.model_validate(
            {
                "decision_id": row.decision_id,
                "request_id": row.request_id or f"legacy:{row.decision_id}",
                "domain_pack_ref": row.domain_pack_ref or "legacy",
                "candidate_id": row.candidate_id,
                "owner": row.owner,
                "decision": row.decision,
                "reason": row.reason,
                "evaluation_refs": json.loads(row.evaluation_refs_json),
                "previous_candidate_id": row.previous_candidate_id,
                "resulting_candidate_id": row.resulting_candidate_id,
                "resulting_generation": row.resulting_generation,
                "created_at": created_at,
            }
        )

    @staticmethod
    def _pointer(row: ActivePointerRecord) -> ActivePointerView:
        updated_at = as_utc(row.updated_at)
        assert updated_at is not None
        return ActivePointerView(
            pointer_id=row.pointer_id,
            domain_pack_ref=row.domain_pack_ref,
            candidate_id=row.candidate_id,
            generation=row.generation,
            updated_at=updated_at,
        )

    @staticmethod
    def _failure(row: FailurePatternRecord) -> FailurePatternView:
        updated_at = as_utc(row.updated_at)
        assert updated_at is not None
        return FailurePatternView.model_validate(
            {
                "pattern_id": row.pattern_id,
                "failure_code": row.failure_code,
                "occurrence_count": row.occurrence_count,
                "impact": row.impact,
                "source_refs": json.loads(row.source_refs_json),
                "root_cause_hypothesis": row.root_cause_hypothesis,
                "remediation_refs": json.loads(row.remediation_refs_json),
                "status": row.status,
                "updated_at": updated_at,
            }
        )

    @staticmethod
    def _experience(row: ExperienceRecord) -> ExperienceView:
        created_at = as_utc(row.created_at)
        available_at = as_utc(row.available_at)
        assert created_at is not None and available_at is not None
        return ExperienceView.model_validate(
            {
                "request_id": row.request_id,
                "domain_pack_ref": row.domain_pack_ref,
                "event_family": row.event_family,
                "lesson": row.lesson,
                "applicable_conditions": json.loads(row.applicable_conditions_json),
                "evidence_refs": json.loads(row.evidence_refs_json),
                "outcome_refs": json.loads(row.outcome_refs_json),
                "evaluation_refs": json.loads(row.evaluation_refs_json),
                "source_type": row.source_type,
                "created_by": row.created_by,
                "experience_id": row.experience_id,
                "content_hash": row.content_hash,
                "status": row.status,
                "available_at": available_at,
                "created_at": created_at,
            }
        )

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from packages.contracts_py.decision_hub_contracts.models import (
    CandidateProposal,
    CandidateVersion,
    EvaluationDatasetManifest,
    EvolutionJobCreate,
    ExperimentManifest,
)
from packages.evals.runner import EvaluationRun, EvaluationRunner
from packages.kernel.decision_hub_kernel.application.evolution import EvolutionAssetService
from packages.kernel.decision_hub_kernel.application.live_observation import (
    EvolutionContextService,
    EvolutionExperienceService,
    EvolutionJobPolicy,
    EvolutionJobService,
    EvolutionTriggerScanner,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    ActivePointerRecord,
    CandidateVersionRecord,
    Database,
    EvaluationRecord,
    FeedbackRecord,
)
from packages.kernel.decision_hub_kernel.ports.runtime import (
    AgentExecutionError,
    AgentRequest,
    AgentResult,
    AgentRuntime,
    AgentUsage,
)
from packages.orchestration.langgraph.evolution_executor import (
    CandidateArtifactStore,
    EvolutionJobExecutor,
    FixtureEvaluationPlanFactory,
    SupervisorEvolutionPlanner,
)
from packages.runtime_adapters.fake_runtime.runtime import FakeAgentRuntime
from tools.replay.run_fixture import run_fixture

NOW = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)


class ProposalRuntime(FakeAgentRuntime):
    async def execute(self, request: AgentRequest) -> AgentResult:
        result = await super().execute(request)
        if request.role != "decision_synthesis":
            return result
        return AgentResult(
            role=request.role,
            payload={
                "proposal_id": "proposal-1",
                "candidate_type": "strategy",
                "version": "candidate.v1",
                "parent_version": "baseline.v1",
                "summary": "Require explicit cross-asset confirmation before directional action.",
                "changes": ["Add a DXY and real-yield confirmation condition."],
                "rationale": "The owner feedback identified a missing transmission check.",
                "evidence_refs": list(request.evidence),
            },
            runtime_id="proposal-fixture",
            runtime_version="proposal-fixture.v1",
            latency_ms=0,
            usage=AgentUsage(cost_status="unknown"),
        )


class FailOnceEvaluationRunner(EvaluationRunner):
    def __init__(self, work_dir: Path) -> None:
        super().__init__(work_dir)
        self.failed = False

    async def run(
        self,
        experiment: ExperimentManifest,
        dataset: EvaluationDatasetManifest,
        fixture_paths: Sequence[Path],
        runtimes: Mapping[str, AgentRuntime],
        *,
        strategy_versions: Mapping[str, str] | None = None,
    ) -> EvaluationRun:
        completed = await super().run(
            experiment,
            dataset,
            fixture_paths,
            runtimes,
            strategy_versions=strategy_versions,
        )
        if not self.failed:
            self.failed = True
            raise AgentExecutionError(
                "provider_unavailable",
                "simulated worker interruption after raw reports",
                retryable=True,
            )
        return completed


def _seed(database: Database) -> None:
    baseline = CandidateVersion(
        candidate_id="baseline",
        candidate_type="strategy",
        content_hash=hashlib.sha256(b"baseline").hexdigest(),
        version="baseline.v1",
        parent_version=None,
        status="active",
        created_at=NOW,
        content_ref=None,
        source="owner",
    )
    EvolutionAssetService(database, clock=lambda: NOW).register_candidate(
        baseline.model_copy(update={"status": "candidate"})
    )
    with database.session() as session:
        stored = session.get(CandidateVersionRecord, "baseline")
        assert stored is not None
        stored.status = "active"
        session.add(
            ActivePointerRecord(
                pointer_id="pointer:crypto_macro.v1",
                domain_pack_ref="crypto_macro.v1",
                candidate_id="baseline",
                generation=1,
                updated_at=NOW,
            )
        )
        session.add(
            FeedbackRecord(
                feedback_id="feedback-1",
                request_id="feedback-request-1",
                target_type="run",
                target_id="run-1",
                created_by="owner",
                verdict="incorrect",
                notes="The causal chain missed cross-asset transmission.",
                created_at=NOW,
            )
        )


def test_executor_reuses_supervisor_and_evaluation_without_promoting(
    tmp_path: Path,
) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'evolution.sqlite3'}")
    database.create_all()
    _seed(database)
    jobs = EvolutionJobService(database, clock=lambda: NOW)
    assets = EvolutionAssetService(database, clock=lambda: NOW)
    scanner = EvolutionTriggerScanner(
        database,
        jobs,
        assets,
        policy=EvolutionJobPolicy(
            failure_occurrence_threshold=99,
            evaluation_batch_size=99,
            scheduled_scan_enabled=False,
        ),
        clock=lambda: NOW,
    )
    runtime = FakeAgentRuntime()
    root = Path(__file__).parents[2]
    plans = FixtureEvaluationPlanFactory(
        {
            "replay": [
                root / "fixtures/evolution/replay/powell-higher-for-longer.json"
            ],
            "holdout": [
                root / "fixtures/evolution/holdout/powell-higher-for-longer.json"
            ],
            "shadow": [
                root / "fixtures/evolution/shadow/powell-higher-for-longer.json"
            ],
        },
        baseline_runtime=runtime,
        candidate_runtime=runtime,
    )
    executor = EvolutionJobExecutor(
        jobs=jobs,
        scanner=scanner,
        context=EvolutionContextService(database),
        planner=SupervisorEvolutionPlanner(
            ProposalRuntime(),
            available_capabilities={"counter_thesis", "data_quality"},
            required_capabilities=("counter_thesis", "data_quality"),
            clock=lambda: NOW,
        ),
        artifacts=CandidateArtifactStore(tmp_path / "artifacts"),
        assets=assets,
        evaluation_plans=plans,
        evaluation_runner=EvaluationRunner(tmp_path / "evaluations"),
        clock=lambda: NOW,
    )

    completed = asyncio.run(executor.tick("evolution-worker", lease_seconds=30))

    assert completed is not None
    assert completed.status == "pending_owner_review"
    assert completed.stage == "review"
    assert len(completed.experiment_refs) == 3
    assert len(completed.result_refs) == 3
    assert completed.candidate_id is not None
    assert (tmp_path / "artifacts/candidates" / f"{completed.candidate_id}.json").is_file()
    assert assets.active_pointer("crypto_macro.v1").candidate_id == "baseline"  # type: ignore[union-attr]
    overview = assets.overview()
    assert len(overview.experiments) == 3
    assert len(overview.results) == 6
    baseline_report = next(
        Path(ref)
        for result in overview.results
        if result.candidate_id == "baseline"
        for ref in result.raw_artifact_refs
    )
    candidate_report = next(
        Path(ref)
        for result in overview.results
        if result.candidate_id == completed.candidate_id
        for ref in result.raw_artifact_refs
    )
    baseline_payload = json.loads(baseline_report.read_text())
    candidate_payload = json.loads(candidate_report.read_text())
    assert baseline_payload["strategy_version"] == "baseline.v1"
    assert candidate_payload["strategy_version"] == "candidate.v1"
    assert baseline_payload["artifact_fingerprint"] != candidate_payload["artifact_fingerprint"]
    assert asyncio.run(executor.tick("evolution-worker", lease_seconds=30)) is None
    assert len(jobs.list_jobs()) == 1


def test_executor_restart_reuses_candidate_experiment_and_raw_reports(
    tmp_path: Path,
) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'recovery.sqlite3'}")
    database.create_all()
    _seed(database)
    current = NOW
    jobs = EvolutionJobService(database, clock=lambda: current)
    assets = EvolutionAssetService(database, clock=lambda: current)
    scanner = EvolutionTriggerScanner(
        database,
        jobs,
        assets,
        policy=EvolutionJobPolicy(
            failure_occurrence_threshold=99,
            evaluation_batch_size=99,
            scheduled_scan_enabled=False,
        ),
        clock=lambda: current,
    )
    runtime = FakeAgentRuntime()
    root = Path(__file__).parents[2]
    runner = FailOnceEvaluationRunner(tmp_path / "recovery-evaluations")
    executor = EvolutionJobExecutor(
        jobs=jobs,
        scanner=scanner,
        context=EvolutionContextService(database),
        planner=SupervisorEvolutionPlanner(
            ProposalRuntime(),
            available_capabilities={"counter_thesis", "data_quality"},
            required_capabilities=("counter_thesis", "data_quality"),
            clock=lambda: current,
        ),
        artifacts=CandidateArtifactStore(tmp_path / "recovery-artifacts"),
        assets=assets,
        evaluation_plans=FixtureEvaluationPlanFactory(
            {
                "replay": [
                    root / "fixtures/evolution/replay/powell-higher-for-longer.json"
                ],
                "holdout": [
                    root / "fixtures/evolution/holdout/powell-higher-for-longer.json"
                ],
                "shadow": [
                    root / "fixtures/evolution/shadow/powell-higher-for-longer.json"
                ],
            },
            baseline_runtime=runtime,
            candidate_runtime=runtime,
        ),
        evaluation_runner=runner,
        clock=lambda: current,
    )

    interrupted = asyncio.run(
        executor.tick("evolution-worker-a", lease_seconds=30, retry_backoff_seconds=60)
    )
    assert interrupted is not None and interrupted.status == "retry_wait"
    candidate_id = interrupted.candidate_id
    current = NOW + timedelta(seconds=61)

    recovered = asyncio.run(executor.tick("evolution-worker-b", lease_seconds=30))

    assert recovered is not None and recovered.status == "pending_owner_review"
    assert recovered.candidate_id == candidate_id
    overview = assets.overview()
    assert len(overview.candidates) == 2
    assert len(overview.experiments) == 3
    assert len(overview.results) == 6
    candidate_files = list((tmp_path / "recovery-artifacts/candidates").glob("*.json"))
    assert len(candidate_files) == 1


def test_plan_factory_reads_promoted_candidate_as_next_baseline_without_restart(
    tmp_path: Path,
) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'dynamic-baseline.sqlite3'}")
    database.create_all()
    _seed(database)
    assets = EvolutionAssetService(database, clock=lambda: NOW)
    jobs = EvolutionJobService(database, clock=lambda: NOW)
    artifacts = CandidateArtifactStore(tmp_path / "dynamic-artifacts")
    first_job = jobs.enqueue(
        EvolutionJobCreate(
            trigger_key="feedback:first",
            trigger_type="feedback",
            domain_pack_ref="crypto_macro.v1",
            input_refs=["feedback:feedback-1"],
            max_attempts=3,
        )
    )
    first_proposal = CandidateProposal(
        proposal_id="first-proposal",
        candidate_type="strategy",
        version="promoted.v1",
        parent_version="baseline.v1",
        summary="Require real-yield confirmation.",
        changes=["Add real-yield confirmation."],
        rationale="Owner-reviewed evaluation supported the change.",
        evidence_refs=["feedback:feedback-1"],
    )
    candidate_id, content_hash, content_ref = artifacts.persist(first_job, first_proposal)
    promoted = assets.register_candidate(
        CandidateVersion(
            candidate_id=candidate_id,
            candidate_type="strategy",
            content_hash=content_hash,
            version=first_proposal.version,
            parent_version=first_proposal.parent_version,
            status="candidate",
            created_at=NOW,
            content_ref=content_ref,
            source="evolution_supervisor",
        )
    )
    with database.session() as session:
        old = session.get(CandidateVersionRecord, "baseline")
        current = session.get(CandidateVersionRecord, promoted.candidate_id)
        pointer = session.get(ActivePointerRecord, "pointer:crypto_macro.v1")
        assert old is not None and current is not None and pointer is not None
        old.status = "retired"
        current.status = "active"
        pointer.candidate_id = current.candidate_id
        pointer.generation = 2

    root = Path(__file__).parents[2]
    factory = FixtureEvaluationPlanFactory(
        {
            "replay": [root / "fixtures/evolution/replay/powell-higher-for-longer.json"],
            "holdout": [root / "fixtures/evolution/holdout/powell-higher-for-longer.json"],
            "shadow": [root / "fixtures/evolution/shadow/powell-higher-for-longer.json"],
        },
        baseline_runtime=FakeAgentRuntime(),
        candidate_runtime=FakeAgentRuntime(),
        assets=assets,
        artifacts=artifacts,
    )
    second_job = jobs.enqueue(
        EvolutionJobCreate(
            trigger_key="feedback:second",
            trigger_type="feedback",
            domain_pack_ref="crypto_macro.v1",
            input_refs=["feedback:feedback-1"],
            max_attempts=3,
        )
    )
    next_proposal = first_proposal.model_copy(
        update={
            "proposal_id": "second-proposal",
            "version": "candidate.v2",
            "parent_version": "promoted.v1",
            "changes": ["Add DXY confirmation after real-yield confirmation."],
        }
    )
    next_candidate = CandidateVersion(
        candidate_id="next-candidate",
        candidate_type="strategy",
        content_hash=hashlib.sha256(b"next-candidate").hexdigest(),
        version=next_proposal.version,
        parent_version=next_proposal.parent_version,
        status="candidate",
        created_at=NOW,
        content_ref=None,
        source="test",
    )

    plan = factory.build(
        second_job,
        next_candidate,
        promoted.candidate_id,
        promoted.version,
        next_proposal,
    )[0]

    baseline_runtime = plan.runtimes[promoted.candidate_id]
    assert baseline_runtime.runtime_id == "candidate-config:fake"
    assert plan.strategy_versions[promoted.candidate_id] == "promoted.v1"
    result = asyncio.run(
        baseline_runtime.execute(
            AgentRequest(
                role="decision_synthesis",
                text="Rates may stay higher for longer.",
                evidence=("evidence:1",),
                deadline_at=NOW + timedelta(minutes=1),
            )
        )
    )
    assert "Add real-yield confirmation." in str(result.payload["summary"])


def test_experience_service_only_uses_traceable_evaluation_lineage(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "experience.sqlite3"
    root = Path(__file__).parents[2]
    asyncio.run(
        run_fixture(root / "fixtures/replay/powell-higher-for-longer.json", database_path)
    )
    database = Database(f"sqlite+pysqlite:///{database_path}")
    assets = EvolutionAssetService(database)
    jobs = EvolutionJobService(database)
    with database.session() as session:
        evaluation = session.query(EvaluationRecord).first()
        assert evaluation is not None
        evaluation_id = evaluation.evaluation_id
    proposal = CandidateProposal(
        proposal_id="experience-proposal",
        candidate_type="strategy",
        version="candidate.experience.v1",
        parent_version="baseline.v1",
        summary="Tie conclusions to the tradable observation window.",
        changes=["Require canonical Outcome and Evaluation lineage."],
        rationale="The prospective label is now available.",
        evidence_refs=[f"evaluation:{evaluation_id}"],
    )
    job = jobs.enqueue(
        EvolutionJobCreate(
            trigger_key="evaluation:experience",
            trigger_type="evaluation_batch",
            domain_pack_ref="crypto_macro.v1",
            input_refs=[f"evaluation:{evaluation_id}"],
            max_attempts=3,
        )
    )
    service = EvolutionExperienceService(database, assets)

    first = service.create_from_job(job, proposal)
    second = service.create_from_job(job, proposal)

    assert first is not None and second is not None
    assert first.experience_id == second.experience_id
    assert len(assets.list_experiences()) == 1
    feedback_only = jobs.enqueue(
        EvolutionJobCreate(
            trigger_key="feedback:no-experience",
            trigger_type="feedback",
            domain_pack_ref="crypto_macro.v1",
            input_refs=["feedback:feedback-1"],
            max_attempts=3,
        )
    )
    assert service.create_from_job(feedback_only, proposal) is None
    missing = jobs.enqueue(
        EvolutionJobCreate(
            trigger_key="evaluation:missing",
            trigger_type="evaluation_batch",
            domain_pack_ref="crypto_macro.v1",
            input_refs=["evaluation:missing"],
            max_attempts=3,
        )
    )
    assert service.create_from_job(missing, proposal) is None
    assert len(assets.list_experiences()) == 1

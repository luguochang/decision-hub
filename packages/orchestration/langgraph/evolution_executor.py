from __future__ import annotations

import asyncio
import hashlib
import json
import os
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, Protocol, TypeVar, cast

from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import Checkpointer
from pydantic import ValidationError

from packages.contracts_py.decision_hub_contracts.models import (
    CandidateProposal,
    CandidateVersion,
    EvaluationDatasetManifest,
    EvolutionJobView,
    EvolutionPlanningContext,
    ExperimentManifest,
    PromotionReviewRequest,
)
from packages.evals.runner import EvaluationRunner
from packages.kernel.decision_hub_kernel.application.evolution import (
    EvolutionAssetService,
    dataset_manifest_hash,
)
from packages.kernel.decision_hub_kernel.application.live_observation import (
    EvolutionContextService,
    EvolutionExperienceService,
    EvolutionJobService,
    EvolutionTriggerScanner,
)
from packages.kernel.decision_hub_kernel.ports.runtime import (
    AgentExecutionError,
    AgentRuntime,
)
from packages.orchestration.langgraph.graphs.supervisor_graph import (
    SupervisorCandidateState,
    build_supervisor_candidate_graph,
)
from packages.runtime_adapters.candidate_runtime import CandidateConfigurationRuntime
from tools.replay.run_fixture import ReplayFixture

Stage = Literal["replay", "holdout", "shadow"]
T = TypeVar("T")


class EvolutionCandidatePlanner(Protocol):
    async def propose(self, context: EvolutionPlanningContext) -> CandidateProposal | None: ...


@dataclass(frozen=True)
class EvolutionStagePlan:
    dataset: EvaluationDatasetManifest
    experiment: ExperimentManifest
    fixture_paths: tuple[Path, ...]
    runtimes: Mapping[str, AgentRuntime]
    strategy_versions: Mapping[str, str]


class EvolutionEvaluationPlanFactory(Protocol):
    def build(
        self,
        job: EvolutionJobView,
        candidate: CandidateVersion,
        baseline_id: str,
        baseline_version: str,
        proposal: CandidateProposal,
    ) -> Sequence[EvolutionStagePlan]: ...


class SupervisorEvolutionPlanner:
    """Use the existing bounded Supervisor graph as a candidate-only planner."""

    def __init__(
        self,
        runtime: AgentRuntime,
        *,
        available_capabilities: set[str],
        required_capabilities: tuple[str, ...],
        deadline_seconds: int = 180,
        checkpointer: Checkpointer = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.graph = build_supervisor_candidate_graph(
            runtime,
            available_capabilities=available_capabilities,
            required_capabilities=required_capabilities,
            checkpointer=checkpointer,
        )
        self.deadline_seconds = deadline_seconds
        self.clock = clock
        self.checkpointed = checkpointer is not None

    async def propose(self, context: EvolutionPlanningContext) -> CandidateProposal | None:
        baseline = (
            f"Active baseline id={context.active_candidate_id}; "
            f"version={context.active_candidate_version}; "
            f"content_hash={context.active_candidate_content_hash}"
        )
        state: SupervisorCandidateState = {
            "run_id": context.job_id,
            "text": "\n".join((baseline, *context.context_items)),
            "evidence": tuple(context.evidence_refs),
            "deadline_at": (self.clock() + timedelta(seconds=self.deadline_seconds)).isoformat(),
            "domain_pack_ref": context.domain_pack_ref,
            "replan_count": 0,
            "specialist_results": [],
        }
        config: RunnableConfig | None = None
        if self.checkpointed:
            config = {"configurable": {"thread_id": f"evolution:{context.job_id}:plan"}}
        result = await self.graph.ainvoke(state, config=config)
        raw = result.get("candidate")
        if raw is None or (isinstance(raw, dict) and raw.get("no_candidate") is True):
            return None
        try:
            proposal = CandidateProposal.model_validate(raw)
        except ValidationError as exc:
            raise AgentExecutionError(
                "structured_output_invalid",
                "evolution supervisor returned an invalid candidate proposal",
            ) from exc
        if not set(proposal.evidence_refs).issubset(context.evidence_refs):
            raise AgentExecutionError(
                "candidate_evidence_out_of_scope",
                "candidate proposal cited evidence outside the frozen job context",
            )
        if proposal.parent_version is None and context.active_candidate_version is not None:
            proposal = proposal.model_copy(
                update={"parent_version": context.active_candidate_version}
            )
        return proposal


class CandidateArtifactStore:
    """Write immutable candidate payloads before registering their ledger projection."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def persist(
        self, job: EvolutionJobView, proposal: CandidateProposal
    ) -> tuple[str, str, str]:
        payload = {
            "schema_version": "candidate-artifact.v1",
            "job_id": job.job_id,
            "domain_pack_ref": job.domain_pack_ref,
            "proposal": proposal.model_dump(mode="json"),
        }
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        content_hash = hashlib.sha256(body.encode()).hexdigest()
        identity = hashlib.sha256(f"{job.job_id}:{content_hash}".encode()).hexdigest()
        candidate_id = f"candidate_{identity[:24]}"
        directory = self.root / "candidates"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{candidate_id}.json"
        if path.exists():
            if path.read_text() != body:
                raise ValueError("candidate_artifact_conflict")
        else:
            temporary = directory / f".{candidate_id}.{os.getpid()}.tmp"
            temporary.write_text(body)
            os.replace(temporary, path)
        return candidate_id, content_hash, str(path)

    def load(self, job: EvolutionJobView, candidate_id: str) -> CandidateProposal:
        path = self.root / "candidates" / f"{candidate_id}.json"
        if not path.is_file():
            raise ValueError("candidate_artifact_not_found")
        payload = json.loads(path.read_text())
        if (
            payload.get("schema_version") != "candidate-artifact.v1"
            or payload.get("job_id") != job.job_id
            or payload.get("domain_pack_ref") != job.domain_pack_ref
        ):
            raise ValueError("candidate_artifact_lineage_mismatch")
        return CandidateProposal.model_validate(payload.get("proposal"))

    def load_candidate(self, candidate: CandidateVersion) -> CandidateProposal:
        """Load an already registered candidate for use as a future active baseline."""
        if candidate.content_ref is None:
            raise ValueError("candidate_artifact_not_found")
        expected = (self.root / "candidates" / f"{candidate.candidate_id}.json").resolve()
        path = Path(candidate.content_ref).resolve()
        if path != expected or not path.is_file():
            raise ValueError("candidate_artifact_ref_invalid")
        body = path.read_bytes()
        if hashlib.sha256(body).hexdigest() != candidate.content_hash:
            raise ValueError("candidate_artifact_hash_mismatch")
        payload = json.loads(body)
        if payload.get("schema_version") != "candidate-artifact.v1":
            raise ValueError("candidate_artifact_schema_invalid")
        proposal = CandidateProposal.model_validate(payload.get("proposal"))
        if proposal.version != candidate.version:
            raise ValueError("candidate_artifact_version_mismatch")
        return proposal


class FixtureEvaluationPlanFactory:
    """Build immutable replay/holdout/shadow manifests from explicit fixture sets."""

    def __init__(
        self,
        fixtures: Mapping[Stage, Sequence[Path]],
        *,
        baseline_runtime: AgentRuntime,
        candidate_base_runtime: AgentRuntime | None = None,
        candidate_runtime: AgentRuntime | None = None,
        baseline_version: str = "baseline.v1",
        assets: EvolutionAssetService | None = None,
        artifacts: CandidateArtifactStore | None = None,
        deadline_seconds: int = 300,
        max_cost_usd: float | None = None,
    ) -> None:
        self.fixtures = {stage: tuple(paths) for stage, paths in fixtures.items()}
        self.baseline_runtime = baseline_runtime
        resolved_candidate_runtime = candidate_base_runtime or candidate_runtime
        if resolved_candidate_runtime is None:
            raise ValueError("candidate_runtime_required")
        self.candidate_base_runtime: AgentRuntime = resolved_candidate_runtime
        self.baseline_version = baseline_version
        self.assets = assets
        self.artifacts = artifacts
        self.deadline_seconds = deadline_seconds
        self.max_cost_usd = max_cost_usd

    def build(
        self,
        job: EvolutionJobView,
        candidate: CandidateVersion,
        baseline_id: str,
        baseline_version: str,
        proposal: CandidateProposal,
    ) -> Sequence[EvolutionStagePlan]:
        if set(self.fixtures) != {"replay", "holdout", "shadow"}:
            raise ValueError("evaluation_stage_coverage_missing")
        baseline_runtime = self._baseline_runtime(baseline_id, baseline_version)
        if candidate.parent_version != baseline_version:
            raise ValueError("candidate_parent_version_mismatch")
        candidate_runtime = CandidateConfigurationRuntime(
            self.candidate_base_runtime, proposal
        )
        if (
            candidate_runtime.runtime_id == baseline_runtime.runtime_id
            and candidate_runtime.runtime_version == baseline_runtime.runtime_version
        ):
            raise ValueError("candidate_runtime_not_configured")
        return tuple(
            self._stage(
                job,
                candidate,
                baseline_id,
                baseline_version,
                baseline_runtime,
                candidate_runtime,
                stage,
            )
            for stage in ("replay", "holdout", "shadow")
        )

    def _stage(
        self,
        job: EvolutionJobView,
        candidate: CandidateVersion,
        baseline_id: str,
        baseline_version: str,
        baseline_runtime: AgentRuntime,
        candidate_runtime: AgentRuntime,
        stage: Stage,
    ) -> EvolutionStagePlan:
        paths = self.fixtures[stage]
        if not paths:
            raise ValueError("evaluation_fixture_required")
        fixtures = [ReplayFixture.model_validate_json(path.read_text()) for path in paths]
        if any(item.split != stage for item in fixtures):
            raise ValueError("evaluation_fixture_split_mismatch")
        hashes = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
        families = Counter(item.observation.event_hint or "unknown" for item in fixtures)
        start = min(item.received_at for item in fixtures) - timedelta(seconds=1)
        end = max(item.received_at for item in fixtures) + timedelta(seconds=1)
        seed = json.dumps(
            {"stage": stage, "hashes": hashes}, sort_keys=True, separators=(",", ":")
        )
        suffix = hashlib.sha256(seed.encode()).hexdigest()[:20]
        dataset_id = f"dataset_{stage}_{suffix}"
        source_mode = "prospective" if stage == "shadow" else "fixture"
        cutoff_rule = "published_at <= observed_at <= received_at"
        dataset = EvaluationDatasetManifest(
            dataset_id=dataset_id,
            split=stage,
            manifest_hash=dataset_manifest_hash(
                stage,
                tuple(str(path) for path in paths),
                cutoff_rule,
                fixture_hashes=hashes,
                source_mode=source_mode,
                window_start_at=start,
                window_end_at=end,
                event_family_counts=dict(families),
            ),
            fixture_refs=[str(path) for path in paths],
            fixture_hashes=hashes,
            cutoff_rule=cutoff_rule,
            label_rule="outcomes.available_at > received_at",
            leakage_audit="passed",
            authorization="owner",
            visibility="owner_only",
            source_mode=source_mode,
            window_start_at=start,
            window_end_at=end,
            event_family_counts=dict(families),
            created_at=job.created_at,
        )
        experiment_identity = hashlib.sha256(
            f"{job.job_id}:{stage}:{suffix}".encode()
        ).hexdigest()
        experiment_id = f"experiment_{experiment_identity[:24]}"
        experiment = ExperimentManifest(
            experiment_id=experiment_id,
            dataset_id=dataset_id,
            baseline_ref=baseline_id,
            candidate_refs=[candidate.candidate_id],
            status="registered",
            created_at=job.created_at,
            strategy_version=candidate.version,
            runtime_id="evolution-comparison",
            runtime_version="evolution-comparison.v1",
            provider_id=None,
            model=None,
            schema_version="experiment.v1",
            random_seed=0,
            randomness_policy="deterministic",
            deadline_seconds=self.deadline_seconds,
            max_cost_usd=self.max_cost_usd,
        )
        return EvolutionStagePlan(
            dataset=dataset,
            experiment=experiment,
            fixture_paths=paths,
            runtimes={
                baseline_id: baseline_runtime,
                candidate.candidate_id: candidate_runtime,
            },
            strategy_versions={
                baseline_id: baseline_version,
                candidate.candidate_id: candidate.version,
            },
        )

    def _baseline_runtime(
        self, baseline_id: str, baseline_version: str
    ) -> AgentRuntime:
        if self.assets is None:
            if baseline_version != self.baseline_version:
                raise ValueError("baseline_version_mismatch")
            return self.baseline_runtime
        baseline = self.assets.get_candidate(baseline_id)
        if baseline is None:
            raise ValueError("evolution_baseline_not_found")
        if baseline.version != baseline_version:
            raise ValueError("baseline_version_mismatch")
        if baseline.content_ref is None:
            if baseline.source != "release" and baseline.version != self.baseline_version:
                raise ValueError("baseline_candidate_artifact_required")
            return self.baseline_runtime
        if self.artifacts is None:
            raise ValueError("candidate_artifact_store_required")
        proposal = self.artifacts.load_candidate(baseline)
        return CandidateConfigurationRuntime(self.baseline_runtime, proposal)


class EvolutionJobExecutor:
    """Compose existing planning/evaluation assets around a durable Job lease."""

    RETRYABLE_ERRORS = {
        "provider_timeout",
        "provider_rate_limited",
        "provider_unavailable",
        "search_temporarily_unavailable",
    }

    def __init__(
        self,
        *,
        jobs: EvolutionJobService,
        scanner: EvolutionTriggerScanner,
        context: EvolutionContextService,
        planner: EvolutionCandidatePlanner,
        artifacts: CandidateArtifactStore,
        assets: EvolutionAssetService,
        evaluation_plans: EvolutionEvaluationPlanFactory,
        evaluation_runner: EvaluationRunner,
        experiences: EvolutionExperienceService | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.jobs = jobs
        self.scanner = scanner
        self.context = context
        self.planner = planner
        self.artifacts = artifacts
        self.assets = assets
        self.evaluation_plans = evaluation_plans
        self.evaluation_runner = evaluation_runner
        self.experiences = experiences
        self.clock = clock

    async def tick(
        self,
        worker_id: str,
        *,
        lease_seconds: int = 300,
        retry_backoff_seconds: int = 60,
    ) -> EvolutionJobView | None:
        self.scanner.scan()
        job = self.jobs.claim(worker_id, lease_seconds=lease_seconds)
        if job is None:
            return None
        try:
            return await self._execute(job, worker_id, lease_seconds)
        except AgentExecutionError as exc:
            return self.jobs.fail(
                job.job_id,
                worker_id,
                error_code=exc.error_code,
                retryable=exc.retryable or exc.error_code in self.RETRYABLE_ERRORS,
                backoff_seconds=retry_backoff_seconds,
            )
        except (PermissionError, ValidationError, ValueError) as exc:
            code = str(exc) or "evolution_job_invalid"
            return self.jobs.fail(
                job.job_id,
                worker_id,
                error_code=code,
                retryable=code in self.RETRYABLE_ERRORS,
                backoff_seconds=retry_backoff_seconds,
            )
        except Exception:
            return self.jobs.fail(
                job.job_id,
                worker_id,
                error_code="evolution_job_failed",
                retryable=False,
                backoff_seconds=retry_backoff_seconds,
            )

    async def _execute(
        self, job: EvolutionJobView, worker_id: str, lease_seconds: int
    ) -> EvolutionJobView:
        planning_context = self.context.build(job)
        if job.candidate_id is not None:
            candidate = self.assets.get_candidate(job.candidate_id)
            if candidate is None:
                raise ValueError("evolution_candidate_not_found")
            proposal = self.artifacts.load(job, candidate.candidate_id)
            if candidate.content_ref is None or (
                hashlib.sha256(Path(candidate.content_ref).read_bytes()).hexdigest()
                != candidate.content_hash
            ):
                raise ValueError("candidate_artifact_hash_mismatch")
        else:
            self.jobs.advance(job.job_id, worker_id, stage="plan")
            proposal = await self._with_lease(
                self.planner.propose(planning_context),
                job.job_id,
                worker_id,
                lease_seconds,
            )
            if proposal is None:
                return self.jobs.advance(
                    job.job_id, worker_id, stage="plan", status="completed"
                )
            candidate_id, content_hash, content_ref = self.artifacts.persist(job, proposal)
            candidate = self.assets.register_candidate(
                CandidateVersion(
                    candidate_id=candidate_id,
                    candidate_type=proposal.candidate_type,
                    content_hash=content_hash,
                    version=proposal.version,
                    parent_version=proposal.parent_version,
                    status="candidate",
                    created_at=self.clock(),
                    content_ref=content_ref,
                    source="evolution_supervisor",
                )
            )
        self.jobs.advance(
            job.job_id,
            worker_id,
            stage="candidate",
            candidate_id=candidate.candidate_id,
        )
        baseline_id = planning_context.active_candidate_id
        baseline_version = planning_context.active_candidate_version
        if baseline_id is None or baseline_version is None:
            raise ValueError("evolution_baseline_missing")
        if proposal.parent_version != baseline_version:
            raise ValueError("candidate_parent_version_mismatch")
        if self.experiences is not None:
            self.experiences.create_from_job(job, proposal)

        experiment_refs: list[str] = []
        result_refs: list[str] = []
        for plan in self.evaluation_plans.build(
            job, candidate, baseline_id, baseline_version, proposal
        ):
            stage = plan.dataset.split
            if stage not in {"replay", "holdout", "shadow"}:
                raise ValueError("evaluation_stage_invalid")
            typed_stage = cast(Stage, stage)
            self.jobs.advance(
                job.job_id,
                worker_id,
                stage=typed_stage,
                experiment_refs=experiment_refs,
                result_refs=result_refs,
            )
            self.assets.register_dataset(plan.dataset)
            experiment = self.assets.register_experiment(plan.experiment)
            self.assets.set_experiment_status(experiment.experiment_id, "running")
            try:
                evaluation = await self._with_lease(
                    self.evaluation_runner.run(
                        experiment,
                        plan.dataset,
                        plan.fixture_paths,
                        plan.runtimes,
                        strategy_versions=plan.strategy_versions,
                    ),
                    job.job_id,
                    worker_id,
                    lease_seconds,
                )
            except Exception:
                self.assets.set_experiment_status(experiment.experiment_id, "failed")
                raise
            for result in evaluation.results:
                stored = self.assets.record_result(result)
                if stored.candidate_id == candidate.candidate_id:
                    result_refs.append(stored.result_id)
            self.assets.set_experiment_status(experiment.experiment_id, "completed")
            experiment_refs.append(experiment.experiment_id)

        self.assets.review_promotion(
            job.domain_pack_ref,
            # Review computes deterministic checks only; it cannot change the active pointer.
            PromotionReviewRequest(
                candidate_id=candidate.candidate_id,
                evaluation_refs=result_refs,
            ),
        )
        return self.jobs.advance(
            job.job_id,
            worker_id,
            stage="review",
            status="pending_owner_review",
            candidate_id=candidate.candidate_id,
            experiment_refs=experiment_refs,
            result_refs=result_refs,
        )

    async def _with_lease(
        self,
        operation: Awaitable[T],
        job_id: str,
        worker_id: str,
        lease_seconds: int,
    ) -> T:
        async def renew() -> None:
            interval = max(0.1, lease_seconds / 3)
            while True:
                await asyncio.sleep(interval)
                await asyncio.to_thread(
                    self.jobs.renew,
                    job_id,
                    worker_id,
                    lease_seconds=lease_seconds,
                )

        operation_task = asyncio.ensure_future(operation)
        renew_task = asyncio.create_task(renew())
        done, _ = await asyncio.wait(
            {operation_task, renew_task}, return_when=asyncio.FIRST_COMPLETED
        )
        if renew_task in done:
            operation_task.cancel()
            await asyncio.gather(operation_task, return_exceptions=True)
            await renew_task
            raise RuntimeError("evolution_lease_renewer_stopped")
        renew_task.cancel()
        await asyncio.gather(renew_task, return_exceptions=True)
        return await operation_task

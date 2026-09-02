from __future__ import annotations

import hashlib
import json
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, cast

from packages.contracts_py.decision_hub_contracts import (
    ArtifactView,
    CausalCase,
    CausalLink,
    HorizonDecision,
    ResearchPlan,
    ResearchRound,
    ResearchSessionRequest,
    ResearchSessionResult,
    ResearchStopReason,
    ResearchTask,
)
from packages.contracts_py.decision_hub_contracts.models import ObservationCreate
from packages.kernel.decision_hub_kernel.decision.sufficiency import (
    assess_evidence_sufficiency,
)
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.kernel.decision_hub_kernel.ports.research import ResearchTraceSink
from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError, AgentRuntime
from packages.orchestration.langgraph import build_analyze_text_service


class FixedResearchRuntime:
    """Comparison-only adapter around the unchanged legacy fixed analysis graph."""

    runtime_id = "fixed-baseline"
    runtime_version = "fixed-baseline.v1"
    profile_ref = "fixed-policy-counter-synthesis.v1"

    def __init__(self, runtime: AgentRuntime) -> None:
        self._runtime = runtime

    async def execute(
        self,
        request: ResearchSessionRequest,
        trace_sink: ResearchTraceSink | None = None,
    ) -> ResearchSessionResult:
        del trace_sink
        started_at = datetime.now(UTC)
        trigger = request.input_evidence[0]
        observation = ObservationCreate.model_validate(
            {
                "text": trigger.excerpt,
                "source_id": trigger.source_id,
                "source_type": "manual",
                "observed_at": trigger.observed_at,
                "published_at": trigger.published_at,
                "language": "en",
                "event_hint": request.event_id,
                "source_url": str(trigger.source_url) if trigger.source_url else None,
            }
        )
        remaining = max(
            0.001,
            (request.deadline_at - datetime.now(UTC)).total_seconds(),
        )
        try:
            with tempfile.TemporaryDirectory(prefix="decision-hub-fixed-eval-") as raw:
                database = Database(f"sqlite+pysqlite:///{Path(raw) / 'fixed.sqlite3'}")
                database.create_all()
                service = build_analyze_text_service(
                    database,
                    self._runtime,
                    run_timeout_seconds=remaining,
                    strategy_version="fixed-baseline.v1",
                    admission_clock=lambda: trigger.received_at,
                )
                _event_id, run_id, _admitted = await service.submit_and_run(observation)
                inspector = database.get_run_inspector(run_id)
                if inspector is None or inspector.artifact is None:
                    raise AgentExecutionError(
                        "fixed_baseline_artifact_missing",
                        "fixed baseline did not produce an artifact",
                    )
                artifact = inspector.artifact
                calls = inspector.calls
        except AgentExecutionError:
            raise
        except BaseException as exc:
            raise AgentExecutionError(
                "fixed_baseline_failed",
                "fixed baseline comparison execution failed",
            ) from exc

        finished_at = datetime.now(UTC)
        coverage = assess_evidence_sufficiency(
            request.evidence_requirements,
            [],
            cutoff_at=max(item.received_at for item in request.input_evidence),
        )
        evidence_refs = list(request.evidence_refs)
        horizons = [
            HorizonDecision(
                horizon=cast(Literal["30m", "24h", "72h"], forecast.horizon),
                action=forecast.direction.value,
                subjective_probability=forecast.probability,
                probability_status="uncalibrated",
                evidence_refs=evidence_refs,
                trigger=forecast.trigger,
                invalidation=forecast.invalidation,
                expires_at=_aware_utc(forecast.expires_at),
                next_review_at=_next_review(forecast.horizon, finished_at),
                missing_facts=[gap.requirement_id for gap in coverage.gaps],
                confidence_cap_reason="fixed baseline has no evidence-acquisition tool loop",
            )
            for forecast in artifact.forecasts
        ]
        plan = ResearchPlan(
            plan_id=f"fixed-plan:{request.request_id}",
            objective="Execute the unchanged fixed policy/counter/synthesis workflow.",
            tasks=[
                ResearchTask(
                    task_id="fixed-workflow",
                    capability_id="fixed.workflow",
                    objective="Produce the legacy fixed decision artifact.",
                    question="What conditional decision follows from the input text?",
                    input_evidence_refs=evidence_refs,
                    output_schema_ref="artifact.v1",
                    depends_on=[],
                    success_condition="The deterministic Gate receives one candidate artifact.",
                    priority=1,
                )
            ],
            required_capabilities=["fixed.workflow"],
            created_at=started_at,
        )
        research_round = ResearchRound(
            round=1,
            plan=plan,
            tool_invocations=[],
            tool_results=[],
            new_evidence_refs=[],
            coverage=coverage,
            started_at=started_at,
            finished_at=finished_at,
        )
        trace_hash = hashlib.sha256(
            json.dumps(
                artifact.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        hard_gaps = [
            gap.requirement_id for gap in coverage.gaps if gap.importance == "hard"
        ]
        total_tokens = (
            sum(item.total_tokens or 0 for item in calls)
            if any(item.total_tokens is not None for item in calls)
            else None
        )
        total_cost = (
            sum(item.cost_usd or 0 for item in calls)
            if calls and all(item.cost_usd is not None for item in calls)
            else None
        )
        return ResearchSessionResult(
            schema_version="research-session-result.v1",
            request_id=request.request_id,
            research_session_id=f"fixed:{request.request_id}",
            runtime_id=self.runtime_id,
            runtime_version=self.runtime_version,
            profile_ref=self.profile_ref,
            trace_ref=f"fixed://run/{run_id}/artifact/{artifact.artifact_id}",
            trace_hash=trace_hash,
            status="degraded" if hard_gaps else "completed",
            rounds=[research_round],
            evidence_candidates=[],
            final_coverage=coverage,
            causal_case=_causal_case(artifact, evidence_refs, request.request_id),
            horizons=horizons,
            stop_reason=ResearchStopReason(
                code="critical_data_unavailable" if hard_gaps else "sufficient",
                detail=(
                    "Fixed baseline completed without an evidence-acquisition loop."
                    if hard_gaps
                    else "Fixed baseline satisfied the supplied requirements."
                ),
                bounded=bool(hard_gaps),
                remaining_hard_gaps=hard_gaps,
            ),
            total_tool_calls=0,
            total_subagents=0,
            total_tokens=total_tokens,
            estimated_cost_usd=total_cost,
            started_at=started_at,
            finished_at=finished_at,
        )

    async def close(self) -> None:
        return None


def _causal_case(
    artifact: ArtifactView, evidence_refs: list[str], request_id: str
) -> CausalCase:
    main_statements = artifact.transmission_chain or artifact.inferences or [artifact.summary]
    main_chain = [
        CausalLink(
            link_id=f"fixed-main-{index}",
            claim_type="inference",
            statement=statement,
            evidence_refs=evidence_refs,
            confirmation="Requires independent market confirmation.",
            invalidation="The stated transmission path fails to appear.",
            affected_horizons=["30m", "24h", "72h"],
        )
        for index, statement in enumerate(main_statements, start=1)
    ]
    opposite = CausalLink(
        link_id="fixed-counter-1",
        claim_type="scenario",
        statement=artifact.counter_thesis or "The event may already be priced in.",
        evidence_refs=evidence_refs,
        confirmation="Price action rejects the main transmission path.",
        invalidation="Independent market evidence confirms the main path.",
        affected_horizons=["30m", "24h", "72h"],
    )
    return CausalCase(
        case_id=f"fixed-case:{request_id}",
        thesis=artifact.summary,
        main_chain=main_chain,
        opposite_chain=[opposite],
        unresolved_questions=artifact.uncertainty,
        evidence_refs=evidence_refs,
    )


def _next_review(horizon: str, finished_at: datetime) -> datetime:
    delays = {"30m": timedelta(minutes=10), "24h": timedelta(hours=6), "72h": timedelta(hours=24)}
    return finished_at + delays.get(horizon, timedelta(minutes=10))


def _aware_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

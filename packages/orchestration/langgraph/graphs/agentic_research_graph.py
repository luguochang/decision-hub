from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Literal

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import Checkpointer

from packages.contracts_py.decision_hub_contracts import (
    CoverageAssessment,
    EvidenceCandidate,
    EvidenceRequirement,
    ExecutionBudget,
    HorizonDecision,
    ResearchInputEvidence,
    ResearchSessionRequest,
    ResearchSessionResult,
    ResearchStopReason,
    ResearchTraceEvent,
)
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchEvidenceService,
)
from packages.kernel.decision_hub_kernel.decision.sufficiency import (
    assess_evidence_sufficiency,
)
from packages.kernel.decision_hub_kernel.ports.research import (
    ResearchHarnessRuntime,
    ResearchTraceSink,
)
from packages.orchestration.langgraph.state.research import AgenticResearchState


def initial_research_state(request: ResearchSessionRequest) -> AgenticResearchState:
    return {
        "request": request.model_dump(mode="json"),
        "requirements": [item.model_dump(mode="json") for item in request.evidence_requirements],
        "evidence": [],
        "rounds": [],
        "current_round": request.current_round,
        "previous_evidence_ids": [],
        "total_tool_calls": 0,
        "total_subagents": 0,
        "total_tokens": 0,
        "estimated_cost_usd": 0.0,
        "decision_cutoff_at": _initial_decision_cutoff(request).isoformat(),
        "synthesis_failure_code": None,
    }


def build_agentic_research_graph(
    runtime: ResearchHarnessRuntime,
    evidence_service: ResearchEvidenceService,
    *,
    checkpointer: Checkpointer = None,
    trace_sink: ResearchTraceSink | None = None,
):
    """Build the product-level evidence-round graph around one Harness loop.

    DSH owns the inner model/tool/subagent loop. This graph only decides whether
    the next bounded evidence round is necessary and freezes the final snapshot.
    """

    async def emit(
        request: ResearchSessionRequest,
        *,
        event_type: str,
        stage: str,
        summary: str,
        occurred_at: datetime,
        status: str = "succeeded",
        reference_type: str | None = None,
        reference_id: str | None = None,
        error_code: str | None = None,
    ) -> None:
        if trace_sink is None:
            return
        event = ResearchTraceEvent.model_validate(
            {
                "schema_version": "research-trace-event.v1",
                "run_id": request.run_id,
                "research_session_id": f"product:{request.run_id}",
                "sequence_no": 0,
                "event_type": event_type,
                "occurred_at": occurred_at,
                "stage": stage,
                "summary": summary,
                "reference_type": reference_type,
                "reference_id": reference_id,
                "status": status,
                "error_code": error_code,
            }
        )
        await trace_sink.emit(event)

    async def run_round(state: AgenticResearchState) -> dict[str, object]:
        base_request = ResearchSessionRequest.model_validate(state["request"])
        current_round = state.get("current_round", base_request.current_round)
        if not state.get("rounds"):
            await emit(
                base_request,
                event_type="session_started",
                stage="planning",
                summary="Research lifecycle started from the immutable Trigger Snapshot.",
                occurred_at=_trigger_cutoff(base_request),
                status="running",
                reference_type="snapshot",
                reference_id=base_request.trigger_snapshot_id,
            )
        requirements = {
            item.requirement_id: item
            for item in (EvidenceRequirement.model_validate(raw) for raw in state["requirements"])
        }
        existing = [EvidenceCandidate.model_validate(raw) for raw in state.get("evidence", [])]
        coverage = (
            _coverage_from_state(state)
            if state.get("coverage")
            else assess_evidence_sufficiency(
                requirements.values(),
                existing,
                cutoff_at=_decision_cutoff(state, base_request),
            )
        )
        await emit(
            base_request,
            event_type="coverage_assessed",
            stage="assessing_sufficiency",
            summary=(
                f"Round {current_round} began with hard coverage "
                f"{coverage.hard_coverage_ratio:.0%} and {len(coverage.gaps)} open gaps."
            ),
            occurred_at=_decision_cutoff(state, base_request),
            reference_type="coverage",
            reference_id=f"round:{current_round}:before",
        )
        if state.get("rounds"):
            await emit(
                base_request,
                event_type="replan",
                stage="planning",
                summary=f"Evidence gaps triggered bounded research round {current_round}.",
                occurred_at=_decision_cutoff(state, base_request),
                status="running",
                reference_type="round",
                reference_id=str(current_round),
            )
        remaining_tool_calls = (
            base_request.execution_budget.max_tool_calls
            - state.get("total_tool_calls", 0)
        )
        if remaining_tool_calls <= 0:
            raise RuntimeError("research_tool_budget_exhausted_before_round")
        request = base_request.model_copy(
            update={
                "current_round": current_round,
                "evidence_refs": _unique_refs(
                    (*base_request.evidence_refs, *(item.evidence_id for item in existing))
                ),
                "input_evidence": _input_evidence(base_request.input_evidence, existing),
                "target_gaps": coverage.gaps,
                "execution_budget": base_request.execution_budget.model_copy(
                    update={"max_tool_calls": remaining_tool_calls}
                ),
                "repair_instructions": base_request.repair_instructions,
            }
        )
        result = await runtime.execute(request, trace_sink=trace_sink)
        round_cutoff = _round_cutoff(state, request, result)
        if result.rounds:
            await emit(
                request,
                event_type="plan_created",
                stage="planning",
                summary=result.rounds[-1].plan.objective,
                occurred_at=result.rounds[-1].plan.created_at,
                reference_type="plan",
                reference_id=result.rounds[-1].plan.plan_id,
            )
        accepted = evidence_service.accept_candidates(
            run_id=request.run_id,
            capability_id="dsh.research",
            candidates=result.evidence_candidates,
            requirements=requirements,
            cutoff_at=round_cutoff,
        )
        all_evidence = evidence_service.list_run_evidence(request.run_id)
        for item in accepted:
            accepted_event = item.quality == "accepted"
            await emit(
                request,
                event_type="evidence_accepted" if accepted_event else "evidence_rejected",
                stage="acquiring_evidence",
                summary=(
                    f"Accepted {item.source_id} evidence for {item.requirement_id}."
                    if accepted_event
                    else f"Rejected {item.source_id} evidence with quality {item.quality}."
                ),
                occurred_at=result.finished_at,
                status="succeeded" if accepted_event else "denied",
                reference_type="evidence",
                reference_id=item.evidence_id,
                error_code=None if accepted_event else f"evidence_{item.quality}",
            )
        new_ids = [
            item.evidence_id
            for item in accepted
            if item.quality == "accepted"
            and item.evidence_id not in set(state.get("previous_evidence_ids", []))
        ]
        deterministic_coverage = assess_evidence_sufficiency(
            requirements.values(), all_evidence, cutoff_at=round_cutoff
        )
        await emit(
            request,
            event_type="coverage_assessed",
            stage="assessing_sufficiency",
            summary=(
                f"Round {current_round} ended with hard coverage "
                f"{deterministic_coverage.hard_coverage_ratio:.0%}; "
                f"status {deterministic_coverage.status}."
            ),
            occurred_at=result.finished_at,
            reference_type="coverage",
            reference_id=f"round:{current_round}:after",
        )
        await emit(
            request,
            event_type="round_completed",
            stage="assessing_sufficiency",
            summary=f"Bounded evidence round {current_round} completed.",
            occurred_at=result.finished_at,
            reference_type="round",
            reference_id=str(current_round),
        )
        if result.rounds:
            round_record = result.rounds[-1].model_copy(
                update={
                    "coverage": deterministic_coverage,
                    "new_evidence_refs": new_ids,
                }
            )
            rounds = [*state.get("rounds", []), round_record.model_dump(mode="json")]
        else:  # defensive; the contract normally requires at least one round
            rounds = list(state.get("rounds", []))
        total_cost = _sum_optional(
            state.get("estimated_cost_usd"), result.estimated_cost_usd
        )
        return {
            "evidence": [item.model_dump(mode="json") for item in all_evidence],
            "rounds": rounds,
            "latest_result": result.model_dump(mode="json"),
            "synthesis_failure_code": result.synthesis_failure_code,
            "coverage": deterministic_coverage.model_dump(mode="json"),
            "previous_evidence_ids": [item.evidence_id for item in all_evidence],
            "new_evidence_ids": new_ids,
            "total_tool_calls": state.get("total_tool_calls", 0) + result.total_tool_calls,
            "total_subagents": state.get("total_subagents", 0) + result.total_subagents,
            "total_tokens": _sum_optional(state.get("total_tokens"), result.total_tokens),
            "estimated_cost_usd": total_cost,
            "decision_cutoff_at": round_cutoff.isoformat(),
            "current_round": state.get("current_round", base_request.current_round),
        }

    def route_after_round(state: AgenticResearchState) -> Literal["next_round", "finalize"]:
        coverage = _coverage_from_state(state)
        request = ResearchSessionRequest.model_validate(state["request"])
        budget = request.execution_budget
        if coverage.status == "sufficient":
            return "finalize"
        if state.get("total_tool_calls", 0) >= budget.max_tool_calls:
            return "finalize"
        if state.get("total_subagents", 0) > budget.max_subagents:
            return "finalize"
        if state.get("current_round", 1) >= budget.max_evidence_rounds:
            return "finalize"
        return "next_round" if state.get("new_evidence_ids") else "finalize"

    def next_round(state: AgenticResearchState) -> dict[str, object]:
        return {"current_round": state.get("current_round", 1) + 1}

    async def finalize(state: AgenticResearchState) -> dict[str, object]:
        request = ResearchSessionRequest.model_validate(state["request"])
        latest = ResearchSessionResult.model_validate(state["latest_result"])
        await emit(
            request,
            event_type="synthesis_started",
            stage="synthesis",
            summary="The product Gate started final causal and horizon synthesis.",
            occurred_at=latest.finished_at,
            status="running",
            reference_type="run",
            reference_id=request.run_id,
        )
        coverage = _coverage_from_state(state)
        budget = request.execution_budget
        stop_code, detail = _stop_reason(state, coverage, budget)
        synthesis_failure_code = state.get("synthesis_failure_code")
        if synthesis_failure_code is not None:
            # A sufficient evidence set cannot repair a rejected model
            # synthesis in the same round. Keep the result degraded until a
            # later, independently valid synthesis succeeds.
            stop_code = "critical_data_unavailable"
            detail = (
                "Synthesis remained untrusted ("
                f"{synthesis_failure_code}); trusted Evidence was retained, "
                "but no directional semantics were published."
            )
        horizons = _valid_horizons(
            latest.horizons,
            reference_at=_decision_cutoff(state, request),
        )
        if latest.horizons and horizons is None:
            stop_code = "critical_data_unavailable"
            detail = "horizon outputs are not distinct or do not cover 30m/24h/72h"
            horizons = []
        if stop_code != "sufficient" and horizons:
            # A bounded/insufficient run may retain the model proposal for
            # audit, but it must never expose a directional action as a
            # product decision. The ledger applies the same guard defensively.
            horizons = [_fail_closed_horizon(item, stop_code) for item in horizons]
        evidence = [EvidenceCandidate.model_validate(raw) for raw in state.get("evidence", [])]
        snapshot = evidence_service.freeze_decision_snapshot(
            run_id=request.run_id,
            evidence_refs=[item.evidence_id for item in evidence if item.quality == "accepted"],
            cutoff_at=_decision_cutoff(state, request),
        )
        stop = ResearchStopReason(
            code=stop_code,
            detail=detail,
            bounded=stop_code != "sufficient",
            remaining_hard_gaps=[
                item.requirement_id for item in coverage.gaps if item.importance == "hard"
            ],
        )
        status = "completed" if stop_code == "sufficient" else "degraded"
        final = latest.model_copy(
            update={
                "status": status,
                "rounds": [
                    # Round records are deterministically replaced with the Gate's coverage.
                    type(latest.rounds[0]).model_validate(raw)
                    for raw in state.get("rounds", [])
                ],
                "evidence_candidates": evidence,
                "final_coverage": coverage,
                "horizons": horizons or [],
                "stop_reason": stop,
                "total_tool_calls": state.get("total_tool_calls", latest.total_tool_calls),
                "total_subagents": state.get("total_subagents", latest.total_subagents),
                "total_tokens": state.get("total_tokens"),
                "estimated_cost_usd": state.get("estimated_cost_usd"),
                "synthesis_failure_code": synthesis_failure_code,
            }
        )
        await emit(
            request,
            event_type="session_stopped",
            stage="done",
            summary=detail,
            occurred_at=latest.finished_at,
            status="succeeded" if stop_code == "sufficient" else "degraded",
            reference_type="stop_reason",
            reference_id=stop_code,
        )
        return {
            "stop_code": stop_code,
            "stop_detail": detail,
            "decision_snapshot_id": snapshot.snapshot_id,
            "final_result": final.model_dump(mode="json"),
        }

    graph = StateGraph(AgenticResearchState)
    graph.add_node("research_round", run_round)
    graph.add_node("next_round", next_round)
    graph.add_node("finalize", finalize)
    graph.add_edge(START, "research_round")
    graph.add_conditional_edges("research_round", route_after_round, ["next_round", "finalize"])
    graph.add_edge("next_round", "research_round")
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=checkpointer)


def _trigger_cutoff(request: ResearchSessionRequest) -> datetime:
    return max(
        item.received_at for item in request.input_evidence
    ).astimezone(UTC)


def _initial_decision_cutoff(request: ResearchSessionRequest) -> datetime:
    if request.execution_mode == "replay":
        return request.pit_cutoff_at.astimezone(UTC)
    return _trigger_cutoff(request)


def _decision_cutoff(
    state: AgenticResearchState, request: ResearchSessionRequest
) -> datetime:
    raw = state.get("decision_cutoff_at")
    if raw is None:
        return _initial_decision_cutoff(request)
    return datetime.fromisoformat(raw).astimezone(UTC)


def _round_cutoff(
    state: AgenticResearchState,
    request: ResearchSessionRequest,
    result: ResearchSessionResult,
) -> datetime:
    finished_at = result.finished_at.astimezone(UTC)
    if finished_at > request.deadline_at.astimezone(UTC):
        raise ValueError("research_result_after_deadline")
    pit_cutoff = request.pit_cutoff_at.astimezone(UTC)
    if request.execution_mode == "replay":
        return pit_cutoff
    if finished_at > pit_cutoff:
        raise ValueError("research_result_after_pit_cutoff")
    return max(_decision_cutoff(state, request), finished_at)


def _input_evidence(
    initial: Sequence[ResearchInputEvidence], evidence: Sequence[EvidenceCandidate]
) -> list[ResearchInputEvidence]:
    result = list(initial)
    seen = {item.evidence_id for item in result}
    for item in evidence:
        if item.evidence_id in seen:
            continue
        result.append(
            ResearchInputEvidence(
                evidence_id=item.evidence_id,
                kind=item.kind,
                authority=item.authority,
                source_id=item.source_id,
                source_url=item.source_url,
                published_at=item.published_at,
                observed_at=item.observed_at,
                received_at=item.received_at,
                content_hash=item.content_hash,
                excerpt=item.excerpt,
            )
        )
        seen.add(item.evidence_id)
    return result[:50]


def _unique_refs(refs: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(refs))


def _coverage_from_state(state: AgenticResearchState) -> CoverageAssessment:
    return CoverageAssessment.model_validate(state["coverage"])


def _sum_optional(left: int | float | None, right: int | float | None):
    if left is None or right is None:
        return None
    return left + right


def _stop_reason(
    state: AgenticResearchState,
    coverage: CoverageAssessment,
    budget: ExecutionBudget,
) -> tuple[Literal["sufficient", "tool_budget", "round_budget", "critical_data_unavailable"], str]:
    if coverage.status == "sufficient":
        return "sufficient", "All hard evidence requirements passed the deterministic Gate."
    if state.get("total_tool_calls", 0) >= budget.max_tool_calls:
        return "tool_budget", "The audited tool-call budget was reached."
    if state.get("total_subagents", 0) > budget.max_subagents:
        return "tool_budget", "The audited subagent budget was exceeded; output is bounded."
    if state.get("current_round", 1) >= budget.max_evidence_rounds:
        return "round_budget", "The bounded evidence-round budget was reached."
    return "critical_data_unavailable", "No new accepted evidence changed the hard coverage."


def _valid_horizons(
    horizons: Sequence[HorizonDecision],
    *,
    reference_at: datetime,
) -> list[HorizonDecision] | None:
    if not horizons:
        return []
    if {item.horizon for item in horizons} != {"30m", "24h", "72h"} or len(horizons) != 3:
        return None
    if any(
        item.next_review_at <= reference_at or item.next_review_at > item.expires_at
        for item in horizons
    ):
        return None
    signatures = {
        (
            item.trigger,
            item.invalidation,
            item.expires_at,
            item.next_review_at,
            tuple(item.evidence_refs),
        )
        for item in horizons
    }
    return list(horizons) if len(signatures) == 3 else None


def _fail_closed_horizon(
    horizon: HorizonDecision,
    stop_code: str,
) -> HorizonDecision:
    return horizon.model_copy(
        update={
            "action": "no_trade",
            "subjective_probability": 0.5,
            "probability_status": "uncalibrated",
            "confidence_cap_reason": (
                f"Directional output suppressed because research stopped at {stop_code}."
            ),
        }
    )

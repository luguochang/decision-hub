from __future__ import annotations

import time

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import Checkpointer
from langgraph.types import RetryPolicy

from packages.kernel.decision_hub_kernel.application.commit import CommitDecisionService
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.application.snapshot import SnapshotService
from packages.kernel.decision_hub_kernel.application.steps import RunStepService
from packages.kernel.decision_hub_kernel.decision.gate import evaluate_gate
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    RunRecord,
    RunStatus,
)
from packages.kernel.decision_hub_kernel.ports.runtime import (
    AgentExecutionError,
    AgentRuntime,
)
from packages.orchestration.langgraph.graphs.research_graph import build_research_graph
from packages.orchestration.langgraph.state.decision import DecisionState


def _retry_provider_failure(error: Exception) -> bool:
    return isinstance(error, AgentExecutionError) and error.retryable


def build_decision_graph(
    database: Database, runtime: AgentRuntime, *, checkpointer: Checkpointer = None
):
    snapshots = SnapshotService(database)
    run_service = RunService(database)
    commit = CommitDecisionService(database)
    steps = RunStepService(database)
    research = build_research_graph(runtime, database)

    def mark_running(state: DecisionState) -> dict[str, object]:
        handle = steps.start(state["run_id"], "mark_running")
        try:
            run_service.set_status(state["run_id"], RunStatus.running)
            database.record_run_event(state["run_id"], 1, "run.started")
        except BaseException as exc:
            steps.fail(handle, exc)
            raise
        steps.complete(handle)
        return {}

    def freeze(state: DecisionState) -> dict[str, object]:
        handle = steps.start(state["run_id"], "freeze_snapshot")
        try:
            snapshot_id, _ = snapshots.freeze_for_run(
                state["run_id"], state["event_id"]
            )
            database.record_run_event(
                state["run_id"], 2, "snapshot.frozen", {"snapshot_id": snapshot_id}
            )
        except BaseException as exc:
            steps.fail(handle, exc)
            raise
        steps.complete(handle)
        return {"snapshot_id": snapshot_id}

    async def research_node(state: DecisionState) -> dict[str, object]:
        handle = steps.start(state["run_id"], "research")
        try:
            result = await research.ainvoke(state)
            database.record_run_event(state["run_id"], 3, "research.completed")
        except BaseException as exc:
            steps.fail(handle, exc)
            raise
        steps.complete(handle)
        return {
            "policy_output_id": result["policy_output_id"],
            "counter_output_id": result["counter_output_id"],
            "candidate_output_id": result["candidate_output_id"],
        }

    def gate_and_commit(state: DecisionState) -> dict[str, object]:
        handle = steps.start(state["run_id"], "gate_and_commit")
        started = time.perf_counter()
        try:
            candidate = database.get_role_output(state["candidate_output_id"])
            gate = evaluate_gate(candidate)
            artifact_id = commit.commit(
                state["run_id"], state["event_id"], candidate, gate
            )
            with database.session() as session:
                run = session.get(RunRecord, state["run_id"])
                if run:
                    run.snapshot_id = state["snapshot_id"]
                    run.latency_ms = round((time.perf_counter() - started) * 1000)
        except BaseException as exc:
            steps.fail(handle, exc)
            raise
        steps.complete(handle)
        return {"gate_status": gate.status.value, "artifact_id": artifact_id}

    graph = StateGraph(DecisionState)
    graph.add_node("mark_running", mark_running)
    graph.add_node("freeze_snapshot", freeze)
    graph.add_node(
        "research",
        research_node,
        retry_policy=RetryPolicy(
            initial_interval=0.01,
            backoff_factor=1.0,
            max_interval=0.01,
            max_attempts=max(1, runtime.max_attempts),
            jitter=False,
            retry_on=_retry_provider_failure,
        ),
    )
    graph.add_node("gate_and_commit", gate_and_commit)
    graph.add_edge(START, "mark_running")
    graph.add_edge("mark_running", "freeze_snapshot")
    graph.add_edge("freeze_snapshot", "research")
    graph.add_edge("research", "gate_and_commit")
    graph.add_edge("gate_and_commit", END)
    return graph.compile(checkpointer=checkpointer)

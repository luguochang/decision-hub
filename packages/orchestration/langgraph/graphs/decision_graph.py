from __future__ import annotations

import json
import time
from datetime import UTC, datetime

from langgraph.graph import END, START, StateGraph

from packages.kernel.decision_hub_kernel.application.commit import CommitDecisionService
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.application.snapshot import SnapshotService
from packages.kernel.decision_hub_kernel.decision.gate import evaluate_gate
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    RunEventRecord,
    RunRecord,
    RunStatus,
)
from packages.kernel.decision_hub_kernel.ports.runtime import AgentRuntime
from packages.orchestration.langgraph.graphs.research_graph import build_research_graph
from packages.orchestration.langgraph.state.decision import DecisionState


def build_decision_graph(database: Database, runtime: AgentRuntime):
    snapshots = SnapshotService(database)
    run_service = RunService(database)
    commit = CommitDecisionService(database)
    research = build_research_graph(runtime)

    def mark_running(state: DecisionState) -> dict[str, object]:
        run_service.set_status(state["run_id"], RunStatus.running)
        with database.session() as session:
            session.add(
                RunEventRecord(
                    run_id=state["run_id"],
                    sequence_no=1,
                    event_type="run.started",
                    occurred_at=datetime.now(UTC),
                    payload_json="{}",
                )
            )
        return {}

    def freeze(state: DecisionState) -> dict[str, object]:
        from packages.contracts_py.decision_hub_contracts.models import TextEnvelope

        envelope = TextEnvelope.model_validate(state["envelope"])
        snapshot_id, evidence_ids = snapshots.freeze(state["event_id"], envelope)
        with database.session() as session:
            session.add(
                RunEventRecord(
                    run_id=state["run_id"],
                    sequence_no=2,
                    event_type="snapshot.frozen",
                    occurred_at=envelope.received_at,
                    payload_json=json.dumps({"snapshot_id": snapshot_id}),
                )
            )
        return {"snapshot_id": snapshot_id, "evidence_ids": evidence_ids}

    async def research_node(state: DecisionState) -> dict[str, object]:
        result = await research.ainvoke(state)
        with database.session() as session:
            session.add(
                RunEventRecord(
                    run_id=state["run_id"],
                    sequence_no=3,
                    event_type="research.completed",
                    occurred_at=datetime.now(UTC),
                    payload_json="{}",
                )
            )
        return {"candidate": result["candidate"]}

    def gate_and_commit(state: DecisionState) -> dict[str, object]:
        started = time.perf_counter()
        candidate = state["candidate"]
        gate = evaluate_gate(candidate)
        artifact_id = commit.commit(state["run_id"], state["event_id"], candidate, gate)
        with database.session() as session:
            run = session.get(RunRecord, state["run_id"])
            if run:
                run.snapshot_id = state["snapshot_id"]
                run.latency_ms = round((time.perf_counter() - started) * 1000)
        return {"gate_status": gate.status.value, "artifact_id": artifact_id}

    graph = StateGraph(DecisionState)
    graph.add_node("mark_running", mark_running)
    graph.add_node("freeze_snapshot", freeze)
    graph.add_node("research", research_node)
    graph.add_node("gate_and_commit", gate_and_commit)
    graph.add_edge(START, "mark_running")
    graph.add_edge("mark_running", "freeze_snapshot")
    graph.add_edge("freeze_snapshot", "research")
    graph.add_edge("research", "gate_and_commit")
    graph.add_edge("gate_and_commit", END)
    return graph.compile()

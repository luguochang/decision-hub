from __future__ import annotations

from pathlib import Path

from langchain_core.runnables import RunnableConfig

from packages.contracts_py.decision_hub_contracts import ResearchSessionRequest
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchEvidenceService,
)
from packages.kernel.decision_hub_kernel.application.research_observability import (
    ResearchObservabilityService,
)
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.kernel.decision_hub_kernel.ports.research import ResearchHarnessRuntime
from packages.kernel.decision_hub_kernel.ports.runtime import AgentRuntime
from packages.kernel.decision_hub_kernel.ports.workflow import DecisionExecutionRequest
from packages.orchestration.langgraph.checkpoint.recovery import CheckpointStore
from packages.orchestration.langgraph.graphs.agentic_research_graph import (
    build_agentic_research_graph,
    initial_research_state,
)
from packages.orchestration.langgraph.graphs.decision_graph import build_decision_graph
from packages.orchestration.langgraph.state.decision import DecisionState


class LangGraphDecisionExecutor:
    def __init__(
        self,
        database: Database,
        runtime: AgentRuntime,
        *,
        checkpoint_path: Path | None = None,
    ) -> None:
        self.database = database
        self.runtime = runtime
        self.checkpoint_path = checkpoint_path
        self.graph = build_decision_graph(database, runtime)

    async def execute(self, request: DecisionExecutionRequest) -> None:
        state: DecisionState = {
            "run_id": request.run_id,
            "event_id": request.event_id,
            "deadline_at": request.deadline_at.isoformat(),
        }
        config: RunnableConfig = {
            "configurable": {"thread_id": request.run_id}
        }
        if self.checkpoint_path is None:
            await self.graph.ainvoke(state, config=config)
            return
        async with CheckpointStore(self.checkpoint_path).open() as saver:
            graph = build_decision_graph(
                self.database, self.runtime, checkpointer=saver
            )
            await graph.ainvoke(state, config=config)

    async def resume(self, run_id: str) -> None:
        if self.checkpoint_path is None:
            raise RuntimeError("checkpoint recovery is not configured")
        async with CheckpointStore(self.checkpoint_path).open() as saver:
            graph = build_decision_graph(
                self.database, self.runtime, checkpointer=saver
            )
            await graph.ainvoke(
                None, config={"configurable": {"thread_id": run_id}}
            )


class LangGraphResearchExecutor:
    """Durable entry point for the R2-R agentic evidence-round graph."""

    def __init__(
        self,
        database: Database,
        runtime: ResearchHarnessRuntime,
        *,
        checkpoint_path: Path | None = None,
    ) -> None:
        self.database = database
        self.runtime = runtime
        self.checkpoint_path = checkpoint_path
        self.trace_sink = ResearchObservabilityService(database)

    async def execute(self, request: ResearchSessionRequest) -> dict[str, object]:
        state = initial_research_state(request)
        config: RunnableConfig = {"configurable": {"thread_id": request.run_id}}
        if self.checkpoint_path is None:
            graph = build_agentic_research_graph(
                self.runtime,
                ResearchEvidenceService(self.database),
                trace_sink=self.trace_sink,
            )
            return await graph.ainvoke(state, config=config)
        async with CheckpointStore(self.checkpoint_path).open() as saver:
            graph = build_agentic_research_graph(
                self.runtime,
                ResearchEvidenceService(self.database),
                checkpointer=saver,
                trace_sink=self.trace_sink,
            )
            return await graph.ainvoke(state, config=config)

    async def resume(self, run_id: str) -> dict[str, object]:
        if self.checkpoint_path is None:
            raise RuntimeError("checkpoint recovery is not configured")
        async with CheckpointStore(self.checkpoint_path).open() as saver:
            graph = build_agentic_research_graph(
                self.runtime,
                ResearchEvidenceService(self.database),
                checkpointer=saver,
                trace_sink=self.trace_sink,
            )
            return await graph.ainvoke(None, config={"configurable": {"thread_id": run_id}})

    async def has_checkpoint(self, run_id: str) -> bool:
        if self.checkpoint_path is None:
            return False
        async with CheckpointStore(self.checkpoint_path).open() as saver:
            return (
                await saver.aget_tuple({"configurable": {"thread_id": run_id}})
            ) is not None

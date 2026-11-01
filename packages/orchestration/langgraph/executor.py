from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import cast

from langchain_core.runnables import RunnableConfig

from packages.contracts_py.decision_hub_contracts import ResearchSessionRequest
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchEvidenceService,
)
from packages.kernel.decision_hub_kernel.application.research_graph_binding import (
    prepare_graph_state_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_graph_projection import (
    AgenticResearchState,
)
from packages.kernel.decision_hub_kernel.application.research_observability import (
    ResearchObservabilityService,
)
from packages.kernel.decision_hub_kernel.application.research_task import (
    ResearchTaskError,
    ResearchTaskService,
)
from packages.kernel.decision_hub_kernel.persistence.db import Database, utcnow
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
from packages.orchestration.owned_work import HeavyWorkAdmission


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
            "lease_owner": request.lease_owner,
        }
        config: RunnableConfig = {"configurable": {"thread_id": request.run_id}}
        if self.checkpoint_path is None:
            await self.graph.ainvoke(state, config=config)
            return
        async with CheckpointStore(self.checkpoint_path).open() as saver:
            graph = build_decision_graph(self.database, self.runtime, checkpointer=saver)
            await graph.ainvoke(state, config=config)

    async def resume(self, run_id: str) -> None:
        if self.checkpoint_path is None:
            raise RuntimeError("checkpoint recovery is not configured")
        async with CheckpointStore(self.checkpoint_path).open() as saver:
            graph = build_decision_graph(self.database, self.runtime, checkpointer=saver)
            await graph.ainvoke(None, config={"configurable": {"thread_id": run_id}})


class LangGraphResearchExecutor:
    """Durable entry point for the R2-R agentic evidence-round graph."""

    def __init__(
        self,
        database: Database,
        runtime: ResearchHarnessRuntime,
        *,
        checkpoint_path: Path | None = None,
        clock: Callable[[], datetime] = utcnow,
        expected_lease_owner: str | None = None,
        heavy_work_admission: HeavyWorkAdmission | None = None,
    ) -> None:
        self.database = database
        self.runtime = runtime
        self.expected_lease_owner = expected_lease_owner
        self.checkpoint_path = checkpoint_path
        self.trace_sink = ResearchObservabilityService(database)
        self.tasks = ResearchTaskService(database, clock=clock)
        self.owned_work = heavy_work_admission or HeavyWorkAdmission()

    async def execute(
        self, request: ResearchSessionRequest, *, require_context_selection: bool = False
    ) -> dict[str, object]:
        await self.owned_work.run_thread(self.tasks.freeze, request)
        state = initial_research_state(request)
        config: RunnableConfig = {"configurable": {"thread_id": request.run_id}}
        if self.checkpoint_path is None:
            graph = build_agentic_research_graph(
                self.runtime,
                ResearchEvidenceService(self.database),
                trace_sink=self.trace_sink,
                progress_reader=self.trace_sink,
                expected_lease_owner=self.expected_lease_owner,
                require_context_selection=require_context_selection,
                heavy_work_admission=self.owned_work,
            )
            return await graph.ainvoke(state, config=config)
        async with CheckpointStore(self.checkpoint_path).open() as saver:
            graph = build_agentic_research_graph(
                self.runtime,
                ResearchEvidenceService(self.database),
                checkpointer=saver,
                trace_sink=self.trace_sink,
                progress_reader=self.trace_sink,
                expected_lease_owner=self.expected_lease_owner,
                require_context_selection=require_context_selection,
                heavy_work_admission=self.owned_work,
            )
            return await graph.ainvoke(state, config=config)

    async def resume(
        self, run_id: str, *, require_context_selection: bool = False
    ) -> dict[str, object]:
        if self.checkpoint_path is None:
            raise RuntimeError("checkpoint recovery is not configured")
        async with CheckpointStore(self.checkpoint_path).open() as saver:
            saved = await self.owned_work.run_thread(self.tasks.get, run_id)
            checkpoint = await saver.aget_tuple({"configurable": {"thread_id": run_id}})
            if checkpoint is None:
                raise ResearchTaskError("research_task_request_missing_before_execution")
            checkpoint_request = ResearchSessionRequest.model_validate(
                checkpoint.checkpoint["channel_values"]["request"]
            )
            # Legacy checkpoints may resume their own original input without
            # gaining a fabricated pre-execution receipt. Coverage stays unknown.
            if saved is not None and saved != checkpoint_request:
                raise ResearchTaskError("research_task_request_checkpoint_mismatch")

            # A finished checkpoint may have no pending node, so ainvoke(None)
            # alone cannot guarantee another node's native/permission guard.
            # Validate the original locator before using any cached projection.
            values = checkpoint.checkpoint["channel_values"]
            state = cast(
                AgenticResearchState,
                {
                    key: value
                    for key, value in values.items()
                    if key in AgenticResearchState.__annotations__
                },
            )
            def prepare_resume():
                with self.database.session() as session:
                    prepare_graph_state_in_session(
                        session, state, expected_lease_owner=self.expected_lease_owner
                    )

            await self.owned_work.run_thread(prepare_resume)

            graph = build_agentic_research_graph(
                self.runtime,
                ResearchEvidenceService(self.database),
                checkpointer=saver,
                trace_sink=self.trace_sink,
                progress_reader=self.trace_sink,
                expected_lease_owner=self.expected_lease_owner,
                require_context_selection=require_context_selection,
                heavy_work_admission=self.owned_work,
            )
            return await graph.ainvoke(None, config={"configurable": {"thread_id": run_id}})

    async def has_checkpoint(self, run_id: str) -> bool:
        if self.checkpoint_path is None:
            return False
        async with CheckpointStore(self.checkpoint_path).open() as saver:
            return (await saver.aget_tuple({"configurable": {"thread_id": run_id}})) is not None

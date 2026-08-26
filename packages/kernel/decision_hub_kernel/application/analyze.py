from __future__ import annotations

from packages.contracts_py.decision_hub_contracts.models import (
    ObservationCreate,
    RunStatus,
    TextEnvelope,
)
from packages.kernel.decision_hub_kernel.application.admission import AdmissionService
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.kernel.decision_hub_kernel.ports.runtime import AgentRuntime
from packages.orchestration.langgraph.graphs.decision_graph import build_decision_graph


class AnalyzeTextService:
    def __init__(self, database: Database, runtime: AgentRuntime) -> None:
        self.database = database
        self.admission = AdmissionService(database)
        self.runs = RunService(database)
        self.graph = build_decision_graph(database, runtime)

    async def submit_and_run(self, request: ObservationCreate) -> tuple[str, str, bool]:
        event_id, envelope, admitted = self.admission.admit(request)
        if not admitted:
            existing_run = self.runs.for_event(event_id)
            if existing_run:
                return event_id, existing_run, False
        run_id, _ = self.runs.create(event_id)
        if admitted:
            await self.run_admitted(event_id, run_id, envelope)
        else:
            self.runs.set_status(run_id, RunStatus.degraded, error_code="duplicate_observation")
        return event_id, run_id, admitted

    async def run_admitted(self, event_id: str, run_id: str, envelope: TextEnvelope) -> None:
        await self.graph.ainvoke(
            {
                "run_id": run_id,
                "event_id": event_id,
                "text": envelope.raw_text,
                "envelope": envelope.model_dump(mode="json"),
            },
            config={"configurable": {"thread_id": run_id}},
        )

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from packages.kernel.decision_hub_kernel.application.analyze import AnalyzeTextService
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.kernel.decision_hub_kernel.ports.runtime import AgentRuntime
from packages.orchestration.langgraph.executor import LangGraphDecisionExecutor


def build_analyze_text_service(
    database: Database,
    runtime: AgentRuntime,
    *,
    checkpoint_path: Path | None = None,
    run_timeout_seconds: float = 180,
    strategy_version: str = "baseline.v1",
    admission_clock: Callable[[], datetime] | None = None,
) -> AnalyzeTextService:
    return AnalyzeTextService(
        database,
        LangGraphDecisionExecutor(
            database, runtime, checkpoint_path=checkpoint_path
        ),
        run_timeout_seconds=run_timeout_seconds,
        strategy_version=strategy_version,
        admission_clock=admission_clock,
    )

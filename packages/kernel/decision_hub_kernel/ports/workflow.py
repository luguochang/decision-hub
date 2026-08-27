from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class DecisionExecutionRequest:
    run_id: str
    event_id: str
    deadline_at: datetime


class DecisionWorkflowExecutor(Protocol):
    async def execute(self, request: DecisionExecutionRequest) -> None: ...

    async def resume(self, run_id: str) -> None: ...

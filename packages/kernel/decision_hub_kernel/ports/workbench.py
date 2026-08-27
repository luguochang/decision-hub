from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from packages.contracts_py.decision_hub_contracts.models import (
    CapabilityManifest,
    FeedbackCreate,
    FeedbackView,
    ResearchMemoCreate,
    ResearchMemoView,
    WorkbenchOverviewView,
)


class ResearchWorkbenchPort(Protocol):
    def create_memo(self, request: ResearchMemoCreate) -> ResearchMemoView: ...

    def list_memos(self, limit: int = 100) -> list[ResearchMemoView]: ...

    def create_feedback(self, request: FeedbackCreate) -> FeedbackView: ...

    def list_feedback(self, limit: int = 100) -> list[FeedbackView]: ...

    def overview(self, *, limit: int = 100) -> WorkbenchOverviewView: ...

    def list_capabilities(self) -> list[CapabilityManifest]: ...

    def require_enabled(self, capability_id: str) -> CapabilityManifest: ...


@dataclass(frozen=True)
class CapabilityCall:
    capability_id: str
    input: Mapping[str, object]
    deadline_at: datetime
    request_id: str


@dataclass(frozen=True)
class CapabilityResult:
    capability_id: str
    output: Mapping[str, object]
    started_at: datetime
    finished_at: datetime
    status: str = "succeeded"


CapabilityExecutor = Callable[
    [CapabilityCall], Awaitable[Mapping[str, object]] | Mapping[str, object]
]


class CapabilityPort(Protocol):
    async def execute(self, call: CapabilityCall) -> CapabilityResult: ...

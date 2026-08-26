from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from packages.contracts_py.decision_hub_contracts.models import TextEnvelope


@dataclass(frozen=True)
class AgentRequest:
    role: str
    text: str
    evidence: tuple[str, ...]
    deadline_at: datetime
    max_tokens: int = 4_000


@dataclass(frozen=True)
class AgentResult:
    role: str
    payload: dict[str, object]
    runtime_id: str
    runtime_version: str
    latency_ms: int
    cost_usd: float


class AgentRuntime(Protocol):
    runtime_id: str
    runtime_version: str

    async def execute(self, request: AgentRequest) -> AgentResult: ...


class SourcePlugin(Protocol):
    source_id: str

    async def ingest(self, envelope: TextEnvelope) -> list[TextEnvelope]: ...


class TranscriptSourcePlugin(SourcePlugin, Protocol):
    """Future ASR/Meeting Copilot boundary; Core only consumes TextEnvelope."""

    source_id: str

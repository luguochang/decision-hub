from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from packages.contracts_py.decision_hub_contracts import ErrorProvenance
from packages.contracts_py.decision_hub_contracts.models import TextEnvelope


@dataclass(frozen=True)
class AgentRequest:
    role: str
    text: str
    evidence: tuple[str, ...]
    deadline_at: datetime
    max_tokens: int = 4_000
    instructions: tuple[str, ...] = ()


@dataclass(frozen=True)
class AgentUsage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cost_usd: float | None = None
    cost_status: str = "unknown"
    pricing_version: str | None = None


class AgentExecutionError(RuntimeError):
    def __init__(
        self,
        error_code: str,
        message: str,
        *,
        retryable: bool = False,
        attempt: int = 1,
        provider_id: str | None = None,
        model: str | None = None,
        origin: str = "orchestration",
        cause_code: str | None = None,
        capability_id: str | None = None,
        tool_call_id: str | None = None,
        deadline_ms: int | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.retryable = retryable
        self.attempt = attempt
        self.provider_id = provider_id
        self.model = model
        self.origin = origin
        self.cause_code = cause_code
        self.capability_id = capability_id
        self.tool_call_id = tool_call_id
        self.deadline_ms = deadline_ms

    def provenance(self) -> ErrorProvenance:
        return ErrorProvenance(
            error_code=self.error_code,
            origin=self.origin,  # type: ignore[arg-type]
            cause_code=self.cause_code,
            capability_id=self.capability_id,
            tool_call_id=self.tool_call_id,
            retryable=self.retryable,
            deadline_ms=self.deadline_ms,
        )


@dataclass(frozen=True)
class AgentResult:
    role: str
    payload: dict[str, object]
    runtime_id: str
    runtime_version: str
    latency_ms: int
    cost_usd: float | None = None
    usage: AgentUsage = field(default_factory=AgentUsage)
    provider_id: str | None = None
    model: str | None = None
    api_mode: str | None = None
    schema_version: str = "agent-payload.v1"


class AgentRuntime(Protocol):
    runtime_id: str
    runtime_version: str
    max_attempts: int

    @property
    def cost_budget(self) -> float | None: ...

    async def execute(self, request: AgentRequest) -> AgentResult: ...


class SourcePlugin(Protocol):
    source_id: str

    async def ingest(self, envelope: TextEnvelope) -> list[TextEnvelope]: ...


class TranscriptSourcePlugin(SourcePlugin, Protocol):
    """Future ASR/Meeting Copilot boundary; Core only consumes TextEnvelope."""

    source_id: str

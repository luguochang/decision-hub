from __future__ import annotations

from typing import Protocol

from packages.contracts_py.decision_hub_contracts import (
    ResearchCapabilityQuery,
    ResearchCapabilityResult,
    ResearchSessionRequest,
    ResearchSessionResult,
    ResearchTraceEvent,
)


class ResearchCapabilityAdapter(Protocol):
    capability_id: str
    supported_modes: frozenset[str]

    async def execute(self, query: ResearchCapabilityQuery) -> ResearchCapabilityResult: ...


class ResearchCapabilityGateway(Protocol):
    async def execute(self, query: ResearchCapabilityQuery) -> ResearchCapabilityResult: ...


class ResearchTraceSink(Protocol):
    async def emit(self, event: ResearchTraceEvent) -> None: ...


class ResearchHarnessRuntime(Protocol):
    runtime_id: str
    runtime_version: str
    profile_ref: str

    async def execute(
        self,
        request: ResearchSessionRequest,
        trace_sink: ResearchTraceSink | None = None,
    ) -> ResearchSessionResult: ...

    async def close(self) -> None: ...

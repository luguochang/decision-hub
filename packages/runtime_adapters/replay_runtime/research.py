from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from packages.contracts_py.decision_hub_contracts import (
    ResearchSessionRequest,
    ResearchSessionResult,
)
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    research_evidence_instance_id,
)
from packages.kernel.decision_hub_kernel.ports.research import ResearchTraceSink
from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError


class ReplayResearchFixture(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["research-runtime-replay.v1"]
    runtime_version: str = Field(min_length=1)
    profile_ref: str = Field(min_length=1)
    repeat_last_result: bool = False
    results: list[ResearchSessionResult] = Field(min_length=1)


class ReplayResearchRuntime:
    """Return archived Harness results through the production research contract."""

    runtime_id = "research-replay"

    def __init__(self, fixture: ReplayResearchFixture) -> None:
        self.fixture = fixture
        self.runtime_version = fixture.runtime_version
        self.profile_ref = fixture.profile_ref

    @classmethod
    def from_path(cls, path: Path) -> ReplayResearchRuntime:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            fixture = ReplayResearchFixture.model_validate(payload)
        except (OSError, json.JSONDecodeError, ValidationError) as exc:
            raise ValueError("research_runtime_replay_fixture_invalid") from exc
        return cls(fixture)

    async def execute(
        self,
        request: ResearchSessionRequest,
        trace_sink: ResearchTraceSink | None = None,
    ) -> ResearchSessionResult:
        del trace_sink
        index = request.current_round - 1
        if index < 0:
            raise AgentExecutionError(
                "research_replay_round_missing",
                "the archived Research Harness has no result for this evidence round",
            )
        if index >= len(self.fixture.results) and self.fixture.repeat_last_result:
            index = len(self.fixture.results) - 1
        if index >= len(self.fixture.results):
            raise AgentExecutionError(
                "research_replay_round_missing",
                "the archived Research Harness has no result for this evidence round",
            )
        return _bind_result(
            self.fixture.results[index],
            request,
            runtime_version=self.runtime_version,
            profile_ref=self.profile_ref,
        )

    async def close(self) -> None:
        return None


def _bind_result(
    template: ResearchSessionResult,
    request: ResearchSessionRequest,
    *,
    runtime_version: str,
    profile_ref: str,
) -> ResearchSessionResult:
    session_id = f"replay:{request.request_id}"
    evidence_mapping = {
        item.evidence_id: research_evidence_instance_id(
            content_hash=item.content_hash,
            research_session_id=session_id,
        )
        for item in template.evidence_candidates
    }
    payload = _replace_refs(template.model_dump(mode="python"), evidence_mapping)
    if not isinstance(payload, dict):
        raise AgentExecutionError("research_replay_invalid", "replay payload must be an object")
    payload.update(
        request_id=request.request_id,
        research_session_id=session_id,
        runtime_id=ReplayResearchRuntime.runtime_id,
        runtime_version=runtime_version,
        profile_ref=profile_ref,
        trace_ref=f"replay://{request.request_id}/round/{request.current_round}",
    )
    for candidate in payload.get("evidence_candidates", []):
        if isinstance(candidate, dict):
            candidate["research_session_id"] = session_id
            candidate["round"] = request.current_round
    for round_payload in payload.get("rounds", []):
        if isinstance(round_payload, dict):
            round_payload["round"] = request.current_round
            plan = round_payload.get("plan")
            if isinstance(plan, dict):
                for task in plan.get("tasks", []):
                    if isinstance(task, dict):
                        task["input_evidence_refs"] = list(request.evidence_refs)
    try:
        return ResearchSessionResult.model_validate(payload)
    except ValidationError as exc:
        raise AgentExecutionError(
            "research_replay_invalid",
            "archived Research Harness result cannot be bound to the current request",
        ) from exc


def _replace_refs(value: object, mapping: Mapping[str, str]) -> object:
    if isinstance(value, str):
        return mapping.get(value, value)
    if isinstance(value, Mapping):
        return {str(key): _replace_refs(item, mapping) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_replace_refs(item, mapping) for item in value]
    return value

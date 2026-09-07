from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, cast

import pytest
from pydantic import ValidationError

from packages.contracts_py.decision_hub_contracts import (
    EventWatch,
    EventWindowSample,
    EvidenceCandidate,
    EvidenceRequirement,
    ExecutionBudget,
    ResearchCapabilityResult,
    ResearchInputEvidence,
    ResearchSessionRequest,
    ResearchSessionResult,
    ResearchSynthesisCandidate,
    ResearchTraceEvent,
)
from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError
from packages.runtime_adapters.dsh_runtime import (
    DshResearchRuntime,
    DshRuntimeConfig,
    DshSdkClient,
    DshSdkNotification,
    DshSdkRun,
    check_local_readiness,
    inspect_profile,
)
from packages.runtime_adapters.dsh_runtime.event_mapper import map_notifications
from packages.runtime_adapters.dsh_runtime.profile import (
    DENIED_PLUGIN_FRAGMENTS,
    REQUIRED_PLUGINS,
    UNATTENDED_REASONING_EFFORT,
    build_research_prompt,
    build_trusted_web_research_prompt,
)
from packages.runtime_adapters.dsh_runtime.result_mapper import (
    map_evidence_only_result,
    map_session_result,
)

NOW = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)


def _request(
    *,
    deadline_at: datetime | None = None,
    execution_mode: Literal["live", "replay"] = "replay",
    allowed_capabilities: list[str] | None = None,
) -> ResearchSessionRequest:
    return ResearchSessionRequest(
        schema_version="research-session-request.v1",
        request_id="request-r2-r01",
        run_id="run-r2-r01",
        event_id="event-warsh",
        trigger_snapshot_id="snapshot-trigger",
        domain_pack_ref="crypto_macro.v1",
        role_profile_ref="crypto_macro.manager.v1",
        execution_mode=execution_mode,
        pit_cutoff_at=NOW,
        current_round=1,
        evidence_refs=["evidence-transcript"],
        input_evidence=[
            ResearchInputEvidence(
                evidence_id="evidence-transcript",
                kind="transcript",
                authority="unverified",
                source_id="manual-transcript",
                source_url=None,
                published_at=NOW - timedelta(minutes=1),
                observed_at=NOW - timedelta(seconds=1),
                received_at=NOW,
                content_hash="0" * 64,
                excerpt="A central-bank official discussed inflation risks.",
            )
        ],
        evidence_requirements=[
            EvidenceRequirement(
                requirement_id="event_identity",
                description="Verify the event identity from an authoritative source.",
                importance="hard",
                source_priority=["official"],
                authority_floor="official",
                preferred_capabilities=["replay.research"],
                freshness_seconds=3600,
                minimum_independent_sources=1,
                allowed_fallbacks=["web.search"],
                confidence_cap=0.45,
            )
        ],
        target_gaps=[],
        allowed_capabilities=allowed_capabilities or ["replay.research"],
        execution_budget=ExecutionBudget(
            max_evidence_rounds=3,
            max_tool_calls=12,
            max_subagents=6,
            total_deadline_seconds=180,
            per_tool_timeout_seconds=20,
            per_model_step_timeout_seconds=60,
            max_structured_repairs=1,
            max_estimated_cost_usd=1.0,
        ),
        deadline_at=deadline_at or datetime.now(UTC) + timedelta(minutes=3),
        output_schema_ref="research-session-result.v1",
        repair_instructions=None,
    )


def _result_payload(
    *,
    request_id: str = "request-r2-r01",
) -> dict[str, object]:
    return {
        "schema_version": "research-synthesis-candidate.v1",
        "request_id": request_id,
        "causal_case": None,
        "horizons": [],
    }


def _horizon_payload(evidence_ref: str, *, horizon: str = "30m") -> dict[str, object]:
    return {
        "horizon": horizon,
        "action": "no_trade",
        "subjective_probability": 0.5,
        "probability_status": "uncalibrated",
        "evidence_refs": [evidence_ref],
        "trigger": "Wait for confirmation.",
        "invalidation": "New evidence invalidates the draft.",
        "expires_at": (NOW + timedelta(minutes=30)).isoformat(),
        "next_review_at": (NOW + timedelta(minutes=10)).isoformat(),
        "missing_facts": ["event_identity"],
        "confidence_cap_reason": "Hard evidence remains open.",
    }


def _attested_evidence(*, excerpt: str = "Official policy source.") -> EvidenceCandidate:
    return EvidenceCandidate.model_validate(
        {
            "evidence_id": "ev-attested-1",
            "requirement_id": "event_identity",
            "kind": "official",
            "authority": "official",
            "source_id": "federalreserve.gov",
            "source_url": "https://www.federalreserve.gov/newsevents/speech/example.htm",
            "published_at": (NOW - timedelta(minutes=2)).isoformat(),
            "observed_at": (NOW - timedelta(seconds=2)).isoformat(),
            "received_at": (NOW - timedelta(seconds=1)).isoformat(),
            "content_hash": "1" * 64,
            "excerpt": excerpt,
            "structured_payload_ref": None,
            "tool_call_id": "capability-request-1",
            "research_session_id": "dsh-session-1",
            "round": 1,
            "quality": "candidate",
            "freshness_status": "unknown",
            "conflict_group": None,
        }
    )


def _capability_result(
    evidence: EvidenceCandidate,
    *,
    capability_id: str = "replay.research",
) -> ResearchCapabilityResult:
    return ResearchCapabilityResult(
        schema_version="research-capability-result.v1",
        request_id="request-r2-r01",
        capability_id=capability_id,
        provider="fixture-replay",
        evidence_candidates=[evidence],
        cost_usd=0.0,
        completed_at=NOW,
    )


def _mcp_result_notification(result: ResearchCapabilityResult) -> DshSdkNotification:
    return _notification(
        "tool/result",
        {
            "message": {
                "content": [
                    {
                        "type": "tool-result",
                        "toolCallId": "call-mcp-1",
                        "structuredContent": result.model_dump(mode="json"),
                        "content": [{"type": "text", "text": result.model_dump_json()}],
                    }
                ]
            }
        },
    )


def _notification(
    event_type: str,
    data: Mapping[str, object] | None = None,
    *,
    timestamp: str = "2026-08-29T12:00:00Z",
    session_id: str = "dsh-session-1",
) -> DshSdkNotification:
    return DshSdkNotification(
        method="session.event",
        payload={
            "sessionId": session_id,
            "event": {"type": event_type, "data": dict(data or {}), "timestamp": timestamp},
        },
    )


def _synthesis_capture_notifications(
    payload: Mapping[str, object],
    *,
    call_id: str = "capture-1",
    tool_name: str = "decision_hub_synthesis_submit",
    failed: bool = False,
    session_id: str = "dsh-session-1",
) -> tuple[DshSdkNotification, DshSdkNotification]:
    return (
        _notification(
            "tool/call",
            {
                "message": {
                    "content": [
                        {
                            "type": "tool-call",
                            "toolCallId": call_id,
                            "toolName": tool_name,
                        }
                    ]
                }
            },
            session_id=session_id,
        ),
        _notification(
            "tool/result",
            {
                "message": {
                    "content": [
                        {
                            "type": "tool-result",
                            "toolCallId": call_id,
                            "structuredContent": dict(payload),
                            "content": [],
                            "isError": failed,
                        }
                    ]
                }
            },
            session_id=session_id,
        ),
    )


def _run(
    *,
    session_id: str = "dsh-session-1",
    request_id: str = "request-r2-r01",
    finish_reason: str | None = "completed",
    notifications: tuple[DshSdkNotification, ...] = (),
    final_response: str | None = None,
) -> DshSdkRun:
    payload = _result_payload(request_id=request_id)
    return DshSdkRun(
        session_id=session_id,
        final_response=final_response or json.dumps(payload),
        finish_reason=finish_reason,
        events=(),
        notifications=notifications,
        session_root="/redacted/session/root",
    )


def _traces() -> list[ResearchTraceEvent]:
    return map_notifications(
        [
            _notification("tool/call", {"toolCallId": "call-1", "toolName": "todo_write"}),
            _notification("tool/result", {"toolCallId": "call-1", "toolName": "todo_write"}),
            DshSdkNotification(
                method="subagent.started",
                payload={"childSessionId": "child-1"},
            ),
        ],
        run_id="run-r2-r01",
        research_session_id="dsh-session-1",
        received_at=NOW,
    )


def test_restricted_profile_is_stable_and_denies_host_capabilities() -> None:
    first = inspect_profile()
    second = inspect_profile()

    assert REQUIRED_PLUGINS <= set(first.plugin_names)
    assert first.content_hash == second.content_hash
    assert first.profile_ref == second.profile_ref
    assert len(first.content_hash) == 64
    assert first.reasoning_effort == UNATTENDED_REASONING_EFFORT == "low"
    assert all(
        fragment not in plugin.lower()
        for plugin in first.plugin_names
        for fragment in DENIED_PLUGIN_FRAGMENTS
    )


def test_research_prompt_carries_contract_and_no_provider_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "secret-must-not-enter-prompt")
    prompt = build_research_prompt(_request(), "dsh-session-1")

    assert "research-synthesis-candidate.v1" in prompt
    assert '"request_id":"request-r2-r01"' in prompt
    assert '"title":"ResearchSynthesisCandidate"' in prompt
    assert '"additionalProperties":false' in prompt
    assert "adapter owns Session, Round, Tool, Evidence, Coverage" in prompt
    assert "Never invent or rewrite an evidence id" in prompt
    assert "target_url=null" in prompt
    assert "allowed_domains=[]" in prompt
    assert "<event_id>:<requirement_id>" in prompt
    assert "Do not call todo_write" in prompt
    assert "decision_hub_synthesis_submit" not in prompt
    assert "Return exactly one JSON object" in prompt
    assert "secret-must-not-enter-prompt" not in prompt


def test_live_research_prompt_carries_audited_capability_invocation_playbook() -> None:
    prompt = build_research_prompt(
        _request(
            execution_mode="live",
            allowed_capabilities=[
                "official.macro",
                "market.cross_asset",
                "market.crypto_derivatives",
            ],
        ),
        "dsh-live-session",
    )

    assert "Do not stop merely because the first capability failed" in prompt
    assert "https://www.federalreserve.gov/feeds/speeches.xml" in prompt
    assert 'symbols=["DGS2","DGS10","DTWEXBGS"]' in prompt
    assert 'fields=["spot_price","spot_volume"]' in prompt
    assert '"funding_rate","open_interest","mark_price","index_price","basis"' in prompt
    assert "Event-window eligibility: unavailable" in prompt
    assert "do not submit requested_event_offsets" in prompt.lower()


def test_live_research_prompt_only_exposes_captured_event_window_offsets() -> None:
    request = _request(
        execution_mode="live",
        allowed_capabilities=["market.crypto_derivatives"],
    ).model_copy(
        update={
            "event_watch": EventWatch(
                schema_version="event-watch.v1",
                watch_id="watch-1",
                event_id="event-warsh",
                source_id="fed-calendar",
                event_family="central_bank_speech",
                scheduled_at=NOW,
                status="active",
                window_offsets=["t-5m", "t+1m", "t+30m"],
                baseline_status="ready",
                created_at=NOW - timedelta(hours=1),
                updated_at=NOW,
                next_tick_at=NOW + timedelta(minutes=30),
            ),
            "event_window_samples": [
                EventWindowSample(
                    schema_version="event-window-sample.v1",
                    sample_id="watch-1:t-5m",
                    watch_id="watch-1",
                    event_id="event-warsh",
                    offset="t-5m",
                    target_at=NOW - timedelta(minutes=5),
                    status="captured",
                    observed_at=NOW - timedelta(minutes=5),
                    received_at=NOW - timedelta(minutes=5),
                    provider_id="crypto-window",
                    payload_ref="hub://event-window/watch-1/t-5m",
                    payload_hash="1" * 64,
                    error_code=None,
                ),
                EventWindowSample(
                    schema_version="event-window-sample.v1",
                    sample_id="watch-1:t+1m",
                    watch_id="watch-1",
                    event_id="event-warsh",
                    offset="t+1m",
                    target_at=NOW + timedelta(minutes=1),
                    status="captured",
                    observed_at=NOW + timedelta(minutes=1),
                    received_at=NOW + timedelta(minutes=1),
                    provider_id="crypto-window",
                    payload_ref="hub://event-window/watch-1/t+1m",
                    payload_hash="2" * 64,
                    error_code=None,
                ),
                EventWindowSample(
                    schema_version="event-window-sample.v1",
                    sample_id="watch-1:t+30m",
                    watch_id="watch-1",
                    event_id="event-warsh",
                    offset="t+30m",
                    target_at=NOW + timedelta(minutes=30),
                    status="pending",
                    observed_at=None,
                    received_at=None,
                    provider_id=None,
                    payload_ref=None,
                    payload_hash=None,
                    error_code=None,
                ),
            ],
        }
    )

    prompt = build_research_prompt(request, "dsh-window-session")

    assert 'Event-window eligibility: captured offsets ["t-5m","t+1m"]' in prompt
    assert 'requested_event_offsets=["t-5m","t+1m"]' in prompt
    assert 'requested_event_offsets=["t+30m"]' not in prompt


def test_live_research_prompt_marks_retrospective_watch_without_baseline() -> None:
    request = _request(
        execution_mode="live",
        allowed_capabilities=["market.crypto_derivatives"],
    ).model_copy(
        update={
            "event_watch": EventWatch(
                schema_version="event-watch.v1",
                watch_id="watch-retrospective",
                event_id="event-warsh",
                source_id="manual-text",
                event_family="central_bank_speech",
                scheduled_at=NOW - timedelta(days=1),
                status="retrospective_only",
                window_offsets=["t-5m", "t+1m"],
                baseline_status="unavailable",
                created_at=NOW,
                updated_at=NOW,
                next_tick_at=None,
            ),
            "event_window_samples": [],
        }
    )

    prompt = build_research_prompt(request, "dsh-retrospective-session")

    assert "Event-window eligibility: retrospective_only/baseline_unavailable" in prompt
    assert "do not submit requested_event_offsets" in prompt.lower()


def test_live_web_prompt_requires_active_search_fetch_loop_before_bounded_stop() -> None:
    prompt = build_trusted_web_research_prompt(
        _request(execution_mode="live", allowed_capabilities=["official.macro", "web.fetch"])
    )

    assert "do not stop after the first failed or thin source" in prompt
    assert "official DSH web_search tool" in prompt
    assert "allowed_capabilities list governs only calls through" in prompt
    assert "does not disable the separately exposed official DSH web_search/web_fetch" in prompt
    assert "call the official DSH web_search tool at least once" in prompt
    assert "web.fetch for each relevant locator" in prompt
    assert "Only EvidenceCandidate records returned by decision_hub_research" in prompt
    assert "never fill a gap from model memory or a search snippet" in prompt
    assert "call decision_hub_synthesis_submit exactly once" in prompt
    assert "correct only the candidate and retry inside this same DSH Agent Loop" in prompt
    assert "final prose is ignored by the product runtime" in prompt


def test_web_research_prompt_uses_native_tool_without_model_session_identity() -> None:
    prompt = build_trusted_web_research_prompt(
        _request(
            execution_mode="live",
            allowed_capabilities=["official.macro", "market.cross_asset"],
        )
    )

    assert "decision_hub_research" in prompt
    assert "research_capability_execute" not in prompt
    assert "Runtime session id:" not in prompt
    assert "research_session_id" in prompt
    assert "Never include, infer or copy" in prompt
    assert "dsh-live-session" not in prompt


def test_web_research_prompt_uses_topic_title_and_preserves_input_language() -> None:
    request = _request(execution_mode="live", allowed_capabilities=["official.macro"])
    request.input_evidence[0].excerpt = "研究最新央行讲话对 BTC 的影响。"

    prompt = build_trusted_web_research_prompt(request)

    assert prompt.splitlines()[0] == "研究任务：研究最新央行讲话对 BTC 的影响。"
    assert "人类可读的结论、因果链、触发条件、失效条件和缺失事实必须使用中文" in prompt
    assert "schema 字段名、枚举值、Evidence ID、错误码和来源原文保持原样" in prompt


def test_web_research_prompt_uses_only_first_evidence_line_as_session_title() -> None:
    request = _request(execution_mode="live", allowed_capabilities=["official.macro"])
    request.input_evidence[0].excerpt = (
        "Waller, The Economic Outlook and Some Comments on My Policy Communication\n"
        "Skip to main content\nThe full speech body follows."
    )

    prompt = build_trusted_web_research_prompt(request)

    assert prompt.splitlines()[0] == (
        "Research task: Waller, The Economic Outlook and Some Comments on My Policy Communication"
    )


def test_web_profile_projects_native_search_as_discovery_only_plan_capability() -> None:
    request = _request(execution_mode="live", allowed_capabilities=["market.cross_asset"])
    requirement = request.evidence_requirements[0].model_copy(
        update={"preferred_capabilities": ["web.search"]}
    )
    request = request.model_copy(update={"evidence_requirements": [requirement]})

    result = map_session_result(
        _run(),
        request,
        [],
        runtime_version="dsh-web-test",
        profile_ref="decision-research.web.v1",
        started_at=NOW,
        finished_at=NOW + timedelta(seconds=1),
    )

    assert result.rounds[0].plan.tasks[0].capability_id == "dsh.native.web_search"
    assert result.rounds[0].plan.required_capabilities == ["dsh.native.web_search"]


def test_web_profile_projects_executed_native_search_with_canonical_capability_id() -> None:
    notifications = (
        _notification(
            "tool/call",
            {"toolCallId": "call-search-1", "toolName": "web_search"},
        ),
        _notification(
            "tool/result",
            {"toolCallId": "call-search-1", "toolName": "web_search"},
        ),
    )
    request = _request(execution_mode="live", allowed_capabilities=["market.cross_asset"])
    request = request.model_copy(
        update={
            "evidence_requirements": [
                request.evidence_requirements[0].model_copy(
                    update={"preferred_capabilities": ["web.search"]}
                )
            ]
        }
    )

    result = map_session_result(
        _run(notifications=notifications),
        request,
        map_notifications(
            notifications,
            run_id=request.run_id,
            research_session_id="dsh-session-1",
            received_at=NOW,
        ),
        runtime_version="dsh-web-test",
        profile_ref="decision-research.web.v1",
        started_at=NOW,
        finished_at=NOW + timedelta(seconds=1),
    )

    invocation = result.rounds[0].tool_invocations[0]
    assert invocation.capability_id == "dsh.native.web_search"
    assert invocation.status == "succeeded"


def test_non_web_profile_keeps_unavailable_capability_fail_closed() -> None:
    request = _request(execution_mode="live", allowed_capabilities=["market.cross_asset"])
    requirement = request.evidence_requirements[0].model_copy(
        update={"preferred_capabilities": ["web.search"]}
    )
    request = request.model_copy(update={"evidence_requirements": [requirement]})

    with pytest.raises(AgentExecutionError) as raised:
        map_session_result(
            _run(),
            request,
            [],
            runtime_version="dsh-sdk-test",
            profile_ref="decision-research.v1",
            started_at=NOW,
            finished_at=NOW + timedelta(seconds=1),
        )

    assert raised.value.error_code == "research_capability_unavailable"


def test_profile_rejects_unattended_high_reasoning(tmp_path: Path) -> None:
    profile = inspect_profile().path.read_text(encoding="utf-8")
    path = tmp_path / "high-reasoning.cordis.yml"
    path.write_text(
        profile.replace("reasoningEffort: low", "reasoningEffort: high"),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="dsh_profile_reasoning_effort_invalid"):
        inspect_profile(path)


def test_result_contract_rejects_model_invented_top_level_fields() -> None:
    payload = _result_payload()
    payload["run_id"] = "model-invented-run-id"

    with pytest.raises(ValidationError, match="extra_forbidden"):
        ResearchSynthesisCandidate.model_validate(payload)


def test_runtime_config_is_bounded_strict_and_does_not_store_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "secret-must-remain-in-process-environment"
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    config = DshRuntimeConfig.from_env()

    assert secret not in repr(config)
    assert secret not in config.model_dump_json()
    assert config.research_mcp_url is None
    with pytest.raises(ValidationError, match="extra_forbidden"):
        DshRuntimeConfig.model_validate({"unknown": True})
    with pytest.raises(ValidationError, match="greater_than_equal"):
        DshRuntimeConfig(max_tokens=255)
    with pytest.raises(ValidationError, match="greater_than"):
        DshRuntimeConfig(request_timeout_seconds=0)


def test_runtime_config_does_not_cross_route_openai_endpoint_into_deepseek(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DECISION_HUB_DSH_BASE_URL", raising=False)
    monkeypatch.delenv("DEEPSEEK_BASE_URL", raising=False)
    monkeypatch.delenv("SUB2API_BASE_URL", raising=False)
    monkeypatch.setenv("OPENAI_BASE_URL", "https://codexai.club/v1")

    config = DshRuntimeConfig.from_env()

    assert config.provider == "deepseek-official"
    assert config.base_url is None


def test_runtime_config_uses_openai_endpoint_only_for_non_deepseek_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DECISION_HUB_DSH_PROVIDER", "codexai-gpt55")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://codexai.club/v1")

    config = DshRuntimeConfig.from_env()

    assert str(config.base_url) == "https://codexai.club/v1"


@dataclass
class _RawNotification:
    method: str
    payload: Mapping[str, object]


@dataclass
class _RawRun:
    session_id: str
    final_response: str
    finish_reason: str | None
    events: list[Mapping[str, object]]
    notifications: list[_RawNotification]
    session_root: str | None = None


class _FakeHandle:
    def __init__(self, raw_run: object) -> None:
        self.raw_run = raw_run
        self.started = False
        self.closed = False
        self.prompts: list[str] = []

    def start(self) -> None:
        self.started = True

    def close(self) -> None:
        self.closed = True

    def run(
        self,
        input: str,
        *,
        session_id: str,
        on_notification: Callable[[object], None] | None = None,
    ) -> object:
        del session_id, on_notification
        self.prompts.append(input)
        return self.raw_run


@pytest.mark.asyncio
async def test_sdk_client_owns_handle_lifecycle_and_normalizes_protocol() -> None:
    raw = _RawRun(
        session_id="dsh-session-1",
        final_response="{}",
        finish_reason="completed",
        events=[{"type": "turn/end"}],
        notifications=[_RawNotification("session.status", {"status": "idle"})],
    )
    handle = _FakeHandle(raw)
    client = DshSdkClient(DshRuntimeConfig(), factory=lambda _: handle)

    await client.start()
    result = await client.run("bounded prompt", session_id="dsh-session-1")
    await client.close()

    assert handle.started is True
    assert handle.closed is True
    assert handle.prompts == ["bounded prompt"]
    assert result.session_id == "dsh-session-1"
    assert result.notifications[0].method == "session.status"


@pytest.mark.asyncio
async def test_sdk_client_rejects_missing_or_changed_session_identity() -> None:
    missing = _RawRun(
        session_id=cast(str, None),
        final_response="{}",
        finish_reason="completed",
        events=[],
        notifications=[],
    )
    missing_client = DshSdkClient(DshRuntimeConfig(), factory=lambda _: _FakeHandle(missing))
    with pytest.raises(AgentExecutionError, match="missing identity") as missing_error:
        await missing_client.run("prompt", session_id="dsh-session-1")
    assert missing_error.value.error_code == "dsh_protocol_invalid"

    changed = _RawRun(
        session_id="different-session",
        final_response="{}",
        finish_reason="completed",
        events=[],
        notifications=[],
    )
    changed_client = DshSdkClient(DshRuntimeConfig(), factory=lambda _: _FakeHandle(changed))
    with pytest.raises(AgentExecutionError, match="different session") as changed_error:
        await changed_client.run("prompt", session_id="dsh-session-1")
    assert changed_error.value.error_code == "dsh_protocol_invalid"


def test_event_mapper_normalizes_ordered_trace_without_raw_payload() -> None:
    notifications = [
        _notification("agent/inbox/spliced"),
        _notification("step/start", {"id": "step-1"}),
        _notification("tool/call", {"toolCallId": "call-1", "toolName": "todo_write"}),
        _notification("tool/result", {"toolCallId": "call-1", "toolName": "todo_write"}),
        _notification(
            "tool/result",
            {"toolCallId": "call-2", "toolName": "restricted_tool", "isError": True},
        ),
        DshSdkNotification(
            method="subagent.started",
            payload={"childSessionId": "child-1", "private": "raw-secret"},
        ),
        DshSdkNotification(
            method="subagent.finished",
            payload={"childSessionId": "child-1", "status": "completed"},
        ),
        _notification("step/end", {"reason": {"kind": "tool-calls"}}),
        _notification("turn/end", {"reason": {"kind": "completed"}}),
        DshSdkNotification(
            method="session.status",
            payload={"sessionId": "dsh-session-1", "status": "idle"},
        ),
    ]

    traces = map_notifications(
        notifications,
        run_id="run-r2-r01",
        research_session_id="dsh-session-1",
        received_at=NOW,
    )

    assert [trace.sequence_no for trace in traces] == list(range(len(traces)))
    assert [trace.event_type for trace in traces] == [
        "session_started",
        "model_step_started",
        "tool_started",
        "tool_completed",
        "tool_failed",
        "subagent_started",
        "subagent_completed",
        "model_step_completed",
        "round_completed",
        "session_stopped",
    ]
    assert traces[4].error_code == "dsh_tool_failed"
    assert "raw-secret" not in json.dumps(
        [trace.model_dump(mode="json") for trace in traces], sort_keys=True
    )


def test_event_mapper_supports_official_nested_tool_result_shape() -> None:
    notifications = [
        _notification(
            "tool/call",
            {
                "callId": "call-mcp-1",
                "name": "mcp__decision_research__research_capability_execute",
                "arguments": "{}",
            },
        ),
        _notification(
            "tool/result",
            {
                "message": {
                    "content": [
                        {
                            "type": "tool-result",
                            "toolCallId": "call-mcp-1",
                            "content": [{"type": "text", "text": "ok"}],
                        }
                    ]
                }
            },
        ),
    ]

    traces = map_notifications(
        notifications,
        run_id="run-r2-r02",
        research_session_id="dsh-session-1",
        received_at=NOW,
    )

    assert [item.reference_id for item in traces] == ["call-mcp-1", "call-mcp-1"]
    assert traces[1].event_type == "tool_completed"
    assert "research_capability_execute" in traces[1].summary


def test_event_mapper_reads_official_nested_tool_error() -> None:
    traces = map_notifications(
        [
            _notification(
                "tool/result",
                {
                    "message": {
                        "content": [
                            {
                                "type": "tool-result",
                                "toolCallId": "call-mcp-error",
                                "content": [
                                    {
                                        "type": "text",
                                        "text": (
                                            "Error: Error executing tool "
                                            "research_capability_execute: "
                                            "research_replay_fixture_missing "
                                            'provenance={"capability_id":'
                                            '"replay.research","cause_code":null,'
                                            '"deadline_ms":5000,"error_code":'
                                            '"research_replay_fixture_missing",'
                                            '"origin":"gateway","retryable":false,'
                                            '"tool_call_id":"research-request:run-1"}'
                                        ),
                                    }
                                ],
                                "isError": True,
                            }
                        ]
                    },
                },
            )
        ],
        run_id="run-r2-r02",
        research_session_id="dsh-session-1",
        received_at=NOW,
    )

    assert traces[0].reference_id == "call-mcp-error"
    assert traces[0].event_type == "tool_failed"
    assert traces[0].error_code == "research_replay_fixture_missing"
    assert traces[0].error is not None
    assert traces[0].error.origin == "gateway"
    assert traces[0].error.capability_id == "replay.research"
    assert traces[0].error.tool_call_id == "call-mcp-error"
    assert traces[0].error.retryable is False
    assert traces[0].error.deadline_ms == 5000


def test_result_mapper_builds_runtime_ledger_from_trusted_trace() -> None:
    traces = _traces()
    result = map_session_result(
        _run(notifications=()),
        _request(),
        traces,
        runtime_version="dsh-sdk-0.1.1rc1",
        profile_ref="decision-research.v1:hash",
        started_at=NOW,
        finished_at=NOW + timedelta(seconds=2),
    )

    assert isinstance(result, ResearchSessionResult)
    assert result.runtime_id == "dsh"
    assert result.runtime_version == "dsh-sdk-0.1.1rc1"
    assert result.profile_ref == "decision-research.v1:hash"
    assert result.total_tool_calls == 1
    assert result.total_subagents == 1
    assert result.total_tokens is None
    assert result.estimated_cost_usd is None
    assert result.trace_ref.startswith("dsh://session/dsh-session-1/trace/")
    assert len(result.trace_hash) == 64


def test_result_mapper_prefers_attested_synthesis_tool_over_final_prose() -> None:
    notifications = _synthesis_capture_notifications(_result_payload())

    result = map_session_result(
        _run(
            notifications=notifications,
            final_response="Research complete. The validated result was submitted through Tool.",
        ),
        _request(),
        map_notifications(
            notifications,
            run_id="run-r2-r01",
            research_session_id="dsh-session-1",
            received_at=NOW,
        ),
        runtime_version="dsh-web-test",
        profile_ref="decision-research.web.v1",
        started_at=NOW,
        finished_at=NOW + timedelta(seconds=2),
    )

    assert result.causal_case is None
    assert result.horizons == []
    assert result.synthesis_failure_code is None


def test_attested_synthesis_tool_cannot_bypass_evidence_whitelist() -> None:
    payload = _result_payload()
    payload["horizons"] = [_horizon_payload("ev-not-attested")]
    notifications = _synthesis_capture_notifications(payload)

    with pytest.raises(AgentExecutionError) as raised:
        map_session_result(
            _run(notifications=notifications, final_response="Submitted."),
            _request(),
            map_notifications(
                notifications,
                run_id="run-r2-r01",
                research_session_id="dsh-session-1",
                received_at=NOW,
            ),
            runtime_version="dsh-web-test",
            profile_ref="decision-research.web.v1",
            started_at=NOW,
            finished_at=NOW + timedelta(seconds=2),
        )

    assert raised.value.error_code == "dsh_evidence_unattested"
    assert raised.value.cause_code == "synthesis_attestation"


@pytest.mark.parametrize(
    ("notifications", "reason"),
    [
        (
            _synthesis_capture_notifications(_result_payload(), failed=True),
            "failed result",
        ),
        (
            _synthesis_capture_notifications(
                {
                    "wrapper": {
                        "name": "decision_hub_synthesis_submit",
                        "candidate": _result_payload(),
                    }
                },
                tool_name="todo_write",
            ),
            "payload name spoof",
        ),
        (
            _synthesis_capture_notifications(
                _result_payload(),
                session_id="another-dsh-session",
            ),
            "cross-session result",
        ),
    ],
)
def test_result_mapper_rejects_unattested_synthesis_capture(
    notifications: tuple[DshSdkNotification, DshSdkNotification],
    reason: str,
) -> None:
    with pytest.raises(AgentExecutionError) as raised:
        map_session_result(
            _run(notifications=notifications, final_response=f"Ignored {reason}."),
            _request(),
            map_notifications(
                notifications,
                run_id="run-r2-r01",
                research_session_id="dsh-session-1",
                received_at=NOW,
            ),
            runtime_version="dsh-web-test",
            profile_ref="decision-research.web.v1",
            started_at=NOW,
            finished_at=NOW + timedelta(seconds=2),
        )

    assert raised.value.error_code == "structured_output_invalid"


def test_result_mapper_uses_last_successful_synthesis_capture() -> None:
    first = _synthesis_capture_notifications(
        _result_payload(request_id="stale-request"),
        call_id="capture-1",
    )
    second = _synthesis_capture_notifications(_result_payload(), call_id="capture-2")
    notifications = (*first, *second)

    result = map_session_result(
        _run(notifications=notifications, final_response="Submitted."),
        _request(),
        map_notifications(
            notifications,
            run_id="run-r2-r01",
            research_session_id="dsh-session-1",
            received_at=NOW,
        ),
        runtime_version="dsh-web-test",
        profile_ref="decision-research.web.v1",
        started_at=NOW,
        finished_at=NOW + timedelta(seconds=2),
    )

    assert result.request_id == "request-r2-r01"


def test_result_mapper_accepts_only_exact_mcp_tool_result_evidence() -> None:
    evidence = _attested_evidence()
    capability_result = _capability_result(evidence)
    notifications = (
        _notification(
            "tool/call",
            {
                "toolCallId": "call-mcp-1",
                "toolName": "mcp__decision_research__research_capability_execute",
            },
        ),
        _mcp_result_notification(capability_result),
    )
    run = _run(notifications=notifications)

    result = map_session_result(
        run,
        _request(),
        map_notifications(
            notifications,
            run_id="run-r2-r01",
            research_session_id="dsh-session-1",
            received_at=NOW,
        ),
        runtime_version="dsh-sdk-0.1.1rc1",
        profile_ref="decision-research.v1:hash",
        started_at=NOW,
        finished_at=NOW + timedelta(seconds=2),
    )

    assert result.evidence_candidates == [evidence]
    assert len(result.rounds[0].tool_invocations) == 1
    invocation = result.rounds[0].tool_invocations[0]
    assert invocation.tool_call_id == "call-mcp-1"
    assert invocation.capability_id == "replay.research"
    assert invocation.tool_name.endswith("research_capability_execute")
    assert invocation.status == "succeeded"
    assert len(result.rounds[0].tool_results) == 1
    summary = result.rounds[0].tool_results[0]
    assert summary.tool_call_id == "call-mcp-1"
    assert summary.status == "succeeded"
    assert summary.evidence_refs == [evidence.evidence_id]
    assert summary.content_hash is not None
    assert summary.content_ref is None


def test_result_mapper_accepts_call_scoped_request_id_for_same_product_request() -> None:
    """DSH may suffix the product request id for an individual tool call."""
    evidence = _attested_evidence()
    capability_result = _capability_result(evidence).model_copy(
        update={"request_id": "request-r2-r01:1"}
    )
    notifications = (
        _notification(
            "tool/call",
            {
                "toolCallId": "call-mcp-1",
                "toolName": "mcp__decision_research__research_capability_execute",
            },
        ),
        _mcp_result_notification(capability_result),
    )

    result = map_session_result(
        _run(notifications=notifications),
        _request(),
        map_notifications(
            notifications,
            run_id="run-r2-r01",
            research_session_id="dsh-session-1",
            received_at=NOW,
        ),
        runtime_version="dsh-sdk-0.1.1rc1",
        profile_ref="decision-research.v1:hash",
        started_at=NOW,
        finished_at=NOW + timedelta(seconds=2),
    )

    assert result.evidence_candidates == [evidence]
    assert result.rounds[0].tool_results[0].evidence_refs == [evidence.evidence_id]


def test_result_mapper_projects_failed_tool_without_raw_payload() -> None:
    notifications = (
        _notification(
            "tool/call",
            {
                "callId": "call-mcp-error",
                "name": "mcp__decision_research__research_capability_execute",
                "arguments": "provider-secret-must-not-be-projected",
            },
        ),
        _notification(
            "tool/result",
            {
                "message": {
                    "content": [
                        {
                            "type": "tool-result",
                            "toolCallId": "call-mcp-error",
                            "content": [],
                            "isError": True,
                        }
                    ]
                }
            },
        ),
    )
    run = _run(notifications=notifications)

    result = map_session_result(
        run,
        _request(),
        map_notifications(
            notifications,
            run_id="run-r2-r01",
            research_session_id="dsh-session-1",
            received_at=NOW,
        ),
        runtime_version="dsh-sdk-0.1.1rc1",
        profile_ref="decision-research.v1:hash",
        started_at=NOW,
        finished_at=NOW + timedelta(seconds=2),
    )

    assert result.rounds[0].tool_invocations[0].status == "failed"
    assert result.rounds[0].tool_results[0].status == "failed"
    assert result.rounds[0].tool_results[0].error_code == "dsh_tool_failed"
    assert "provider-secret-must-not-be-projected" not in result.model_dump_json()


def test_result_mapper_retains_attested_success_when_another_tool_fails_and_session_stops() -> None:
    evidence = _attested_evidence()
    capability_result = _capability_result(evidence)
    notifications = (
        _notification(
            "tool/call",
            {
                "toolCallId": "call-mcp-1",
                "toolName": "mcp__decision_research__research_capability_execute",
            },
        ),
        _mcp_result_notification(capability_result),
        _notification(
            "tool/call",
            {
                "toolCallId": "call-failed",
                "toolName": "mcp__decision_research__research_capability_execute",
            },
        ),
        _notification(
            "tool/result",
            {
                "toolCallId": "call-failed",
                "toolName": "mcp__decision_research__research_capability_execute",
                "isError": True,
                "error": {
                    "error_code": "research_capability_timeout",
                    "origin": "transport",
                    "cause_code": "deadline",
                    "capability_id": "replay.research",
                    "retryable": True,
                    "deadline_ms": 20000,
                },
            },
        ),
    )
    run = _run(finish_reason="max-tokens", notifications=notifications)

    result = map_session_result(
        run,
        _request(),
        map_notifications(
            notifications,
            run_id="run-r2-r01",
            research_session_id="dsh-session-1",
            received_at=NOW,
        ),
        runtime_version="dsh-sdk-0.1.1rc1",
        profile_ref="decision-research.v1:hash",
        started_at=NOW,
        finished_at=NOW + timedelta(seconds=2),
    )

    assert result.status == "degraded"
    assert result.evidence_candidates == [evidence]
    failures = [item for item in result.rounds[0].tool_results if item.status == "failed"]
    assert len(failures) == 1
    assert failures[0].error is not None
    assert failures[0].error.error_code == "research_capability_timeout"
    assert failures[0].error.origin == "transport"


def test_native_tool_failure_keeps_declared_capability_in_multi_capability_run() -> None:
    request = _request(
        execution_mode="live",
        allowed_capabilities=["official.macro", "market.cross_asset"],
    )
    request = request.model_copy(
        update={
            "evidence_requirements": [
                item.model_copy(
                    update={"preferred_capabilities": ["market.cross_asset"]}
                )
                for item in request.evidence_requirements
            ]
        }
    )
    notifications = (
        _notification(
            "tool/call",
            {"toolCallId": "call-native", "toolName": "decision_hub_research"},
        ),
        _notification(
            "tool/result",
            {
                "toolCallId": "call-native",
                "toolName": "decision_hub_research",
                "isError": True,
                "error": {
                    "error_code": "research_capability_timeout",
                    "origin": "transport",
                    "cause_code": "deadline",
                    "capability_id": "market.cross_asset",
                    "retryable": True,
                    "deadline_ms": 20000,
                },
            },
        ),
    )
    run = _run(finish_reason="completed", notifications=notifications)

    result = map_session_result(
        run,
        request,
        map_notifications(
            notifications,
            run_id=request.run_id,
            research_session_id=run.session_id,
            received_at=NOW,
        ),
        runtime_version="dsh-web-test",
        profile_ref="decision-research.web.v1",
        started_at=NOW,
        finished_at=NOW + timedelta(seconds=2),
    )

    invocation = result.rounds[0].tool_invocations[0]
    assert invocation.capability_id == "market.cross_asset"
    assert not invocation.capability_id.startswith("dsh.internal")


@pytest.mark.parametrize("failure", ["missing", "unauthorized", "wrong_lineage"])
def test_result_mapper_rejects_unattested_synthesis_references(failure: str) -> None:
    tool_evidence = _attested_evidence()
    capability_result = _capability_result(
        tool_evidence,
        capability_id="web.search" if failure == "unauthorized" else "replay.research",
    )
    if failure == "wrong_lineage":
        capability_result = capability_result.model_copy(update={"request_id": "other-request"})
    notifications = (
        _notification(
            "tool/call",
            {
                "toolCallId": "call-mcp-1",
                "toolName": "mcp__decision_research__research_capability_execute",
            },
        ),
        _mcp_result_notification(capability_result),
    )
    payload = _result_payload()
    payload["horizons"] = [
        {
            "horizon": "30m",
            "action": "no_trade",
            "subjective_probability": 0.5,
            "probability_status": "uncalibrated",
            "evidence_refs": [tool_evidence.evidence_id],
            "trigger": "Wait for confirmation.",
            "invalidation": "New evidence invalidates the draft.",
            "expires_at": (NOW + timedelta(minutes=30)).isoformat(),
            "next_review_at": (NOW + timedelta(minutes=10)).isoformat(),
            "missing_facts": ["event_identity"],
            "confidence_cap_reason": "Hard evidence remains open.",
        }
    ]
    run = _run(
        notifications=() if failure == "missing" else notifications,
        final_response=json.dumps(payload),
    )

    with pytest.raises(AgentExecutionError) as raised:
        map_session_result(
            run,
            _request(),
            map_notifications(
                run.notifications,
                run_id="run-r2-r01",
                research_session_id="dsh-session-1",
                received_at=NOW,
            ),
            runtime_version="dsh-sdk-0.1.1rc1",
            profile_ref="decision-research.v1:hash",
            started_at=NOW,
            finished_at=NOW + timedelta(seconds=2),
        )

    assert raised.value.error_code == "dsh_evidence_unattested"
    assert raised.value.cause_code == "synthesis_attestation"
    assert raised.value.origin == "orchestration"


def test_evidence_only_mapper_discards_unattested_synthesis_but_keeps_facts() -> None:
    evidence = _attested_evidence()
    notifications = (
        _notification(
            "tool/call",
            {
                "toolCallId": "call-mcp-1",
                "toolName": "mcp__decision_research__research_capability_execute",
            },
        ),
        _mcp_result_notification(_capability_result(evidence)),
    )
    result = map_evidence_only_result(
        _run(final_response="not-json", notifications=notifications),
        _request(),
        map_notifications(
            notifications,
            run_id="run-r2-r01",
            research_session_id="dsh-session-1",
            received_at=NOW,
        ),
        runtime_version="dsh-web-test",
        profile_ref="decision-research.web.v1",
        started_at=NOW,
        finished_at=NOW + timedelta(seconds=2),
        failure_code="structured_output_invalid",
        failure_cause="synthesis_invalid",
    )

    assert result.status == "degraded"
    assert result.synthesis_failure_code == "structured_output_invalid"
    assert result.causal_case is None
    assert result.horizons == []
    assert result.evidence_candidates == [evidence]
    assert result.stop_reason.code == "critical_data_unavailable"
    assert "trusted evidence item(s) were retained" in result.stop_reason.detail


def test_result_mapper_accepts_tool_call_scoped_capability_request_id() -> None:
    """Live DSH calls use the unique capability call ID as result.request_id."""
    evidence = _attested_evidence()
    capability = _capability_result(evidence).model_copy(
        update={"request_id": "call-mcp-1"}
    )
    notifications = (
        _notification(
            "tool/call",
            {
                "toolCallId": "call-mcp-1",
                "toolName": "mcp__decision_research__research_capability_execute",
            },
        ),
        _mcp_result_notification(capability),
    )
    payload = _result_payload()
    payload["horizons"] = [_horizon_payload(evidence.evidence_id)]

    result = map_session_result(
        _run(final_response=json.dumps(payload), notifications=notifications),
        _request(),
        map_notifications(
            notifications,
            run_id="run-r2-r01",
            research_session_id="dsh-session-1",
            received_at=NOW,
        ),
        runtime_version="dsh-web-test",
        profile_ref="decision-research.web.v1",
        started_at=NOW,
        finished_at=NOW + timedelta(seconds=2),
    )

    assert result.evidence_candidates == [evidence]
    assert result.rounds[0].tool_results[0].evidence_refs == [evidence.evidence_id]


def test_result_mapper_accepts_declared_tool_request_id() -> None:
    """DSH may use the request_id declared in tool arguments (dhreq_*)."""
    evidence = _attested_evidence()
    capability = _capability_result(evidence).model_copy(
        update={"request_id": "dhreq_event_identity_001"}
    )
    notifications = (
        _notification(
            "tool/call",
            {
                "callId": "call-mcp-1",
                "name": "decision_hub_research",
                "arguments": json.dumps(
                    {
                        "request_id": "dhreq_event_identity_001",
                        "capability_id": "replay.research",
                    }
                ),
            },
        ),
        _mcp_result_notification(capability),
    )
    payload = _result_payload()
    payload["horizons"] = [_horizon_payload(evidence.evidence_id)]

    result = map_session_result(
        _run(final_response=json.dumps(payload), notifications=notifications),
        _request(),
        map_notifications(
            notifications,
            run_id="run-r2-r01",
            research_session_id="dsh-session-1",
            received_at=NOW,
        ),
        runtime_version="dsh-web-test",
        profile_ref="decision-research.web.v1",
        started_at=NOW,
        finished_at=NOW + timedelta(seconds=2),
    )

    assert result.evidence_candidates == [evidence]


@pytest.mark.parametrize(
    ("run", "error_code"),
    [
        (_run(request_id="other-request"), "dsh_protocol_invalid"),
        (
            _run(final_response=json.dumps(_result_payload() | {"runtime_id": "model-invented"})),
            "structured_output_invalid",
        ),
        (_run(final_response="not-json"), "structured_output_invalid"),
        (_run(finish_reason="max-tokens"), "dsh_session_incomplete"),
        (_run(finish_reason=None), "dsh_session_incomplete"),
    ],
)
def test_result_mapper_fails_closed(run: DshSdkRun, error_code: str) -> None:
    with pytest.raises(AgentExecutionError) as raised:
        map_session_result(
            run,
            _request(),
            _traces(),
            runtime_version="dsh-sdk-0.1.1rc1",
            profile_ref="decision-research.v1:hash",
            started_at=NOW,
            finished_at=NOW + timedelta(seconds=2),
        )
    assert raised.value.error_code == error_code


class _StubClient(DshSdkClient):
    def __init__(
        self,
        *,
        request_id: str,
        delay_seconds: float = 0,
        error: BaseException | None = None,
    ) -> None:
        self.request_id = request_id
        self.delay_seconds = delay_seconds
        self.error = error
        self.closed = False
        self.called = False

    async def run(self, prompt: str, *, session_id: str) -> DshSdkRun:
        self.called = True
        assert self.request_id in prompt
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        if self.error is not None:
            raise self.error
        notifications = (
            _notification("tool/call", {"toolCallId": "call-1", "toolName": "todo_write"}),
            _notification("tool/result", {"toolCallId": "call-1", "toolName": "todo_write"}),
        )
        return _run(session_id=session_id, request_id=self.request_id, notifications=notifications)

    async def close(self) -> None:
        self.closed = True


class _RepairStubClient(DshSdkClient):
    def __init__(self, runs: list[DshSdkRun]) -> None:
        self.runs = list(runs)
        self.calls: list[tuple[str, str]] = []
        self.closed = False

    async def run(self, prompt: str, *, session_id: str) -> DshSdkRun:
        self.calls.append((prompt, session_id))
        if not self.runs:
            raise AssertionError("unexpected additional DSH repair attempt")
        return self.runs.pop(0)

    async def close(self) -> None:
        self.closed = True


class _TraceSink:
    def __init__(self) -> None:
        self.events: list[ResearchTraceEvent] = []

    async def emit(self, event: ResearchTraceEvent) -> None:
        self.events.append(event)


@pytest.mark.asyncio
async def test_runtime_executes_bounded_session_and_emits_normalized_trace(tmp_path: Path) -> None:
    request = _request()
    client = _StubClient(request_id=request.request_id)
    runtime = DshResearchRuntime(
        DshRuntimeConfig(workspace=tmp_path, session_root=tmp_path / "sessions"),
        client=client,
    )
    sink = _TraceSink()

    result = await runtime.execute(request, sink)

    assert result.runtime_id == "dsh"
    assert result.research_session_id.startswith("decision-research-")
    assert [event.event_type for event in sink.events] == ["tool_started", "tool_completed"]
    assert result.total_tool_calls == 1


@pytest.mark.asyncio
async def test_runtime_repairs_structured_output_once_in_same_session(tmp_path: Path) -> None:
    request = _request()
    invalid = _run(final_response=json.dumps(_result_payload() | {"extra": True}))
    repaired = _run(final_response=json.dumps(_result_payload()))
    client = _RepairStubClient([invalid, repaired])
    runtime = DshResearchRuntime(
        DshRuntimeConfig(workspace=tmp_path, session_root=tmp_path / "sessions"),
        client=client,
    )

    result = await runtime.execute(request)

    assert result.request_id == request.request_id
    assert len(client.calls) == 2
    assert client.calls[0][1] == client.calls[1][1]
    assert "Do not call any tool" in client.calls[1][0]
    assert "Return exactly one JSON object" in client.calls[1][0]
    assert "extra_forbidden" in client.calls[1][0]


@pytest.mark.asyncio
async def test_runtime_repairs_unattested_synthesis_reference_from_exact_whitelist(
    tmp_path: Path,
) -> None:
    request = _request()
    evidence = _attested_evidence()
    capability_result = _capability_result(evidence)
    notifications = (
        _notification(
            "tool/call",
            {
                "toolCallId": "call-mcp-1",
                "toolName": "mcp__decision_research__research_capability_execute",
            },
        ),
        _mcp_result_notification(capability_result),
    )
    invalid_payload = _result_payload()
    invalid_payload["horizons"] = [_horizon_payload("ev-model-rewrote-this-id")]
    repaired_payload = _result_payload()
    repaired_payload["horizons"] = [_horizon_payload(evidence.evidence_id)]
    client = _RepairStubClient(
        [
            _run(
                final_response=json.dumps(invalid_payload),
                notifications=notifications,
            ),
            _run(
                final_response=json.dumps(repaired_payload),
                notifications=notifications,
            ),
        ]
    )
    runtime = DshResearchRuntime(
        DshRuntimeConfig(workspace=tmp_path, session_root=tmp_path / "sessions"),
        client=client,
    )

    result = await runtime.execute(request)

    assert result.horizons[0].evidence_refs == [evidence.evidence_id]
    assert len(client.calls) == 2
    assert "Allowed Evidence ID whitelist" in client.calls[1][0]
    assert evidence.evidence_id in client.calls[1][0]
    assert "ev-model-rewrote-this-id" in client.calls[1][0]
    assert '"horizons","0","evidence_refs","0"' in client.calls[1][0]
    assert "Do not call any tool" in client.calls[1][0]


@pytest.mark.asyncio
async def test_runtime_fails_closed_when_evidence_ref_repair_remains_unattested(
    tmp_path: Path,
) -> None:
    request = _request()
    evidence = _attested_evidence()
    notifications = (
        _notification(
            "tool/call",
            {
                "toolCallId": "call-mcp-1",
                "toolName": "mcp__decision_research__research_capability_execute",
            },
        ),
        _mcp_result_notification(_capability_result(evidence)),
    )
    invalid_payload = _result_payload()
    invalid_payload["horizons"] = [_horizon_payload("ev-still-not-attested")]
    invalid = _run(
        final_response=json.dumps(invalid_payload),
        notifications=notifications,
    )
    client = _RepairStubClient([invalid, invalid])
    runtime = DshResearchRuntime(
        DshRuntimeConfig(workspace=tmp_path, session_root=tmp_path / "sessions"),
        client=client,
    )

    with pytest.raises(AgentExecutionError) as raised:
        await runtime.execute(request)

    assert raised.value.error_code == "dsh_evidence_unattested"
    assert raised.value.cause_code == "synthesis_attestation"
    assert raised.value.attempt == 2
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_runtime_repair_preserves_initial_attested_evidence_and_tool_trace(
    tmp_path: Path,
) -> None:
    request = _request()
    evidence = _attested_evidence()
    capability_result = _capability_result(evidence)
    initial_notifications = (
        _notification(
            "tool/call",
            {
                "toolCallId": "call-mcp-1",
                "toolName": "mcp__decision_research__research_capability_execute",
            },
        ),
        _mcp_result_notification(capability_result),
    )
    invalid_payload = _result_payload()
    invalid_payload["horizons"] = []
    invalid_payload["extra"] = True
    repaired_payload = _result_payload()
    repaired_payload["causal_case"] = None
    repaired = _run(
        final_response=json.dumps(repaired_payload),
        notifications=initial_notifications,
    )
    client = _RepairStubClient(
        [
            _run(
                final_response=json.dumps(invalid_payload),
                notifications=initial_notifications,
            ),
            repaired,
        ]
    )
    runtime = DshResearchRuntime(
        DshRuntimeConfig(workspace=tmp_path, session_root=tmp_path / "sessions"),
        client=client,
    )

    result = await runtime.execute(request)

    assert result.evidence_candidates == [evidence]
    assert result.total_tool_calls == 1
    assert [item.tool_call_id for item in result.rounds[0].tool_invocations] == [
        "call-mcp-1"
    ]
    assert result.rounds[0].tool_results[0].evidence_refs == [evidence.evidence_id]
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_runtime_fails_closed_after_one_structured_repair(tmp_path: Path) -> None:
    request = _request()
    invalid = _run(final_response="not-json")
    client = _RepairStubClient([invalid, invalid])
    runtime = DshResearchRuntime(
        DshRuntimeConfig(workspace=tmp_path, session_root=tmp_path / "sessions"),
        client=client,
    )

    with pytest.raises(AgentExecutionError) as raised:
        await runtime.execute(request)

    assert raised.value.error_code == "structured_output_invalid"
    assert raised.value.attempt == 2
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_runtime_does_not_repair_when_budget_disables_it(tmp_path: Path) -> None:
    request = _request().model_copy(
        update={
            "execution_budget": _request().execution_budget.model_copy(
                update={"max_structured_repairs": 0}
            )
        }
    )
    client = _RepairStubClient([_run(final_response="not-json")])
    runtime = DshResearchRuntime(
        DshRuntimeConfig(workspace=tmp_path, session_root=tmp_path / "sessions"),
        client=client,
    )

    with pytest.raises(AgentExecutionError) as raised:
        await runtime.execute(request)

    assert raised.value.error_code == "structured_output_invalid"
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_runtime_rejects_tool_use_during_structured_repair(tmp_path: Path) -> None:
    request = _request()
    repair_with_tool = _run(
        notifications=(
            _notification(
                "tool/call",
                {"toolCallId": "repair-tool", "toolName": "todo_write"},
            ),
        )
    )
    client = _RepairStubClient([_run(final_response="not-json"), repair_with_tool])
    runtime = DshResearchRuntime(
        DshRuntimeConfig(workspace=tmp_path, session_root=tmp_path / "sessions"),
        client=client,
    )

    with pytest.raises(AgentExecutionError) as raised:
        await runtime.execute(request)

    assert raised.value.error_code == "structured_output_invalid"
    assert raised.value.attempt == 2


@pytest.mark.asyncio
async def test_runtime_closes_client_on_timeout(tmp_path: Path) -> None:
    request = _request()
    client = _StubClient(request_id=request.request_id, delay_seconds=0.1)
    runtime = DshResearchRuntime(
        DshRuntimeConfig(
            workspace=tmp_path,
            session_root=tmp_path / "sessions",
            request_timeout_seconds=0.01,
        ),
        client=client,
    )

    with pytest.raises(AgentExecutionError) as raised:
        await runtime.execute(request)

    assert raised.value.error_code == "provider_timeout"
    assert raised.value.retryable is True
    assert client.closed is True


@pytest.mark.asyncio
async def test_runtime_rejects_elapsed_deadline_before_calling_dsh(tmp_path: Path) -> None:
    request = _request(deadline_at=datetime.now(UTC) - timedelta(seconds=1))
    client = _StubClient(request_id=request.request_id)
    runtime = DshResearchRuntime(
        DshRuntimeConfig(workspace=tmp_path, session_root=tmp_path / "sessions"),
        client=client,
    )

    with pytest.raises(AgentExecutionError) as raised:
        await runtime.execute(request)

    assert raised.value.error_code == "provider_timeout"
    assert client.called is False


@pytest.mark.asyncio
async def test_runtime_maps_unexpected_harness_failure(tmp_path: Path) -> None:
    request = _request()
    client = _StubClient(request_id=request.request_id, error=RuntimeError("harness crashed"))
    runtime = DshResearchRuntime(
        DshRuntimeConfig(workspace=tmp_path, session_root=tmp_path / "sessions"),
        client=client,
    )

    with pytest.raises(AgentExecutionError) as raised:
        await runtime.execute(request)

    assert raised.value.error_code == "dsh_runtime_failed"


def test_bundled_runtime_completes_local_handshake_without_external_model() -> None:
    pytest.importorskip("deepseek_harness")

    report = check_local_readiness(DshRuntimeConfig())

    assert report.sdk_version == "0.1.1rc1"
    assert report.runtime_name == "deepseek-harness-sdk-runtime"
    assert report.local_handshake is True
    assert report.error_code is None

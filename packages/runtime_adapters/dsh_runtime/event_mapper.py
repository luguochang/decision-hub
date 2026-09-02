from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Literal, cast

from pydantic import ValidationError

from packages.contracts_py.decision_hub_contracts import ErrorProvenance, ResearchTraceEvent

from .client import DshSdkNotification

type TraceEventType = Literal[
    "session_started",
    "plan_created",
    "task_started",
    "model_step_started",
    "model_step_completed",
    "tool_started",
    "tool_completed",
    "tool_failed",
    "subagent_started",
    "subagent_completed",
    "evidence_accepted",
    "evidence_rejected",
    "coverage_assessed",
    "replan",
    "round_completed",
    "synthesis_started",
    "session_stopped",
]
type TraceStatus = Literal["running", "succeeded", "failed", "degraded", "denied"]


def map_notifications(
    notifications: Sequence[DshSdkNotification],
    *,
    run_id: str,
    research_session_id: str,
    received_at: datetime | None = None,
) -> list[ResearchTraceEvent]:
    fallback_time = received_at or datetime.now(UTC)
    traces: list[ResearchTraceEvent] = []
    tool_names: dict[str, str] = {}
    for notification in notifications:
        mapped = _map_notification(notification, fallback_time, tool_names)
        if mapped is None:
            continue
        (
            event_type,
            stage,
            summary,
            reference_type,
            reference_id,
            status,
            error_code,
            error,
        ) = mapped
        traces.append(
            ResearchTraceEvent(
                schema_version="research-trace-event.v1",
                run_id=run_id,
                research_session_id=research_session_id,
                sequence_no=len(traces),
                event_type=cast(TraceEventType, event_type),
                occurred_at=_occurred_at(notification.payload, fallback_time),
                stage=stage,
                summary=summary,
                reference_type=reference_type,
                reference_id=reference_id,
                status=cast(TraceStatus, status),
                error_code=error_code,
                error=error,
            )
        )
    return traces


def _map_notification(
    notification: DshSdkNotification,
    fallback_time: datetime,
    tool_names: dict[str, str],
) -> tuple[str, str, str, str | None, str | None, str, str | None, ErrorProvenance | None] | None:
    payload = notification.payload
    if notification.method == "subagent.started":
        child_id = _string(payload, "childSessionId", "sessionId")
        return (
            "subagent_started",
            "acquiring_evidence",
            "A restricted research subagent started.",
            "dsh_session",
            child_id,
            "running",
            None,
            None,
        )
    if notification.method == "subagent.finished":
        child_id = _string(payload, "childSessionId", "sessionId")
        succeeded = _string(payload, "status") in {None, "ok", "completed", "succeeded"}
        return (
            "subagent_completed",
            "acquiring_evidence",
            "A restricted research subagent completed."
            if succeeded
            else "A restricted research subagent failed.",
            "dsh_session",
            child_id,
            "succeeded" if succeeded else "failed",
            None if succeeded else "dsh_subagent_failed",
            None
            if succeeded
            else ErrorProvenance(
                error_code="dsh_subagent_failed",
                origin="dsh",
                cause_code=None,
                capability_id=None,
                tool_call_id=None,
                retryable=False,
                deadline_ms=None,
            ),
        )
    if notification.method == "session.status":
        status = _string(payload, "status")
        if status == "idle":
            return (
                "session_stopped",
                "done",
                "The DSH research session reached idle.",
                "dsh_session",
                _string(payload, "sessionId"),
                "succeeded",
                None,
                None,
            )
        return None
    if notification.method != "session.event":
        return None

    event = payload.get("event")
    if not isinstance(event, Mapping):
        return None
    event_type = event.get("type")
    if not isinstance(event_type, str):
        return None
    data = event.get("data")
    event_data = data if isinstance(data, Mapping) else {}
    reference_id = _tool_call_id(event_data)
    tool_name = _string(event_data, "toolName", "name")
    if event_type == "tool/call" and reference_id is not None and tool_name is not None:
        tool_names[reference_id] = tool_name
    tool_name = tool_name or (tool_names.get(reference_id) if reference_id else None) or "tool"

    if event_type == "agent/inbox/spliced":
        return (
            "session_started",
            "planning",
            "The research request entered the durable DSH session inbox.",
            "dsh_session",
            _string(payload, "sessionId"),
            "running",
            None,
            None,
        )
    if event_type in {"step/start", "agent/step-start"}:
        return (
            "model_step_started",
            "planning",
            "The research manager started a model step.",
            "dsh_event",
            reference_id,
            "running",
            None,
            None,
        )
    if event_type == "tool/call":
        return (
            "tool_started",
            "acquiring_evidence",
            f"The research session called {tool_name}.",
            "dsh_tool_call",
            reference_id,
            "running",
            None,
            None,
        )
    if event_type in {"tool/result", "tool/call-result"}:
        failed = _tool_result_failed(event_data)
        error = (
            _tool_error_provenance(event_data, reference_id, tool_name)
            if failed
            else None
        )
        return (
            "tool_failed" if failed else "tool_completed",
            "acquiring_evidence",
            f"The {tool_name} call failed." if failed else f"The {tool_name} call completed.",
            "dsh_tool_call",
            reference_id,
            "failed" if failed else "succeeded",
            error.error_code if error is not None else None,
            error,
        )
    if event_type in {"step/end", "agent/step-end"}:
        reason = event_data.get("reason")
        reason_kind = _string(reason, "kind") if isinstance(reason, Mapping) else None
        succeeded = reason_kind in {None, "completed", "tool-calls"}
        return (
            "model_step_completed",
            "acquiring_evidence",
            "The research manager completed a model step."
            if succeeded
            else "The research manager stopped without a completed model step.",
            "dsh_event",
            reference_id,
            "succeeded" if succeeded else "degraded",
            None if succeeded else f"dsh_finish_{reason_kind or 'unknown'}",
            None
            if succeeded
            else ErrorProvenance(
                error_code=f"dsh_finish_{reason_kind or 'unknown'}",
                origin="dsh",
                cause_code=reason_kind,
                capability_id=None,
                tool_call_id=reference_id,
                retryable=False,
                deadline_ms=None,
            ),
        )
    if event_type == "turn/end":
        reason = event_data.get("reason")
        reason_kind = _string(reason, "kind") if isinstance(reason, Mapping) else None
        succeeded = reason_kind == "completed"
        return (
            "round_completed",
            "synthesis",
            "The DSH turn completed." if succeeded else "The DSH turn stopped without completion.",
            "dsh_event",
            reference_id,
            "succeeded" if succeeded else "degraded",
            None if succeeded else f"dsh_finish_{reason_kind or 'unknown'}",
            None
            if succeeded
            else ErrorProvenance(
                error_code=f"dsh_finish_{reason_kind or 'unknown'}",
                origin="dsh",
                cause_code=reason_kind,
                capability_id=None,
                tool_call_id=reference_id,
                retryable=False,
                deadline_ms=None,
            ),
        )
    return None


def _tool_call_id(data: Mapping[str, object]) -> str | None:
    direct = _string(data, "toolCallId", "callId", "id")
    if direct is not None:
        return direct
    message = data.get("message")
    if not isinstance(message, Mapping):
        return None
    content = message.get("content")
    if not isinstance(content, Sequence) or isinstance(content, (str, bytes)):
        return None
    for block in content:
        if isinstance(block, Mapping):
            nested = _string(block, "toolCallId", "callId", "id")
            if nested is not None:
                return nested
    return None


def _tool_result_failed(data: Mapping[str, object]) -> bool:
    if data.get("error") is not None or bool(data.get("isError")):
        return True
    if _string(data, "status") == "error":
        return True
    message = data.get("message")
    if not isinstance(message, Mapping):
        return False
    content = message.get("content")
    if not isinstance(content, Sequence) or isinstance(content, (str, bytes)):
        return False
    return any(isinstance(block, Mapping) and bool(block.get("isError")) for block in content)


def _tool_error_provenance(
    data: Mapping[str, object], reference_id: str | None, tool_name: str
) -> ErrorProvenance:
    raw = data.get("error")
    if isinstance(raw, Mapping) and _string(raw, "error_code") is not None:
        parsed = _validate_provenance(raw, reference_id)
        if parsed is not None:
            return parsed
    if isinstance(raw, str):
        parsed = _provenance_from_text(raw, reference_id)
        if parsed is not None:
            return parsed
    for text in _nested_tool_result_texts(data):
        parsed = _provenance_from_text(text, reference_id)
        if parsed is not None:
            return parsed
    return ErrorProvenance(
        error_code="dsh_tool_failed",
        origin="mcp",
        cause_code=tool_name,
        capability_id=None,
        tool_call_id=reference_id,
        retryable=False,
        deadline_ms=None,
    )


def _nested_tool_result_texts(data: Mapping[str, object]) -> list[str]:
    """Read only the official tool-result content path, never arbitrary payload text."""
    message = data.get("message")
    if not isinstance(message, Mapping):
        return []
    content = message.get("content")
    if not isinstance(content, Sequence) or isinstance(content, (str, bytes)):
        return []
    texts: list[str] = []
    for block in content:
        if not isinstance(block, Mapping) or block.get("type") != "tool-result":
            continue
        nested = block.get("content")
        if not isinstance(nested, Sequence) or isinstance(nested, (str, bytes)):
            continue
        for item in nested:
            if not isinstance(item, Mapping) or item.get("type") != "text":
                continue
            value = item.get("text")
            if isinstance(value, str) and value:
                texts.append(value)
    return texts


def _provenance_from_text(text: str, reference_id: str | None) -> ErrorProvenance | None:
    marker = "provenance="
    marker_index = text.find(marker)
    if marker_index < 0:
        return None
    encoded = text[marker_index + len(marker) :].lstrip()
    try:
        payload, _ = json.JSONDecoder().raw_decode(encoded)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, Mapping):
        return None
    return _validate_provenance(payload, reference_id)


def _validate_provenance(
    payload: Mapping[str, object], reference_id: str | None
) -> ErrorProvenance | None:
    # Validate the canonical shape instead of guessing from free-form error text.
    candidate = dict(payload)
    candidate["tool_call_id"] = reference_id
    try:
        return ErrorProvenance.model_validate(candidate)
    except (ValidationError, TypeError, ValueError):
        return None


def _string(values: Mapping[str, object], *keys: str) -> str | None:
    for key in keys:
        value = values.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _occurred_at(payload: Mapping[str, object], fallback: datetime) -> datetime:
    event = payload.get("event")
    candidates: list[object] = [payload.get("occurredAt"), payload.get("timestamp")]
    if isinstance(event, Mapping):
        candidates.extend((event.get("occurredAt"), event.get("timestamp")))
    for value in candidates:
        if not isinstance(value, str):
            continue
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.tzinfo is not None:
            return parsed
    return fallback

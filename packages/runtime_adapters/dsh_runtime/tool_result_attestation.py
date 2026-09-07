from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from pydantic import ValidationError

from packages.contracts_py.decision_hub_contracts import (
    EvidenceCandidate,
    FactEnvelope,
    ResearchCapabilityResult,
    ResearchSessionRequest,
    ResearchSynthesisCandidate,
)
from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError

from .client import DshSdkNotification

_RESEARCH_TOOL_NAMES = frozenset(
    {"decision_hub_research", "research_capability_execute"}
)
_SYNTHESIS_TOOL_NAME = "decision_hub_synthesis_submit"
_MAX_EMBEDDED_JSON_BYTES = 1_000_000


@dataclass(frozen=True)
class AttestedCapabilityResult:
    tool_call_id: str
    result: ResearchCapabilityResult
    declared_request_id: str | None = None


def attest_evidence_candidates(
    candidates: Sequence[EvidenceCandidate],
    notifications: Sequence[DshSdkNotification],
    request: ResearchSessionRequest,
) -> None:
    """Require each model-returned candidate to exactly match a successful MCP result."""

    if not candidates:
        return
    allowed = frozenset(request.allowed_capabilities)
    fingerprints: dict[str, set[str]] = {}
    for result in extract_capability_results(notifications):
        if result.capability_id not in allowed:
            continue
        for evidence in result.evidence_candidates:
            if evidence.research_session_id != candidates[0].research_session_id:
                continue
            fingerprints.setdefault(evidence.evidence_id, set()).add(_fingerprint(evidence))

    for candidate in candidates:
        matches = fingerprints.get(candidate.evidence_id, set())
        if _fingerprint(candidate) not in matches:
            raise AgentExecutionError(
                "dsh_evidence_unattested",
                "DSH returned evidence that was not attested by an allowed MCP tool result",
            )


def attest_facts(
    facts: Sequence[FactEnvelope],
    notifications: Sequence[DshSdkNotification],
    request: ResearchSessionRequest,
    *,
    research_session_id: str,
) -> None:
    """Require every typed fact to exactly match one allowed MCP result."""

    if not facts:
        return
    allowed = frozenset(request.allowed_capabilities)
    fingerprints: dict[str, set[str]] = {}
    for result in extract_capability_results(notifications):
        if result.capability_id not in allowed:
            continue
        for fact in result.facts or []:
            fingerprints.setdefault(fact.fact_id, set()).add(_fact_fingerprint(fact))
    for fact in facts:
        matches = fingerprints.get(fact.fact_id, set())
        if _fact_fingerprint(fact) not in matches:
            raise AgentExecutionError(
                "dsh_fact_unattested",
                "DSH returned a typed fact that was not attested by an allowed MCP result",
                cause_code="capability_fact_conflict",
            )


def extract_capability_results(
    notifications: Sequence[DshSdkNotification],
) -> list[ResearchCapabilityResult]:
    return [item.result for item in extract_attested_capability_results(notifications)]


def extract_attested_capability_results(
    notifications: Sequence[DshSdkNotification],
) -> list[AttestedCapabilityResult]:
    tool_names: dict[tuple[str | None, str], str] = {}
    tool_request_ids: dict[tuple[str | None, str], str] = {}
    results: dict[str, AttestedCapabilityResult] = {}
    for notification in notifications:
        event = _session_event(notification)
        if event is None:
            continue
        event_type, data, session_id = event
        call_id = _tool_call_id(data)
        if event_type == "tool/call":
            tool_name = _tool_name(data)
            if call_id is not None and tool_name is not None:
                tool_names[(session_id, call_id)] = tool_name
            declared_request_id = _tool_request_id(data)
            if call_id is not None and declared_request_id is not None:
                tool_request_ids[(session_id, call_id)] = declared_request_id
            continue
        if event_type not in {"tool/result", "tool/call-result"} or call_id is None:
            continue
        tool_name = _tool_name(data) or tool_names.get((session_id, call_id))
        if tool_name is None or not any(
            tool_name.endswith(name) for name in _RESEARCH_TOOL_NAMES
        ):
            continue
        if _tool_result_failed(data):
            continue
        for payload in _canonical_payloads(data):
            try:
                result = ResearchCapabilityResult.model_validate(payload)
            except ValidationError:
                continue
            fingerprint = json.dumps(
                result.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            results[fingerprint] = AttestedCapabilityResult(
                tool_call_id=call_id,
                result=result,
                declared_request_id=tool_request_ids.get((session_id, call_id)),
            )
    return list(results.values())


def extract_attested_synthesis_candidates(
    notifications: Sequence[DshSdkNotification],
    *,
    expected_session_id: str,
) -> list[ResearchSynthesisCandidate]:
    """Return successful synthesis captures proven by an exact DSH Tool call/result pair."""

    calls: dict[tuple[str, str], str] = {}
    candidates: list[ResearchSynthesisCandidate] = []
    for notification in notifications:
        event = _session_event(notification)
        if event is None:
            continue
        event_type, data, session_id = event
        if session_id is None or session_id != expected_session_id:
            continue
        if event_type == "tool/call":
            identity = _typed_tool_identity(data, item_type="tool-call")
            if identity is not None:
                call_id, tool_name = identity
                calls[(session_id, call_id)] = tool_name
            continue
        if event_type not in {"tool/result", "tool/call-result"}:
            continue
        call_id = _typed_tool_result_call_id(data)
        if call_id is None:
            continue
        # The result payload is untrusted. Resolve the tool name only from the
        # preceding typed call event with the same Session and call identity.
        if calls.get((session_id, call_id)) != _SYNTHESIS_TOOL_NAME:
            continue
        if _tool_result_failed(data):
            continue
        for payload in _schema_payloads(data, "research-synthesis-candidate.v1"):
            try:
                candidate = ResearchSynthesisCandidate.model_validate(payload)
            except ValidationError:
                continue
            candidates.append(candidate)
    return candidates


def _session_event(
    notification: DshSdkNotification,
) -> tuple[str, Mapping[str, object], str | None] | None:
    if notification.method != "session.event":
        return None
    event = notification.payload.get("event")
    if not isinstance(event, Mapping):
        return None
    event_type = event.get("type")
    if not isinstance(event_type, str):
        return None
    data = event.get("data")
    event_data = cast(Mapping[str, object], data) if isinstance(data, Mapping) else {}
    session_id = notification.payload.get("sessionId")
    return event_type, event_data, session_id if isinstance(session_id, str) else None


def _tool_name(data: Mapping[str, object]) -> str | None:
    direct = _string(data, "toolName", "name")
    if direct is not None:
        return direct
    for item in _walk_mappings(data):
        nested = _string(item, "toolName", "name")
        if nested is not None:
            return nested
    return None


def _typed_tool_identity(
    data: Mapping[str, object],
    *,
    item_type: str,
) -> tuple[str, str] | None:
    call_id = _string(data, "toolCallId", "callId", "id")
    tool_name = _string(data, "toolName", "name")
    if call_id is not None and tool_name is not None:
        return call_id, tool_name
    for item in _typed_content_items(data, item_type):
        call_id = _string(item, "toolCallId", "callId", "id")
        tool_name = _string(item, "toolName", "name")
        if call_id is not None and tool_name is not None:
            return call_id, tool_name
    return None


def _typed_tool_result_call_id(data: Mapping[str, object]) -> str | None:
    direct = _string(data, "toolCallId", "callId", "id")
    if direct is not None:
        return direct
    for item in _typed_content_items(data, "tool-result"):
        call_id = _string(item, "toolCallId", "callId", "id")
        if call_id is not None:
            return call_id
    return None


def _typed_content_items(
    data: Mapping[str, object], item_type: str
) -> Iterable[Mapping[str, object]]:
    message = data.get("message")
    if not isinstance(message, Mapping):
        return
    content = message.get("content")
    if not isinstance(content, Sequence) or isinstance(content, (str, bytes)):
        return
    accepted_types = {item_type, item_type.replace("-", "_")}
    for item in content:
        if isinstance(item, Mapping) and item.get("type") in accepted_types:
            yield cast(Mapping[str, object], item)


def _tool_call_id(data: Mapping[str, object]) -> str | None:
    direct = _string(data, "toolCallId", "callId", "id")
    if direct is not None:
        return direct
    for item in _walk_mappings(data):
        nested = _string(item, "toolCallId", "callId")
        if nested is not None:
            return nested
    return None


def _tool_request_id(data: Mapping[str, object]) -> str | None:
    """Read request_id only from the structured tool-call arguments."""
    direct = _string(data, "request_id", "requestId")
    if direct is not None:
        return direct
    for item in _walk_mappings(data):
        arguments = item.get("arguments")
        if isinstance(arguments, Mapping):
            value = _string(arguments, "request_id", "requestId")
            if value is not None:
                return value
        if (
            not isinstance(arguments, str)
            or len(arguments.encode("utf-8")) > _MAX_EMBEDDED_JSON_BYTES
        ):
            continue
        try:
            decoded = json.loads(arguments)
        except json.JSONDecodeError:
            continue
        if isinstance(decoded, Mapping):
            value = _string(decoded, "request_id", "requestId")
            if value is not None:
                return value
    return None


def _tool_result_failed(data: Mapping[str, object]) -> bool:
    if data.get("error") is not None or bool(data.get("isError")):
        return True
    return any(
        item.get("error") is not None
        or bool(item.get("isError"))
        or _string(item, "status") == "error"
        for item in _walk_mappings(data)
    )


def _canonical_payloads(value: object) -> Iterable[Mapping[str, object]]:
    yield from _schema_payloads(value, "research-capability-result.v1")


def _schema_payloads(
    value: object, expected_schema_version: str
) -> Iterable[Mapping[str, object]]:
    if isinstance(value, Mapping):
        mapping = cast(Mapping[str, object], value)
        if mapping.get("schema_version") == expected_schema_version:
            yield mapping
        for nested in mapping.values():
            yield from _schema_payloads(nested, expected_schema_version)
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for nested in value:
            yield from _schema_payloads(nested, expected_schema_version)
        return
    if not isinstance(value, str) or len(value.encode("utf-8")) > _MAX_EMBEDDED_JSON_BYTES:
        return
    stripped = value.strip()
    if not stripped.startswith("{"):
        return
    try:
        decoded = json.loads(stripped)
    except json.JSONDecodeError:
        return
    yield from _schema_payloads(decoded, expected_schema_version)


def _walk_mappings(value: object) -> Iterable[Mapping[str, object]]:
    if isinstance(value, Mapping):
        mapping = cast(Mapping[str, object], value)
        yield mapping
        for nested in mapping.values():
            yield from _walk_mappings(nested)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for nested in value:
            yield from _walk_mappings(nested)


def _fingerprint(candidate: EvidenceCandidate) -> str:
    return json.dumps(
        candidate.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _fact_fingerprint(fact: FactEnvelope) -> str:
    return json.dumps(
        fact.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _string(values: Mapping[str, object], *keys: str) -> str | None:
    for key in keys:
        value = values.get(key)
        if isinstance(value, str) and value:
            return value
    return None

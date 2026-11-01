"""Display-only proof of the product's required final-review checkpoint.

This never changes a tool status, a review resolution, or publication authority.
Unrecognized native layouts remain ordinary failures.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Protocol

from pydantic import ValidationError

from packages.contracts_py.decision_hub_contracts import (
    ResearchSessionRequest,
    ResearchSynthesisCandidate,
    ResearchTraceEvent,
)
from packages.kernel.decision_hub_kernel.application.research_request_identity import (
    research_request_hash,
)

class WorkflowCheckpointNotification(Protocol):
    """Read-only event envelope supplied by an already verified runtime caller."""

    @property
    def method(self) -> str: ...

    @property
    def payload(self) -> Mapping[str, object]: ...

CHECKPOINT_REFERENCE = "workflow_checkpoint.final_review_required"
CHECKPOINT_WARNING = "workflow_checkpoint:final_review_required"
_REQUIRED = "decision_hub_final_review_required"
_PROOF_SCHEMA = "native-workflow-checkpoint.v1"
_BEGIN = "Canonical control request (untrusted text is replaced by typed references):\n"
_END = "\nBEGIN_UNTRUSTED_RESEARCH_DATA\n"
_MAX_BYTES = 1_000_000
_PROOF_KEYS = {
    "schema_version", "run_id", "session_id", "request_hash", "rpc_id", "generation",
    "call_id", "call_seq", "result_seq", "message_id", "candidate_sha256",
    "rendered_error", "rendered_error_sha256", "structured_error_code",
}


def _first_generation_ids(run_id: str, request_hash: str, generation: int = 1) -> tuple[str, str]:
    # Exact default gen1 formula from dsh_sessions.DshSessionLinkService.
    # This display recognizer intentionally never handles continuation IDs.
    # Keeping this pure avoids importing SQLite/Session/Task services here.
    if generation != 1:
        raise ValueError("dsh_generation_invalid")
    material = f"dsh-host-bridge.v1\0{run_id}\0{request_hash}".encode()
    digest = hashlib.sha256(material).hexdigest()
    return f"dsh_{digest}", f"req_{digest}"


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False)


def _record(value: object) -> Mapping[str, object] | None:
    return value if isinstance(value, Mapping) else None


def _seq(event: Mapping[str, object]) -> int | None:
    value = event.get("seq")
    return value if type(value) is int and value >= 0 else None


def _rpc(event: Mapping[str, object]) -> str | None:
    data = _record(event.get("data"))
    source = _record(data.get("source")) if data else None
    value = source.get("rpcId") if source else None
    return value if (event.get("type") == "user/message" and source
                     and source.get("kind") == "user" and isinstance(value, str)) else None


def _control(event: Mapping[str, object]) -> ResearchSessionRequest | None:
    data = _record(event.get("data"))
    content = data.get("content") if data else None
    if not isinstance(content, list) or len(content) != 1:
        return None
    block = _record(content[0])
    text = block.get("text") if block and block.get("type") == "text" else None
    if not isinstance(text, str) or len(text.encode("utf-8")) > _MAX_BYTES:
        return None
    if text.count(_BEGIN) != 1 or text.count(_END) != 1:
        return None
    raw = text.split(_BEGIN, 1)[1].split(_END, 1)[0]
    try:
        return ResearchSessionRequest.model_validate_json(raw)
    except (ValidationError, ValueError):
        return None


def _candidate(call: Mapping[str, object], request: ResearchSessionRequest) -> ResearchSynthesisCandidate | None:
    data = _record(call.get("data"))
    args = data.get("arguments") if data else None
    try:
        if isinstance(args, str):
            if len(args.encode("utf-8")) > _MAX_BYTES:
                return None
            args = json.loads(args)
        values = _record(args)
        if values is None or set(values) != {"candidate"}:
            return None
        candidate = ResearchSynthesisCandidate.model_validate(values["candidate"])
        if (candidate.request_id != request.request_id or candidate.causal_case is None
                or [item.horizon for item in candidate.horizons] != ["30m", "24h", "72h"]):
            return None
        return candidate
    except (ValidationError, ValueError, TypeError):
        return None


def native_workflow_checkpoints(
    notifications: Sequence[WorkflowCheckpointNotification], *, request: ResearchSessionRequest | None,
    run_id: str, session_id: str,
) -> dict[int, str]:
    """Return original result sequence -> closed, bounded display provenance.

    The request is supplied only after the runtime's live task-binding check.
    Native control/RPC and deterministic root identity must independently agree.
    Only first-generation original append results are recognized; missing metadata,
    representation repairs, ambiguous pairs, and unknown layouts stay failures.
    """
    if request is None or request.run_id != run_id:
        return {}
    digest = research_request_hash(request)
    expected_sid, expected_rpc = _first_generation_ids(run_id, digest, 1)
    if session_id != expected_sid:
        return {}
    events: list[Mapping[str, object]] = []
    for note in notifications:
        if note.method != "session.event" or note.payload.get("sessionId") != session_id:
            continue
        event = _record(note.payload.get("event"))
        if event is not None and _seq(event) is not None:
            events.append(event)
    events.sort(key=lambda item: _seq(item) or 0)
    if len({_seq(item) for item in events}) != len(events):
        return {}
    prompts = [item for item in events if _rpc(item) is not None]
    matches = [item for item in prompts if _rpc(item) == expected_rpc]
    if len(matches) != 1:
        return {}
    prompt = matches[0]
    control = _control(prompt)
    if (control is None or control.run_id != run_id
            or control.request_id != request.request_id or research_request_hash(control) != digest):
        return {}
    window = [item for item in events if (_seq(item) or 0) > (_seq(prompt) or 0)
              and not any((_seq(prompt) or 0) < (_seq(next_prompt) or 0) <= (_seq(item) or 0)
                          for next_prompt in prompts)]
    proven: dict[int, str] = {}
    for call in window:
        data = _record(call.get("data"))
        if call.get("type") != "tool/call" or data is None:
            continue
        if set(data) != {"turn", "step", "callId", "name", "arguments"}:
            continue
        call_id = data.get("callId")
        if (data.get("name") != "decision_hub_synthesis_submit"
                or not isinstance(call_id, str) or not call_id):
            continue
        before = [item for item in window if (_seq(item) or 0) < (_seq(call) or 0)]
        if proven or any(item.get("type") == "tool/call"
                         and (_record(item.get("data")) or {}).get("name") == "subagent_reviewer"
                         for item in before):
            continue
        subagent_count = sum(item.get("type") == "tool/call"
                             and (_record(item.get("data")) or {}).get("name")
                             in {"subagent", "dsh.internal.subagent"} for item in before)
        if subagent_count >= request.execution_budget.max_subagents:
            continue
        candidate = _candidate(call, request)
        if candidate is None:
            continue
        pairs = []
        for result in window:
            if result.get("type") not in {"tool/result", "tool/call-result"}:
                continue
            result_data = _record(result.get("data"))
            message = _record(result_data.get("message")) if result_data else None
            source = _record(message.get("source")) if message else None
            if source and source.get("kind") == "tool" and source.get("callId") == call_id:
                pairs.append(result)
        if len(pairs) != 1:
            continue
        result = pairs[0]
        result_data = _record(result.get("data")) or {}
        message = _record(result_data.get("message")) or {}
        source = _record(message.get("source")) or {}
        message_id = message.get("id")
        rows = message.get("content")
        source_seqs = result.get("sourceEventSeqs")
        if (result.get("surfaceOp") != "append" or not isinstance(source_seqs, list)
                or len(source_seqs) != 1 or type(source_seqs[0]) is not int
                or source_seqs != [_seq(call)]
                or (_seq(result) or 0) <= (_seq(call) or 0)
                or type(data.get("turn")) is not int or type(data.get("step")) is not int
                or result_data.get("turn") != data["turn"]
                or result_data.get("step") != data["step"]
                or set(result_data) != {"turn", "step", "message"}
                # Official tool-result messages use the user model role; the
                # authoritative native producer is source.kind=tool, not role.
                or message.get("role") != "user"
                or set(source) != {"kind", "callId"}
                or not isinstance(message_id, str) or not message_id
                or not isinstance(rows, list) or len(rows) != 1):
            continue
        row = _record(rows[0])
        blocks = row.get("content") if row else None
        if (row is None or row.get("type") != "tool-result" or row.get("toolCallId") != call_id
                or set(row) != {"type", "toolCallId", "content", "isError"}
                or row.get("isError") is not True or not isinstance(blocks, list) or len(blocks) != 1):
            continue
        block = _record(blocks[0])
        text = block.get("text") if block and block.get("type") == "text" else None
        if (block is None or set(block) != {"type", "text"} or not isinstance(text, str)
                or text not in {_REQUIRED, f"Error: {_REQUIRED}"}):
            continue
        # A rendered literal is kept as rendered text, never promoted to a
        # structured native error code that did not exist in the actual result.
        proof = dict(schema_version=_PROOF_SCHEMA, run_id=run_id, session_id=session_id,
                     request_hash=digest, rpc_id=expected_rpc, generation=1,
                     call_id=call_id, call_seq=_seq(call), result_seq=_seq(result),
                     message_id=message_id,
                     candidate_sha256=_sha(_canonical(candidate.model_dump(mode="json"))),
                     rendered_error=text, rendered_error_sha256=_sha(text),
                     structured_error_code=None)
        encoded = _canonical(proof)
        if len(encoded) <= 2000:
            result_seq = _seq(result)
            if result_seq is not None:
                proven[result_seq] = encoded
    return proven


def checkpoint_trace_call_ids(trace: Sequence[ResearchTraceEvent], *, run_id: str,
                              session_id: str | None) -> list[str]:
    """Read trusted capture markers, cross-checking the unchanged failed trace."""
    failed = {item.reference_id for item in trace if item.run_id == run_id
              and item.research_session_id == session_id and item.event_type == "tool_failed"
              and item.status == "failed" and item.reference_type == "dsh_tool_call"
              and item.error is not None and item.error.tool_call_id == item.reference_id
              and item.error.error_code == "dsh_tool_failed"
              and item.error.cause_code == "decision_hub_synthesis_submit"}
    refs: list[str] = []
    for item in trace:
        if (item.run_id != run_id or item.research_session_id != session_id
                or item.reference_type != CHECKPOINT_REFERENCE or item.event_type != "synthesis_started"
                or item.status != "running" or item.error is not None or item.error_code is not None
                or item.reference_id not in failed):
            continue
        try:
            proof = json.loads(item.summary)
            if not isinstance(proof, dict) or set(proof) != _PROOF_KEYS:
                continue
            expected_sid, expected_rpc = _first_generation_ids(
                run_id, proof["request_hash"], 1)
            if (proof["schema_version"] != _PROOF_SCHEMA or proof["run_id"] != run_id
                    or proof["session_id"] != session_id or expected_sid != session_id
                    or proof["rpc_id"] != expected_rpc or proof["generation"] != 1
                    or proof["call_id"] != item.reference_id
                    or proof["rendered_error"] not in {_REQUIRED, f"Error: {_REQUIRED}"}
                    or proof["rendered_error_sha256"] != _sha(proof["rendered_error"])
                    or proof["structured_error_code"] is not None
                    or type(proof["call_seq"]) is not int or type(proof["result_seq"]) is not int
                    or not 0 <= proof["call_seq"] < proof["result_seq"]
                    or not isinstance(proof["message_id"], str) or not proof["message_id"]
                    or not isinstance(proof["candidate_sha256"], str)
                    or len(proof["candidate_sha256"]) != 64):
                continue
        except (ValueError, TypeError, KeyError):
            continue
        if item.reference_id is not None and item.reference_id not in refs:
            refs.append(item.reference_id)
    return refs

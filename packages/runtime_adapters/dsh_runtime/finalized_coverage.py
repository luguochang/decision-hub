"""Validated child projection from an authenticated native finalize; no source discovery."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Annotated, Literal, cast

from pydantic import BaseModel, ConfigDict, Field

from packages.contracts_py.decision_hub_contracts import (
    EvidenceCandidate,
    FactEnvelope,
    ResearchSessionRequest,
)
from packages.kernel.decision_hub_kernel.application.research_request_identity import (
    research_request_hash,
)
from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError

from .client import DshSdkNotification

_MAX_SAFE = 2**53 - 1
_Seq = Annotated[int, Field(ge=0, le=_MAX_SAFE)]
_Text = Annotated[str, Field(min_length=1)]
_Hash = Annotated[str, Field(pattern="^[a-f0-9]{64}$")]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class _Binding(_Strict):
    runId: _Text
    requestHash: _Hash
    sessionId: _Text
    requestId: _Text
    generation: Annotated[int, Field(ge=1, le=_MAX_SAFE)]


class _Cutoff(_Strict):
    before_seq: _Seq
    events_sha256: _Hash


class _Snapshot(_Strict):
    message_id: _Text
    message_seq: _Seq
    body_sha256: _Hash


class _ProviderCall(_Strict):
    callId: _Text
    receiptHash: _Hash


class _Prefix(_Strict):
    sessionId: _Text
    parentSessionId: _Text
    firstLiveSeq: _Seq
    lifecycleId: _Text
    lastSeq: Annotated[int, Field(ge=-1, le=_MAX_SAFE)]
    eventsHash: _Hash
    providerCalls: list[_ProviderCall]


class _Source(_Strict):
    evidence_id: _Text
    capability_id: _Text
    execution_session_id: _Text
    native_call_id: _Text

    def identity(self) -> tuple[str, str, str, str]:
        return (self.evidence_id, self.capability_id,
                self.execution_session_id, self.native_call_id)


class _Carrier(_Strict):
    schema_version: Literal["decision-hub-finalized-coverage.v1"]
    binding: _Binding
    finalize_call_id: _Text
    finalize_call_seq: _Seq
    root_cutoff: _Cutoff
    source_snapshot: _Snapshot
    child_prefixes: Annotated[list[_Prefix], Field(max_length=63)]
    evidence_sources: list[_Source]
    evidence_candidates: Annotated[list[EvidenceCandidate], Field(min_length=1)]
    facts: list[FactEnvelope]


@dataclass(frozen=True)
class FinalizedCoverage:
    evidence_candidates: tuple[EvidenceCandidate, ...]
    facts: tuple[FactEnvelope, ...]


def _canonical(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                          allow_nan=False)
    except RecursionError as exc:
        raise ValueError("finalized_coverage_json_depth") from exc


def _object(value: object) -> dict[str, object]:
    if type(value) is not dict:
        raise ValueError("finalized_coverage_object")
    if any(type(key) is not str for key in cast(dict[object, object], value)):
        raise ValueError("finalized_coverage_json_key")
    return cast(dict[str, object], value)


def _pairs(values: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in values:
        if key in result:
            raise ValueError("finalized_coverage_duplicate_json_key")
        result[key] = value
    return result


def _raw_dtos(raw: dict[str, object]) -> None:
    for name in ("evidence_candidates", "facts"):
        rows = raw.get(name)
        if type(rows) is not list:
            raise ValueError("finalized_coverage_dto_array")
        for value in cast(list[object], rows):
            row = _object(value)
            for key in ("observed_at", "received_at", "published_at",
                        "window_start_at", "window_end_at"):
                stamp = row.get(key)
                if stamp is not None and type(stamp) is not str:
                    raise ValueError("finalized_coverage_dto_clock")
            if name == "evidence_candidates" and type(row.get("round")) is not int:
                raise ValueError("finalized_coverage_dto_round")
            if name == "facts" and type(row.get("value")) is bool:
                raise ValueError("finalized_coverage_dto_value")


def _prefixes(carrier: _Carrier, root: str, budget: int) -> dict[str, _Prefix]:
    prefixes = {row.sessionId: row for row in carrier.child_prefixes}
    if len(prefixes) != len(carrier.child_prefixes):
        raise ValueError("finalized_coverage_duplicate_child")
    if sum(len(row.providerCalls) for row in prefixes.values()) > budget:
        raise ValueError("finalized_coverage_provider_budget")
    for row in prefixes.values():
        if (row.sessionId == root or row.parentSessionId == row.sessionId
                or row.firstLiveSeq > row.lastSeq + 1
                or len({call.callId for call in row.providerCalls}) != len(row.providerCalls)):
            raise ValueError("finalized_coverage_child_prefix")
        visited = {row.sessionId}
        parent = row.parentSessionId
        while parent in prefixes:
            if parent in visited:
                raise ValueError("finalized_coverage_child_cycle")
            visited.add(parent)
            parent = prefixes[parent].parentSessionId
    return prefixes


def _rows(carrier: _Carrier, request: ResearchSessionRequest) -> None:
    evidence = {row.evidence_id: row for row in carrier.evidence_candidates}
    evidence_ids = [row.evidence_id for row in carrier.evidence_candidates]
    fact_ids = [row.fact_id for row in carrier.facts]
    if (evidence_ids != sorted(set(evidence_ids)) or fact_ids != sorted(set(fact_ids))):
        raise ValueError("finalized_coverage_duplicate_or_unsorted_dto")
    requirements = {row.requirement_id for row in request.evidence_requirements}
    for row in evidence.values():
        if (row.quality != "accepted" or row.research_session_id != carrier.binding.sessionId
                or row.requirement_id not in requirements):
            raise ValueError("finalized_coverage_evidence_scope")
    for fact in carrier.facts:
        source = evidence.get(fact.evidence_id)
        if (fact.quality != "accepted" or source is None
                or fact.requirement_id != source.requirement_id):
            raise ValueError("finalized_coverage_fact_scope")
    prefixes = _prefixes(
        carrier, carrier.binding.sessionId, request.execution_budget.max_tool_calls,
    )
    sources = [row.identity() for row in carrier.evidence_sources]
    if sources != sorted(set(sources)):
        raise ValueError("finalized_coverage_duplicate_or_unsorted_source")
    for row in carrier.evidence_sources:
        prefix = prefixes.get(row.execution_session_id)
        if (row.evidence_id not in evidence or row.capability_id not in request.allowed_capabilities
                or prefix is None
                or row.native_call_id not in {p.callId for p in prefix.providerCalls}):
            raise ValueError("finalized_coverage_source_scope")
    if {row.evidence_id for row in carrier.evidence_sources} != set(evidence):
        raise ValueError("finalized_coverage_evidence_source_missing")


_NativeRows = dict[int, list[tuple[int, dict[str, object]]]]


def _native_rows(notifications: Sequence[DshSdkNotification], root: str) -> _NativeRows:
    rows: _NativeRows = {}
    for index, notification in enumerate(notifications):
        if (notification.method != "session.event"
                or notification.payload.get("sessionId") != root):
            continue
        raw = notification.payload.get("event")
        if type(raw) is not dict:
            continue
        event = _object(cast(dict[str, object], raw))
        seq = event.get("seq")
        if type(seq) is int:
            rows.setdefault(seq, []).append((index, event))
    return rows


def _protected_body(data: dict[str, object]) -> tuple[str, dict[str, object]]:
    if (data.get("role") != "user" or data.get("source") != {
            "kind": "plugin", "plugin": "decision-hub-protected-context"}):
        raise ValueError("finalized_coverage_snapshot_source")
    content = data.get("content")
    if type(content) is not list or len(cast(list[object], content)) != 2:
        raise ValueError("finalized_coverage_snapshot_content")
    blocks = [_object(value) for value in cast(list[object], content)]
    if any(set(block) != {"type", "text"} or block["type"] != "text"
           or type(block["text"]) is not str for block in blocks):
        raise ValueError("finalized_coverage_snapshot_text")
    text = cast(str, blocks[1]["text"])
    body = _object(json.loads(text, object_pairs_hook=_pairs))
    if (type(data.get("id")) is not str or not data["id"]
            or data["id"] != body.get("snapshot_id") or body.get("schema_version") not in {
                "decision-hub-protected-context.v5", "decision-hub-protected-context.v6"}):
        raise ValueError("finalized_coverage_snapshot_identity")
    return text, body


def _snapshot_chain(rows: _NativeRows, seq: int, expected: dict[str, object]) -> dict[str, object]:
    selected: dict[str, object] | None = None
    seen: set[int] = set()
    while True:
        matches = rows.get(seq, [])
        if len(matches) != 1 or seq in seen or not 0 <= seq <= _MAX_SAFE:
            raise ValueError("finalized_coverage_snapshot_chain")
        seen.add(seq)
        index, event = matches[0]
        if event.get("type") != "user/message":
            raise ValueError("finalized_coverage_snapshot_original")
        data = _object(event.get("data"))
        _, body = _protected_body(data)
        same_id = sum(1 for group in rows.values() for _, other in group
                      if other.get("type") == "user/message" and type(other.get("data")) is dict
                      and _object(other.get("data")).get("id") == data["id"])
        if same_id != 1:
            raise ValueError("finalized_coverage_duplicate_snapshot_id")
        if _canonical({key: body.get(key) for key in expected}) != _canonical(expected):
            raise ValueError("finalized_coverage_snapshot_chain_scope")
        if selected is None:
            selected = data
        surface = event.get("surfaceOp")
        if surface in (None, "append"):
            return selected
        operation = _object(surface)
        start = operation.get("start")
        if (set(operation) != {"op", "start", "end"} or operation["op"] != "replace"
                or type(start) is not int or type(operation["end"]) is not int
                or start != operation["end"] or not 0 <= start < seq
                or _canonical(event.get("sourceEventSeqs")) != _canonical([start])):
            raise ValueError("finalized_coverage_snapshot_replace")
        original, prunes = rows.get(start, []), rows.get(seq - 1, [])
        if len(original) != 1 or len(prunes) != 1 or not original[0][0] < prunes[0][0] < index:
            raise ValueError("finalized_coverage_snapshot_prune_order")
        prune = prunes[0][1]
        shadow = _object(prune.get("data"))
        tokens = shadow.get("shadowedTokenCount")
        if (prune.get("type") != "compaction/prune"
                or set(shadow) != {"shadowedRange", "shadowedSeqs", "shadowedTokenCount"}
                or _canonical(shadow["shadowedRange"]) != _canonical({"start": start, "end": start})
                or _canonical(shadow["shadowedSeqs"]) != _canonical([start])
                or type(tokens) is not int or not 0 <= tokens <= _MAX_SAFE):
            raise ValueError("finalized_coverage_snapshot_prune")
        seq = start


def _snapshot(carrier: _Carrier, notifications: Sequence[DshSdkNotification],
              request: ResearchSessionRequest) -> None:
    reference = carrier.source_snapshot
    expected: dict[str, object] = {
        "run_id": carrier.binding.runId, "root_session_id": carrier.binding.sessionId,
        "request_hash": carrier.binding.requestHash, "generation": carrier.binding.generation,
        "command_request_id": carrier.binding.requestId, "request_id": request.request_id,
    }
    rows = _native_rows(notifications, carrier.binding.sessionId)
    if reference.message_seq >= carrier.finalize_call_seq:
        raise ValueError("finalized_coverage_snapshot_cutoff")
    data = _snapshot_chain(rows, reference.message_seq, expected)
    text, body = _protected_body(data)
    if (data["id"] != reference.message_id
            or hashlib.sha256(text.encode("utf-8")).hexdigest() != reference.body_sha256
            or _canonical(body.get("child_research_prefixes")) != _canonical(
                [row.model_dump(mode="json") for row in carrier.child_prefixes])):
        raise ValueError("finalized_coverage_snapshot_binding")


def validate_finalized_coverage(
    value: object, *, binding: object, request: ResearchSessionRequest | None,
    notifications: Sequence[DshSdkNotification], call_id: str, call_seq: int | None,
) -> FinalizedCoverage:
    """Call only after the full native pair, review and latest preparation are attested."""
    if request is None or type(call_seq) is not int:
        raise ValueError("finalized_coverage_request_required")
    raw = _object(value)
    _raw_dtos(raw)
    carrier = _Carrier.model_validate(raw)
    _canonical(raw)
    if (carrier.binding.model_dump(mode="json") != binding
            or carrier.binding.runId != request.run_id
            or carrier.binding.requestHash != research_request_hash(request)
            or carrier.finalize_call_id != call_id or carrier.finalize_call_seq != call_seq
            or carrier.root_cutoff.before_seq != call_seq):
        raise ValueError("finalized_coverage_finalize_binding")
    budget = request.execution_budget.max_tool_calls
    if (len(carrier.evidence_candidates) > 20 * budget
            or len(carrier.evidence_sources) > 20 * budget or len(carrier.facts) > 200 * budget):
        raise ValueError("finalized_coverage_budget")
    _rows(carrier, request)
    _snapshot(carrier, notifications, request)
    return FinalizedCoverage(tuple(carrier.evidence_candidates), tuple(carrier.facts))


def _merge_rows[Row: BaseModel](
    original: Sequence[Row], child: Sequence[Row], identity: Callable[[Row], str], *,
    error_code: str, cause_code: str,
) -> list[Row]:
    result = list(original)
    by_id = {identity(row): _canonical(row.model_dump(mode="json")) for row in original}
    for row in child:
        key, fingerprint = identity(row), _canonical(row.model_dump(mode="json"))
        if key in by_id:
            if by_id[key] != fingerprint:
                raise AgentExecutionError(
                    error_code, "Native child coverage conflicts with retained data",
                    cause_code=cause_code,
                )
        else:
            by_id[key] = fingerprint
            result.append(row)
    return result


def merge_finalized_coverage(
    evidence: list[EvidenceCandidate], facts: list[FactEnvelope], coverage: FinalizedCoverage,
) -> tuple[list[EvidenceCandidate], list[FactEnvelope]]:
    """Keep the old root order and exact DTO identity; never replace a conflicting row."""
    merged_evidence = _merge_rows(
        evidence, coverage.evidence_candidates, lambda row: row.evidence_id,
        error_code="dsh_evidence_unattested", cause_code="capability_evidence_conflict",
    )
    merged_facts = _merge_rows(
        facts, coverage.facts, lambda row: row.fact_id,
        error_code="dsh_fact_unattested", cause_code="capability_fact_conflict",
    )
    evidence_ids = {row.evidence_id for row in merged_evidence}
    if any(row.evidence_id not in evidence_ids for row in merged_facts):
        raise AgentExecutionError(
            "dsh_fact_unattested", "Native child fact has no retained evidence",
            cause_code="fact_evidence_missing",
        )
    return merged_evidence, merged_facts

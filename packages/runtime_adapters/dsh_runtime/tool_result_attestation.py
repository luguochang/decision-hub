from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from packages.contracts_py.decision_hub_contracts import (
    EvidenceCandidate,
    FactEnvelope,
    IndependentReviewReceiptV2,
    ResearchCapabilityResult,
    ResearchSessionRequest,
    ResearchSynthesisCandidate,
)
from packages.kernel.decision_hub_kernel.application.research_review import (
    independent_review_receipt_hash,
)
from packages.kernel.decision_hub_kernel.ports.research_action_synthesis import (
    normalize_action_synthesis,
)
from packages.kernel.decision_hub_kernel.ports.research_signal import ResearchDecisionSynthesisV1
from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError

from .client import DshSdkNotification

_RESEARCH_TOOL_NAMES = frozenset({"decision_hub_research", "research_capability_execute"})
_SYNTHESIS_TOOL_NAME = "decision_hub_synthesis_submit"
# The Web preset exposes the product's isolated reviewer as a distinct tool;
# its successful result is still a subagent-owned review receipt and must be
# bound to the preceding call for attestation.
_SUBAGENT_TOOL_NAMES = frozenset({"subagent", "dsh.internal.subagent", "subagent_reviewer"})
_REVIEW_RECEIPT_SCHEMA = "independent-review-receipt.v1"
_MAX_EMBEDDED_JSON_BYTES = 1_000_000


@dataclass(frozen=True)
class AttestedCapabilityResult:
    tool_call_id: str
    result: ResearchCapabilityResult
    declared_request_id: str | None = None


@dataclass(frozen=True)
class AttestedReviewReceipt:
    receipt_hash: str
    role: Literal["independent_reviewer"]
    target_claim: str
    evidence_refs: list[str]
    challenge: str
    resolution: Literal["accepted", "bounded", "requires_new_evidence", "rejected"]
    limitations: list[str]
    created_at: datetime
    # Public Review uses its historical six-field hash; V2 retains full effects.
    summary_hash: str | None = None
    structured_receipt: IndependentReviewReceiptV2 | None = None
    tool_call_id: str | None = None
    call_event_index: int | None = None
    result_event_index: int | None = None
    wrapper_json: str | None = None


@dataclass(frozen=True)
class AttestedSynthesisCapture:
    candidate: ResearchSynthesisCandidate
    call_id: str
    call_event_index: int
    result_event_index: int
    payload_occurrence: int
    payload_path: tuple[str | int | None, ...]
    candidate_payload_hash: str
    call_seq: int | None
    result_seq: int | None


@dataclass(frozen=True)
class AttestedDecisionSynthesisCapture:
    synthesis: ResearchDecisionSynthesisV1
    projection: AttestedSynthesisCapture
    payload_path: tuple[str | int | None, ...]
    raw_candidate_hash: str


def _actual_return_payloads(
    data: Mapping[str, object], call_id: str, *, allow_full_perpetual_review: bool = False
):
    """Exact Tool return locations, excluding text quoting nested candidates."""
    message = data.get("message")
    content = message.get("content") if isinstance(message, Mapping) else None
    rows = (
        [
            (item, ("message", "content", index))
            for index, item in enumerate(content or [])
            if isinstance(item, Mapping) and item.get("type") in {"tool-result", "tool_result"}
        ]
        if isinstance(content, list)
        else []
    )
    rows = rows or [(data, ())]
    rows = [
        (row, path) for row, path in rows if _string(row, "toolCallId", "callId", "id") == call_id
    ]
    if len(rows) != 1:
        return
    row, path = rows[0]
    blocks = row.get("content")
    values = (
        [
            (block.get("text"), (*path, "content", index, "text"))
            for index, block in enumerate(blocks)
            if isinstance(block, Mapping) and block.get("type") == "text"
        ]
        if isinstance(blocks, list)
        else [(row[key], (*path, key)) for key in ("value", "result", "output") if key in row]
    )
    if "structuredContent" in row:
        # DSH may retain both the typed output and its identical text render.
        # They are two representations of one result, not two executions.
        typed_value = row["structuredContent"]
        for rendered, _location in values:
            try:
                decoded = (
                    json.loads(rendered, object_pairs_hook=_unique_pairs)
                    if isinstance(rendered, str)
                    else rendered
                )
            except (ValueError, TypeError):
                return
            if decoded != typed_value:
                return
        values = [(typed_value, (*path, "structuredContent"))]
    for value, location in values:
        if isinstance(value, str):
            oversized = len(value.encode()) > _MAX_EMBEDDED_JSON_BYTES
            if oversized and not allow_full_perpetual_review:
                continue
            try:
                value = json.loads(value, object_pairs_hook=_unique_pairs)
            except (ValueError, TypeError):
                continue
            # Only the V4 independent review reader opts into full native proof.
            # Other tool payloads and legacy review protocols keep their bound.
            if oversized and (
                not isinstance(value, Mapping)
                or value.get("schema_version") != "decision-hub-frozen-review-result.v4"
            ):
                continue
            location = (*location, None)
        yield value, location


def extract_attested_decision_synthesis_captures(
    notifications: Sequence[DshSdkNotification],
    *,
    expected_session_id: str,
) -> list[AttestedDecisionSynthesisCapture]:
    """New protocol only: a complete wrapper and the exact matching root call.

    Never fall back to a nested old candidate or final model prose. Selection
    authority still belongs to the frozen request and the downstream native reader.
    """
    calls: dict[str, list[tuple[int, Mapping[str, object]]]] = {}
    results: dict[str, list[int]] = {}
    events_by_seq: dict[int, list[int]] = {}
    for index, notification in enumerate(notifications):
        event = _session_event(notification)
        if event is None or event[2] != expected_session_id:
            continue
        kind, data, _ = event
        seq = _event_seq(notification)
        if seq is not None:
            events_by_seq.setdefault(seq, []).append(index)
        identity = _typed_tool_identity(data, item_type="tool-call")
        if kind == "tool/call" and identity is not None and identity[1] == _SYNTHESIS_TOOL_NAME:
            calls.setdefault(identity[0], []).append((index, data))
        elif kind in {"tool/result", "tool/call-result"}:
            call_id = _typed_tool_result_call_id(data)
            if call_id is not None:
                results.setdefault(call_id, []).append(index)
    # Include replacements even when an attacker changes their reported call ID.
    for index, notification in enumerate(notifications):
        event = _session_event(notification)
        if event is None or event[2] != expected_session_id or event[0] != "tool/result":
            continue
        surface = notification.payload["event"].get("surfaceOp")
        if not isinstance(surface, Mapping) or type(surface.get("start")) is not int:
            continue
        for original_index in events_by_seq.get(surface["start"], []):
            original = _session_event(notifications[original_index])
            target = _typed_tool_result_call_id(original[1]) if original else None
            if target in calls and index not in results.setdefault(target, []):
                results[target].append(index)
    captures = []
    for call_id, starts in calls.items():
        indexes = results.get(call_id, [])
        if len(starts) == 1:
            originals = []
            invalid_replacement = False
            for index in indexes:
                raw_event = notifications[index].payload["event"]
                if "surfaceOp" not in raw_event or raw_event["surfaceOp"] == "append":
                    originals.append(index)
                elif not _pruned_review_result_projection(
                    notifications,
                    call_index=starts[0][0],
                    result_index=index,
                    expected_session_id=expected_session_id,
                    events_by_seq=events_by_seq,
                ):
                    invalid_replacement = True
            if invalid_replacement:
                raise AgentExecutionError(
                    "dsh_protocol_invalid",
                    "Decision synthesis display replacement is unproved",
                    cause_code="decision_synthesis_pair_invalid",
                )
            indexes = originals
        if len(starts) != 1 or len(indexes) != 1 or indexes[0] <= starts[0][0]:
            raise AgentExecutionError(
                "dsh_protocol_invalid",
                "Decision synthesis Tool pair is ambiguous",
                cause_code="decision_synthesis_pair_invalid",
            )
        call_index, call_data = starts[0]
        index = indexes[0]
        event = _session_event(notifications[index])
        assert event is not None
        if _tool_result_failed(event[1]):
            continue
        try:
            returned = list(_actual_return_payloads(event[1], call_id))
            if len(returned) != 1:
                raise ValueError("return_ambiguous")
            wrapper, path = returned[0]
            if (
                not isinstance(wrapper, Mapping)
                or set(wrapper) != {"schema_version", "candidate", "review_gate"}
                or wrapper["schema_version"] != "decision-hub-synthesis-tool-result.v2"
            ):
                raise ValueError("wrapper_invalid")
            synthesis = ResearchDecisionSynthesisV1.model_validate(wrapper["candidate"])
            rows = list(_typed_content_items(call_data, "tool-call")) or [call_data]
            rows = [
                row
                for row in rows
                if _typed_tool_identity(row, item_type="tool-call")
                == (call_id, _SYNTHESIS_TOOL_NAME)
            ]
            if len(rows) != 1:
                raise ValueError("call_ambiguous")
            arguments = rows[0].get("arguments")
            if isinstance(arguments, str):
                arguments = json.loads(arguments, object_pairs_hook=_unique_pairs)
            if (
                not isinstance(arguments, dict)
                or set(arguments) != {"candidate"}
                or normalize_action_synthesis(arguments["candidate"]) != synthesis
            ):
                raise ValueError("call_candidate_changed")
            raw_path = ("payload", "event", "data", *path, "candidate")
            candidate = ResearchSynthesisCandidate.model_validate(
                synthesis.research_candidate_v1.model_dump(mode="json")
            )

            def digest(value: object) -> str:
                return hashlib.sha256(
                    json.dumps(
                        value,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    ).encode()
                ).hexdigest()

            projection = AttestedSynthesisCapture(
                candidate,
                call_id,
                call_index,
                index,
                0,
                (*raw_path, "research_candidate_v1"),
                digest(candidate.model_dump(mode="json")),
                _event_seq(notifications[call_index]),
                _event_seq(notifications[index]),
            )
            captures.append(
                AttestedDecisionSynthesisCapture(
                    synthesis, projection, raw_path, digest(wrapper["candidate"])
                )
            )
        except (ValueError, TypeError, KeyError, ValidationError) as exc:
            raise AgentExecutionError(
                "dsh_protocol_invalid",
                "Decision synthesis Tool proof is invalid",
                cause_code="decision_synthesis_attestation_invalid",
            ) from exc
    return sorted(captures, key=lambda item: item.projection.result_event_index)


class _IndependentReviewReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["independent-review-receipt.v1"]
    role: Literal["independent_reviewer"]
    target_claim: Annotated[str, Field(min_length=1)]
    evidence_refs: Annotated[list[str], Field(min_length=1)]
    challenge: Annotated[str, Field(min_length=1)]
    resolution: Literal["accepted", "bounded", "requires_new_evidence", "rejected"]
    limitations: list[str] = Field(default_factory=list)

    @field_validator("limitations", mode="before")
    @classmethod
    def _normalize_single_limitation(cls, value: object) -> object:
        # The product contract remains an array. A reviewer may naturally emit
        # one limitation as a JSON string; normalize that losslessly instead of
        # discarding an otherwise valid, evidence-bound independent receipt.
        return [value] if isinstance(value, str) else value


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
    for result in [*extract_capability_results(notifications),
                   *extract_server_baseline_results(notifications, request,
                       expected_session_id=candidates[0].research_session_id)]:
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
    for result in [*extract_capability_results(notifications),
                   *extract_server_baseline_results(notifications, request,
                       expected_session_id=research_session_id)]:
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
        if tool_name is None or not any(tool_name.endswith(name) for name in _RESEARCH_TOOL_NAMES):
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


def extract_server_baseline_results(
    notifications: Sequence[DshSdkNotification], request: ResearchSessionRequest,
    *, expected_session_id: str,
) -> list[ResearchCapabilityResult]:
    """Host plugin projection only; Kernel must authenticate each batch original.

    Server results remain separate from native Tool pairs and tool-call counts.
    A model/tool text block that copies this JSON never enters this projection.
    """
    from packages.kernel.decision_hub_kernel.application.research_method import canonical_recipe
    from packages.kernel.decision_hub_kernel.ports.research_baseline import BaselineDelivery
    from packages.kernel.decision_hub_kernel.ports.research_synthesis_protocol import (
        DECISION_OUTPUT_SCHEMA_REF,
    )
    if (request.output_schema_ref != DECISION_OUTPUT_SCHEMA_REF
            or request.execution_mode != "live" or request.research_mode != "current_decision"):
        return []
    batches = {}
    for notification in notifications:
        event = _session_event(notification)
        if event is None:
            continue
        kind, message, session_id = event
        if (kind != "user/message" or session_id != expected_session_id
                or message.get("source") != {"kind": "plugin", "plugin": "decision-hub-protected-context"}):
            continue
        blocks = message.get("content")
        if (not isinstance(blocks, list) or len(blocks) != 2
                or not isinstance(blocks[1], Mapping) or blocks[1].get("type") != "text"):
            continue
        text = blocks[1].get("text")
        if not isinstance(text, str) or len(text.encode()) > _MAX_EMBEDDED_JSON_BYTES:
            continue
        try:
            body = json.loads(text, object_pairs_hook=_unique_pairs)
            if (body.get("schema_version") not in {"decision-hub-protected-context.v2", "decision-hub-protected-context.v3", "decision-hub-protected-context.v4"}
                    or body.get("run_id") != request.run_id
                    or body.get("root_session_id") != expected_session_id
                    or body.get("snapshot_id") != message.get("id")
                    or body.get("request_id") != request.request_id):
                continue
            baseline_body = body.get("server_baseline")
            if body.get("schema_version") in {"decision-hub-protected-context.v3", "decision-hub-protected-context.v4"}:
                from packages.kernel.decision_hub_kernel.application.research_baseline_projection import (
                    restore_baseline_projection,
                )

                semantic_facts = body.get("facts")
                if body.get("schema_version") == "decision-hub-protected-context.v4":
                    from packages.kernel.decision_hub_kernel.application.research_baseline_projection import (
                        restore_fact_attributes,
                    )

                    semantic_facts = restore_fact_attributes(semantic_facts, body.get("shared_fact_attributes"))
                baseline_body = restore_baseline_projection(
                    baseline_body, facts=semantic_facts,
                    evidence_candidates=body.get("evidence_candidates"),
                )
            delivery = BaselineDelivery.model_validate_json(canonical_recipe(baseline_body))
            if (delivery.run_id != request.run_id
                    or delivery.research_session_id != expected_session_id
                    or delivery.request_hash != body.get("request_hash")
                    or delivery.batch_ref.role != "research_baseline_batch:1"
                    or delivery.batch_ref.schema_version != "research-baseline-batch.v1"
                    or delivery.batch_ref.recorded_at != delivery.recorded_at):
                continue
            batch = {**delivery.model_dump(mode="json", exclude={"batch_ref"}),
                     "schema_version": "research-baseline-batch.v1"}
            if hashlib.sha256(canonical_recipe(batch).encode()).hexdigest() != delivery.batch_ref.payload_hash:
                continue
        except (ValueError, TypeError, AttributeError):
            continue
        old = batches.get(delivery.batch_ref.output_id)
        if old is not None and old != delivery:
            raise AgentExecutionError("dsh_baseline_unattested", "Conflicting Host baseline originals")
        batches[delivery.batch_ref.output_id] = delivery
    return [result for delivery in batches.values() for result in delivery.results]


def extract_attested_synthesis_candidates(
    notifications: Sequence[DshSdkNotification],
    *,
    expected_session_id: str,
) -> list[ResearchSynthesisCandidate]:
    """Return successful synthesis captures proven by an exact DSH Tool call/result pair."""

    return [
        capture.candidate
        for capture in extract_attested_synthesis_captures(
            notifications,
            expected_session_id=expected_session_id,
        )
    ]


def extract_attested_synthesis_captures(
    notifications: Sequence[DshSdkNotification],
    *,
    expected_session_id: str,
) -> list[AttestedSynthesisCapture]:
    """The original matching traversal, with its exact indices; no generation inference."""

    calls: dict[tuple[str, str], tuple[str, int, int | None]] = {}
    captures: list[AttestedSynthesisCapture] = []
    for index, notification in enumerate(notifications):
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
                calls[(session_id, call_id)] = (tool_name, index, _event_seq(notification))
            continue
        if event_type not in {"tool/result", "tool/call-result"}:
            continue
        call_id = _typed_tool_result_call_id(data)
        if call_id is None:
            continue
        # The result payload is untrusted. Resolve the tool name only from the
        # preceding typed call event with the same Session and call identity.
        call = calls.get((session_id, call_id))
        if call is None or call[0] != _SYNTHESIS_TOOL_NAME:
            continue
        if _tool_result_failed(data):
            continue
        if any(_schema_payloads_with_path(data, "decision-hub-synthesis-tool-result.v3")) or any(_schema_payloads_with_path(data, "decision-hub-synthesis-tool-result.v2")) or any(
            _schema_payloads_with_path(data, "research-decision-synthesis.v1")
        ):
            raise AgentExecutionError(
                "dsh_protocol_invalid",
                "Decision synthesis requires its frozen output protocol",
                cause_code="decision_synthesis_protocol_mismatch",
            )
        # A malformed new product wrapper cannot fall back to final model prose
        # as a legacy submission. Only this exact successful synthesis pair is
        # inspected; quoted candidate fields and other tools confer no authority.
        wrappers = [
            payload
            for payload, path in _schema_payloads_with_path(
                data, "decision-hub-synthesis-tool-result.v1"
            )
            if not any(
                part in {"candidate", "candidate_json", "review_gate"}
                for part in path
                if isinstance(part, str)
            )
        ]
        for wrapper in wrappers:
            try:
                if set(wrapper) != {"schema_version", "candidate", "review_gate"}:
                    raise ValueError("wrapper_fields")
                ResearchSynthesisCandidate.model_validate(wrapper["candidate"])
            except (ValueError, ValidationError) as exc:
                raise AgentExecutionError(
                    "dsh_protocol_invalid",
                    "Final review Tool proof is invalid",
                    cause_code="final_review_gate_invalid",
                ) from exc
        for occurrence, (payload, path) in enumerate(
            _schema_payloads_with_path(data, "research-synthesis-candidate.v1")
        ):
            try:
                candidate = ResearchSynthesisCandidate.model_validate(payload)
            except ValidationError:
                continue
            encoded = json.dumps(
                candidate.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            captures.append(
                AttestedSynthesisCapture(
                    candidate,
                    call_id,
                    call[1],
                    index,
                    occurrence,
                    ("payload", "event", "data", *path),
                    hashlib.sha256(encoded.encode()).hexdigest(),
                    call[2],
                    _event_seq(notification),
                )
            )
    return captures


def _event_seq(notification: DshSdkNotification) -> int | None:
    event = notification.payload.get("event")
    value = event.get("seq") if isinstance(event, Mapping) else None
    return value if type(value) is int and value >= 0 else None


def extract_attested_review_receipts(
    notifications: Sequence[DshSdkNotification],
    *,
    expected_session_id: str,
    allowed_evidence_ids: frozenset[str],
    fallback_created_at: datetime,
) -> list[AttestedReviewReceipt]:
    """Project strict reviewer receipts from matched native subagent calls.

    The subagent text remains an internal receipt until the result mapper binds
    it to one immutable result hash. Only a successful result matched to a
    preceding call in the same Session, with a closed shape and retained
    Evidence IDs, can reach that binding step.
    """

    calls: set[tuple[str, str]] = set()
    reviews: dict[str, AttestedReviewReceipt] = {}
    for notification in notifications:
        event = _session_event(notification)
        if event is None:
            continue
        event_type, data, session_id = event
        if session_id != expected_session_id:
            continue
        if event_type == "tool/call":
            identity = _typed_tool_identity(data, item_type="tool-call")
            if identity is not None and identity[1] in _SUBAGENT_TOOL_NAMES:
                calls.add((expected_session_id, identity[0]))
            continue
        if event_type not in {"tool/result", "tool/call-result"}:
            continue
        call_id = _typed_tool_result_call_id(data)
        if call_id is None or (session_id, call_id) not in calls or _tool_result_failed(data):
            continue
        # A V2 wrapper preserves the rejected first output as audit evidence.
        # It must never also promote that output through the legacy V1 reader.
        if any(
            wrapper.get("schema_version")
            in {"decision-hub-frozen-review-result.v2", "decision-hub-frozen-review-result.v3"}
            for wrapper in _returned_review_wrappers(data, call_id)
        ):
            continue
        for payload in _schema_payloads(data, _REVIEW_RECEIPT_SCHEMA):
            try:
                receipt = _IndependentReviewReceipt.model_validate(payload)
            except ValidationError:
                continue
            refs = receipt.evidence_refs
            if len(refs) != len(set(refs)) or not set(refs) <= allowed_evidence_ids:
                continue
            if any(not item.strip() for item in receipt.limitations):
                continue
            digest = independent_review_receipt_hash(
                role=receipt.role,
                target_claim=receipt.target_claim,
                evidence_refs=refs,
                challenge=receipt.challenge,
                resolution=receipt.resolution,
                limitations=receipt.limitations,
            )
            reviews[digest] = AttestedReviewReceipt(
                receipt_hash=digest,
                role=receipt.role,
                target_claim=receipt.target_claim,
                evidence_refs=refs,
                challenge=receipt.challenge,
                resolution=receipt.resolution,
                limitations=receipt.limitations,
                created_at=_event_time(notification, fallback_created_at),
            )
    legacy = list(reviews.values())
    return [
        *legacy,
        *extract_structured_review_receipts(
            notifications,
            expected_session_id=expected_session_id,
            allowed_evidence_ids=allowed_evidence_ids,
            fallback_created_at=fallback_created_at,
        ),
    ]


def structured_review_references_valid(
    receipt: IndependentReviewReceiptV2,
    allowed: frozenset[str],
) -> bool:
    """Finding consistency only; accepted/PIT effects are checked by Kernel."""

    def valid(refs: list[str]) -> bool:
        return len(refs) == len(set(refs)) and set(refs) <= allowed

    if (
        not valid(receipt.evidence_refs)
        or not receipt.challenge.strip()
        or not receipt.reviewer_version.strip()
        or any(not item.strip() for item in receipt.limitations)
        or len(receipt.issues) != len({item.issue_id for item in receipt.issues})
    ):
        return False
    if not receipt.issues and receipt.resolution != "accepted":
        return False
    return all(
        issue.issue_id.strip()
        and issue.reason.strip()
        and valid(issue.evidence_refs)
        and valid(issue.resolution_evidence_refs)
        and len(issue.blocking_scope) == len(set(issue.blocking_scope))
        and (issue.status != "answered" or issue.disposition == "accepted")
        and (issue.status != "rejected" or issue.disposition == "rebutted")
        and (issue.status not in {"open", "deferred"} or issue.disposition == "unresolved")
        for issue in receipt.issues
    )


def _returned_review_wrappers(data: Mapping[str, object], call_id: str):
    """Only the actual root Tool return, never prose quoting another wrapper."""
    typed = list(_typed_content_items(data, "tool-result"))
    rows = typed or [data]
    rows = [row for row in rows if _string(row, "toolCallId", "callId", "id") == call_id]
    if len(rows) != 1:
        return
    row = rows[0]
    values: list[object] = []
    content = row.get("content")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, Mapping) and block.get("type") == "text":
                values.append(block.get("text"))
    else:
        values.extend(row[key] for key in ("value", "result", "output") if key in row)
    for value in values:
        if isinstance(value, str):
            if len(value.encode("utf-8")) > _MAX_EMBEDDED_JSON_BYTES:
                continue
            try:
                # Duplicated JSON keys cannot attest one exact product return.
                value = json.loads(value, object_pairs_hook=_unique_pairs)
            except (ValueError, TypeError):
                continue
        if isinstance(value, Mapping) and value.get("schema_version") in {
            "decision-hub-frozen-review-result.v1",
            "decision-hub-frozen-review-result.v2",
            "decision-hub-frozen-review-result.v3",
        }:
            yield value


def _unique_pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            raise ValueError("review_duplicate_json_key")
        result[key] = value
    return result


def _validated_review_repair(
    value: Mapping[str, object],
    allowed_evidence_ids: frozenset[str],
) -> bool:
    repair = value.get("schema_repair")
    if repair is None:
        return True
    if not isinstance(repair, Mapping) or set(repair) != {
        "schema_version",
        "attempt",
        "child_parent_session_id",
        "invalid_output_json",
        "invalid_output_sha256",
        "validation_errors",
        "repair_prompt",
        "repair_prompt_sha256",
        "event_start_seq",
        "event_end_seq",
        "events_json",
        "events_sha256",
    }:
        return False
    binding = value.get("binding")
    if (
        repair.get("schema_version") != "decision-hub-review-schema-repair.v1"
        or type(repair.get("attempt")) is not int
        or repair.get("attempt") != 1
        or not isinstance(binding, Mapping)
        or repair.get("child_parent_session_id") != binding.get("sessionId")
        or not isinstance(repair.get("validation_errors"), list)
        or not 1 <= len(cast(list[object], repair["validation_errors"])) <= 16
        or any(
            not isinstance(item, str) or not 1 <= len(item) <= 512
            for item in cast(list[object], repair["validation_errors"])
        )
    ):
        return False
    for name in ("invalid_output", "repair_prompt", "events"):
        field = name if name == "repair_prompt" else name + "_json"
        text = repair.get(field)
        if (
            not isinstance(text, str)
            or len(text.encode()) > (32_000 if name == "repair_prompt" else 1_000_000)
            or hashlib.sha256(text.encode()).hexdigest() != repair.get(name + "_sha256")
        ):
            return False
    try:
        before = json.loads(
            cast(str, repair["invalid_output_json"]),
            object_pairs_hook=_unique_pairs,
        )
        events = json.loads(cast(str, repair["events_json"]), object_pairs_hook=_unique_pairs)
        if (
            not isinstance(before, dict)
            or set(before) != {"kind", "runId", "output"}
            or before["kind"] != "foreground"
            or before["runId"] != value["child_session_id"]
        ):
            return False
        invalid = list(_schema_payloads(before["output"], "independent-review-receipt.v2"))
        if len(invalid) == 1:
            try:
                receipt = IndependentReviewReceiptV2.model_validate(invalid[0])
                if receipt.target_claim == value[
                    "expected_target_claim"
                ] and structured_review_references_valid(receipt, allowed_evidence_ids):
                    return False
            except ValidationError:
                pass
        if (
            not isinstance(events, list)
            or not events
            or any(
                not isinstance(event, dict) or type(event.get("seq")) is not int for event in events
            )
            or any(events[i]["seq"] <= events[i - 1]["seq"] for i in range(1, len(events)))
            or events[0]["seq"] != repair["event_start_seq"]
            or events[-1]["seq"] != repair["event_end_seq"]
        ):
            return False
        prompts = [event for event in events if event.get("type") == "user/message"]
        ends = [event for event in events if event.get("type") == "turn/end"]
        if len(prompts) != 1 or len(ends) != 1 or ends[0]["data"]["reason"]["kind"] != "completed":
            return False
        message = prompts[0]["data"].get("message", prompts[0]["data"])
        if message.get("source", {}).get("kind") != "user" or message.get("content") != [
            {"type": "text", "text": repair["repair_prompt"]}
        ]:
            return False
        answers = [
            event["data"]["message"]["content"]
            for event in events
            if event.get("type") == "assistant/message" and event["data"]["message"].get("content")
        ]
        return bool(
            answers
            and answers[-1] == cast(Mapping[str, object], value["original_output"])["output"]
        )
    except (ValueError, TypeError, KeyError, IndexError, AttributeError):
        return False


def _validated_review_wrapper(
    value: Mapping[str, object],
    session_id: str,
    allowed_evidence_ids: frozenset[str] = frozenset(),
) -> bool:
    keys = {
        "schema_version",
        "binding",
        "freeze_call_id",
        "freeze_seq",
        "candidate_json",
        "candidate_sha256",
        "expected_target_claim",
        "actual_prompt",
        "actual_prompt_sha256",
        "child_session_id",
        "original_output",
    }
    if value.get("schema_version") in {
        "decision-hub-frozen-review-result.v2",
        "decision-hub-frozen-review-result.v3",
    }:
        keys.add("schema_repair")
    binding, output = value.get("binding"), value.get("original_output")
    if (
        set(value) != keys
        or not isinstance(binding, Mapping)
        or set(binding) != {"runId", "requestHash", "sessionId", "requestId", "generation"}
        or binding.get("sessionId") != session_id
        or type(binding.get("generation")) is not int
        or cast(int, binding["generation"]) < 1
        or not isinstance(output, Mapping)
        or set(output) != {"kind", "runId", "output"}
        or output.get("kind") != "foreground"
        or output.get("runId") != value.get("child_session_id")
        or not isinstance(value.get("child_session_id"), str)
        or not value["child_session_id"]
        or not isinstance(output.get("output"), list)
        or type(value.get("freeze_seq")) is not int
        or cast(int, value["freeze_seq"]) < 0
    ):
        return False
    for key in ("runId", "requestHash", "sessionId", "requestId"):
        if not isinstance(binding.get(key), str) or not binding[key]:
            return False
    candidate_json, prompt = value.get("candidate_json"), value.get("actual_prompt")
    if not isinstance(candidate_json, str) or not isinstance(prompt, str):
        return False
    if hashlib.sha256(candidate_json.encode()).hexdigest() != value.get(
        "candidate_sha256"
    ) or hashlib.sha256(prompt.encode()).hexdigest() != value.get("actual_prompt_sha256"):
        return False
    try:
        decision = value.get("schema_version") == "decision-hub-frozen-review-result.v3"
        model = ResearchDecisionSynthesisV1 if decision else ResearchSynthesisCandidate
        candidate = model.model_validate_json(candidate_json)
        research = candidate.research_candidate_v1 if decision else candidate
        lines = prompt.split("\n")
        starts = [i for i, line in enumerate(lines) if line == "BEGIN_FROZEN_REVIEW_CONTROL_JSON"]
        ends = [i for i, line in enumerate(lines) if line == "END_FROZEN_REVIEW_CONTROL_JSON"]
        if len(starts) != 1 or len(ends) != 1 or ends[0] != starts[0] + 2:
            return False
        control = json.loads(lines[starts[0] + 1], object_pairs_hook=_unique_pairs)
        return (
            (
                value.get("schema_version")
                not in {
                    "decision-hub-frozen-review-result.v2",
                    "decision-hub-frozen-review-result.v3",
                }
                or _validated_review_repair(value, allowed_evidence_ids)
            )
            and research.causal_case is not None
            and value["expected_target_claim"] == research.causal_case.thesis
            and isinstance(control, dict)
            and set(control)
            == {"schema_version", "candidate_sha256", "expected_target_claim", "frozen_candidate"}
            and control["schema_version"]
            == (
                "decision-hub-frozen-review-input.v2"
                if decision
                else "decision-hub-frozen-review-input.v1"
            )
            and control["candidate_sha256"] == value["candidate_sha256"]
            and control["expected_target_claim"] == value["expected_target_claim"]
            and model.model_validate(control["frozen_candidate"]) == candidate
        )
    except (ValueError, TypeError, ValidationError):
        return False


def _pruned_review_result_projection(
    notifications: Sequence[DshSdkNotification],
    *,
    call_index: int,
    result_index: int,
    expected_session_id: str,
    events_by_seq: Mapping[int, list[int]],
) -> bool:
    """Authenticate an official display replacement; retain the full original."""
    call = notifications[call_index].payload.get("event")
    replacement = notifications[result_index].payload.get("event")
    if not isinstance(call, Mapping) or not isinstance(replacement, Mapping):
        return False
    call_seq, seq, surface = call.get("seq"), replacement.get("seq"), replacement.get("surfaceOp")
    if (
        type(call_seq) is not int
        or type(seq) is not int
        or not isinstance(surface, Mapping)
        or set(surface) != {"op", "start", "end"}
        or surface.get("op") != "replace"
        or type(surface.get("start")) is not int
        or type(surface.get("end")) is not int
        or surface["start"] != surface["end"]
        or not 0 <= call_seq < surface["start"] < seq <= 2**53 - 1
        or replacement.get("type") != "tool/result"
        or replacement.get("sourceEventSeqs") != [surface["start"]]
    ):
        return False
    originals, prunes = events_by_seq.get(surface["start"], []), events_by_seq.get(seq - 1, [])
    if (
        len(originals) != 1
        or len(prunes) != 1
        or events_by_seq.get(call_seq) != [call_index]
        or events_by_seq.get(seq) != [result_index]
        or not call_index < originals[0] < prunes[0] < result_index
    ):
        return False
    original_notification, prune_notification = (
        notifications[originals[0]],
        notifications[prunes[0]],
    )
    original = cast(Mapping[str, object], original_notification.payload["event"])
    prune = cast(Mapping[str, object], prune_notification.payload["event"])
    original_data, replacement_data = original.get("data"), replacement.get("data")
    prune_data = prune.get("data")
    if (
        original_notification.payload.get("sessionId") != expected_session_id
        or prune_notification.payload.get("sessionId") != expected_session_id
        or original.get("type") != "tool/result"
        or original.get("surfaceOp") != "append"
        or original.get("sourceEventSeqs") != [call_seq]
        or prune.get("type") != "compaction/prune"
        or not isinstance(prune_data, Mapping)
        or prune_data.get("shadowedRange") != {"start": surface["start"], "end": surface["start"]}
        or prune_data.get("shadowedSeqs") != [surface["start"]]
        or not isinstance(original_data, Mapping)
        or not isinstance(replacement_data, Mapping)
        or type(original_data.get("turn")) is not int
        or type(original_data.get("step")) is not int
        or {key: value for key, value in original_data.items() if key != "message"}
        != {key: value for key, value in replacement_data.items() if key != "message"}
        or _tool_result_failed(original_data)
        or _tool_result_failed(replacement_data)
    ):
        return False
    before, after = original_data.get("message"), replacement_data.get("message")
    identity = _typed_tool_identity(call.get("data", {}), item_type="tool-call")
    if not isinstance(before, Mapping) or not isinstance(after, Mapping) or identity is None:
        return False
    source = before.get("source")
    if (
        not isinstance(before.get("id"), str)
        or not before["id"]
        or not isinstance(source, Mapping)
        or source.get("kind") != "tool"
        or source.get("callId") != identity[0]
        or {key: value for key, value in before.items() if key != "content"}
        != {key: value for key, value in after.items() if key != "content"}
        or not isinstance(before.get("content"), list)
        or len(before["content"]) != 1
        or not isinstance(after.get("content"), list)
        or len(after["content"]) != 1
    ):
        return False
    full_rows, display_rows = (
        list(_typed_content_items(original_data, "tool-result")),
        list(_typed_content_items(replacement_data, "tool-result")),
    )
    if len(full_rows) != 1 or len(display_rows) != 1:
        return False
    full, display = full_rows[0], display_rows[0]
    full_content, display_content = full.get("content"), display.get("content")
    if (
        _typed_tool_result_call_id(original_data) != identity[0]
        or _typed_tool_result_call_id(replacement_data) != identity[0]
        or {key: value for key, value in full.items() if key != "content"}
        != {key: value for key, value in display.items() if key != "content"}
        or not isinstance(full_content, list)
        or not isinstance(display_content, list)
        or not full_content
        or len(full_content) != len(display_content)
    ):
        return False
    shrunk = False
    for source_block, display_block in zip(full_content, display_content, strict=True):
        if not isinstance(source_block, Mapping) or not isinstance(display_block, Mapping):
            return False
        if source_block.get("type") != "text":
            if source_block != display_block:
                return False
            continue
        text, shortened = source_block.get("text"), display_block.get("text")
        if (
            not isinstance(text, str)
            or not isinstance(shortened, str)
            or len(shortened) > len(text)
            or {key: value for key, value in source_block.items() if key != "text"}
            != {key: value for key, value in display_block.items() if key != "text"}
        ):
            return False
        shrunk |= len(shortened) < len(text)
    return shrunk


def extract_structured_review_receipts(
    notifications: Sequence[DshSdkNotification],
    *,
    expected_session_id: str,
    allowed_evidence_ids: frozenset[str],
    fallback_created_at: datetime,
) -> list[AttestedReviewReceipt]:
    """Retain V2 only from the actual unique frozen-reviewer call/result pair.

    Candidate/gate/admission binding is rechecked from originals by the Kernel
    before any structured effect is projected. This transport parser grants none.
    """
    calls: dict[str, int] = {}
    results: dict[str, list[int]] = {}
    events_by_seq: dict[int, list[int]] = {}
    for index, notification in enumerate(notifications):
        event = _session_event(notification)
        if event is None or event[2] != expected_session_id:
            continue
        kind, data, _ = event
        raw_event = cast(Mapping[str, object], notification.payload["event"])
        native_seq = raw_event.get("seq")
        if type(native_seq) is int:
            events_by_seq.setdefault(native_seq, []).append(index)
        identity = _typed_tool_identity(data, item_type="tool-call")
        if kind == "tool/call" and identity is not None and identity[1] == "subagent_reviewer":
            if identity[0] in calls:
                calls[identity[0]] = -1
            else:
                calls[identity[0]] = index
        elif kind in {"tool/result", "tool/call-result"}:
            call_id = _typed_tool_result_call_id(data)
            if call_id in calls:
                results.setdefault(call_id, []).append(index)
    # A changed call ID must not hide a display replacement of this review.
    for index, notification in enumerate(notifications):
        event = _session_event(notification)
        if event is None or event[2] != expected_session_id or event[0] != "tool/result":
            continue
        raw_event = cast(Mapping[str, object], notification.payload["event"])
        surface = raw_event.get("surfaceOp")
        if not isinstance(surface, Mapping) or type(surface.get("start")) is not int:
            continue
        for original_index in events_by_seq.get(surface["start"], []):
            original = _session_event(notifications[original_index])
            target = _typed_tool_result_call_id(original[1]) if original is not None else None
            if target in calls and index not in results.setdefault(target, []):
                results[target].append(index)
    receipts: list[AttestedReviewReceipt] = []
    for call_id, indexes in results.items():
        call_index = calls[call_id]
        if call_index < 0:
            continue
        originals = []
        invalid_projection = False
        for index in indexes:
            raw_event = cast(Mapping[str, object], notifications[index].payload["event"])
            if "surfaceOp" not in raw_event or raw_event["surfaceOp"] == "append":
                originals.append(index)
            elif not _pruned_review_result_projection(
                notifications,
                call_index=call_index,
                result_index=index,
                expected_session_id=expected_session_id,
                events_by_seq=events_by_seq,
            ):
                invalid_projection = True
        if invalid_projection or len(originals) != 1 or originals[0] <= call_index:
            continue
        index = originals[0]
        notification = notifications[index]
        event = _session_event(notification)
        assert event is not None
        if _tool_result_failed(event[1]):
            continue
        wrappers = list(_returned_review_wrappers(event[1], call_id))
        if len(wrappers) != 1 or not _validated_review_wrapper(
            wrappers[0],
            expected_session_id,
            allowed_evidence_ids,
        ):
            continue
        wrapper = wrappers[0]
        output = cast(Mapping[str, object], wrapper["original_output"])
        payloads = list(_schema_payloads(output["output"], "independent-review-receipt.v2"))
        if len(payloads) != 1:
            continue
        try:
            receipt = IndependentReviewReceiptV2.model_validate(payloads[0])
        except ValidationError:
            continue
        if receipt.target_claim != wrapper[
            "expected_target_claim"
        ] or not structured_review_references_valid(receipt, allowed_evidence_ids):
            continue
        body = receipt.model_dump(mode="json")
        canonical = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        summary_hash = independent_review_receipt_hash(
            role=receipt.role,
            target_claim=receipt.target_claim,
            evidence_refs=receipt.evidence_refs,
            challenge=receipt.challenge,
            resolution=receipt.resolution,
            limitations=receipt.limitations,
        )
        receipts.append(
            AttestedReviewReceipt(
                receipt_hash=hashlib.sha256(canonical.encode()).hexdigest(),
                summary_hash=summary_hash,
                role=receipt.role,
                target_claim=receipt.target_claim,
                evidence_refs=receipt.evidence_refs,
                challenge=receipt.challenge,
                resolution=receipt.resolution,
                limitations=receipt.limitations,
                created_at=_event_time(notification, fallback_created_at),
                structured_receipt=receipt,
                tool_call_id=call_id,
                call_event_index=call_index,
                result_event_index=index,
                wrapper_json=json.dumps(
                    dict(wrapper), ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ),
            )
        )
    return receipts


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


def _event_time(notification: DshSdkNotification, fallback: datetime) -> datetime:
    event = notification.payload.get("event")
    if not isinstance(event, Mapping):
        return fallback
    milliseconds = event.get("time")
    if isinstance(milliseconds, (int, float)) and not isinstance(milliseconds, bool):
        try:
            return datetime.fromtimestamp(float(milliseconds) / 1000, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return fallback
    timestamp = event.get("timestamp")
    if isinstance(timestamp, str):
        try:
            return datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError:
            return fallback
    return fallback


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


def extract_capability_call_targets(
    notifications: Sequence[DshSdkNotification],
    *,
    expected_session_id: str,
) -> dict[str, tuple[str, str]]:
    """Bind a requirement to an actual research tool call, never to a plan."""
    targets: dict[str, tuple[str, str]] = {}
    for notification in notifications:
        event = _session_event(notification)
        if event is None:
            continue
        event_type, data, session_id = event
        if event_type != "tool/call" or session_id != expected_session_id:
            continue
        name = _tool_name(data)
        call_id = _tool_call_id(data)
        if not name or not any(name.endswith(item) for item in _RESEARCH_TOOL_NAMES):
            continue
        capability = _tool_argument_string(data, "capability_id")
        requirement = _tool_argument_string(data, "requirement_id")
        if call_id and capability and requirement:
            targets[call_id] = (capability, requirement)
    return targets


def _tool_request_id(data: Mapping[str, object]) -> str | None:
    return _tool_argument_string(data, "request_id", "requestId")


def _tool_argument_string(data: Mapping[str, object], *keys: str) -> str | None:
    """Read a bounded string only from structured tool-call arguments."""
    direct = _string(data, *keys)
    if direct is not None:
        return direct
    for item in _walk_mappings(data):
        arguments = item.get("arguments")
        if isinstance(arguments, Mapping):
            value = _string(arguments, *keys)
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
            value = _string(decoded, *keys)
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


def _schema_payloads(value: object, expected_schema_version: str) -> Iterable[Mapping[str, object]]:
    for payload, _path in _schema_payloads_with_path(value, expected_schema_version):
        yield payload


def synthesis_tool_result_wrappers(
    value: object,
    *,
    decision: bool = False,
) -> list[tuple[Mapping[str, object], tuple[str | int | None, ...]]]:
    """Locate product wrappers without granting authority to their contents."""
    return list(
        _schema_payloads_with_path(
            value,
            "decision-hub-synthesis-tool-result.v2"
            if decision
            else "decision-hub-synthesis-tool-result.v1",
        )
    )


def _schema_payloads_with_path(
    value: object,
    expected_schema_version: str,
    path: tuple[str | int | None, ...] = (),
) -> Iterable[tuple[Mapping[str, object], tuple[str | int | None, ...]]]:
    if isinstance(value, Mapping):
        mapping = cast(Mapping[str, object], value)
        if mapping.get("schema_version") == expected_schema_version:
            yield mapping, path
        for key, nested in mapping.items():
            # The closed product gate carries exact JSON bytes as a hash proof,
            # not a second synthesis submission. Its candidate is captured only
            # from the adjacent wrapper.candidate; legacy traversal is unchanged.
            if (
                expected_schema_version == "research-synthesis-candidate.v1"
                and mapping.get("schema_version") == "decision-hub-final-review-gate.v1"
                and key == "candidate_json"
            ):
                continue
            yield from _schema_payloads_with_path(nested, expected_schema_version, (*path, key))
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for index, nested in enumerate(value):
            yield from _schema_payloads_with_path(nested, expected_schema_version, (*path, index))
        return
    if not isinstance(value, str) or len(value.encode("utf-8")) > _MAX_EMBEDDED_JSON_BYTES:
        return
    stripped = value.strip()
    if not stripped.startswith("{"):
        return
    try:
        decoded = (
            json.loads(stripped, object_pairs_hook=_unique_pairs)
            if expected_schema_version == "independent-review-receipt.v2"
            else json.loads(stripped)
        )
    except (ValueError, TypeError):
        return
    yield from _schema_payloads_with_path(decoded, expected_schema_version, (*path, None))


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


def extract_attested_perpetual_synthesis_captures(
    notifications: Sequence[DshSdkNotification], *, expected_session_id: str,
    request: ResearchSessionRequest | None = None,
):
    """Separate V2 native protocol; never projects a legacy synthesis candidate."""
    from .perpetual_attestation import extract_attested_perpetual_synthesis_captures as extract
    return extract(notifications, expected_session_id=expected_session_id, request=request)

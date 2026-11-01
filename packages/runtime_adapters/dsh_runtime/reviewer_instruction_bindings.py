"""Owned Host instruction projections; sparse native events are not a surface proof."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import cast

from packages.contracts_py.decision_hub_contracts import DshSessionResult
from packages.contracts_py.decision_hub_contracts.generated.dsh_host_bridge import (
    DshInstructionMessage,
    DshReviewerInstructionBinding,
    DshReviewerInstructionBindings,
)

from .client import DshSdkNotification
from .tool_result_attestation import (
    _event_seq,  # pyright: ignore[reportPrivateUsage]
    _session_event,  # pyright: ignore[reportPrivateUsage]
    _typed_content_items,  # pyright: ignore[reportPrivateUsage]
    _typed_tool_identity,  # pyright: ignore[reportPrivateUsage]
)

RESERVED_METHOD = "decision-hub.reviewer-instruction-bindings"


def _request_id(run_id: str, request_hash: str, generation: int) -> str:
    identity = f"dsh-host-bridge.v1\0{run_id}\0{request_hash}" if generation == 1 else (
        f"dsh-host-turn.v1\0{run_id}\0{request_hash}\0{generation}"
    )
    return "req_" + hashlib.sha256(identity.encode()).hexdigest()


def _validate_projection(value: DshReviewerInstructionBindings) -> None:
    digest = hashlib.sha256(
        f"dsh-host-bridge.v1\0{value.run_id}\0{value.request_hash}".encode()
    ).hexdigest()
    if value.dsh_session_id != "dsh_" + digest or value.request_id != _request_id(
        value.run_id, value.request_hash, value.generation,
    ):
        raise ValueError("review_instruction_request_identity")
    calls: set[str] = set()
    sequences: set[int] = set()
    for row in value.bindings:
        if (
            row.reviewer_call_id in calls or row.reviewer_call_seq in sequences
            or row.parent_prefix_last_seq != row.reviewer_call_seq - 1
            or row.baseline_event.seq >= row.reviewer_call_seq
            or row.reviewer_generation > value.generation
            or row.reviewer_request_id != _request_id(
                value.run_id, value.request_hash, row.reviewer_generation,
            )
        ):
            raise ValueError("review_instruction_call_binding")
        calls.add(row.reviewer_call_id)
        sequences.add(row.reviewer_call_seq)
        data = row.baseline_event.data
        if (
            not data.id.strip() or not data.source.baselineIdentity.strip()
            or any(not change.scope.strip() or not change.path.strip()
                   for change in data.source.changes)
        ):
            raise ValueError("review_instruction_baseline_shape")


def notification_for_result(
    result: DshSessionResult, notifications: Sequence[DshSdkNotification],
) -> DshSdkNotification | None:
    """Only the typed result field can create this reserved, non-native metadata."""
    projection = result.reviewer_instruction_bindings
    if projection is None:
        return None
    projection = DshReviewerInstructionBindings.model_validate(
        projection.model_dump(mode="json"), strict=True,
    )
    _validate_projection(projection)
    if (
        projection.run_id != result.run_id
        or projection.dsh_session_id != result.dsh_session_id
        or projection.generation != result.generation
    ):
        raise ValueError("review_instruction_result_scope")
    for row in projection.bindings:
        matches = 0
        latest_rpc: object = None
        for notification in notifications:
            event = _session_event(notification)
            if event is None or event[2] != result.dsh_session_id:
                continue
            if event[0] == "user/message":
                source = event[1].get("source")
                if isinstance(source, dict) and source.get("kind") == "user":
                    latest_rpc = source.get("rpcId")
            if (
                event[0] != "tool/call"
                or _event_seq(notification) != row.reviewer_call_seq
            ):
                continue
            if latest_rpc != row.reviewer_request_id and not (
                latest_rpc is None
                and _historical_marker_omitted(projection, row, notifications)
            ):
                raise ValueError("review_instruction_native_request")
            items = list(_typed_content_items(event[1], "tool-call")) or [event[1]]
            matches += sum(
                _typed_tool_identity(item, item_type="tool-call")
                == (row.reviewer_call_id, "subagent_reviewer") for item in items
            )
        if matches != 1:
            raise ValueError("review_instruction_native_call")
    return DshSdkNotification(
        RESERVED_METHOD, cast(Mapping[str, object], projection.model_dump(mode="json")),
    )


def _historical_marker_omitted(
    projection: DshReviewerInstructionBindings, row: DshReviewerInstructionBinding,
    notifications: Sequence[DshSdkNotification],
) -> bool:
    """Retained old tools omit their prompt; a later current RPC bounds that exception."""
    if row.reviewer_generation >= projection.generation:
        return False
    current_rpc_seqs = []
    for notification in notifications:
        event = _session_event(notification)
        if (
            event is None or event[0] != "user/message"
            or event[2] != projection.dsh_session_id
        ):
            continue
        source = event[1].get("source")
        if (
            isinstance(source, dict) and source.get("kind") == "user"
            and source.get("rpcId") == projection.request_id
        ):
            current_rpc_seqs.append(_event_seq(notification))
    return (
        len(current_rpc_seqs) == 1 and type(current_rpc_seqs[0]) is int
        and current_rpc_seqs[0] > row.reviewer_call_seq
    )


def expected_review_instructions(
    notifications: Sequence[DshSdkNotification], *, call_id: str, call_seq: int | None,
    binding: Mapping[str, object],
) -> DshInstructionMessage | None:
    """Select by original native call identity, never a filtered notification index."""
    metadata = [note for note in notifications if note.method == RESERVED_METHOD]
    if not metadata:
        return None
    if len(metadata) != 1:
        raise ValueError("review_instruction_metadata_ambiguous")
    projection = DshReviewerInstructionBindings.model_validate(metadata[0].payload, strict=True)
    _validate_projection(projection)
    if (
        projection.run_id != binding["runId"]
        or projection.dsh_session_id != binding["sessionId"]
        or projection.request_hash != binding["requestHash"]
    ):
        raise ValueError("review_instruction_scope")
    matches = [row for row in projection.bindings
               if row.reviewer_call_id == call_id and row.reviewer_call_seq == call_seq
               and row.reviewer_generation == binding["generation"]
               and row.reviewer_request_id == binding["requestId"]]
    if len(matches) != 1:
        raise ValueError("review_instruction_expectation_missing")
    return matches[0].baseline_event.data


def matches_review_instructions(data: object, expected: DshInstructionMessage | None) -> bool:
    if expected is None or not isinstance(data, dict):
        return False
    source = data.get("source")
    if not isinstance(source, dict) or source.get("baseline") is not True:
        return False
    try:
        actual = DshInstructionMessage.model_validate(data, strict=True)
    except ValueError:
        return False
    return (
        bool(actual.id.strip()) and actual.source == expected.source
        and actual.content == expected.content
    )

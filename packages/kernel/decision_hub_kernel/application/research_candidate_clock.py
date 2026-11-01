"""Candidate information time from native originals, never model-authored as_of.

Only the new decision protocol uses this boundary. Completion, acquisition
accounting, writer publication and expiry keep their actual clocks.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts import DshSessionResult, ResearchSessionRequest

from ..ports.research_observation import ObservedExecutionRef
from ..ports.research_synthesis_protocol import synthesis_protocol
from .provider_input_receipts import sha
from .research_publication_binding import (
    observation_sha,
    read_mapped_observation,
    read_transport_observation,
    verify_selection_witness,
)
from .research_review_effects import _derive  # pyright: ignore[reportPrivateUsage]
from .research_transport_json import read_transport_events_json


@dataclass(frozen=True)
class CandidateClock:
    reference_at: datetime
    basis: Literal["reviewed_native_freeze", "unreviewed_native_call"]
    mapped_ref: ObservedExecutionRef
    transport_ref: ObservedExecutionRef
    call_event_index: int
    call_event_hash: str

    def state_value(self) -> dict[str, object]:
        return {
            "schema_version": "research-candidate-clock.v1",
            "reference_at": self.reference_at.isoformat(),
            "basis": self.basis,
            "mapped_ref": self.mapped_ref.model_dump(mode="json"),
            "transport_ref": self.transport_ref.model_dump(mode="json"),
            "call_event_index": self.call_event_index,
            "call_event_hash": self.call_event_hash,
        }


def _native_time(event: dict[str, object]) -> datetime:
    value = event.get("time")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("research_candidate_clock_native_time_invalid")
    try:
        return datetime.fromtimestamp(value / 1000, UTC)
    except (ValueError, OverflowError, OSError) as exc:
        raise ValueError("research_candidate_clock_native_time_invalid") from exc


def read_candidate_clock_in_session(
    session: Session,
    *,
    mapped_ref: ObservedExecutionRef,
    request: ResearchSessionRequest,
) -> CandidateClock | None:
    """Re-read exact transport/mapping/reviewer sources in the caller snapshot.

    No write, no fallback to a caller timestamp, no review permission granted.
    Graph also authenticates prepared request/context and its source readset.
    """
    if (
        synthesis_protocol(request.output_schema_ref) != "decision"
        or request.execution_mode == "replay"
    ):
        return None
    mapped = read_mapped_observation(session, mapped_ref)
    if mapped.selection.kind == "evidence_only" or mapped.result.synthesis_failure_code:
        return None
    raw = read_transport_observation(session, mapped.mapping_input_ref)
    payload = raw.reply
    if (
        not isinstance(payload, DshSessionResult)
        or raw.phase != "result"
        or raw.error is not None
        or raw.context.run_id != mapped.context.run_id
        or raw.context.generation != mapped.mapping_input_generation
        or raw.context.generation > mapped.context.generation
        or raw.context.original_task_request_hash != mapped.context.original_task_request_hash
        or request.run_id != mapped.context.run_id
        or request.request_id != mapped.context.request_id
        or observation_sha(request.model_dump(mode="json")) != mapped.context.execution_request_hash
        or raw.events_json_hash != sha(payload.events_json)
        or payload.result_hash != sha(payload.final_response + "\0" + payload.events_json)
    ):
        raise ValueError("research_candidate_clock_original_mismatch")
    verify_selection_witness(payload, mapped.selection)
    if raw.context.generation != mapped.context.generation:
        # The existing Graph authenticates both generations and can retain an
        # earlier synthesis after a failed continuation. This clock has no
        # producer-request proof for that path. Preserve its bounded legacy
        # timing instead of granting a new cutoff or breaking report recovery.
        return None
    if raw.context != mapped.context:
        raise ValueError("research_candidate_clock_original_mismatch")
    events = read_transport_events_json(payload.events_json)
    selected_index = mapped.selection.call_event_index
    if selected_index is None:
        raise ValueError("research_candidate_clock_call_missing")
    indexes: list[int] = []
    original = _derive(session, mapped_ref, mapped.result, raw.recorded_at, _freeze_indexes=indexes)
    if original is not None and len(indexes) != 1:
        raise ValueError("research_candidate_clock_review_missing")
    index = indexes[0] if original is not None else selected_index
    if not 0 <= index <= selected_index < len(events):
        raise ValueError("research_candidate_clock_sequence_invalid")
    try:
        call = events[index]["payload"]["event"]
        selected = events[selected_index]["payload"]["event"]
        if call["type"] != "tool/call" or selected["type"] != "tool/call":
            raise ValueError("research_candidate_clock_call_invalid")
        reference, submitted = _native_time(call), _native_time(selected)
    except (KeyError, TypeError, IndexError) as exc:
        raise ValueError("research_candidate_clock_call_invalid") from exc
    if not (
        payload.started_at <= reference <= submitted <= payload.finished_at
        and payload.finished_at <= min(request.deadline_at, request.pit_cutoff_at)
        and payload.finished_at <= raw.recorded_at
    ):
        raise ValueError("research_candidate_clock_time_order_invalid")
    return CandidateClock(
        reference,
        "reviewed_native_freeze" if original is not None else "unreviewed_native_call",
        mapped_ref,
        mapped.mapping_input_ref,
        index,
        observation_sha(events[index]),
    )


def candidate_clock_state(
    clock: CandidateClock | None, *, mapped_ref: ObservedExecutionRef,
) -> dict[str, object]:
    """Replace, never inherit, the prior round's clock when this one lacks proof."""
    if clock is not None:
        return clock.state_value()
    return {
        "schema_version": "research-candidate-clock.v1",
        "status": "unavailable",
        "reason": "same_generation_candidate_clock_unavailable",
        "mapped_ref": mapped_ref.model_dump(mode="json"),
    }

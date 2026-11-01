"""Closed output protocol selector from the original frozen research request.

This selects a wire shape, never admission, review, or financial qualification.
New model text and mutable environment settings cannot switch an existing Run.
"""

from typing import Literal
from datetime import datetime

LEGACY_OUTPUT_SCHEMA_REF = "agentic_research.schema.yaml#/$defs/research_session_result"
DECISION_OUTPUT_SCHEMA_REF = "research_signal.schema.yaml#/$defs/research_decision_synthesis_v1"
V2_DECISION_OUTPUT_SCHEMA_REF = "decision_product_v2.schema.yaml#/$defs/research_decision_synthesis_v2"
V3_DECISION_OUTPUT_SCHEMA_REF = "decision_product_v2.schema.yaml#/$defs/research_decision_synthesis_v3"
PERPETUAL_OUTPUT_SCHEMA_REFS = frozenset({V2_DECISION_OUTPUT_SCHEMA_REF, V3_DECISION_OUTPUT_SCHEMA_REF})
# Existing evaluation requests persisted this named v1 alias.
LEGACY_OUTPUT_SCHEMA_REFS = frozenset({LEGACY_OUTPUT_SCHEMA_REF, "research-session-result.v1"})


def synthesis_protocol(output_schema_ref: str) -> Literal["legacy", "decision", "decision_v2"]:
    if output_schema_ref in LEGACY_OUTPUT_SCHEMA_REFS:
        return "legacy"
    if output_schema_ref == DECISION_OUTPUT_SCHEMA_REF:
        return "decision"
    if output_schema_ref in PERPETUAL_OUTPUT_SCHEMA_REFS:
        return "decision_v2"
    raise ValueError("research_synthesis_protocol_unsupported")


def upcoming_perpetual_event(admission: dict, *, as_of: datetime) -> dict | None:
    """Select an exact upcoming entry from caller-authenticated admission data only.

    Identity/hash checks here are local consistency, never schedule authentication.
    The existing prepared-admission reader must authenticate its source first.
    """
    import hashlib
    import json

    agenda = admission.get("event_agenda")
    if agenda is None:
        return None
    from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
        EventAgendaSnapshotV1,
    )

    parsed = EventAgendaSnapshotV1.model_validate(agenda)
    canonical = json.dumps({key: value for key, value in agenda.items() if key != "payload_hash"},
                           sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    context = admission["context"]
    if (hashlib.sha256(canonical.encode()).hexdigest() != agenda["payload_hash"]
            or agenda["instrument_refs"] != [context["instrument_ref"]]
            or not parsed.window_start <= parsed.as_of <= as_of < parsed.window_end):
        raise ValueError("perpetual_event_output_agenda_binding_mismatch")
    rows = [row for row, typed in zip(agenda["entries"], parsed.entries, strict=True)
            if typed.time_precision == "exact" and typed.scheduled_at is not None
            and typed.stage_id != "meeting_date"
            and typed.known_at <= parsed.as_of and as_of < typed.scheduled_at <= parsed.window_end]
    return min(rows, key=lambda row: (datetime.fromisoformat(row["scheduled_at"]), row["event_key"], row["stage_id"])) if rows else None


def perpetual_output_schema_for_admission(admission: dict, *, as_of: datetime) -> str:
    return V3_DECISION_OUTPUT_SCHEMA_REF if upcoming_perpetual_event(admission, as_of=as_of) is not None else V2_DECISION_OUTPUT_SCHEMA_REF

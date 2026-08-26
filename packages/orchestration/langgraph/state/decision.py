from __future__ import annotations

from typing import TypedDict


class DecisionState(TypedDict, total=False):
    run_id: str
    event_id: str
    snapshot_id: str
    evidence_ids: list[str]
    text: str
    envelope: dict[str, object]
    candidate: dict[str, object]
    gate_status: str
    artifact_id: str
    error_code: str

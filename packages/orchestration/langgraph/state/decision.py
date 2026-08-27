from __future__ import annotations

from typing import TypedDict


class DecisionState(TypedDict, total=False):
    run_id: str
    event_id: str
    snapshot_id: str
    deadline_at: str
    policy_output_id: str
    counter_output_id: str
    candidate_output_id: str
    gate_status: str
    artifact_id: str
    error_code: str

from __future__ import annotations

from typing import TypedDict


class AgenticResearchState(TypedDict, total=False):
    # JSON-compatible projections keep LangGraph checkpoints portable across
    # Python workers and avoid storing live SDK/session objects in state.
    request: dict[str, object]
    requirements: list[dict[str, object]]
    evidence: list[dict[str, object]]
    facts: list[dict[str, object]]
    rounds: list[dict[str, object]]
    latest_result: dict[str, object]
    # Runtime-owned marker: a rejected synthesis can retain evidence without
    # being eligible for directional publication.
    synthesis_failure_code: str | None
    # A later DSH generation may fail after an earlier attested round. The
    # product retains the exact failure while deterministically finalizing the
    # last trusted synthesis as research_only.
    continuation_failure: dict[str, object]
    coverage: dict[str, object]
    current_round: int
    previous_evidence_ids: list[str]
    new_evidence_ids: list[str]
    attempted_capabilities_by_requirement: dict[str, list[str]]
    retryable_failures: int
    total_tool_calls: int
    total_subagents: int
    total_tokens: int | None
    estimated_cost_usd: float | None
    decision_cutoff_at: str
    stop_code: str
    stop_detail: str
    decision_snapshot_id: str
    final_result: dict[str, object]

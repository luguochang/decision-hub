"""Capture actual Graph input; never return a successful research result."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from packages.contracts_py.decision_hub_contracts import (
    EvidenceCandidate,
    ResearchSessionRequest,
    ResearchSessionResult,
)
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchEvidenceService,
)
from packages.kernel.decision_hub_kernel.persistence.db import SnapshotRecord, as_utc
from packages.kernel.decision_hub_kernel.ports.research import ResearchTraceSink
from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError
from packages.orchestration.langgraph.graphs.agentic_research_graph import (
    build_agentic_research_graph,
    initial_research_state,
)
from tests.research.test_full_method_v2_bundle import (
    PreparedCase,
    _factory,  # pyright: ignore[reportPrivateUsage]
    _rows,  # pyright: ignore[reportPrivateUsage]
    forbid_external_connections,  # pyright: ignore[reportUnusedImport]  # noqa: F401
    prepared_case,  # pyright: ignore[reportUnusedImport]  # noqa: F401
)


class CaptureOnlyRuntime:
    runtime_id = "controlled-context-capture"
    runtime_version = "controlled-context-capture.v1"
    profile_ref = "controlled-context-capture.v1"

    def __init__(self) -> None:
        self.requests: list[ResearchSessionRequest] = []

    async def execute(
        self, request: ResearchSessionRequest, trace_sink: ResearchTraceSink | None = None
    ) -> ResearchSessionResult:
        self.requests.append(request)
        raise AgentExecutionError(
            "pc_context_capture_stop", "Controlled capture stops before any model or result"
        )

    async def close(self) -> None:
        return None


def _source(case: PreparedCase) -> tuple[ResearchSessionRequest, EvidenceCandidate, datetime]:
    # Actual ingestion, Scope, Snapshot and live Factory produce this request.
    # The replay projection below is deliberately controlled in memory to model
    # a pre-fix late-input Task. It is never written over the native Task.
    live = _factory(case, execution_mode="live").build(case.event_id, case.run_id)
    library_input = next(
        item for item in live.input_evidence if item.source_id.startswith("research-library:")
    )
    with case.database.session() as session:
        snapshot = session.get(SnapshotRecord, live.trigger_snapshot_id)
        assert snapshot is not None
        cutoff = as_utc(snapshot.cutoff_at)
    assert cutoff is not None and library_input.received_at > cutoff
    candidate = EvidenceCandidate.model_validate(
        library_input.model_dump(mode="json")
        | {
            "evidence_id": f"controlled-candidate:{library_input.evidence_id}",
            "requirement_id": live.evidence_requirements[0].requirement_id,
            "structured_payload_ref": None,
            "tool_call_id": None,
            "research_session_id": f"controlled:{case.run_id}",
            "round": 1,
            "quality": "accepted",
            "freshness_status": "unknown",
            "conflict_group": None,
        }
    )
    return live, candidate, cutoff


def _capture(
    case: PreparedCase,
    request: ResearchSessionRequest,
    candidates: list[EvidenceCandidate],
    **kwargs: Any,
) -> ResearchSessionRequest:
    runtime = CaptureOnlyRuntime()
    graph = build_agentic_research_graph(
        runtime, ResearchEvidenceService(case.database), **kwargs
    )
    state = initial_research_state(request)
    state["evidence"] = [item.model_dump(mode="json") for item in candidates]
    with pytest.raises(AgentExecutionError) as stopped:
        asyncio.run(graph.ainvoke(state))
    assert stopped.value.error_code == "pc_context_capture_stop"
    assert len(runtime.requests) == 1
    return runtime.requests[0]


@pytest.mark.parametrize("path", ["input", "candidate"])
def test_replay_capture_never_hands_late_material_to_runtime(
    prepared_case: PreparedCase,  # noqa: F811 -- imported pytest fixture
    tmp_path: Path,
    path: str,
) -> None:
    case = prepared_case
    live, candidate, cutoff = _source(case)
    late_ids = {
        item.evidence_id
        for item in live.input_evidence
        if item.source_id.startswith("research-library:")
    }
    replay = live.model_copy(update={"execution_mode": "replay", "pit_cutoff_at": cutoff})
    candidates = []
    if path == "candidate":
        replay = replay.model_copy(
            update={
                "input_evidence": [
                    item for item in replay.input_evidence if item.evidence_id not in late_ids
                ],
                "evidence_refs": [item for item in replay.evidence_refs if item not in late_ids],
            }
        )
        candidates = [candidate]
        late_ids = {candidate.evidence_id}
    original = replay.model_dump_json()
    native = _rows(case.database, case.run_id)
    captured = _capture(case, replay, candidates)
    (tmp_path / f"capture-{path}-observation.json").write_text(
        json.dumps(
            {
                "input_path": path,
                "controlled_replay_projection_not_native_rewrite": True,
                "original": replay.model_dump(mode="json"),
                "captured": captured.model_dump(mode="json"),
                "late_ids": sorted(late_ids),
                "native_task_unchanged": native == _rows(case.database, case.run_id),
                "returned_success_result": False,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    assert replay.model_dump_json() == original
    assert _rows(case.database, case.run_id) == native
    assert late_ids.isdisjoint(item.evidence_id for item in captured.input_evidence)
    assert late_ids.isdisjoint(captured.evidence_refs)
    assert captured.context_manifest is not None
    assert late_ids <= {
        item.evidence_ref
        for item in captured.context_manifest.excluded_inputs
        if item.reason == "received_after_cutoff"
    }


def test_budget_exclusion_does_not_remove_eligible_collected_reference(
    prepared_case: PreparedCase,  # noqa: F811 -- imported pytest fixture
) -> None:
    case = prepared_case
    live, candidate, cutoff = _source(case)
    trigger = [item for item in live.input_evidence if item.received_at <= cutoff]
    replay = live.model_copy(
        update={
            "execution_mode": "replay",
            "pit_cutoff_at": cutoff,
            "input_evidence": trigger,
            "evidence_refs": [item.evidence_id for item in trigger],
        }
    )
    # Controlled candidate DTOs test reference semantics, not producer attestations.
    candidates = [
        candidate.model_copy(
            update={"evidence_id": f"eligible-budget-{index}", "received_at": cutoff}
        )
        for index in range(14)
    ]
    captured = _capture(case, replay, candidates)
    assert captured.context_manifest is not None
    excluded = {
        item.evidence_ref
        for item in captured.context_manifest.excluded_inputs
        if item.reason == "context_budget"
    }
    assert excluded
    assert excluded <= set(captured.evidence_refs)


def test_live_capture_uses_actual_selection_time_and_retains_post_trigger_input(
    prepared_case: PreparedCase,  # noqa: F811 -- imported pytest fixture
) -> None:
    case = prepared_case
    live, candidate, trigger_cutoff = _source(case)
    selection_time = case.now
    assert trigger_cutoff < candidate.received_at <= selection_time < live.pit_cutoff_at
    future = candidate.model_copy(
        update={
            "evidence_id": "future-at-selection",
            "received_at": selection_time + timedelta(seconds=1),
        }
    )
    captured = _capture(case, live, [future], selection_clock=lambda: selection_time)
    library_ids = {
        item.evidence_id
        for item in live.input_evidence
        if item.source_id.startswith("research-library:")
    }
    assert library_ids <= {item.evidence_id for item in captured.input_evidence}
    assert future.evidence_id not in captured.evidence_refs
    assert all(item.evidence_id != future.evidence_id for item in captured.input_evidence)


def test_naive_live_selection_clock_is_rejected_before_runtime(
    prepared_case: PreparedCase,  # noqa: F811 -- imported pytest fixture
) -> None:
    case = prepared_case
    live, _, _ = _source(case)
    runtime = CaptureOnlyRuntime()
    graph = build_agentic_research_graph(
        runtime,
        ResearchEvidenceService(case.database),
        selection_clock=lambda: case.now.replace(tzinfo=None),
    )
    with pytest.raises(ValueError, match="research_context_cutoff_timezone_required"):
        asyncio.run(graph.ainvoke(initial_research_state(live)))
    assert runtime.requests == []


def test_replay_does_not_consume_live_selection_clock(
    prepared_case: PreparedCase,  # noqa: F811 -- imported pytest fixture
) -> None:
    case = prepared_case
    live, _, cutoff = _source(case)
    replay = live.model_copy(update={"execution_mode": "replay", "pit_cutoff_at": cutoff})

    def forbidden_clock() -> datetime:
        raise AssertionError("Replay must use its original frozen PIT")

    captured = _capture(case, replay, [], selection_clock=forbidden_clock)
    assert all(item.received_at <= cutoff for item in captured.input_evidence)

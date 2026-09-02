from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest

from apps.hub_worker.composition import build_research_worker
from apps.hub_worker.research import DurableResearchWorker, ResearchRequestFactory
from packages.contracts_py.decision_hub_contracts import (
    ObservationCreate,
    ResearchSessionRequest,
    ResearchSessionResult,
)
from packages.contracts_py.decision_hub_contracts.models import RunStatus, SourceType
from packages.kernel.decision_hub_kernel.application.admission import AdmissionService
from packages.kernel.decision_hub_kernel.application.commit import CommitDecisionService
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    research_evidence_content_hash,
)
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.application.snapshot import SnapshotService
from packages.kernel.decision_hub_kernel.persistence.db import Database, RunRecord
from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError

NOW = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)
PACK_ROOT = Path(__file__).resolve().parents[2] / "packs" / "crypto_macro"


def _candidate(request: ResearchSessionRequest) -> dict[str, Any]:
    published_at = NOW - timedelta(seconds=10)
    excerpt = "The official statement confirms the event identity."
    return {
        "evidence_id": "worker-official-evidence",
        "requirement_id": "event_identity",
        "kind": "official",
        "authority": "official",
        "source_id": "official-feed",
        "source_url": "https://www.federalreserve.gov/",
        "published_at": published_at.isoformat(),
        "observed_at": (NOW - timedelta(seconds=9)).isoformat(),
        "received_at": (NOW - timedelta(seconds=8)).isoformat(),
        "content_hash": research_evidence_content_hash(
            requirement_id="event_identity",
            kind="official",
            authority="official",
            source_id="official-feed",
            source_url="https://www.federalreserve.gov/",
            published_at=published_at,
            excerpt=excerpt,
            structured_payload_ref=None,
        ),
        "excerpt": excerpt,
        "structured_payload_ref": None,
        "tool_call_id": "tool-worker-1",
        "research_session_id": "worker-session-1",
        "round": request.current_round,
        "quality": "candidate",
        "freshness_status": "unknown",
        "conflict_group": None,
    }


def _result(request: ResearchSessionRequest, *, fail: bool = False) -> ResearchSessionResult:
    candidate = _candidate(request)
    coverage = {
        "status": "insufficient",
        "covered_requirement_ids": [],
        "gaps": [],
        "conflicts": [],
        "hard_coverage_ratio": 0.0,
        "soft_coverage_ratio": 1.0,
        "assessed_at": NOW.isoformat(),
    }
    round_payload = {
        "round": request.current_round,
        "plan": {
            "plan_id": "worker-plan-1",
            "objective": "verify event",
            "tasks": [
                {
                    "task_id": "worker-task-1",
                    "capability_id": "replay.research",
                    "objective": "verify event",
                    "question": "What is the official event?",
                    "input_evidence_refs": list(request.evidence_refs),
                    "output_schema_ref": "evidence-candidate.v1",
                    "depends_on": [],
                    "success_condition": "one official source",
                    "priority": 1,
                }
            ],
            "required_capabilities": ["replay.research"],
            "created_at": NOW.isoformat(),
        },
        "tool_invocations": [],
        "tool_results": [],
        "new_evidence_refs": [],
        "coverage": coverage,
        "started_at": NOW.isoformat(),
        "finished_at": (NOW + timedelta(seconds=1)).isoformat(),
    }
    horizons = [
        {
            "horizon": horizon,
            "action": "short",
            "subjective_probability": 0.6,
            "probability_status": "uncalibrated",
            "evidence_refs": [candidate["evidence_id"]],
            "trigger": f"{horizon} confirmation trigger",
            "invalidation": f"{horizon} thesis invalidation",
            "expires_at": (
                NOW + timedelta(minutes=(30, 1440, 4320)[index])
            ).isoformat(),
            "next_review_at": (
                NOW + timedelta(minutes=(10, 360, 1440)[index])
            ).isoformat(),
            "missing_facts": [],
            "confidence_cap_reason": None,
        }
        for index, horizon in enumerate(("30m", "24h", "72h"))
    ]
    return ResearchSessionResult.model_validate(
        {
            "schema_version": "research-session-result.v1",
            "request_id": request.request_id,
            "research_session_id": "worker-session-1",
            "runtime_id": "fake-worker-harness",
            "runtime_version": "fake-worker-harness.v1",
            "profile_ref": "fake-worker-profile.v1",
            "trace_ref": "fake://worker-trace",
            "trace_hash": hashlib.sha256(b"worker-trace").hexdigest(),
            "status": "failed" if fail else "completed",
            "rounds": [round_payload],
            "evidence_candidates": [candidate],
            "final_coverage": coverage,
            "causal_case": {
                "case_id": "worker-case-1",
                "thesis": "The official event creates a bounded bearish candidate.",
                "main_chain": [
                    {
                        "link_id": "worker-link-1",
                        "claim_type": "fact",
                        "statement": "The official event was identified.",
                        "evidence_refs": [candidate["evidence_id"]],
                        "confirmation": "A second official revision confirms it.",
                        "invalidation": "The official source retracts it.",
                        "affected_horizons": ["30m", "24h", "72h"],
                    }
                ],
                "opposite_chain": [
                    {
                        "link_id": "worker-link-2",
                        "claim_type": "scenario",
                        "statement": "The event may already be priced in.",
                        "evidence_refs": [candidate["evidence_id"]],
                        "confirmation": "Price holds the pre-event range.",
                        "invalidation": "Price breaks lower with confirmation.",
                        "affected_horizons": ["30m", "24h"],
                    }
                ],
                "unresolved_questions": [],
                "evidence_refs": [candidate["evidence_id"]],
            },
            "horizons": horizons,
            "stop_reason": {
                "code": "sufficient",
                "detail": "The bounded research fixture completed.",
                "bounded": False,
                "remaining_hard_gaps": [],
            },
            "total_tool_calls": 1,
            "total_subagents": 0,
            "total_tokens": 10,
            "estimated_cost_usd": 0.01,
            "started_at": NOW.isoformat(),
            "finished_at": (NOW + timedelta(seconds=1)).isoformat(),
        }
    )


class FakeResearchRuntime:
    runtime_id = "fake-worker-harness"
    runtime_version = "fake-worker-harness.v1"
    profile_ref = "fake-worker-profile.v1"

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.requests: list[ResearchSessionRequest] = []

    async def execute(
        self, request: ResearchSessionRequest, trace_sink: Any = None
    ) -> ResearchSessionResult:
        self.requests.append(request)
        if self.fail:
            raise AgentExecutionError("provider_timeout", "fixture timeout", retryable=True)
        return _result(request)

    async def close(self) -> None:
        return None


def _admitted_run(tmp_path: Path) -> tuple[Database, str, str]:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'worker.sqlite3'}")
    database.create_all()
    event_id, _, admitted = AdmissionService(database, clock=lambda: NOW).admit(
        ObservationCreate(
            text="The official event confirms elevated inflation risks.",
            source_id="worker-input",
            source_type=SourceType.transcript,
            observed_at=NOW - timedelta(seconds=2),
            published_at=NOW - timedelta(seconds=3),
            language="en",
            event_hint="central_bank_speech",
        )
    )
    assert admitted
    run_id, created = RunService(database).create(event_id, strategy_version="research.v1")
    assert created
    return database, event_id, run_id


def test_research_request_accepts_manual_text_without_published_at(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'manual-text.sqlite3'}")
    database.create_all()
    event_id, _, admitted = AdmissionService(database, clock=lambda: NOW).admit(
        ObservationCreate(
            text="A manual transcript may not have a separate publication timestamp.",
            source_id="manual-text",
            source_type=SourceType.transcript,
            observed_at=NOW - timedelta(seconds=1),
            language="en",
        )
    )
    assert admitted
    run_id, _ = RunService(database).create(event_id, strategy_version="research.v1")

    request = ResearchRequestFactory(database, pack_root=PACK_ROOT, clock=lambda: NOW).build(
        event_id, run_id
    )

    assert request.input_evidence[0].published_at is None


def test_replay_worker_request_declares_replay_execution_mode(tmp_path: Path) -> None:
    database, event_id, run_id = _admitted_run(tmp_path)

    request = ResearchRequestFactory(
        database,
        pack_root=PACK_ROOT,
        allowed_capabilities=("replay.research",),
        execution_mode="replay",
        clock=lambda: NOW,
    ).build(event_id, run_id)

    assert request.execution_mode == "replay"
    assert request.pit_cutoff_at < request.deadline_at
    assert request.pit_cutoff_at == request.input_evidence[0].received_at


def test_research_request_bounds_long_source_excerpt_without_changing_snapshot_hash(
    tmp_path: Path,
) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'long-source.sqlite3'}")
    database.create_all()
    source_text = "Official speech paragraph. " * 400
    event_id, _, admitted = AdmissionService(database, clock=lambda: NOW).admit(
        ObservationCreate(
            text=source_text,
            source_id="official-federal-reserve",
            source_type=SourceType.transcript,
            observed_at=NOW - timedelta(seconds=2),
            published_at=NOW - timedelta(seconds=3),
            language="en",
            event_hint="central_bank_speech",
        )
    )
    assert admitted
    run_id, _ = RunService(database).create(event_id, strategy_version="research.v1")

    request = ResearchRequestFactory(database, pack_root=PACK_ROOT, clock=lambda: NOW).build(
        event_id, run_id
    )

    assert len(request.input_evidence[0].excerpt) == 4000
    snapshot = database.get_run_record(run_id)
    assert snapshot is not None and snapshot.snapshot_id is not None
    trigger_evidence = database.get_snapshot_evidence(snapshot.snapshot_id)
    assert trigger_evidence and trigger_evidence[0]["text"] == source_text


def test_research_worker_projects_agentic_result_into_existing_ledger(tmp_path: Path) -> None:
    database, _event_id, run_id = _admitted_run(tmp_path)
    runtime = FakeResearchRuntime()
    worker = DurableResearchWorker(
        database,
        runtime,
        pack_root=PACK_ROOT,
        # Replay fixtures own their clock; wall-clock time must not change
        # whether the deterministic next-review child is scheduled.
        clock=lambda: NOW,
    )

    report = asyncio.run(worker.tick())

    assert report is not None
    assert report.status == "research_only"
    assert report.run_id == run_id
    assert report.artifact_id
    assert len(runtime.requests) == 2
    assert runtime.requests[0].request_id == runtime.requests[1].request_id
    assert runtime.requests[1].current_round == 2
    run = database.get_run_record(run_id)
    assert run is not None
    assert run.status == "degraded"
    assert run.runtime_version == "fake-worker-harness.v1"
    assert run.artifact_id == report.artifact_id
    assert run.snapshot_id
    assert run.decision_snapshot_id
    assert run.cost_usd == pytest.approx(0.02)
    artifact = database.get_artifact_view(report.artifact_id)
    assert artifact is not None
    assert artifact.gate_status.value == "research_only"
    assert {item.horizon for item in artifact.forecasts} == {"30m", "24h", "72h"}
    assert [item.direction.value for item in artifact.forecasts] == ["no_trade"] * 3
    assert all(item.probability == pytest.approx(0.5) for item in artifact.forecasts)
    event_types = [item.event_type for item in database.get_timeline(run_id)]
    assert set(event_types) == {
        "decision.committed",
        "research.agentic.completed",
        "research.recheck.scheduled",
    }

    assert asyncio.run(worker.tick()) is None


def test_research_worker_marks_runtime_failure_and_keeps_no_artifact(tmp_path: Path) -> None:
    database, _event_id, run_id = _admitted_run(tmp_path)
    worker = DurableResearchWorker(
        database,
        FakeResearchRuntime(fail=True),
        pack_root=PACK_ROOT,
    )

    report = asyncio.run(worker.tick())

    assert report is not None
    assert report.status == "failed"
    assert report.error_code == "provider_timeout"
    run = database.get_run_record(run_id)
    assert run is not None
    assert run.status == "failed"
    assert run.error_code == "provider_timeout"
    assert run.artifact_id is None
    assert [item.event_type for item in database.get_timeline(run_id)] == [
        "research.agentic.failed"
    ]


def test_research_worker_does_not_reclaim_terminal_or_unadmitted_runs(tmp_path: Path) -> None:
    database, _event_id, run_id = _admitted_run(tmp_path)
    worker = DurableResearchWorker(database, FakeResearchRuntime(), pack_root=PACK_ROOT)
    first = asyncio.run(worker.tick())
    assert first is not None

    assert asyncio.run(worker.tick()) is None
    terminal = database.get_run_record(run_id)
    assert terminal is not None
    assert terminal.artifact_id == first.artifact_id


def test_research_worker_composition_writes_research_heartbeat(tmp_path: Path) -> None:
    database, _event_id, _run_id = _admitted_run(tmp_path)
    runtime = FakeResearchRuntime()
    worker = build_research_worker(
        database,
        runtime=runtime,
        heartbeat_interval_seconds=1,
    )

    report = asyncio.run(worker.tick())

    assert report is not None
    heartbeat = next(
        item
        for item in worker.heartbeats.list_views()
        if item.service_id == "hub-research-worker"
    )
    assert heartbeat.role == "research_worker"
    assert heartbeat.mode == "replay"


def test_research_worker_heartbeat_stays_online_during_long_runtime(
    tmp_path: Path,
) -> None:
    database, _event_id, _run_id = _admitted_run(tmp_path)

    class SlowRuntime(FakeResearchRuntime):
        async def execute(
            self, request: ResearchSessionRequest, trace_sink: Any = None
        ) -> ResearchSessionResult:
            await asyncio.sleep(2.2)
            return await super().execute(request, trace_sink=trace_sink)

    worker = build_research_worker(
        database,
        runtime=SlowRuntime(),
        heartbeat_interval_seconds=1,
    )

    async def run_and_observe() -> None:
        task = asyncio.create_task(worker.tick())
        await asyncio.sleep(2.2)
        heartbeat = next(
            item
            for item in worker.heartbeats.list_views()
            if item.service_id == "hub-research-worker"
        )
        assert heartbeat.status == "online"
        await task

    asyncio.run(run_and_observe())


def test_research_worker_capabilities_are_explicit_and_deduplicated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database, _event_id, _run_id = _admitted_run(tmp_path)
    monkeypatch.setenv(
        "DECISION_HUB_RESEARCH_CAPABILITIES",
        "replay.research, web.fetch, replay.research",
    )

    worker = build_research_worker(database, runtime=FakeResearchRuntime())

    assert worker.worker.factory.allowed_capabilities == ("replay.research", "web.fetch")


def test_research_run_lease_is_single_winner_and_expired_claim_is_recoverable(
    tmp_path: Path,
) -> None:
    database, event_id, run_id = _admitted_run(tmp_path)
    baseline_id, _ = RunService(database).create(
        event_id,
        idempotency_key="baseline-isolation",
        strategy_version="baseline.v1",
    )
    current = NOW
    runs = RunService(database, clock=lambda: current)

    first = runs.claim_next(
        strategy_version="research.v1",
        worker_id="research-a",
        lease_seconds=30,
    )
    assert first is not None
    assert first.run_id == run_id
    assert first.recovered is False
    assert runs.claim_next(
        strategy_version="research.v1",
        worker_id="research-b",
        lease_seconds=30,
    ) is None

    current = NOW + timedelta(seconds=31)
    recovered = runs.claim_next(
        strategy_version="research.v1",
        worker_id="research-b",
        lease_seconds=30,
    )
    assert recovered is not None
    assert recovered.run_id == run_id
    assert recovered.recovered is True
    row = database.get_run_record(run_id)
    assert row is not None
    assert row.lease_owner == "research-b"
    assert row.lease_expires_at is not None
    baseline = database.get_run_record(baseline_id)
    assert baseline is not None
    assert baseline.status == "admitted"


def test_manual_research_claims_ahead_of_older_automatic_and_scheduled_runs(
    tmp_path: Path,
) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'priority.sqlite3'}")
    database.create_all()
    runs = RunService(database, clock=lambda: NOW)
    automatic_id, _ = runs.create(
        "event-automatic",
        strategy_version="research.v1",
        admission_origin="automatic",
        priority=50,
    )
    scheduled_id, _ = runs.create(
        "event-scheduled",
        strategy_version="research.v1",
        admission_origin="scheduled_recheck",
        priority=10,
    )
    manual_id, _ = runs.create(
        "event-manual",
        strategy_version="research.v1",
        admission_origin="manual",
        priority=100,
    )

    first = runs.claim_next(
        strategy_version="research.v1", worker_id="worker-priority", lease_seconds=60
    )
    assert first is not None and first.run_id == manual_id
    runs.set_status(manual_id, RunStatus.completed)
    second = runs.claim_next(
        strategy_version="research.v1", worker_id="worker-priority", lease_seconds=60
    )
    assert second is not None and second.run_id == automatic_id
    runs.set_status(automatic_id, RunStatus.completed)
    third = runs.claim_next(
        strategy_version="research.v1", worker_id="worker-priority", lease_seconds=60
    )
    assert third is not None and third.run_id == scheduled_id


def test_equal_priority_claim_has_stable_run_id_tiebreak(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'priority-tie.sqlite3'}")
    database.create_all()
    runs = RunService(database, clock=lambda: NOW)
    run_ids = [
        runs.create(
            f"event-tie-{index}",
            strategy_version="research.v1",
            admission_origin="automatic",
            priority=50,
        )[0]
        for index in range(3)
    ]

    claimed: list[str] = []
    for _ in run_ids:
        item = runs.claim_next(
            strategy_version="research.v1", worker_id="worker-tie", lease_seconds=60
        )
        assert item is not None
        claimed.append(item.run_id)
        runs.set_status(item.run_id, RunStatus.completed)

    assert claimed == sorted(run_ids)


def test_research_commit_schedules_one_idempotent_child_run_at_next_review(
    tmp_path: Path,
) -> None:
    database, event_id, run_id = _admitted_run(tmp_path)
    request = ResearchRequestFactory(database, pack_root=PACK_ROOT, clock=lambda: NOW).build(
        event_id, run_id
    )
    commit = CommitDecisionService(database, clock=lambda: NOW)

    first_artifact, _ = commit.commit_research_result(run_id, event_id, _result(request))
    second_artifact, _ = commit.commit_research_result(run_id, event_id, _result(request))

    assert second_artifact == first_artifact
    with database.session() as session:
        children = session.query(RunRecord).filter_by(parent_run_id=run_id).all()
    assert len(children) == 1
    child = children[0]
    assert child.strategy_version == "research.v1"
    assert child.available_at == (NOW + timedelta(minutes=10)).replace(tzinfo=None)
    assert (
        RunService(database, clock=lambda: NOW + timedelta(minutes=9)).claim_next(
            strategy_version="research.v1",
            worker_id="research-before-review",
            lease_seconds=30,
        )
        is None
    )
    claimed = RunService(
        database, clock=lambda: NOW + timedelta(minutes=11)
    ).claim_next(
        strategy_version="research.v1",
        worker_id="research-at-review",
        lease_seconds=30,
    )
    assert claimed is not None and claimed.run_id == child.run_id


def test_research_worker_resumes_expired_checkpoint_and_commits_once(tmp_path: Path) -> None:
    database, event_id, run_id = _admitted_run(tmp_path)
    SnapshotService(database).freeze_for_run(run_id, event_id)
    current = NOW
    first_runs = RunService(database, clock=lambda: current)
    claimed = first_runs.claim_next(
        strategy_version="research.v1",
        worker_id="research-a",
        lease_seconds=1,
    )
    assert claimed is not None

    request = ResearchRequestFactory(database, pack_root=PACK_ROOT, clock=lambda: NOW).build(
        event_id, run_id
    )
    resumed_state = {
        "final_result": _result(request).model_dump(mode="json"),
        "decision_snapshot_id": None,
    }

    class ResumeExecutor:
        def __init__(self) -> None:
            self.resumed: list[str] = []

        async def has_checkpoint(self, candidate_run_id: str) -> bool:
            return candidate_run_id == run_id

        async def resume(self, candidate_run_id: str) -> dict[str, object]:
            self.resumed.append(candidate_run_id)
            return cast(dict[str, object], resumed_state)

        async def execute(self, _request: ResearchSessionRequest) -> dict[str, object]:
            raise AssertionError("an expired run with a checkpoint must resume")

    executor = ResumeExecutor()
    worker = DurableResearchWorker(
        database,
        FakeResearchRuntime(),
        pack_root=PACK_ROOT,
        worker_id="research-b",
        lease_seconds=30,
        clock=lambda: NOW + timedelta(seconds=2),
    )
    worker.executor = executor  # type: ignore[assignment]

    report = asyncio.run(worker.tick())

    assert report is not None
    assert report.run_id == run_id
    assert report.artifact_id
    assert executor.resumed == [run_id]
    assert asyncio.run(worker.tick()) is None
    row = database.get_run_record(run_id)
    assert row is not None
    assert row.artifact_id == report.artifact_id
    assert row.lease_owner is None
    assert row.lease_expires_at is None

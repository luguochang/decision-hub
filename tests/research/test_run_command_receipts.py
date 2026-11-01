# pyright: reportPrivateUsage=false
from __future__ import annotations

import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

from apps.hub_api.main import create_app
from apps.hub_worker.research import DurableResearchWorker, ResearchRequestFactory
from packages.contracts_py.decision_hub_contracts import (
    ResearchRunCommand,
    ResearchSessionRequest,
    ResearchSessionResult,
)
from packages.kernel.decision_hub_kernel.application.commit import CommitDecisionService
from packages.kernel.decision_hub_kernel.application.research_observability import (
    ResearchCommandService,
)
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.persistence.db import (
    ArtifactRecord,
    OutboxRecord,
    ProductCommandRecord,
    ResearchCommandRecord,
    RunRecord,
)
from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError
from tests.evolution.test_research_worker import (
    NOW,
    PACK_ROOT,
    FakeResearchRuntime,
    _admitted_run,
    _result,
)


@pytest.fixture(autouse=True)
def _disable_external_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DECISION_HUB_LLM_ENABLED", "0")


def _command(key: str, action: str = "cancel", reason: str = "Stop this run") -> ResearchRunCommand:
    return ResearchRunCommand.model_validate(
        {
            "schema_version": "research-run-command.v1",
            "request_id": key,
            "command": action,
            "reason": reason,
        }
    )


def test_rejected_command_replay_preserves_rejection(tmp_path: Path) -> None:
    database, _, run_id = _admitted_run(tmp_path)
    service = ResearchCommandService(database, clock=lambda: NOW)
    command = _command("retry-not-terminal", "retry")
    assert service.apply(run_id, command).status == "rejected"
    assert service.apply(run_id, command).status == "rejected"


def test_api_same_request_different_owner_cannot_replay_receipt(tmp_path: Path) -> None:
    database, _, run_id = _admitted_run(tmp_path)
    command = _command("cancel-owner-isolation")
    app = create_app(database=database, source_connectors=[])
    with TestClient(app) as client:
        first = client.post(
            f"/v1/research/runs/{run_id}/commands",
            headers={"Idempotency-Key": command.request_id, "X-Owner-Id": "owner-a"},
            json=command.model_dump(mode="json"),
        )
        assert first.status_code == 200
        second = client.post(
            f"/v1/research/runs/{run_id}/commands",
            headers={"Idempotency-Key": command.request_id, "X-Owner-Id": "owner-b"},
            json=command.model_dump(mode="json"),
        )
        assert second.status_code == 409
        assert second.json()["detail"] == "research_command_request_reused"


@pytest.mark.parametrize("late_failure", [False, True])
async def test_reclaimed_worker_cannot_publish_or_fail_new_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, late_failure: bool
) -> None:
    database, _, run_id = _admitted_run(tmp_path)
    current_time = [NOW]
    started, release = asyncio.Event(), asyncio.Event()

    class BlockingExecutor:
        async def execute(self, request: ResearchSessionRequest) -> dict[str, object]:
            started.set()
            await release.wait()
            if late_failure:
                raise AgentExecutionError("late_provider_failure", "old worker stopped")
            return {"final_result": _result(request).model_dump(mode="json")}

    worker = DurableResearchWorker(
        database,
        FakeResearchRuntime(),
        pack_root=PACK_ROOT,
        worker_id="old-worker",
        lease_seconds=1,
        clock=lambda: current_time[0],
    )
    monkeypatch.setattr(worker, "executor", BlockingExecutor())
    task = asyncio.create_task(worker.tick())
    await asyncio.wait_for(started.wait(), timeout=5)
    current_time[0] = NOW + timedelta(seconds=2)
    claim = RunService(database, clock=lambda: current_time[0]).claim_next(
        strategy_version="research.v1", worker_id="new-worker", lease_seconds=30
    )
    assert claim is not None and claim.run_id == run_id and claim.recovered
    release.set()
    report = await asyncio.wait_for(task, timeout=5)
    assert report is not None and report.status == "lease_lost"
    current = database.get_run_record(run_id)
    assert current is not None
    assert current.status == "running"
    assert current.lease_owner == "new-worker"
    assert current.artifact_id is None
    assert current.error_code is None


@pytest.mark.parametrize("action", ["cancel", "retry"])
def test_get_receipt_returns_frozen_original_not_current_status(
    tmp_path: Path, action: str
) -> None:
    database, _, run_id = _admitted_run(tmp_path)
    command = _command(f"frozen-{action}", action)
    with TestClient(create_app(database=database, source_connectors=[])) as client:
        headers = {"Idempotency-Key": command.request_id, "X-Owner-Id": "owner-a"}
        first = client.post(
            f"/v1/research/runs/{run_id}/commands", headers=headers,
            json=command.model_dump(mode="json"),
        )
        assert first.status_code == 200
        original = first.json()
        with database.session() as session:
            run = session.get(RunRecord, run_id)
            assert run is not None
            run.status = "failed"  # Current state cannot replace the original receipt.
        replay = client.post(
            f"/v1/research/runs/{run_id}/commands", headers=headers,
            json=command.model_dump(mode="json"),
        )
        assert replay.status_code == 200
        assert replay.json()["status"] == ("already_applied" if action == "cancel" else "rejected")
        receipt = client.get(
            f"/v1/research/runs/{run_id}/commands/{command.request_id}",
            headers={"X-Owner-Id": "owner-a"},
        )
        assert receipt.status_code == 200
        assert receipt.json() == {
            "schema_version": "research-run-command-receipt.v1",
            "request_id": command.request_id,
            "owner_id": "owner-a",
            "source_run_id": run_id,
            "command": command.model_dump(mode="json"),
            "result": original,
        }
    with database.session() as session:
        assert session.query(ResearchCommandRecord).count() == 1
        assert session.query(ProductCommandRecord).filter_by(kind="research_command").count() == 1


@pytest.mark.parametrize("change", ["owner", "run", "action", "reason"])
def test_same_key_full_intent_is_immutable(tmp_path: Path, change: str) -> None:
    database, _, run_id = _admitted_run(tmp_path)
    service = ResearchCommandService(database, clock=lambda: NOW)
    command = _command("immutable-command", "feedback")
    first = service.apply(run_id, command, owner_id="owner-a")
    owner, target = "owner-a", run_id
    if change == "owner":
        owner = "owner-b"
    elif change == "run":
        target = "run-other"
    elif change == "action":
        command = command.model_copy(update={"command": "cancel"})
    else:
        command = command.model_copy(update={"reason": "Changed feedback"})
    with pytest.raises(ValueError, match="research_command_request_reused"):
        service.apply(target, command, owner_id=owner)
    receipt = service.receipt(run_id, command.request_id, owner_id="owner-a")
    assert receipt.result == first
    assert receipt.command.command == "feedback" and receipt.command.reason == "Stop this run"
    assert database.get_run_record(run_id).status == "admitted"  # type: ignore[union-attr]


@pytest.mark.parametrize("mismatch", ["owner", "run", "kind", "missing"])
def test_exact_get_receipt_nonmatches_are_404(tmp_path: Path, mismatch: str) -> None:
    database, _, run_id = _admitted_run(tmp_path)
    command = _command("private-command", "feedback")
    service = ResearchCommandService(database, clock=lambda: NOW)
    service.apply(run_id, command, owner_id="owner-a")
    owner = "owner-b" if mismatch == "owner" else "owner-a"
    target = "run-other" if mismatch == "run" else run_id
    key = "not-found" if mismatch == "missing" else command.request_id
    if mismatch == "kind":
        with database.session() as session:
            row = session.get(ProductCommandRecord, key)
            assert row is not None
            row.kind = "library_upload"
    with TestClient(create_app(database=database, source_connectors=[])) as client:
        response = client.get(
            f"/v1/research/runs/{target}/commands/{key}", headers={"X-Owner-Id": owner}
        )
    assert response.status_code == 404
    assert response.json()["detail"] == "research_command_receipt_not_found"


def test_legacy_unowned_command_is_not_assigned_to_new_header(tmp_path: Path) -> None:
    database, _, run_id = _admitted_run(tmp_path)
    command = _command("old-unowned-command", "feedback")
    with database.session() as session:
        session.add(ResearchCommandRecord(
            request_id=command.request_id, run_id=run_id, command=command.command,
            reason=command.reason, target_run_id=None, status="accepted", created_at=NOW,
        ))
    service = ResearchCommandService(database, clock=lambda: NOW)
    with pytest.raises(ValueError, match="research_command_legacy_owner_unknown"):
        service.apply(run_id, command, owner_id="owner-a")
    with pytest.raises(KeyError, match="research_command_receipt_not_found"):
        service.receipt(run_id, command.request_id, owner_id="owner-a")
    with database.session() as session:
        assert session.query(ProductCommandRecord).count() == 0
        assert session.query(ResearchCommandRecord).count() == 1


def test_tampered_receipt_cannot_confirm_or_replay(tmp_path: Path) -> None:
    database, _, run_id = _admitted_run(tmp_path)
    command = _command("tampered-receipt", "feedback")
    service = ResearchCommandService(database, clock=lambda: NOW)
    service.apply(run_id, command, owner_id="owner-a")
    with database.session() as session:
        row = session.get(ProductCommandRecord, command.request_id)
        assert row is not None
        receipt = json.loads(row.result_json)
        receipt["command"]["reason"] = "Another intent"
        row.result_json = json.dumps(receipt)
    with pytest.raises(ValueError, match="research_command_receipt_invalid"):
        service.receipt(run_id, command.request_id, owner_id="owner-a")
    with pytest.raises(ValueError, match="research_command_receipt_invalid"):
        service.apply(run_id, command, owner_id="owner-a")


@pytest.mark.parametrize(
    ("action", "source_status"),
    [("cancel", "admitted"), ("feedback", "admitted"), ("retry", "failed"),
     ("recheck", "completed"), ("retry", "admitted")],
)
def test_receipt_wrong_child_target_is_not_confirmable(
    tmp_path: Path, action: str, source_status: str
) -> None:
    database, _, run_id = _admitted_run(tmp_path)
    with database.session() as session:
        run = session.get(RunRecord, run_id)
        assert run is not None
        run.status = source_status
    command = _command("wrong-child-receipt", action)
    service = ResearchCommandService(database, clock=lambda: NOW)
    original = service.apply(run_id, command, owner_id="owner-a")
    assert service.receipt(run_id, command.request_id, owner_id="owner-a").result == original
    with database.session() as session:
        row = session.get(ProductCommandRecord, command.request_id)
        assert row is not None
        receipt = json.loads(row.result_json)
        receipt["result"]["target_run_id"] = "run-wrong-child"
        row.result_json = json.dumps(receipt)
    with pytest.raises(ValueError, match="research_command_receipt_invalid"):
        service.receipt(run_id, command.request_id, owner_id="owner-a")
    with pytest.raises(ValueError, match="research_command_receipt_invalid"):
        service.apply(run_id, command, owner_id="owner-a")


def test_concurrent_same_key_retry_has_one_child_and_original_receipt(tmp_path: Path) -> None:
    database, _, run_id = _admitted_run(tmp_path)
    service = ResearchCommandService(database, clock=lambda: NOW)
    service.apply(run_id, _command("first-cancel"), owner_id="owner-a")
    retry = _command("concurrent-retry", "retry")
    start = threading.Barrier(4)

    def apply() -> str | None:
        start.wait(timeout=5)
        return service.apply(run_id, retry, owner_id="owner-a").target_run_id

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(apply) for _ in range(4)]
        children = [future.result(timeout=5) for future in futures]
    assert len(set(children)) == 1 and children[0] is not None
    receipt = service.receipt(run_id, retry.request_id, owner_id="owner-a")
    assert receipt.result.status == "accepted" and receipt.result.target_run_id == children[0]
    with database.session() as session:
        assert session.query(RunRecord).filter_by(parent_run_id=run_id).count() == 1
        assert (
            session.query(ResearchCommandRecord).filter_by(request_id=retry.request_id).count() == 1
        )
        assert (
            session.query(ProductCommandRecord).filter_by(request_id=retry.request_id).count() == 1
        )


def test_cancel_write_lock_wins_before_waiting_commit(tmp_path: Path) -> None:
    database, event_id, run_id = _admitted_run(tmp_path)
    request = ResearchRequestFactory(database, pack_root=PACK_ROOT, clock=lambda: NOW).build(
        event_id, run_id
    )
    locked, release, commit_started = threading.Event(), threading.Event(), threading.Event()

    def after_cursor(
        _connection: Any, _cursor: Any, statement: str, _parameters: Any,
        _context: Any, _executemany: bool,
    ) -> None:
        if statement == "BEGIN IMMEDIATE" and threading.current_thread().name == "cancel-lock":
            locked.set()
            assert release.wait(timeout=5)

    def cancel():
        threading.current_thread().name = "cancel-lock"
        return ResearchCommandService(database, clock=lambda: NOW).apply(
            run_id, _command("cancel-linearized"), owner_id="owner-a"
        )

    def commit():
        commit_started.set()
        return CommitDecisionService(database, clock=lambda: NOW).commit_research_result(
            run_id, event_id, _result(request)
        )

    event.listen(database.engine, "after_cursor_execute", after_cursor)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            cancel_future = pool.submit(cancel)
            assert locked.wait(timeout=5)
            commit_future = pool.submit(commit)
            assert commit_started.wait(timeout=5)
            release.set()
            assert cancel_future.result(timeout=5).status == "accepted"
            with pytest.raises(PermissionError):
                commit_future.result(timeout=5)
    finally:
        release.set()
        event.remove(database.engine, "after_cursor_execute", after_cursor)
    with database.session() as session:
        assert session.query(ArtifactRecord).count() == 0
        assert session.query(OutboxRecord).count() == 0
    assert database.get_run_record(run_id).status == "cancelled"  # type: ignore[union-attr]


def test_published_run_rejects_cancel_without_mutating_original(tmp_path: Path) -> None:
    database, event_id, run_id = _admitted_run(tmp_path)
    request = ResearchRequestFactory(database, pack_root=PACK_ROOT, clock=lambda: NOW).build(
        event_id, run_id
    )
    artifact_id, _ = CommitDecisionService(database, clock=lambda: NOW).commit_research_result(
        run_id, event_id, _result(request)
    )
    before = database.get_run_record(run_id)
    assert before is not None
    service = ResearchCommandService(database, clock=lambda: NOW + timedelta(seconds=2))
    command = _command("cancel-after-publish")
    assert service.apply(run_id, command, owner_id="owner-a").status == "rejected"
    assert service.apply(run_id, command, owner_id="owner-a").status == "rejected"
    after = database.get_run_record(run_id)
    assert after is not None and after.artifact_id == artifact_id
    assert (after.status, after.finished_at, after.error_code) == (
        before.status, before.finished_at, before.error_code
    )
    with database.session() as session:
        assert session.query(ArtifactRecord).count() == 1
        assert session.query(OutboxRecord).count() == 1


def test_commit_write_lock_wins_before_waiting_cancel(tmp_path: Path) -> None:
    database, event_id, run_id = _admitted_run(tmp_path)
    request = ResearchRequestFactory(database, pack_root=PACK_ROOT, clock=lambda: NOW).build(
        event_id, run_id
    )
    locked, release, cancel_started = threading.Event(), threading.Event(), threading.Event()

    def after_cursor(
        _connection: Any, _cursor: Any, statement: str, _parameters: Any,
        _context: Any, _executemany: bool,
    ) -> None:
        if (
            threading.current_thread().name == "commit-lock"
            and statement.startswith("UPDATE runs SET artifact_id=")
        ):
            locked.set()
            assert release.wait(timeout=5)

    def commit():
        threading.current_thread().name = "commit-lock"
        return CommitDecisionService(database, clock=lambda: NOW).commit_research_result(
            run_id, event_id, _result(request)
        )

    def cancel():
        cancel_started.set()
        return ResearchCommandService(database, clock=lambda: NOW).apply(
            run_id, _command("publish-linearized"), owner_id="owner-a"
        )

    event.listen(database.engine, "after_cursor_execute", after_cursor)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            commit_future = pool.submit(commit)
            assert locked.wait(timeout=5)
            cancel_future = pool.submit(cancel)
            assert cancel_started.wait(timeout=5)
            release.set()
            artifact_id, _ = commit_future.result(timeout=5)
            assert cancel_future.result(timeout=5).status == "rejected"
    finally:
        release.set()
        event.remove(database.engine, "after_cursor_execute", after_cursor)
    current = database.get_run_record(run_id)
    assert current is not None and current.artifact_id == artifact_id
    assert current.status == "degraded" and current.error_code is None
    with database.session() as session:
        assert session.query(ArtifactRecord).count() == 1
        assert session.query(OutboxRecord).count() == 1


class HeldRuntime(FakeResearchRuntime):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.cleaned = asyncio.Event()

    async def execute(
        self, request: ResearchSessionRequest, trace_sink: Any = None
    ) -> ResearchSessionResult:
        self.started.set()
        try:
            await self.release.wait()
            return _result(request)
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        finally:
            self.cleaned.set()


def _assert_worker_tasks_cleaned(run_id: str) -> None:
    expected = {
        f"research-operation:{run_id}", f"research-cancel-watch:{run_id}",
        f"research-lease:{run_id}",
    }
    assert not [task for task in asyncio.all_tasks() if task.get_name() in expected]


async def test_api_owner_cancel_reaches_real_graph_runtime_and_cleans_tasks(tmp_path: Path) -> None:
    database, _, run_id = _admitted_run(tmp_path)
    runtime = HeldRuntime()
    worker = DurableResearchWorker(
        database, runtime, pack_root=PACK_ROOT, worker_id="cancelled-worker", clock=lambda: NOW,
    )
    task = asyncio.create_task(worker.tick())
    await asyncio.wait_for(runtime.started.wait(), timeout=5)
    command = _command("cancel-held-runtime")
    with TestClient(create_app(database=database, source_connectors=[])) as client:
        response = client.post(
            f"/v1/research/runs/{run_id}/commands",
            headers={"Idempotency-Key": command.request_id, "X-Owner-Id": "owner-a"},
            json=command.model_dump(mode="json"),
        )
        assert response.status_code == 200 and response.json()["status"] == "accepted"
    report = await asyncio.wait_for(task, timeout=5)
    assert report is not None and report.status == "cancelled"
    assert runtime.cancelled.is_set() and runtime.cleaned.is_set()
    _assert_worker_tasks_cleaned(run_id)
    current = database.get_run_record(run_id)
    assert current is not None and current.status == "cancelled"
    assert current.artifact_id is None and current.lease_owner is None
    assert current.error_code == "cancelled_by_owner"
    with database.session() as session:
        assert session.query(ArtifactRecord).count() == 0
        assert session.query(OutboxRecord).count() == 0


async def test_external_tick_shutdown_propagates_without_fake_owner_cancel(tmp_path: Path) -> None:
    database, _, run_id = _admitted_run(tmp_path)
    runtime = HeldRuntime()
    worker = DurableResearchWorker(
        database, runtime, pack_root=PACK_ROOT, worker_id="shutdown-worker", clock=lambda: NOW,
    )
    task = asyncio.create_task(worker.tick())
    await asyncio.wait_for(runtime.started.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5)
    assert runtime.cancelled.is_set() and runtime.cleaned.is_set()
    _assert_worker_tasks_cleaned(run_id)
    current = database.get_run_record(run_id)
    assert current is not None and current.status == "running"
    assert current.error_code is None and current.artifact_id is None


async def test_cancel_before_claim_never_starts_runtime(tmp_path: Path) -> None:
    database, _, run_id = _admitted_run(tmp_path)
    runtime = HeldRuntime()
    ResearchCommandService(database, clock=lambda: NOW).apply(
        run_id, _command("cancel-before-claim"), owner_id="owner-a"
    )
    worker = DurableResearchWorker(database, runtime, pack_root=PACK_ROOT, clock=lambda: NOW)
    assert await worker.tick() is None
    assert not runtime.started.is_set()
    _assert_worker_tasks_cleaned(run_id)


async def test_old_worker_after_new_worker_commit_preserves_winner_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database, _, run_id = _admitted_run(tmp_path)
    current_time = [NOW]
    started, release = asyncio.Event(), asyncio.Event()

    class OldExecutor:
        async def execute(self, request: ResearchSessionRequest) -> dict[str, object]:
            started.set()
            await release.wait()
            result = _result(request).model_copy(update={
                "runtime_version": "late-old-runtime", "estimated_cost_usd": 999,
            })
            return {"final_result": result.model_dump(mode="json")}

    old = DurableResearchWorker(
        database, FakeResearchRuntime(), pack_root=PACK_ROOT, worker_id="old-worker",
        lease_seconds=1, clock=lambda: current_time[0],
    )
    monkeypatch.setattr(old, "executor", OldExecutor())
    task = asyncio.create_task(old.tick())
    await asyncio.wait_for(started.wait(), timeout=5)
    current_time[0] = NOW + timedelta(seconds=2)
    new = DurableResearchWorker(
        database, FakeResearchRuntime(), pack_root=PACK_ROOT, worker_id="new-worker",
        lease_seconds=30, clock=lambda: current_time[0],
    )
    winner = await new.tick()
    assert winner is not None and winner.artifact_id is not None
    before = database.get_run_record(run_id)
    assert before is not None and before.artifact_id == winner.artifact_id
    release.set()
    loser = await asyncio.wait_for(task, timeout=5)
    assert loser is not None and loser.status == "lease_lost"
    after = database.get_run_record(run_id)
    assert after is not None
    assert (after.status, after.artifact_id, after.runtime_version, after.cost_usd,
            after.latency_ms, after.error_code) == (
        before.status, before.artifact_id, before.runtime_version, before.cost_usd,
        before.latency_ms, before.error_code,
    )
    assert after.runtime_version != "late-old-runtime" and after.cost_usd != 999
    _assert_worker_tasks_cleaned(run_id)
    with database.session() as session:
        assert session.query(ArtifactRecord).count() == 1
        assert session.query(OutboxRecord).count() == 1

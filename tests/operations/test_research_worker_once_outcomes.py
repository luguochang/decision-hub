"""Real CLI parser/once lifecycle with controlled reports, never Provider research."""
from __future__ import annotations

import json
import os
import socket
import sys
from pathlib import Path

import pytest
from sqlalchemy import text

from apps.hub_worker import main
from apps.hub_worker.composition import ResearchWorker, build_research_worker
from apps.hub_worker.research import ResearchWorkerReport
from packages.contracts_py.decision_hub_contracts import (
    ResearchSessionRequest,
    ResearchSessionResult,
)
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.kernel.decision_hub_kernel.ports.research import ResearchTraceSink


class NoModelRuntime:
    runtime_id = "controlled-cli-replay"
    runtime_version = "controlled-cli.v1"
    profile_ref = "controlled-cli.v1"

    def __init__(self) -> None:
        self.closed = 0
        self.calls = 0

    async def execute(
        self, request: ResearchSessionRequest, trace_sink: ResearchTraceSink | None = None,
    ) -> ResearchSessionResult:
        self.calls += 1
        raise AssertionError("once presentation must not submit model research")

    async def close(self) -> None:
        self.closed += 1


def _rows(database: Database) -> dict[str, list[dict[str, object]]]:
    with database.engine.connect() as connection:
        return {
            table: [
                dict(row) for row in connection.execute(text(f"SELECT * FROM {table}")).mappings()
            ]
            for table in ("runs", "run_events", "research_trace_events", "run_role_outputs")
        }


@pytest.mark.parametrize(
    ("status", "expected_exit", "phrases"),
    [
        ("publish", 0, ("本次调用结束",)),
        ("degraded", 0, ("本次调用结束",)),
        ("research_only", 0, ("本次调用结束",)),
        ("reject", 0, ("本次调用结束",)),
        ("no_run", 0, ("当前无待执行研究",)),
        ("failed", 1, ("本次执行失败",)),
        ("preparation_pending", 2, ("原研究尚未结束", "等待状态已保存")),
        ("preparation_storage_unavailable", 1, ("等待状态保存未确认", "原任务保持")),
        ("cancelled", 0, ("本次执行已取消", "不表示研究完成")),
        ("lease_lost", 0, ("租约已失效", "不表示研究完成")),
        ("future_unrecognized_status", 1, ("状态未确认",)),
    ],
)
def test_research_cli_once_reports_outcome_without_restarting_or_mutating_research(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    status: str, expected_exit: int, phrases: tuple[str, ...],
) -> None:
    monkeypatch.setenv("DECISION_HUB_LLM_ENABLED", "0")
    monkeypatch.setenv("DECISION_HUB_DOCUMENT_CORRECTION_LLM_ENABLED", "0")
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("DECISION_HUB_SOURCES_ENABLED", "0")
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'once.sqlite3'}")
    database.initialize()
    runtime = NoModelRuntime()
    worker = build_research_worker(
        database, runtime=runtime, allowed_capabilities=("replay.research",),
    )
    calls = {"build": 0, "tick": 0, "network": 0}
    creating_self_pipe = False
    original_socketpair = socket.socketpair
    original_connect = socket.socket.connect
    error = (
        "research_method_binding_failed" if status == "preparation_pending" else
        "research_preparation_storage_busy" if status == "preparation_storage_unavailable" else
        "controlled_failure" if status == "failed" else None
    )
    report = None if status == "no_run" else ResearchWorkerReport(
        status=status, run_id="controlled-original-run", error_code=error,
    )

    async def controlled_tick() -> ResearchWorkerReport | None:
        calls["tick"] += 1
        return report

    def selected_worker(*args: object, **kwargs: object) -> ResearchWorker:
        calls["build"] += 1
        return worker

    def selected_database(*args: object, **kwargs: object) -> Database:
        return database

    def self_pipe_pair() -> tuple[socket.socket, socket.socket]:
        # Windows asyncio creates an internal loopback socketpair before tick.
        # Permit only the synchronous original socketpair implementation.
        nonlocal creating_self_pipe
        creating_self_pipe = True
        try:
            return original_socketpair()
        finally:
            creating_self_pipe = False

    def forbidden_connect(
        connection: socket.socket, address: tuple[str, int] | str | bytes,
    ) -> None:
        if creating_self_pipe:
            original_connect(connection, address)
            return
        calls["network"] += 1
        raise AssertionError("offline once gate prohibits network")

    monkeypatch.setattr(worker, "tick", controlled_tick)
    monkeypatch.setattr(main, "build_research_worker", selected_worker)
    monkeypatch.setattr(main, "Database", selected_database)
    monkeypatch.setattr(socket, "socketpair", self_pipe_pair)
    monkeypatch.setattr(socket.socket, "connect", forbidden_connect)
    monkeypatch.setattr(sys, "argv", ["hub-worker", "--role", "research", "--once"])
    before = _rows(database)
    try:
        actual_exit = main.run()
        captured = capsys.readouterr()
        after = _rows(database)
        observation = {
            "controlled_tick_report": report.model_dump() if report is not None else None,
            "status": status, "exit_code": actual_exit,
            "stdout": captured.out, "stderr": captured.err,
            "calls": calls, "runtime_calls": runtime.calls, "runtime_closed": runtime.closed,
            "before": before, "after": after,
            "scope": "CLI once presentation/lifecycle; not Provider or real Run completion",
        }
        if directory := os.getenv("DECISION_HUB_ONCE_EVIDENCE_DIR"):
            with (Path(directory) / f"{status}.json").open("x", encoding="utf-8") as stream:
                json.dump(observation, stream, ensure_ascii=False, indent=2)
        assert calls == {"build": 1, "tick": 1, "network": 0}
        assert runtime.calls == 0 and runtime.closed == 1
        assert before == after
        assert actual_exit == expected_exit
        assert f"status={status}" in captured.out
        assert "completed" not in captured.out
        assert all(phrase in captured.out for phrase in phrases)
        assert not captured.err
        if status == "preparation_storage_unavailable":
            assert "等待状态已保存" not in captured.out
    finally:
        database.engine.dispose()

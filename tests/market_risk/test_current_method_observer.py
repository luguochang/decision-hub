"""Derived registry failures cannot repeat model execution or change publication."""

import asyncio
import threading

import pytest
from sqlalchemy import select

from apps.hub_worker.research import DurableResearchWorker
from packages.kernel.decision_hub_kernel.persistence.db import RunRoleOutputRecord
from tests.evolution.test_research_worker import NOW, PACK_ROOT, FakeResearchRuntime, _admitted_run


@pytest.mark.parametrize("fail,audit_fail", [(False, False), (True, False), (True, True)])
def test_post_graph_observer_is_off_thread_and_failure_keeps_original_run(
    tmp_path, fail, audit_fail, monkeypatch, capsys
):
    database, _, run_id = _admitted_run(tmp_path)
    runtime = FakeResearchRuntime()
    caller_thread = threading.get_ident()
    observed = []
    original_record_event = database.record_run_event

    def record_event(actual_run_id, sequence, event_type, payload):
        if audit_fail and event_type == "research.current_method_observer.failed":
            raise RuntimeError("controlled derived audit unavailable")
        return original_record_event(actual_run_id, sequence, event_type, payload)

    monkeypatch.setattr(database, "record_run_event", record_event)

    def observe(actual_run_id):
        # Actual graph execution is complete; this has no open original writer.
        with database.session() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            assert (
                session.scalar(
                    select(RunRoleOutputRecord.output_id).where(
                        RunRoleOutputRecord.run_id == actual_run_id,
                        RunRoleOutputRecord.role == "task_policy",
                    )
                )
                is not None
            )
            assert (
                session.scalar(
                    select(RunRoleOutputRecord.output_id).where(
                        RunRoleOutputRecord.run_id == actual_run_id,
                        RunRoleOutputRecord.role == "independent_reviewer",
                    )
                )
                is None
            )
        observed.append((actual_run_id, threading.get_ident(), len(runtime.requests)))
        if fail:
            raise RuntimeError("controlled registry failure")

    worker = DurableResearchWorker(
        database,
        runtime,
        pack_root=PACK_ROOT,
        execution_mode="replay",
        clock=lambda: NOW,
        current_method_observer=observe,
    )
    report = asyncio.run(worker.tick())

    assert report is not None and report.status == "research_only" and report.artifact_id
    assert len(runtime.requests) == 2
    assert len(observed) == 1
    assert observed == [(run_id, observed[0][1], 2)]
    assert observed[0][1] != caller_thread
    run = database.get_run_record(run_id)
    assert run is not None and run.status == "degraded" and run.error_code is None
    events = database.get_timeline(run_id)
    failures = [
        row for row in events if row.event_type == "research.current_method_observer.failed"
    ]
    assert len(failures) == int(fail and not audit_fail)
    if audit_fail:
        assert (
            "research_current_method_observer_storage_receipt_unavailable"
            in capsys.readouterr().out
        )
    assert all(row.event_type != "research.agentic.failed" for row in events)
    assert asyncio.run(worker.tick()) is None
    assert len(runtime.requests) == 2 and len(observed) == 1

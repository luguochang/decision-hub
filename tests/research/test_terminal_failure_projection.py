"""Engineering read-model control; no database, Runtime or real financial result."""

from datetime import UTC, datetime, timedelta

from packages.contracts_py.decision_hub_contracts import ErrorProvenance
from packages.kernel.decision_hub_kernel.persistence.db import ResearchTraceRecord, RunRecord
from packages.query_views.research.service import (
    _failure_view,
    _fallback_stop_reason,
    _latest_failure_summary,
    _trace_failures,
)


def test_first_terminal_cause_survives_later_denied_work_and_duplicate_stop():
    run_id = "engineering-terminal-cause"
    finished = datetime(2026, 10, 15, 17, 26, 45, 895640, tzinfo=UTC)
    first = ErrorProvenance(
        error_code="research_method_binding_failed",
        origin="orchestration",
        cause_code="original_preparation_readset_changed",
        capability_id=None,
        tool_call_id=None,
        retryable=False,
        deadline_ms=None,
    )
    denied = ErrorProvenance(
        error_code="research_run_terminal",
        origin="gateway",
        cause_code=None,
        capability_id="data.historical",
        tool_call_id="engineering-denied",
        retryable=False,
        deadline_ms=None,
    )

    def row(sequence, kind, status, error, summary, instant):
        return ResearchTraceRecord(
            run_id=run_id,
            research_session_id=f"product:{run_id}",
            sequence_no=sequence,
            event_type=kind,
            occurred_at=instant,
            stage="done",
            summary=summary,
            reference_type="run" if kind == "session_stopped" else "capability_call",
            reference_id=run_id if kind == "session_stopped" else "engineering-denied",
            status=status,
            error_code=error.error_code,
            error_provenance_json=error.model_dump_json(),
            event_hash=f"{sequence:064x}",
            created_at=instant,
        )

    traces = [
        # The real observed ordering has finished_at about6.8ms before this Trace.
        row(
            7,
            "session_stopped",
            "failed",
            first,
            "Original research Task changed or could not be verified",
            finished + timedelta(microseconds=6788),
        ),
        row(
            8,
            "tool_failed",
            "denied",
            denied,
            "Capability data.historical was rejected before execution.",
            finished + timedelta(seconds=37),
        ),
        row(
            9,
            "session_stopped",
            "failed",
            first.model_copy(update={"cause_code": "engineering_later_cleanup"}),
            "A later cleanup must not replace the first stop",
            finished + timedelta(seconds=38),
        ),
    ]
    run = RunRecord(
        run_id=run_id,
        event_id="engineering",
        status="failed",
        artifact_id=None,
        created_at=finished - timedelta(minutes=12),
        updated_at=finished,
        finished_at=finished,
        error_code=first.error_code,
    )
    raw_before = [item.error_provenance_json for item in traces]
    assert _failure_view(run, traces) == first
    assert _latest_failure_summary(traces, run=run) == traces[0].summary
    reason = _fallback_stop_reason(run, traces)
    assert reason is not None and "原件核对发现变化" in reason.detail
    assert "rejected before execution" not in reason.detail
    assert {item.error_code for item in _trace_failures(run, traces)} == {
        "research_method_binding_failed",
        "research_run_terminal",
    }
    assert [item.error_provenance_json for item in traces] == raw_before
    run.status, run.artifact_id, run.error_code = "completed", "engineering-artifact", None
    assert _failure_view(run, traces) is None

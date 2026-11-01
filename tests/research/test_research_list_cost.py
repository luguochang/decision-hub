from datetime import timedelta

from sqlalchemy import event

from packages.kernel.decision_hub_kernel.application.commit import CommitDecisionService
from packages.kernel.decision_hub_kernel.application.research_observability import (
    ResearchObservabilityService,
)
from packages.kernel.decision_hub_kernel.persistence.db import Database, EventWatchRecord, RunRecord
from packages.query_views.research import ResearchQueryService
from tests.evolution.test_research_worker import (
    NOW,
    PACK_ROOT,
    ResearchRequestFactory,
    _admitted_run,
    _result,
)
from tests.research.test_research_observability import _trace


def test_inbox_merged_order_and_query_cost_do_not_grow_with_hidden_history(tmp_path):
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'inbox.sqlite3'}")
    database.create_all()
    service = ResearchQueryService(database, pack_root=PACK_ROOT)

    def run(identifier, event_id, at, strategy="research.v1"):
        return RunRecord(run_id=identifier, event_id=event_id, strategy_version=strategy,
                         status="admitted", created_at=at, updated_at=at)

    def watch(identifier, event_id, at):
        return EventWatchRecord(watch_id=identifier, event_id=event_id, source_id="source",
                                event_family="fomc_decision", scheduled_at=at,
                                window_offsets_json="[]", created_at=at, updated_at=at)

    with database.session() as session:
        session.add_all([
            run("run-z", "event-z", NOW), run("run-a", "event-z", NOW),
            run("run-older", "event-hidden", NOW - timedelta(days=10)),
            run("baseline", "event-baseline", NOW, "baseline.v1"),
            watch("watch-y", "event-y", NOW),
            watch("watch-x", "event-x", NOW),
            # A recent watch is excluded even if its research Run is outside top-N.
            watch("watch-hidden", "event-hidden", NOW + timedelta(days=1)),
            watch("watch-baseline", "event-baseline", NOW + timedelta(seconds=1)),
        ])
    statements = []

    def count(*args):
        statements.append(1)

    event.listen(database.engine, "before_cursor_execute", count)
    before = service.inbox(limit=4)
    baseline_count = len(statements)
    assert [(item.event_id, item.run_id) for item in before.items] == [
        ("event-baseline", None), ("event-z", "run-z"),
        ("event-z", "run-a"), ("event-y", None),
    ]
    with database.session() as session:
        session.add_all(run(f"old-{i}", f"old-event-{i}", NOW - timedelta(days=30))
                        for i in range(1000))
    statements.clear()
    after = service.inbox(limit=4)
    assert after.items == before.items
    assert len(statements) == baseline_count
    assert len(statements) < 30
    database.engine.dispose()


def test_saved_run_list_uses_frozen_coverage_without_loading_all_evidence(tmp_path, monkeypatch):
    database, event_id, run_id = _admitted_run(tmp_path)
    request = ResearchRequestFactory(database, pack_root=PACK_ROOT, clock=lambda: NOW).build(
        event_id, run_id
    )
    result = _result(request)
    CommitDecisionService(database, clock=lambda: NOW).commit_research_result(
        run_id, event_id, result
    )
    observability = ResearchObservabilityService(database, clock=lambda: NOW)
    for i in range(40):
        observability.append_trace(_trace(run_id, event_type="tool_started", summary=f"Step {i}"))
    service = ResearchQueryService(database, pack_root=PACK_ROOT)

    def forbidden(*args):
        raise AssertionError("saved result must not recalculate fallback coverage")

    monkeypatch.setattr(service, "_requirements_for_run", forbidden)
    statements = []
    event.listen(database.engine, "before_cursor_execute",
                 lambda conn, cursor, statement, *args: statements.append(statement))
    view = service.list_page(limit=1).items[0]
    assert view.coverage == result.final_coverage
    assert view.stop_reason == result.stop_reason
    assert view.current_action == "Step 39"
    assert view.failure is None
    assert not any("research_evidence" in sql or "research_facts" in sql for sql in statements)
    trace_sql = [sql for sql in statements if "research_trace_events" in sql]
    assert len(trace_sql) == 1 and "LIMIT" in trace_sql[0]
    # The public audit continues to expose the full saved trace history.
    assert len(service.trace(run_id)) >= 40
    database.engine.dispose()

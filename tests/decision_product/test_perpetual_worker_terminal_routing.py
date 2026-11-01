"""Routing only: genuine Graph/committer authentication is covered by native pipeline gates."""

import asyncio
from types import SimpleNamespace

from apps.hub_worker.research import DurableResearchWorker
from tests.evolution.test_research_worker import NOW, _result


def test_perpetual_terminal_forwards_original_graph_locator_without_legacy_review():
    final = _result(SimpleNamespace(current_round=1, evidence_refs=[], request_id="routing"))
    final = final.model_copy(update={"horizons": [], "causal_case": None, "reviews": []})
    locator = dict(
        kind="graph_final_projection",
        row=dict(
            output_id="actual-checkpoint-locator",
            role="research_graph_final",
            schema_version="research-perpetual-graph-final.v1",
            payload_hash="a" * 64,
            recorded_at=NOW.isoformat().replace("+00:00", "Z"),
        ),
    )
    state = dict(final_result=final.model_dump(mode="json"), graph_final_projection_ref=locator)
    calls = []

    class Executor:
        async def execute(self, request):
            return state

    class RejectLegacyReviewer:
        def review(self, *args, **kwargs):
            raise AssertionError("V2 must not invent a V1 review")

    class RecordingCommitter:
        def commit_research_result(self, run_id, event_id, value, **kwargs):
            calls.append((run_id, event_id, value, kwargs))
            return "artifact", SimpleNamespace(status=SimpleNamespace(value="report_only"))

    worker = object.__new__(DurableResearchWorker)
    worker.executor, worker.reviews, worker.commit = (
        Executor(),
        RejectLegacyReviewer(),
        RecordingCommitter(),
    )
    worker.worker_id = "routing-only"
    worker.heavy_work_admission = worker.frozen_run_harness = worker.current_method_observer = None
    worker._factory_for_run = lambda run_id: (object(), False)
    worker._prepare_request_policy = lambda *args: object()
    worker._record_completed = lambda *args: None
    worker._evaluate_terminal_safely = lambda *args: None
    worker._publish_actions_safely = lambda *args: None

    async def renew(*args):
        await asyncio.Event().wait()

    async def expose_failure():
        raise worker._pending_terminal_failure.error

    worker._renew_lease_while_running = renew
    worker._flush_terminal_failure = expose_failure
    report = asyncio.run(worker._execute_claim("event", "run", recovered=False))
    assert report.status == "report_only"
    assert len(calls) == 1 and calls[0][:3] == ("run", "event", final)
    assert calls[0][3]["graph_final_ref"].model_dump(mode="json") == locator
    assert calls[0][3]["expected_lease_owner"] == "routing-only"
    assert state["final_result"] == final.model_dump(mode="json")

"""Real hook ledgers/Worker control flow with component-only publisher doubles.

Task/spot admissions and Graph publication originals are synthetic fixtures;
these tests do not certify the native DSH pipeline or a live model.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from apps.hub_worker.research import DurableResearchWorker
from packages.contracts_py.decision_hub_contracts import ResearchReview
from packages.kernel.decision_hub_kernel.application import (
    action_research_integration as integration,
)
from packages.kernel.decision_hub_kernel.persistence.action_records import DecisionOpportunityRecord
from packages.kernel.decision_hub_kernel.persistence.action_repository import ActionRepository
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    RunEventRecord,
    RunRecord,
    RunRoleOutputRecord,
)
from packages.kernel.decision_hub_kernel.persistence.original_preparation import begin_immediate
from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash
from packages.kernel.decision_hub_kernel.ports.research_synthesis_protocol import (
    DECISION_OUTPUT_SCHEMA_REF,
)
from tests.dsh_native.test_spot_prompt_asset_scope import _spot_request

AT = datetime(2026, 10, 13, tzinfo=UTC)
PACK_HASH = "a" * 64
POLICY_HASH = "b" * 64


class Publisher:
    def __init__(self):
        self.calls = []
        self.fail = False

    def publish(self, **identity):
        self.calls.append(identity)
        if self.fail:
            raise ValueError("action_test_native_unavailable")
        return SimpleNamespace(
            request_id="publish_"
            + canonical_hash([identity["run_id"], identity["opportunity_id"]]),
            receipt_hash="f" * 64, response={"status": "published"},
        )


@pytest.fixture
def context(tmp_path, monkeypatch):
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "isolated"))
    db = Database(f"sqlite+pysqlite:///{tmp_path / 'hook.sqlite'}")
    db.create_all()
    tasks = {}
    monkeypatch.setattr(integration, "_task", lambda _session, run_id: tasks[run_id])
    monkeypatch.setattr(
        integration,
        "read_run_spot_admission_in_session",
        lambda *_: SimpleNamespace(
            scope=SimpleNamespace(
                instrument="BTC-USDT", scope_id="crypto:spot:okx:BTC-USDT:trade:1m"
            )
        ),
    )
    clock = [AT + timedelta(minutes=1)]
    publisher = Publisher()
    service = integration.ActionResearchIntegration(db, publisher=publisher, clock=lambda: clock[0])
    yield SimpleNamespace(db=db, tasks=tasks, clock=clock, publisher=publisher, service=service)
    db.engine.dispose()


def register(ctx, run_id="run-hook", **changes):
    request = _spot_request("BTC-USDT").model_copy(
        update={
            "run_id": run_id,
            "event_id": "event-" + run_id,
            "request_id": "request-" + run_id,
            "output_schema_ref": DECISION_OUTPUT_SCHEMA_REF,
            "research_mode": "current_decision",
            "execution_mode": "live",
            "deadline_at": AT + timedelta(minutes=15),
            **changes,
        }
    )
    ctx.tasks[run_id] = SimpleNamespace(
        request=request,
        request_hash=canonical_hash(request.model_dump(mode="json")),
        pack_definition_hash=PACK_HASH,
    )
    with ctx.db.session() as session:
        begin_immediate(session)
        if session.get(RunRecord, run_id) is None:
            session.add(
                RunRecord(
                    run_id=run_id,
                    event_id=request.event_id,
                    status="running",
                    created_at=AT,
                    updated_at=AT,
                )
            )
            session.flush()
        values = integration.register_action_opportunities_in_session(
            session,
            request=request,
            pack_definition_hash=PACK_HASH,
            compiled_policy_recipe_hash=POLICY_HASH,
            registered_at=AT,
        )
    return request, values


def committed(ctx, request):
    reference = integration.parse_graph_final_ref(
        {
            "kind": "graph_final_projection",
            "row": {
                "output_id": "final-" + request.run_id,
                "role": "graph_final",
                "schema_version": "fixture.v1",
                "payload_hash": "e" * 64,
                "recorded_at": ctx.clock[0].isoformat(),
            },
        }
    )
    identity = dict(
        run_id=request.run_id,
        event_id=request.event_id,
        artifact_id="artifact-" + request.run_id,
        reference=reference,
    )
    body = {
        **identity,
        "reference": None,
        "schema_version": "research-publication-source.v4",
        "graph_final_ref": reference.model_dump(mode="json"),
        "recorded_at": ctx.clock[0].isoformat(),
    }
    body.pop("reference")
    payload = integration._json(body)
    digest = canonical_hash(body)
    with ctx.db.session() as session:
        run = session.get(RunRecord, request.run_id)
        run.status, run.artifact_id, run.finished_at = (
            "completed",
            identity["artifact_id"],
            ctx.clock[0],
        )
        session.add(
            RunRoleOutputRecord(
                output_id="publication_" + digest[:40],
                run_id=request.run_id,
                role=integration.PUBLICATION_SOURCE_ROLE,
                schema_version=body["schema_version"],
                payload_json=payload,
                payload_hash=digest,
                created_at=ctx.clock[0],
            )
        )
    return identity


def test_registration_is_event_idempotent_and_recipe_not_task_identity(context):
    request, first = register(context)
    _, replay = register(context)
    _, second = register(context, "run-second")
    assert replay == first and first[0].recipe_hash == second[0].recipe_hash
    assert {item.kind for item in first} == {"event"}
    assert {item.horizon for item in first} == {"30m", "24h", "72h"}
    for item in first:
        assert request.run_id in item.key
        assert item.registered_at == item.time_start == AT
        assert item.time_end == request.deadline_at + timedelta(
            seconds=integration.HORIZONS[item.horizon]
        )
    with context.db.session() as session:
        assert len(session.scalars(select(DecisionOpportunityRecord)).all()) == 6


@pytest.mark.parametrize(
    "changes",
    [
        {"output_schema_ref": "agentic_research.schema.yaml#/$defs/research_session_result"},
        {"research_mode": "historical_review"},
    ],
)
def test_old_or_historical_tasks_have_no_opportunities(context, changes):
    request, values = register(context, **changes)
    assert values == ()
    with context.db.session() as session:
        assert integration._row(session, request.run_id, integration.REGISTRATION_ROLE) is None


def test_opportunity_registration_rollback_and_changed_time_conflict(context):
    request, _ = register(context)
    with pytest.raises(ValueError, match="immutable"):
        with context.db.session() as session:
            begin_immediate(session)
            integration.register_action_opportunities_in_session(
                session,
                request=request,
                pack_definition_hash=PACK_HASH,
                compiled_policy_recipe_hash=POLICY_HASH,
                registered_at=AT + timedelta(seconds=1),
            )
    with context.db.session() as session:
        assert len(session.scalars(select(DecisionOpportunityRecord)).all()) == 3


def test_report_commit_publishes_all_registered_horizons_and_replays_same_keys(context):
    request, opportunities = register(context)
    identity = committed(context, request)
    first = context.service.publish_committed(**identity)
    repeated = context.service.publish_committed(**identity)
    assert first == repeated
    assert len(context.publisher.calls) == 3
    assert {row["opportunity_id"] for row in first} == {item.id for item in opportunities}
    assert all(row["status"] == "published" for row in first)
    assert all(
        set(call) == {"run_id", "event_id", "artifact_id", "reference", "opportunity_id"}
        for call in context.publisher.calls
    )


def test_failure_is_separate_from_report_with_bounded_retry_and_missed_denominator(context):
    request, opportunities = register(context)
    identity = committed(context, request)
    context.publisher.fail = True
    assert all(row["status"] == "failed" for row in context.service.publish_committed(**identity))
    assert all(row["status"] == "deferred" for row in context.service.publish_committed(**identity))
    for _ in range(2):
        context.clock[0] += timedelta(minutes=16)
        context.service.reconcile_missed_hooks()
    assert len(context.publisher.calls) == 9
    assert context.service.reconcile_missed_hooks() == ()
    with context.db.session() as session:
        assert session.get(RunRecord, request.run_id).status == "completed"
        assert all(
            ActionRepository(session).get_opportunity(item.id).status == "missed"
            for item in opportunities
        )
        events = session.scalars(select(RunEventRecord)).all()
        assert sum(row.event_type == "action.publication.failed" for row in events) == 9
        assert sum(row.event_type == "action.publication.exhausted" for row in events) == 3


def test_missed_hook_reconcile_is_fixed_maximum_three_and_empty_is_fast(context):
    assert context.service.reconcile_missed_hooks() == ()
    for run_id in ("run-a", "run-b"):
        request, _ = register(context, run_id)
        committed(context, request)
    first = context.service.reconcile_missed_hooks()
    assert len(first) == len(context.publisher.calls) == 3
    assert len(context.service.reconcile_missed_hooks()) == 3
    assert len(context.publisher.calls) == 6
    assert context.service.reconcile_missed_hooks() == ()
    with pytest.raises(ValueError, match="batch_limit"):
        context.service.reconcile_missed_hooks(max_candidates=4)


def test_reconcile_uses_committed_exact_ref_and_no_registration_is_never_backfilled(context):
    request, _ = register(context)
    identity = committed(context, request)
    with pytest.raises(ValueError, match="committed_identity_mismatch"):
        context.service.publish_committed(**{**identity, "artifact_id": "different"})
    context.service.reconcile_missed_hooks()
    assert all(call["reference"] == identity["reference"] for call in context.publisher.calls)
    old, _ = register(
        context,
        "old",
        output_schema_ref="agentic_research.schema.yaml#/$defs/research_session_result",
    )
    assert context.service.publish_committed(**committed(context, old)) == ()
    with context.db.session() as session:
        assert len(session.scalars(select(DecisionOpportunityRecord)).all()) == 3


@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_terminal_without_report_closes_registered_denominator_without_publisher(context, status):
    request, _ = register(context)
    with context.db.session() as session:
        run = session.get(RunRecord, request.run_id)
        run.status, run.error_code = status, "research_fixture_terminal"
    output = context.service.reconcile_missed_hooks()
    assert len(output) == 3
    assert {item["status"] for item in output} == {"missed"}
    assert {item["reason"] for item in output} == {"research_run_" + status}
    assert not context.publisher.calls
    assert context.service.reconcile_missed_hooks() == ()
    with context.db.session() as session:
        assert session.get(RunRecord, request.run_id).status == status
        assert {row.status for row in session.scalars(select(DecisionOpportunityRecord))} == {
            "missed"
        }


def test_running_is_not_missed_before_window_and_expiry_shares_three_candidate_budget(context):
    for run_id in ("run-a", "run-b"):
        register(context, run_id)
    assert context.service.reconcile_missed_hooks() == ()
    context.clock[0] += timedelta(days=4)
    assert len(context.service.reconcile_missed_hooks()) == 3
    with context.db.session() as session:
        rows = session.scalars(select(DecisionOpportunityRecord)).all()
        assert sum(row.status == "open" for row in rows) == 3
        assert sum(row.status == "expired" for row in rows) == 3
        assert {row.status for row in session.scalars(select(RunRecord))} == {"running"}
    assert len(context.service.reconcile_missed_hooks()) == 3
    assert context.service.reconcile_missed_hooks() == ()
    assert not context.publisher.calls


def test_expired_opportunity_records_missed_without_research_retry(context):
    request, _ = register(context)
    committed(context, request)
    context.clock[0] += timedelta(days=4)
    results = context.service.reconcile_missed_hooks()
    assert len(results) == 3 and all(row["status"] == "expired" for row in results)
    assert not context.publisher.calls
    with context.db.session() as session:
        assert {row.status for row in session.scalars(select(DecisionOpportunityRecord))} == {
            "missed"
        }
        assert session.get(RunRecord, request.run_id).status == "completed"


def test_success_response_lost_recovers_original_receipt_before_budget_or_expiry(
    context, monkeypatch
):
    request, opportunities = register(context)
    identity = committed(context, request)
    command_hash = canonical_hash(
        {
            **identity,
            "reference": identity["reference"].model_dump(mode="json"),
            "opportunity_id": opportunities[0].id,
        }
    )
    key = "publish_" + canonical_hash([request.run_id, opportunities[0].id])
    monkeypatch.setattr(
        ActionRepository,
        "get_command",
        lambda _self, _owner, request_id: (
            SimpleNamespace(
                operation="publish",
                payload_hash=command_hash,
                request_id=key,
                receipt_hash="f" * 64, response={"status": "published"},
            )
            if request_id == key
            else None
        ),
    )
    context.clock[0] += timedelta(days=4)
    result = context.service._attempt(identity, opportunities[0])
    assert result["status"] == "published" and result["attempt"] == 0
    assert not context.publisher.calls


def test_worker_hook_storage_failure_is_logged_without_raising_or_changing_report(context, caplog):
    worker = object.__new__(DurableResearchWorker)
    worker.action_integration = SimpleNamespace(
        publish_committed=lambda **_: (_ for _ in ()).throw(ValueError("invalid"))
    )
    worker.database = SimpleNamespace(
        record_run_event=lambda *_: (_ for _ in ()).throw(RuntimeError("db-down"))
    )
    worker._next_event_sequence = lambda _: 1
    worker._publish_actions_safely("run-hook", "event-hook", "report-success", object())
    assert "action_hook_audit_storage_unavailable" in caplog.text


@pytest.mark.asyncio
async def test_normal_worker_commit_return_survives_action_failure_and_does_not_rerun_model(
    context,
):
    from tests.research.test_review_schedule import _result

    final = _result()
    request, _ = register(context)
    identity = committed(context, request)
    calls = []

    async def execute(*_args, **_kwargs):
        calls.append("model")
        return {
            "final_result": final.model_dump(mode="json"),
            "graph_final_projection_ref": identity["reference"].model_dump(mode="json"),
        }

    async def nothing(*_args):
        return None

    @asynccontextmanager
    async def scope():
        yield

    worker = object.__new__(DurableResearchWorker)
    worker.database = context.db
    worker.clock = lambda: context.clock[0]
    worker.worker_id = "component-worker"
    worker.frozen_run_harness = None
    worker.current_method_observer = None
    worker.heavy_work_admission = None
    worker._heavy_work_scope = scope
    worker._renew_lease_while_running = nothing
    worker._stop_lease_renewal = nothing
    worker._factory_for_run = lambda _run: (SimpleNamespace(build=lambda *_: request), False)
    worker.executor = SimpleNamespace(execute=execute)
    review = ResearchReview(
        review_id="review-1",
        role="independent_reviewer",
        target_claim="fixture",
        evidence_refs=["ev-1"],
        result_hash="a" * 64,
        challenge="fixture",
        resolution="accepted",
        limitations=[],
        created_at=AT,
    )
    worker.reviews = SimpleNamespace(review=lambda *_args, **_kwargs: review)
    worker.commit = SimpleNamespace(
        commit_research_result=lambda *_args, **_kwargs: (
            identity["artifact_id"],
            SimpleNamespace(status=SimpleNamespace(value="completed")),
        )
    )
    worker._record_completed = lambda *_: calls.append("report_completed")
    worker._evaluate_terminal_safely = lambda _: None
    worker.action_integration = context.service
    context.publisher.fail = True
    result = await worker._execute_claim(request.event_id, request.run_id, recovered=False)
    assert result.status == "completed" and result.artifact_id == identity["artifact_id"]
    assert calls == ["model", "report_completed"]
    assert len(context.publisher.calls) == 3
    await asyncio.sleep(0)



"""UI boundary with real private SQLite publication/commands and a named native fixture.

The imported publication fixture replaces native authority. Its test-only 'live'
label exercises prospective routing, never a real provider/market acceptance.
The production API main/default DB is deliberately not imported.
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from apps.hub_api.action_routes import action_router
from packages.kernel.decision_hub_kernel.application.action_plan import ActionPlanService
from packages.kernel.decision_hub_kernel.persistence.action_records import (
    ActionCommandReceiptRecord,
    ActionConditionEvaluationRecord,
    ActionPlanHeadRecord,
)
from packages.kernel.decision_hub_kernel.persistence.action_repository import ActionRepository
from packages.kernel.decision_hub_kernel.persistence.db import RunRoleOutputRecord
from packages.kernel.decision_hub_kernel.persistence.original_preparation import begin_immediate
from packages.kernel.decision_hub_kernel.ports.action_plan import (
    ActionPlanHead,
    ActionPlanRevision,
    ConditionEvaluation,
    Opportunity,
)
from packages.query_views.decision_product import DecisionProductQuery

from .action_fixtures import seal
from .test_action_publication import context as publication_context

context = publication_context


def publish(context, suffix=""):
    prospective = Opportunity.model_validate(
        {
            **context.opportunity.model_dump(mode="python"),
            "id": "op-ui-prospective" + suffix,
            "key": "ui-prospective" + suffix,
            "cohort": "prospective",
        }
    )
    context.native.execution_mode = "live"  # Explicit fixture; no real native authority.
    with context.database.session() as session:
        begin_immediate(session)
        ActionRepository(session).register_opportunity(prospective)
    context.opportunity = prospective
    context.args["opportunity_id"] = prospective.id
    return context.service.publish(**context.args)


def query(context, *, enabled=True):
    return DecisionProductQuery(context.database, enabled=enabled, clock=lambda: context.clock[0])


def app_client(context, *, enabled=True):
    def owner(value):
        if value != "owner":
            raise HTTPException(403, "owner_identity_required")
        return value

    app = FastAPI()
    app.include_router(
        action_router(context.database, owner, enabled=enabled, clock=lambda: context.clock[0])
    )
    return TestClient(app)


def command(branch, *, operation="pause", key="request-ui"):
    return {
        "request_id": key,
        "expected_revision_id": branch.revision.revision_id,
        "expected_state_version": branch.head.state_version,
        "operation": operation,
        "payload": {},
    }


def test_current_three_positions_probability_mechanisms_and_scope_isolation(context):
    original = publish(context)
    view = query(context).current(context.opportunity.scope_id)
    assert view.decision.id == original.response["decision_id"]
    assert view.availability == "available" and view.gap_codes == []
    flat, long, unknown = view.branches
    assert flat.probability.value is None and long.probability.value == 0.61
    assert flat.causal_steps and flat.causal_steps[0].counter_refs
    assert unknown.probability is None and unknown.revision.position_context == "unknown"
    assert (
        query(context).current(context.opportunity.scope_id.replace("BTC", "ETH")).decision is None
    )
    assert query(context).current(context.opportunity.scope_id, horizon="72h").decision is None


def test_diagnostic_publication_cannot_appear_in_live_current(context):
    receipt = context.service.publish(**context.args)
    result = query(context).current(context.opportunity.scope_id)
    assert result.decision is None and result.latest_opportunity is None
    assert result.availability == "unavailable"
    with pytest.raises(ValueError, match="action_view_cursor_invalid"):
        query(context).tracking(
            context.opportunity.scope_id, cursor=receipt.response["branches"][0]["plan_id"]
        )


def test_current_keeps_new_missed_opportunity_distinct_from_old_success(context):
    old = publish(context)
    context.clock[0] += timedelta(minutes=1)
    later = Opportunity.model_validate(
        {
            **context.opportunity.model_dump(mode="python"),
            "id": "later",
            "key": "later",
            "registered_at": context.clock[0],
            "time_start": context.clock[0],
        }
    )
    with context.database.session() as session:
        begin_immediate(session)
        repo = ActionRepository(session)
        repo.register_opportunity(later)
        repo.set_opportunity_status(
            later.id, expected_status="open", status="missed", reason="test_source_failure"
        )
    view = query(context).current(later.scope_id)
    assert view.latest_opportunity.id == "later" and view.latest_opportunity.status == "missed"
    assert view.decision.id == old.response["decision_id"]
    assert view.gap_codes == ["action_latest_opportunity_unpublished"]


def test_current_clock_and_expiry_are_explicit(context):
    publish(context)
    context.clock[0] -= timedelta(seconds=1)
    assert query(context).current(context.opportunity.scope_id).decision is None
    context.clock[0] += timedelta(days=2)
    result = query(context, enabled=False).current(context.opportunity.scope_id)
    assert result.availability == "disabled"
    assert all(branch.expired for branch in result.branches)


def test_current_rechecks_sidecar_original_hash(context):
    publish(context)
    with context.database.session() as session:
        row = session.scalar(select(RunRoleOutputRecord))
        body = json.loads(row.payload_json)
        body["payload_hash"] = "0" * 64
        row.payload_json = json.dumps(body)
    with pytest.raises(ValueError, match="action_view_source_original_mismatch"):
        query(context).current(context.opportunity.scope_id)


def test_unpublished_repository_rows_are_not_published_ui(context):
    publish(context)
    # Private fixture fault injection drops only its append-only deletion guard.
    with context.database.engine.begin() as connection:
        triggers = (
            connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='trigger' "
                "AND tbl_name='action_command_receipts' AND sql LIKE '%DELETE%'"
            )
            .scalars()
            .all()
        )
        for name in triggers:
            assert name.replace("_", "").isalnum()
            connection.exec_driver_sql(f'DROP TRIGGER "{name}"')
        connection.execute(ActionCommandReceiptRecord.__table__.delete())
    with pytest.raises(ValueError, match="action_view_publication_missing"):
        query(context).current(context.opportunity.scope_id)


def test_detail_reads_exact_revision_and_invalid_cursor_rejected(context):
    publish(context)
    branch = query(context).current(context.opportunity.scope_id).branches[0]
    detail = query(context).plan(branch.head.plan_id, revision_id=branch.revision.revision_id)
    assert detail.branch.revision == branch.revision
    assert detail.evaluations == [] and detail.next_cursor is None
    with pytest.raises(KeyError, match="action_revision_not_found"):
        query(context).plan(branch.head.plan_id, revision_id="foreign")
    with pytest.raises(ValueError, match="action_view_cursor_invalid"):
        query(context).plan(branch.head.plan_id, cursor="foreign")


def test_display_and_ack_are_owner_bound_received_events_not_fills(context):
    publish(context)
    service = ActionPlanService(context.database, clock=lambda: context.clock[0])
    branch = query(context).current(context.opportunity.scope_id).branches[0]
    for operation in ("display", "ack"):
        service.record_command(
            owner_id="owner",
            plan_id=branch.head.plan_id,
            **command(branch, operation=operation, key=operation),
        )
    view = query(context).current(context.opportunity.scope_id, owner_id="owner").branches[0]
    assert view.first_display_at == view.first_ack_at == context.clock[0]
    assert view.head == branch.head and view.head.state == "armed"
    assert (
        query(context)
        .current(context.opportunity.scope_id, owner_id="other")
        .branches[0]
        .first_ack_at
        is None
    )


def test_http_validation_owner_and_typed_disabled_read(context):
    client = app_client(context, enabled=False)
    path = "/v1/actions/current"
    response = client.get(path, params={"scope_id": context.opportunity.scope_id})
    assert response.status_code == 200 and response.json()["availability"] == "disabled"
    assert response.headers["Cache-Control"] == "no-store"
    assert client.get(path, params={"scope_id": "wrong"}).status_code == 404
    assert (
        client.get(
            path, params={"scope_id": context.opportunity.scope_id, "horizon": "7d"}
        ).status_code
        == 422
    )
    assert client.get("/v1/actions/commands/key").status_code == 403


def test_http_exact_command_replay_conflict_and_lost_response_recovery(context):
    publish(context)
    client = app_client(context)
    branch = query(context).current(context.opportunity.scope_id).branches[0]
    path = f"/v1/actions/plans/{branch.head.plan_id}/commands"
    headers = {"X-Owner-Id": "owner"}
    payload = command(branch)
    assert client.post(path, json=payload).status_code == 403
    one = client.post(path, json=payload, headers=headers)
    assert one.status_code == 200, one.text
    assert one.json()["response"]["head"]["monitor_state"] == "paused"
    recovered = client.get("/v1/actions/commands/request-ui", headers=headers)
    assert recovered.status_code == 200 and recovered.json() == one.json()
    context.clock[0] += timedelta(days=2)
    assert client.post(path, json=payload, headers=headers).json() == one.json()
    assert (
        app_client(context, enabled=False).post(path, json=payload, headers=headers).json()
        == one.json()
    )
    conflict = client.post(path, json={**payload, "operation": "resume"}, headers=headers)
    assert (
        conflict.status_code == 409 and conflict.json()["detail"] == "action_command_key_conflict"
    )
    fresh = client.post(path, json={**payload, "request_id": "new"}, headers=headers)
    assert fresh.status_code == 409


def test_http_disabled_new_command_rejected_and_pending_get_404(context):
    publish(context)
    client = app_client(context, enabled=False)
    branch = query(context).current(context.opportunity.scope_id).branches[0]
    headers = {"X-Owner-Id": "owner"}
    response = client.post(
        f"/v1/actions/plans/{branch.head.plan_id}/commands", headers=headers, json=command(branch)
    )
    assert response.status_code == 503 and response.json()["detail"] == "action_product_disabled"
    assert client.get("/v1/actions/commands/request-ui", headers=headers).status_code == 404


def test_http_stale_state_and_arbitrary_payload_cannot_mutate(context):
    publish(context)
    client = app_client(context)
    branch = query(context).current(context.opportunity.scope_id).branches[0]
    headers = {"X-Owner-Id": "owner"}
    path = f"/v1/actions/plans/{branch.head.plan_id}/commands"
    payload = command(branch)
    assert (
        client.post(path, headers=headers, json={**payload, "payload": {"price": 1}}).status_code
        == 422
    )
    with context.database.session() as session:
        session.execute(
            update(ActionPlanHeadRecord)
            .where(ActionPlanHeadRecord.plan_id == branch.head.plan_id)
            .values(state_version=2)
        )
    result = client.post(path, headers=headers, json=payload)
    assert result.status_code == 409 and result.json()["detail"] == "action_state_version_conflict"


def test_original_revision_is_readable_with_current_head_but_never_rewritable(context):
    publish(context)
    branch = query(context).current(context.opportunity.scope_id).branches[0]
    revision = seal(
        ActionPlanRevision,
        {
            **branch.revision.model_dump(mode="python"),
            "revision_id": "revision-ui-2",
            "revision_no": 2,
            "previous_id": branch.revision.revision_id,
        },
        "payload_hash",
    )
    head = ActionPlanHead.model_validate(
        {
            **branch.head.model_dump(mode="python"),
            "current_revision_id": revision.revision_id,
            "state_version": 2,
            "lease_generation": 1,
        }
    )
    with context.database.session() as session:
        begin_immediate(session)
        ActionRepository(session).append_revision(
            revision,
            expected_revision_id=branch.revision.revision_id,
            expected_state_version=1,
            next_head=head,
        )
    detail = query(context).plan(branch.head.plan_id, revision_id=branch.revision.revision_id)
    assert detail.branch.revision == branch.revision and detail.branch.head == head
    assert (
        "action_published_revision_superseded"
        in query(context).current(context.opportunity.scope_id).gap_codes
    )
    response = app_client(context).post(
        f"/v1/actions/plans/{branch.head.plan_id}/commands",
        headers={"X-Owner-Id": "owner"},
        json=command(branch),
    )
    assert response.status_code == 409 and response.json()["detail"] == "action_revision_superseded"
    with pytest.raises(ValueError, match="action_view_revision_not_published"):
        query(context).plan(branch.head.plan_id)


def test_evaluation_pages_are_bounded_stable_and_original_checked(context):
    publish(context)
    branch = query(context).current(context.opportunity.scope_id).branches[0]
    records = []
    for i in range(25):
        tick = context.clock[0] + timedelta(seconds=i)
        value = seal(
            ConditionEvaluation,
            {
                "schema_version": "decision-condition-evaluation.v1",
                "id": f"eval-ui-{i:02}",
                "plan_id": branch.head.plan_id,
                "revision_id": branch.revision.revision_id,
                "tick_at": tick,
                "as_of": tick,
                "evaluated_at": tick,
                "lease_generation": i + 1,
                "input_ref": "synthetic:page-fixture",
                "input_hash": "c" * 64,
                "truth": "unknown",
                "status": "ok",
                "per_condition": [],
                "state_before": "armed",
                "state_after": "armed",
            },
            "evaluation_hash",
        )
        records.append(
            ActionConditionEvaluationRecord(
                evaluation_id=value.id,
                plan_id=value.plan_id,
                revision_id=value.revision_id,
                tick_at=value.tick_at,
                lease_generation=value.lease_generation,
                input_hash=value.input_hash,
                payload_json=value.model_dump_json(),
                payload_hash=value.evaluation_hash,
            )
        )
    with context.database.session() as session:
        session.add_all(records)
    context.clock[0] += timedelta(minutes=1)
    first = query(context).plan(branch.head.plan_id)
    assert len(first.evaluations) == 20 and first.next_cursor == "eval-ui-05"
    second = query(context).plan(branch.head.plan_id, cursor=first.next_cursor)
    assert len(second.evaluations) == 5 and second.next_cursor is None
    assert len({value.id for value in [*first.evaluations, *second.evaluations]}) == 25
    with context.database.engine.begin() as connection:
        triggers = (
            connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='trigger' "
                "AND tbl_name='action_condition_evaluations' AND sql LIKE '%UPDATE%'"
            )
            .scalars()
            .all()
        )
        for name in triggers:
            assert name.replace("_", "").isalnum()
            connection.exec_driver_sql(f'DROP TRIGGER "{name}"')
        connection.execute(
            update(ActionConditionEvaluationRecord)
            .where(ActionConditionEvaluationRecord.evaluation_id == first.next_cursor)
            .values(payload_hash="f" * 64)
        )
    with pytest.raises(ValueError, match="action_record_binding_mismatch"):
        query(context).plan(branch.head.plan_id, cursor=first.next_cursor)


def test_new_research_never_hides_older_triggered_protection_and_tracking_is_bounded(context):
    triggered = []
    for i in range(6):
        receipt = publish(context, str(i))
        ids = [branch["plan_id"] for branch in receipt.response["branches"][:2]]
        with context.database.session() as session:
            # Test-only state setup, not a claimed real monitor trigger.
            session.execute(
                update(ActionPlanHeadRecord)
                .where(ActionPlanHeadRecord.plan_id.in_(ids))
                .values(state="triggered")
            )
        triggered.extend(ids)
    context.clock[0] += timedelta(seconds=1)
    latest = publish(context, "new")
    result = query(context).current(context.opportunity.scope_id)
    assert result.decision.id == latest.response["decision_id"]
    assert result.still_tracking_count == 12 and len(result.still_tracking) == 10
    assert not (
        {item.plan_id for item in result.still_tracking}
        & {branch["plan_id"] for branch in latest.response["branches"]}
    )
    following = query(context).tracking(
        context.opportunity.scope_id, cursor=result.tracking_next_cursor
    )
    assert following.count == 12 and len(following.items) == 2 and following.next_cursor is None
    assert {item.plan_id for item in [*result.still_tracking, *following.items]} == set(triggered)
    client = app_client(context)
    response = client.get("/v1/actions/tracking", params={"scope_id": context.opportunity.scope_id})
    assert response.status_code == 200 and response.json()["count"] == 12
    assert query(context).tracking(context.opportunity.scope_id, horizon="72h").count == 0
    with pytest.raises(ValueError, match="action_view_cursor_invalid"):
        query(context).tracking(context.opportunity.scope_id, cursor="foreign")
    context.clock[0] += timedelta(days=2)
    # A due-but-not-evaluated protection remains visible until durable completion.
    overdue = query(context).tracking(context.opportunity.scope_id)
    assert overdue.count == 12 and all(
        item.valid_until < context.clock[0] for item in overdue.items
    )

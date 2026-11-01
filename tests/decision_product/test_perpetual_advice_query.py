"""Signed read model; pure publication fixture is not native/provider authority."""

import importlib
import importlib.util
from datetime import timedelta

from sqlalchemy import event

from tests.decision_product.test_okx_perpetual_original import (
    persisted_original as persisted_original,
)
from tests.decision_product.test_perpetual_research_binding import AT, OWNER, changed_risk, freeze
from tests.decision_product.test_perpetual_research_binding import binding_case as binding_case
from tests.decision_product.test_perpetual_research_entry import BASE, client_for, command

MODULE = "packages.kernel.decision_hub_kernel.application.perpetual_advice_query"


def service(case, *, now=AT):
    assert importlib.util.find_spec(MODULE), "signed perpetual advice current query is missing"
    return importlib.import_module(MODULE).PerpetualAdviceQuery(case["db"], now=lambda: now)


def params(case):
    body = command(case)
    return dict(
        **body["partition"],
        expected_settings_ref=body["expected_settings_revision"]["ref"],
        expected_settings_hash=body["expected_settings_revision"]["hash"],
        expected_settings_state_version=body["expected_settings_state_version"],
        horizon="4h",
    )


def current(case, **kwargs):
    return service(case, **kwargs).current(OWNER, **params(case))


def test_signed_query_returns_explicit_states_without_writes_or_collection(binding_case):
    case = binding_case
    query = service(case)
    assert current(case)["status"] == "not_started"
    client, _, collector = client_for(case)
    route = BASE + "/advice/current"
    statements = []

    def record(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement.lstrip().split(None, 1)[0].upper())

    event.listen(case["db"].engine, "before_cursor_execute", record)
    try:
        reply = client.get(route, params=params(case))
        assert reply.status_code == 200, reply.text
        assert reply.json()["status"] == "not_started"
        anonymous, _, _ = client_for(case, pair=False)
        assert anonymous.get(route, params=params(case)).status_code == 403
        assert client.get(route, params={**params(case), "horizon": "30m"}).status_code == 422
    finally:
        event.remove(case["db"].engine, "before_cursor_execute", record)
    assert not set(statements) & {"INSERT", "UPDATE", "DELETE", "REPLACE"}
    assert collector.calls == 0
    freeze(case)
    assert current(case)["status"] == "researching"
    from packages.kernel.decision_hub_kernel.persistence.db import RunRecord

    for status, expected in (("failed", "failed"), ("degraded", "awaiting_publication")):
        with case["db"].session() as session:
            session.get(RunRecord, case["run_id"]).status = status
        assert current(case)["status"] == expected
    old = params(case)
    case["settings_service"].save(OWNER, changed_risk(case, "advice-settings-changed"))
    changed = query.current(OWNER, **old)
    assert changed["status"] == "settings_changed" and changed["snapshot"] is None
    assert changed["settings_revision"] == dict(
        ref=old["expected_settings_ref"], hash=old["expected_settings_hash"]
    )


def persisted_publication(case, *, omit_comparison=False):
    """Real originals/repository writes from pure derive, without a Graph qualification claim."""
    import json
    from dataclasses import replace

    from packages.kernel.decision_hub_kernel.application.perpetual_action_publication import (
        _store_original,
    )
    from packages.kernel.decision_hub_kernel.application.perpetual_research_binding import (
        ADMISSION_ROLE,
        _read,
    )
    from packages.kernel.decision_hub_kernel.decision.perpetual_publication import (
        derive_perpetual_publication,
    )
    from packages.kernel.decision_hub_kernel.decision.publication_anchor import (
        freeze_publication_anchor,
    )
    from packages.kernel.decision_hub_kernel.persistence.action_repository import (
        ActionRepository,
        make_command_receipt,
    )
    from packages.kernel.decision_hub_kernel.persistence.original_preparation import begin_immediate
    from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash
    from tests.contracts.test_d2_candidate_contract import body_ref
    from tests.decision_product.test_perpetual_action_publication import lawful_packet_for
    from tests.decision_product.test_perpetual_publication import fixture

    from packages.kernel.decision_hub_kernel.persistence.db import ArtifactRecord, RunRecord

    freeze(case)
    with case["db"].session() as session:
        admission, admission_ref = _read(session, case["run_id"], ADMISSION_ROLE)
    from tests.decision_product.perpetual_advice_fixture import publication_materials

    request, materials = publication_materials(case)
    packet = lawful_packet_for(request, materials)
    synthesis = packet["synthesis"]
    template, _, costs = fixture(unknown=True)
    authorization = json.loads(template.authorization_json)
    authorization.update(
        review_request_ref=body_ref("review", packet),
        synthesis_ref=packet["synthesis_ref"],
        candidate_set_ref=synthesis["candidate_set_ref"],
        candidate_target_ref=synthesis["candidate_set"]["candidate_target_ref"],
        anchor_rule_ref=synthesis["candidate_set"]["anchor_rule_ref"],
        event_probability_ref=synthesis["event_probability_ref"],
        market_distribution_ref=synthesis["market_distribution_ref"],
        method_ref=synthesis["method_ref"],
        method_code_hash=synthesis["method_code_hash"],
        allowed_domain_ref=synthesis["allowed_domain_ref"],
        reviewed_at=(AT + timedelta(seconds=20)).isoformat().replace("+00:00", "Z"),
        valid_until=packet["allowed_domain"]["valid_until"],
        invalidation_refs=packet["allowed_domain"]["invalidation_refs"],
        reviewer_input_ref=body_ref("review", packet),
    )
    trusted = replace(
        template,
        run_id=case["run_id"],
        owner_id=OWNER,
        opportunity_id=admission["opportunity_id"],
        admission_ref_json=json.dumps(admission_ref),
        synthesis_json=json.dumps(synthesis),
        review_request_json=json.dumps(packet),
        authorization_json=json.dumps(authorization),
        materials_json=json.dumps(materials),
        known_at=AT + timedelta(seconds=20),
    )
    assigned = AT + timedelta(seconds=30)
    bound = freeze_publication_anchor(
        candidate_target=synthesis["candidate_target"],
        anchor_rule=synthesis["anchor_rule"],
        candidate_target_ref=synthesis["candidate_set"]["candidate_target_ref"],
        anchor_rule_ref=synthesis["candidate_set"]["anchor_rule_ref"],
        assigned_at=assigned,
        opportunity_id=admission["opportunity_id"],
        commit_id="pure-read-model-test",
    )
    derived = derive_perpetual_publication(trusted=trusted, bound=bound, cost_original=costs)
    revision, head = derived.branches[0].revision, derived.branches[0].head
    with case["db"].session() as session:
        begin_immediate(session)
        repo = ActionRepository(session)
        for original in derived.originals:
            if omit_comparison and original.role == "comparison":
                continue
            _store_original(session, case["run_id"], original, assigned)
        repo.append_snapshot(derived.snapshot)
        repo.create_plan(revision, head=head)
        repo.set_opportunity_status(
            admission["opportunity_id"], expected_status="open", status="decided", reason=None
        )
        repo.append_command_receipt(
            make_command_receipt(
                owner_id="system:action-publication:v1",
                request_id="publish_"
                + canonical_hash([case["run_id"], admission["opportunity_id"]]),
                plan_id=revision.plan_id,
                operation="publish",
                payload_hash="a" * 64,
                accepted_at=assigned,
                response=dict(
                    status="published",
                    opportunity_id=admission["opportunity_id"],
                    decision_id=derived.snapshot.id,
                    snapshot_hash=derived.snapshot.snapshot_hash,
                    branches=[
                        dict(
                            position_context=revision.position_context,
                            plan_id=revision.plan_id,
                            revision_id=revision.revision_id,
                            revision_hash=revision.payload_hash,
                            outbox_id="pure-fixture",
                        )
                    ],
                    qualification="research_trial",
                    execution="paper_only",
                    uncovered_branches=list(derived.uncovered_branches),
                    gaps=list(derived.gaps),
                    server_time=assigned.isoformat(),
                ),
            )
        )
        run = session.get(RunRecord, case["run_id"])
        run.status, run.finished_at, run.updated_at, run.artifact_id = (
            "degraded",
            assigned,
            assigned,
            "pure-report",
        )
        session.add(
            ArtifactRecord(
                artifact_id="pure-report",
                run_id=run.run_id,
                event_id=run.event_id,
                gate_status="degraded",
                headline="Synthetic pure read-model fixture",
                summary="No native authority claim",
                payload_json="{}",
                created_at=assigned,
            )
        )
    return derived


def test_persisted_v2_publication_originals_available_expired_and_no_foreign_or_old_fallback(
    binding_case,
):
    case = binding_case
    derived = persisted_publication(case)
    available = current(case, now=AT + timedelta(seconds=40))
    assert available["status"] == "available"
    assert available["snapshot"] == derived.snapshot.model_dump(mode="json")
    assert available["target"] == derived.bound.target.model_dump(mode="json")
    assert available["context"]["account_snapshot"]["equity"] is None
    assert available["comparison"]["executable_contracts"] is None
    assert available["candidate_probability"]["probabilities"]
    from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash

    assert (
        canonical_hash(available["candidate_probability"])
        == available["frozen_forecast"]["candidate_probability_ref"]["hash"]
    )
    import json
    from pathlib import Path

    Path("artifacts/d2-perpetual-advice/available-pure-persisted-example.json").write_text(
        json.dumps(available, ensure_ascii=False, indent=2), "utf-8"
    )
    expired = current(case, now=derived.branches[0].revision.valid_until + timedelta(seconds=1))
    assert expired["status"] == "expired" and expired["snapshot"] == available["snapshot"]
    foreign = service(case).current("other-owner", **params(case))
    assert foreign["status"] == "settings_changed" and foreign["snapshot"] is None
    client, entry, _ = client_for(case)
    entry.now = lambda: AT + timedelta(seconds=60)
    body = command(case)
    queued = client.post(
        BASE + "/research", json=body, headers={"Idempotency-Key": body["request_id"]}
    )
    assert queued.status_code == 200, queued.text
    newer = current(case, now=AT + timedelta(seconds=70))
    assert newer["status"] == "researching" and newer["snapshot"] is None
    assert newer["run_id"] == queued.json()["run_id"] != available["run_id"]


def test_missing_published_comparison_cannot_fall_back_to_snapshot_summary(binding_case):
    import pytest

    case = binding_case
    persisted_publication(case, omit_comparison=True)
    with pytest.raises(ValueError, match="perpetual_advice_original_unavailable"):
        current(case, now=AT + timedelta(seconds=40))

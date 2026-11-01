"""Bounded watch selection and condition-source fences; temporary SQL only.

Receipt, native-policy and financial-original authentication are explicit trusted
boundaries here. These cases do not manufacture a reviewed publication or candle.
"""

import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
    DecisionSettingsPartitionV1,
    DecisionSourceRefV1,
)
from packages.kernel.decision_hub_kernel.application import (
    perpetual_condition_monitor as monitor,
)
from packages.kernel.decision_hub_kernel.application import (
    perpetual_plan_watch_continuous as continuous,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    Base,
    Database,
    OutboxRecord,
    ProductCommandRecord,
    as_utc,
)
from packages.kernel.decision_hub_kernel.persistence.risk_research_records import (
    RiskResearchRequestRecord,
    RiskResearchTriggerRecord,
)
from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash
from packages.kernel.decision_hub_kernel.ports.perpetual_plan_watch import (
    PerpetualPlanReviewTriggerV2,
)
from packages.kernel.decision_hub_kernel.ports.perpetual_preparation import canonical
from packages.kernel.decision_hub_kernel.ports.perpetual_research_schedule import next_hour

AT = datetime(2026, 10, 21, 0, 5, tzinfo=UTC)
OWNER = "condition-boundary-owner"


@pytest.fixture
def command_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "unused-default-data"))
    db = Database(f"sqlite+pysqlite:///{(tmp_path / 'boundaries.sqlite3').as_posix()}")
    Base.metadata.create_all(
        db.engine,
        tables=[
            ProductCommandRecord.__table__,
            OutboxRecord.__table__,
            RiskResearchRequestRecord.__table__,
            RiskResearchTriggerRecord.__table__,
        ],
    )
    try:
        yield db
    finally:
        db.engine.dispose()


def _reference(name):
    return {"ref": name, "hash": canonical_hash(["condition-boundary", name])}


def test_rotating_due_batch_prepares_every_selected_watch(command_db, monkeypatch):
    """Old newest-commands preparation skips a different set than the SQL cursor."""
    receipts, progresses = {}, {}
    with command_db.session() as session:
        for index in range(40):
            watch_id = f"watch_{index:02}"
            command_id = f"current_command_{index:02}"
            receipts[watch_id] = SimpleNamespace(
                owner_id=OWNER,
                watch_id=watch_id,
                revision=SimpleNamespace(ref=command_id),
                state="active",
            )
            progresses[watch_id] = {
                "status": "following",
                "plan_id": f"plan_{index:02}",
                "plan_revision_ref": _reference(f"plan_revision_{index:02}"),
                "origin_run_id": f"source_run_{index:02}",
            }
            for version, key in ((1, f"old_command_{index:02}"), (2, command_id)):
                body = {"request_id": key, "state_version": version, "state": "active"}
                session.add(
                    ProductCommandRecord(
                        request_id=key,
                        owner_id=OWNER,
                        target_id=watch_id,
                        kind=continuous.KIND,
                        payload_hash=canonical_hash(body),
                        result_json=canonical(body),
                        created_at=AT + timedelta(seconds=version * 100 + index - 400),
                    )
                )

    # Validate the real SQL selector/preparation handoff without replaying native
    # receipts, financial publications or archive evidence in this boundary test.
    monkeypatch.setattr(continuous, "_latest", lambda _session, watch: receipts[watch])
    monkeypatch.setattr(continuous, "_latest_progress", lambda _session, watch: progresses[watch])
    consumed = []

    def prepare_inputs(receipt, _progress):
        consumed.append(receipt.revision.ref)
        return {"prepared_for": receipt.revision.ref}

    policy_boundary = SimpleNamespace(
        read_policy=lambda *_args: (object(), {"reason_code": None}, ()),
        prepare_inputs=prepare_inputs,
    )
    parent = SimpleNamespace(
        database=command_db,
        now=lambda: AT,
        channel="local",
        condition_monitor=policy_boundary,
        _continuous_scan_after=None,
    )
    service = continuous.ContinuousPlanWatch(parent)
    seen = set()
    expected_batches = [list(range(32)), list(range(32, 40)) + list(range(24))]
    for expected_indices in expected_batches:
        with command_db.session() as session:
            due = service._due_commands(session)
        due_ids = [row.request_id for row in due]
        assert due_ids == [f"current_command_{index:02}" for index in expected_indices]
        consumed.clear()
        prepared = service._prepare_conditions(due_ids)
        assert consumed == due_ids
        assert list(prepared) == [
            (f"current_command_{index:02}", f"plan_revision_{index:02}")
            for index in expected_indices
        ]
        seen.update(due_ids)
        # Discovery advances this same parent cursor only after its transaction.
        parent._continuous_scan_after = due[-1].target_id
    assert seen == {f"current_command_{index:02}" for index in range(40)}


def _source_case(db, monkeypatch, *, fault=None):
    """Keep all hashes/IDs valid so rejection identifies the new source fence."""
    watch_ref = _reference("watch-consent-original")
    revision_ref = _reference("source-plan-revision")
    partition = DecisionSettingsPartitionV1.model_validate(
        {
            "account_kind": "paper",
            "account_id": "condition-boundary-paper",
            "instrument_id": "okx:BTC-USDT-SWAP",
            "profile_ref": "decision-profile:okx-usdt-linear-paper-net-isolated-1x.v1",
        }
    )
    until = AT + timedelta(hours=4)
    condition = {
        "schema_version": "perpetual-evidence-condition.v1",
        "causal_link_id": "support-mechanism",
        "candidate_ids": ["flat-long"],
        "purpose": "confirmation" if fault == "purpose" else "invalidation",
        "expression": {
            "op": "compare",
            "feature": "close",
            "scope_id": "crypto:perp:okx:BTC-USDT-SWAP:trade:1m",
            "unit": "USDT/BTC",
            "cmp": "lte",
            "threshold": "67000",
            "max_age_seconds": 120,
        },
        "min_fresh_samples": 2,
        "evaluation_seconds": 60,
        "valid_until": until.isoformat().replace("+00:00", "Z"),
    }
    assumption = {
        "schema_version": "decision-model-assumption.v1",
        "assumption_id": "support-lost",
        "role": "entry_condition",
        "statement": canonical(condition),
        "evidence_refs": [],
    }
    assumption_hash = canonical_hash(assumption)
    policy = {
        "schema_version": "perpetual-condition-monitoring-policy.v1",
        "owner_id": OWNER,
        "watch_id": "source-watch",
        "watch_ref": watch_ref,
        "plan_id": "source-plan",
        "plan_revision_ref": revision_ref,
        "source_run_id": "reviewed-source-run",
        "assumptions": [assumption],
    }
    policy_key = "perpconditionpolicy_" + canonical_hash([watch_ref, revision_ref])
    policy_ref = {"ref": policy_key, "hash": canonical_hash(policy)}
    event = {
        "schema_version": "perpetual-condition-transition.v1",
        "owner_id": OWNER,
        "plan_id": "source-plan",
        "plan_revision_ref": revision_ref,
        "watch_id": "source-watch",
        "watch_ref": watch_ref,
        "assumption_ref": {
            "ref": "decision_model_assumption_" + assumption_hash,
            "hash": assumption_hash,
        },
        "purpose": "invalidation",
        "status": "confirmed" if fault in {"purpose", "status"} else "invalidated",
        "first_confirmed_bar_ref": _reference("first-confirmed-close"),
        "observed_at": AT.isoformat().replace("+00:00", "Z"),
        "policy_ref": policy_ref,
    }
    event_key = "perpconditionevent_" + canonical_hash(
        [
            event[key]
            for key in (
                "watch_ref",
                "plan_revision_ref",
                "assumption_ref",
                "purpose",
                "first_confirmed_bar_ref",
            )
        ]
    )
    event_ref = {"ref": event_key, "hash": canonical_hash(event)}
    source = PerpetualPlanReviewTriggerV2(
        schema_version="perpetual-plan-review-trigger.v2",
        owner_id=OWNER,
        watch_id="source-watch",
        watch_ref=watch_ref,
        partition=partition,
        horizon="4h",
        motive="condition_invalidated",
        expired_at=None,
        condition_event_ref=event_ref,
        observed_at=AT,
        scheduled_at=AT,
        deadline_at=min(next_hour(AT), until),
        recorded_at=AT,
        origin_run_id="reviewed-source-run",
        origin_artifact_id="reviewed-source-report",
        plan_revision_ref=revision_ref,
        snapshot_ref=_reference("source-snapshot"),
        comparison_ref=_reference("source-comparison"),
        frozen_forecast_ref=_reference("source-forecast"),
        publication_ref=_reference("source-publication"),
    )
    source_body = source.model_dump(mode="json")
    trigger_key = "perpetual_plan_review:" + canonical_hash(
        [source_body["watch_ref"], source_body["plan_revision_ref"], event_ref]
    )
    scope = "crypto:perp:okx:BTC-USDT-SWAP:trade:1m"
    request_id = "riskresearch_" + canonical_hash([scope, trigger_key])[:40]
    event.update(request_id=request_id, outbox_ids=[19])
    request = RiskResearchRequestRecord(
        request_id=request_id,
        protocol_version="risk-research-request.perpetual-plan-review.v1",
        scope_id=scope,
        instrument="BTC-USDT-SWAP",
        status="pending_admission",
        created_at=AT,
        updated_at=AT,
        available_at=AT,
        attempts=0,
        lease_generation=0,
    )
    trigger = RiskResearchTriggerRecord(
        trigger_key=trigger_key,
        request_id=request_id,
        kind="perpetual_plan_review",
        scope_id=scope,
        source_ref="source-watch",
        payload_json=canonical(source_body),
        payload_hash=canonical_hash(source_body),
        recorded_at=AT,
    )
    with db.session() as session:
        session.add_all(
            [
                ProductCommandRecord(
                    request_id=policy_key,
                    owner_id=OWNER,
                    target_id="source-plan",
                    kind=monitor.POLICY_KIND,
                    payload_hash=canonical_hash(policy),
                    result_json=canonical(policy),
                    created_at=AT - timedelta(minutes=1),
                ),
                ProductCommandRecord(
                    request_id=event_key,
                    owner_id=OWNER,
                    target_id="source-plan",
                    kind=monitor.TRANSITION_KIND,
                    payload_hash=canonical_hash(event),
                    result_json=canonical(event),
                    created_at=AT,
                ),
                request,
                trigger,
            ]
        )
    receipt = SimpleNamespace(
        schema_version="perpetual-plan-watch-receipt.v2"
        if fault == "legacy_consent"
        else "perpetual-plan-watch-receipt.v3",
        owner_id=OWNER,
        watch_id="source-watch",
        revision=DecisionSourceRefV1.model_validate(watch_ref),
        accepted_at=(
            AT + timedelta(seconds=1) if fault == "preconsent" else AT - timedelta(seconds=30)
        ),
        state="active",
        command=SimpleNamespace(
            schema_version="perpetual-plan-watch-command.v2"
            if fault == "legacy_consent"
            else "perpetual-plan-watch-command.v3",
            condition_research=True,
            mode="remind_and_research",
            partition=partition,
            horizon="4h",
        ),
    )
    monkeypatch.setattr(continuous, "_read", lambda *_args: receipt)
    monkeypatch.setattr(continuous, "_check_origin", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        monitor,
        "ActionRepository",
        lambda _session: SimpleNamespace(
            get_revision=lambda _ref: SimpleNamespace(valid_until=until)
        ),
    )
    stored = SimpleNamespace(result_json=canonical({"request_id": "watch-consent-command"}))
    return request_id, trigger_key, source, stored


def _read_source(db, identifiers):
    request_id, trigger_key, source, stored = identifiers
    with db.session() as session:
        return monitor.read_condition_review_source_in_session(
            session,
            session.get(RiskResearchRequestRecord, request_id),
            session.get(RiskResearchTriggerRecord, trigger_key),
            source,
            stored,
            at=AT + timedelta(seconds=2),
            require_active=False,
        )


def test_condition_source_accepts_consistent_event_after_consent(command_db, monkeypatch):
    identifiers = _source_case(command_db, monkeypatch)
    proof = _read_source(command_db, identifiers)
    assert proof["request_id"] == identifiers[0]
    assert proof["source"]["motive"] == "condition_invalidated"


@pytest.mark.parametrize("fault", ["purpose", "status", "preconsent", "legacy_consent"])
def test_condition_source_rejects_crossed_declaration_or_preconsent_event(
    command_db, monkeypatch, fault
):
    identifiers = _source_case(command_db, monkeypatch, fault=fault)
    with pytest.raises(ValueError, match="^perpetual_condition_trigger_invalid$"):
        _read_source(command_db, identifiers)


def test_simultaneous_conditions_share_one_invalidation_successor_and_replay(
    command_db, monkeypatch
):
    """Confirmation is authored first; invalidation still chooses the one motive."""
    from packages.kernel.decision_hub_kernel.decision.perpetual_conditions import (
        decode_perpetual_condition,
    )
    from packages.kernel.decision_hub_kernel.ports.perpetual_close import PerpetualClose
    from tests.decision_product.test_perpetual_action_conditions import condition_case

    _fixture, condition, assumption, prepared_candidate = condition_case()
    synthesis = deepcopy(prepared_candidate["candidate"])
    published_at = AT - timedelta(minutes=2)
    until = AT + timedelta(hours=4)
    synthesis["as_of"] = published_at.isoformat()
    synthesis["candidate_set"]["valid_until"] = until.isoformat()
    assumptions, declarations = [], []
    for purpose in ("confirmation", "invalidation"):
        body = {**deepcopy(condition), "purpose": purpose, "valid_until": until.isoformat()}
        authored = {
            **deepcopy(assumption),
            "assumption_id": f"simultaneous_{purpose}",
            "statement": canonical(body),
        }
        declaration = decode_perpetual_condition(authored, synthesis=synthesis)
        assert declaration.policy is not None, declaration.reason_code
        assumptions.append(authored)
        declarations.append(declaration)

    watch_ref, revision_ref = _reference("simultaneous-watch"), _reference("simultaneous-plan")
    policy = {
        "schema_version": "perpetual-condition-monitoring-policy.v1",
        "owner_id": OWNER,
        "watch_id": "simultaneous-watch-id",
        "watch_ref": watch_ref,
        "plan_id": "simultaneous-plan-id",
        "plan_revision_ref": revision_ref,
        "source_run_id": "simultaneous-source-run",
        "published_at": published_at.isoformat().replace("+00:00", "Z"),
        "assumptions": assumptions,
        "reason_code": None,
    }
    policy_key = "perpconditionpolicy_" + canonical_hash([watch_ref, revision_ref])
    policy_row = ProductCommandRecord(
        request_id=policy_key,
        owner_id=OWNER,
        target_id=policy["plan_id"],
        kind=monitor.POLICY_KIND,
        payload_hash=canonical_hash(policy),
        result_json=canonical(policy),
        created_at=published_at,
    )
    with command_db.session() as session:
        session.add(policy_row)
    partition = DecisionSettingsPartitionV1.model_validate(
        {
            "account_kind": "paper",
            "account_id": "condition-boundary-paper",
            "instrument_id": "okx:BTC-USDT-SWAP",
            "profile_ref": "decision-profile:okx-usdt-linear-paper-net-isolated-1x.v1",
        }
    )
    receipt = SimpleNamespace(
        schema_version="perpetual-plan-watch-receipt.v3",
        owner_id=OWNER,
        watch_id=policy["watch_id"],
        revision=DecisionSourceRefV1.model_validate(watch_ref),
        accepted_at=published_at,
        state="active",
        command=SimpleNamespace(
            schema_version="perpetual-plan-watch-command.v3",
            condition_research=True,
            mode="remind_and_research",
            input_policy="saved_preferences_current_paper_ledger.v1",
            partition=partition,
            horizon="4h",
        ),
    )
    progress = {
        "plan_id": policy["plan_id"],
        "origin_run_id": policy["source_run_id"],
        "origin_artifact_id": "simultaneous-source-report",
        "plan_revision_ref": revision_ref,
        "snapshot_ref": _reference("simultaneous-snapshot"),
        "comparison_ref": _reference("simultaneous-comparison"),
        "frozen_forecast_ref": _reference("simultaneous-forecast"),
        "publication_ref": _reference("simultaneous-publication"),
    }
    closes = tuple(
        PerpetualClose(
            open_at=closed_at - timedelta(minutes=1),
            close_at=closed_at,
            price="67000",
            capture_id="trusted-synthetic-closed-page",
            capture_hash="c" * 64,
            capture_metadata_hash="d" * 64,
            body_ref="raw://sha256/" + "c" * 64,
            row_index=index,
            received_at=closed_at + timedelta(milliseconds=10),
            recorded_at=closed_at + timedelta(milliseconds=20),
        )
        for index, closed_at in enumerate((AT - timedelta(minutes=1), AT))
    )
    # Only the prepared/native-policy boundary is substituted. Evaluation,
    # transitions, request/trigger creation, outbox enqueue and replay use SQL.
    evidence = SimpleNamespace(
        value={
            "policy_ref": {"ref": policy_key, "hash": policy_row.payload_hash},
            "closes": closes,
            "reason_code": None,
        },
        _require_issued=lambda _session: None,
        verify=lambda _session: None,
    )
    clock = [AT + timedelta(seconds=1)]
    service = monitor.PerpetualConditionMonitor(command_db, None, now=lambda: clock[0])
    monkeypatch.setattr(
        service, "read_policy", lambda *_args: (policy_row, policy, tuple(declarations))
    )

    def durable_rows(session):
        return {
            "commands": [
                (row.request_id, row.payload_hash, row.result_json)
                for row in session.scalars(
                    select(ProductCommandRecord).order_by(ProductCommandRecord.request_id)
                )
            ],
            "requests": [
                (row.request_id, row.status, as_utc(row.updated_at))
                for row in session.scalars(
                    select(RiskResearchRequestRecord).order_by(RiskResearchRequestRecord.request_id)
                )
            ],
            "triggers": [
                (row.trigger_key, row.request_id, row.payload_hash, row.payload_json)
                for row in session.scalars(
                    select(RiskResearchTriggerRecord).order_by(
                        RiskResearchTriggerRecord.trigger_key
                    )
                )
            ],
            "notices": [
                (row.id, row.payload_hash, row.payload_json)
                for row in session.scalars(select(OutboxRecord).order_by(OutboxRecord.id))
            ],
        }

    with command_db.session() as session:
        observation = service.observe(session, receipt, progress, evidence)
        events = list(
            session.scalars(
                select(ProductCommandRecord).where(
                    ProductCommandRecord.kind == monitor.TRANSITION_KIND
                )
            )
        )
        notices = list(session.scalars(select(OutboxRecord)))
        requests = list(session.scalars(select(RiskResearchRequestRecord)))
        triggers = list(session.scalars(select(RiskResearchTriggerRecord)))
        assert len(events) == 2
        assert len(notices) == 2
        assert len(requests) == 1
        assert len(triggers) == 1
        request_id = requests[0].request_id
        event_bodies = [json.loads(row.result_json) for row in events]
        assert {body["status"] for body in event_bodies} == {"confirmed", "invalidated"}
        assert {body["request_id"] for body in event_bodies} == {request_id}
        assert {state["request_id"] for state in observation["conditions"]} == {request_id}
        assert observation["new_exposure_blocked"] is True
        assert {outbox_id for body in event_bodies for outbox_id in body["outbox_ids"]} == {
            row.id for row in notices
        }
        invalidation = next(
            row for row in events if json.loads(row.result_json)["purpose"] == "invalidation"
        )
        trigger_body = json.loads(triggers[0].payload_json)
        assert triggers[0].request_id == request_id
        assert trigger_body["motive"] == "condition_invalidated"
        assert trigger_body["condition_event_ref"]["ref"] == invalidation.request_id
        before = durable_rows(session)

    clock[0] += timedelta(seconds=1)
    with command_db.session() as session:
        replay = service.observe(session, receipt, progress, evidence)
        assert replay == observation
        assert durable_rows(session) == before


def _pending_context(db, source, stored):
    """The old receipt reader is the trusted-consent stub installed by _source_case."""
    with db.session() as session:
        session.add(
            ProductCommandRecord(
                request_id=source.watch_ref.ref,
                owner_id=source.owner_id,
                target_id=source.watch_id,
                kind=continuous.KIND,
                payload_hash=canonical_hash(json.loads(stored.result_json)),
                result_json=stored.result_json,
                created_at=AT - timedelta(seconds=30),
            )
        )
        receipt = continuous._read(
            session, source.owner_id, json.loads(stored.result_json)["request_id"]
        )
    progress = {
        "plan_id": "source-plan",
        "plan_revision_ref": source.plan_revision_ref.model_dump(mode="json"),
        "origin_run_id": source.origin_run_id,
    }
    return receipt, progress


def test_pending_successor_reuses_queued_run_from_prior_consent(command_db, monkeypatch):
    from packages.kernel.decision_hub_kernel.persistence.db import RunRecord

    request_id, trigger_key, source, stored = _source_case(command_db, monkeypatch)
    old_receipt, progress = _pending_context(command_db, source, stored)
    current_receipt = SimpleNamespace(
        **{
            **vars(old_receipt),
            "revision": DecisionSourceRefV1.model_validate(_reference("resumed-watch-consent")),
            "accepted_at": AT + timedelta(seconds=1),
        }
    )
    with command_db.session() as session:
        request = session.get(RiskResearchRequestRecord, request_id)
        request.status, request.run_id = "queued", "prior-consent-running-run"
    with command_db.session() as session:
        trigger = session.get(RiskResearchTriggerRecord, trigger_key)
        frozen_trigger = (trigger.trigger_key, trigger.payload_hash, trigger.payload_json)
        real_get = session.get

        def read_with_trusted_run(entity, key, *args, **kwargs):
            # Only the Run projection is substituted; request/trigger/consent
            # lookups remain real SQL and the old receipt uses its explicit stub.
            if entity is RunRecord and key == "prior-consent-running-run":
                return SimpleNamespace(run_id=key, status="running")
            return real_get(entity, key, *args, **kwargs)

        monkeypatch.setattr(session, "get", read_with_trusted_run)
        reused = monitor.PerpetualConditionMonitor._pending_successor(
            session, current_receipt, progress, AT + timedelta(seconds=2)
        )
        assert reused == request_id
        request = session.get(RiskResearchRequestRecord, request_id)
        assert (request.status, request.run_id) == ("queued", "prior-consent-running-run")
        assert (trigger.trigger_key, trigger.payload_hash, trigger.payload_json) == frozen_trigger


@pytest.mark.parametrize("status", ["pending_admission", "claiming"])
def test_pending_successor_expires_unadmitted_source_without_mutating_trigger(
    command_db, monkeypatch, status
):
    request_id, trigger_key, source, stored = _source_case(command_db, monkeypatch)
    receipt, progress = _pending_context(command_db, source, stored)
    with command_db.session() as session:
        request = session.get(RiskResearchRequestRecord, request_id)
        request.status = status
        if status == "claiming":
            request.lease_owner = "trusted-fixture-claim"
            request.lease_generation = 1
            request.lease_expires_at = AT + timedelta(hours=2)
    with command_db.session() as session:
        trigger = session.get(RiskResearchTriggerRecord, trigger_key)
        original_trigger = (trigger.trigger_key, trigger.payload_hash, trigger.payload_json)
        assert (
            monitor.PerpetualConditionMonitor._pending_successor(
                session, receipt, progress, AT + timedelta(seconds=2)
            )
            == request_id
        )
        request = session.get(RiskResearchRequestRecord, request_id)
        assert request.status == status
        assert request.error_code is None
        assert (
            monitor.PerpetualConditionMonitor._pending_successor(
                session, receipt, progress, AT + timedelta(hours=1)
            )
            is None
        )
        assert request.status == "failed"
        assert request.error_code == "perpetual_watch_slot_expired"
        assert (trigger.trigger_key, trigger.payload_hash, trigger.payload_json) == original_trigger
    with command_db.session() as session:
        request = session.get(RiskResearchRequestRecord, request_id)
        trigger = session.get(RiskResearchTriggerRecord, trigger_key)
        assert request.status == "failed"
        assert request.error_code == "perpetual_watch_slot_expired"
        assert (trigger.trigger_key, trigger.payload_hash, trigger.payload_json) == original_trigger

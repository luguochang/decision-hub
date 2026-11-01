"""New perpetual monitor risks only; temporary raw captures and SQL, no network."""

import json
from dataclasses import asdict
from datetime import timedelta

import pytest

from tests.decision_product.test_okx_perpetual_original import INST, NOW
from tests.decision_product.test_okx_perpetual_original import (
    persisted_original as persisted_original,
)
from tests.decision_product.test_perpetual_research_binding import binding_case as binding_case


def closed_page(fixture, *, at=NOW, price="67000", fault=None, identity="condition_page"):
    from packages.kernel.decision_hub_kernel.persistence.db import RawCaptureRecord
    from packages.kernel.decision_hub_kernel.ports.action_quote import CaptureOriginal
    from packages.kernel.decision_hub_kernel.ports.perpetual_close import (
        VERSION,
        millis,
        public_url,
    )

    db, archive, _source, _metadata = fixture
    stamp = millis(at)
    data = [
        [str(stamp - 60000 * i), price, price, price, price, "7", "0.07", "7", "1"] for i in (1, 2)
    ]
    if fault == "unclosed":
        data[0][8] = "0"
    raw = json.dumps({"code": "0", "data": data}, separators=(",", ":")).encode()
    digest, ref = archive.put(raw)
    url = public_url(INST, stamp)
    if fault == "spot":
        url = url.replace(INST, "BTC-USDT")
    capture = CaptureOriginal(
        identity,
        f"{VERSION}:{INST}:{'1' * 32}:base:0",
        url,
        200,
        ref,
        digest,
        len(raw),
        VERSION,
        None,
        at + timedelta(milliseconds=10),
        at + timedelta(milliseconds=20),
        at + timedelta(milliseconds=30),
    )
    with db.session() as session:
        session.add(RawCaptureRecord(**asdict(capture), media_type="application/json"))
    return capture


def test_perpetual_close_evidence_is_original_bound_and_does_not_alias_spot(persisted_original):
    from packages.kernel.decision_hub_kernel.application.perpetual_condition_inputs import (
        read_perpetual_condition_inputs_in_session,
    )

    capture = closed_page(persisted_original)
    db, _archive, source, metadata = persisted_original
    with db.session() as session:
        result = read_perpetual_condition_inputs_in_session(
            session,
            instrument_ref=source.metadata_contract_reference(metadata.capture_id),
            scope_id="crypto:perp:okx:BTC-USDT-SWAP:trade:1m",
            as_of=NOW + timedelta(seconds=1),
            source=source,
        )
    assert [bar.price for bar in result.closes] == ["67000", "67000"]
    assert result.closes[-1].close_at == NOW
    assert result.closes[-1].capture_id == capture.capture_id
    assert result.closes[-1].capture_hash == capture.body_hash


@pytest.mark.parametrize("fault", ["spot", "unclosed"])
def test_invalid_perpetual_original_cannot_be_confirmation(persisted_original, fault):
    from packages.kernel.decision_hub_kernel.application.perpetual_condition_inputs import (
        read_perpetual_condition_inputs_in_session,
    )

    closed_page(persisted_original, fault=fault)
    db, _archive, source, metadata = persisted_original
    with db.session() as session:
        with pytest.raises(ValueError):
            read_perpetual_condition_inputs_in_session(
                session,
                instrument_ref=source.metadata_contract_reference(metadata.capture_id),
                scope_id="crypto:perp:okx:BTC-USDT-SWAP:trade:1m",
                as_of=NOW + timedelta(seconds=1),
                source=source,
            )


def test_condition_streak_counts_distinct_closed_bars_and_preserves_unknown(persisted_original):
    from packages.kernel.decision_hub_kernel.application.perpetual_condition_inputs import (
        read_perpetual_condition_inputs_in_session,
    )
    from packages.kernel.decision_hub_kernel.decision.perpetual_conditions import (
        decode_perpetual_condition,
        evaluate_perpetual_condition,
    )
    from packages.kernel.decision_hub_kernel.ports.perpetual_preparation import canonical
    from tests.decision_product.test_perpetual_action_conditions import condition_case

    closed_page(persisted_original)
    db, _archive, source, metadata = persisted_original
    _fixture, body, assumption, prepared = condition_case()
    prepared["candidate"]["as_of"] = (NOW - timedelta(minutes=2)).isoformat()
    prepared["candidate"]["candidate_set"]["valid_until"] = (NOW + timedelta(hours=4)).isoformat()
    body["valid_until"] = (NOW + timedelta(hours=4)).isoformat()
    assumption["statement"] = canonical(body)
    declaration = decode_perpetual_condition(assumption, synthesis=prepared["candidate"])
    with db.session() as session:
        inputs = read_perpetual_condition_inputs_in_session(
            session,
            instrument_ref=source.metadata_contract_reference(metadata.capture_id),
            scope_id="crypto:perp:okx:BTC-USDT-SWAP:trade:1m",
            as_of=NOW + timedelta(seconds=1),
            source=source,
        )
    first = evaluate_perpetual_condition(
        declaration,
        inputs.closes[:1],
        previous=None,
        as_of=NOW + timedelta(seconds=1),
        not_before=NOW - timedelta(minutes=2),
    )
    assert first["streak"] == 1 and first["status"] == "waiting"
    repeated = evaluate_perpetual_condition(
        declaration,
        inputs.closes[:1],
        previous=first,
        as_of=NOW + timedelta(seconds=2),
        not_before=NOW - timedelta(minutes=2),
    )
    assert repeated["streak"] == 1 and repeated["status"] == "waiting"
    second = evaluate_perpetual_condition(
        declaration,
        inputs.closes,
        previous=first,
        as_of=NOW + timedelta(seconds=2),
        not_before=NOW - timedelta(minutes=2),
    )
    assert second["streak"] == 2 and second["status"] == "invalidated"
    missing = evaluate_perpetual_condition(
        declaration,
        (),
        previous=second,
        as_of=NOW + timedelta(seconds=3),
        not_before=NOW - timedelta(minutes=2),
    )
    assert missing["status"] == "invalidated"
    stale = evaluate_perpetual_condition(
        declaration,
        inputs.closes,
        previous=None,
        as_of=NOW + timedelta(minutes=3),
        not_before=NOW - timedelta(minutes=2),
    )
    assert stale["truth"] == "unknown" and stale["status"] == "unknown"


class CurrentBoundaryCloses:
    """A complete synthetic window ending at the actual immediate minute boundary."""

    def __init__(self, case, clock):
        self.case, self.clock = case, clock

    async def __call__(self, *, inst_id):
        from urllib.parse import parse_qs, urlsplit

        from packages.kernel.decision_hub_kernel.application.perpetual_close_source import (
            CollectedPerpetualCloseWindow,
        )
        from packages.kernel.decision_hub_kernel.persistence.db import RawCaptureRecord, as_utc
        from packages.kernel.decision_hub_kernel.ports.perpetual_close import minute_end, public_url
        from tests.decision_product.test_perpetual_research_binding import AT

        end = minute_end(self.clock[0])
        delta = end - minute_end(AT)
        ids = []
        with self.case["db"].session() as session:
            for original_id in self.case["ids"]:
                old = session.get(RawCaptureRecord, original_id)
                value = json.loads(self.case["archive"].read(old.body_ref))
                for row in value["data"]:
                    row[0] = str(int(row[0]) + int(delta.total_seconds() * 1000))
                digest, ref = self.case["archive"].put(
                    json.dumps(value, separators=(",", ":")).encode()
                )
                fields = {
                    column.key: getattr(old, column.key)
                    for column in RawCaptureRecord.__table__.columns
                }
                after = int(parse_qs(urlsplit(old.url).query)["after"][0])
                fields.update(
                    capture_id="immediate_" + original_id,
                    body_ref=ref,
                    body_hash=digest,
                    byte_count=len(self.case["archive"].read(ref)),
                    url=public_url(inst_id, after + int(delta.total_seconds() * 1000)),
                    started_at=as_utc(old.started_at) + delta,
                    completed_at=as_utc(old.completed_at) + delta,
                    recorded_at=as_utc(old.recorded_at) + delta,
                )
                session.add(RawCaptureRecord(**fields))
                ids.append(fields["capture_id"])
        self.clock[0] = max(self.clock[0], end + timedelta(seconds=6))
        return CollectedPerpetualCloseWindow(tuple(ids), end, self.clock[0])


@pytest.mark.parametrize(
    "mode,consent",
    [
        pytest.param("remind_only", "v3", id="v3_remind_only"),
        pytest.param("remind_and_research", "v3", id="v3_explicit_conditions"),
        pytest.param("remind_and_research", "v2", id="legacy_expiry_only"),
    ],
)
@pytest.mark.asyncio
async def test_real_watch_condition_transition_notice_and_immediate_successor(
    binding_case, tmp_path, monkeypatch, mode, consent
):
    """Native policy reader is the already-verified Task1 boundary; all later SQL/raw steps real."""
    from sqlalchemy import select

    from packages.kernel.decision_hub_kernel.application import (
        perpetual_condition_monitor as monitor,
    )
    from packages.kernel.decision_hub_kernel.application.decision_settings import (
        DecisionSettingsService,
    )
    from packages.kernel.decision_hub_kernel.application.perpetual_advice_query import _original
    from packages.kernel.decision_hub_kernel.application.perpetual_plan_followup import (
        PerpetualPlanFollowupService,
        read_plan_review_source_in_session,
    )
    from packages.kernel.decision_hub_kernel.application.perpetual_research_reader import (
        PreparedPerpetualConditions,
    )
    from packages.kernel.decision_hub_kernel.decision.perpetual_conditions import (
        decode_perpetual_condition,
    )
    from packages.kernel.decision_hub_kernel.persistence.db import (
        OutboxRecord,
        ProductCommandRecord,
    )
    from packages.kernel.decision_hub_kernel.persistence.risk_research_records import (
        RiskResearchRequestRecord,
    )
    from packages.kernel.decision_hub_kernel.ports.perpetual_preparation import (
        canonical,
        derived_ref,
    )
    from packages.kernel.decision_hub_kernel.ports.research_observation import ObservedExecutionRef
    from packages.runtime_adapters.perpetual_research_entry import (
        configured_perpetual_research_services,
    )
    from tests.decision_product.test_perpetual_research_binding import AT, OWNER

    case, clock = binding_case, [AT]
    from packages.kernel.decision_hub_kernel.persistence.action_repository import ActionRepository
    from tests.decision_product.test_perpetual_manual_execution import paper_publication

    _paper, _protocol, at, _identity, _view = paper_publication(case)
    clock[0] = at
    with case["db"].session() as session:
        revision = ActionRepository(session).get_revision(_view["revision"]["revision_id"])

    def trusted_policy(session, **kwargs):
        synthesis = _original(
            session,
            case["run_id"],
            revision.bindings.native_synthesis.model_dump(mode="json"),
            "research-decision-synthesis.v2",
        )
        policy = {
            "schema_version": "perpetual-evidence-condition.v1",
            "causal_link_id": synthesis["analysis"]["main_chain"][0]["link_id"],
            "candidate_ids": [synthesis["candidate_set"]["candidates"][0]["candidate_id"]],
            "purpose": "invalidation",
            "expression": {
                "op": "compare",
                "feature": "close",
                "scope_id": revision.context.scope_id,
                "unit": "USDT/BTC",
                "cmp": "lte",
                "threshold": "67000",
                "max_age_seconds": 120,
            },
            "min_fresh_samples": 2,
            "evaluation_seconds": 60,
            "valid_until": synthesis["candidate_set"]["valid_until"],
        }
        assumption = {
            "schema_version": "decision-model-assumption.v1",
            "assumption_id": "support_lost",
            "role": "entry_condition",
            "statement": canonical(policy),
            "evidence_refs": [],
        }
        prepare_ref = ObservedExecutionRef(
            output_id="fixture_already_authenticated_prepare",
            role="provider_tool_emission",
            schema_version="research-provider-tool-emission.v1",
            payload_hash="a" * 64,
            recorded_at=AT,
        )
        return PreparedPerpetualConditions(
            case["run_id"],
            canonical(kwargs["candidate_target_ref"]),
            canonical(derived_ref("research_decision_synthesis", synthesis)),
            prepare_ref,
            (decode_perpetual_condition(assumption, synthesis=synthesis),),
            mapped_ref=ObservedExecutionRef.model_validate(kwargs["mapped_ref"]),
            provider_emission_ref=ObservedExecutionRef.model_validate(
                kwargs["provider_emission_ref"]
            ),
        )

    monkeypatch.setattr(monitor, "read_perpetual_conditions_in_session", trusted_policy)
    configured = configured_perpetual_research_services(
        case["db"], source=case["source"], now=lambda: clock[0]
    )
    service = PerpetualPlanFollowupService(case["db"], configured.entry, now=lambda: clock[0])
    with case["db"].session() as session:
        saved = DecisionSettingsService.current_in_session(
            session, OWNER, case["settings"].partition
        )
        frozen_before = _original(
            session,
            case["run_id"],
            revision.bindings.frozen_forecast.model_dump(mode="json"),
            "frozen-forecast.v1",
        )
    command = {
        "schema_version": "perpetual-plan-watch-command." + consent,
        "continuation": "follow_published_revisions",
        "input_policy": "saved_preferences_current_paper_ledger.v1",
        "request_id": "condition_enable",
        "partition": case["settings"].partition.model_dump(mode="json"),
        "horizon": "4h",
        "plan_id": revision.plan_id,
        "expected_revision_ref": {"ref": revision.revision_id, "hash": revision.payload_hash},
        "expected_state_version": 0,
        "expected_settings_revision": {
            "ref": saved.revision.revision_id,
            "hash": saved.revision.payload_hash,
        },
        "expected_settings_state_version": saved.state_version,
        "operation": "watch",
        "mode": mode,
    }
    if consent == "v3":
        command["condition_research"] = mode == "remind_and_research"
    receipt = service.save_continuous(OWNER, command)
    assert receipt.state == "active"
    original_receipt = receipt.model_dump_json()
    clock[0] = (clock[0] + timedelta(minutes=2)).replace(second=0, microsecond=0)
    closed_page(
        (case["db"], case["source"].archive, case["source"], None),
        at=clock[0],
        identity="monitored_live_fixture",
    )
    clock[0] += timedelta(seconds=1)
    service.discover()
    view = service.current_continuous(
        OWNER, command["partition"], "4h", command["expected_settings_revision"]
    )
    assert view["schema_version"] == "perpetual-plan-watch-view.v3"
    state = view["condition_monitoring"]
    assert state["reason_codes"] == [], state["reason_codes"]
    assert state["new_exposure_blocked"] is True, state
    assert (
        state["conditions"][0]["status"] == "invalidated" and state["conditions"][0]["streak"] == 2
    )
    from tests.decision_product.test_perpetual_advice_query import current

    advice = current(case, now=clock[0])
    assert advice["status"] == "invalidated"
    assert advice["revision"]["revision_id"] == revision.revision_id
    with case["db"].session() as session:
        events = session.scalars(
            select(ProductCommandRecord).where(ProductCommandRecord.kind == monitor.TRANSITION_KIND)
        ).all()
        notices = session.scalars(select(OutboxRecord)).all()
        requests = session.scalars(
            select(RiskResearchRequestRecord).where(
                RiskResearchRequestRecord.protocol_version
                == "risk-research-request.perpetual-plan-review.v1"
            )
        ).all()
        assert len(events) == 1 and len(notices) == 1
        research_allowed = mode == "remind_and_research" and consent == "v3"
        assert len(requests) == (1 if research_allowed else 0)
        if requests:
            proof = read_plan_review_source_in_session(
                session, requests[0].request_id, None, None, at=clock[0], claim_check=False
            )
            assert proof["source"]["motive"] == "condition_invalidated"
            assert proof["source"]["scheduled_at"] == clock[0].isoformat().replace("+00:00", "Z")
            assert proof["source"]["deadline_at"] == receipt.review_due_at.isoformat().replace("+00:00", "Z")
            assert proof["source"]["condition_event_ref"]["ref"] == events[0].request_id
        assert monitor.condition_new_exposure_blocked_in_session(
            session,
            owner_id=OWNER,
            plan_id=revision.plan_id,
            revision_ref=command["expected_revision_ref"],
        )
        assert (
            _original(
                session,
                case["run_id"],
                revision.bindings.frozen_forecast.model_dump(mode="json"),
                "frozen-forecast.v1",
            )
            == frozen_before
        )
    clock[0] += timedelta(seconds=1)
    service.discover()
    with case["db"].session() as session:
        assert len(session.scalars(select(OutboxRecord)).all()) == 1
        assert (
            len(
                session.scalars(
                    select(ProductCommandRecord).where(
                        ProductCommandRecord.kind == monitor.TRANSITION_KIND
                    )
                ).all()
            )
            == 1
        )
    assert (
        service.receipt_continuous(OWNER, command["request_id"]).model_dump_json()
        == original_receipt
    )
    if not research_allowed:
        from packages.kernel.decision_hub_kernel.application.outbox import NotificationDispatcher
        from packages.provider_adapters.notifications.local import LocalNotificationAdapter

        delivered = tmp_path / "condition-notifications.jsonl"
        dispatcher = NotificationDispatcher(
            case["db"], {"local": LocalNotificationAdapter(delivered)}, clock=lambda: clock[0]
        )
        assert await dispatcher.dispatch_once() == 1
        assert await dispatcher.dispatch_once() == 0
        notification = json.loads(delivered.read_text(encoding="utf-8").strip())
        assert notification["subject_kind"] == "action_revision"
        assert notification["subject_id"] == revision.revision_id
        assert "失效条件已触发" in notification["subject"]
    if research_allowed:
        from packages.kernel.decision_hub_kernel.application.perpetual_research_drivers import (
            PerpetualResearchAdmissionDrivers,
        )
        from packages.kernel.decision_hub_kernel.application.risk_research_admission import (
            RiskResearchAdmissionService,
        )
        from packages.kernel.decision_hub_kernel.persistence.db import RunRecord

        configured.entry.collect_window = CurrentBoundaryCloses(case, clock)
        driver = PerpetualResearchAdmissionDrivers(
            case["db"], configured.entry, now=lambda: clock[0]
        )
        engine = RiskResearchAdmissionService(
            case["db"], enabled=True, clock=lambda: clock[0], perpetual_driver=driver
        )
        admitted = await engine.process_due(limit=1)
        with case["db"].session() as session:
            request = session.get(RiskResearchRequestRecord, requests[0].request_id)
            assert admitted == 1, request.error_code
            run = session.get(RunRecord, request.run_id)
            assert run is not None and run.run_id != case["run_id"]
            assert request.status == "queued" and run.status == "admitted"

    # Invalidation blocks new exposure, but retains the separate expiry lifecycle.
    from packages.kernel.decision_hub_kernel.persistence.risk_research_records import (
        RiskResearchTriggerRecord,
    )

    with case["db"].session() as session:
        original_triggers = [
            (item.trigger_key, item.payload_hash, item.payload_json)
            for item in session.scalars(select(RiskResearchTriggerRecord)).all()
        ]
        original_notice_ids = set(session.scalars(select(OutboxRecord.id)).all())
    clock[0] = receipt.review_due_at + timedelta(seconds=1)
    service.discover()
    expired = service.current_continuous(
        OWNER, command["partition"], "4h", command["expected_settings_revision"]
    )
    assert expired["progress"]["status"] == "expired"
    assert expired["condition_monitoring"]["state"] == "invalidated"
    assert expired["condition_monitoring"]["new_exposure_blocked"] is True
    with case["db"].session() as session:
        expiry_requests = session.scalars(
            select(RiskResearchRequestRecord).where(
                RiskResearchRequestRecord.protocol_version
                == "risk-research-request.perpetual-plan-review.v1"
            )
        ).all()
        assert len(expiry_requests) == (1 if mode == "remind_and_research" else 0)
        expiry_notice_ids = set(session.scalars(select(OutboxRecord.id)).all())
        assert len(expiry_notice_ids - original_notice_ids) == 1
        assert (
            len(
                session.scalars(
                    select(ProductCommandRecord).where(
                        ProductCommandRecord.kind == monitor.TRANSITION_KIND
                    )
                ).all()
            )
            == 1
        )
        if research_allowed:
            # A queued nonterminal Run owns this cycle; expiry cannot duplicate it.
            assert expiry_requests[0].request_id == requests[0].request_id
            assert expiry_requests[0].run_id == run.run_id
            assert expiry_requests[0].status == "queued"
            assert [
                (item.trigger_key, item.payload_hash, item.payload_json)
                for item in session.scalars(select(RiskResearchTriggerRecord)).all()
            ] == original_triggers
        elif mode == "remind_and_research":
            expiry_request_id = expiry_requests[0].request_id
            trigger = session.scalar(
                select(RiskResearchTriggerRecord).where(
                    RiskResearchTriggerRecord.request_id == expiry_request_id
                )
            )
            expiry_source = json.loads(trigger.payload_json)
            assert expiry_source["schema_version"] == "perpetual-plan-review-trigger.v1"
            assert expiry_source["motive"] == "revision_expired"
            assert expiry_requests[0].status == "pending_admission"
    service.discover()
    with case["db"].session() as session:
        assert set(session.scalars(select(OutboxRecord.id)).all()) == expiry_notice_ids
    assert (
        service.receipt_continuous(OWNER, command["request_id"]).model_dump_json()
        == original_receipt
    )
    if mode == "remind_and_research" and not research_allowed:
        from datetime import datetime

        from packages.kernel.decision_hub_kernel.application.perpetual_research_drivers import (
            PerpetualResearchAdmissionDrivers,
        )
        from packages.kernel.decision_hub_kernel.application.risk_research_admission import (
            RiskResearchAdmissionService,
        )

        driver = PerpetualResearchAdmissionDrivers(
            case["db"], configured.entry, now=lambda: clock[0]
        )
        engine = RiskResearchAdmissionService(
            case["db"], enabled=True, clock=lambda: clock[0], perpetual_driver=driver
        )
        assert engine._claim() is None
        clock[0] = datetime.fromisoformat(expiry_source["scheduled_at"].replace("Z", "+00:00"))
        claim = engine._claim()
        assert claim == (expiry_request_id, "BTC-USDT-SWAP", 1)
        assert engine._claim() is None
        with case["db"].session() as session:
            proof = read_plan_review_source_in_session(
                session, expiry_request_id, claim[2], engine.owner_id, at=clock[0]
            )
            assert proof["source"] == expiry_source


@pytest.mark.asyncio
async def test_bounded_fresh_collection_uses_existing_capture_pipeline_without_network(
    persisted_original, monkeypatch
):
    import httpx

    from packages.kernel.decision_hub_kernel.application import perpetual_condition_inputs as inputs
    from packages.kernel.decision_hub_kernel.persistence.db import RawCaptureRecord
    from packages.kernel.decision_hub_kernel.ports.perpetual_close import VERSION, millis
    from packages.provider_adapters.research.raw_capture import CapturingAsyncClient

    db, archive, source, metadata = persisted_original

    def response(request):
        stamp = millis(NOW) - 60000
        return httpx.Response(
            200,
            request=request,
            json={
                "code": "0",
                "data": [[str(stamp), "67000", "67000", "67000", "67000", "7", "0.07", "7", "1"]],
            },
        )

    monkeypatch.setattr(
        inputs,
        "CapturingAsyncClient",
        lambda **kwargs: CapturingAsyncClient(**kwargs, transport=httpx.MockTransport(response)),
    )
    capture_id = await inputs.collect_perpetual_condition_page(source, inst_id=INST, as_of=NOW)
    with db.session() as session:
        capture = session.get(RawCaptureRecord, capture_id)
        assert capture.adapter_version == VERSION and capture.context_id.endswith(":base:0")
        assert capture.body_ref and capture.byte_count > 0
        evidence = inputs.read_perpetual_condition_inputs_in_session(
            session,
            instrument_ref=source.metadata_contract_reference(metadata.capture_id),
            scope_id="crypto:perp:okx:BTC-USDT-SWAP:trade:1m",
            as_of=capture.recorded_at.replace(tzinfo=__import__("datetime").UTC)
            + timedelta(seconds=1),
            source=source,
        )
        assert evidence.closes[-1].price == "67000"
        assert evidence.closes[-1].capture_id == capture_id

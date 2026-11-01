"""Real isolated SQLite publications/admission/outbox; no native or delivery claim."""

import importlib.util
from datetime import timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import func, select

from tests.decision_product.test_okx_perpetual_original import (
    persisted_original as persisted_original,
)
from tests.decision_product.test_perpetual_research_binding import (
    AT,
    OWNER,
    changed_risk,
    factory_for,
)
from tests.decision_product.test_perpetual_research_binding import binding_case as binding_case


def test_continuous_consent_cannot_be_inferred_from_a_legacy_once_command():
    module = "packages.kernel.decision_hub_kernel.ports.perpetual_plan_watch"
    assert importlib.util.find_spec(module), "explicit continuous watch wire is missing"
    from packages.kernel.decision_hub_kernel.ports.perpetual_plan_watch import (
        PerpetualPlanWatchCommandV2,
    )

    with pytest.raises(ValueError):
        PerpetualPlanWatchCommandV2.model_validate(
            {"schema_version": "perpetual-plan-watch-command.v1"}
        )


def published(case, tmp_path, clock):
    """Use the existing pure producer/repository fixture with unique report identity.

    A successor already has real common-admission originals; do not re-freeze it
    with the first fixture's old captures/time. No model/publication gate is faked.
    """
    from packages.kernel.decision_hub_kernel.application.perpetual_research_binding import (
        read_perpetual_admission_identity_in_session,
        read_perpetual_research_materials_in_session,
    )
    from packages.kernel.decision_hub_kernel.persistence.db import ArtifactRecord, RunRecord
    from tests.decision_product import test_perpetual_advice_query as fixture
    from tests.decision_product import test_perpetual_native_graph as native
    from tests.decision_product.test_perpetual_action_publication import lawful_packet_for

    factory, binding = factory_for(case, tmp_path)
    factory.clock = binding.now = lambda: clock[0]
    request = factory.build(case["event_id"], case["run_id"])
    with case["db"].session() as session:
        actual = read_perpetual_research_materials_in_session(session, request)

    def packet(req, materials):
        return lawful_packet_for(req, {**materials, "review_originals": actual["review_originals"]})

    def existing_admission(value):
        with value["db"].session() as session:
            assert read_perpetual_admission_identity_in_session(session, value["run_id"])

    report_id = "pure-report-" + case["run_id"]
    at = clock[0]
    with (
        patch.object(fixture, "AT", at),
        patch.object(native, "AT", at),
        patch.object(fixture, "freeze", existing_admission),
        patch(
            "packages.kernel.decision_hub_kernel.persistence.db.ArtifactRecord",
            lambda **kw: ArtifactRecord(**{**kw, "artifact_id": report_id}),
        ),
        patch("tests.decision_product.test_perpetual_action_publication.lawful_packet_for", packet),
    ):
        derived = fixture.persisted_publication(case)
    with case["db"].session() as session:
        session.get(RunRecord, case["run_id"]).artifact_id = report_id
    clock[0] = at + timedelta(seconds=40)
    return derived.branches[0].revision


@pytest.mark.asyncio
async def test_real_successor_two_expiries_restart_dedupe_pause_and_source_binding(
    binding_case, tmp_path
):
    from packages.kernel.decision_hub_kernel.application.decision_settings import (
        DecisionSettingsService,
    )
    from packages.kernel.decision_hub_kernel.application.perpetual_plan_followup import (
        PerpetualPlanFollowupService,
        read_plan_review_source_in_session,
    )
    from packages.kernel.decision_hub_kernel.application.perpetual_research_drivers import (
        PerpetualResearchAdmissionDrivers,
    )
    from packages.kernel.decision_hub_kernel.application.risk_research_admission import (
        RiskResearchAdmissionService,
    )
    from packages.kernel.decision_hub_kernel.persistence.db import OutboxRecord, RunRecord
    from packages.kernel.decision_hub_kernel.persistence.risk_research_records import (
        RiskResearchRequestRecord,
    )
    from packages.runtime_adapters.perpetual_research_entry import (
        configured_perpetual_research_services,
    )
    from tests.decision_product.test_perpetual_research_entry import BASE, client_for
    from tests.decision_product.test_perpetual_research_schedule import NextSlotCloses

    case, clock = binding_case, [AT]
    revision = published(case, tmp_path / "initial", clock)
    configured = configured_perpetual_research_services(
        case["db"], source=case["source"], now=lambda: clock[0]
    )
    configured.entry.collect_window = NextSlotCloses(case, clock)
    service = PerpetualPlanFollowupService(case["db"], configured.entry, now=lambda: clock[0])
    assert hasattr(service, "save_continuous"), "persistent consent/cursor lifecycle is missing"
    with case["db"].session() as session:
        saved = DecisionSettingsService.current_in_session(
            session, OWNER, case["settings"].partition
        )
    command = dict(
        schema_version="perpetual-plan-watch-command.v2",
        continuation="follow_published_revisions",
        request_id="continuous-enable",
        partition=case["settings"].partition.model_dump(mode="json"),
        horizon="4h",
        plan_id=revision.plan_id,
        expected_revision_ref=dict(ref=revision.revision_id, hash=revision.payload_hash),
        expected_state_version=0,
        expected_settings_revision=dict(
            ref=saved.revision.revision_id, hash=saved.revision.payload_hash
        ),
        expected_settings_state_version=saved.state_version,
        operation="watch",
        mode="remind_and_research",
    )
    client, _, _ = client_for(case, clock=lambda: clock[0])
    path = BASE + "/plans/watches/continuous/commands"
    reply = client.post(path, json=command, headers={"Idempotency-Key": command["request_id"]})
    assert reply.status_code == 200, reply.text
    receipt = service.receipt_continuous(OWNER, command["request_id"])
    assert client.get(path + "/" + command["request_id"]).json() == reply.json()
    csrf = client.headers.pop("X-Settings-CSRF")
    assert (
        client.post(
            path, json={**command, "request_id": "no-csrf"}, headers={"Idempotency-Key": "no-csrf"}
        ).status_code
        == 403
    )
    client.headers["X-Settings-CSRF"] = csrf
    assert service.save_continuous(OWNER, command) == receipt
    with pytest.raises(ValueError, match="conflict"):
        service.save_continuous(OWNER, {**command, "mode": "remind_only"})
    with pytest.raises(ValueError):
        service.save_continuous("foreign-owner", {**command, "request_id": "foreign"})
    for scope in (
        {"partition": {**command["partition"], "account_id": "foreign-account"}},
        {"horizon": "24h"},
    ):
        with pytest.raises(ValueError):
            service.save_continuous(OWNER, {**command, **scope, "request_id": "wrong-scope"})
    with pytest.raises(ValueError, match="conflict"):
        service.save_continuous(OWNER, {**command, "request_id": "stale"})

    def current(instance=service):
        return instance.current_continuous(
            OWNER, command["partition"], "4h", command["expected_settings_revision"]
        )

    def notices():
        with case["db"].session() as session:
            return session.scalar(
                select(func.count())
                .select_from(OutboxRecord)
                .where(OutboxRecord.dedupe_key.startswith("perpetual-plan-continuous:"))
            )

    first = current()
    assert first["progress"]["origin_run_id"] == case["run_id"]
    service.discover()
    service.discover()
    restarted = PerpetualPlanFollowupService(case["db"], configured.entry, now=lambda: clock[0])
    assert current(restarted) == first and notices() == 0
    clock[0] = revision.valid_until
    driver = PerpetualResearchAdmissionDrivers(case["db"], configured.entry, now=lambda: clock[0])
    driver.discover()
    driver.discover()
    expired = current()["progress"]
    assert expired["status"] == "expired" and expired["request_id"] and notices() == 1
    with case["db"].session() as session:
        proof = read_plan_review_source_in_session(
            session,
            expired["request_id"],
            None,
            None,
            at=clock[0],
            require_active=False,
            original_only=True,
        )
        assert proof["source"]["origin_run_id"] == case["run_id"]
        first_proof = proof
    clock[0] = __import__("datetime").datetime.fromisoformat(
        expired["auto_review_slot"].replace("Z", "+00:00")
    ) + timedelta(seconds=10)
    engine = RiskResearchAdmissionService(
        case["db"], enabled=True, clock=lambda: clock[0], perpetual_driver=driver
    )
    assert await engine.process_due(limit=1) == 1
    with case["db"].session() as session:
        request = session.get(RiskResearchRequestRecord, expired["request_id"])
        run = session.get(RunRecord, request.run_id)
        successor = {**case, "run_id": run.run_id, "event_id": run.event_id}
    second_revision = published(successor, tmp_path / "successor", clock)
    service.discover()
    service.discover()
    second = current()["progress"]
    assert second["plan_revision_ref"]["ref"] == second_revision.revision_id
    assert second["origin_run_id"] == successor["run_id"] != first["progress"]["origin_run_id"]
    assert second["result_hash"] == first["progress"]["result_hash"]
    assert notices() == 1, (
        "unchanged numeric result must not produce a successor change notification"
    )
    clock[0] = second_revision.valid_until
    service.discover()
    restarted.discover()
    second_expiry = current()["progress"]
    assert (
        second_expiry["status"] == "expired"
        and second_expiry["request_id"] != expired["request_id"]
    )
    assert notices() == 2
    with case["db"].session() as session:
        assert session.scalar(select(func.count()).select_from(RiskResearchRequestRecord)) == 2
        proof = read_plan_review_source_in_session(
            session,
            second_expiry["request_id"],
            None,
            None,
            at=clock[0],
            require_active=False,
            original_only=True,
        )
        assert proof["source"]["origin_run_id"] == successor["run_id"]
        assert proof["source"]["plan_revision_ref"] == second["plan_revision_ref"]
    paused = service.save_continuous(
        OWNER, {**command, "request_id": "pause", "expected_state_version": 1, "operation": "pause"}
    )
    assert paused.state == "paused"
    assert current()["receipt"]["state"] == "paused"
    with case["db"].session() as session:
        with pytest.raises(ValueError, match="paused|changed"):
            read_plan_review_source_in_session(
                session, second_expiry["request_id"], None, None, at=clock[0], claim_check=False
            )
        assert (
            read_plan_review_source_in_session(
                session,
                expired["request_id"],
                None,
                None,
                at=clock[0],
                require_active=False,
                run_id=successor["run_id"],
            )
            == first_proof
        )
    before = notices()
    clock[0] += timedelta(hours=3)
    service.discover()
    assert notices() == before
    resumed = {
        **command,
        "request_id": "resume-after-gap",
        "expected_state_version": 2,
        "plan_id": second_revision.plan_id,
        "expected_revision_ref": dict(
            ref=second_revision.revision_id, hash=second_revision.payload_hash
        ),
    }
    service.save_continuous(OWNER, resumed)
    service.discover()
    service.discover()
    assert current()["progress"]["status"] == "slot_missed"
    with case["db"].session() as session:
        missed = session.get(RiskResearchRequestRecord, current()["progress"]["request_id"])
        assert missed.status == "failed" and missed.error_code == "perpetual_watch_slot_expired"
    case["settings_service"].save(OWNER, changed_risk(case, "continuous-settings-changed"))
    service.discover()
    service.discover()
    assert current()["progress"]["status"] == "settings_changed"


def test_substantive_fingerprint_keeps_numeric_condition_and_qualification_changes():
    import json
    from copy import deepcopy
    from pathlib import Path

    from packages.kernel.decision_hub_kernel.application.perpetual_plan_watch_continuous import (
        result_hash,
    )

    value = json.loads(
        (
            Path(__file__).parents[2] / "artifacts/d2-perp-advice-ui/pure-presentation-fixture.json"
        ).read_text("utf-8")
    )["value"]
    original = result_hash(value)
    shifted = deepcopy(value)
    shifted["revision"]["revision_id"] = "another"
    shifted["revision"]["published_at"] = "2099-01-01T00:00:00Z"
    shifted["candidate_probability"]["as_of"] = "2099-01-01T00:00:00Z"
    assert result_hash(shifted) == original
    for mutate in (
        lambda v: v["candidate_probability"].update(kind="unavailable"),
        lambda v: v["comparison"]["candidate_results"][0].update(
            unit_expected_net_if_assumed_fills_usdt="123.456"
        ),
        lambda v: v["revision"]["selected_candidate"].update(
            condition_refs=[dict(ref="new-threshold", hash="a" * 64)]
        ),
        lambda v: v["candidate_probability"]["probabilities"][0].update(value="0.9"),
    ):
        changed = deepcopy(value)
        mutate(changed)
        assert result_hash(changed) != original


def test_equivalent_declared_decimal_spellings_do_not_notify_a_result_change():
    import json
    from copy import deepcopy
    from pathlib import Path

    from packages.kernel.decision_hub_kernel.application.perpetual_plan_watch_continuous import (
        result_hash,
    )

    value = json.loads(
        (
            Path(__file__).parents[2] / "artifacts/d2-perp-advice-ui/pure-presentation-fixture.json"
        ).read_text("utf-8")
    )["value"]
    equivalent = deepcopy(value)
    equivalent["candidate_probability"]["probabilities"][0]["value"] += "0"
    equivalent["comparison"]["candidate_results"][0]["unit_expected_net_if_assumed_fills_usdt"] += (
        "0"
    )
    equivalent["revision"]["selected_candidate"]["unit_notional_usdt"] = "100.0"
    equivalent["cost_assumptions"]["assumed_taker_rate"] += "0"
    assert result_hash(equivalent) == result_hash(value)


def test_terminal_watches_leave_capacity_and_waiting_scopes_rotate(tmp_path, monkeypatch):
    """Exercise actual SQLite selection; bypass publication checks only here.

    Receipt authenticity and business transitions are covered by the preceding
    real-publication test. These minimal rows represent scan candidates only.
    """
    from types import SimpleNamespace

    from packages.kernel.decision_hub_kernel.application import (
        perpetual_plan_watch_continuous as module,
    )
    from packages.kernel.decision_hub_kernel.persistence.db import Database, ProductCommandRecord
    from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash

    database = Database(f"sqlite:///{tmp_path / 'scan.sqlite3'}")
    database.create_all()
    seen = []
    with database.session() as session:
        for prefix, count in (("a-terminated", 32), ("b-waiting", 32), ("c-new-scope", 1)):
            for index in range(count):
                key = f"{prefix}-{index:02d}"
                body = dict(request_id=key, state_version=1, state="active")
                session.add(
                    ProductCommandRecord(
                        request_id=key,
                        owner_id=OWNER,
                        target_id=key,
                        kind=module.KIND,
                        payload_hash=canonical_hash(body),
                        result_json=module.canonical(body),
                        created_at=AT,
                    )
                )
                if prefix == "a-terminated":
                    terminal = dict(
                        status="settings_changed",
                        watch_ref=dict(ref=key, hash="a" * 64),
                        sequence=1,
                    )
                    session.add(
                        ProductCommandRecord(
                            request_id="terminal-" + key,
                            owner_id=OWNER,
                            target_id=key,
                            kind=module.PROGRESS,
                            payload_hash=canonical_hash(terminal),
                            result_json=module.canonical(terminal),
                            created_at=AT,
                        )
                    )

    def read(_session, owner, request):
        seen.append(request)
        return SimpleNamespace(watch_id=request, owner_id=owner, command=None)

    monkeypatch.setattr(module, "_read", read)
    monkeypatch.setattr(
        module,
        "_latest_progress",
        lambda _session, watch: dict(
            status="settings_changed" if watch.startswith("a-") else "expired"
        ),
    )
    monkeypatch.setattr(module, "_check_origin", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "_settings_current", lambda _session, _owner, _command: True)
    monkeypatch.setattr(module.ContinuousPlanWatch, "_successor", lambda *args: None)
    from packages.kernel.decision_hub_kernel.application.perpetual_plan_followup import (
        PerpetualPlanFollowupService,
    )

    entry = SimpleNamespace(database=database, total_deadline_seconds=3600)
    parent = PerpetualPlanFollowupService(database, entry, now=lambda: AT)
    parent.discover()
    assert len(seen) == 32 and all(value.startswith("b-waiting") for value in seen)
    seen.clear()
    parent.discover()
    assert len(seen) == 32 and "c-new-scope-00" in seen

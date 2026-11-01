"""Signed schedule originals -> existing admission request engine -> genuine Run."""

import importlib
import json
from datetime import timedelta

import pytest

from tests.decision_product.test_okx_perpetual_original import (
    persisted_original as persisted_original,
)
from tests.decision_product.test_perpetual_research_binding import AT, OWNER
from tests.decision_product.test_perpetual_research_binding import binding_case as binding_case
from tests.decision_product.test_perpetual_research_entry import BASE, client_for, counts


class NextSlotCloses:
    """New archived synthetic market pages at the next hour, not stale old pages."""

    def __init__(self, case, clock):
        self.case, self.clock, self.calls = case, clock, 0

    async def __call__(self, *, inst_id):
        from urllib.parse import parse_qs, urlsplit

        from packages.kernel.decision_hub_kernel.application.perpetual_close_source import (
            CollectedPerpetualCloseWindow,
        )
        from packages.kernel.decision_hub_kernel.persistence.db import RawCaptureRecord
        from packages.kernel.decision_hub_kernel.ports.perpetual_close import public_url

        self.calls += 1
        end = self.clock[0].replace(minute=0, second=0, microsecond=0)
        delta = end - AT.replace(minute=0, second=0, microsecond=0)
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
                    capture_id="periodic_" + original_id,
                    body_ref=ref,
                    body_hash=digest,
                    byte_count=len(self.case["archive"].read(ref)),
                    url=public_url(inst_id, after + int(delta.total_seconds() * 1000)),
                    started_at=old.started_at + delta,
                    completed_at=old.completed_at + delta,
                    recorded_at=old.recorded_at + delta,
                )
                session.add(RawCaptureRecord(**fields))
                ids.append(fields["capture_id"])
        return CollectedPerpetualCloseWindow(tuple(ids), end, self.clock[0])


def body(case, operation="enable", version=0):
    return dict(
        schema_version="perpetual-research-schedule-command.v1",
        request_id=f"schedule-{operation}-{version}",
        partition=case["settings"].partition.model_dump(mode="json"),
        horizon="24h",
        expected_state_version=version,
        operation=operation,
        interval_seconds=3600,
        settings_binding_policy="follow_current_saved_head",
    )


def test_signed_schedule_enable_pause_original_and_cas_without_research(binding_case):
    case = binding_case
    accepted = AT.replace(minute=59, second=0)
    client, _, collector = client_for(case, clock=lambda: accepted)
    before = counts(case)
    command = body(case)
    reply = client.post(
        BASE + "/research/schedules/commands",
        json=command,
        headers={"Idempotency-Key": command["request_id"]},
    )
    assert reply.status_code == 200, reply.text
    receipt = reply.json()
    assert receipt["command"] == command and receipt["state"] == "active"
    assert receipt["owner_id"] == OWNER
    assert collector.calls == 0
    assert counts(case)[:3] == before[:3]
    current = client.get(
        BASE + "/research/schedules/current", params={**command["partition"], "horizon": "24h"}
    ).json()
    assert current["revision"] == receipt["revision"]
    assert current["next_due_at"] == "2026-10-18T13:00:00Z"
    csrf = client.headers.pop("X-Settings-CSRF")
    assert (
        client.post(
            BASE + "/research/schedules/commands",
            json={**command, "request_id": "no-csrf"},
            headers={"Idempotency-Key": "no-csrf"},
        ).status_code
        == 403
    )
    client.headers["X-Settings-CSRF"] = csrf
    assert (
        client.get(BASE + "/research/schedules/commands/" + command["request_id"]).json() == receipt
    )
    paused = body(case, "pause", 1)
    reply = client.post(
        BASE + "/research/schedules/commands",
        json=paused,
        headers={"Idempotency-Key": paused["request_id"]},
    )
    assert reply.status_code == 200 and reply.json()["state"] == "paused"
    stale = {**command, "request_id": "stale-enable"}
    assert (
        client.post(
            BASE + "/research/schedules/commands",
            json=stale,
            headers={"Idempotency-Key": stale["request_id"]},
        ).status_code
        == 409
    )
    anonymous, _, _ = client_for(case, pair=False)
    assert anonymous.post(BASE + "/research/schedules/commands", json=command).status_code == 403


@pytest.mark.asyncio
async def test_periodic_slot_uses_common_claim_and_real_automatic_admission(binding_case, tmp_path):
    module = importlib.import_module(
        "packages.kernel.decision_hub_kernel.application.perpetual_research_schedule"
    )
    from packages.kernel.decision_hub_kernel.application.perpetual_research_binding import (
        read_perpetual_admission_identity_in_session,
    )
    from packages.kernel.decision_hub_kernel.application.risk_research_admission import (
        RiskResearchAdmissionService,
    )
    from packages.kernel.decision_hub_kernel.persistence.db import RunRecord, StudyRecord

    case = binding_case
    clock = [AT.replace(minute=59, second=0)]
    collector = NextSlotCloses(case, clock)
    from packages.runtime_adapters.perpetual_research_entry import (
        configured_perpetual_research_services,
    )

    configured = configured_perpetual_research_services(
        case["db"], source=case["source"], now=lambda: clock[0]
    )
    entry = configured.entry
    assert entry.total_deadline_seconds == 3600
    entry.collect_window = collector
    driver = module.PerpetualResearchScheduleService(case["db"], entry, now=lambda: clock[0])
    driver.save(OWNER, body(case))
    driver.discover()
    assert driver.current(OWNER, case["settings"].partition, "24h")["last_slot"] is None
    clock[0] = AT + timedelta(hours=1)
    driver.discover()
    legacy = RiskResearchAdmissionService(case["db"], enabled=True, clock=lambda: clock[0])
    assert legacy._claim() is None, "legacy claimant must not take a perpetual protocol request"
    engine = RiskResearchAdmissionService(
        case["db"], enabled=True, clock=lambda: clock[0], perpetual_driver=driver
    )
    processed = await engine.process_due(limit=1)
    diagnostic = driver.current(OWNER, case["settings"].partition, "24h")
    assert processed == 1, diagnostic["last_slot"]
    view = driver.current(OWNER, case["settings"].partition, "24h")
    assert view["last_slot"]["status"] == "queued"
    run_id = view["last_slot"]["run_id"]
    with case["db"].session() as session:
        run = session.get(RunRecord, run_id)
        assert run.admission_origin == "automatic"
        assert session.get(StudyRecord, run.study_id).horizon_scope == "24h"
        original, _ = read_perpetual_admission_identity_in_session(session, run_id)
        assert original["automatic_source"]["schedule_ref"] == view["revision"]
        assert original["runtime_deadline_at"] != original["valid_until"]
        assert original["automatic_source"]["source"]["scheduled_at"] == "2026-10-18T13:00:00Z"
        assert original["valid_until"] == "2026-10-18T14:00:00Z"
    assert collector.calls == 1
    assert await engine.process_due(limit=1) == 0
    from packages.kernel.decision_hub_kernel.application.research_binding import (
        read_research_bundle_in_session,
    )
    from tests.decision_product.test_perpetual_research_binding import factory_for

    actual = {**case, "run_id": run_id, "event_id": run.event_id}
    factory, binding = factory_for(actual, tmp_path)
    factory.clock = lambda: clock[0]
    binding.now = lambda: clock[0]
    request = factory.build(actual["event_id"], run_id)
    with case["db"].session() as session:
        bundle = read_research_bundle_in_session(session, run_id)
        assert bundle.task.scope.horizon_scope == ["24h"]
    assert request.deadline_at == clock[0] + timedelta(hours=1)
    assert request.execution_budget.total_deadline_seconds == 3600


@pytest.mark.asyncio
async def test_paused_preparation_and_expired_slots_create_no_run(binding_case):
    module = importlib.import_module(
        "packages.kernel.decision_hub_kernel.application.perpetual_research_schedule"
    )
    from packages.kernel.decision_hub_kernel.application.risk_research_admission import (
        RiskResearchAdmissionService,
    )

    case = binding_case
    clock = [AT]
    _, entry, _ = client_for(case, clock=lambda: clock[0])
    entry.collect_window = NextSlotCloses(case, clock)
    driver = module.PerpetualResearchScheduleService(case["db"], entry, now=lambda: clock[0])
    driver.save(OWNER, body(case))
    clock[0] = AT + timedelta(hours=1)
    driver.discover()
    engine = RiskResearchAdmissionService(
        case["db"], enabled=True, clock=lambda: clock[0], perpetual_driver=driver
    )
    claim = engine._claim()
    prepared = await driver.prepare(claim[0], claim[2], engine.owner_id)
    driver.save(OWNER, body(case, "pause", 1))
    before = counts(case)
    with pytest.raises(ValueError, match="perpetual_schedule_changed_or_paused"):
        driver.admit(claim[0], claim[2], engine, prepared)
    assert counts(case) == before
    driver.save(OWNER, {**body(case, "enable", 2), "request_id": "resume"})
    clock[0] = AT + timedelta(hours=3)
    driver.discover()
    assert driver.current(OWNER, case["settings"].partition, "24h")["missed_slots"] >= 1
    assert counts(case)[:3] == before[:3]


@pytest.mark.parametrize("persisted_legacy", [True, False])
def test_migration_preserves_legacy_requests_and_refuses_lossy_downgrade(
    tmp_path, persisted_legacy
):
    from pathlib import Path

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text

    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{tmp_path / 'migration.sqlite3'}")
    command.upgrade(config, "0101_decision_opportunity_version")
    engine = create_engine(config.get_main_option("sqlalchemy.url"))
    with engine.begin() as connection:
        if persisted_legacy:
            # Exact persisted pre-change 0101 shape: the historical dynamic ORM
            # migration above includes the new column only in fresh installs.
            connection.execute(
                text("ALTER TABLE risk_research_requests DROP COLUMN protocol_version")
            )
        connection.execute(
            text(
                "INSERT INTO risk_research_requests(request_id,scope_id,instrument,status,created_at,updated_at,available_at,attempts,lease_generation) VALUES('old','crypto:spot:okx:BTC-USDT:trade:1m','BTC-USDT','pending_admission','2026-10-19','2026-10-19','2026-10-19',0,0)"
            )
        )
        before = dict(
            connection.execute(text("SELECT * FROM risk_research_requests")).mappings().one()
        )
        before.pop("protocol_version", None)
    if not persisted_legacy:
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE risk_research_requests SET protocol_version='unrecognized'")
            )
        with pytest.raises(RuntimeError, match="early_protocol_column_invalid"):
            command.upgrade(config, "0102_perpetual_research_schedules")
        with engine.begin() as connection:
            assert (
                connection.execute(
                    text("SELECT protocol_version FROM risk_research_requests")
                ).scalar_one()
                == "unrecognized"
            )
            connection.execute(
                text(
                    "UPDATE risk_research_requests SET protocol_version='risk-research-request.spot.v1'"
                )
            )
    command.upgrade(config, "0102_perpetual_research_schedules")
    with engine.begin() as connection:
        after = dict(
            connection.execute(text("SELECT * FROM risk_research_requests")).mappings().one()
        )
        assert after.pop("protocol_version") == "risk-research-request.spot.v1"
        assert before == after
        with pytest.raises(Exception, match="research_request_protocol_immutable"):
            connection.execute(
                text(
                    "UPDATE risk_research_requests SET protocol_version='wrong' WHERE request_id='old'"
                )
            )
        connection.execute(
            text(
                "INSERT INTO risk_research_requests(request_id,protocol_version,scope_id,instrument,status,created_at,updated_at,available_at,attempts,lease_generation) VALUES('perp','risk-research-request.perpetual-periodic.v1','crypto:perp:okx:BTC-USDT-SWAP:trade:1m','BTC-USDT-SWAP','pending_admission','2026-10-19','2026-10-19','2026-10-19',0,0)"
            )
        )
    with pytest.raises(RuntimeError, match="perpetual_schedule_restore_required"):
        command.downgrade(config, "0101_decision_opportunity_version")
    with engine.begin() as connection:
        assert (
            connection.execute(text("SELECT count(*) FROM risk_research_requests")).scalar_one()
            == 2
        )
        connection.execute(text("DELETE FROM risk_research_requests WHERE request_id='perp'"))
    command.downgrade(config, "0101_decision_opportunity_version")
    with engine.connect() as connection:
        assert (
            dict(connection.execute(text("SELECT * FROM risk_research_requests")).mappings().one())
            == before
        )

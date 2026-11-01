"""Independent event motives reuse the existing perpetual admission lane."""

import importlib
import json
from datetime import timedelta

import pytest

from packages.kernel.decision_hub_kernel.application.perpetual_research_schedule import (
    PerpetualResearchScheduleService,
    read_automatic_source_in_session,
)
from packages.kernel.decision_hub_kernel.application.risk_research_admission import (
    RiskResearchAdmissionService,
)
from packages.kernel.decision_hub_kernel.persistence.risk_research_records import (
    RiskResearchRequestRecord,
    RiskResearchTriggerRecord,
)
from tests.decision_product.test_okx_perpetual_original import (
    persisted_original as persisted_original,
)
from tests.decision_product.test_perpetual_event_agenda import schedule_sources
from tests.decision_product.test_perpetual_research_binding import AT, OWNER
from tests.decision_product.test_perpetual_research_binding import (
    binding_case as binding_case,
)
from tests.decision_product.test_perpetual_research_entry import client_for
from tests.decision_product.test_perpetual_research_schedule import NextSlotCloses, body


@pytest.mark.asyncio
async def test_event_only_batch_is_idempotent_and_uses_existing_admission(binding_case):
    event_module = importlib.import_module(
        "packages.kernel.decision_hub_kernel.application.perpetual_event_research"
    )
    case = binding_case
    schedule_sources(case)
    clock = [AT]
    _, entry, _ = client_for(case, clock=lambda: clock[0])
    schedule = PerpetualResearchScheduleService(case["db"], entry, now=lambda: clock[0])
    schedule.save(OWNER, {**body(case), "horizon": "72h"})
    clock[0] = AT + timedelta(seconds=30)
    events = event_module.PerpetualEventResearchService(
        case["db"], entry, now=lambda: clock[0]
    )

    assert events.discover() == 1
    with case["db"].session() as session:
        requests = session.query(RiskResearchRequestRecord).all()
        assert len(requests) == 1
        request = requests[0]
        assert request.protocol_version == "risk-research-request.perpetual-event.v1"
        triggers = (
            session.query(RiskResearchTriggerRecord)
            .filter_by(request_id=request.request_id)
            .order_by(RiskResearchTriggerRecord.trigger_key)
            .all()
        )
        assert [row.kind for row in triggers].count("perpetual_event") == 1
        assert [row.kind for row in triggers].count("perpetual_event_motive") == 3
        root = json.loads(
            next(row.payload_json for row in triggers if row.kind == "perpetual_event")
        )
        assert root["owner_id"] == OWNER
        assert root["partition"] == case["settings"].partition.model_dump(mode="json")
        assert root["horizon"] == "72h"
        assert root["scheduled_at"] == clock[0].isoformat().replace("+00:00", "Z")
        assert root["deadline_at"] == (clock[0] + timedelta(hours=1)).isoformat().replace(
            "+00:00", "Z"
        )
        assert len(root["trigger_events"]) == 3
        proof = read_automatic_source_in_session(
            session, request.request_id, None, None, at=clock[0], original_only=True
        )
        assert proof["source"] == root
        assert len(proof["event_motives"]) == 3
        request_id = request.request_id

    assert events.discover() == 0
    with case["db"].session() as session:
        assert session.query(RiskResearchRequestRecord).count() == 1
        assert session.query(RiskResearchTriggerRecord).count() == 4

    engine = RiskResearchAdmissionService(
        case["db"], enabled=True, clock=lambda: clock[0], perpetual_driver=schedule
    )
    claim = engine._claim()
    assert claim is not None and claim[0] == request_id
    entry.collect_window = NextSlotCloses(case, clock)
    prepared = await schedule.prepare(claim[0], claim[2], engine.owner_id)
    assert schedule.admit(claim[0], claim[2], engine, prepared) is True
    current = schedule.current(OWNER, case["settings"].partition, "72h")
    assert current["last_slot"]["request_id"] == request_id
    assert current["last_slot"]["status"] == "queued"


def test_periodic_request_absorbs_same_event_batch_without_second_request(binding_case):
    event_module = importlib.import_module(
        "packages.kernel.decision_hub_kernel.application.perpetual_event_research"
    )
    case = binding_case
    schedule_sources(case)
    clock = [AT]
    _, entry, _ = client_for(case, clock=lambda: clock[0])
    schedule = PerpetualResearchScheduleService(case["db"], entry, now=lambda: clock[0])
    schedule.save(OWNER, body(case))
    clock[0] = AT + timedelta(hours=1)

    schedule.discover()
    events = event_module.PerpetualEventResearchService(
        case["db"], entry, now=lambda: clock[0]
    )
    assert events.discover() == 0
    with case["db"].session() as session:
        requests = session.query(RiskResearchRequestRecord).all()
        assert len(requests) == 1
        assert requests[0].protocol_version == "risk-research-request.perpetual-motives.v2"
        triggers = session.query(RiskResearchTriggerRecord).all()
        assert [row.kind for row in triggers].count("perpetual_periodic") == 1
        assert [row.kind for row in triggers].count("perpetual_event_motive") == 3

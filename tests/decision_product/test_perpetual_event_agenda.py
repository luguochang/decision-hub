"""Archived official source contexts, never network/model or inferred exact T0."""

import importlib
import json
from datetime import timedelta

import pytest

from packages.kernel.decision_hub_kernel.application.admission import AdmissionService
from packages.kernel.decision_hub_kernel.persistence.db import (
    ObservationRecord,
    RawCaptureRecord,
    SourcePollAttemptRecord,
)
from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash
from packages.source_adapters.official_feeds.presets import official_source_presets
from tests.decision_product.test_okx_perpetual_original import (
    persisted_original as persisted_original,
)
from tests.decision_product.test_perpetual_research_binding import AT, OWNER
from tests.decision_product.test_perpetual_research_binding import binding_case as binding_case


def schedule_sources(case):
    """Normal parsers + admission; synthetic bytes are explicitly archived originals."""
    from packages.contracts_py.decision_hub_contracts import MacroEventPayload, TextEnvelope
    from packages.kernel.decision_hub_kernel.ports.cpi_schedule import (
        fred_calendar_url,
        parse_cpi_publication_schedules,
    )
    from packages.provider_adapters.research.raw_capture import safe_url
    from packages.source_adapters.official_feeds.diagnostics import response_snapshot
    from packages.source_adapters.official_feeds.html_calendar import parse_official_schedule_html

    presets = {source.manifest.source_id: source for source in official_source_presets()}
    fixtures = [
        (
            "cpi-publication-schedule",
            b"<h1>2026 Economic Release Calendar - Consumer Price Index</h1><p>All times are US Central Time.</p><table><tr><td>Monday October 19, 2026</td></tr><tr><td>7:30 AM</td><td>Consumer Price Index</td></tr></table>",
        ),
        (
            "bls-calendar",
            b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:employment-202610\r\nSUMMARY:Employment Situation\r\nDTSTART:20261019T123000Z\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n",
        ),
        ("fed-calendar", b"<h2>2026 FOMC Meetings</h2><ul><li>October 18-19</li></ul>"),
    ]
    observations = {}
    for source_id, raw in fixtures:
        source = presets[source_id]
        attempt, capture_id = "agenda_poll_" + source_id, "agenda_raw_" + source_id
        digest, ref = case["archive"].put(raw)
        received = AT - timedelta(seconds=4)
        url = (
            fred_calendar_url(2026) if source_id == "cpi-publication-schedule" else source.endpoint
        )
        if source_id == "cpi-publication-schedule":
            payload = parse_cpi_publication_schedules(
                raw,
                None,
                year=2026,
                fred_capture=dict(
                    capture_id=capture_id,
                    raw_body_hash=digest,
                    raw_body_ref=ref,
                    url=url,
                    received_at=received,
                ),
                dol_capture=None,
                attempt_id=attempt,
            )[0]
            text = json.dumps(
                payload.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            envelope = TextEnvelope(
                source_id=source_id,
                source_type="official_feed",
                observed_at=received,
                received_at=received,
                raw_text=text,
                language="en",
                event_hint=payload.event_key,
                event_family="cpi",
                scheduled_at=payload.scheduled_at,
                source_url=url,
                content_hash=canonical_hash(payload.model_dump(mode="json")),
                macro_event=MacroEventPayload(
                    external_event_id=payload.event_key,
                    family="cpi",
                    scheduled_at=payload.scheduled_at,
                    scheduled_date=payload.scheduled_date,
                    time_precision="exact",
                    source_timezone="America/Chicago",
                ),
            )
        elif source_id == "bls-calendar":
            envelope = source._parse_events(raw, received)[0]
        else:
            uid, start, summary = parse_official_schedule_html(
                raw.decode(), source_id=source_id, family="fomc", event_title=source.event_title
            )[0]
            envelope = source._to_envelope(uid, start, summary, received)
        snapshot = response_snapshot(200, raw, source.manifest.version).model_copy(
            update=dict(
                parse_status="valid", parsed_count=1, coverage_status="available", upcoming_count=1
            )
        )
        with case["db"].session() as session:
            session.add(
                RawCaptureRecord(
                    capture_id=capture_id,
                    context_id="source:" + attempt,
                    url=safe_url(url),
                    status_code=200,
                    body_hash=digest,
                    body_ref=ref,
                    byte_count=len(raw),
                    media_type="text/plain",
                    adapter_version=source.manifest.version,
                    error_code=None,
                    started_at=AT - timedelta(seconds=6),
                    completed_at=received,
                    recorded_at=AT - timedelta(seconds=3),
                )
            )
            session.add(
                SourcePollAttemptRecord(
                    attempt_id=attempt,
                    source_id=source_id,
                    trigger_type="scheduled",
                    status="succeeded",
                    started_at=AT - timedelta(seconds=8),
                    completed_at=AT - timedelta(seconds=1),
                    cursor_before=None,
                    cursor_after=None,
                    received_count=1,
                    accepted_count=1,
                    duplicate_count=0,
                    revision_count=0,
                    latency_ms=1,
                    error_code=None,
                    content_snapshot_json=snapshot.model_dump_json(),
                )
            )
        event_id, _, accepted = AdmissionService(
            case["db"], clock=lambda: AT - timedelta(seconds=2)
        ).admit_envelopes((envelope,))[0]
        assert accepted
        with case["db"].session() as session:
            row = session.query(ObservationRecord).filter_by(event_id=event_id).one()
            observations[source_id] = row.observation_id
    return observations


def test_actual_agenda_reopens_sources_and_keeps_precision_and_provider_gaps(persisted_original):
    assert importlib.util.find_spec(
        "packages.kernel.decision_hub_kernel.application.perpetual_event_agenda"
    ), "source-authenticated perpetual agenda missing"
    module = importlib.import_module(
        "packages.kernel.decision_hub_kernel.application.perpetual_event_agenda"
    )
    db, archive, source, capture = persisted_original
    case = dict(db=db, archive=archive)
    instrument = source.metadata_contract_reference(capture.capture_id)
    ids = schedule_sources(case)
    with case["db"].session() as session:
        from packages.kernel.decision_hub_kernel.application.official_schedule_original import (
            read_official_schedule_original_in_session,
        )

        assert (
            read_official_schedule_original_in_session(
                session, ids["cpi-publication-schedule"], archive=archive, cutoff_at=AT
            ).family
            == "cpi"
        )
        agenda = module.collect_event_agenda_in_session(
            session,
            archive=case["archive"],
            as_of=AT,
            instrument_refs=[instrument],
            window_end=AT + timedelta(hours=25),
        )
        assert {row.family for row in agenda.entries} == {"cpi", "nfp", "fomc"}
        assert next(row for row in agenda.entries if row.family == "fomc").scheduled_at is None
        assert next(row for row in agenda.coverage if row.family == "pce").status == "unavailable"
        assert {row.source_ref.ref for row in agenda.entries} == set(ids.values())
        assert all(row.dependency_status == "unknown" for row in agenda.entries)
        assert (
            module.read_event_agenda_in_session(session, agenda, archive=case["archive"]) == agenda
        )
    with case["db"].session() as session:
        capture = session.get(RawCaptureRecord, "agenda_raw_bls-calendar")
        path = case["archive"].root / capture.body_hash[:2] / capture.body_hash
    path.write_bytes(path.read_bytes() + b" ")
    with (
        case["db"].session() as session,
        pytest.raises(ValueError, match="agenda_original_changed"),
    ):
        module.read_event_agenda_in_session(session, agenda, archive=case["archive"])


def test_saved_admission_factory_and_independent_catalog_keep_same_agenda(binding_case, tmp_path):
    from packages.kernel.decision_hub_kernel.application.perpetual_research_binding import (
        read_perpetual_research_materials_in_session,
    )
    from tests.decision_product.test_perpetual_research_binding import factory_for
    from tests.decision_product.test_perpetual_research_entry import BASE, client_for, command

    case = binding_case
    schedule_sources(case)
    client, _, _ = client_for(case)
    value = {**command(case), "schema_version": "perpetual-research-command.v2", "horizon": "24h"}
    reply = client.post(
        BASE + "/research", json=value, headers={"Idempotency-Key": value["request_id"]}
    )
    assert reply.status_code == 200, reply.text
    accepted = reply.json()
    actual = {**case, "run_id": accepted["run_id"], "event_id": accepted["event_id"]}
    factory, _ = factory_for(actual, tmp_path)
    request = factory.build(actual["event_id"], actual["run_id"])
    with case["db"].session() as session:
        materials = read_perpetual_research_materials_in_session(session, request)
    agenda = materials["event_agenda"]
    assert {entry["family"] for entry in agenda["entries"]} == {"cpi", "nfp", "fomc"}
    original = next(
        row for row in materials["review_originals"]["originals"] if row["role"] == "event_agenda"
    )
    assert json.loads(original["body_json"])["agenda"] == agenda
    assert json.loads(original["body_json"])["child_path"] == "/event_agenda"
    assert original["original_ref"] == materials["event_agenda_ref"]
    assert agenda["as_of"] == AT.isoformat().replace("+00:00", "Z")


def test_latest_calendar_does_not_retain_removed_historical_meeting(persisted_original):
    from packages.kernel.decision_hub_kernel.application.perpetual_event_agenda import (
        collect_event_agenda_in_session,
        read_event_agenda_in_session,
    )
    from packages.source_adapters.official_feeds.diagnostics import response_snapshot

    db, archive, source, capture = persisted_original
    schedule_sources(dict(db=db, archive=archive))
    instrument = source.metadata_contract_reference(capture.capture_id)
    with db.session() as session:
        previous = collect_event_agenda_in_session(
            session,
            archive=archive,
            as_of=AT - timedelta(microseconds=750000),
            instrument_refs=[instrument],
            window_end=AT + timedelta(hours=25),
        )
    raw = b"<h2>2026 FOMC Meetings</h2><ul><li>October 20-21</li></ul>"
    digest, ref = archive.put(raw)
    with db.session() as session:
        old = session.get(RawCaptureRecord, "agenda_raw_fed-calendar")
        fields = {c.name: getattr(old, c.name) for c in old.__table__.columns}
        fields.update(
            capture_id="agenda_updated_fed",
            context_id="source:agenda_updated_fed",
            body_hash=digest,
            body_ref=ref,
            byte_count=len(raw),
            started_at=AT - timedelta(microseconds=700000),
            completed_at=AT - timedelta(microseconds=600000),
            recorded_at=AT - timedelta(microseconds=550000),
        )
        session.add(RawCaptureRecord(**fields))
        old = session.get(SourcePollAttemptRecord, "agenda_poll_fed-calendar")
        fields = {c.name: getattr(old, c.name) for c in old.__table__.columns}
        fields.update(
            attempt_id="agenda_updated_fed",
            started_at=AT - timedelta(microseconds=710000),
            completed_at=AT - timedelta(microseconds=500000),
            content_snapshot_json=response_snapshot(200, raw, "official-html-calendar.v2")
            .model_copy(update=dict(parse_status="valid", parsed_count=1))
            .model_dump_json(),
        )
        session.add(SourcePollAttemptRecord(**fields))
    with db.session() as session:
        current = collect_event_agenda_in_session(
            session,
            archive=archive,
            as_of=AT,
            instrument_refs=[instrument],
            window_end=AT + timedelta(hours=25),
        )
        assert not any(row.family == "fomc" for row in current.entries)
        assert (
            "schedule_not_in_latest_calendar"
            in next(row for row in current.coverage if row.family == "fomc").reason_codes
        )
        assert read_event_agenda_in_session(session, previous, archive=archive) == previous


@pytest.mark.asyncio
async def test_slot_and_event_motives_share_supported_existing_request(binding_case):
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
    from tests.decision_product.test_perpetual_research_entry import client_for
    from tests.decision_product.test_perpetual_research_schedule import body

    case = binding_case
    schedule_sources(case)
    clock = [AT]
    _, entry, _ = client_for(case, clock=lambda: clock[0])
    driver = PerpetualResearchScheduleService(case["db"], entry, now=lambda: clock[0])
    driver.save(OWNER, body(case))
    clock[0] = AT + timedelta(hours=1)
    driver.discover()
    driver.discover()
    with case["db"].session() as session:
        requests = session.query(RiskResearchRequestRecord).all()
        assert len(requests) == 1
        assert requests[0].protocol_version == "risk-research-request.perpetual-motives.v2"
        assert (
            session.query(RiskResearchTriggerRecord)
            .filter_by(request_id=requests[0].request_id)
            .count()
            == 4
        )
    old = RiskResearchAdmissionService(case["db"], enabled=True, clock=lambda: clock[0])
    assert old._claim() is None
    engine = RiskResearchAdmissionService(
        case["db"], enabled=True, clock=lambda: clock[0], perpetual_driver=driver
    )
    claim = engine._claim()
    with case["db"].session() as session:
        proof = read_automatic_source_in_session(
            session, claim[0], claim[2], engine.owner_id, at=clock[0]
        )
        assert len(proof["event_motives"]) == 3
    before = driver.current(OWNER, case["settings"].partition, "24h")
    driver.discover()
    assert driver.current(OWNER, case["settings"].partition, "24h") == before
    from packages.kernel.decision_hub_kernel.application.perpetual_research_binding import (
        read_perpetual_admission_identity_in_session,
    )
    from tests.decision_product.test_perpetual_research_schedule import NextSlotCloses

    entry.collect_window = NextSlotCloses(case, clock)
    prepared = await driver.prepare(claim[0], claim[2], engine.owner_id)
    assert driver.admit(claim[0], claim[2], engine, prepared) is True
    current = driver.current(OWNER, case["settings"].partition, "24h")
    assert current["last_slot"]["status"] == "queued"
    with case["db"].session() as session:
        admission, _ = read_perpetual_admission_identity_in_session(
            session, current["last_slot"]["run_id"]
        )
        assert len(admission["automatic_source"]["event_motives"]) == 3
        assert {entry["family"] for entry in admission["event_agenda"]["entries"]} == {
            "cpi",
            "nfp",
            "fomc",
        }


def test_actual_tool_independent_review_and_same_returned_report_renderer(
    binding_case, tmp_path, monkeypatch
):
    from packages.kernel.decision_hub_kernel.application import perpetual_research_report as report

    assert callable(getattr(report, "render_perpetual_report_content", None)), (
        "same authenticated report renderer has not been exposed"
    )
    from tests.decision_product.test_perpetual_native_graph import (
        build_perpetual_native_graph_case,
        packet_for,
    )

    case = binding_case
    schedule_sources(case)
    from tests.decision_product.test_perpetual_research_entry import BASE, client_for, command

    client, _, _ = client_for(case)
    command_value = {
        **command(case),
        "schema_version": "perpetual-research-command.v2",
        "horizon": "24h",
    }
    response = client.post(
        BASE + "/research",
        json=command_value,
        headers={"Idempotency-Key": command_value["request_id"]},
    )
    assert response.status_code == 200, response.text
    accepted = response.json()
    case = {**case, "run_id": accepted["run_id"], "event_id": accepted["event_id"]}

    def authored(request, materials):
        packet = packet_for(request, materials)
        for candidate in packet["synthesis"]["candidate_set"]["candidates"]:
            candidate["cost_ref"] = materials["cost_policy_ref"]
        original = next(
            row
            for row in materials["review_originals"]["originals"]
            if row["role"] == "event_agenda"
        )
        link = packet["synthesis"]["analysis"]["main_chain"][0]
        link["evidence_refs"].append(original["source_ref"])
        link["statement"] += " CPI和就业日程可核验，FOMC仅日期，PCE日程缺口需保留。"
        return packet

    value = build_perpetual_native_graph_case(
        case, tmp_path, monkeypatch, packet_builder=authored, phased=True, run_graph=False
    )
    native = value["native"]
    from packages.kernel.decision_hub_kernel.application.perpetual_research_reader import (
        _review_originals,
    )

    originals = _review_originals(native)
    catalog = json.loads(originals.review_originals_json)
    authorization = json.loads(originals.authorization_json)
    content = report.render_perpetual_report_content(
        synthesis=native.synthesis.model_dump(mode="json"),
        review_request=native.review_request.model_dump(mode="json"),
        review=authorization,
        catalog=catalog,
        binding=dict(
            proof_scope="actual_tool_review_and_shared_renderer_only_no_committed_report_claim",
            mapped_ref=value["mapped_ref"].model_dump(mode="json"),
            synthesis_ref=native.review_request.synthesis_ref.model_dump(mode="json"),
            native_review_verdict=authorization["verdict"],
            financial_qualification="unqualified",
        ),
    )
    text = "\n".join(content.candidate["inferences"])
    assert "窗口事件议程" in text and "具体时间未公布" in text
    assert "PCE" in text and "CPI" in text and "就业" in text
    assert content.candidate["perpetual_synthesis"]["review_originals"] == catalog
    assert content.candidate["perpetual_synthesis"]["financial_qualified"] is False
    # Rehashing a changed child and its parent cannot change the saved Task's
    # exact admission original. This is the same source instance, no Graph run.
    from packages.kernel.decision_hub_kernel.application.perpetual_research_binding import (
        ADMISSION_ROLE,
        canonical,
        read_perpetual_research_materials_in_session,
    )
    from packages.kernel.decision_hub_kernel.persistence.db import RunRoleOutputRecord

    with case["db"].session() as session:
        parent = (
            session.query(RunRoleOutputRecord)
            .filter_by(run_id=case["run_id"], role=ADMISSION_ROLE)
            .one()
        )
        old_json, old_hash = parent.payload_json, parent.payload_hash
        changed = json.loads(old_json)
        changed["event_agenda"]["coverage"][-1]["status"] = "available"
        changed["event_agenda"]["payload_hash"] = canonical_hash(
            {key: body for key, body in changed["event_agenda"].items() if key != "payload_hash"}
        )
        parent.payload_json, parent.payload_hash = canonical(changed), canonical_hash(changed)
    try:
        with case["db"].session() as session, pytest.raises(ValueError):
            read_perpetual_research_materials_in_session(session, value["request"])
    finally:
        with case["db"].session() as session:
            parent = (
                session.query(RunRoleOutputRecord)
                .filter_by(run_id=case["run_id"], role=ADMISSION_ROLE)
                .one()
            )
            parent.payload_json, parent.payload_hash = old_json, old_hash

"""B: explicit durable automatic consent -> current ledger -> full lawful writer.

Only public market responses and the phased child reply are synthetic. Real
settings/ledger/consent/claim/Host/Graph/publication validators remain installed.
"""

import asyncio
import json
from datetime import datetime, timedelta

import httpx
import pytest

from tests.decision_product import test_perpetual_action_publication as publication
from tests.decision_product.test_okx_perpetual_original import (
    persisted_original as persisted_original,
)
from tests.decision_product.test_perpetual_research_binding import OWNER
from tests.decision_product.test_perpetual_research_binding import (
    binding_case as binding_case,
)

POLICY = "saved_preferences_current_paper_ledger.v1"


@pytest.mark.parametrize("origin", ["periodic", "event", "watch"])
def test_explicit_automatic_consent_settlement_full_writer_and_history(
    binding_case, monkeypatch, tmp_path, record_property, origin
):
    horizon = "72h" if origin == "event" else "4h"
    print("B_FIXTURE_ROOT", origin, str(tmp_path), flush=True)
    from packages.kernel.decision_hub_kernel.application import (
        perpetual_research_binding as binding,
    )
    from packages.kernel.decision_hub_kernel.application.decision_settings import (
        DecisionSettingsService,
    )
    from packages.kernel.decision_hub_kernel.application.perpetual_close_source import (
        CollectedPerpetualCloseWindow,
    )
    from packages.kernel.decision_hub_kernel.application.perpetual_event_research import (
        PerpetualEventResearchService,
    )
    from packages.kernel.decision_hub_kernel.application.perpetual_funding_history_source import (
        PerpetualFundingHistorySource,
    )
    from packages.kernel.decision_hub_kernel.application.perpetual_plan_followup import (
        PerpetualPlanFollowupService,
    )
    from packages.kernel.decision_hub_kernel.application.perpetual_research_drivers import (
        PerpetualResearchAdmissionDrivers,
    )
    from packages.kernel.decision_hub_kernel.application.perpetual_saved_report import (
        PerpetualSavedReportQuery,
    )
    from packages.kernel.decision_hub_kernel.application.risk_research_admission import (
        RiskResearchAdmissionService,
    )
    from packages.kernel.decision_hub_kernel.persistence.db import RunRecord
    from packages.kernel.decision_hub_kernel.persistence.perpetual_paper_repository import (
        PerpetualPaperRepository,
    )
    from packages.kernel.decision_hub_kernel.persistence.risk_research_records import (
        RiskResearchRequestRecord,
    )
    from packages.kernel.decision_hub_kernel.ports.perpetual_close import minute_end
    from packages.runtime_adapters.dsh_runtime.perpetual_profile import _root_material_view
    from packages.runtime_adapters.perpetual_research_entry import (
        configured_perpetual_research_services,
    )
    from tests.decision_product import test_perpetual_native_graph as native_graph
    from tests.decision_product import test_perpetual_research_binding as fixture_binding
    from tests.decision_product.test_perpetual_advice_query import current
    from tests.decision_product.test_perpetual_event_agenda import schedule_sources
    from tests.decision_product.test_perpetual_forecast_experience import future_pages
    from tests.decision_product.test_perpetual_manual_execution import normal_queue_lifecycle
    from tests.decision_product.test_perpetual_research_schedule import body

    case = binding_case
    # Parse the new public consent boundary before the costly real settlement.
    # These DTO values grant no authority; the full path below saves/reopens the
    # actual signed consent and authenticates its real trigger/claim and ledger.
    paper_partition = {
        **case["settings"].partition.model_dump(mode="json"),
        "account_kind": "paper",
        "account_id": "manual-account",
    }
    if origin == "watch":
        from packages.kernel.decision_hub_kernel.ports.perpetual_plan_watch import (
            PerpetualPlanWatchCommandV2,
        )

        contract = PerpetualPlanWatchCommandV2.model_validate(
            dict(
                schema_version="perpetual-plan-watch-command.v2",
                continuation="follow_published_revisions",
                input_policy=POLICY,
                request_id="B-contract",
                partition=paper_partition,
                horizon=horizon,
                plan_id="B-contract-plan",
                expected_revision_ref={"ref": "B-contract-revision", "hash": "a" * 64},
                expected_state_version=0,
                expected_settings_revision={"ref": "B-contract-settings", "hash": "b" * 64},
                expected_settings_state_version=1,
                operation="watch",
                mode="remind_and_research",
            )
        )
    else:
        from packages.kernel.decision_hub_kernel.ports.perpetual_research_schedule import (
            parse_perpetual_research_schedule_command,
        )

        contract = parse_perpetual_research_schedule_command(
            {
                **body(case),
                "schema_version": "perpetual-research-schedule-command.v2",
                "partition": paper_partition,
                "input_policy": POLICY,
                "horizon": horizon,
            }
        )
    contract_schema = type(contract).model_json_schema()
    partition_schema = contract_schema["properties"]["partition"]
    if "$ref" in partition_schema:
        partition_schema = contract_schema["$defs"][
            partition_schema["$ref"].removeprefix("#/$defs/")
        ]
    assert partition_schema["additionalProperties"] is False
    assert partition_schema["properties"]["account_kind"]["const"] == "paper"
    assert partition_schema["properties"]["instrument_id"]["enum"] == [
        "okx:BTC-USDT-SWAP",
        "okx:ETH-USDT-SWAP",
    ]
    _, manual, clock, executor = normal_queue_lifecycle(case, monkeypatch, return_runtime=True)
    partition = case["settings"].partition
    with case["db"].session() as session:
        saved = DecisionSettingsService.current_in_session(session, OWNER, partition)
        before = PerpetualPaperRepository(session).current(OWNER, partition.account_id)
    assert before["funding_complete"] is False

    async def funding_response(transport, request):
        assert request.url.path in {"/api/v5/public/time", "/api/v5/public/funding-rate-history"}
        rows = (
            [{"ts": str(int(clock[0].timestamp() * 1000))}]
            if request.url.path.endswith("/time")
            else []
        )
        return httpx.Response(200, request=request, json={"code": "0", "data": rows})

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", funding_response)
    manual.funding_history = PerpetualFundingHistorySource(case["db"], case["archive"])
    clock[0] = minute_end(clock[0]) + timedelta(minutes=1, seconds=10)
    settled = executor.process_due()
    assert settled["errors"] == [] and settled["funding_reconciled"] == 1
    with case["db"].session() as session:
        ledger = PerpetualPaperRepository(session).current(OWNER, partition.account_id)
        assert ledger["funding_complete"] and ledger["revision"] != before["revision"]
        assert DecisionSettingsService.current_in_session(session, OWNER, partition) == saved
    print("B_PHASE", origin, "real_settlement_no_settings_save", flush=True)

    configured = configured_perpetual_research_services(
        case["db"], source=case["source"], now=lambda: clock[0]
    )
    driver = PerpetualResearchAdmissionDrivers(case["db"], configured.entry, now=lambda: clock[0])
    if origin == "watch":
        old_view = current(case, now=clock[0])
        revision = old_view["revision"]
        consent_command = dict(
            schema_version="perpetual-plan-watch-command.v2",
            continuation="follow_published_revisions",
            input_policy=POLICY,
            request_id="B-watch-enable",
            partition=partition.model_dump(mode="json"),
            horizon=horizon,
            plan_id=revision["plan_id"],
            expected_revision_ref={
                "ref": revision["revision_id"],
                "hash": revision["payload_hash"],
            },
            expected_state_version=0,
            expected_settings_revision={
                "ref": saved.revision.revision_id,
                "hash": saved.revision.payload_hash,
            },
            expected_settings_state_version=saved.state_version,
            operation="watch",
            mode="remind_and_research",
        )
        for invalid in ({"account_kind": "user_declared"}, {"instrument_id": "okx:SOL-USDT-SWAP"}):
            with pytest.raises(ValueError):
                driver.plan.save_continuous(
                    OWNER,
                    {**consent_command, "partition": {**consent_command["partition"], **invalid}},
                )
        consent = driver.plan.save_continuous(OWNER, consent_command)
        from packages.kernel.decision_hub_kernel.ports.perpetual_plan_watch import (
            PerpetualPlanWatchReceiptV2,
        )

        for invalid in ({"account_kind": "user_declared"}, {"instrument_id": "okx:SOL-USDT-SWAP"}):
            raw = consent.model_dump(mode="json")
            raw["command"]["partition"].update(invalid)
            with pytest.raises(ValueError):
                PerpetualPlanWatchReceiptV2.model_validate(raw)
        clock[0] = datetime.fromisoformat(revision["valid_until"])
        driver.discover()
        watch = driver.plan.current_continuous(
            OWNER, partition, "4h", consent_command["expected_settings_revision"]
        )
        clock[0] = datetime.fromisoformat(watch["progress"]["auto_review_slot"])
    else:
        legacy = driver.periodic.save(OWNER, {**body(case), "horizon": horizon})
        driver.periodic.save(OWNER, {**body(case, "pause", 1), "horizon": horizon})
        consent_command = {
            **body(case),
            "schema_version": "perpetual-research-schedule-command.v2",
            "request_id": "B-" + origin,
            "horizon": horizon,
            "input_policy": POLICY,
            "expected_state_version": 2,
        }
        for invalid in ({"account_kind": "user_declared"}, {"instrument_id": "okx:SOL-USDT-SWAP"}):
            with pytest.raises(ValueError):
                driver.periodic.save(
                    OWNER,
                    {**consent_command, "partition": {**consent_command["partition"], **invalid}},
                )
        consent = driver.periodic.save(OWNER, consent_command)
        from packages.kernel.decision_hub_kernel.ports.perpetual_research_schedule import (
            PerpetualResearchScheduleReceiptV2,
        )

        for invalid in ({"account_kind": "user_declared"}, {"instrument_id": "okx:SOL-USDT-SWAP"}):
            raw = consent.model_dump(mode="json")
            raw["command"]["partition"].update(invalid)
            with pytest.raises(ValueError):
                PerpetualResearchScheduleReceiptV2.model_validate(raw)
        if origin == "event":
            schedule_sources(case)
            clock[0] += timedelta(seconds=2)
        else:
            clock[0] = clock[0].replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    # Persisted restart preserves the exact original. Discovery is plan, periodic,
    # then event; a periodic request absorbs its same event motives.
    driver = PerpetualResearchAdmissionDrivers(case["db"], configured.entry, now=lambda: clock[0])
    driver.discover()
    driver.discover()
    if origin == "event":
        assert (
            PerpetualEventResearchService(
                case["db"], configured.entry, now=lambda: clock[0]
            ).discover()
            == 0
        )
    # The unchanged five real archived fixture pages are recorded at minute
    # end + 1..5 seconds. Observe them afterward, without rewriting their clocks.
    clock[0] = max(clock[0], minute_end(clock[0]) + timedelta(seconds=10))
    ids = future_pages(case, minute_end(clock[0]), prefix="B_" + origin + "_", count=1500)
    collected = CollectedPerpetualCloseWindow(ids, clock[0], clock[0])

    async def collect(*, inst_id):
        assert inst_id == "BTC-USDT-SWAP"
        return collected

    configured.entry.collect_window = collect
    engine = RiskResearchAdmissionService(
        case["db"], enabled=True, clock=lambda: clock[0], perpetual_driver=driver
    )
    claim = engine._claim()
    assert claim is not None
    prepared = asyncio.run(driver.prepare(claim[0], claim[2], engine.owner_id))
    assert prepared[1].schema_version == "perpetual-research-command.v3"
    from packages.kernel.decision_hub_kernel.application.perpetual_research_inputs import (
        issue_automatic_input_authority,
    )

    issued = prepared[2]
    for invalid_claim, invalid_proof in (
        ((claim[0], claim[2] + 1, engine.owner_id), issued.automatic_source),
        (
            issued.automatic_claim,
            {**issued.automatic_source, "trigger_ref": {"ref": "forged", "hash": "a" * 64}},
        ),
    ):
        with pytest.raises(ValueError):
            issue_automatic_input_authority(
                prepared[1],
                authenticated_owner=OWNER,
                planned_run_id=issued.planned_run_id,
                database=case["db"],
                prepared_at=issued.prepared_at,
                valid_until=issued.valid_until,
                automatic_claim=invalid_claim,
                automatic_source=invalid_proof,
            )
    pair = prepared[2].inputs.value
    witness = pair["account_binding"]["authority"]
    assert witness["origin"] == "automatic_consent"
    assert (
        witness["source_schema_version"]
        == {
            "periodic": "perpetual-research-periodic-trigger.v1",
            "event": "perpetual-research-event-trigger.v1",
            "watch": "perpetual-plan-review-trigger.v1",
        }[origin]
    )
    assert witness["command"]["horizon"] == horizon
    assert witness["consent_ref"] == consent.revision.model_dump(mode="json")
    assert witness["claim_request_id"] == claim[0] == witness["command"]["request_id"]
    assert witness["claim_generation"] == claim[2] and witness["claim_owner"] == engine.owner_id
    assert pair["admission"]["automatic_source"] == prepared[2].automatic_source
    assert "automatic_source" not in pair
    assert driver.admit(claim[0], claim[2], engine, prepared) is True
    assert asyncio.run(engine.process_due(limit=1)) == 0
    with case["db"].session() as session:
        request_row = session.get(RiskResearchRequestRecord, claim[0])
        run = session.get(RunRecord, request_row.run_id)
        assert request_row.lease_owner is None and run.admission_origin == "automatic"
        admission, _ = binding.read_perpetual_admission_identity_in_session(session, run.run_id)
        assert admission["effective_values"]["account_snapshot"]["original"] == ledger["revision"]
        receipt = configured.entry._receipt_in_session(session, OWNER, claim[0])
    case = {**case, "run_id": run.run_id, "event_id": run.event_id}
    at = clock[0]
    print("B_PHASE", origin, "automatic_v3_queued_exact_authority", flush=True)
    for module in (fixture_binding, native_graph, publication):
        monkeypatch.setattr(module, "AT", at)

    def request_setup(actual, factory, service):
        request = factory.build(actual["event_id"], actual["run_id"])
        context, materials = native_graph.prepare(actual, request)
        assert materials["input_binding"]["authority"] == witness
        return request, context, materials

    value = native_graph.build_perpetual_native_graph_case(
        case,
        tmp_path,
        monkeypatch,
        phased=True,
        request_setup=request_setup,
        packet_builder=publication.lawful_packet_for,
        before_graph=lambda actual: publication.authenticate_provider(actual, monkeypatch),
    )
    sizes = dict(
        material_utf8=len(binding.canonical(_root_material_view(value["materials"])).encode()),
        catalog_utf8=len(binding.canonical(value["materials"]["review_originals"]).encode()),
        complete_prompt_chars=len(value["execution"].prompt.prompt),
    )
    print("B_COMPLETE_INPUT_SIZES", origin, json.dumps(sizes), flush=True)
    record_property(origin + "_complete_input_sizes", json.dumps(sizes))
    assert sizes["material_utf8"] <= 256_000
    assert sizes["catalog_utf8"] <= 384_000
    assert sizes["complete_prompt_chars"] <= 262_144
    assert (
        value["execution"].prompt.model_validate_json(value["execution"].prompt.model_dump_json())
        == value["execution"].prompt
    )
    published, advice = publication.assert_actual_report_then_unique_perpetual_action(
        value, monkeypatch, horizon=horizon
    )
    assert advice["context"]["account_snapshot"]["original"] == ledger["revision"]
    assert published.response["status"] == "published"
    print("B_PHASE", origin, "Host_fullwriter_Graph_commit_action_advice_saved_report", flush=True)
    clock[0] = at + timedelta(seconds=41)
    from packages.kernel.decision_hub_kernel.application.perpetual_execution_protocol import (
        PerpetualPaperProtocolService,
    )
    from packages.kernel.decision_hub_kernel.ports.perpetual_manual_execution import (
        ManualPreparationCommand,
    )

    manual.protocols = PerpetualPaperProtocolService(
        case["db"], case["source"], clock=lambda: clock[0]
    )
    visible_body = dict(
        schema_version="perpetual-manual-prepare.v1",
        request_id="B-visible-" + origin,
        horizon=horizon,
        partition=advice["partition"],
        settings_revision=advice["settings_revision"],
        settings_state_version=advice["settings_state_version"],
        revision={
            "ref": advice["revision"]["revision_id"],
            "hash": advice["revision"]["payload_hash"],
        },
    )
    if horizon == "4h":
        visible = ManualPreparationCommand.model_validate(visible_body)
        assert (
            manual.prepare_context(OWNER, visible).response["context"]["account_revision"]
            == ledger["revision"]
        )
    else:
        with pytest.raises(ValueError):
            ManualPreparationCommand.model_validate(visible_body)
        print("B_PHASE", origin, "72h_manual_unsupported_existing_4h_contract", flush=True)
    query = PerpetualSavedReportQuery(case["db"], now=lambda: clock[0])
    report = query.read(OWNER, run.run_id)
    paused = {
        **consent_command,
        "request_id": "B-pause",
        "expected_state_version": consent.state_version,
        "operation": "pause",
    }
    if origin == "watch":
        PerpetualPlanFollowupService(
            case["db"], configured.entry, now=lambda: clock[0]
        ).save_continuous(OWNER, paused)
    else:
        driver.periodic.save(OWNER, paused)
        assert driver.periodic.receipt(OWNER, legacy.request_id) == legacy
    assert configured.entry.receipt(OWNER, claim[0]) == receipt
    reopened = query.read(OWNER, run.run_id)
    assert reopened["source_json"] == report["source_json"]
    assert reopened["artifact_json"] == report["artifact_json"]
    print("B_PHASE", origin, "paused_consent_historical_originals_unchanged", flush=True)

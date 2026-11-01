"""C direct chain: actual settlement and full lawful native fixture, no model/service."""

import json
from datetime import timedelta

import httpx
import pytest

from tests.decision_product import test_perpetual_action_publication as publication
from tests.decision_product.test_okx_perpetual_original import (
    persisted_original as persisted_original,
)
from tests.decision_product.test_perpetual_research_binding import (
    OWNER,
)
from tests.decision_product.test_perpetual_research_binding import (
    binding_case as binding_case,
)


def test_settled_v3_full_material_prompt_writer_advice_manual_and_frozen_history(
    binding_case, monkeypatch, tmp_path, record_property
):
    from packages.kernel.decision_hub_kernel.application import (
        perpetual_research_binding as binding,
    )
    from packages.kernel.decision_hub_kernel.application.decision_settings import (
        DecisionSettingsService,
    )
    from packages.kernel.decision_hub_kernel.application.perpetual_close_source import (
        CollectedPerpetualCloseWindow,
    )
    from packages.kernel.decision_hub_kernel.application.perpetual_funding_history_source import (
        PerpetualFundingHistorySource,
    )
    from packages.kernel.decision_hub_kernel.application.perpetual_saved_report import (
        PerpetualSavedReportQuery,
    )
    from packages.kernel.decision_hub_kernel.persistence.perpetual_paper_repository import (
        PerpetualPaperRepository,
    )
    from packages.kernel.decision_hub_kernel.ports.perpetual_close import minute_end
    from packages.runtime_adapters.dsh_runtime.perpetual_profile import _root_material_view
    from tests.decision_product import test_perpetual_native_graph as native_graph
    from tests.decision_product import test_perpetual_research_binding as fixture_binding
    from tests.decision_product.test_perpetual_forecast_experience import future_pages
    from tests.decision_product.test_perpetual_manual_execution import normal_queue_lifecycle
    from tests.decision_product.test_perpetual_research_entry import BASE, client_for, command

    case = binding_case
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

    at = clock[0] + timedelta(seconds=2)
    clock[0] = at
    ids = future_pages(case, minute_end(at), prefix="C_settled_closes_", count=1500)
    collected = CollectedPerpetualCloseWindow(ids, at, at)
    client, entry, _ = client_for(case, clock=lambda: clock[0])

    async def collect(*, inst_id):
        assert inst_id == "BTC-USDT-SWAP"
        return collected

    entry.collect_window = collect
    body = {
        **command(case),
        "request_id": "C-current-paper",
        "schema_version": "perpetual-research-command.v3",
        "input_policy": "saved_preferences_current_paper_ledger.v1",
    }
    response = client.post(
        BASE + "/research", json=body, headers={"Idempotency-Key": body["request_id"]}
    )
    assert response.status_code == 200, response.text
    receipt = response.json()
    case = {**case, "run_id": receipt["run_id"], "event_id": receipt["event_id"]}
    # These existing native-fixture modules share one clock; all production
    # source, catalog, permission and original validators remain installed.
    for module in (fixture_binding, native_graph, publication):
        monkeypatch.setattr(module, "AT", at)

    def request_setup(actual, factory, service):
        request = factory.build(actual["event_id"], actual["run_id"])
        with actual["db"].session() as session:
            materials = binding.read_perpetual_research_materials_in_session(session, request)
        rows = [
            row
            for row in materials["review_originals"]["originals"]
            if row["source_kind"] == "server_material" and row["role"] == "input_binding"
        ]
        assert len(rows) == 1, "current-paper binding must be a complete reviewer original"
        assert json.loads(rows[0]["body_json"]) == materials["input_binding"]
        assert rows[0]["original_ref"] == receipt["input_binding_ref"]
        assert materials["comparison_method"]["source_bundle"]["files"]
        context, materials = native_graph.prepare(actual, request)
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
    material = _root_material_view(value["materials"])
    sizes = dict(
        material_utf8=len(binding.canonical(material).encode()),
        catalog_utf8=len(binding.canonical(value["materials"]["review_originals"]).encode()),
        complete_prompt_chars=len(value["execution"].prompt.prompt),
        complete_prompt_utf8=len(value["execution"].prompt.prompt.encode()),
    )
    print("C_COMPLETE_INPUT_SIZES", json.dumps(sizes), flush=True)
    record_property("complete_input_sizes", json.dumps(sizes))
    assert sizes["material_utf8"] <= 256_000
    assert sizes["catalog_utf8"] <= 384_000
    assert sizes["complete_prompt_chars"] <= 262_144
    assert (
        value["execution"].prompt.model_validate_json(value["execution"].prompt.model_dump_json())
        == value["execution"].prompt
    )
    published, advice = publication.assert_actual_report_then_unique_perpetual_action(
        value, monkeypatch
    )
    assert advice["context"]["account_snapshot"]["original"] == ledger["revision"]
    assert advice["context"][
        "account_snapshot"
    ] != saved.revision.values.account_snapshot.model_dump(mode="json")
    clock[0] = at + timedelta(seconds=41)
    from packages.kernel.decision_hub_kernel.application.perpetual_execution_protocol import (
        PerpetualPaperProtocolService,
    )
    from packages.kernel.decision_hub_kernel.ports.perpetual_manual_execution import (
        ManualPreparationCommand,
    )

    # The previous lifecycle's protocol service intentionally captured its old
    # registration time. A new manual preparation uses the current real clock.
    manual.protocols = PerpetualPaperProtocolService(
        case["db"], case["source"], clock=lambda: clock[0]
    )
    visible = ManualPreparationCommand.model_validate(
        dict(
            schema_version="perpetual-manual-prepare.v1",
            request_id="C-visible",
            horizon="4h",
            partition=advice["partition"],
            settings_revision=advice["settings_revision"],
            settings_state_version=advice["settings_state_version"],
            revision={
                "ref": advice["revision"]["revision_id"],
                "hash": advice["revision"]["payload_hash"],
            },
        )
    )
    prepared = manual.prepare_context(OWNER, visible)
    assert prepared.response["status"] == "prepared"
    assert prepared.response["context"]["account_revision"] == ledger["revision"]
    report_query = PerpetualSavedReportQuery(case["db"], now=lambda: clock[0])
    report = report_query.read(OWNER, receipt["run_id"])
    # The earlier authentic ledger head is a negative substitution: frozen
    # reads must retain this Run's specified original, active use must reject.
    from packages.kernel.decision_hub_kernel.persistence.original_preparation import begin_immediate
    from packages.kernel.decision_hub_kernel.persistence.perpetual_paper_records import (
        PerpetualPaperHeadRecord,
    )
    from packages.kernel.decision_hub_kernel.persistence.perpetual_paper_repository import (
        account_key,
    )

    with case["db"].session() as session:
        begin_immediate(session)
        head = session.get(PerpetualPaperHeadRecord, account_key(OWNER, partition.account_id))
        head.current_revision_id, head.state_version = (
            before["revision"]["ref"],
            before["state_version"],
        )
    with case["db"].session() as session:
        with pytest.raises(ValueError, match="account_original_changed"):
            binding.read_perpetual_research_binding_in_session(session, receipt["run_id"])
    reopened = report_query.read(OWNER, receipt["run_id"])
    assert reopened["source_json"] == report["source_json"]
    assert reopened["artifact_json"] == report["artifact_json"]
    assert client.get(BASE + "/research/commands/" + body["request_id"]).json() == receipt
    assert published.response["status"] == "published"

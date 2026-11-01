"""Normal tick + genuine provider originals, authored venue/model fixture only."""

import hashlib
import importlib.util
import json
import os
from datetime import datetime, timedelta
from uuid import uuid4

import httpx
import pytest

from apps.hub_worker.composition import build_research_admission_tick
from packages.kernel.decision_hub_kernel.application.perpetual_forecast_runtime import (
    PerpetualForecastRuntime,
)
from packages.kernel.decision_hub_kernel.persistence.db import RunRoleOutputRecord, utcnow
from tests.decision_product import test_perpetual_selected_dispatch_identity as dispatch_fixture
from tests.decision_product.test_okx_perpetual_original import (
    persisted_original as persisted_original,
)
from tests.decision_product.test_perpetual_forecast_experience import future_pages
from tests.decision_product.test_perpetual_research_binding import binding_case as binding_case


def test_normal_perpetual_experience_tick_is_installed():
    assert importlib.util.find_spec(
        "packages.kernel.decision_hub_kernel.application.perpetual_forecast_runtime"
    ), "normal worker cannot mature a saved perpetual forecast or observe actual experience use"


@pytest.mark.asyncio
async def test_normal_due_pending_retry_next_queue_provider_input_and_causal_use(
    binding_case, tmp_path, monkeypatch
):
    from packages.contracts_py.decision_hub_contracts import ObservationCreate
    from packages.contracts_py.decision_hub_contracts.generated.dsh_host_bridge import (
        DshModelDispatchBinding,
    )
    from packages.contracts_py.decision_hub_contracts.generated.provider_input import (
        ResearchProviderInputSubmit,
        ResearchProviderToolEmissionSubmit,
    )
    from packages.contracts_py.decision_hub_contracts.models import SourceType
    from packages.kernel.decision_hub_kernel.application import provider_input_receipts
    from packages.kernel.decision_hub_kernel.application.admission import AdmissionService
    from packages.kernel.decision_hub_kernel.application.model_dispatch import ModelDispatchService
    from packages.kernel.decision_hub_kernel.application.perpetual_research_binding import (
        PerpetualResearchBindingService,
        read_perpetual_research_materials_in_session,
    )
    from packages.kernel.decision_hub_kernel.application.run import RunService
    from packages.kernel.decision_hub_kernel.persistence import db, original_preparation
    from packages.kernel.decision_hub_kernel.persistence.db import RunRecord
    from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash
    from packages.kernel.decision_hub_kernel.ports.perpetual_close import millis, minute_end
    from tests.contracts.test_d2_candidate_contract import body_ref
    from tests.decision_product import test_perpetual_action_publication as packets
    from tests.decision_product import test_perpetual_native_graph as native_fixture
    from tests.decision_product.test_perpetual_advice_query import persisted_publication
    from tests.decision_product.test_perpetual_research_binding import OWNER, factory_for

    case = binding_case
    builder = packets.packet_for

    def pure_catalog(request, materials):
        return builder(
            request,
            {
                **materials,
                "review_originals": {
                    "originals": [
                        {
                            "source_kind": "server_material",
                            "role": "close_window",
                            "source_ref": {
                                "ref": "synthetic-pure-close-window",
                                "hash": canonical_hash(materials["close_window"]),
                            },
                        }
                    ]
                },
            },
        )

    monkeypatch.setattr(packets, "packet_for", pure_catalog)
    saved = persisted_publication(case)
    monkeypatch.setattr(packets, "packet_for", builder)
    target = json.loads(
        next(item.body_json for item in saved.originals if item.role == "final_target")
    )
    end = datetime.fromisoformat(target["window_end"].replace("Z", "+00:00"))
    holder = {"at": utcnow()}
    assert holder["at"] > end
    calls = []

    def http_fixture(request):
        calls.append(request)
        assert request.url.params["instId"] == "BTC-USDT-SWAP"
        assert request.url.params["after"] == str(millis(end))
        if len(calls) == 1:
            return httpx.Response(503, json={"code": "500", "data": []})
        rows = []
        for index in range(241):
            close = end - timedelta(minutes=index)
            value = "100" if index == 240 else "110"
            rows.append(
                [str(millis(close) - 60000), value, value, value, value, "7", "0.07", "7", "1"]
            )
        return httpx.Response(200, json={"code": "0", "data": rows})

    runtime = PerpetualForecastRuntime(
        case["db"],
        case["source"],
        clock=lambda: holder["at"],
        http_transport=httpx.MockTransport(http_fixture),
    )
    from packages.kernel.decision_hub_kernel.application.risk_research_admission import (
        RiskResearchAdmissionService,
    )

    normal_tick = build_research_admission_tick(
        perpetual_forecasts=runtime,
        periodic_admission=RiskResearchAdmissionService(case["db"], enabled=False),
        previous_research_tick=None,
    )
    assert await normal_tick() == 1
    with case["db"].session() as session:
        assert (
            session.query(RunRoleOutputRecord)
            .filter_by(role="perpetual_forecast_experience_v1")
            .count()
            == 0
        )
        pending = [
            json.loads(row.payload_json)
            for row in session.query(RunRoleOutputRecord).filter_by(
                schema_version="perpetual-forecast-due-observation.v1"
            )
        ]
        assert any(row["status"] == "pending" and row["label_created"] is False for row in pending)
    assert await normal_tick() == 0 and len(calls) == 1
    holder["at"] += timedelta(seconds=301)
    assert await normal_tick() == 1 and len(calls) == 2
    assert await normal_tick() == 0 and len(calls) == 2
    with case["db"].session() as session:
        experience_row = (
            session.query(RunRoleOutputRecord)
            .filter_by(role="perpetual_forecast_experience_v1")
            .one()
        )
        experience_ref = {"ref": experience_row.output_id, "hash": experience_row.payload_hash}

    # A next authorized normal Run queue entry; the label tick itself creates no research.
    at = minute_end(holder["at"]) + timedelta(minutes=1, seconds=10)
    holder["at"] = at
    history = future_pages(case, minute_end(at), prefix="normal_next_history_", count=1500)
    event_id, _, admitted = AdmissionService(case["db"], clock=lambda: at).admit(
        ObservationCreate(
            source_id="explicit-next-experience-research",
            source_type=SourceType.transcript,
            source_url="https://example.com/explicit-next-experience-research",
            text="Authorized next central-bank research.",
            observed_at=at,
            published_at=at,
            language="en",
            event_hint="central_bank_speech",
        )
    )
    assert admitted
    run_id, queued = RunService(case["db"], clock=lambda: at).create(
        event_id, strategy_version="research.v1"
    )
    assert queued
    with case["db"].session() as session:
        run = session.get(RunRecord, run_id)
        assert run.status == "admitted"
        run.study_id = session.get(RunRecord, case["run_id"]).study_id
    binding = PerpetualResearchBindingService(case["db"], case["source"], now=lambda: at)
    binding.freeze_admission(
        run_id=run_id,
        owner_id=OWNER,
        partition=case["settings"].partition,
        capture_ids=history,
        as_of=at,
        valid_until=at + timedelta(hours=1),
    )
    next_case = {**case, "run_id": run_id, "event_id": event_id}
    factory, _ = factory_for(next_case, tmp_path / "normal-next-pack")
    factory.clock, factory.perpetual_binding_service = lambda: at, binding
    monkeypatch.setattr(dispatch_fixture, "CLOCK", at)
    monkeypatch.setattr(db, "datetime", dispatch_fixture._FixedDateTime)
    request = factory.build(event_id, run_id)
    assert any(item.evidence_id == experience_ref["ref"] for item in request.input_evidence)
    # Real normal selector/PreparedExecution up to dispatch only. The host stops
    # at submit; no Graph execution or model invocation is needed.
    monkeypatch.setattr(dispatch_fixture, "CLOCK", at + timedelta(seconds=20))
    dispatch_fixture._freeze_clock(monkeypatch)
    assert RunService(case["db"], clock=lambda: dispatch_fixture.CLOCK).claim_for_execution(
        run_id, worker_id="binding-test", lease_seconds=300
    )
    host = dispatch_fixture._BoundaryHost()
    web = dispatch_fixture.DshWebResearchRuntime(
        dispatch_fixture.DshSessionLinkService(case["db"]),
        host,
        poll_interval_seconds=0.001,
        readiness_grace_seconds=0.1,
    )
    from packages.kernel.decision_hub_kernel.application import (
        research_context_selection,
        research_context_selection_v4,
    )
    from packages.kernel.decision_hub_kernel.application.research_graph_dispatch import (
        is_quality_graph_in_session,
    )

    with case["db"].session() as session:
        selection_type = (
            research_context_selection_v4.ContextSelectionService
            if is_quality_graph_in_session(session, run_id)
            else research_context_selection.ContextSelectionService
        )
    actual_selection = selection_type(case["db"], clock=lambda: dispatch_fixture.CLOCK).select(
        run_id=run_id,
        graph_round=1,
        attempt_basis="initial_round",
        previous_graph_ref=None,
        expected_lease_owner="binding-test",
    )
    with pytest.raises(dispatch_fixture._DispatchBoundaryReached):
        await web.execute_selected_observed(
            actual_selection.request,
            actual_selection.reference,
            expected_lease_owner="binding-test",
        )
    assert len(host.submit_calls) == 1
    with case["db"].session() as session:
        execution = dispatch_fixture.read_prepared_execution_in_session(session, run_id)
        selected = execution.execution.request
        assert selected.model_dump(mode="json") != request.model_dump(mode="json")
        assert any(item.evidence_id == experience_ref["ref"] for item in selected.input_evidence)
        assert experience_ref["ref"] in selected.context_manifest.effective_inputs
        assert session.get(RunRecord, run_id).artifact_id is None
        materials = read_perpetual_research_materials_in_session(
            session,
            selected,
            selection_reference=dispatch_fixture._selection_reference(execution.execution),
            expected_lease_owner="binding-test",
        )
    request = selected
    at = dispatch_fixture.CLOCK
    monkeypatch.setattr(native_fixture, "AT", at)
    origin = next(
        row
        for row in materials["review_originals"]["originals"]
        if row["source_kind"] == "frozen_input" and row["original_ref"] == experience_ref
    )
    packet = packets.lawful_packet_for(request, materials)
    packet["synthesis"]["analysis"]["main_chain"][0]["evidence_refs"] = [origin["source_ref"]]
    packet["synthesis_ref"] = body_ref("synth", packet["synthesis"])

    def now():
        return at + timedelta(seconds=5)

    monkeypatch.setattr(original_preparation, "utcnow", now)
    monkeypatch.setattr(provider_input_receipts, "utcnow", now)
    params = dict(
        provider="synthetic-experience-backend",
        model="authored-no-model",
        reasoning_effort=None,
        temperature=None,
        max_tokens=None,
        stop_sequences_hash=None,
    )
    method = execution.method
    ModelDispatchService(case["db"], clock=lambda: at + timedelta(milliseconds=500)).bind(
        DshModelDispatchBinding(
            schema_version="dsh-model-dispatch-binding.v1",
            run_id=run_id,
            dsh_session_id=execution.link.dsh_session_id,
            execution_session_id=None,
            parent_session_id=None,
            request_hash=method.request_hash,
            generation=1,
            plugin_build_hash=method.definition.upstream_identity.plugin_build_hash,
            purpose="conversation",
            **params,
        )
    )
    provider_body = json.dumps(
        dict(
            system="Synthetic engineering input, no financial authority.",
            messages=[dict(role="user", content=execution.prompt.prompt)],
            tools=[dict(name="decision_hub_synthesis_submit")],
        ),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    shared = dict(
        run_id=run_id,
        generation=1,
        root_session_id=execution.link.dsh_session_id,
        execution_session_id=execution.link.dsh_session_id,
        dispatch_id="actual-experience-dispatch",
        request_hash=method.request_hash,
        plugin_build_hash=method.definition.upstream_identity.plugin_build_hash,
    )
    recorder = provider_input_receipts.ProviderInputReceiptService(
        case["db"], clock=lambda: at + timedelta(seconds=1)
    )
    recorded = recorder.record_input(
        ResearchProviderInputSubmit(
            schema_version="research-provider-input-submit.v1",
            **shared,
            **params,
            parent_session_id=None,
            input_json=provider_body,
            input_hash=hashlib.sha256(provider_body.encode()).hexdigest(),
            previous_input_ref=None,
            host_scope=dict(
                host_instance_id=str(uuid4()),
                process_id=os.getpid(),
                started_at=at,
                hub_base_url="http://test",
                workspace_id="default",
                workspace_cwd_hash="a" * 64,
            ),
        )
    )
    for index in range(2):
        if index:
            packet["synthesis"]["analysis"]["main_chain"][0]["evidence_refs"] = []
            packet["synthesis_ref"] = body_ref("synth", packet["synthesis"])
        arguments = {"candidate": packet["synthesis"], "review_request": packet}
        raw = json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
        provider_input_receipts.ProviderInputReceiptService(
            case["db"], clock=lambda index=index: at + timedelta(seconds=2 + index)
        ).record_emission(
            ResearchProviderToolEmissionSubmit(
                schema_version="research-provider-tool-emission-submit.v1",
                **shared,
                input_ref=recorded.reference.model_dump(
                    mode="json", include={"output_id", "payload_hash"}
                ),
                call_id=f"actual-causal-reference-{index}",
                tool_name="decision_hub_synthesis_submit",
                raw_arguments_json=raw,
                raw_arguments_hash=hashlib.sha256(raw.encode()).hexdigest(),
                arguments_hash=hashlib.sha256(
                    provider_input_receipts.tool_arguments_json(arguments).encode()
                ).hexdigest(),
                block_index=index,
            )
        )
    holder["at"] = now()
    assert await normal_tick() == 1
    assert await normal_tick() == 1
    assert await normal_tick() == 0
    with case["db"].session() as session:
        uses = [
            json.loads(row.payload_json)
            for row in session.query(RunRoleOutputRecord).filter_by(
                schema_version="perpetual-experience-provider-use.v1"
            )
        ]
    assert len(uses) == 2
    referenced = next(
        row for row in uses if row.get("uses") and row["uses"][0]["consumed_as_historical_data"]
    )
    not_referenced = next(
        row for row in uses if row.get("uses") and not row["uses"][0]["consumed_as_historical_data"]
    )
    assert referenced["uses"][0]["experience_ref"] == experience_ref
    assert referenced["prompt_pointer"] == "/messages/0/content"
    assert referenced["uses"][0]["causal_links"][0]["pointer"] == "/analysis/main_chain/0"
    assert not_referenced["uses"][0]["status"] == "not_referenced"
    assert all(
        row["skill_consumed"] is False and row["financial_qualified"] is False for row in uses
    )
    assert len(calls) == 2

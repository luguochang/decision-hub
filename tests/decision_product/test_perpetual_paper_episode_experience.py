"""Real temporary-DB paper outcome -> historical input -> raw Provider use gate."""

import asyncio
import hashlib
import json
import os
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select

from tests.decision_product.test_okx_perpetual_original import (
    persisted_original as persisted_original,
)
from tests.decision_product.test_perpetual_research_binding import binding_case as binding_case


def test_fees_turn_positive_gross_into_loss_without_probability_label():
    from packages.kernel.decision_hub_kernel.ports.perpetual_paper_experience import cashflow_label

    assert cashflow_label("0.08", "0.10", "-0.02", "0") == ("-0.02", "loss")
    with pytest.raises(ValueError, match="identity"):
        cashflow_label("0.08", "0.10", "0.08", "0")
    with pytest.raises(ValueError, match="nonfinite"):
        cashflow_label("NaN", "0.1", "0", "0")


def test_settled_paper_original_next_selector_and_actual_provider_use(
    binding_case, tmp_path, monkeypatch
):
    from packages.kernel.decision_hub_kernel.application.perpetual_funding_history_source import (
        PerpetualFundingHistorySource,
    )
    from packages.kernel.decision_hub_kernel.application.perpetual_paper_experience import (
        PerpetualPaperExperienceService,
        paper_experience_context_in_session,
    )
    from packages.kernel.decision_hub_kernel.persistence.action_records import ActionOutcomeRecord
    from packages.kernel.decision_hub_kernel.persistence.db import RunRoleOutputRecord
    from packages.kernel.decision_hub_kernel.persistence.perpetual_execution_adapter import (
        PerpetualExecutionAdapter,
    )
    from tests.decision_product.test_perpetual_manual_execution import normal_queue_lifecycle
    from tests.decision_product.test_perpetual_research_binding import OWNER

    case = binding_case
    wire, svc, clock, executor = normal_queue_lifecycle(case, monkeypatch, return_runtime=True)
    funding = PerpetualFundingHistorySource(case["db"], case["archive"])
    service = PerpetualPaperExperienceService(case["db"], funding, clock=lambda: clock[0])
    account_key = wire["execution"]["intent"]["account_key"]
    with case["db"].session() as session:
        episode, episode_ref = PerpetualExecutionAdapter(session).episode(account_key)
        outcome = session.get(ActionOutcomeRecord, episode["outcome_id"])
        outcome_ref = dict(ref=outcome.outcome_id, hash=outcome.payload_hash)
    with pytest.raises(ValueError, match="settled_closed"):
        service.prepare(episode_ref=episode_ref, outcome_ref=outcome_ref)
    assert service.process_due() == ()
    clock[0] += timedelta(seconds=2)

    async def funding_response(transport, request):
        if request.url.path == "/api/v5/public/time":
            rows = [{"ts": str(int(clock[0].timestamp() * 1000))}]
        else:
            assert request.url.path == "/api/v5/public/funding-rate-history"
            rows = []
        return httpx.Response(200, request=request, json={"code": "0", "data": rows})

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", funding_response)
    svc.funding_history = funding
    result = executor.process_due()
    assert result["errors"] == [], result
    assert result["funding_reconciled"] == 1
    with case["db"].session() as session:
        episode, episode_ref = PerpetualExecutionAdapter(session).episode(account_key)
        outcome = session.get(ActionOutcomeRecord, episode["outcome_id"])
        outcome_ref = dict(ref=outcome.outcome_id, hash=outcome.payload_hash)
        rows = session.scalars(
            select(RunRoleOutputRecord).where(
                RunRoleOutputRecord.schema_version == "perpetual-paper-episode-experience.v1"
            )
        ).all()
        assert len(rows) == 1  # normal postcommit, not a test-only record path
        experience_ref = dict(ref=rows[0].output_id, hash=rows[0].payload_hash)
        original = service.read_in_session(session, experience_ref, cutoff_at=clock[0])
        assert original.body["reference_horizon"] == "4h"
        assert Decimal(original.body["actual_holding_seconds"]) < 14400
        assert original.body["kind"] == "paper_episode_net_cashflow"
        assert original.body["funding_cash_usdt"] == "0"
        from copy import deepcopy

        altered = deepcopy(original.body["funding_coverage"])
        altered["provider_lower"] = (
            datetime.fromisoformat(altered["provider_lower"]) - timedelta(seconds=1)
        ).isoformat()
        with pytest.raises(ValueError, match="saved_bounds"):
            funding.read_coverage_body_in_session(session, body=altered, as_of=clock[0])
        assert original.body["probability_scored"] is False
        assert (
            original.body["financial_qualified"] is False
            and original.body["skill_consumed"] is False
        )
        assert "mark_risk_path_unavailable" in original.body["gaps"]
        scope = dict(
            owner_id=OWNER,
            account_kind="paper",
            account_id="manual-account",
            instrument_id=wire["command"]["partition"]["instrument_id"],
            profile_ref=original.body["profile_ref"],
            horizon="4h",
            cutoff_at=clock[0],
        )
        assert len(paper_experience_context_in_session(session, **scope)) == 1
        for field, value in [
            ("owner_id", "foreign"),
            ("account_id", "foreign"),
            ("instrument_id", "okx:ETH-USDT-SWAP"),
            ("horizon", "24h"),
            ("account_kind", "user_declared"),
            ("profile_ref", dict(ref="foreign", hash="a" * 64)),
            ("cutoff_at", clock[0] - timedelta(days=1)),
        ]:
            assert paper_experience_context_in_session(session, **{**scope, field: value}) == ()
        with pytest.raises(ValueError, match="invalid_or_future"):
            service.read_in_session(session, experience_ref, cutoff_at=clock[0] - timedelta(days=1))
        with pytest.raises(ValueError, match="invalid_or_future"):
            service.read_in_session(
                session, {**experience_ref, "hash": "a" * 64}, cutoff_at=clock[0]
            )
    clock[0] += timedelta(seconds=1)
    assert service.record(episode_ref=episode_ref, outcome_ref=outcome_ref) == original
    assert service.process_due() == ()
    asyncio.run(_prove_next_provider(case, tmp_path, monkeypatch, clock[0], experience_ref))
    # Archived provider bytes are checked again when an experience is read.
    capture = original.body["funding_coverage"]["pages"][0]["capture"]
    raw = case["archive"].root / capture["body_hash"][:2] / capture["body_hash"]
    raw.write_bytes(raw.read_bytes() + b" ")
    with (
        case["db"].session() as session,
        pytest.raises(ValueError, match="archive_corrupt|hash|raw"),
    ):
        service.read_in_session(session, original.ref, cutoff_at=clock[0])


async def _prove_next_provider(case, tmp_path, monkeypatch, paper_at, experience_ref):
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
    from packages.kernel.decision_hub_kernel.persistence.db import RunRecord, RunRoleOutputRecord
    from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash
    from packages.kernel.decision_hub_kernel.ports.perpetual_close import minute_end
    from tests.contracts.test_d2_candidate_contract import body_ref
    from tests.decision_product import test_perpetual_action_publication as packets
    from tests.decision_product import test_perpetual_native_graph as native_fixture
    from tests.decision_product import test_perpetual_selected_dispatch_identity as dispatch_fixture
    from tests.decision_product.test_perpetual_forecast_experience import future_pages
    from tests.decision_product.test_perpetual_research_binding import OWNER, factory_for

    # A next authorized normal Run queue entry; the label tick itself creates no research.
    at = minute_end(paper_at) + timedelta(minutes=1, seconds=10)
    # Account settlement legitimately changed the authoritative snapshot. Save
    # that fresh original through the existing command path before a next Run.
    from copy import deepcopy

    from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
        DecisionSettingsCommandV1,
    )
    from packages.kernel.decision_hub_kernel.application.decision_settings import (
        DecisionSettingsService,
    )
    from packages.kernel.decision_hub_kernel.application.decision_settings_binding import (
        DecisionSettingsBindingReader,
    )
    from packages.kernel.decision_hub_kernel.application.perpetual_paper import (
        PerpetualPaperService,
    )

    paper = PerpetualPaperService(case["db"], case["source"], now=lambda: at)
    with case["db"].session() as session:
        current_settings = DecisionSettingsService.current_in_session(
            session, OWNER, case["settings"].partition
        )
        account, position = paper.account_reader(session, OWNER, case["settings"].partition)
    body = deepcopy(case["settings"].model_dump(mode="json"))
    body.update(
        request_id="fresh-post-settlement-settings",
        expected_state_version=current_settings.state_version,
    )
    body["values"].update(
        account_snapshot=account.model_dump(mode="json"),
        position_context=position.model_dump(mode="json"),
    )
    policy = body["values"]["utility_policy"]
    policy.update(account_ref=account.original.model_dump(mode="json"), position_scenario="flat")
    policy["hash"] = canonical_hash({key: value for key, value in policy.items() if key != "hash"})
    fresh_settings = DecisionSettingsCommandV1.model_validate(body)
    DecisionSettingsService(
        case["db"],
        now=lambda: at,
        binding_reader=DecisionSettingsBindingReader(
            case["source"], now=lambda: at, paper_account_reader=paper.account_reader
        ),
    ).save(OWNER, fresh_settings)
    case["settings"] = fresh_settings
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
    recorded_emissions = []
    for index in range(2):
        if index:
            packet["synthesis"]["analysis"]["main_chain"][0]["evidence_refs"] = []
            packet["synthesis_ref"] = body_ref("synth", packet["synthesis"])
        from tests.decision_product.perpetual_draft_fixture import draft_from_packet

        arguments = {
            "action": "prepare",
            "draft": draft_from_packet(packet, materials["review_originals"]),
        }
        raw = json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
        emitted = provider_input_receipts.ProviderInputReceiptService(
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
        recorded_emissions.append(emitted)
    from packages.kernel.decision_hub_kernel.application.perpetual_forecast_runtime import (
        PerpetualForecastRuntime,
    )

    runtime = PerpetualForecastRuntime(
        case["db"], case["source"], clock=now, collection_enabled=False
    )
    # Normal worker discovery, not only a direct derive/record call. Exactly one
    # authoring emission is processed per tick, and matching v2 use prevents retry.
    assert runtime.process_uses() == 1
    assert runtime.process_uses() == 1
    assert runtime.process_uses() == 0
    with case["db"].session() as session:
        by_emission = {
            json.loads(row.payload_json)["provider_emission_ref"]["output_id"]: json.loads(
                row.payload_json
            )
            for row in session.query(RunRoleOutputRecord).filter_by(
                schema_version="perpetual-experience-provider-use.v2"
            )
        }
    uses = [by_emission[emitted.reference.output_id] for emitted in recorded_emissions]
    assert len(uses) == 2
    assert uses[0]["schema_version"] == "perpetual-experience-provider-use.v2"
    assert uses[0]["uses"][0]["consumed_as_historical_data"] is True
    assert uses[1]["uses"][0]["consumed_as_historical_data"] is False
    assert uses[0]["uses"][0]["experience_kind"] == "paper_episode_net_cashflow"
    assert uses[0]["prompt_pointer"] == "/messages/0/content"
    assert uses[0]["uses"][0]["causal_links"][0]["pointer"] == "/analysis/main_chain/0"
    assert all(
        use["skill_consumed"] is False and use["financial_qualified"] is False for use in uses
    )


@pytest.mark.asyncio
async def test_existing_forecast_v1_original_and_provider_use_remain_readable(
    binding_case, tmp_path, monkeypatch
):
    from tests.decision_product.test_perpetual_forecast_runtime import (
        test_normal_due_pending_retry_next_queue_provider_input_and_causal_use,
    )

    await test_normal_due_pending_retry_next_queue_provider_input_and_causal_use(
        binding_case, tmp_path, monkeypatch
    )

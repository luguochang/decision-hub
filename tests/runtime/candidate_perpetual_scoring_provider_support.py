"""Original Main dispatch fixture adapted only to v2 sample/v3 use; authored backend."""

import hashlib
import json
import os
from datetime import timedelta
from typing import Any, cast
from uuid import uuid4

import pytest
from candidate_perpetual_forecast_support import load
from pydantic import HttpUrl


async def assert_original_next_task_and_provider_use(
    case: Any,
    tmp_path: Any,
    monkeypatch: Any,
    holder: Any,
    normal_tick: Any,
    experience_ref: Any,
    calls: Any,
):
    from tests.decision_product import test_perpetual_selected_dispatch_identity as dispatch_fixture
    from tests.decision_product.test_perpetual_forecast_experience import future_pages

    load("packages/kernel/decision_hub_kernel/application/perpetual_forecast_scoring_inputs.py")
    load("apps/hub_worker/research.py")
    from packages.contracts_py.decision_hub_contracts import ObservationCreate
    from packages.contracts_py.decision_hub_contracts.generated.dsh_host_bridge import (
        DshModelDispatchBinding,
    )
    from packages.contracts_py.decision_hub_contracts.generated.provider_input import (
        ResearchProviderHostScope,
        ResearchProviderInputRef,
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
    from packages.kernel.decision_hub_kernel.ports.perpetual_close import minute_end
    from tests.contracts.test_d2_candidate_contract import body_ref
    from tests.decision_product import test_perpetual_action_publication as packets
    from tests.decision_product import test_perpetual_native_graph as native_fixture
    from tests.decision_product.test_perpetual_research_binding import OWNER, factory_for

    # A next authorized normal Run queue entry; the label tick itself creates no research.
    at = minute_end(holder["at"]) + timedelta(minutes=1, seconds=10)
    holder["at"] = at
    history = future_pages(case, minute_end(at), prefix="normal_next_history_", count=1500)
    event_id, _, admitted = AdmissionService(case["db"], clock=lambda: at).admit(
        ObservationCreate(
            source_id="explicit-next-experience-research",
            source_type=SourceType.transcript,
            source_url=HttpUrl("https://example.com/explicit-next-experience-research"),
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
    monkeypatch.setattr(db, "datetime", dispatch_fixture._FixedDateTime)  # pyright: ignore[reportPrivateUsage]
    request = factory.build(event_id, run_id)
    assert any(item.evidence_id == experience_ref["ref"] for item in request.input_evidence)
    # Real normal selector/PreparedExecution up to dispatch only. The host stops
    # at submit; no Graph execution or model invocation is needed.
    monkeypatch.setattr(dispatch_fixture, "CLOCK", at + timedelta(seconds=20))
    dispatch_fixture._freeze_clock(monkeypatch)  # pyright: ignore[reportPrivateUsage]
    assert RunService(case["db"], clock=lambda: dispatch_fixture.CLOCK).claim_for_execution(
        run_id, worker_id="binding-test", lease_seconds=300
    )
    host = dispatch_fixture._BoundaryHost()  # pyright: ignore[reportPrivateUsage]
    web = dispatch_fixture.DshWebResearchRuntime(
        dispatch_fixture.DshSessionLinkService(case["db"]),
        cast(Any, host),  # Intentional partial Host that stops at real submit.
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
    with pytest.raises(dispatch_fixture._DispatchBoundaryReached):  # pyright: ignore[reportPrivateUsage]
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
        assert selected.context_manifest is not None
        assert experience_ref["ref"] in selected.context_manifest.effective_inputs
        assert session.get(RunRecord, run_id).artifact_id is None
        materials = read_perpetual_research_materials_in_session(
            session,
            cast(Any, selected),
            selection_reference=dispatch_fixture._selection_reference(execution.execution),  # pyright: ignore[reportPrivateUsage]
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
    params: dict[str, Any] = dict(
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
    shared: dict[str, Any] = dict(
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
            host_scope=ResearchProviderHostScope.model_validate(
                dict(
                    host_instance_id=str(uuid4()),
                    process_id=os.getpid(),
                    started_at=at,
                    hub_base_url="http://test",
                    workspace_id="default",
                    workspace_cwd_hash="a" * 64,
                )
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
                input_ref=ResearchProviderInputRef.model_validate(
                    recorded.reference.model_dump(
                        mode="json", include={"output_id", "payload_hash"}
                    )
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
                schema_version="perpetual-experience-provider-use.v3"
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
    assert len(calls) == 1

"""Real private SQLite originals/writers; controlled Host declarations, no Provider."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import event, select

from apps.hub_api.provider_input_routes import provider_input_router
from packages.contracts_py.decision_hub_contracts import (
    DshSessionResult,
    ResearchSessionResult,
    ResearchSynthesisCandidate,
)
from packages.contracts_py.decision_hub_contracts.generated.provider_input import (
    ResearchProviderFinishSubmit,
    ResearchProviderInputRef,
    ResearchProviderInputSubmit,
    ResearchProviderToolEmissionSubmit,
)
from packages.kernel.decision_hub_kernel.application.model_dispatch import ModelDispatchService
from packages.kernel.decision_hub_kernel.application.provider_input_receipts import (
    ProviderInputReceiptService,
    read_provider_input_in_session,
    sha,
    strict_json,
    tool_arguments_json,
)
from packages.kernel.decision_hub_kernel.application.research_context_delivery import (
    read_context_delivery_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_publication_binding import (
    native_observation_context_in_session,
    observation_json,
    observation_sha,
    store_mapped_observation,
    store_transport_observation,
)
from packages.kernel.decision_hub_kernel.application.research_review import (
    research_result_content_hash,
)
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.persistence.db import (
    ResearchEvidenceRecord,
    RunRecord,
    RunRoleOutputRecord,
    utcnow,
)
from packages.kernel.decision_hub_kernel.ports.research_observation import (
    ResearchMappedObservation,
    ResearchSynthesisSelectionWitness,
    ResearchTransportObservation,
)
from tests.dsh_native.test_execution_preparation_v2 import PreparedInputs
from tests.dsh_native.test_execution_preparation_v2 import inputs as inputs
from tests.dsh_native.test_model_dispatch_v2 import _payload
from tests.orchestration.test_agentic_research_graph import _causal_case, _horizons, _result
from tests.research.test_full_method_v2_bundle import (
    forbid_external_connections as forbid_external_connections,
)

pytestmark = pytest.mark.usefixtures("forbid_external_connections")


@pytest.fixture
def active(inputs: PreparedInputs):
    inputs.prepare()
    assert RunService(inputs.database).claim_for_execution(
        inputs.request.run_id,
        worker_id="provider-original-controls",
        lease_seconds=900,
        strategy_version="research.v1",
    )
    ModelDispatchService(inputs.database).bind(_payload(inputs))
    value = dict(
        schema_version="research-provider-input-submit.v1",
        run_id=inputs.request.run_id,
        generation=1,
        root_session_id=inputs.submit.deterministic_session_id,
        execution_session_id=inputs.submit.deterministic_session_id,
        dispatch_id="dispatch-private-1",
        request_hash=inputs.submit.request_hash,
        plugin_build_hash=inputs.upstream.plugin_build_hash,
        parent_session_id=None,
        provider="controlled-declaration-only",
        model="research-model",
        reasoning_effort="high",
        temperature=None,
        max_tokens=8192,
        stop_sequences_hash=None,
        input_json=observation_json(
            {
                "messages": [
                    {
                        "source": {"kind": "user"},
                        "content": [{"type": "text", "text": inputs.prompt}],
                    }
                ],
                "system": None,
                "tools": [],
            }
        ),
        previous_input_ref=None,
        host_scope=dict(
            host_instance_id="00000000-0000-4000-8000-000000000001",
            process_id=123,
            started_at=(datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
            hub_base_url="http://isolated.invalid",
            workspace_id="controlled-private",
            workspace_cwd_hash="3" * 64,
        ),
    )
    value["input_hash"] = sha(value["input_json"])
    yield inputs, ResearchProviderInputSubmit.model_validate(value)


def emission(payload, recorded, *, arguments=None, call_id="actual-controlled-call"):
    raw = observation_json(arguments or {"actual_argument": 1})
    common = payload.model_dump(
        include={
            "run_id",
            "generation",
            "root_session_id",
            "execution_session_id",
            "dispatch_id",
            "request_hash",
            "plugin_build_hash",
        }
    )
    return ResearchProviderToolEmissionSubmit.model_validate(
        dict(
            schema_version="research-provider-tool-emission-submit.v1",
            **common,
            input_ref=dict(
                output_id=recorded.reference.output_id, payload_hash=recorded.reference.payload_hash
            ),
            call_id=call_id,
            tool_name="decision_hub_synthesis_submit" if arguments else "controlled-test-tool",
            raw_arguments_json=raw,
            raw_arguments_hash=sha(raw),
            arguments_hash=sha(tool_arguments_json(json.loads(raw))),
            block_index=0,
        )
    )


def finish(payload, recorded):
    body = observation_json(
        {
            "finish": {"type": "finish", "reason": {"kind": "tool-calls"}},
            "usage_chunks": [
                {"type": "usage", "usage": {"inputTokens": 7, "outputTokens": 3, "totalTokens": 10}}
            ],
        }
    )
    common = payload.model_dump(
        include={
            "run_id",
            "generation",
            "root_session_id",
            "execution_session_id",
            "dispatch_id",
            "request_hash",
            "plugin_build_hash",
        }
    )
    return ResearchProviderFinishSubmit.model_validate(
        dict(
            schema_version="research-provider-finish-submit.v1",
            **common,
            input_ref=dict(
                output_id=recorded.reference.output_id, payload_hash=recorded.reference.payload_hash
            ),
            status="completed",
            finish_json=body,
            finish_hash=sha(body),
            error_code=None,
        )
    )


def test_actual_writer_idempotence_exact_refs_and_unknown_fees(active):
    inputs, payload = active
    service = ProviderInputReceiptService(inputs.database)
    first = service.record_input(payload)
    assert service.record_input(payload) == first
    emitted = service.record_emission(emission(payload, first))
    assert service.record_emission(emission(payload, first)) == emitted
    completed = service.record_finish(finish(payload, first))
    assert service.record_finish(finish(payload, first)) == completed
    assert (
        service.get_emission(
            payload.run_id,
            emitted.call_id,
            generation=1,
            execution_session_id=payload.root_session_id,
        )
        == emitted
    )
    with inputs.database.session() as session:
        rows = session.scalars(
            select(RunRoleOutputRecord).where(RunRoleOutputRecord.role.like("model_%"))
        ).all()
        assert len(rows) == 3
        stored = json.loads(
            next(row.payload_json for row in rows if row.role.startswith("model_finish:"))
        )
        assert (
            stored["input_tokens"],
            stored["output_tokens"],
            stored["total_tokens"],
            stored["cost_usd"],
        ) == (7, 3, 10, None)
    changed = payload.model_copy(
        update={"input_json": payload.input_json + " ", "input_hash": sha(payload.input_json + " ")}
    )
    with pytest.raises(ValueError, match="callback_conflict"):
        service.record_input(changed)
    with pytest.raises(ValueError, match="dispatch_identity_invalid"):
        service.record_emission(
            emission(payload, first).model_copy(update={"dispatch_id": "different-dispatch"})
        )


def test_wrong_generation_expired_lease_and_after_flush_mutation_roll_back(active):
    inputs, payload = active
    service = ProviderInputReceiptService(inputs.database)
    with pytest.raises(ValueError, match="current_execution_mismatch"):
        service.record_input(payload.model_copy(update={"generation": 2}))

    def alter(session, _):
        if any(
            isinstance(row, RunRoleOutputRecord) and row.role.startswith("model_input:")
            for row in session.new
        ):
            session.connection().exec_driver_sql(
                "UPDATE dsh_session_prompts SET prompt=prompt||' altered' WHERE run_id=?",
                (payload.run_id,),
            )

    event.listen(inputs.database.factory.class_, "after_flush", alter)
    try:
        with pytest.raises(ValueError):
            service.record_input(payload)
    finally:
        event.remove(inputs.database.factory.class_, "after_flush", alter)
    with inputs.database.session() as session:
        assert not session.scalars(
            select(RunRoleOutputRecord).where(RunRoleOutputRecord.role.like("model_input:%"))
        ).all()
        run = session.get(RunRecord, payload.run_id)
        run.lease_expires_at = utcnow() - timedelta(seconds=1)
    with pytest.raises(PermissionError, match="permission_lost"):
        service.record_input(payload)


def test_saved_original_tamper_and_borrowed_readonly_scope(active):
    inputs, payload = active
    service = ProviderInputReceiptService(inputs.database)
    first = service.record_input(payload)
    ref = ResearchProviderInputRef(
        output_id=first.reference.output_id, payload_hash=first.reference.payload_hash
    )
    with inputs.database.session() as session:
        session.connection().exec_driver_sql("BEGIN")
        native = session.connection().connection.driver_connection
        before = native.total_changes
        native.set_authorizer(lambda action, *_: 0 if action in {20, 21, 31} else 1)
        try:
            original, _ = read_provider_input_in_session(session, ref)
            assert original.producer_authenticated and not original.model_understanding_verified
            assert native.in_transaction and native.total_changes == before
        finally:
            native.set_authorizer(None)
    with inputs.database.session() as session:
        row = session.get(RunRoleOutputRecord, ref.output_id)
        row.payload_json += " "
    with inputs.database.session() as session:
        session.connection().exec_driver_sql("BEGIN")
        with pytest.raises(ValueError, match="original_hash_invalid"):
            read_provider_input_in_session(session, ref)


def _mapped(inputs, candidate, args, *, call_id="root-synthesis", preceding_events=()):
    root = inputs.submit.deterministic_session_id
    offset = len(preceding_events)
    events = [
        *preceding_events,
        {
            "method": "session.event",
            "payload": {
                "sessionId": root,
                "event": {
                    "type": "tool/call",
                    "seq": offset + 1,
                    "data": {
                        "callId": call_id,
                        "name": "decision_hub_synthesis_submit",
                        "arguments": observation_json(args),
                    },
                },
            },
        },
        {
            "method": "session.event",
            "payload": {
                "sessionId": root,
                "event": {
                    "type": "tool/result",
                    "seq": offset + 2,
                    "data": {
                        "callId": call_id,
                        "name": "decision_hub_synthesis_submit",
                        "result": candidate.model_dump(mode="json"),
                    },
                },
            },
        },
    ]
    encoded = observation_json(events)
    now = utcnow()
    reply = DshSessionResult.model_validate(
        dict(
            schema_version="dsh-session-result.v1",
            run_id=inputs.request.run_id,
            dsh_session_id=root,
            generation=1,
            last_seq=offset + 2,
            final_response="",
            events_json=encoded,
            result_hash=sha("\0" + encoded),
            trace_ref="fixture://private-provider-original",
            finish_reason="tool-calls",
            started_at=now,
            finished_at=now,
        )
    )
    result = ResearchSessionResult.model_validate(
        _result(
            inputs.request,
            candidates=[],
            horizons=candidate.horizons and [h.model_dump(mode="json") for h in candidate.horizons],
        )
    )
    result = result.model_copy(
        update={
            "request_id": candidate.request_id,
            "research_session_id": root,
            "causal_case": candidate.causal_case,
            "horizons": candidate.horizons,
        }
    )
    with inputs.database.session() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        context = native_observation_context_in_session(session, inputs.request.run_id)
        transport = store_transport_observation(
            session,
            ResearchTransportObservation(
                schema_version="research-transport-observation.v1",
                context=context,
                phase="result",
                reply=reply,
                error_code=None,
                events_json_hash=sha(encoded),
                final_response_hash=sha(""),
                recorded_at=now,
            ),
        )
        witness = ResearchSynthesisSelectionWitness(
            kind="tool_capture",
            candidate=candidate,
            candidate_payload_hash=observation_sha(candidate.model_dump(mode="json")),
            final_response_hash=None,
            call_id=call_id,
            call_event_index=offset,
            result_event_index=offset + 1,
            payload_occurrence=0,
            payload_path=("payload", "event", "data", "result"),
            call_event_hash=observation_sha(events[offset]),
            result_event_hash=observation_sha(events[offset + 1]),
            call_seq=offset + 1,
            result_seq=offset + 2,
        )
        mapped = store_mapped_observation(
            session,
            ResearchMappedObservation(
                schema_version="research-mapped-observation.v1",
                context=context,
                mapping_input_ref=transport,
                latest_transport_ref=transport,
                mapping_input_generation=1,
                latest_transport_generation=1,
                selection=witness,
                result=result,
                result_content_hash=research_result_content_hash(result),
                recorded_at=now,
            ),
            binding_validator=lambda s: native_observation_context_in_session(
                s, inputs.request.run_id
            ),
        )
    return mapped


def test_exact_root_selected_input_and_full_body_vs_ids_only(active):
    inputs, payload = active
    evidence_id = inputs.request.input_evidence[0].evidence_id
    dynamic = {
        "evidence_id": "controlled-root-dynamic-body",
        "excerpt": "Exact retained root Tool evidence; no financial sample authority.",
        "content_hash": sha("controlled-root-dynamic-content"),
        "source_content_hash": sha("controlled-root-dynamic-source"),
    }
    with inputs.database.session() as session:
        session.add(
            ResearchEvidenceRecord(
                **dynamic,
                run_id=payload.run_id,
                capability_id="controlled-local",
                requirement_id="controlled-body-delivery",
                kind="fact",
                authority="internal",
                source_id="fixture://private-provider-original",
                source_url=None,
                published_at=None,
                observed_at=utcnow(),
                received_at=utcnow(),
                research_session_id=payload.root_session_id,
                round=1,
                quality="accepted",
                freshness_status="fresh",
                accepted_at=utcnow(),
                tool_call_id="root-research",
            )
        )
    preceding = tuple(
        {
            "method": "session.event",
            "payload": {
                "sessionId": payload.root_session_id,
                "event": {
                    "type": event_type,
                    "seq": index + 1,
                    "data": {
                        "callId": "root-research",
                        "name": "decision_hub_research",
                        **data,
                    },
                },
            },
        }
        for index, (event_type, data) in enumerate(
            (
                ("tool/call", {"arguments": "{}"}),
                ("tool/result", {"result": {"evidence": [dynamic]}}),
            )
        )
    )
    full = strict_json(payload.input_json)
    full["messages"].append(
        {
            "source": {"kind": "tool", "callId": "root-research"},
            "content": [{"type": "text", "text": observation_json({"evidence": [dynamic]})}],
        }
    )
    full_json = observation_json(full)
    payload = payload.model_copy(update={"input_json": full_json, "input_hash": sha(full_json)})
    candidate = ResearchSynthesisCandidate.model_validate(
        dict(
            schema_version="research-synthesis-candidate.v1",
            request_id=inputs.request.request_id,
            causal_case=_causal_case(
                thesis="Controlled original.", unresolved_questions=[], evidence_id=evidence_id
            ),
            horizons=_horizons(dynamic["evidence_id"]),
        )
    )
    args = {"candidate": candidate.model_dump(mode="json")}
    service = ProviderInputReceiptService(inputs.database)
    first = service.record_input(payload)
    service.record_emission(emission(payload, first, arguments=args, call_id="root-synthesis"))
    service.record_finish(finish(payload, first))
    mapped = _mapped(inputs, candidate, args, preceding_events=preceding)
    with inputs.database.session() as session:
        session.connection().exec_driver_sql("BEGIN")
        before = session.connection().connection.driver_connection.total_changes
        view = read_context_delivery_in_session(session, mapped_ref=mapped)
        assert view.root_synthesis_input_verified and all(item.covered for item in view.items)
        assert not view.model_understanding_verified and not view.financial_qualified
        assert any(item.source_kind == "root_tool_result" for item in view.items)
        assert session.connection().connection.driver_connection.total_changes == before
    # A real later dispatch carrying only an ID cannot replace the selected
    # call's exact input. It also cannot claim the same provider CallID.
    changed_json = observation_json(
        {
            "messages": [
                {"source": {"kind": "user"}, "content": [{"type": "text", "text": evidence_id}]}
            ],
            "system": None,
            "tools": [],
        }
    )
    second_payload = payload.model_copy(
        update={
            "dispatch_id": "dispatch-private-2",
            "input_json": changed_json,
            "input_hash": sha(changed_json),
        }
    )
    second = service.record_input(second_payload)
    service.record_emission(
        emission(second_payload, second, arguments=args, call_id="ids-only-synthesis")
    )
    service.record_finish(finish(second_payload, second))
    second_mapped = _mapped(
        inputs, candidate, args, call_id="ids-only-synthesis", preceding_events=preceding
    )
    with inputs.database.session() as session:
        session.connection().exec_driver_sql("BEGIN")
        bad = read_context_delivery_in_session(session, mapped_ref=second_mapped)
        assert bad.root_synthesis_input_verified and not bad.context_fidelity_verified
        assert not any(item.covered for item in bad.items)
    child_events = tuple(
        {**item, "payload": {**item["payload"], "sessionId": "independent-child-only"}}
        for item in preceding
    )
    child_mapped = _mapped(inputs, candidate, args, preceding_events=child_events)
    with inputs.database.session() as session:
        session.connection().exec_driver_sql("BEGIN")
        child_only = read_context_delivery_in_session(session, mapped_ref=child_mapped)
        assert child_only.root_synthesis_input_verified and not child_only.context_fidelity_verified
        item = next(i for i in child_only.items if i.evidence_ref == dynamic["evidence_id"])
        assert (
            not item.covered and item.reason_code == "provider_root_admitted_body_original_missing"
        )


def test_real_callback_router_auth_paths_and_exact_get(active):
    inputs, payload = active

    def auth(value):
        if value != "private-test-only":
            raise HTTPException(401, "dsh_callback_unauthorized")

    app = FastAPI()
    app.include_router(provider_input_router(inputs.database, auth))
    with TestClient(app) as client:
        path = f"/v1/dsh/sessions/{payload.run_id}/provider-inputs/{payload.dispatch_id}"
        assert client.put(path, json=payload.model_dump(mode="json")).status_code == 401
        assert (
            client.put(
                path.replace(payload.dispatch_id, "wrong"),
                json=payload.model_dump(mode="json"),
                headers={"X-Decision-Hub-Bridge-Key": "private-test-only"},
            ).status_code
            == 409
        )
        response = client.put(
            path,
            json=payload.model_dump(mode="json"),
            headers={"X-Decision-Hub-Bridge-Key": "private-test-only"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["producer_authenticated"] is True
        missing = client.get(
            f"/v1/dsh/sessions/{payload.run_id}/provider-tool-emissions/missing",
            params={"generation": 1, "execution_session_id": payload.root_session_id},
            headers={"X-Decision-Hub-Bridge-Key": "private-test-only"},
        )
        assert (
            missing.status_code == 404
            and missing.json()["detail"] == "research_provider_tool_emission_not_found"
        )


def test_finite_cross_language_numbers_and_malformed_raw():
    value = strict_json('{"z":1.0,"negative":-0.0,"tiny":1e-7,"plain":1e-6}')
    assert tool_arguments_json(value) == '{"negative":0,"plain":0.000001,"tiny":1e-7,"z":1}'
    for raw in ('{"duplicate":1,"duplicate":2}', '{"n":NaN}', '{"n":1e999}'):
        with pytest.raises(ValueError):
            strict_json(raw)
    with pytest.raises(ValueError, match="unsafe_integer"):
        tool_arguments_json({"value": 2**53})

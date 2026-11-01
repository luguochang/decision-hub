"""Private native originals only. No Provider/model/financial receipt is claimed."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from sqlalchemy import event

from packages.contracts_py.decision_hub_contracts import (
    DshSessionResult,
    ResearchSessionResult,
    ResearchSynthesisCandidate,
)
from packages.kernel.decision_hub_kernel.application.provider_input_receipts import sha
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
from packages.kernel.decision_hub_kernel.application.research_review_effects import (
    read_review_effects_in_session,
    record_review_effects_in_session,
)
from packages.kernel.decision_hub_kernel.persistence.db import RunRoleOutputRecord, utcnow
from packages.kernel.decision_hub_kernel.ports.research_observation import (
    ResearchMappedObservation,
    ResearchSynthesisSelectionWitness,
    ResearchTransportObservation,
)
from packages.runtime_adapters.dsh_runtime.client import DshSdkNotification
from packages.runtime_adapters.dsh_runtime.result_mapper import _attach_attested_review
from packages.runtime_adapters.dsh_runtime.tool_result_attestation import (
    extract_structured_review_receipts,
)
from tests.dsh_native.test_provider_input_receipts import active as active
from tests.dsh_native.test_provider_input_receipts import inputs as inputs
from tests.orchestration.test_agentic_research_graph import _causal_case, _horizons, _result
from tests.research.test_full_method_v2_bundle import (
    forbid_external_connections as forbid_external_connections,
)

pytestmark = pytest.mark.usefixtures("forbid_external_connections")


def _mapping(
    inputs,
    *,
    empty=False,
    answered=False,
    bad_resolution_ref=False,
    wrong_gate=False,
    before_store=None,
):
    now = utcnow()
    root = inputs.submit.deterministic_session_id
    evidence = inputs.request.input_evidence[0].evidence_id
    candidate = ResearchSynthesisCandidate.model_validate(
        {
            "schema_version": "research-synthesis-candidate.v1",
            "request_id": inputs.request.request_id,
            "causal_case": _causal_case(
                thesis="受控原件工程验证", unresolved_questions=[], evidence_id=evidence
            ),
            "horizons": _horizons(evidence),
        }
    )
    binding = {
        "runId": inputs.request.run_id,
        "generation": 1,
        "requestHash": inputs.submit.request_hash,
        "sessionId": root,
        "requestId": inputs.submit.deterministic_request_id,
    }
    issue = {
        "issue_id": "controlled-question",
        "severity": "important",
        "blocking_scope": ["direction", "report"],
        "evidence_refs": [evidence],
        "status": "answered" if answered or bad_resolution_ref else "deferred",
        "disposition": "accepted" if answered or bad_resolution_ref else "unresolved",
        "resolution_evidence_refs": ["unadmitted-source"]
        if bad_resolution_ref
        else [evidence]
        if answered
        else [],
        "effect_on_direction": "unchanged" if answered else "unknown",
        "effect_on_tail_risk": "unknown",
        "effect_on_monitor": "unknown",
        "effect_on_report": "reduced" if answered else "unknown",
        "reason": "保留真实未知效果，不能推断不变。",
    }
    body = {
        "schema_version": "independent-review-receipt.v2",
        "role": "independent_reviewer",
        "target_claim": candidate.causal_case.thesis,
        "evidence_refs": [evidence],
        "challenge": "仅工程控制",
        "resolution": "accepted" if empty else "bounded",
        "limitations": ["不是金融资格"],
        "reviewer_version": "controlled-native.v2",
        "issues": [] if empty else [issue],
    }
    receipt_hash = observation_sha(body)
    candidate_json = observation_json(candidate.model_dump(mode="json"))
    prompt = "\n".join(
        [
            "BEGIN_FROZEN_REVIEW_CONTROL_JSON",
            observation_json(
                {
                    "schema_version": "decision-hub-frozen-review-input.v1",
                    "candidate_sha256": sha(candidate_json),
                    "expected_target_claim": candidate.causal_case.thesis,
                    "frozen_candidate": candidate.model_dump(mode="json"),
                }
            ),
            "END_FROZEN_REVIEW_CONTROL_JSON",
        ]
    )
    wrapper = {
        "schema_version": "decision-hub-frozen-review-result.v1",
        "binding": binding,
        "freeze_call_id": "freeze",
        "freeze_seq": 2,
        "candidate_json": candidate_json,
        "candidate_sha256": sha(candidate_json),
        "expected_target_claim": candidate.causal_case.thesis,
        "actual_prompt": prompt,
        "actual_prompt_sha256": sha(prompt),
        "child_session_id": "controlled-child",
        "original_output": {
            "kind": "foreground",
            "runId": "controlled-child",
            "output": [{"type": "text", "text": observation_json(body)}],
        },
    }
    gate = {
        "schema_version": "decision-hub-final-review-gate.v1",
        "status": "matched",
        "reason": "exact_target",
        "binding": binding,
        "candidate_json": candidate_json,
        "candidate_sha256": sha(candidate_json),
        "review_receipt_hash": "f" * 64 if wrong_gate else receipt_hash,
        "review_call_id": "review",
    }

    def native(seq, kind, data):
        return {
            "method": "session.event",
            "payload": {
                "sessionId": root,
                "event": {"seq": seq, "type": kind, "time": now.timestamp() * 1000, "data": data},
            },
        }

    events = [
        native(1, "user/message", {"source": {"kind": "user", "rpcId": binding["requestId"]}}),
        native(
            2,
            "tool/call",
            {
                "callId": "freeze",
                "name": "decision_hub_synthesis_submit",
                "arguments": {"candidate": candidate.model_dump(mode="json")},
            },
        ),
        native(
            3,
            "tool/result",
            {
                "callId": "freeze",
                "name": "decision_hub_synthesis_submit",
                "isError": True,
                "error": "decision_hub_final_review_required",
            },
        ),
        native(4, "tool/call", {"callId": "review", "name": "subagent_reviewer", "arguments": {}}),
        native(
            5, "tool/result", {"callId": "review", "name": "subagent_reviewer", "result": wrapper}
        ),
        native(
            6,
            "tool/call",
            {
                "callId": "final",
                "name": "decision_hub_synthesis_submit",
                "arguments": {"candidate": candidate.model_dump(mode="json")},
            },
        ),
        native(
            7,
            "tool/result",
            {
                "callId": "final",
                "name": "decision_hub_synthesis_submit",
                "result": {
                    "schema_version": "decision-hub-synthesis-tool-result.v1",
                    "candidate": candidate.model_dump(mode="json"),
                    "review_gate": gate,
                },
            },
        ),
    ]
    notifications = tuple(DshSdkNotification(item["method"], item["payload"]) for item in events)
    receipts = extract_structured_review_receipts(
        notifications,
        expected_session_id=root,
        allowed_evidence_ids=frozenset([evidence]),
        fallback_created_at=now,
    )
    result = ResearchSessionResult.model_validate(
        _result(
            inputs.request,
            candidates=[],
            horizons=[item.model_dump(mode="json") for item in candidate.horizons],
        )
    )
    result = result.model_copy(
        update={
            "research_session_id": root,
            "causal_case": candidate.causal_case,
            "horizons": candidate.horizons,
            "started_at": now - timedelta(seconds=1),
            "finished_at": now + timedelta(seconds=1),
        }
    )
    result = _attach_attested_review(result, receipts)
    if before_store is not None:
        before_store(notifications, candidate)
        now = utcnow()
    encoded = observation_json(events)
    reply = DshSessionResult.model_validate(
        {
            "schema_version": "dsh-session-result.v1",
            "run_id": inputs.request.run_id,
            "dsh_session_id": root,
            "generation": 1,
            "last_seq": 7,
            "final_response": "",
            "events_json": encoded,
            "result_hash": sha("\0" + encoded),
            "trace_ref": "fixture://private-review-control",
            "finish_reason": "tool-calls",
            "started_at": result.started_at,
            "finished_at": result.finished_at,
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
            call_id="final",
            call_event_index=5,
            result_event_index=6,
            payload_occurrence=0,
            payload_path=("payload", "event", "data", "result", "candidate"),
            call_event_hash=observation_sha(events[5]),
            result_event_hash=observation_sha(events[6]),
            call_seq=6,
            result_seq=7,
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
    return mapped, result, notifications, receipt_hash


def test_native_review_effect_writer_and_read_snapshot(active):
    inputs, _ = active
    mapped, result, notifications, full_hash = _mapping(inputs)
    with inputs.database.session() as session:
        session.connection().exec_driver_sql("BEGIN")
        assert (
            read_review_effects_in_session(session, mapped_ref=mapped, source_result=result) is None
        )
    with inputs.database.session() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        with pytest.raises(PermissionError, match="permission_lost"):
            record_review_effects_in_session(
                session,
                mapped_ref=mapped,
                source_result=result,
                expected_lease_owner="stale-owner",
                known_at=utcnow() + timedelta(seconds=2),
            )
        view = record_review_effects_in_session(
            session,
            mapped_ref=mapped,
            source_result=result,
            expected_lease_owner="provider-original-controls",
            known_at=utcnow() + timedelta(seconds=2),
        )
        assert view.public_review_ids == (result.reviews[0].review_id,)
        assert view.source_result_hash == observation_sha(result.model_dump(mode="json"))
        assert (
            view.challenges[0].effect_on_report == view.challenges[0].effect_on_monitor == "unknown"
        )
        assert (
            view.challenges[0].status == "deferred"
            and view.challenges[0].resolved_result_hash is None
        )
        assert view.review_effect_ref is not None and len(view.review_effect_ref.role) <= 64
        original = json.loads(
            session.get(RunRoleOutputRecord, view.review_effect_ref.output_id).payload_json
        )
        assert original["receipt_hash"] == full_hash and not original["financial_qualified"]
        assert original["public_review_id"] != "review_" + full_hash[:32]
        again = record_review_effects_in_session(
            session,
            mapped_ref=mapped,
            source_result=result,
            expected_lease_owner="provider-original-controls",
            known_at=utcnow() + timedelta(seconds=20),
        )
        assert again == view
    with inputs.database.session() as session:
        connection = session.connection()
        connection.exec_driver_sql("BEGIN")
        before = connection.connection.driver_connection.total_changes
        statements = []

        def sql(_c, _cur, statement, _params, _ctx, _many):
            statements.append(statement)

        event.listen(connection, "before_cursor_execute", sql)
        try:
            assert (
                read_review_effects_in_session(session, mapped_ref=mapped, source_result=result)
                == view
            )
            assert session.connection() is connection
            assert connection.connection.driver_connection.total_changes == before
            assert statements and all(
                item.lstrip().upper().startswith("SELECT ") for item in statements
            )
        finally:
            event.remove(connection, "before_cursor_execute", sql)
        with pytest.raises(ValueError, match="mapped_source_invalid"):
            read_review_effects_in_session(
                session,
                mapped_ref=mapped,
                source_result=result.model_copy(update={"synthesis_failure_code": "wrong-source"}),
            )
    empty, empty_result, _, _ = _mapping(inputs, empty=True)
    with inputs.database.session() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        checked = record_review_effects_in_session(
            session,
            mapped_ref=empty,
            source_result=empty_result,
            expected_lease_owner="provider-original-controls",
            known_at=utcnow() + timedelta(seconds=2),
        )
        assert (
            checked.public_review_ids == (empty_result.reviews[0].review_id,)
            and checked.challenges == ()
        )
    answered, answered_result, _, _ = _mapping(inputs, answered=True)
    with inputs.database.session() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        resolved = record_review_effects_in_session(
            session,
            mapped_ref=answered,
            source_result=answered_result,
            expected_lease_owner="provider-original-controls",
            known_at=utcnow() + timedelta(seconds=2),
        )
        assert resolved.challenges[0].resolved_result_hash == resolved.source_result_hash
        assert resolved.challenges[0].effect_on_report == "reduced"
        assert resolved.challenges[0].effect_on_monitor == "unknown"
        from packages.kernel.decision_hub_kernel.decision.research_quality import (
            _challenge_resolved,
        )

        assert _challenge_resolved(
            resolved.challenges[0],
            resolved.source_result_hash,
            {inputs.request.input_evidence[0].evidence_id},
        )
    wrong, wrong_result, _, _ = _mapping(inputs, wrong_gate=True)
    with inputs.database.session() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        assert (
            record_review_effects_in_session(
                session,
                mapped_ref=wrong,
                source_result=wrong_result,
                expected_lease_owner="provider-original-controls",
                known_at=utcnow() + timedelta(seconds=2),
            )
            is None
        )
    receipts = extract_structured_review_receipts(
        notifications,
        expected_session_id="wrong-root",
        allowed_evidence_ids=frozenset([inputs.request.input_evidence[0].evidence_id]),
        fallback_created_at=utcnow(),
    )
    assert receipts == []
    with inputs.database.session() as session:
        original_row = session.get(RunRoleOutputRecord, view.review_effect_ref.output_id)
        original_row.payload_json += " "
    with inputs.database.session() as session:
        session.connection().exec_driver_sql("BEGIN")
        with pytest.raises(ValueError, match="original_hash_invalid"):
            read_review_effects_in_session(session, mapped_ref=mapped, source_result=result)

"""Real private provider/effect originals; controlled Host envelope, no model."""

from datetime import timedelta
from typing import Any

import pytest

from packages.contracts_py.decision_hub_contracts import (
    EvidenceCandidate,
    ResearchSynthesisCandidate,
)
from packages.kernel.decision_hub_kernel.application.fact_store import ResearchFactStore
from packages.kernel.decision_hub_kernel.application.provider_input_receipts import (
    ProviderInputReceiptService,
    sha,
    strict_json,
)
from packages.kernel.decision_hub_kernel.application.research_context_delivery import (
    read_context_delivery_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchEvidenceService,
)
from packages.kernel.decision_hub_kernel.application.research_publication_binding import (
    observation_json,
    observation_sha,
)
from packages.kernel.decision_hub_kernel.application.research_review_effects import (
    record_review_effects_in_session,
)
from packages.kernel.decision_hub_kernel.persistence.db import ResearchFactRecord, utcnow
from tests.dsh_native.test_independent_review_effects import _mapping
from tests.dsh_native.test_provider_input_receipts import (
    _mapped,  # pyright: ignore[reportPrivateUsage] - existing original-writer fixture
    emission,
    finish,
)
from tests.dsh_native.test_provider_review_delivery import active as active
from tests.dsh_native.test_provider_review_delivery import inputs as inputs
from tests.orchestration.test_agentic_research_graph import (
    _causal_case,  # pyright: ignore[reportPrivateUsage] - existing candidate fixture
    _horizons,  # pyright: ignore[reportPrivateUsage] - existing candidate fixture
)
from tests.research.test_fact_store import _fact  # pyright: ignore[reportPrivateUsage]
from tests.research.test_full_method_v2_bundle import (
    forbid_external_connections as forbid_external_connections,
)

pytestmark = pytest.mark.usefixtures("forbid_external_connections")


@pytest.mark.parametrize("mode", ["full", "altered-receipt", "wrong-generation"])
def test_protected_projection_full_body_and_identity(active, mode):
    prepared, base = active
    service = ProviderInputReceiptService(prepared.database)

    def before_store(notifications, candidate):
        wrapper = notifications[4].payload["event"]["data"]["result"]
        receipt = strict_json(wrapper["original_output"]["output"][0]["text"])
        projection = {
            "schema_version": "decision-hub-protected-review-body.v1",
            "run_id": base.run_id,
            "generation": base.generation,
            "root_session_id": base.root_session_id,
            "request_hash": base.request_hash,
            "review_call_id": "review",
            "review_call_seq": 4,
            "review_result_seq": 5,
            "frozen_candidate_sha256": wrapper["candidate_sha256"],
            "wrapper_sha256": observation_sha(wrapper),
            "receipt_sha256": observation_sha(receipt),
            "receipt": receipt,
        }
        envelope = {
            "schema_version": "decision-hub-protected-context.v1",
            "snapshot_id": "snapshot",
            "run_id": base.run_id,
            "generation": base.generation,
            "root_session_id": base.root_session_id,
            "request_hash": base.request_hash,
            "plugin_build_hash": base.plugin_build_hash,
            "request_id": prepared.request.request_id,
            "command_request_id": prepared.submit.deterministic_request_id,
            "native_first_seq": 1,
            "native_last_seq": 5,
            "frozen_input_evidence": [
                item.model_dump(mode="json") for item in prepared.request.input_evidence
            ],
            "evidence_candidates": [],
            "facts": [],
            "independent_reviews": [projection],
            "proof_scope": "dsh_llm_generate_options",
            "model_understanding_verified": False,
            "budget_qualified": False,
            "financial_qualified": False,
        }
        if mode == "altered-receipt":
            projection["receipt"]["issues"][0]["reason"] += " changed"
        elif mode == "wrong-generation":
            envelope["generation"] += 1
        # The old giant prompt and wrapper are absent after controlled prune.
        # Only the actual full-body projection is retained in options.messages.
        body = {
            "messages": [
                {
                    "id": "snapshot",
                    "role": "user",
                    "source": {"kind": "plugin", "plugin": "decision-hub-protected-context"},
                    "content": [
                        {"type": "text", "text": "Preserved data, no authority."},
                        {"type": "text", "text": observation_json(envelope)},
                    ],
                }
            ],
            "system": None,
            "tools": [],
        }
        encoded = observation_json(body)
        payload = base.model_copy(update={"input_json": encoded, "input_hash": sha(encoded)})
        recorded = service.record_input(payload)
        service.record_emission(
            emission(
                payload,
                recorded,
                arguments={"candidate": candidate.model_dump(mode="json")},
                call_id="final",
            )
        )
        service.record_finish(finish(payload, recorded))

    mapped, result, _, _ = _mapping(prepared, before_store=before_store)
    with prepared.database.session() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        effect = record_review_effects_in_session(
            session,
            mapped_ref=mapped,
            source_result=result,
            expected_lease_owner="provider-original-controls",
            known_at=utcnow() + timedelta(seconds=2),
        )
        assert effect and effect.review_effect_ref
    with prepared.database.session() as session:
        connection = session.connection()
        connection.exec_driver_sql("BEGIN")
        native = connection.connection.driver_connection
        changes = native.total_changes
        native.set_authorizer(lambda action, *_: 0 if action in {20, 21, 31} else 1)
        try:
            view = read_context_delivery_in_session(session, mapped_ref=mapped)
            assert view.root_synthesis_input_verified
            review = next(item for item in view.items if item.source_kind == "root_child_summary")
            assert review.original_ref == effect.review_effect_ref
            assert review.covered is (mode == "full")
            assert view.context_fidelity_verified is (mode == "full")
            assert not view.model_understanding_verified and not view.financial_qualified
            assert native.total_changes == changes and native.in_transaction
            assert session.connection() is connection
        finally:
            native.set_authorizer(None)


@pytest.mark.parametrize(
    "mode",
    [
        "numeric-equivalent",
        "changed-number",
        "changed-bool",
        "changed-string",
        "native-only-change",
        "changed-schema",
        "changed-clock",
        "changed-ref",
        "changed-status",
        "unsafe-integer",
        "nonfinite",
    ],
)
def test_protected_fact_durable_dto_wire_identity(
    active,  # pyright: ignore[reportMissingParameterType] - actual official Factory fixture
    mode: str,
):
    """Official Factory/savers and exact private receipts; no census substitution."""
    prepared, base = active
    requirement = prepared.request.evidence_requirements[0]
    cutoff = prepared.request.pit_cutoff_at
    evidence = EvidenceCandidate.model_validate(
        {
            "evidence_id": "protected-wire-evidence",
            "requirement_id": requirement.requirement_id,
            "kind": "market",
            "authority": "exchange",
            "source_id": "coinex-public",
            "source_url": "https://api.coinex.com/v2/spot/ticker",
            "published_at": None,
            "observed_at": cutoff - timedelta(seconds=2),
            "received_at": cutoff - timedelta(seconds=1),
            "content_hash": sha("controlled-wire-content"),
            "source_content_hash": sha("controlled-wire-source"),
            "excerpt": "Controlled private evidence for a wire identity boundary.",
            "structured_payload_ref": None,
            "tool_call_id": "root-research",
            "research_session_id": base.root_session_id,
            "round": 1,
            "quality": "candidate",
            "freshness_status": "unknown",
            "conflict_group": None,
        }
    )
    evidence = ResearchEvidenceService(prepared.database).accept_candidates(
        run_id=base.run_id,
        capability_id="market.crypto_derivatives",
        candidates=[evidence],
        requirements={requirement.requirement_id: requirement},
        cutoff_at=cutoff,
    )[0]
    fact = ResearchFactStore(prepared.database).accept_facts(
        run_id=base.run_id,
        capability_id="market.crypto_derivatives",
        facts=[
            _fact(
                evidence_id=evidence.evidence_id,
                requirement_id=evidence.requirement_id,
                observed_at=evidence.observed_at,
                received_at=evidence.received_at,
                attributes={"window_sessions": 5.0},
            )
        ],
        cutoff_at=cutoff,
    )[0]
    assert evidence.quality == fact.quality == "accepted"
    assert type(fact.attributes["window_sessions"]) is float
    wire_fact = fact.model_dump(mode="json")
    wire_fact["attributes"]["window_sessions"] = 5
    if mode in {"changed-number", "changed-bool", "changed-string", "unsafe-integer"}:
        wire_fact["attributes"]["window_sessions"] = {
            "changed-number": 6,
            "changed-bool": True,
            "changed-string": "5",
            "unsafe-integer": 2**53 + 1,
        }[mode]
    elif mode == "changed-clock":
        wire_fact["observed_at"] = (fact.observed_at - timedelta(seconds=1)).isoformat()
    elif mode == "changed-ref":
        wire_fact["evidence_id"] = "unbound-evidence"
    elif mode == "changed-status":
        wire_fact["quality"] = "rejected"
    native_fact = {**wire_fact, "attributes": dict(wire_fact["attributes"])}
    if mode == "native-only-change":
        native_fact["attributes"]["window_sessions"] = 6
    envelope: dict[str, Any] = {
        "schema_version": "decision-hub-protected-context.v1",
        "snapshot_id": "protected-wire",
        "run_id": base.run_id,
        "generation": base.generation,
        "root_session_id": base.root_session_id,
        "request_hash": base.request_hash,
        "plugin_build_hash": base.plugin_build_hash,
        "request_id": prepared.request.request_id,
        "command_request_id": prepared.submit.deterministic_request_id,
        "native_first_seq": 1,
        "native_last_seq": 2,
        "frozen_input_evidence": [
            item.model_dump(mode="json") for item in prepared.request.input_evidence
        ],
        "evidence_candidates": [evidence.model_dump(mode="json")],
        "facts": [wire_fact],
        "independent_reviews": [],
        "proof_scope": "dsh_llm_generate_options",
        "model_understanding_verified": False,
        "budget_qualified": False,
        "financial_qualified": False,
    }
    if mode == "changed-schema":
        envelope["schema_version"] = "decision-hub-protected-context.v2"
    encoded = observation_json(
        {
            "messages": [
                {
                    "id": "protected-wire",
                    "role": "user",
                    "source": {"kind": "plugin", "plugin": "decision-hub-protected-context"},
                    "content": [
                        {"type": "text", "text": "Preserved data, no authority."},
                        {"type": "text", "text": observation_json(envelope)},
                    ],
                }
            ],
            "system": None,
            "tools": [],
        }
    )
    payload = base.model_copy(update={"input_json": encoded, "input_hash": sha(encoded)})
    candidate = ResearchSynthesisCandidate.model_validate(
        {
            "schema_version": "research-synthesis-candidate.v1",
            "request_id": prepared.request.request_id,
            "causal_case": _causal_case(
                thesis="Controlled wire identity.",
                unresolved_questions=[],
                evidence_id=prepared.request.input_evidence[0].evidence_id,
            ),
            "horizons": _horizons(prepared.request.input_evidence[0].evidence_id),
        }
    )
    args = {"candidate": candidate.model_dump(mode="json")}
    service = ProviderInputReceiptService(prepared.database)
    recorded = service.record_input(payload)
    service.record_emission(
        emission(payload, recorded, arguments=args, call_id="root-synthesis")
    )
    service.record_finish(finish(payload, recorded))
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
                (
                    "tool/result",
                    {
                        "result": {
                            "evidence": envelope["evidence_candidates"],
                            "facts": [native_fact],
                        }
                    },
                ),
            )
        )
    )
    mapped = _mapped(prepared, candidate, args, preceding_events=preceding)
    if mode == "nonfinite":
        # Corrupt only the private durable candidate. Native/body bytes stay finite.
        with prepared.database.session() as session:
            row = session.get(ResearchFactRecord, fact.fact_id)
            assert row is not None
            row.attributes_json = '{"window_sessions":NaN}'
    with prepared.database.session() as session:
        connection = session.connection()
        connection.exec_driver_sql("BEGIN")
        native = connection.connection.driver_connection
        changes = native.total_changes
        native.set_authorizer(
            # The real DBAPI connection has no static callback signature here.
            lambda action, *_: (  # pyright: ignore[reportUnknownLambdaType]
                0 if action in {20, 21, 31} else 1
            )
        )
        try:
            if mode in {"unsafe-integer", "nonfinite"}:
                error = "unsafe_integer" if mode == "unsafe-integer" else "nonfinite_json"
                with pytest.raises(ValueError, match="research_provider_" + error):
                    read_context_delivery_in_session(session, mapped_ref=mapped)
            else:
                view = read_context_delivery_in_session(session, mapped_ref=mapped)
                assert view.root_synthesis_input_verified
                assert view.context_fidelity_verified is (mode == "numeric-equivalent")
                assert all(item.covered for item in view.items) is (mode == "numeric-equivalent")
                assert not view.model_understanding_verified and not view.financial_qualified
                if mode == "numeric-equivalent":
                    assert all("frozen_input_evidence" in item.input_loci[0] for item in view.items)
            assert native.total_changes == changes and native.in_transaction
            assert session.connection() is connection
        finally:
            native.set_authorizer(None)

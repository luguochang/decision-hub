"""Four bounded usage controls; synthetic Host declarations, real SQLite originals.

Child inspection events below are explicit engineering originals. The current
production Host does not emit that independent denominator and remains unknown.
No provider, financial sample, invoice or budget qualification is exercised.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta

import pytest
from pydantic import ValidationError

from packages.contracts_py.decision_hub_contracts import (
    DshSessionCompletion,
    ResearchSynthesisCandidate,
)
from packages.contracts_py.decision_hub_contracts.generated.provider_input import (
    ResearchProviderInputRef,
)
from packages.kernel.decision_hub_kernel.application.dsh_sessions import DshSessionLinkService
from packages.kernel.decision_hub_kernel.application.model_dispatch import ModelDispatchService
from packages.kernel.decision_hub_kernel.application.provider_input_receipts import (
    ProviderInputReceiptService,
)
from packages.kernel.decision_hub_kernel.application.research_context import build_task_policy
from packages.kernel.decision_hub_kernel.application.research_graph_projection_v3 import (
    quality_for_state,
)
from packages.kernel.decision_hub_kernel.application.research_graph_recipe_v3 import _effective_hash
from packages.kernel.decision_hub_kernel.application.research_provider_usage import (
    read_provider_run_usage_in_session,
    verify_provider_usage_census_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_publication_binding import (
    observation_json,
    observation_sha,
    read_mapped_observation,
    read_transport_observation,
)
from packages.kernel.decision_hub_kernel.application.research_quality_sidecar import (
    _row_ref,
    persist_quality_sidecar_in_session,
    read_quality_sidecar_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_quality_view import (
    PreparedQualityBudgetViewV1,
    ResearchQualityViewV1,
)
from packages.kernel.decision_hub_kernel.application.research_request_identity import (
    research_request_hash,
)
from packages.kernel.decision_hub_kernel.persistence.db import RunRoleOutputRecord, utcnow
from packages.kernel.decision_hub_kernel.ports.provider_input import (
    ProviderConversationUsageCensusV1,
)
from tests.dsh_native.test_execution_preparation_v2 import inputs as inputs_fixture
from tests.dsh_native.test_model_dispatch_v2 import _payload
from tests.dsh_native.test_provider_input_receipts import (
    _mapped,
    finish,
)
from tests.dsh_native.test_provider_input_receipts import (
    active as active_fixture,
)
from tests.market_risk.test_quality_sidecar_v3 import (
    original_fixture as quality_fixture,
)
from tests.market_risk.test_quality_sidecar_v3 import (
    persist,
)
from tests.orchestration.test_agentic_research_graph import _causal_case, _horizons
from tests.research.test_full_method_v2_bundle import (
    forbid_external_connections as forbid_external_connections,
)

pytestmark = pytest.mark.usefixtures("forbid_external_connections")


def _rehash(body):
    body["census_hash"] = observation_sha(
        {key: value for key, value in body.items() if key != "census_hash"}
    )
    return body


def _step(session_id, seq):
    return {
        "method": "session.event",
        "payload": {
            "sessionId": session_id,
            "event": {"type": "step/start", "seq": seq, "data": {}},
        },
    }


@pytest.fixture(scope="module")
def usage_originals(tmp_path_factory):
    maker = inputs_fixture.__wrapped__(tmp_path_factory.mktemp("usage-originals"))
    inputs = next(maker)
    # The shared preparation fixture narrows the round's requirements but keeps
    # the full-Task policy ref. Authenticate a genuinely matching round policy.
    policy = build_task_policy(
        research_mode=inputs.request.research_mode or "current_decision",
        requirements=inputs.request.evidence_requirements,
        created_at=inputs.now,
        max_evidence_rounds=inputs.request.execution_budget.max_evidence_rounds,
    )
    policy_ref = f"{policy.policy_id}.{policy.policy_version}:{policy.content_hash[:16]}"
    request = inputs.request.model_copy(update={"task_policy_ref": policy_ref})
    digest = research_request_hash(request)
    session_id, request_id = DshSessionLinkService.deterministic_ids(request.run_id, digest)
    submit = inputs.submit.model_copy(
        update={
            "request_hash": digest,
            "deterministic_session_id": session_id,
            "deterministic_request_id": request_id,
        }
    )
    inputs = replace(inputs, request=request, submit=submit)
    active = active_fixture.__wrapped__(inputs)
    inputs, payload = next(active)
    try:
        service = ProviderInputReceiptService(inputs.database)
        first = service.record_input(payload)
        # A real duplicate callback is idempotent and cannot double the census.
        assert service.record_input(payload) == first
        service.record_finish(finish(payload, first))
        second_payload = payload.model_copy(
            update={
                "dispatch_id": "controlled-root-2",
                "previous_input_ref": ResearchProviderInputRef(
                    output_id=first.reference.output_id,
                    payload_hash=first.reference.payload_hash,
                ),
            }
        )
        second = service.record_input(second_payload)
        service.record_finish(finish(second_payload, second))
        child = "controlled-independent-child"
        binding = _payload(inputs).model_copy(
            update={"execution_session_id": child, "parent_session_id": payload.root_session_id}
        )
        ModelDispatchService(inputs.database).bind(binding)
        child_payload = payload.model_copy(
            update={
                "execution_session_id": child,
                "parent_session_id": payload.root_session_id,
                "dispatch_id": "controlled-child-1",
            }
        )
        child_input = service.record_input(child_payload)
        service.record_finish(finish(child_payload, child_input))
        candidate = ResearchSynthesisCandidate.model_validate(
            dict(
                schema_version="research-synthesis-candidate.v1",
                request_id=inputs.request.request_id,
                causal_case=_causal_case(
                    thesis="Synthetic usage control.", unresolved_questions=[]
                ),
                horizons=_horizons(),
            )
        )
        args = {"candidate": candidate.model_dump(mode="json")}
        roots = (_step(payload.root_session_id, 1), _step(payload.root_session_id, 2))
        partial_ref = _mapped(inputs, candidate, args, preceding_events=roots)
        with inputs.database.session() as session:
            session.connection().exec_driver_sql("BEGIN")
            partial = read_provider_run_usage_in_session(
                session, mapped_ref=partial_ref, as_of=utcnow()
            )
        mapped_ref = _mapped(inputs, candidate, args, preceding_events=(*roots, _step(child, 1)))
        with inputs.database.session() as session:
            session.connection().exec_driver_sql("BEGIN")
            mapped = read_mapped_observation(session, mapped_ref)
            reply = read_transport_observation(session, mapped.mapping_input_ref).reply
        DshSessionLinkService(inputs.database).complete(
            DshSessionCompletion(
                schema_version="dsh-session-completion.v1",
                run_id=payload.run_id,
                dsh_session_id=payload.root_session_id,
                generation=1,
                terminal_status="completed",
                last_seq=reply.last_seq,
                trace_ref=reply.trace_ref,
                result_ref=f"hub://runs/{payload.run_id}/result",
                result_hash=reply.result_hash,
                completed_at=reply.finished_at,
                error=None,
            ),
            authenticated_host=True,
        )
        at = utcnow()
        with inputs.database.session() as session:
            session.connection().exec_driver_sql("BEGIN")
            census = read_provider_run_usage_in_session(session, mapped_ref=mapped_ref, as_of=at)
        yield inputs, mapped_ref, census, partial
    finally:
        active.close()
        maker.close()


def test_complete_independent_root_child_census_is_not_a_bill(usage_originals):
    inputs, mapped, census, _ = usage_originals
    assert census.coverage_status == "complete" and census.token_status == "known"
    assert (
        census.expected_dispatches == census.observed_dispatches == census.covered_dispatches == 3
    )
    assert census.root_dispatches == 2 and census.child_dispatches == 1
    assert (census.input_tokens, census.output_tokens, census.total_tokens) == (21, 9, 30)
    assert census.cost_status == "unknown" and census.cost_usd is None
    assert census.scope == "conversation_only" and census.excluded_purposes == (
        "compaction",
        "title",
    )
    assert not census.budget_qualified and not census.model_understanding_verified
    with inputs.database.session() as session:
        connection = session.connection()
        connection.exec_driver_sql("BEGIN")
        native = connection.connection.driver_connection
        before, tx = native.total_changes, session.get_transaction()
        assert (
            read_provider_run_usage_in_session(session, mapped_ref=mapped, as_of=census.as_of)
            == census
        )
        verify_provider_usage_census_in_session(session, census)
        assert native.total_changes == before and session.get_transaction() is tx
        assert not session.new and not session.dirty and not session.deleted


def test_root_only_child_missing_and_missing_finish_keep_totals_null(usage_originals):
    inputs, mapped, census, partial = usage_originals
    assert partial.coverage_status == "incomplete" and partial.token_status == "unknown"
    assert partial.observed_dispatches == partial.covered_dispatches == 3
    assert partial.expected_dispatches is None
    assert "provider_child_step_census_unavailable" in partial.missing_codes
    assert partial.input_tokens is partial.output_tokens is partial.total_tokens is None
    forged = partial.model_dump(mode="json")
    forged.update(
        coverage_status="complete",
        token_status="known",
        expected_dispatches=3,
        input_tokens=21,
        output_tokens=9,
        total_tokens=30,
    )
    with pytest.raises(ValidationError):
        ProviderConversationUsageCensusV1.model_validate_json(observation_json(_rehash(forged)))
    with inputs.database.session() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        transaction = session.begin_nested()
        ref = census.dispatches[0].finish_ref
        session.delete(session.get(RunRoleOutputRecord, ref.output_id))
        session.flush()
        unknown = read_provider_run_usage_in_session(session, mapped_ref=mapped, as_of=census.as_of)
        assert unknown.coverage_status == "incomplete" and unknown.covered_dispatches == 2
        assert unknown.input_tokens is unknown.output_tokens is unknown.total_tokens is None
        assert any(
            "provider_dispatch_finish_missing" in item.missing_codes for item in unknown.dispatches
        )
        transaction.rollback()


def test_original_tamper_census_forgery_and_physical_read_boundary(usage_originals):
    inputs, mapped, census, _ = usage_originals
    with inputs.database.session() as session:
        with pytest.raises(ValueError, match="physical_snapshot_required"):
            verify_provider_usage_census_in_session(session, census)
    with inputs.database.session() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        transaction = session.begin_nested()
        row = session.get(RunRoleOutputRecord, census.dispatches[0].input_ref.output_id)
        row.payload_json += " "
        session.flush()
        with pytest.raises(ValueError):
            verify_provider_usage_census_in_session(session, census)
        transaction.rollback()
        transaction = session.begin_nested()
        row = session.get(RunRoleOutputRecord, mapped.output_id)
        session.delete(row)
        session.flush()
        with pytest.raises(ValueError, match="frozen_original_changed"):
            verify_provider_usage_census_in_session(session, census)
        transaction.rollback()
    forged = census.model_dump(mode="json")
    for item in forged["dispatches"]:
        item["input_tokens"] += 1
        item["total_tokens"] += 1
    forged["input_tokens"] += 3
    forged["total_tokens"] += 3
    forged = ProviderConversationUsageCensusV1.model_validate_json(
        observation_json(_rehash(forged))
    )
    with inputs.database.session() as session:
        session.connection().exec_driver_sql("BEGIN")
        before = session.connection().connection.driver_connection.total_changes
        with pytest.raises(ValueError, match="frozen_computation_changed"):
            verify_provider_usage_census_in_session(session, forged)
        assert session.connection().connection.driver_connection.total_changes == before


def test_legacy_sidecar_is_not_backfilled_and_public_usage_is_run_bound(usage_originals, tmp_path):
    inputs, _, census, _ = usage_originals
    maker = quality_fixture.__wrapped__(tmp_path)
    fixture = next(maker)
    try:
        database, request, _, state, clock, _, _, frozen = fixture
        with database.session() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            row, quality = persist(fixture, session)
            saved = read_quality_sidecar_in_session(
                session, row.sidecar_id, selection=frozen.receipt
            )
            assert "authenticated_conversation_usage" not in json.loads(saved.prepared_runtime_json)
            original_payload, original_hash = row.payload_json, row.payload_hash
            # Public shape seam: no published authentication is substituted here.
            refs = saved.proof_rows
            view = ResearchQualityViewV1(
                run_id=request.run_id,
                as_of=clock,
                status="available",
                gate_status="research_only",
                published_at=clock,
                publication_ref=refs[0],
                graph_final_ref=refs[0],
                recipe_selection_ref=saved.recipe_selection_ref,
                quality_sidecar_id=row.sidecar_id,
                decision=quality,
                budget=PreparedQualityBudgetViewV1(
                    max_tool_calls=1,
                    max_subagents=1,
                    max_evidence_rounds=1,
                    max_estimated_cost_usd=1,
                    deadline_at=clock + timedelta(hours=1),
                ),
            )
            assert view.usage is None and view.budget.actual_total_tokens is None
            with pytest.raises(ValidationError, match="usage_publication_invalid"):
                ResearchQualityViewV1.model_validate({**view.model_dump(), "usage": census})
            assert row.payload_json == original_payload and row.payload_hash == original_hash
        # A controlled V3 quality/selection seam reuses the real authenticated
        # provider census. It exercises sidecar writer/readback, not publication
        # permission, installed V3 admission or an actual financial report.
        value = frozen.receipt.model_copy(update={"run_id": census.run_id})
        value = value.model_copy(update={"effective_hash": _effective_hash(value)})
        state["request"]["run_id"] = census.run_id
        quality = quality_for_state(state, quality_context=saved.quality_context)
        raw = observation_json(value.model_dump(mode="json"))
        with inputs.database.session() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            selection_row = RunRoleOutputRecord(
                output_id="controlled-usage-selection",
                run_id=census.run_id,
                role="controlled_usage_selection",
                schema_version=value.schema_version,
                payload_json=raw,
                payload_hash=observation_sha(value.model_dump(mode="json")),
                created_at=census.as_of,
            )
            session.add(selection_row)
            session.flush()
            bound = persist_quality_sidecar_in_session(
                session,
                selection=value,
                recipe_selection_ref=_row_ref(selection_row),
                logical_round=1,
                context=saved.quality_context,
                quality=quality,
                proof_rows=(*census.source_refs, _row_ref(selection_row)),
                prepared_runtime_json=observation_json(
                    {
                        "authenticated_conversation_usage": census.model_dump(mode="json"),
                        "actual_total_tokens": census.total_tokens,
                        "actual_estimated_cost_usd": None,
                    }
                ),
                unknown_receipt_codes=("actual_usage_cost_receipt_unavailable",),
                recorded_at=utcnow(),
            )
            actual = read_quality_sidecar_in_session(session, bound.sidecar_id, selection=value)
            frozen_usage = ProviderConversationUsageCensusV1.model_validate_json(
                observation_json(
                    json.loads(actual.prepared_runtime_json)["authenticated_conversation_usage"]
                )
            )
            assert frozen_usage == census
            public = ResearchQualityViewV1(
                run_id=census.run_id,
                as_of=actual.recorded_at,
                status="available",
                gate_status="research_only",
                published_at=actual.recorded_at,
                publication_ref=actual.recipe_selection_ref,
                graph_final_ref=actual.recipe_selection_ref,
                recipe_selection_ref=actual.recipe_selection_ref,
                quality_sidecar_id=bound.sidecar_id,
                decision=quality,
                budget=view.budget,
                usage=frozen_usage,
            )
            assert public.usage.total_tokens == 30 and public.budget.actual_total_tokens is None
        with pytest.raises(ValidationError, match="usage_publication_invalid"):
            ResearchQualityViewV1(
                run_id=census.run_id,
                as_of=census.as_of,
                status="unavailable",
                reason_code="research_quality_publication_missing",
                usage=census,
            )
    finally:
        maker.close()

"""Real Factory/native protocol/provider receipts/report/unique Action transaction.

The native child backend and its subjective scenarios are synthetic. All SQLite
originals and service writes below are real; no model or HTTP service is called.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import event, select

from packages.contracts_py.decision_hub_contracts.generated.dsh_host_bridge import (
    DshModelDispatchBinding,
)
from packages.contracts_py.decision_hub_contracts.generated.provider_input import (
    ResearchProviderInputSubmit,
    ResearchProviderToolEmissionSubmit,
)
from packages.kernel.decision_hub_kernel.application import provider_input_receipts
from packages.kernel.decision_hub_kernel.application.action_publication import (
    ActionPublicationService,
)
from packages.kernel.decision_hub_kernel.application.commit import CommitDecisionService
from packages.kernel.decision_hub_kernel.application.model_dispatch import ModelDispatchService
from packages.kernel.decision_hub_kernel.application.provider_input_receipts import (
    ProviderInputReceiptService,
    tool_arguments_json,
)
from packages.kernel.decision_hub_kernel.application.research_publication_binding import (
    observation_sha,
)
from packages.kernel.decision_hub_kernel.persistence.action_records import (
    ActionCommandReceiptRecord,
    ActionPlanHeadRecord,
    ActionPlanRevisionRecord,
    DecisionSnapshotRecord,
)
from packages.kernel.decision_hub_kernel.persistence.action_repository import ActionRepository
from packages.kernel.decision_hub_kernel.persistence.db import (
    ArtifactRecord,
    ForecastRecord,
    OutboxRecord,
    RunRecord,
    RunRoleOutputRecord,
)
from packages.kernel.decision_hub_kernel.persistence.research_publication_bundle_records import (
    PublicationBundleJobRecord,
)
from tests.contracts.test_d2_candidate_contract import body_ref, review_request
from tests.decision_product.test_okx_perpetual_original import (
    persisted_original as persisted_original,
)
from tests.decision_product.test_perpetual_native_graph import (
    ROOT,
    build_perpetual_native_graph_case,
    packet_for,
)
from tests.decision_product.test_perpetual_research_binding import AT
from tests.decision_product.test_perpetual_research_binding import binding_case as binding_case


def lawful_packet_for(request, materials):
    packet = packet_for(request, materials)
    synthesis = packet["synthesis"]
    for candidate in synthesis["candidate_set"]["candidates"]:
        candidate["cost_ref"] = materials["cost_policy_ref"]
    synthesis["candidate_set"]["candidate_refs"] = [
        body_ref(row["candidate_id"], row) for row in synthesis["candidate_set"]["candidates"]
    ]
    synthesis["candidate_set_ref"] = body_ref("set", synthesis["candidate_set"])
    synthesis["economic_input_refs"].extend(
        [materials["cost_assumptions_ref"], materials["cost_policy_ref"]]
    )
    packet["allowed_domain"]["candidate_set_ref"] = synthesis["candidate_set_ref"]
    synthesis["allowed_domain_ref"] = body_ref("domain", packet["allowed_domain"])
    packet.update(
        synthesis_ref=body_ref("synth", synthesis),
        allowed_domain_ref=synthesis["allowed_domain_ref"],
    )
    return packet


def authenticate_provider(value, monkeypatch):
    """Real model dispatch/input/emission APIs while the actual Run lease is active."""
    db, context, execution = value["db"], value["context"], value["execution"]
    identity = execution.method.definition.upstream_identity
    from packages.kernel.decision_hub_kernel.persistence import original_preparation

    monkeypatch.setattr(original_preparation, "utcnow", value["clock"])
    params = dict(
        provider="synthetic-native-test",
        model="synthetic-child-backend",
        reasoning_effort=None,
        temperature=None,
        max_tokens=None,
        stop_sequences_hash=None,
    )
    monkeypatch.setattr(provider_input_receipts, "utcnow", value["clock"])
    ModelDispatchService(db, clock=lambda: AT + timedelta(milliseconds=500)).bind(
        DshModelDispatchBinding(
            schema_version="dsh-model-dispatch-binding.v1",
            run_id=context.run_id,
            dsh_session_id=context.dsh_session_id,
            execution_session_id=None,
            parent_session_id=None,
            request_hash=execution.method.request_hash,
            generation=1,
            plugin_build_hash=identity.plugin_build_hash,
            purpose="conversation",
            **params,
        )
    )
    body = dict(
        system="Synthetic provider backend exercising the actual admitted native protocol.",
        messages=[dict(role="user", content=execution.prompt.prompt)],
        tools=[dict(name="decision_hub_synthesis_submit")],
    )
    encoded = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    shared = dict(
        run_id=context.run_id,
        generation=1,
        root_session_id=context.dsh_session_id,
        execution_session_id=context.dsh_session_id,
        dispatch_id="native-publication-dispatch",
        request_hash=execution.method.request_hash,
        plugin_build_hash=identity.plugin_build_hash,
    )
    recorded = ProviderInputReceiptService(
        db, clock=lambda: AT + timedelta(seconds=1)
    ).record_input(
        ResearchProviderInputSubmit(
            schema_version="research-provider-input-submit.v1",
            **shared,
            parent_session_id=None,
            **params,
            input_json=encoded,
            input_hash=hashlib.sha256(encoded.encode()).hexdigest(),
            previous_input_ref=None,
            host_scope=dict(
                host_instance_id=str(uuid4()),
                process_id=os.getpid(),
                started_at=AT,
                hub_base_url="http://test",
                workspace_id="default",
                workspace_cwd_hash=hashlib.sha256(str(ROOT).encode()).hexdigest(),
            ),
        )
    )
    call = value["events"][value["native"].selection.call_event_index]["payload"]["event"]["data"]
    if isinstance(call.get('arguments'), dict) and call['arguments'].get('action') == 'finalize':
        # Two actual Provider argument blocks; neither is falsely classified as
        # the canonical candidate produced later by deterministic preparation.
        prepare_call = next(row['payload']['event']['data'] for row in value['events']
            if row['payload']['event'].get('type') == 'tool/call'
            and row['payload']['event']['data'].get('callId') == call['arguments']['prepared_call_id'])
        for index, selected in enumerate((prepare_call, call)):
            args = selected['arguments']
            if isinstance(args, str):
                args = json.loads(args)
            raw = json.dumps(args, ensure_ascii=False, separators=(',', ':'))
            saved = ProviderInputReceiptService(db, clock=lambda: AT + timedelta(seconds=3 + index)).record_emission(
                ResearchProviderToolEmissionSubmit(schema_version='research-provider-tool-emission-submit.v1', **shared,
                    input_ref=recorded.reference.model_dump(mode='json', include={'output_id','payload_hash'}),
                    call_id=selected['callId'], tool_name=selected['name'], raw_arguments_json=raw,
                    raw_arguments_hash=hashlib.sha256(raw.encode()).hexdigest(),
                    arguments_hash=hashlib.sha256(tool_arguments_json(args).encode()).hexdigest(), block_index=index))
            assert saved.candidate_hash is None
            if index == 0:
                value['provider_prepare_emission'] = saved
            else:
                value.update(provider_input=recorded, provider_emission=saved)
        return
    arguments = call["arguments"]
    if isinstance(arguments, str):
        arguments = json.loads(arguments)
    raw = json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
    emission = ProviderInputReceiptService(
        db, clock=lambda: AT + timedelta(seconds=4)
    ).record_emission(
        ResearchProviderToolEmissionSubmit(
            schema_version="research-provider-tool-emission-submit.v1",
            **shared,
            input_ref=recorded.reference.model_dump(
                mode="json", include={"output_id", "payload_hash"}
            ),
            call_id=call["callId"],
            tool_name=call["name"],
            raw_arguments_json=raw,
            raw_arguments_hash=hashlib.sha256(raw.encode()).hexdigest(),
            arguments_hash=hashlib.sha256(tool_arguments_json(arguments).encode()).hexdigest(),
            block_index=0,
        )
    )
    assert emission.candidate_hash == observation_sha(
        value["native"].synthesis.model_dump(mode="json")
    )
    value.update(provider_input=recorded, provider_emission=emission)


@pytest.fixture
def publication_case(binding_case, tmp_path, monkeypatch):
    return build_perpetual_native_graph_case(
        binding_case,
        tmp_path,
        monkeypatch,
        packet_builder=lawful_packet_for,
        before_graph=lambda value: authenticate_provider(value, monkeypatch),
    )


def financial_counts(db):
    with db.session() as session:
        return [
            session.query(model).count()
            for model in [
                DecisionSnapshotRecord,
                ActionPlanRevisionRecord,
                ActionPlanHeadRecord,
                ActionCommandReceiptRecord,
                OutboxRecord,
            ]
        ], session.query(RunRoleOutputRecord).filter(
            RunRoleOutputRecord.role.like("perpetual_publication_original:%")
        ).count()


@pytest.mark.parametrize(
    "field,value",
    [("qualification", "qualified"), ("kind", "calibrated"), ("kind", "model_estimate")],
)
def test_current_perpetual_method_cannot_self_authorize_model_qualification(field, value):
    """Pure policy test; these copies do not assert archived native authority."""
    from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
        ResearchDecisionSynthesisV2,
    )
    from packages.kernel.decision_hub_kernel.application.perpetual_research_reader import (
        validate_perpetual_probability_method,
    )

    synthesis = ResearchDecisionSynthesisV2.model_validate(review_request()["synthesis"])
    validate_perpetual_probability_method(synthesis)
    estimate = synthesis.market_distribution.direction_distribution.model_copy(
        update={field: value}
    )
    changed = synthesis.model_copy(
        update={
            "market_distribution": synthesis.market_distribution.model_copy(
                update={"direction_distribution": estimate}
            )
        }
    )
    with pytest.raises(ValueError, match="perpetual_probability_model_original_unavailable"):
        validate_perpetual_probability_method(changed)


def assert_actual_report_then_unique_perpetual_action(
    value, monkeypatch, *, horizon="4h", saved_report_example_path=None
):
    db, request = value["db"], value["request"]
    from packages.kernel.decision_hub_kernel.application import commit

    monkeypatch.setattr(commit, "utcnow", lambda: AT + timedelta(seconds=30))
    artifact_id, gate = CommitDecisionService(
        db, clock=lambda: AT + timedelta(seconds=30)
    ).commit_research_result(
        request.run_id,
        request.event_id,
        value["final"].result,
        expected_lease_owner=value["lease_owner"],
        graph_final_ref=value["graph_final_ref"],
    )
    assert CommitDecisionService(
        db, clock=lambda: AT + timedelta(seconds=31)
    ).commit_research_result(
        request.run_id,
        request.event_id,
        value["final"].result,
        expected_lease_owner=value["lease_owner"],
        graph_final_ref=value["graph_final_ref"],
    ) == (artifact_id, gate)
    with db.session() as session:
        run = session.get(RunRecord, request.run_id)
        assert (
            run.artifact_id == artifact_id and run.status == "degraded" and run.lease_owner is None
        )
        artifact = session.get(ArtifactRecord, artifact_id)
        assert artifact and horizon in artifact.headline
        assert session.query(ForecastRecord).filter_by(artifact_id=artifact_id).count() == 0
        assert session.get(PublicationBundleJobRecord, request.run_id) is None
    publisher = ActionPublicationService(db, clock=lambda: AT + timedelta(seconds=40))
    kwargs = dict(
        run_id=request.run_id,
        event_id=request.event_id,
        artifact_id=artifact_id,
        reference=value["graph_final_ref"],
        opportunity_id=value["materials"]["opportunity_id"],
    )
    baseline = financial_counts(db)

    def reject_first_head(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("INSERT INTO ACTION_PLAN_HEADS"):
            raise RuntimeError("injected_first_action_transaction_failure")

    event.listen(db.engine, "before_cursor_execute", reject_first_head)
    try:
        with pytest.raises(RuntimeError, match="injected_first_action_transaction_failure"):
            publisher.publish(**kwargs)
    finally:
        event.remove(db.engine, "before_cursor_execute", reject_first_head)
    assert financial_counts(db) == baseline
    receipt = publisher.publish(**kwargs)
    assert receipt.response["status"] == "published"
    assert publisher.publish(**kwargs) == receipt
    from packages.kernel.decision_hub_kernel.application.perpetual_advice_query import (
        PerpetualAdviceQuery,
    )
    from tests.decision_product.test_perpetual_advice_query import params
    from tests.decision_product.test_perpetual_research_binding import OWNER

    advice = PerpetualAdviceQuery(db, now=lambda: AT + timedelta(seconds=40)).current(
        OWNER, **{**params(value["case"]), "horizon": horizon}
    )
    assert advice["status"] == "available"
    assert advice["snapshot"]["id"] == receipt.response["decision_id"]
    assert advice["comparison"]["executable_contracts"] is None
    assert advice["candidate_probability"]["kind"] == "subjective"
    assert advice["target"]["horizon"] == horizon
    with db.session() as session:
        repository = ActionRepository(session)
        snapshot = repository.get_snapshot(receipt.response["decision_id"])
        assert snapshot.schema_version == "decision-snapshot.v2"
        assert snapshot.bindings.final_target != snapshot.bindings.candidate_target
        originals = session.scalars(
            select(RunRoleOutputRecord).where(
                RunRoleOutputRecord.run_id == request.run_id,
                RunRoleOutputRecord.role.like("perpetual_publication_original:%"),
            )
        ).all()
        hashes = {row.payload_hash for row in originals}
        for name in [
            "comparison",
            "final_target",
            "anchor_rule",
            "anchor_receipt",
            "frozen_forecast",
            "probability_commit",
            "event_probability",
            "market_distribution",
            "native_synthesis",
            "method",
            "allowed_domain",
        ]:
            assert getattr(snapshot.bindings, name).hash in hashes, name
        for branch in receipt.response["branches"]:
            revision = repository.get_revision(branch["revision_id"])
            assert revision.schema_version == "decision-action-plan-revision.v2"
            assert revision.bindings == snapshot.bindings
    from tests.decision_product.test_perpetual_saved_report import (
        assert_saved_report_from_actual_case,
    )

    assert_saved_report_from_actual_case(value, artifact_id, monkeypatch, example_path=saved_report_example_path)
    return receipt, advice


def test_actual_report_then_unique_perpetual_action_transaction_and_original_replay(
    publication_case, monkeypatch
):
    assert_actual_report_then_unique_perpetual_action(publication_case, monkeypatch)

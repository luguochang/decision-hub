"""Real saved settings/raw pages/SQLite admission. No server or model invocation."""

from __future__ import annotations

import importlib
import importlib.util
import json
from datetime import timedelta
from pathlib import Path

import pytest

from packages.contracts_py.decision_hub_contracts import ObservationCreate
from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
    DecisionSettingsCommandV1,
)
from packages.contracts_py.decision_hub_contracts.models import SourceType
from packages.kernel.decision_hub_kernel.application.admission import AdmissionService
from packages.kernel.decision_hub_kernel.application.decision_settings import (
    DecisionSettingsService,
)
from packages.kernel.decision_hub_kernel.application.decision_settings_binding import (
    DecisionSettingsBindingReader,
)
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.application.study import ensure_study
from packages.kernel.decision_hub_kernel.persistence.db import RunRecord, RunRoleOutputRecord
from tests.decision_product.test_okx_perpetual_original import (
    NOW,
)
from tests.decision_product.test_okx_perpetual_original import (
    persisted_original as persisted_original,
)
from tests.decision_product.test_perpetual_close_source import pages
from tests.research.test_bundle_snapshot_read_scope import _prepared_pack

MODULE = "packages.kernel.decision_hub_kernel.application.perpetual_research_binding"
OWNER = "binding-owner"
AT = NOW + timedelta(seconds=10)


def api():
    assert importlib.util.find_spec(MODULE), "immutable perpetual research admission is missing"
    return importlib.import_module(MODULE)


@pytest.fixture
def binding_case(persisted_original):
    db, archive, source, original = persisted_original
    ids = pages(persisted_original)
    fixture = (
        Path(__file__).parents[2] / "apps/decision-desk/src/api/decisionSettingsReceiptFixture.json"
    )
    body = json.loads(fixture.read_text("utf-8"))["intent"]["command"]
    ref = source.metadata_contract_reference(original.capture_id).model_dump(mode="json")
    body["values"]["instrument"] = ref
    body["values"]["position_context"]["instrument_ref"] = ref
    body["values"]["account_snapshot"]["as_of"] = AT.isoformat().replace("+00:00", "Z")
    from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash

    utility = body["values"]["utility_policy"]
    utility["hash"] = canonical_hash(
        {key: value for key, value in utility.items() if key != "hash"}
    )
    settings = DecisionSettingsCommandV1.model_validate(body)
    service = DecisionSettingsService(
        db, now=lambda: AT, binding_reader=DecisionSettingsBindingReader(source, now=lambda: AT)
    )
    service.save(OWNER, settings)
    event_id, _, accepted = AdmissionService(db, clock=lambda: AT).admit(
        ObservationCreate(
            source_id="binding-test",
            source_type=SourceType.transcript,
            source_url="https://example.com/binding-test",
            text="Federal Reserve research observation.",
            observed_at=AT,
            published_at=NOW,
            language="en",
            event_hint="central_bank_speech",
        )
    )
    assert accepted
    run_id, created = RunService(db, clock=lambda: AT).create(
        event_id, strategy_version="research.v1"
    )
    assert created
    with db.session() as session:
        study_id = ensure_study(
            session,
            question_key="perpetual-binding-test",
            title="Test",
            market="crypto",
            instrument="BTC-USDT-SWAP",
            horizon_scope="4h",
            identity_basis="exact_perpetual_decision_v2",
            now=AT,
        )
        session.get(RunRecord, run_id).study_id = study_id
    return dict(
        db=db,
        archive=archive,
        source=source,
        ids=ids,
        settings=settings,
        settings_service=service,
        event_id=event_id,
        run_id=run_id,
    )


def freeze(case):
    service = api().PerpetualResearchBindingService(case["db"], case["source"], now=lambda: AT)
    ref = service.freeze_admission(
        run_id=case["run_id"],
        owner_id=OWNER,
        partition=case["settings"].partition,
        capture_ids=case["ids"],
        as_of=AT,
        valid_until=AT + timedelta(hours=2),
    )
    return service, ref


def test_real_original_admission_reopens_without_task_or_default_account(binding_case):
    case = binding_case
    service, ref = freeze(case)
    with case["db"].session() as session:
        value = service.read_admission_in_session(session, case["run_id"])
        from packages.kernel.decision_hub_kernel.persistence.action_repository import (
            ActionRepository,
        )

        opportunity = ActionRepository(session).get_opportunity(value["opportunity_id"])
        assert opportunity is not None, "actual V2 opportunity is not registered"
        assert opportunity.schema_version == "decision-opportunity.v2"
        assert opportunity.target_spec_hash == value["candidate_target_ref"]["hash"]
    assert value["context"]["horizon"] == "4h"
    assert value["settings_revision"]["values"]["account_snapshot"]["equity"] is None
    assert (
        value["candidate_target"]["threshold_fraction"]
        == value["close_window"]["thresholds"][0]["fraction"]
    )
    assert value["candidate_target"]["method_ref"] != value["method_ref"]
    assert value["product_release_qualified"] is False
    assert ref.hash
    assert freeze(case)[1] == ref
    with case["db"].session() as session:
        assert (
            len(
                session.query(RunRoleOutputRecord)
                .filter_by(run_id=case["run_id"], role="perpetual_research_admission_v1")
                .all()
            )
            == 1
        )


def factory_for(case, tmp_path, *, execution_mode="replay"):
    from apps.hub_worker.research import ResearchRequestFactory
    from packages.kernel.decision_hub_kernel.application.research_binding import (
        PreparedResearchPolicyRefs,
    )

    root, manifest = _prepared_pack(tmp_path)
    with case["db"].session() as session:
        already_admitted = api()._row(session, case["run_id"], api().ADMISSION_ROLE) is not None
    if already_admitted:
        binding_service = api().PerpetualResearchBindingService(case["db"], case["source"], now=lambda: AT)
    else:
        binding_service, _ = freeze(case)
    factory = ResearchRequestFactory(
        case["db"],
        pack_root=root,
        execution_mode=execution_mode,
        allowed_capabilities=(
            tuple(manifest["capability_refs"])
            if execution_mode == "live"
            else ("replay.research", "document.readback", "web.search")
        ),
        prepared_policy_refs=PreparedResearchPolicyRefs(
            gate_policy_ref=manifest["gate_policy_ref"],
            rubric_policy_ref=manifest["evaluation_policy_ref"],
            role_profile_refs=tuple(manifest["role_profile_refs"]),
        ),
        perpetual_binding_service=binding_service,
        clock=lambda: AT,
    )
    return factory, binding_service


def test_factory_freezes_genuine_4h_then_native_materials_reopen(binding_case, tmp_path):
    case = binding_case
    factory, service = factory_for(case, tmp_path)
    request = factory.build(case["event_id"], case["run_id"])
    assert request.output_schema_ref == api().OUTPUT_SCHEMA
    with case["db"].session() as session:
        from packages.kernel.decision_hub_kernel.application.research_binding import (
            read_research_bundle_in_session,
        )

        bundle = read_research_bundle_in_session(session, case["run_id"])
        assert bundle.task.scope.schema_version == "research-task-scope.v2"
        assert bundle.task.scope.horizon_scope == ["4h"]
        materials = api().read_perpetual_research_materials_in_session(session, request)
    assert materials["context"]["horizon"] == "4h"
    assert materials["account_snapshot"]["equity"] is None
    assert materials["economic_input_availability"] == "partial"
    from packages.runtime_adapters.dsh_runtime.profile import build_trusted_web_research_prompt

    prompt = build_trusted_web_research_prompt(request, perpetual_materials=materials)
    encoded = prompt.split("BEGIN_FROZEN_PERPETUAL_MATERIALS_JSON\n")[1].split(
        "\nEND_FROZEN_PERPETUAL_MATERIALS_JSON"
    )[0]
    assert len(encoded.encode()) <= 256_000
    assert factory.build(case["event_id"], case["run_id"]) == request


def test_service_authenticated_binding_endpoint_uses_original_task_generation(
    binding_case, tmp_path
):
    name = "apps.hub_api.perpetual_research_routes"
    assert importlib.util.find_spec(name), (
        "actual service-auth perpetual binding endpoint is missing"
    )
    module = importlib.import_module(name)
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient

    case = binding_case
    factory, service = factory_for(case, tmp_path)
    request = factory.build(case["event_id"], case["run_id"])
    prepared = prepare_execution(case, request)
    app = FastAPI()

    def auth(value):
        if value != "private-test-bridge":
            raise HTTPException(401, "dsh_callback_unauthorized")

    app.include_router(module.perpetual_research_router(case["db"], auth, now=lambda: AT))
    client = TestClient(app)
    route = f"/v1/research/runs/{request.run_id}/decision-binding"
    params = dict(request_hash=prepared.link.request_hash, generation=prepared.link.generation)
    assert client.get(route, params=params).status_code == 401
    reply = client.get(
        route, params=params, headers={"X-Decision-Hub-Bridge-Key": "private-test-bridge"}
    )
    assert reply.status_code == 200, reply.text
    assert reply.json()["context"]["horizon"] == "4h"
    assert reply.json()["binding_ref"]["hash"]
    utility = case["settings"].values.utility_policy
    assert reply.json()["context"]["utility_ref"] == {
        "ref": utility.policy_ref,
        "hash": utility.hash,
    }
    with case["db"].session() as session:
        materials = api().read_perpetual_research_materials_in_session(session, request)
    assert materials["account_snapshot"]["equity"] is None
    assert materials["cost_assumptions"]["unit_notional_usdt"] == "100"
    assert (
        "application/perpetual_cost_assumptions.py"
        in materials["comparison_method"]["dependencies"]
    )
    assert (
        client.get(
            route,
            params={**params, "generation": 2},
            headers={"X-Decision-Hub-Bridge-Key": "private-test-bridge"},
        ).status_code
        == 409
    )


def prepare_execution(case, request):
    from packages.contracts_py.decision_hub_contracts import DshSessionSubmit, DshUpstreamIdentity
    from packages.kernel.decision_hub_kernel.application.dsh_sessions import DshSessionLinkService
    from packages.kernel.decision_hub_kernel.application.research_binding import (
        read_research_bundle_in_session,
    )
    from packages.kernel.decision_hub_kernel.application.research_execution_binding import (
        ResearchExecutionPreparationService,
    )
    from packages.kernel.decision_hub_kernel.application.research_policy_render_v2 import (
        render_prepared_research_policy,
    )
    from packages.kernel.decision_hub_kernel.application.research_request_identity import (
        research_request_hash,
    )
    from packages.runtime_adapters.dsh_runtime.profile import build_trusted_web_research_prompt

    assert RunService(case["db"], clock=lambda: AT).claim_for_execution(
        request.run_id, worker_id="binding-test", lease_seconds=300
    )
    with case["db"].session() as session:
        bundle = read_research_bundle_in_session(session, request.run_id)
        materials = api().read_perpetual_research_materials_in_session(session, request)
        prompt = (
            render_prepared_research_policy(bundle, request.allowed_capabilities)
            + "\n\n"
            + build_trusted_web_research_prompt(request, perpetual_materials=materials)
        )
    digest = research_request_hash(request)
    session_id, request_id = DshSessionLinkService.deterministic_ids(request.run_id, digest)
    submit = DshSessionSubmit(
        schema_version="dsh-session-submit.v1",
        run_id=request.run_id,
        request_hash=digest,
        deterministic_session_id=session_id,
        deterministic_request_id=request_id,
        workspace_ref="decision-hub://workspace/default",
        prompt_ref=f"hub://runs/{request.run_id}/prompts/1",
        agent_preset="decision-research",
        permission_ref="decision-hub://permissions/research-only",
        deadline_at=request.deadline_at,
        model_step_timeout_ms=request.execution_budget.per_model_step_timeout_seconds * 1000,
        max_tool_calls=request.execution_budget.max_tool_calls,
        generation=1,
    )
    identity = DshUpstreamIdentity(
        source_commit="1" * 40,
        source_version="test-perpetual-binding",
        package_versions={},
        plugin_build_hash="2" * 64,
    )
    return ResearchExecutionPreparationService(case["db"], clock=lambda: AT).prepare(
        request,
        submit,
        identity,
        prompt,
        runtime_id="dsh-web",
        runtime_version="dsh-web-test-perpetual-binding+" + "2" * 12,
        profile_ref="decision-research.web",
        prompt_policy_hash="3" * 64,
        expected_lease_owner="binding-test",
    )


@pytest.mark.parametrize("changed", ["archive", "settings_head"])
def test_reopened_admission_rejects_changed_real_original_without_writes(binding_case, changed):
    case = binding_case
    service, ref = freeze(case)
    if changed == "archive":
        with case["db"].session() as session:
            from packages.kernel.decision_hub_kernel.persistence.db import RawCaptureRecord

            row = session.get(RawCaptureRecord, case["ids"][0])
            digest = row.body_hash
        path = case["archive"].root / digest[:2] / digest
        path.write_bytes(path.read_bytes() + b" ")
    else:
        command = changed_risk(case, "change-risk")
        case["settings_service"].save(OWNER, command)
    with case["db"].session() as session:
        before = session.connection().exec_driver_sql("SELECT total_changes()").scalar_one()
        with pytest.raises(ValueError):
            service.read_admission_in_session(session, case["run_id"])
        assert session.connection().exec_driver_sql("SELECT total_changes()").scalar_one() == before


def test_stale_prepared_factory_cannot_freeze_partial_task(binding_case, tmp_path, monkeypatch):
    case = binding_case
    factory, service = factory_for(case, tmp_path)
    prepared = service.prepare_for_factory(case["event_id"], case["run_id"])
    changed = changed_risk(case, "change-before-task")
    case["settings_service"].save(OWNER, changed)
    monkeypatch.setattr(service, "prepare_for_factory", lambda event_id, run_id: prepared)
    with pytest.raises(ValueError, match="readset_changed"):
        factory.build(case["event_id"], case["run_id"])
    with case["db"].session() as session:
        assert (
            not session.query(RunRoleOutputRecord)
            .filter_by(run_id=case["run_id"], role="research_task_request")
            .all()
        )
        assert (
            not session.query(RunRoleOutputRecord)
            .filter_by(run_id=case["run_id"], role="perpetual_research_task_binding_v1")
            .all()
        )


def changed_risk(case, request_id):
    from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash

    body = case["settings"].model_dump(mode="json")
    body.update(request_id=request_id, expected_state_version=1)
    utility = body["values"]["utility_policy"]
    utility["allowed_sides"] = ["long"]
    utility["hash"] = canonical_hash(
        {key: value for key, value in utility.items() if key != "hash"}
    )
    return DecisionSettingsCommandV1.model_validate(body)


def test_durable_worker_selects_saved_admission_with_prepared_service(binding_case, tmp_path):
    from apps.hub_worker.research import DurableResearchWorker
    from packages.kernel.decision_hub_kernel.application.research_binding import (
        PreparedResearchPolicyRefs,
    )

    case = binding_case
    service, reference = freeze(case)
    root, manifest = _prepared_pack(tmp_path)
    refs = PreparedResearchPolicyRefs(
        gate_policy_ref=manifest["gate_policy_ref"],
        rubric_policy_ref=manifest["evaluation_policy_ref"],
        role_profile_refs=tuple(manifest["role_profile_refs"]),
    )
    with pytest.raises(ValueError, match="perpetual_prepared_factory_required"):
        DurableResearchWorker(
            case["db"],
            object(),
            pack_root=root,
            perpetual_binding_service=service,
            clock=lambda: AT,
        )
    worker = DurableResearchWorker(
        case["db"],
        object(),
        pack_root=root,
        prepared_pack_root=root,
        prepared_policy_refs=refs,
        perpetual_binding_service=service,
        execution_mode="replay",
        allowed_capabilities=("replay.research", "document.readback", "web.search"),
        clock=lambda: AT,
    )
    selected, requires_selection = worker._factory_for_run(case["run_id"])
    assert selected is worker.prepared_factory
    assert requires_selection is True
    assert selected.perpetual_binding_service is service
    request = worker._prepare_request_policy(selected, case["event_id"], case["run_id"])
    assert request.output_schema_ref == api().OUTPUT_SCHEMA
    # A reopened already-frozen Run must select the same actual prepared path.
    assert worker._factory_for_run(case["run_id"]) == (selected, True)

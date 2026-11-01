"""Signed user command -> existing durable Run queue, using genuine raw readers."""

import hashlib
import importlib
import importlib.util
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from apps.hub_api.decision_settings_routes import decision_settings_router
from apps.hub_api.local_owner_session import LocalOwnerSessionAuthority
from packages.kernel.decision_hub_kernel.persistence.action_records import DecisionOpportunityRecord
from packages.kernel.decision_hub_kernel.persistence.db import (
    EventRecord,
    OutboxRecord,
    ProductCommandRecord,
    ResearchCommandRecord,
    RunRecord,
    StudyRecord,
)
from packages.contracts_py.decision_hub_contracts import ResearchRunCommand
from packages.kernel.decision_hub_kernel.application.research_observability import (
    ResearchCommandService,
)
from packages.query_views.research import ResearchQueryService
from tests.decision_product.test_okx_perpetual_original import (
    persisted_original as persisted_original,
)
from tests.decision_product.test_perpetual_research_binding import AT, OWNER
from tests.decision_product.test_perpetual_research_binding import binding_case as binding_case

MODULE = "packages.kernel.decision_hub_kernel.application.perpetual_research_entry"
TOKEN = "test-only-private-perpetual-research-owner-credential"
ORIGIN = "http://127.0.0.1:8123"
BASE = "/v1/decision-settings"
PACK_ROOT = Path(__file__).resolve().parents[2] / "packs" / "crypto_macro"


class RecordedCloses:
    def __init__(self, case):
        self.case, self.calls = case, 0

    async def __call__(self, *, inst_id):
        from packages.kernel.decision_hub_kernel.application.perpetual_close_source import (
            CollectedPerpetualCloseWindow,
        )
        from tests.decision_product.test_okx_perpetual_original import NOW

        assert inst_id == "BTC-USDT-SWAP"
        self.calls += 1
        return CollectedPerpetualCloseWindow(self.case["ids"], NOW, AT)


def command(case):
    from packages.kernel.decision_hub_kernel.application.decision_settings import (
        DecisionSettingsService,
    )

    with case["db"].session() as session:
        current = DecisionSettingsService.current_in_session(
            session, OWNER, case["settings"].partition
        )
    return dict(
        schema_version="perpetual-research-command.v1",
        request_id="research-new-command",
        partition=case["settings"].partition.model_dump(mode="json"),
        expected_settings_revision=dict(
            ref=current.revision.revision_id, hash=current.revision.payload_hash
        ),
        expected_settings_state_version=current.state_version,
        horizon="4h",
    )


def client_for(case, *, pair=True, clock=None):
    clock = clock or (lambda: AT)
    assert importlib.util.find_spec(MODULE), (
        "actual signed perpetual research admission service is missing"
    )
    module = importlib.import_module(MODULE)
    from packages.kernel.decision_hub_kernel.application.perpetual_research_binding import (
        PerpetualResearchBindingService,
    )

    collector = RecordedCloses(case)
    service = module.PerpetualResearchEntryService(
        case["db"],
        PerpetualResearchBindingService(case["db"], case["source"], now=clock),
        total_deadline_seconds=3600,
        collect_window=collector,
        now=clock,
    )
    authority = LocalOwnerSessionAuthority(
        signing_key=b"x" * 32,
        pairing_sha256=hashlib.sha256(TOKEN.encode()).digest(),
        owner_identity=OWNER,
        domain_pack_ref="crypto_macro.v1",
        origin=ORIGIN,
    )
    app = FastAPI()
    app.include_router(
        decision_settings_router(
            case["db"],
            authority=authority,
            source=case["source"],
            now=clock,
            research_service=service,
        )
    )
    client = TestClient(app, headers={"Origin": ORIGIN})
    if pair:
        paired = client.post(BASE + "/owner-session", json={"pairing_token": TOKEN})
        assert paired.status_code == 200
        client.headers["X-Settings-CSRF"] = paired.json()["csrf_token"]
    return client, service, collector


def counts(case):
    with case["db"].session() as session:
        return tuple(
            session.scalar(select(func.count()).select_from(model))
            for model in (
                EventRecord,
                StudyRecord,
                RunRecord,
                ProductCommandRecord,
                DecisionOpportunityRecord,
                OutboxRecord,
            )
        )


def test_signed_command_atomically_queues_exact_4h_and_original_reopens(binding_case):
    case = binding_case
    client, service, collector = client_for(case)
    body = command(case)
    before = counts(case)
    reply = client.post(
        BASE + "/research", json=body, headers={"Idempotency-Key": body["request_id"]}
    )
    assert reply.status_code == 200, reply.text
    receipt = reply.json()
    assert receipt["status"] == "queued" and receipt["financial_qualified"] is False
    after = counts(case)
    assert after == tuple(number + 1 for number in before)
    with case["db"].session() as session:
        run = session.get(RunRecord, receipt["run_id"])
        study = session.get(StudyRecord, run.study_id)
        assert (study.instrument, study.horizon_scope, study.identity_basis) == (
            "BTC-USDT-SWAP",
            "4h",
            "exact_perpetual_decision_v2",
        )
        assert run.status == "admitted"
        from packages.kernel.decision_hub_kernel.application.perpetual_research_binding import (
            read_perpetual_admission_identity_in_session,
        )

        admission, ref = read_perpetual_admission_identity_in_session(session, run.run_id)
        assert ref == receipt["admission_ref"]
        assert admission["event_id"] == run.event_id == receipt["event_id"]
        assert (
            admission["settings_revision"]["revision_id"]
            == body["expected_settings_revision"]["ref"]
        )
    assert (
        client.post(
            BASE + "/research", json=body, headers={"Idempotency-Key": body["request_id"]}
        ).json()
        == receipt
    )
    client.close()
    reopened, _, _ = client_for(case)
    assert reopened.get(BASE + "/research/commands/" + body["request_id"]).json() == receipt
    changed = {**body, "expected_settings_state_version": 2}
    assert (
        reopened.post(
            BASE + "/research", json=changed, headers={"Idempotency-Key": body["request_id"]}
        ).status_code
        == 409
    )
    assert counts(case) == after
    assert collector.calls == 1


def test_exact_perpetual_profile_blocks_generic_retry_and_recheck_without_child(binding_case):
    case = binding_case
    client, _, _ = client_for(case)
    body = command(case)
    reply = client.post(
        BASE + "/research", json=body, headers={"Idempotency-Key": body["request_id"]}
    )
    assert reply.status_code == 200, reply.text
    run_id = reply.json()["run_id"]
    with case["db"].session() as session:
        run = session.get(RunRecord, run_id)
        run.status = "failed"
        run.error_code = "controlled_failure"
        run.finished_at = AT
        run.updated_at = AT

    detail = ResearchQueryService(case["db"], pack_root=PACK_ROOT).get(run_id)
    assert detail is not None
    assert detail.run.research_profile == "perpetual_decision_v2"

    commands = ResearchCommandService(case["db"], clock=lambda: AT)
    retry = ResearchRunCommand(
        schema_version="research-run-command.v1",
        request_id="generic-retry-must-not-create-child",
        command="retry",
        reason="The exact perpetual study must return to saved settings.",
    )
    before_runs = counts(case)[2]
    rejected = commands.apply(run_id, retry, owner_id=OWNER)
    replay = commands.apply(run_id, retry, owner_id=OWNER)
    assert (rejected.status, rejected.target_run_id) == ("rejected", None)
    assert (replay.status, replay.target_run_id) == ("rejected", None)

    with case["db"].session() as session:
        run = session.get(RunRecord, run_id)
        run.status = "completed"
        run.error_code = None
        run.updated_at = AT
    recheck = commands.apply(
        run_id,
        ResearchRunCommand(
            schema_version="research-run-command.v1",
            request_id="generic-recheck-must-not-create-child",
            command="recheck",
            reason="The exact perpetual study must start again from saved settings.",
        ),
        owner_id=OWNER,
    )
    assert (recheck.status, recheck.target_run_id) == ("rejected", None)
    assert counts(case)[2] == before_runs
    with case["db"].session() as session:
        rows = session.query(ResearchCommandRecord).filter_by(run_id=run_id).all()
        assert [(row.command, row.status, row.target_run_id) for row in rows] == [
            ("retry", "rejected", None),
            ("recheck", "rejected", None),
        ]


def test_auth_and_saved_head_fail_before_collection_or_any_business_write(binding_case):
    case = binding_case
    body = command(case)
    anonymous, _, anonymous_collector = client_for(case, pair=False)
    before = counts(case)
    assert anonymous.post(BASE + "/research", json=body).status_code == 403
    client, _, collector = client_for(case)
    csrf = client.headers.pop("X-Settings-CSRF")
    assert client.post(BASE + "/research", json=body).status_code == 403
    client.headers["X-Settings-CSRF"] = csrf
    assert (
        client.post(
            BASE + "/research", json=body, headers={"Origin": "https://foreign.example"}
        ).status_code
        == 403
    )
    assert (
        client.post(
            BASE + "/research", json=body, headers={"Idempotency-Key": "different"}
        ).status_code
        == 400
    )
    stale = {**body, "expected_settings_state_version": 2}
    reply = client.post(
        BASE + "/research", json=stale, headers={"Idempotency-Key": stale["request_id"]}
    )
    assert (
        reply.status_code == 409
        and reply.json()["detail"] == "perpetual_research_settings_state_conflict"
    )
    assert collector.calls == anonymous_collector.calls == 0
    assert counts(case) == before


@pytest.mark.parametrize("changed", ["archive", "settings_head", "outbox_failure"])
def test_prepared_entry_rejects_substitution_and_rolls_back_whole_intent(
    binding_case, monkeypatch, changed
):
    from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
        PerpetualResearchCommandV1,
    )
    from packages.kernel.decision_hub_kernel.application.perpetual_close_source import (
        CollectedPerpetualCloseWindow,
    )
    from tests.decision_product.test_okx_perpetual_original import NOW
    from tests.decision_product.test_perpetual_research_binding import changed_risk

    case = binding_case
    _, service, _ = client_for(case)
    body = PerpetualResearchCommandV1.model_validate(command(case))
    issued = service.prepare(OWNER, body, CollectedPerpetualCloseWindow(case["ids"], NOW, AT))
    if changed == "archive":
        from packages.kernel.decision_hub_kernel.persistence.db import RawCaptureRecord

        with case["db"].session() as session:
            digest = session.get(RawCaptureRecord, case["ids"][0]).body_hash
        path = case["archive"].root / digest[:2] / digest
        path.write_bytes(path.read_bytes() + b" ")
    elif changed == "settings_head":
        case["settings_service"].save(OWNER, changed_risk(case, "change-before-entry-commit"))
    else:
        from packages.kernel.decision_hub_kernel.application.outbox import OutboxService

        def fail_after_business_insert(*args, **kwargs):
            raise ValueError("test_outbox_failure")

        monkeypatch.setattr(OutboxService, "enqueue_subject", fail_after_business_insert)
    before = counts(case)
    with pytest.raises(ValueError):
        service.commit(OWNER, body, issued)
    assert counts(case) == before
    assert service.receipt(OWNER, body.request_id) is None


def test_signed_returned_run_is_claimed_by_existing_queue_and_frozen_by_real_worker(
    binding_case, tmp_path
):
    from apps.hub_worker.research import DurableResearchWorker
    from packages.kernel.decision_hub_kernel.application.research_binding import (
        PreparedResearchPolicyRefs,
        read_research_bundle_in_session,
    )
    from packages.kernel.decision_hub_kernel.application.run import RunService
    from packages.runtime_adapters.perpetual_research_entry import (
        configured_perpetual_research_services,
    )
    from tests.research.test_bundle_snapshot_read_scope import _prepared_pack

    case = binding_case
    with case["db"].session() as session:
        session.get(RunRecord, case["run_id"]).status = "completed"
    client, _, _ = client_for(case)
    body = command(case)
    reply = client.post(
        BASE + "/research", json=body, headers={"Idempotency-Key": body["request_id"]}
    )
    assert reply.status_code == 200, reply.text
    receipt = reply.json()
    claim = RunService(case["db"], clock=lambda: AT).claim_next(
        strategy_version="research.v1", worker_id="real-entry-worker", lease_seconds=300
    )
    assert claim is not None and claim.run_id == receipt["run_id"]
    # This is the actual normal API/worker shared constructor, including local pack budget.
    configured = configured_perpetual_research_services(
        case["db"], source=case["source"], now=lambda: AT
    )
    assert configured.entry.total_deadline_seconds > 0
    root, manifest = _prepared_pack(tmp_path)
    worker = DurableResearchWorker(
        case["db"],
        object(),
        pack_root=root,
        prepared_pack_root=root,
        prepared_policy_refs=PreparedResearchPolicyRefs(
            gate_policy_ref=manifest["gate_policy_ref"],
            rubric_policy_ref=manifest["evaluation_policy_ref"],
            role_profile_refs=tuple(manifest["role_profile_refs"]),
        ),
        perpetual_binding_service=configured.binding,
        execution_mode="replay",
        allowed_capabilities=("replay.research", "document.readback", "web.search"),
        clock=lambda: AT,
    )
    selected, requires_selection = worker._factory_for_run(claim.run_id)
    assert selected is worker.prepared_factory and requires_selection
    request = worker._prepare_request_policy(selected, claim.event_id, claim.run_id)
    assert request.output_schema_ref.endswith("research_decision_synthesis_v2")
    with case["db"].session() as session:
        bundle = read_research_bundle_in_session(session, claim.run_id)
        assert bundle.task.scope.horizon_scope == ["4h"]
        assert bundle.task.scope.admission_ref.model_dump(mode="json") == receipt["admission_ref"]
    assert client.get(BASE + "/research/commands/" + body["request_id"]).json() == receipt


def test_signed_paper_entry_uses_exact_persisted_account_projection(binding_case):
    from decimal import Decimal

    from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
        DecisionSettingsCommandV1,
        DecisionSettingsPartitionV1,
    )
    from packages.kernel.decision_hub_kernel.application.decision_settings import (
        DecisionSettingsService,
    )
    from packages.kernel.decision_hub_kernel.application.decision_settings_binding import (
        DecisionSettingsBindingReader,
    )
    from packages.kernel.decision_hub_kernel.application.perpetual_paper import (
        CreatePaperAccount,
        PerpetualPaperService,
    )
    from packages.kernel.decision_hub_kernel.application.perpetual_research_binding import (
        ADMISSION_ROLE,
        _read,
    )
    from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash

    case = dict(binding_case)
    values = case["settings"].values
    partition = DecisionSettingsPartitionV1.model_validate(
        {
            **case["settings"].partition.model_dump(mode="json"),
            "account_kind": "paper",
            "account_id": "explicit-paper",
        }
    )
    paper = PerpetualPaperService(case["db"], case["source"], now=lambda: AT)
    paper.create_account(
        OWNER,
        CreatePaperAccount(
            request_id="explicit-paper-create",
            account_id=partition.account_id,
            instrument_refs=(values.instrument,),
            profile=values.execution_profile,
            initial_cash_usdt=Decimal("2500"),
            simulation_acknowledged=True,
            execution_mode="manual_after_visible",
        ),
    )
    with case["db"].session() as session:
        account, position = paper.account_reader(session, OWNER, partition)
    body = case["settings"].model_dump(mode="json")
    body.update(
        request_id="paper-settings-for-entry",
        expected_state_version=0,
        partition=partition.model_dump(mode="json"),
    )
    body["values"].update(
        account_snapshot=account.model_dump(mode="json"),
        position_context=position.model_dump(mode="json"),
    )
    utility = body["values"]["utility_policy"]
    utility["account_ref"] = account.original.model_dump(mode="json")
    utility["hash"] = canonical_hash(
        {key: value for key, value in utility.items() if key != "hash"}
    )
    settings = DecisionSettingsCommandV1.model_validate(body)
    service = DecisionSettingsService(
        case["db"],
        now=lambda: AT,
        binding_reader=DecisionSettingsBindingReader(
            case["source"], now=lambda: AT, paper_account_reader=paper.account_reader
        ),
    )
    service.save(OWNER, settings)
    case["settings"] = settings
    client, _, _ = client_for(case)
    request = command(case)
    reply = client.post(
        BASE + "/research", json=request, headers={"Idempotency-Key": request["request_id"]}
    )
    assert reply.status_code == 200, reply.text
    with case["db"].session() as session:
        admission, _ = _read(session, reply.json()["run_id"], ADMISSION_ROLE)
        frozen = admission["settings_revision"]["values"]
        assert frozen["account_snapshot"] == account.model_dump(mode="json")
        assert frozen["position_context"] == position.model_dump(mode="json")
        assert frozen["account_snapshot"]["equity"] == "2500"
    assert reply.json()["financial_qualified"] is False

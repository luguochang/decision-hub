import json

import pytest

from packages.kernel.decision_hub_kernel.application.dsh_sessions import DshSessionLinkService
from packages.kernel.decision_hub_kernel.application.research_method import (
    ROLE,
    ResearchMethodService,
)
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.persistence.db import RunRoleOutputRecord
from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError
from packages.runtime_adapters.dsh_runtime.web_runtime import DshWebResearchRuntime
from tests.dsh_native.test_dsh_web_runtime import FakeHostClient, _database, _request


@pytest.fixture
def context(tmp_path):
    db = _database(tmp_path / "method.sqlite3")
    run_id, _ = RunService(db).create("method-test")
    host = FakeHostClient()
    runtime = DshWebResearchRuntime(DshSessionLinkService(db), host, poll_interval_seconds=0.001)
    return db, run_id, host, runtime


async def test_receipt_exists_before_host_submission_and_is_stable(context):
    db, run_id, host, runtime = context
    original = host.submit
    receipts = []

    async def submit(request):
        receipt = ResearchMethodService(db).get(run_id)
        assert receipt is not None
        assert receipt["request_hash"] == request.request_hash
        assert db.get_run_record(run_id).runtime_version == receipt["definition"]["runtime_version"]
        receipts.append(receipt)
        return await original(request)

    host.submit = submit
    await runtime.execute(_request(run_id))
    await runtime.execute(_request(run_id))
    assert receipts[0] == receipts[1]
    definition = receipts[0]["definition"]
    assert definition["runtime_id"] == "dsh"
    assert definition["profile_ref"] == "decision-research.web.v1"
    assert not definition["evolution_pointer_consumed"]
    assert not definition["provider_configuration_attested"]
    assert definition["candidate_id"] is None
    assert len(definition["prompt_policy_hash"]) == 64
    assert "evidence-input" not in json.dumps(receipts[0])
    with db.session() as session:
        assert session.query(RunRoleOutputRecord).filter_by(role=ROLE).count() == 1


async def test_saved_request_pack_is_bound_before_host_submission(context):
    from packages.kernel.decision_hub_kernel.application.research_pack import ResearchPackService

    db, run_id, host, runtime = context
    pack = ResearchPackService(db).freeze(run_id, {"manifest": {"version": "test"}})
    original = host.submit

    async def submit(request):
        method = ResearchMethodService(db).get(run_id)
        assert method["definition"]["request_pack_hash"] == pack["definition_hash"]
        return await original(request)

    host.submit = submit
    await runtime.execute(_request(run_id))
    await runtime.execute(_request(run_id))
    assert len(host.submit_calls) == 2
    from packages.kernel.decision_hub_kernel.persistence.db import utcnow
    from packages.query_views.evolution_readiness import evolution_readiness
    from tools.ops.audit_research_method import audit

    assert evolution_readiness(db, utcnow()).research_method["request_pack_status"] == "verified"
    checked = audit(db, run_id)["checks"]
    assert checked["request_pack_matches_method"]
    assert checked["request_pack_saved_before_method"]
    with db.session() as session:
        row = session.query(RunRoleOutputRecord).filter_by(
            run_id=run_id, role="research_request_pack"
        ).one()
        row.payload_json = "{}"
    assert evolution_readiness(db, utcnow()).research_method["request_pack_status"] == "invalid"
    with pytest.raises(ValueError, match="research_pack_receipt_corrupt"):
        audit(db, run_id)


async def test_restarted_worker_cannot_change_prompt_policy_before_resubmit(context, monkeypatch):
    db, run_id, host, runtime = context
    await runtime.execute(_request(run_id))
    before = ResearchMethodService(db).get(run_id)
    monkeypatch.setattr(
        "packages.runtime_adapters.dsh_runtime.web_runtime.PROMPT_POLICY_HASH", "a" * 64
    )
    restarted = DshWebResearchRuntime(DshSessionLinkService(db), host, poll_interval_seconds=0.001)
    with pytest.raises(AgentExecutionError) as error:
        await restarted.execute(_request(run_id))
    assert error.value.error_code == "research_method_binding_failed"
    assert len(host.submit_calls) == 1
    assert ResearchMethodService(db).get(run_id) == before


async def test_receipt_corruption_is_detected_before_host_submission(context):
    db, run_id, host, runtime = context
    await runtime.execute(_request(run_id))
    with db.session() as session:
        row = session.query(RunRoleOutputRecord).filter_by(role=ROLE).one()
        row.payload_json = row.payload_json.replace('"candidate_id":null', '"candidate_id":"fake"')
    with pytest.raises(AgentExecutionError) as error:
        await runtime.execute(_request(run_id))
    assert error.value.error_code == "research_method_binding_failed"
    assert len(host.submit_calls) == 1


async def test_preexisting_sessions_are_not_retroactively_certified(context):
    db, run_id, host, runtime = context
    request = _request(run_id)
    submit = runtime._submission(request)
    readiness = await host.readiness()
    runtime.links.reserve(submit, readiness.upstream_identity)
    await runtime.execute(request)
    assert ResearchMethodService(db).get(run_id) is None
    assert len(host.submit_calls) == 1


async def test_readiness_reports_saved_method_without_claiming_deployment(context):
    from packages.kernel.decision_hub_kernel.persistence.db import utcnow
    from packages.query_views.evolution_readiness import evolution_readiness

    db, run_id, host, runtime = context
    await runtime.execute(_request(run_id))
    view = evolution_readiness(db, utcnow()).research_method
    assert view["recorded_receipts"] == 1
    assert not view["deployment_connected"]
    assert view["latest_receipt_status"] == "verified"
    with db.session() as session:
        row = session.query(RunRoleOutputRecord).filter_by(role=ROLE).one()
        row.payload_json = "{}"
    assert evolution_readiness(db, utcnow()).research_method["latest_receipt_status"] == "invalid"


async def test_round_selection_receipt_may_change_but_context_policy_cannot(context, monkeypatch):
    db, run_id, host, runtime = context
    await runtime.execute(_request(run_id).model_copy(update={"task_policy_ref": "round-one"}))
    before = ResearchMethodService(db).get(run_id)
    await runtime.execute(_request(run_id).model_copy(update={"task_policy_ref": "round-two"}))
    assert ResearchMethodService(db).get(run_id) == before
    monkeypatch.setattr(
        "packages.kernel.decision_hub_kernel.application.research_method.CONTEXT_POLICY_HASH",
        "a" * 64,
    )
    with pytest.raises(AgentExecutionError):
        await runtime.execute(_request(run_id))
    assert len(host.submit_calls) == 2


async def test_readonly_audit_distinguishes_submission_from_completed_research(context):
    from datetime import UTC, datetime

    from tools.ops.audit_research_method import audit

    db, run_id, host, runtime = context
    assert audit(db, run_id)["status"] == "method_not_recorded"
    original = host.submit

    async def current_time_submit(request):
        accepted = await original(request)
        return accepted.model_copy(update={"accepted_at": datetime.now(UTC)})

    host.submit = current_time_submit
    await runtime.execute(_request(run_id))
    before = ResearchMethodService(db).get(run_id)
    result = audit(db, run_id)
    assert result["status"] == "submission_receipt_verified"
    assert result["checks"]["saved_before_host_acceptance"] is True
    assert not result["result_saved"]  # Runtime-only test has not committed a product report.
    assert not result["deployment_connected"]
    assert ResearchMethodService(db).get(run_id) == before

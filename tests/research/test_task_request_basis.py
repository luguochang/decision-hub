from datetime import timedelta

import pytest

from apps.hub_worker.research import ResearchRequestFactory
from packages.contracts_py.decision_hub_contracts import EvidenceCandidate, RunStatus
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchEvidenceService,
)
from packages.kernel.decision_hub_kernel.application.research_task import (
    ROLE,
    ResearchTaskService,
)
from packages.kernel.decision_hub_kernel.application.research_value import (
    ResearchValueEvaluationService,
)
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.persistence.db import RunRoleOutputRecord
from packages.orchestration.langgraph.graphs.agentic_research_graph import requirements_for_round
from packages.query_views.research import ResearchQueryService
from tests.dsh_native.test_dsh_web_runtime import _request
from tests.evolution.test_research_worker import NOW, PACK_ROOT, _admitted_run, _candidate
from tests.research.query_basis_helpers import save_control_request


@pytest.mark.parametrize("freeze_full_task", [False, True])
def test_unexecutable_requirement_is_never_erased_by_round_filtering(tmp_path, freeze_full_task):
    db, event, run = _admitted_run(tmp_path)
    request = _request(run).model_copy(update={"event_id": event, "pit_cutoff_at": NOW})
    first = request.evidence_requirements[0].model_copy(update={
        "preferred_capabilities": ["web.fetch"], "allowed_fallbacks": [],
    })
    excluded = first.model_copy(update={
        "requirement_id": "unreachable_source", "importance": "soft",
        "preferred_capabilities": ["web.search"],
    })
    request = request.model_copy(update={
        "allowed_capabilities": ["web.fetch"], "evidence_requirements": [first, excluded],
    })
    if freeze_full_task:
        ResearchTaskService(db, clock=lambda: NOW).freeze(request)
    filtered = requirements_for_round(
        request.evidence_requirements, {}, allowed_capabilities=request.allowed_capabilities
    )
    assert [r.requirement_id for r in filtered] == [first.requirement_id]
    save_control_request(
        db, request.model_copy(update={"evidence_requirements": filtered}), NOW, full_task=False
    )
    ResearchEvidenceService(db).accept_candidates(
        run_id=run, capability_id="web.fetch",
        candidates=[EvidenceCandidate.model_validate(_candidate(request))],
        requirements={r.requirement_id: r for r in request.evidence_requirements}, cutoff_at=NOW,
    )
    RunService(db, clock=lambda: NOW).set_status(run, RunStatus.failed)
    query = ResearchQueryService(db, pack_root=PACK_ROOT)
    value = ResearchValueEvaluationService(db, pack_root=PACK_ROOT).evaluate_current(run)
    detail = query.get(run)
    if freeze_full_task:
        assert detail.run.coverage_basis == value.coverage_basis == "saved_task_request"
        assert value.hard_coverage_ratio == 1 and value.soft_coverage_ratio == 0
        assert [g.requirement_id for g in detail.run.coverage.gaps] == ["unreachable_source"]
        assert len(query.observability_view(run).readiness) == 2
    else:
        assert detail.run.coverage_basis == value.coverage_basis == "round_request_only"
        assert detail.run.coverage is None and value.hard_coverage_ratio is None
        assert query.observability_view(run).readiness == []
        with pytest.raises(ValueError, match="missing_before_execution"):
            ResearchTaskService(db, clock=lambda: NOW).freeze(request)
    assert len(detail.evidence) == 1


def test_task_input_is_immutable_and_rebuild_keeps_original_time_boundary(tmp_path):
    db, event, run = _admitted_run(tmp_path)
    factory = ResearchRequestFactory(db, pack_root=PACK_ROOT, clock=lambda: NOW)
    request = factory.build(event, run)
    service = ResearchTaskService(db, clock=lambda: NOW)
    service.freeze(request)
    assert service.freeze(request) == request
    with pytest.raises(ValueError, match="research_task_request_changed"):
        service.freeze(request.model_copy(update={"deadline_at": NOW + timedelta(days=1)}))
    reloaded = ResearchRequestFactory(
        db, pack_root=PACK_ROOT, clock=lambda: NOW + timedelta(days=1)
    )
    assert reloaded.build(event, run) == request
    with db.session() as session:
        assert session.query(RunRoleOutputRecord).filter_by(run_id=run, role=ROLE).count() == 1


@pytest.mark.parametrize("damage", ["hash", "late"])
def test_invalid_full_task_does_not_fall_back_to_valid_round_subset(tmp_path, damage):
    db, event, run = _admitted_run(tmp_path)
    request = _request(run).model_copy(update={"event_id": event, "pit_cutoff_at": NOW})
    save_control_request(db, request, NOW)
    with db.session() as session:
        row = session.query(RunRoleOutputRecord).filter_by(run_id=run, role=ROLE).one()
        if damage == "hash":
            row.payload_hash = "f" * 64
        else:
            row.created_at = NOW + timedelta(seconds=1)
    RunService(db, clock=lambda: NOW + timedelta(seconds=2)).set_status(run, RunStatus.failed)
    value = ResearchValueEvaluationService(db, pack_root=PACK_ROOT).evaluate_current(run)
    assert value.coverage_basis == "invalid" and value.hard_coverage_ratio is None


@pytest.mark.parametrize("change", ["coherent_edit", "legacy_missing"])
def test_real_checkpoint_recovery_checks_complete_request_before_invoking_runtime(tmp_path, change):
    import asyncio
    import hashlib
    import json

    from packages.kernel.decision_hub_kernel.application.research_method import canonical_recipe
    from packages.orchestration.langgraph.executor import LangGraphResearchExecutor
    from tests.evolution.test_research_worker import FakeResearchRuntime

    db, event, run = _admitted_run(tmp_path)
    request = ResearchRequestFactory(
        db, pack_root=PACK_ROOT, execution_mode="replay", clock=lambda: NOW
    ).build(event, run)
    executor = LangGraphResearchExecutor(
        db, FakeResearchRuntime(), checkpoint_path=tmp_path / "checkpoint.sqlite3",
        clock=lambda: NOW
    )
    result = asyncio.run(executor.execute(request))
    assert asyncio.run(executor.has_checkpoint(run))
    recovered = asyncio.run(executor.resume(run))
    assert recovered["final_result"] == result["final_result"]
    if change == "legacy_missing":
        with db.session() as session:
            row = session.query(RunRoleOutputRecord).filter_by(run_id=run, role=ROLE).one()
            session.delete(row)
        assert asyncio.run(executor.resume(run))["final_result"] == result["final_result"]
        assert ResearchTaskService(db).get(run) is None
        return
    # A coherently rehashed ledger edit still conflicts with the original checkpoint.
    with db.session() as session:
        row = session.query(RunRoleOutputRecord).filter_by(run_id=run, role=ROLE).one()
        body = json.loads(row.payload_json)
        body["request"]["evidence_requirements"][0]["importance"] = "soft"
        body["request_hash"] = hashlib.sha256(
            canonical_recipe(body["request"]).encode()
        ).hexdigest()
        row.payload_json = canonical_recipe(body)
        row.payload_hash = hashlib.sha256(row.payload_json.encode()).hexdigest()
    with pytest.raises(ValueError, match="checkpoint_mismatch"):
        asyncio.run(executor.resume(run))


@pytest.mark.parametrize("damage", [None, "readback_missing"])
def test_recover_original_checkpoint_records_import_time_without_backdating(tmp_path, damage):
    import asyncio
    import json

    from packages.kernel.decision_hub_kernel.persistence.db import utcnow
    from packages.orchestration.langgraph.executor import LangGraphResearchExecutor
    from tests.evolution.test_research_worker import FakeResearchRuntime
    from tools.ops.recover_research_tasks import recover

    db, event, run = _admitted_run(tmp_path)
    request = ResearchRequestFactory(
        db, pack_root=PACK_ROOT, execution_mode="replay", clock=lambda: NOW
    ).build(event, run)
    path = tmp_path / "original.sqlite3"
    executor = LangGraphResearchExecutor(db, FakeResearchRuntime(), checkpoint_path=path)
    asyncio.run(executor.execute(request))
    RunService(db).set_status(run, RunStatus.failed)
    with db.session() as session:
        session.delete(session.query(RunRoleOutputRecord).filter_by(run_id=run, role=ROLE).one())
    if damage == "readback_missing":
        import sqlite3

        with sqlite3.connect(path) as connection:
            connection.execute("DELETE FROM checkpoints WHERE parent_checkpoint_id IS NOT NULL")
        with pytest.raises(ValueError, match="readback_missing"):
            recover(db, path, [run], apply=True)
        assert ResearchTaskService(db).get(run) is None
        return
    plan = recover(db, path, [run])
    assert plan["appended"] == 0 and plan["items"][0]["status"] == "recoverable"
    assert ResearchTaskService(db).get(run) is None
    start = utcnow()
    assert recover(db, path, [run], apply=True)["appended"] == 1
    assert recover(db, path, [run], apply=True)["appended"] == 0
    assert ResearchTaskService(db).get(run) == request
    value = ResearchValueEvaluationService(db, pack_root=PACK_ROOT).evaluate_current(run)
    assert value.coverage_basis == "saved_checkpoint"
    with db.session() as session:
        row = session.query(RunRoleOutputRecord).filter_by(run_id=run, role=ROLE).one()
        from packages.kernel.decision_hub_kernel.persistence.db import as_utc

        assert as_utc(row.created_at) >= start
        assert json.loads(row.payload_json)["source"]["checkpoint_id"]

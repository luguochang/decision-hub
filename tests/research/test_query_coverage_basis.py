import shutil
from datetime import timedelta

import pytest
import yaml

from packages.contracts_py.decision_hub_contracts import EvidenceCandidate, RunStatus
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchEvidenceService,
)
from packages.kernel.decision_hub_kernel.application.research_value import (
    ResearchValueEvaluationService,
)
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.persistence.db import DshSessionPromptRecord
from packages.query_views.research import ResearchQueryService
from tests.dsh_native.test_dsh_web_runtime import _request
from tests.evolution.test_research_worker import NOW, PACK_ROOT, _admitted_run, _candidate
from tests.research.query_basis_helpers import save_control_request


@pytest.mark.parametrize("future", [False, True])
@pytest.mark.parametrize("mode", ["replay", "live"])
def test_query_surfaces_share_saved_requirements_and_cutoff(tmp_path, future, mode):
    db, event, run = _admitted_run(tmp_path)
    cutoff = NOW if mode == "replay" else NOW + timedelta(seconds=10)
    request = _request(run).model_copy(update={
        "event_id": event, "execution_mode": mode, "deadline_at": NOW + timedelta(seconds=10),
        "pit_cutoff_at": NOW if mode == "replay" else NOW + timedelta(minutes=2),
    })
    save_control_request(db, request, NOW)
    candidate = EvidenceCandidate.model_validate(_candidate(request))
    if future:
        candidate = candidate.model_copy(update={"received_at": NOW + timedelta(minutes=1)})
    ResearchEvidenceService(db).accept_candidates(
        run_id=run, capability_id="replay.research", candidates=[candidate],
        requirements={r.requirement_id: r for r in request.evidence_requirements},
        cutoff_at=NOW + timedelta(minutes=2),
    )
    RunService(db, clock=lambda: NOW + timedelta(days=1)).set_status(
        run, RunStatus.failed, error_code="provider_timeout"
    )
    # The current Pack asks for a completely different set of semantic facts.
    # Editing it must not change this old Run's single frozen requirement.
    root = tmp_path / "pack"
    shutil.copytree(PACK_ROOT, root)
    policy = root / "evidence/source_manifest.yaml"
    body = yaml.safe_load(policy.read_text(encoding="utf-8"))
    body["requirements"][0]["minimum_independent_sources"] = 9
    policy.write_text(yaml.safe_dump(body), encoding="utf-8")
    query = ResearchQueryService(db, pack_root=root)
    detail = query.get(run)
    listed = query.list()[0]
    business = query.business_status(run)
    observe = query.observability_view(run)
    evaluation = ResearchValueEvaluationService(db, pack_root=root).evaluate_current(run)
    expected = 0 if future else 1
    assert detail.run.coverage.hard_coverage_ratio == expected
    assert listed.coverage == detail.run.coverage
    assert business.hard_coverage_ratio == expected
    assert evaluation.hard_coverage_ratio == expected
    assert detail.rounds[0].coverage == detail.run.coverage
    assert detail.run.coverage_basis == observe.coverage_basis == "saved_task_request"
    assert detail.run.coverage_cutoff_at == observe.coverage_cutoff_at == cutoff
    assert len(observe.readiness) == 1
    assert observe.readiness[0].requirement_id == "event_identity"
    assert len(detail.evidence) == 1 and len(detail.rounds) == 1
    # Nullable coverage exists only in the query DTO, never the execution contract.
    from packages.contracts_py.decision_hub_contracts import ResearchRound, ResearchRoundView

    raw = detail.rounds[0].model_dump(mode="json")
    raw["coverage"] = None
    assert ResearchRoundView.model_validate(raw).coverage is None
    with pytest.raises(ValueError):
        ResearchRound.model_validate(raw)


@pytest.mark.parametrize("damage", ["missing", "hash"])
def test_unverifiable_basis_preserves_material_and_tools(tmp_path, damage):
    db, event, run = _admitted_run(tmp_path)
    request = _request(run).model_copy(update={"event_id": event, "pit_cutoff_at": NOW})
    if damage != "missing":
        save_control_request(db, request, NOW, full_task=False)
        with db.session() as session:
            session.get(DshSessionPromptRecord, (run, 1)).request_hash = "f" * 64
    ResearchEvidenceService(db).accept_candidates(
        run_id=run, capability_id="replay.research",
        candidates=[EvidenceCandidate.model_validate(_candidate(request))],
        requirements={r.requirement_id: r for r in request.evidence_requirements}, cutoff_at=NOW,
    )
    from packages.kernel.decision_hub_kernel.application.research_observability import (
        ResearchObservabilityService,
    )
    from tests.research.test_research_observability import _trace

    for kind in ("tool_started", "tool_completed"):
        ResearchObservabilityService(db, clock=lambda: NOW).append_trace(
            _trace(run, event_type=kind, summary="Recorded capability call").model_copy(update={
                "reference_type": "capability_call", "reference_id": "tool-worker-1",
                "status": "succeeded" if kind == "tool_completed" else "running",
            })
        )
    RunService(db, clock=lambda: NOW).set_status(
        run, RunStatus.failed, error_code="provider_timeout"
    )
    query = ResearchQueryService(db, pack_root=PACK_ROOT)
    detail = query.get(run)
    assert detail.run.coverage is None and query.list()[0].coverage is None
    assert query.business_status(run).hard_coverage_ratio is None
    assert query.observability_view(run).readiness == []
    assert len(detail.evidence) == len(detail.rounds) == 1
    assert detail.rounds[0].coverage is None
    assert len(detail.rounds[0].tool_invocations) == 1
    assert all(p.status != "sufficient" for p in detail.role_packets)
    assert all("不能据此认定没有缺口" in p.conclusion for p in detail.role_packets)


@pytest.mark.parametrize("origin", ["storage", "source_registry"])
def test_business_status_preserves_registered_error_origins(tmp_path, origin):
    from packages.contracts_py.decision_hub_contracts import ErrorProvenance
    from packages.kernel.decision_hub_kernel.application.research_observability import (
        ResearchObservabilityService,
    )
    from tests.research.test_research_observability import _trace

    db, _event, run = _admitted_run(tmp_path)
    RunService(db, clock=lambda: NOW).set_status(run, RunStatus.failed, error_code="provider_error")
    ResearchObservabilityService(db, clock=lambda: NOW).append_trace(
        _trace(run, event_type="tool_failed", summary="Durable failure").model_copy(update={
            "error_code": "provider_error", "status": "failed",
            "error": ErrorProvenance(
                error_code="provider_error", origin=origin, cause_code="provider_read_failed",
                capability_id="web.fetch", tool_call_id="call-1", retryable=False,
                deadline_ms=None,
            ),
        })
    )
    value = ResearchQueryService(db, pack_root=PACK_ROOT).business_status(run)
    assert value.status == "failed" and value.failures[0].origin == origin

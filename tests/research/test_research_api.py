# pyright: reportPrivateUsage=false
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apps.hub_api.main import create_app
from apps.hub_worker.research import DurableResearchWorker
from packages.kernel.decision_hub_kernel.application.commit import CommitDecisionService
from packages.kernel.decision_hub_kernel.application.research_observability import (
    ResearchObservabilityService,
)
from packages.query_views.research import ResearchQueryService
from tests.evolution.test_research_worker import (
    NOW,
    PACK_ROOT,
    FakeResearchRuntime,
    ResearchRequestFactory,
    _admitted_run,
    _result,
)
from tests.research.test_research_observability import _trace


def test_research_list_detail_and_resumable_sse_use_normalized_views(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DECISION_HUB_LLM_ENABLED", "0")
    database, event_id, run_id = _admitted_run(tmp_path)
    request = ResearchRequestFactory(database, pack_root=PACK_ROOT, clock=lambda: NOW).build(
        event_id, run_id
    )
    trace = ResearchObservabilityService(database, clock=lambda: NOW)
    trace.append_trace(_trace(run_id, event_type="session_started", summary="Started"))
    trace.append_trace(_trace(run_id, event_type="plan_created", summary="Plan ready"))
    CommitDecisionService(database, clock=lambda: NOW).commit_research_result(
        run_id, event_id, _result(request)
    )

    with TestClient(create_app(database, source_connectors=[])) as client:
        listed = client.get("/v1/research/runs")
        detail = client.get(f"/v1/research/runs/{run_id}")
        business = client.get(f"/v1/dsh/sessions/{run_id}/business-status")
        events = client.get(
            f"/v1/research/runs/{run_id}/events?after=1",
            headers={"Last-Event-ID": "1"},
        )

    assert listed.status_code == 200
    assert run_id in {item["run_id"] for item in listed.json()}
    assert detail.status_code == 200
    assert business.status_code == 200
    assert business.json()["schema_version"] == "dsh-business-status.v1"
    assert business.json()["gate_status"] == "research_only"
    assert business.json()["coverage_status"] == "insufficient"
    assert detail.json()["run"]["latest_sequence_no"] == 2
    assert detail.json()["run"]["schema_version"] == "research-run-view.v2"
    assert detail.json()["run"]["admission_origin"] == "manual"
    assert detail.json()["run"]["priority"] == "high"
    assert events.status_code == 200
    assert events.headers["content-type"].startswith("text/event-stream")
    assert "id: 2" in events.text
    assert '"event_type": "plan_created"' in events.text
    assert "id: 1" not in events.text


def test_dsh_business_status_returns_404_for_unknown_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DECISION_HUB_LLM_ENABLED", "0")
    database, _event_id, _run_id = _admitted_run(tmp_path)
    with TestClient(create_app(database, source_connectors=[])) as client:
        response = client.get("/v1/dsh/sessions/run-missing/business-status")
    assert response.status_code == 404
    assert response.json()["detail"] == "dsh_business_status_not_found"


def test_research_owner_command_requires_identity_and_idempotency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DECISION_HUB_LLM_ENABLED", "0")
    database, _event_id, run_id = _admitted_run(tmp_path)
    payload = {
        "schema_version": "research-run-command.v1",
        "request_id": "cancel-api-1",
        "command": "cancel",
        "reason": "Stop the obsolete request.",
    }

    with TestClient(create_app(database, source_connectors=[])) as client:
        forbidden = client.post(
            f"/v1/research/runs/{run_id}/commands",
            headers={"Idempotency-Key": "cancel-api-1"},
            json=payload,
        )
        mismatch = client.post(
            f"/v1/research/runs/{run_id}/commands",
            headers={"Idempotency-Key": "wrong", "X-Owner-Id": "owner"},
            json=payload,
        )
        accepted = client.post(
            f"/v1/research/runs/{run_id}/commands",
            headers={"Idempotency-Key": "cancel-api-1", "X-Owner-Id": "owner"},
            json=payload,
        )
        duplicate = client.post(
            f"/v1/research/runs/{run_id}/commands",
            headers={"Idempotency-Key": "cancel-api-1", "X-Owner-Id": "owner"},
            json=payload,
        )

    assert forbidden.status_code == 403
    assert mismatch.status_code == 400
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "accepted"
    assert duplicate.status_code == 200
    assert duplicate.json()["status"] == "already_applied"


def test_failed_run_without_result_projects_error_provenance(tmp_path: Path) -> None:
    database, _event_id, run_id = _admitted_run(tmp_path)
    worker = DurableResearchWorker(
        database,
        FakeResearchRuntime(fail=True),
        pack_root=PACK_ROOT,
        clock=lambda: NOW,
    )

    report = asyncio.run(worker.tick())
    detail = ResearchQueryService(database, pack_root=PACK_ROOT).get(run_id)

    assert report is not None
    assert report.status == "failed"
    assert detail is not None
    assert detail.run.status == "failed"
    assert detail.run.failure is not None
    assert detail.run.failure.error_code == "provider_timeout"
    assert detail.run.failure.retryable is True
    assert detail.rounds == []
    assert detail.evidence == []

    business = ResearchQueryService(database, pack_root=PACK_ROOT).business_status(run_id)
    assert business is not None
    assert [item.model_dump(mode="json") for item in business.failures] == [
        {
            "capability_id": "research.runtime",
            "error_code": "provider_timeout",
            "origin": "orchestration",
            "cause_code": None,
            "retryable": True,
        }
    ]

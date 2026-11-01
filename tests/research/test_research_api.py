# pyright: reportPrivateUsage=false
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apps.hub_api.main import create_app
from apps.hub_worker.research import DurableResearchWorker
from packages.contracts_py.decision_hub_contracts import (
    EvidenceCandidate,
    EvidenceGap,
    FactEnvelope,
    ResearchReview,
    ResearchSessionResult,
    ResearchTraceEvent,
)
from packages.kernel.decision_hub_kernel.application.commit import (
    CommitDecisionService,
    _research_result_is_publishable,
)
from packages.kernel.decision_hub_kernel.application.event_watch import EventWatchService
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchEvidenceService,
)
from packages.kernel.decision_hub_kernel.application.research_observability import (
    ResearchObservabilityService,
)
from packages.kernel.decision_hub_kernel.application.research_review import (
    independent_review_receipt_hash,
    research_result_content_hash,
)
from packages.kernel.decision_hub_kernel.application.research_task import ResearchTaskService
from packages.kernel.decision_hub_kernel.decision.daily_strategy import compute_daily_strategy
from packages.kernel.decision_hub_kernel.persistence.db import ResearchResultRecord, RunRecord
from packages.provider_adapters.research import CryptoMacroFactPack
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


def test_directional_result_requires_trusted_calibration_not_model_status(
    tmp_path: Path,
) -> None:
    database, event_id, run_id = _admitted_run(tmp_path)
    request = ResearchRequestFactory(database, pack_root=PACK_ROOT, clock=lambda: NOW).build(
        event_id, run_id
    )
    base = _result(request)
    coverage = base.final_coverage.model_copy(
        update={
            "status": "sufficient",
            "covered_requirement_ids": ["event_identity"],
            "hard_coverage_ratio": 1.0,
            "gaps": [],
            "conflicts": [],
        }
    )
    accepted_review = ResearchReview(
        review_id="review-risk-gate-1",
        role="independent_reviewer",
        target_claim=base.causal_case.thesis if base.causal_case else "Directional claim.",
        evidence_refs=[base.evidence_candidates[0].evidence_id],
        result_hash="1" * 64,
        challenge="The observed move may reflect prior positioning.",
        resolution="accepted",
        limitations=[],
        created_at=NOW,
    )
    uncalibrated = base.model_copy(
        update={"final_coverage": coverage, "reviews": [accepted_review]}
    )
    assert _research_result_is_publishable(uncalibrated) is False

    calibrated_horizons = [
        item.model_copy(update={"probability_status": "calibrated"})
        for item in uncalibrated.horizons
    ]
    missing_review = uncalibrated.model_copy(
        update={"horizons": calibrated_horizons, "reviews": []}
    )
    assert _research_result_is_publishable(missing_review) is False

    reviewable = missing_review.model_copy(update={"reviews": [accepted_review]})
    assert _research_result_is_publishable(reviewable) is False


def test_model_calibration_label_is_downgraded_before_persistence(tmp_path: Path) -> None:
    database, event_id, run_id = _admitted_run(tmp_path)
    request = ResearchRequestFactory(database, pack_root=PACK_ROOT, clock=lambda: NOW).build(
        event_id, run_id
    )
    result = _result(request)
    result = result.model_copy(
        update={
            "horizons": [
                item.model_copy(update={"probability_status": "calibrated"})
                for item in result.horizons
            ]
        }
    )

    CommitDecisionService(database, clock=lambda: NOW).commit_research_result(
        run_id, event_id, result
    )
    detail = ResearchQueryService(database, pack_root=PACK_ROOT).get(run_id)

    assert detail is not None
    assert {item.probability_status for item in detail.horizons} == {"uncalibrated"}
    assert all(
        "No kernel calibration receipt" in (item.confidence_cap_reason or "")
        for item in detail.horizons
    )


def test_completed_report_projects_soft_gaps_as_supplemental_not_blocking(
    tmp_path: Path,
) -> None:
    database, event_id, run_id = _admitted_run(tmp_path)
    request = ResearchRequestFactory(database, pack_root=PACK_ROOT, clock=lambda: NOW).build(
        event_id, run_id
    )
    base = _result(request)
    soft_gap = EvidenceGap(
        requirement_id="macro_transmission",
        importance="soft",
        reason_code="stale",
        query_hint="Refresh the rates and USD context when a live source is available.",
        attempted_capabilities=["market.macro_intraday"],
        blocks_directional_output=False,
    )
    coverage = base.final_coverage.model_copy(
        update={
            "status": "sufficient",
            "covered_requirement_ids": ["event_identity"],
            "hard_coverage_ratio": 1.0,
            "soft_coverage_ratio": 0.5,
            "gaps": [soft_gap],
            "conflicts": [],
        }
    )
    completed = base.model_copy(
        update={
            "final_coverage": coverage,
            "evidence_candidates": [
                item.model_copy(update={"quality": "accepted"}) for item in base.evidence_candidates
            ],
            "reviews": [],
            "horizons": [item.model_copy(update={"action": "no_trade"}) for item in base.horizons],
        }
    )
    target_claim = (
        completed.causal_case.thesis if completed.causal_case else "Non-directional claim."
    )
    evidence_refs = [completed.evidence_candidates[0].evidence_id]
    challenge = "Live macro context is stale but remains supplemental."
    receipt_hash = independent_review_receipt_hash(
        role="independent_reviewer",
        target_claim=target_claim,
        evidence_refs=evidence_refs,
        challenge=challenge,
        resolution="accepted",
        limitations=[],
    )
    accepted_review = ResearchReview(
        review_id=f"review_{receipt_hash[:32]}",
        role="independent_reviewer",
        target_claim=target_claim,
        evidence_refs=evidence_refs,
        result_hash=research_result_content_hash(completed),
        challenge=challenge,
        resolution="accepted",
        limitations=[],
        created_at=NOW,
    )
    completed = completed.model_copy(update={"reviews": [accepted_review]})
    # This direct publication path must carry the same actual frozen inputs
    # as the Worker/Graph, rather than relying on the caller's completed flag.
    ResearchTaskService(database, clock=lambda: NOW).freeze(request)
    ResearchEvidenceService(database).freeze_decision_snapshot(
        run_id=run_id,
        evidence_refs=[],
        cutoff_at=NOW,
    )
    database.save_role_output(
        run_id,
        "independent_reviewer",
        accepted_review.model_dump(mode="json"),
        "research-review.native-attested.v1",
    )

    artifact_id, gate = CommitDecisionService(database, clock=lambda: NOW).commit_research_result(
        run_id, event_id, completed
    )
    detail = ResearchQueryService(database, pack_root=PACK_ROOT).get(run_id)

    assert artifact_id
    assert gate.status.value == "publish"
    assert detail is not None
    assert detail.action_summary.artifact_status == "saved"
    assert detail.action_summary.data_status == "partial"
    assert detail.action_summary.blocking_gap_ids == []
    assert detail.action_summary.recommendation == "wait_for_confirmation"
    assert detail.action_summary.headline == ("研究报告已完成，当前未形成经过验证的方向优势")
    assert "directional_value_unvalidated" in detail.action_summary.warning_codes
    assert "补充信息缺口：实时宏观传导数据" in detail.action_summary.rationale
    assert "仍缺少" not in detail.action_summary.rationale


def test_legacy_daily_receipt_without_immutable_scope_stays_unqualified(
    tmp_path: Path,
) -> None:
    """An event-only legacy report cannot assign its BTC daily fact to an unknown Run asset."""

    database, event_id, run_id = _admitted_run(tmp_path)
    request = ResearchRequestFactory(database, pack_root=PACK_ROOT, clock=lambda: NOW).build(
        event_id, run_id
    )
    base = _result(request)
    evidence = base.evidence_candidates[0].model_copy(
        update={"quality": "accepted", "freshness_status": "fresh"}
    )
    bars = [
        {
            "date": f"2026-01-{index + 1:02d}",
            "open": 99 + index,
            "high": 101 + index,
            "low": 98 + index,
            "close": 100 + index,
            "volume": 1000 + index,
        }
        for index in range(80)
    ]
    evidence_id = evidence.evidence_id
    receipt = compute_daily_strategy(bars, evidence_refs=[evidence_id]).as_json()
    fact = FactEnvelope(
        schema_version="fact-envelope.v1",
        fact_id="fact-daily-direction",
        evidence_id=evidence_id,
        requirement_id="market_daily_strategy",
        metric_family="crypto.spot.history",
        field="daily_strategy_receipt",
        instrument="BTCUSDT",
        venue="gate",
        value="daily-trend-v0.1",
        unit="json",
        window_start_at=datetime(2026, 1, 1, tzinfo=UTC),
        window_end_at=datetime(2026, 3, 22, tzinfo=UTC),
        event_offset=None,
        observed_at=datetime(2026, 3, 22, tzinfo=UTC),
        received_at=datetime(2026, 3, 22, tzinfo=UTC),
        published_at=None,
        source_id="gate-public",
        independence_group="gate-public:daily",
        quality="accepted",
        delay_class="historical",
        payload_schema_ref="strategy-receipt.v1",
        payload_hash="b" * 64,
        attributes={"daily_strategy_receipt_json": receipt},
    )
    result = base.model_copy(
        update={
            "evidence_candidates": [evidence],
            "facts": [fact],
            "final_coverage": base.final_coverage.model_copy(
                update={
                    "status": "insufficient",
                    "hard_coverage_ratio": 0.5,
                    "gaps": [
                        EvidenceGap(
                            requirement_id="derivatives_crowding",
                            importance="hard",
                            reason_code="tool_unavailable",
                            query_hint="funding and open interest",
                            attempted_capabilities=["market.crypto_derivatives"],
                            blocks_directional_output=True,
                        )
                    ],
                }
            ),
        }
    )
    ResearchEvidenceService(database).accept_candidates(
        run_id=run_id,
        capability_id="replay.research",
        candidates=[evidence],
        requirements={
            item.requirement_id: item
            for item in CryptoMacroFactPack.from_pack(PACK_ROOT).all_contract_requirements()
        },
        cutoff_at=NOW,
    )

    CommitDecisionService(database, clock=lambda: NOW).commit_research_result(
        run_id, event_id, result
    )
    detail = ResearchQueryService(database, pack_root=PACK_ROOT).get(run_id)

    assert detail is not None
    assert detail.action_summary.recommendation == "do_not_trade"
    assert "7日价格规则倾向" not in detail.action_summary.headline
    assert detail.action_summary.trade_authorized is False
    assert "derivatives_crowding" in detail.action_summary.blocking_gap_ids
    assert detail.action_summary.research_outlook is None
    assert "daily_outlook_scope_unverified" in detail.action_summary.warning_codes
    assert "日线参考未与原任务资产核对" in detail.action_summary.rationale
    assert detail.action_summary.market_view is not None
    assert detail.action_summary.market_view.direction == "unresolved"
    assert detail.action_summary.execution_view is not None
    assert detail.action_summary.execution_view.status == "blocked"
    # The original BTC receipt and accepted evidence are still available.
    assert any(item.evidence_id == evidence_id for item in detail.evidence)
    with database.session() as session:
        original = session.get(ResearchResultRecord, run_id)
        assert original is not None
        saved = ResearchSessionResult.model_validate_json(original.payload_json)
        assert saved.facts[0].attributes["daily_strategy_receipt_json"] == receipt



def test_unattested_synthesis_keeps_accepted_evidence_as_research_only(
    tmp_path: Path,
) -> None:
    """A synthesis attestation failure must not hide the evidence already acquired."""

    database, event_id, run_id = _admitted_run(tmp_path)
    request = ResearchRequestFactory(database, pack_root=PACK_ROOT, clock=lambda: NOW).build(
        event_id, run_id
    )
    result = _result(request).model_copy(
        update={
            "status": "degraded",
            "causal_case": None,
            "evidence_candidates": [
                item.model_copy(update={"quality": "accepted"})
                for item in _result(request).evidence_candidates
            ],
            "stop_reason": _result(request).stop_reason.model_copy(
                update={
                    "code": "critical_data_unavailable",
                    "detail": "Synthesis attestation failed after trusted evidence acquisition.",
                    "bounded": True,
                    "remaining_hard_gaps": [],
                }
            ),
        }
    )
    ResearchEvidenceService(database).accept_candidates(
        run_id=run_id,
        capability_id="replay.research",
        candidates=[EvidenceCandidate.model_validate(item) for item in result.evidence_candidates],
        requirements={
            item.requirement_id: item
            for item in CryptoMacroFactPack.from_pack(PACK_ROOT).all_contract_requirements()
        },
        cutoff_at=NOW,
    )

    artifact_id, gate = CommitDecisionService(database, clock=lambda: NOW).commit_research_result(
        run_id, event_id, result
    )
    detail = ResearchQueryService(database, pack_root=PACK_ROOT).get(run_id)

    assert artifact_id
    assert gate.status.value == "research_only"
    assert detail is not None
    assert detail.causal_case is None
    assert len(detail.evidence) == 1
    assert detail.action_summary.recommendation == "do_not_trade"
    assert detail.action_summary.trade_authorized is False


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
    assert detail.json()["action_summary"]["recommendation"] == "do_not_trade"
    assert detail.json()["action_summary"]["trade_authorized"] is False
    assert [item["kind"] for item in detail.json()["process_steps"]] == [
        "intake",
        "plan",
        "acquire",
        "assess",
        "synthesize",
        "review",
        "replan",
        "gate",
    ]
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
    assert detail.action_summary.recommendation == "unavailable"
    assert detail.action_summary.trade_authorized is False
    assert detail.process_steps[-1].status == "failed"
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


def test_completed_run_does_not_project_item_level_stale_rejection_as_failure(
    tmp_path: Path,
) -> None:
    database, event_id, run_id = _admitted_run(tmp_path)
    request = ResearchRequestFactory(database, pack_root=PACK_ROOT, clock=lambda: NOW).build(
        event_id, run_id
    )
    ResearchObservabilityService(database, clock=lambda: NOW).append_trace(
        ResearchTraceEvent.model_validate(
            {
                "schema_version": "research-trace-event.v1",
                "run_id": run_id,
                "research_session_id": f"product:{run_id}",
                "sequence_no": 0,
                "event_type": "evidence_rejected",
                "occurred_at": NOW.isoformat(),
                "stage": "acquiring_evidence",
                "summary": "Rejected stale background evidence.",
                "reference_type": "evidence",
                "reference_id": "ev-stale",
                "status": "denied",
                "error_code": "evidence_stale",
            }
        )
    )
    CommitDecisionService(database, clock=lambda: NOW).commit_research_result(
        run_id, event_id, _result(request)
    )

    detail = ResearchQueryService(database, pack_root=PACK_ROOT).get(run_id)

    assert detail is not None
    assert detail.run.status == "research_only"
    assert detail.run.failure is None


def test_product_research_routes_expose_inbox_observability_and_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DECISION_HUB_LLM_ENABLED", "0")
    database, _event_id, run_id = _admitted_run(tmp_path)
    report = asyncio.run(
        DurableResearchWorker(
            database,
            FakeResearchRuntime(),
            pack_root=PACK_ROOT,
            clock=lambda: NOW,
        ).tick()
    )
    assert report is not None and report.artifact_id is not None

    with TestClient(create_app(database, source_connectors=[])) as client:
        inbox = client.get("/v1/research/inbox")
        observability = client.get(f"/v1/research/runs/{run_id}/observability")
        evaluation = client.get(f"/v1/research/runs/{run_id}/value-evaluation")

    assert inbox.status_code == 200
    item = next(row for row in inbox.json()["items"] if row["run_id"] == run_id)
    assert item["status"] == "research_only"
    assert item["report_available"] is True
    assert item["notification_status"] == "pending"
    assert observability.status_code == 200
    assert observability.json()["run_id"] == run_id
    assert observability.json()["cost"]["status"] == "partial"
    assert "subscription" in observability.json()["cost"]["unknown_components"]
    assert evaluation.status_code == 200
    assert evaluation.json()["mode"] == "research_only"
    assert all(row["brier_score"] is None for row in evaluation.json()["outcomes"])


def test_watch_without_run_is_visible_without_fabricated_run_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DECISION_HUB_LLM_ENABLED", "0")
    database, event_id, run_id = _admitted_run(tmp_path)
    with database.session() as session:
        session.query(RunRecord).filter_by(run_id=run_id).delete()
    EventWatchService(database, clock=lambda: NOW).ensure_watch(
        event_id=event_id,
        source_id="official-calendar",
        event_family="central_bank_speech",
        scheduled_at=NOW.replace(hour=14),
    )

    with TestClient(create_app(database, source_connectors=[])) as client:
        response = client.get("/v1/research/inbox")

    assert response.status_code == 200
    assert response.json()["items"][0]["status"] == "watching"
    assert response.json()["items"][0]["run_id"] is None


@pytest.mark.parametrize(
    ("reason", "message"),
    [
        ("cancelled_by_owner", "用户主动停止"),
        ("scheduled_recheck_superseded", "被更新的研究替代"),
        ("stale_review_queue_cleanup", "过期复查"),
        ("study_tracking_paused", "主题暂停自动复查"),
        ("unrecognised_reason", "未记录具体停止原因"),
    ],
)
def test_cancelled_task_preserves_real_stop_reason_instead_of_claiming_owner_cancel(
    tmp_path, reason, message
):
    database, _, run_id = _admitted_run(tmp_path)
    with database.session() as session:
        run = session.get(RunRecord, run_id)
        run.status, run.error_code, run.finished_at = "cancelled", reason, NOW
    detail = ResearchQueryService(database, pack_root=PACK_ROOT).get(run_id)
    assert detail.run.status == "cancelled"
    assert detail.run.stop_reason.code == "cancelled"
    assert message in detail.run.stop_reason.detail
    if reason != "cancelled_by_owner":
        assert "用户主动" not in detail.run.stop_reason.detail
    assert detail.run.artifact_id is None


@pytest.mark.parametrize("focus", ["", "分段关注点\n" + "美元与资金流、" * 40 + "最后的失效条件"])
def test_saved_focus_is_complete_but_list_title_is_bounded(tmp_path, focus):
    from packages.kernel.decision_hub_kernel.application.research_intake import (
        LATEST_ADVICE_SOURCE_ID,
        LATEST_ADVICE_TEMPLATE,
    )
    from packages.kernel.decision_hub_kernel.persistence.db import ObservationRecord

    database, event_id, run_id = _admitted_run(tmp_path)
    original = LATEST_ADVICE_TEMPLATE + ("\n用户补充关注点：" + focus if focus else "")
    with database.session() as session:
        observation = session.query(ObservationRecord).filter_by(event_id=event_id).first()
        observation.source_id = LATEST_ADVICE_SOURCE_ID
        observation.text = original
    client = TestClient(create_app(database, source_connectors=[]))
    response = client.get(f"/v1/research/runs/{run_id}")
    assert response.status_code == 200
    detail = response.json()
    assert detail["research_focus"] == focus
    assert len(detail["run"]["event_title"]) <= 74
    if focus:
        assert detail["run"]["event_title"].endswith("...")
        assert detail["research_focus"].endswith("最后的失效条件")
    with database.session() as session:
        saved = session.query(ObservationRecord).filter_by(event_id=event_id).first()
        assert saved.text == original

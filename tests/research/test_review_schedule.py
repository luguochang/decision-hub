from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier

import pytest
from pydantic import AnyUrl, TypeAdapter

from packages.contracts_py.decision_hub_contracts import (
    CausalCase,
    CausalLink,
    CoverageAssessment,
    EvidenceCandidate,
    HorizonDecision,
    ResearchPlan,
    ResearchRound,
    ResearchSessionResult,
    ResearchStopReason,
    ResearchTask,
)
from packages.kernel.decision_hub_kernel.application.review_schedule import (
    ReviewScheduleService,
)
from packages.kernel.decision_hub_kernel.application.study import ensure_study
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    EvaluationRecord,
    EventRecord,
    ForecastRecord,
    MacroEventRecord,
    ResearchFactRecord,
    ResearchResultRecord,
    ReviewScheduleRecord,
    RunRecord,
)

NOW = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)


def _follow_fixture_studies(database):
    with database.session() as session:
        for run in session.query(RunRecord).filter(RunRecord.study_id.is_(None)):
            run.study_id = ensure_study(
                session,
                question_key=run.event_id,
                title="研究测试",
                market="crypto",
                instrument="BTC-USDT-SWAP",
                now=NOW,
            )


def test_concurrent_plan_discovery_keeps_one_trigger(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'concurrent-review.sqlite3'}")
    database.create_all()
    barrier = Barrier(8)

    def discover() -> None:
        barrier.wait()
        ReviewScheduleService(database, clock=lambda: NOW)._ensure_plan(
            parent_run_id="run-parent",
            horizon="30m",
            trigger_type="next_review",
            trigger_ref="review-1",
            trigger_at=NOW,
            now=NOW,
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: discover(), range(8)))

    with database.session() as session:
        assert session.query(ReviewScheduleRecord).count() == 1
    database.engine.dispose()


@pytest.mark.parametrize(
    ("terminal_status", "plan_status"),
    [("completed", "completed"), ("failed", "failed"), ("cancelled", "skipped")],
)
def test_due_review_times_create_one_durable_child_per_horizon(
    tmp_path: Path, terminal_status: str, plan_status: str
) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'review-schedule.sqlite3'}")
    database.create_all()
    result = _result()
    with database.session() as session:
        session.add(
            EventRecord(
                event_id="event-1",
                event_type="crypto_market_snapshot",
                occurred_at=NOW - timedelta(hours=1),
                received_at=NOW - timedelta(hours=1),
            )
        )
        session.add(
            RunRecord(
                run_id="run-parent",
                event_id="event-1",
                status="completed",
                strategy_version="research.v1",
                runtime_version="dsh-web",
                admission_origin="manual",
                priority=100,
                created_at=NOW - timedelta(hours=1),
                updated_at=NOW - timedelta(minutes=5),
                finished_at=NOW - timedelta(minutes=5),
                artifact_id="artifact-1",
            )
        )
        payload = result.model_dump_json()
        session.add(
            ResearchResultRecord(
                run_id="run-parent",
                schema_version=result.schema_version,
                payload_json=payload,
                payload_hash=hashlib.sha256(payload.encode()).hexdigest(),
                created_at=NOW - timedelta(minutes=5),
            )
        )

    _follow_fixture_studies(database)
    service = ReviewScheduleService(database, clock=lambda: NOW)
    first = service.plan_due()
    second = service.plan_due()

    assert len(first) == 1
    assert second == ()
    with database.session() as session:
        children = session.query(RunRecord).filter_by(parent_run_id="run-parent").all()
        assert {item.admission_origin for item in children} == {"scheduled_recheck"}
        assert {item.priority for item in children} == {10}
        assert {item.status for item in children} == {"admitted"}
        assert all(item.available_at == NOW.replace(tzinfo=None) for item in children)
        events = (
            session.query(RunRecord)
            .filter(RunRecord.idempotency_key.like("research-review-plan:%"))
            .count()
        )
        assert events == 1
        plans = session.query(ReviewScheduleRecord).all()
        assert len(plans) == 3
        assert sum(item.status == "admitted" for item in plans) == 1
        assert sum(item.status == "planned" for item in plans) == 2
        assert all(item.failure_code == "study_research_active" for item in plans
                   if item.status == "planned")
        assert {item.trigger_type for item in plans} == {"next_review"}

    with database.session() as session:
        children = (
            session.query(RunRecord)
            .filter_by(parent_run_id="run-parent")
            .order_by(RunRecord.run_id.asc())
            .all()
        )
        assert len(children) == 1
        children[0].status = terminal_status
        children[0].finished_at = NOW
        children[0].updated_at = NOW
        children[0].error_code = "fixture_failure" if terminal_status == "failed" else None
    next_admission = service.plan_due()
    assert len(next_admission) == 1
    with database.session() as session:
        original = session.get(ReviewScheduleRecord, first[0].review_plan_id)
        assert original is not None and original.status == plan_status
        if terminal_status == "failed":
            assert original.failure_code == "fixture_failure"
        next_child = session.get(RunRecord, next_admission[0].child_run_id)
        assert next_child is not None
        next_child.status, next_child.finished_at, next_child.updated_at = "completed", NOW, NOW
    assert service.plan_due() == ()
    with database.session() as session:
        plans = session.query(ReviewScheduleRecord).all()
        waiting = [item for item in plans if item.status == "planned"]
        assert len(waiting) == 1
        assert waiting[0].failure_code == "research_admission_budget_exhausted"
        assert session.query(RunRecord).filter_by(parent_run_id="run-parent").count() == 2


def test_future_or_expired_review_is_not_scheduled(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'review-window.sqlite3'}")
    database.create_all()
    result = _result().model_copy(
        update={
            "horizons": [
                item.model_copy(
                    update={
                        "next_review_at": NOW + timedelta(minutes=5),
                        "expires_at": NOW + timedelta(minutes=30),
                    }
                )
                for item in _result().horizons
            ]
        }
    )
    with database.session() as session:
        session.add(
            EventRecord(
                event_id="event-future",
                event_type="crypto_market_snapshot",
                occurred_at=NOW,
                received_at=NOW,
            )
        )
        session.add(
            RunRecord(
                run_id="run-future",
                event_id="event-future",
                status="completed",
                strategy_version="research.v1",
                runtime_version="dsh-web",
                admission_origin="manual",
                priority=100,
                created_at=NOW,
                updated_at=NOW,
                finished_at=NOW,
            )
        )
        payload = result.model_dump_json()
        session.add(
            ResearchResultRecord(
                run_id="run-future",
                schema_version=result.schema_version,
                payload_json=payload,
                payload_hash=hashlib.sha256(payload.encode()).hexdigest(),
                created_at=NOW,
            )
        )
    _follow_fixture_studies(database)
    assert ReviewScheduleService(database, clock=lambda: NOW).plan_due() == ()
    with database.session() as session:
        plans = session.query(ReviewScheduleRecord).all()
        assert len(plans) == 3
        assert {item.status for item in plans} == {"planned"}


@pytest.mark.parametrize("same_study", [True, False])
def test_latest_root_scoped_to_study_preserves_other_questions(
    tmp_path: Path, same_study: bool
) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'review-owner.sqlite3'}")
    database.create_all()
    with database.session() as session:
        for suffix, age_minutes in (("old", 20), ("new", 5)):
            payload = _result().model_copy(update={
                "request_id": f"request-{suffix}",
                "research_session_id": f"session-{suffix}",
            }).model_dump_json()
            event_id = f"event-{suffix}"
            run_id = f"run-{suffix}"
            study_id = ensure_study(
                session,
                question_key="shared" if same_study else suffix,
                title="BTC 观察",
                market="crypto",
                instrument="BTC-USDT-SWAP",
                now=NOW,
            )
            session.add(
                EventRecord(
                    event_id=event_id,
                    event_type="crypto_market_snapshot",
                    occurred_at=NOW - timedelta(minutes=age_minutes),
                    received_at=NOW - timedelta(minutes=age_minutes),
                )
            )
            session.add(
                RunRecord(
                    run_id=run_id,
                    event_id=event_id,
                    study_id=study_id,
                    status="completed",
                    strategy_version="research.v1",
                    runtime_version="dsh-web",
                    admission_origin="manual",
                    priority=100,
                    created_at=NOW - timedelta(minutes=age_minutes),
                    updated_at=NOW - timedelta(minutes=age_minutes),
                    finished_at=NOW - timedelta(minutes=age_minutes),
                )
            )
            session.add(
                ResearchResultRecord(
                    run_id=run_id,
                    schema_version=_result().schema_version,
                    payload_json=payload,
                    payload_hash=hashlib.sha256(payload.encode()).hexdigest(),
                    created_at=NOW - timedelta(minutes=age_minutes),
                )
            )

    _follow_fixture_studies(database)
    admitted = ReviewScheduleService(database, clock=lambda: NOW).plan_due()

    assert len(admitted) == (1 if same_study else 2)
    assert {item.parent_run_id for item in admitted} == (
        {"run-new"} if same_study else {"run-new", "run-old"}
    )
    with database.session() as session:
        assert session.query(RunRecord).filter_by(parent_run_id="run-old").count() == (
            0 if same_study else 1
        )

        # Even if a scheduled child has a terminal result, it is an audit
        # descendant and may not become another scheduling authority.
        child = session.query(RunRecord).filter_by(parent_run_id="run-new").first()
        assert child is not None
        child.status = "completed"
        child.updated_at = NOW + timedelta(minutes=1)
        child.finished_at = NOW + timedelta(minutes=1)
        child_payload = _result().model_copy(update={
            "request_id": f"request-{child.run_id}",
            "research_session_id": f"session-{child.run_id}",
        }).model_dump_json()
        session.add(
            ResearchResultRecord(
                run_id=child.run_id,
                schema_version=_result().schema_version,
                payload_json=child_payload,
                payload_hash=hashlib.sha256(child_payload.encode()).hexdigest(),
                created_at=NOW + timedelta(minutes=1),
            )
        )

    later = ReviewScheduleService(
        database, clock=lambda: NOW + timedelta(minutes=2),
    ).plan_due()
    assert all(item.parent_run_id in {"run-new", "run-old"} for item in later)
    assert ReviewScheduleService(database).list_for_run(child.run_id) == []
    with database.session() as session:
        assert session.query(RunRecord).filter_by(parent_run_id=child.run_id).count() == 0


def test_macro_release_invalidation_and_completed_outcome_admit_distinct_plans(
    tmp_path: Path,
) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'review-triggers.sqlite3'}")
    database.create_all()
    result = _result().model_copy(
        update={
            "horizons": [
                item.model_copy(update={"next_review_at": NOW + timedelta(minutes=20)})
                for item in _result().horizons
            ]
        }
    )
    payload = result.model_dump_json()
    with database.session() as session:
        session.add(
            EventRecord(
                event_id="event-triggered",
                event_type="crypto_market_snapshot",
                occurred_at=NOW - timedelta(hours=1),
                received_at=NOW - timedelta(hours=1),
            )
        )
        session.add(
            RunRecord(
                run_id="run-triggered",
                event_id="event-triggered",
                status="completed",
                strategy_version="research.v1",
                runtime_version="dsh-web",
                admission_origin="manual",
                priority=100,
                created_at=NOW - timedelta(hours=1),
                updated_at=NOW - timedelta(minutes=5),
                finished_at=NOW - timedelta(minutes=5),
                artifact_id="artifact-triggered",
            )
        )
        session.add(
            ResearchResultRecord(
                run_id="run-triggered",
                schema_version=result.schema_version,
                payload_json=payload,
                payload_hash=hashlib.sha256(payload.encode()).hexdigest(),
                created_at=NOW - timedelta(minutes=5),
            )
        )
        session.add(
            MacroEventRecord(
                macro_event_id="macro-cpi",
                event_id="event-cpi",
                external_event_id="cpi-2026-09",
                family="cpi",
                scheduled_at=NOW - timedelta(minutes=1),
                source_id="bls-calendar",
                source_url="https://www.bls.gov/cpi/",
                source_authority="official",
                time_precision="exact",
                status="released",
                created_at=NOW - timedelta(minutes=2),
                updated_at=NOW - timedelta(seconds=30),
            )
        )
        session.add(
            ResearchFactRecord(
                fact_id="fact-invalidation",
                run_id="run-triggered",
                capability_id="market.cross_asset",
                evidence_id="ev-invalidation",
                requirement_id="market_regime_context",
                metric_family="market_regime",
                field="invalidation",
                value_json='"triggered"',
                unit="state",
                observed_at=NOW - timedelta(minutes=1),
                received_at=NOW - timedelta(minutes=1),
                source_id="fixture",
                independence_group="fixture",
                quality="accepted",
                delay_class="live",
                payload_schema_ref="fixture.v1",
                payload_hash="c" * 64,
                attributes_json=json.dumps({"triggered_invalidations": ["all"]}),
                accepted_at=NOW - timedelta(minutes=1),
            )
        )
        session.add(
            ForecastRecord(
                forecast_id="forecast-outcome",
                artifact_id="artifact-triggered",
                instrument="BTC-USDT-SWAP",
                horizon="24h",
                direction="long",
                probability=0.6,
                trigger="fixture",
                invalidation="fixture",
                expires_at=NOW - timedelta(minutes=1),
            )
        )
        session.add(
            EvaluationRecord(
                evaluation_id="evaluation-outcome",
                forecast_id="forecast-outcome",
                brier_score=0.16,
                net_return_pct=0.4,
                direction_correct=True,
                label_status="observed",
                evaluated_at=NOW - timedelta(seconds=15),
            )
        )

    _follow_fixture_studies(database)
    admitted = ReviewScheduleService(database, clock=lambda: NOW).plan_due()

    assert len(admitted) == 1
    due_types = {
        "macro_event",
        "invalidation_evidence",
        "outcome_due",
    }
    assert admitted[0].trigger_type in due_types
    with database.session() as session:
        plans = session.query(ReviewScheduleRecord).all()
        assert len(plans) == 10
        assert {item.trigger_type for item in plans} == due_types | {"next_review"}
        assert sum(item.status == "planned" for item in plans) == 9
        assert sum(item.status == "admitted" for item in plans) == 1
        assert sum(item.failure_code == "study_research_active" for item in plans) == 6


def _result() -> ResearchSessionResult:
    evidence = EvidenceCandidate(
        evidence_id="ev-1",
        kind="market",
        authority="exchange",
        source_id="fixture",
        source_url=TypeAdapter(AnyUrl).validate_python("https://example.test/fixture"),
        published_at=NOW - timedelta(minutes=10),
        observed_at=NOW - timedelta(minutes=10),
        received_at=NOW - timedelta(minutes=9),
        freshness_status="fresh",
        content_hash="a" * 64,
        excerpt="BTC market fixture",
        requirement_id="market_regime_context",
        structured_payload_ref=None,
        tool_call_id=None,
        research_session_id="session-1",
        round=1,
        quality="accepted",
        conflict_group=None,
    )
    coverage = CoverageAssessment(
        status="sufficient",
        covered_requirement_ids=["market_regime_context"],
        gaps=[],
        conflicts=[],
        hard_coverage_ratio=1,
        soft_coverage_ratio=1,
        assessed_at=NOW,
    )
    link = CausalLink(
        link_id="link-1",
        claim_type="fact",
        statement="BTC market state is observed.",
        evidence_refs=["ev-1"],
        confirmation="The observation remains valid.",
        invalidation="The observation is corrected.",
        affected_horizons=["30m", "24h", "72h"],
    )
    horizons = [
        HorizonDecision(
            horizon=horizon,
            action="neutral",
            subjective_probability=0.5,
            probability_status="uncalibrated",
            evidence_refs=["ev-1"],
            trigger="new market evidence arrives",
            invalidation="snapshot expires",
            expires_at=NOW + timedelta(hours=1),
            next_review_at=NOW - timedelta(minutes=1),
            missing_facts=[],
            confidence_cap_reason=None,
        )
        for horizon in ("30m", "24h", "72h")
    ]
    return ResearchSessionResult(
        schema_version="research-session-result.v1",
        request_id="request-1",
        research_session_id="session-1",
        runtime_id="fixture",
        runtime_version="fixture.v1",
        profile_ref="profile.v1",
        trace_ref="fixture://trace",
        trace_hash="b" * 64,
        status="completed",
        rounds=[
            ResearchRound(
                round=1,
                plan=ResearchPlan(
                    plan_id="plan-1",
                    objective="Review BTC",
                    tasks=[
                        ResearchTask(
                            task_id="task-1",
                            requirement_id="market_regime_context",
                            capability_id="market.cross_asset",
                            objective="Refresh the market snapshot",
                            question="Has the market state changed?",
                            input_evidence_refs=["ev-1"],
                            output_schema_ref="research-capability-result.v1",
                            depends_on=[],
                            success_condition="One fresh market snapshot",
                            priority=1,
                        )
                    ],
                    required_capabilities=["market.cross_asset"],
                    created_at=NOW,
                ),
                tool_invocations=[],
                tool_results=[],
                new_evidence_refs=["ev-1"],
                coverage=coverage,
                started_at=NOW - timedelta(minutes=2),
                finished_at=NOW - timedelta(minutes=1),
            )
        ],
        evidence_candidates=[evidence],
        facts=[],
        final_coverage=coverage,
        causal_case=CausalCase(
            case_id="case-1",
            thesis="BTC market state is observed.",
            main_chain=[link],
            opposite_chain=[
                link.model_copy(
                    update={"link_id": "link-2", "statement": "The move may be exhausted."}
                )
            ],
            unresolved_questions=[],
            evidence_refs=["ev-1"],
        ),
        horizons=horizons,
        stop_reason=ResearchStopReason(
            code="sufficient",
            detail="Evidence is sufficient.",
            bounded=False,
            remaining_hard_gaps=[],
        ),
        total_tool_calls=1,
        total_subagents=0,
        total_tokens=None,
        estimated_cost_usd=None,
        started_at=NOW - timedelta(minutes=2),
        finished_at=NOW - timedelta(minutes=1),
    )

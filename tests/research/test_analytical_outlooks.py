import json
from datetime import timedelta
from types import SimpleNamespace

import pytest

from packages.kernel.decision_hub_kernel.application.analytical_outlook import freeze_outlooks
from packages.kernel.decision_hub_kernel.application.commit import CommitDecisionService
from packages.kernel.decision_hub_kernel.application.fact_store import ResearchFactStore
from packages.kernel.decision_hub_kernel.application.study import ensure_study
from packages.kernel.decision_hub_kernel.decision.daily_strategy import compute_daily_strategy
from packages.kernel.decision_hub_kernel.persistence.db import (
    AnalyticalObservationRecord,
    AnalyticalOutlookRecord,
    ExecutionPermissionRecord,
    FactVersionRecord,
    ForecastRecord,
    PublicationDecisionRecord,
    ResearchFactRecord,
    RunRecord,
    as_utc,
)
from tests.evolution.test_research_worker import _result
from tests.research.test_fact_store import NOW, _database, _fact


def prepared(tmp_path, monkeypatch, *, action="short", thesis=None):
    db = _database(tmp_path)
    monkeypatch.setattr(
        "packages.kernel.decision_hub_kernel.application.fact_store.utcnow", lambda: NOW
    )
    ResearchFactStore(db).accept_facts(
        run_id="run-fact", capability_id="market.crypto_derivatives", facts=[_fact()], cutoff_at=NOW
    )
    with db.session() as session:
        session.get(RunRecord, "run-fact").study_id = ensure_study(
            session,
            question_key="test",
            title="Test",
            market="crypto",
            instrument="BTC-USDT-SWAP",
            now=NOW,
        )
    result = _result(SimpleNamespace(current_round=1, request_id="test", evidence_refs=[]))
    result = result.model_copy(
        update={
            "started_at": NOW - timedelta(minutes=1),
            "finished_at": NOW,
            "horizons": [
                h.model_copy(
                    update={
                        "evidence_refs": ["evidence-fact"],
                        "action": action,
                        "expires_at": NOW + timedelta(hours=1),
                    }
                )
                for h in result.horizons
            ],
        }
    )
    if thesis:
        result = result.model_copy(
            update={"causal_case": result.causal_case.model_copy(update={"thesis": thesis})}
        )
    return db, result


def publish(db, result):
    return CommitDecisionService(db, clock=lambda: NOW).commit_research_result(
        "run-fact",
        "event-fact",
        result,
    )[0]


def test_freeze_keeps_original_direction_without_opening_publication_or_execution(
    tmp_path, monkeypatch
):
    db, result = prepared(tmp_path, monkeypatch)
    artifact = publish(db, result)
    assert publish(db, result) == artifact
    with db.session() as session:
        outlooks = session.query(AnalyticalOutlookRecord).all()
        assert len(outlooks) == 3
        assert {o.action for o in outlooks} == {"short"}
        assert {o.eligibility for o in outlooks} == {"eligible"}
        assert {o.probability_status for o in outlooks} == {"uncalibrated"}
        assert {f.direction for f in session.query(ForecastRecord)} == {"no_trade"}
        assert session.get(PublicationDecisionRecord, artifact).status == "research_only"
        assert session.get(ExecutionPermissionRecord, artifact).allowed is False
        assert session.query(AnalyticalObservationRecord).count() == 3
        assert {as_utc(o.data_as_of) for o in outlooks} == {NOW - timedelta(seconds=1)}
        original = [o.raw_payload_json for o in outlooks]
        session.query(ResearchFactRecord).one().value_json = '"revised after publication"'
    with db.session() as session:
        assert [o.raw_payload_json for o in session.query(AnalyticalOutlookRecord)] == original


@pytest.mark.parametrize(
    "case,reason",
    [
        ("future", "future_as_of_claim"),
        ("expired", "model_hypothesis_expired"),
        ("late", "input_after_freeze"),
        ("unknown_ref", "unresolved_evidence_reference"),
        ("no_trade", "no_directional_hypothesis"),
        ("late_finish", "model_finished_after_freeze"),
    ],
)
def test_invalid_samples_are_retained_but_excluded(tmp_path, monkeypatch, case, reason):
    db, result = prepared(
        tmp_path,
        monkeypatch,
        thesis="截至 2099-01-01T00:00:00Z：上涨" if case == "future" else None,
        action="no_trade" if case == "no_trade" else "short",
    )
    if case == "late":
        with db.session() as session:
            session.query(FactVersionRecord).one().known_at = NOW + timedelta(seconds=1)
    if case == "late_finish":
        result = result.model_copy(update={"finished_at": NOW + timedelta(seconds=1)})
    if case in {"expired", "unknown_ref"}:
        update = (
            {"expires_at": NOW} if case == "expired" else {"evidence_refs": ["cross-run-evidence"]}
        )
        result = result.model_copy(
            update={"horizons": [h.model_copy(update=update) for h in result.horizons]}
        )
    publish(db, result)
    with db.session() as session:
        for outlook in session.query(AnalyticalOutlookRecord):
            assert outlook.eligibility == "excluded"
            assert reason in json.loads(outlook.exclusion_reasons_json)


def test_daily_rule_is_separate_uncalibrated_seven_day_method(tmp_path, monkeypatch):
    db, result = prepared(tmp_path, monkeypatch)
    bars = [
        {
            "date": (NOW.date() - timedelta(days=80 - i)).isoformat(),
            "open": 100 + i,
            "close": 101 + i,
            "high": 102 + i,
            "low": 99 + i,
            "volume": 1000,
        }
        for i in range(80)
    ]
    receipt = compute_daily_strategy(bars, evidence_refs=["evidence-fact"]).as_dict()
    # An injected model/calibration label cannot upgrade this deterministic rule.
    receipt["probability_status"] = "calibrated"
    with db.session() as session:
        fact = session.query(ResearchFactRecord).one()
        fact.capability_id, fact.instrument = "data.historical", "BTC-USDT"
        fact.attributes_json = json.dumps({"daily_strategy_receipt_json": json.dumps(receipt)})
    publish(db, result)
    with db.session() as session:
        rule = session.query(AnalyticalOutlookRecord).filter_by(method_family="daily_rule").one()
        assert rule.horizon == "7d" and rule.instrument == "BTC-USDT"
        assert rule.subjective_probability is None and rule.probability_status == "unavailable"
        assert as_utc(rule.window_end_at) == NOW + timedelta(days=7)
        assert rule.eligibility == "eligible"


def test_retrospective_freeze_never_enters_forward_cohort(tmp_path, monkeypatch):
    from packages.contracts_py.decision_hub_contracts.models import GateStatus
    from packages.kernel.decision_hub_kernel.decision.gate import GateResult

    db, result = prepared(tmp_path, monkeypatch)
    with db.session() as session:
        freeze_outlooks(
            session,
            run=session.get(RunRecord, "run-fact"),
            artifact=SimpleNamespace(artifact_id="old"),
            result=result,
            gate=GateResult(GateStatus.research_only, []),
            published_horizons=[],
            now=NOW,
            retrospective=True,
        )
    with db.session() as session:
        assert {o.cohort for o in session.query(AnalyticalOutlookRecord)} == {"retrospective"}
        assert {o.status for o in session.query(AnalyticalObservationRecord)} == {"excluded"}


async def test_read_api_has_pagination_history_and_separate_cohorts(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from apps.hub_api.outlook_routes import outlook_router
    from packages.kernel.decision_hub_kernel.application.analytical_observation import (
        AnalyticalObservationService,
    )
    from tests.scheduler.test_analytical_observation import Market

    db, result = prepared(tmp_path, monkeypatch)
    artifact = publish(db, result)

    def clock():
        return NOW + timedelta(minutes=31)

    await AnalyticalObservationService(db, Market(clock), clock=clock).process_due()
    app = FastAPI()
    app.include_router(outlook_router(db))
    with TestClient(app) as client:
        url = "/v1/analytical-outlooks"
        first = client.get(url, params={"run_id": "run-fact", "limit": 1})
        assert first.status_code == 200, first.text
        value = first.json()
        assert value["total"] == 3 and len(value["items"]) == 1
        outlook = value["items"][0]
        assert outlook["data_as_of"].endswith("Z")
        detail = client.get(url + "/" + outlook["outlook_id"])
        assert detail.status_code == 200, detail.text
        assert "raw_payload" in detail.json()
        groups = client.get(url + "/cohorts", params={"market": "crypto"})
        assert groups.status_code == 200, groups.text
        assert {r["horizon"] for r in groups.json()["items"]} == {"30m", "24h", "72h"}
        assert all(r["directional_hit_rate"] is None for r in groups.json()["items"])
        assert all(r["calibration_status"] == "not_calibration" for r in groups.json()["items"])
        published = client.get(f"/v1/artifacts/{artifact}/publication")
        assert published.status_code == 200, published.text
        assert not published.json()["execution_allowed"]
        assert client.get(url, params={"since": "2026-01-01T00:00:00"}).status_code == 422
        assert client.get(url, params={"limit": 101}).status_code == 422
        assert client.get(url + "/missing").status_code == 404
        assert client.get("/v1/artifacts/missing/publication").status_code == 404


def test_cohort_drilldown_and_scored_denominator_exclude_ineligible_stale_labels(
    tmp_path, monkeypatch
):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from apps.hub_api.outlook_routes import outlook_router
    from packages.query_views.analytical_outlooks import AnalyticalOutlookQuery

    db, result = prepared(tmp_path, monkeypatch)
    publish(db, result)
    with db.session() as session:
        original = session.query(AnalyticalOutlookRecord).filter_by(horizon="30m").one()
        base = {c.name: getattr(original, c.name) for c in original.__table__.columns}
        # Synthetic persisted-state regressions only; no production labels are fabricated.
        for i in range(36):
            values = {
                **base,
                "outlook_id": f"sample-{i}",
                "run_id": f"sample-run-{i}",
                "method_version": "cohort-test",
            }
            status = "completed"
            if i == 30:
                values["eligibility"] = "excluded"
            if i == 31:
                status = "retry"
            if i == 32:
                values["cohort"] = "retrospective"
            if i == 33:
                values["action"] = "neutral"
            if i == 35:
                status = "pending"
            session.add(AnalyticalOutlookRecord(**values))
            session.add(
                AnalyticalObservationRecord(
                    outlook_id=f"sample-{i}",
                    status=status,
                    next_attempt_at=NOW,
                    attempt=1,
                    direction_correct=2 if i == 34 else int(i < 20),
                    directional_return_pct=1 if i < 20 else -1,
                    quality_status=None if i == 35 else "estimated",
                )
            )
        session.commit()
    query = AnalyticalOutlookQuery(db)
    group = query.cohorts(
        method_version="cohort-test",
        cohort="prospective",
        action="short",
        quality_status="estimated",
    )["items"][0]
    assert group["total"] == 33
    assert group["record_directional_count"] == 30 and group["record_correct_count"] == 20
    assert group["fixed_sample_count"] == 1 and group["repeat_count"] == 32
    assert group["source_verified_count"] == 0 and group["unverified_count"] == 1
    assert group["directional_count"] == 0 and group["correct_count"] == 0
    assert group["directional_hit_rate"] is None
    assert group["distinct_studies"] == 1
    for filters in ({"cohort": "retrospective"}, {"action": "neutral"}):
        row = query.cohorts(method_version="cohort-test", **filters)["items"][0]
        assert row["directional_count"] == 0 and row["directional_hit_rate"] is None
    app = FastAPI()
    app.include_router(outlook_router(db))
    client = TestClient(app)
    params = {
        k: group[k]
        for k in (
            "market",
            "instrument",
            "method_family",
            "method_version",
            "horizon",
            "action",
            "research_quality",
            "quality_status",
            "cohort",
        )
    }
    from packages.kernel.decision_hub_kernel.persistence.db import (
        AnalyticalObservationAttemptRecord,
    )

    with db.session() as session:
        for attempt in range(1, 24):
            session.add(
                AnalyticalObservationAttemptRecord(
                    outlook_id="sample-0",
                    attempt=attempt,
                    status="failed",
                    started_at=NOW,
                    finished_at=NOW,
                    error_code="source_unavailable",
                )
            )
        session.commit()
    detail = client.get("/v1/analytical-outlooks/sample-0?limit=10&offset=999").json()
    assert detail["attempts_total"] == 23 and detail["attempts_offset"] == 20
    assert [a["attempt"] for a in detail["attempts"]] == [3, 2, 1]
    url = "/v1/analytical-outlooks"
    listing = client.get(url, params={**params, "limit": 12, "offset": 999}).json()
    assert listing["total"] == group["total"] and listing["offset"] == 24
    grouping = client.get(url + "/cohorts", params={**params, "offset": 999}).json()
    assert grouping["total"] == 1 and grouping["offset"] == 0
    for path in (url, url + "/cohorts"):
        empty = client.get(
            path, params={**params, "method_version": "missing", "offset": 99}
        ).json()
        assert empty["total"] == 0 and empty["offset"] == 0
        unlabeled = client.get(
            path, params={**params, "quality_status": "unlabelled", "status": "pending"}
        ).json()
        assert unlabeled["total"] == 1
        assert client.get(path, params={"quality_status": "invalid"}).status_code == 422

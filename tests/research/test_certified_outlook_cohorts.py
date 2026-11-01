"""SQLite/API aggregation controls; mocked assessments certify no original fixtures.

The real assessor's original-input and archive corruption controls live elsewhere.
Here its typed outputs isolate selection, grouping and certified denominators.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from apps.hub_api.outlook_routes import outlook_router
from packages.evals.analytical_assessment import AnalyticalAssessment
from packages.kernel.decision_hub_kernel.persistence.db import (
    AnalyticalObservationRecord as Observation,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    AnalyticalOutlookRecord as Outlook,
)
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.query_views.analytical_outlooks import AnalyticalOutlookQuery
from tests.research.test_fact_store import NOW, _database

METHOD = "certified-cohort-query-fixture"
RECIPE_A = "a" * 64
RECIPE_B = "b" * 64
QUERY_NOW = NOW + timedelta(hours=2)
URL = "/v1/analytical-outlooks"


def _add_record(
    session: Session, identity: str, *, study_id: str | None,
    outlook: dict[str, Any] | None = None, observation: dict[str, Any] | None = None,
) -> Outlook:
    values: dict[str, Any] = {
        "outlook_id": identity, "run_id": f"run-{identity}", "artifact_id": f"artifact-{identity}",
        "study_id": study_id, "market": "crypto", "instrument": "BTC-USDT-SWAP",
        "method_family": "ai_synthesis", "method_version": METHOD, "horizon": "30m",
        "action": "long", "subjective_probability": 0.6, "probability_status": "uncalibrated",
        "data_as_of": NOW - timedelta(seconds=1), "recorded_at": NOW,
        "window_start_at": NOW, "window_end_at": NOW + timedelta(minutes=30),
        "result_hash": "0" * 64, "evidence_refs_json": '[]', "fact_version_refs_json": '[]',
        "raw_payload_json": json.dumps({"synthetic_original": identity}),
        "trigger": "fixture trigger", "invalidation": "fixture invalidation",
        "cohort": "prospective", "eligibility": "eligible", "exclusion_reasons_json": "[]",
        "research_quality": "sufficient",
    }
    values.update(outlook or {})
    row = Outlook(**values)
    session.add(row)
    label: dict[str, Any] = {
        "outlook_id": identity, "status": "completed", "attempt": 1, "next_attempt_at": NOW,
        "evaluated_at": NOW + timedelta(minutes=31), "direction_correct": 1,
        "market_return_pct": 100.0, "directional_return_pct": 100.0,
        "quality_status": "estimated", "benchmark": "close_1m",
    }
    label.update(observation or {})
    session.add(Observation(**label))
    return row


def _snapshot(database: Database):
    with database.session() as session:
        return tuple(
            {row.outlook_id: {column.name: getattr(row, column.name)
                              for column in row.__table__.columns}
             for row in session.query(model).all()}
            for model in (Outlook, Observation)
        )


def _controlled_assessor(
    monkeypatch, profiles: dict[str, dict[str, Any]] | None = None,
    *, after_assessment: Callable[[str], None] | None = None,
) -> list[str]:
    calls: list[str] = []

    def assess(
        session: Session, outlook_id: str, *, assessed_at: datetime, archive=None,
    ) -> AnalyticalAssessment:
        row = session.get(Outlook, outlook_id)
        label = session.get(Observation, outlook_id)
        if row is None:
            raise KeyError(outlook_id)
        assert label is not None
        calls.append(outlook_id)
        label_state = "verified" if label.status == "completed" else (
            "retry" if label.status == "retry" else "pending"
        )
        values: dict[str, Any] = {
            "assessed_at": assessed_at, "outlook_id": outlook_id, "run_id": row.run_id,
            "study_id": row.study_id, "source_status": "verified", "method_status": "verified",
            "stable_recipe_hash": RECIPE_A, "label_status": label_state,
            "verified_direction_correct": label.direction_correct == 1,
            "verified_directional_return_pct": label.directional_return_pct,
        }
        values.update((profiles or {}).get(outlook_id, {}))
        usable = (
            values["source_status"] == "verified" and values["label_status"] == "verified"
            and values["stable_recipe_hash"] is not None and row.cohort == "prospective"
            and row.eligibility == "eligible" and row.action in {"long", "short"}
        )
        values["directional_usable"] = usable
        if not usable:
            values["verified_direction_correct"] = None
            values["verified_directional_return_pct"] = None
        result = AnalyticalAssessment.model_validate(values)
        if after_assessment is not None:
            after_assessment(outlook_id)
        return result

    monkeypatch.setattr(
        "packages.query_views.analytical_outlooks.assess_outlook_in_session", assess,
    )
    return calls


def _client(database: Database, tmp_path: Path) -> TestClient:
    app = FastAPI()
    # Preserve the frozen v1 synchronous selector controls. New product v3
    # background reads have separate original-input and integration controls.
    app.include_router(outlook_router(database, tmp_path, background_assessments=False))
    return TestClient(app)


def _api_filters(filters: dict[str, Any]) -> dict[str, Any]:
    return {key: value.isoformat() if isinstance(value, datetime) else value
            for key, value in filters.items()}


@pytest.mark.parametrize("first_proof", ["verified", "source_invalid", "label_unknown"])
def test_thirty_repeats_cannot_supply_thirty_certified_samples(
    tmp_path, monkeypatch, first_proof,
):
    database = _database(tmp_path)
    with database.session() as session:
        _add_record(session, "first", study_id="one-study")
        for index in range(32):
            _add_record(
                session, f"repeat-{index:02}", study_id="one-study",
                outlook={"recorded_at": NOW + timedelta(seconds=index + 1),
                         "window_start_at": NOW + timedelta(seconds=index + 1)},
            )
    original = _snapshot(database)
    profiles: dict[str, dict[str, Any]] = {}
    if first_proof == "source_invalid":
        profiles["first"] = {
            "source_status": "invalid", "source_reason": "source_original_invalid",
            "method_status": "unverifiable", "stable_recipe_hash": None,
            "label_status": "unverifiable", "label_reason": "source_not_verified",
        }
    elif first_proof == "label_unknown":
        profiles["first"] = {"label_status": "unverifiable", "label_reason": "label_unknown"}
    usable_count = int(first_proof == "verified")
    calls = _controlled_assessor(monkeypatch, profiles)
    query = AnalyticalOutlookQuery(database, clock=lambda: QUERY_NOW)
    group = query.cohorts(method_version=METHOD)["items"][0]
    assert group["total"] == group["record_directional_count"] == 33
    assert group["record_correct_count"] == 33 and group["distinct_studies"] == 1
    assert group["fixed_sample_count"] == 1 and group["directional_count"] == usable_count
    assert group["repeat_count"] == 32 and group["correct_count"] == usable_count
    assert group["unverified_count"] == 1 - usable_count
    assert group["directional_hit_rate"] is None and calls == ["first"]
    with _client(database, tmp_path) as client:
        response = client.get(URL + "/cohorts", params={"method_version": METHOD})
        assert response.status_code == 200, response.text
        item = response.json()["items"][0]
        assert item["fixed_sample_count"] == 1 and item["repeat_count"] == 32
        assert item["directional_count"] == usable_count
        assert item["directional_hit_rate"] is None
        assert calls == ["first", "first"]
        first = client.get(URL + "/first/assessment")
        assert first.status_code == 200, first.text
        assert first.json()["selection_status"] == "selected"
        assert first.json()["directional_usable"] is bool(usable_count)
        repeat = client.get(URL + "/repeat-00/assessment")
        assert repeat.status_code == 200, repeat.text
        assert repeat.json()["source_status"] == repeat.json()["label_status"] == "verified"
        assert repeat.json()["selection_status"] == "repeat"
        assert not repeat.json()["directional_usable"]
    assert calls == ["first", "first", "first", "repeat-00"]
    assert _snapshot(database) == original


@pytest.mark.parametrize("display_filter", [
    "action", "status", "quality_status", "research_quality", "since", "until",
    "run_id", "method_version", "method_family", "page",
])
def test_filters_and_group_page_never_replace_first_neutral_record(
    tmp_path, monkeypatch, display_filter,
):
    database = _database(tmp_path)
    first_start = NOW + timedelta(minutes=6 if display_filter == "until" else 2)
    later_start = NOW + timedelta(minutes=3)
    with database.session() as session:
        _add_record(
            session, "first-neutral", study_id="one-study",
            outlook={"action": "neutral", "research_quality": "advisory_only",
                     "window_start_at": first_start,
                     "window_end_at": first_start + timedelta(minutes=30)},
            observation={"status": "pending", "quality_status": None,
                         "direction_correct": None, "directional_return_pct": None,
                         "evaluated_at": None},
        )
        _add_record(
            session, "later-long", study_id="one-study",
            outlook={"recorded_at": NOW + timedelta(minutes=1),
                     "window_start_at": later_start,
                     "window_end_at": later_start + timedelta(minutes=30)},
        )
    filters: dict[str, Any] = {"method_version": METHOD}
    filters.update({
        "action": {"action": "long"}, "status": {"status": "completed"},
        "quality_status": {"quality_status": "estimated"},
        "research_quality": {"research_quality": "sufficient"},
        "since": {"since": NOW + timedelta(minutes=2, seconds=30)},
        "until": {"until": NOW + timedelta(minutes=4)},
        "run_id": {"run_id": "run-later-long"},
        "method_version": {"method_version": METHOD},
        "method_family": {"method_family": "ai_synthesis"},
        "page": {"limit": 1, "offset": 0},
    }[display_filter])
    original = _snapshot(database)
    calls = _controlled_assessor(monkeypatch)
    query = AnalyticalOutlookQuery(database, clock=lambda: QUERY_NOW)
    groups = query.cohorts(**filters)
    later = next(item for item in groups["items"] if item["action"] == "long")
    assert later["total"] == later["record_directional_count"] == 1
    assert later["fixed_sample_count"] == later["directional_count"] == 0
    assert later["repeat_count"] == 1 and later["directional_hit_rate"] is None
    assert "later-long" not in calls
    with _client(database, tmp_path) as client:
        response = client.get(URL + "/cohorts", params=_api_filters(filters))
        assert response.status_code == 200, response.text
        later = next(item for item in response.json()["items"] if item["action"] == "long")
        assert later["repeat_count"] == 1 and later["fixed_sample_count"] == 0
        assert later["directional_count"] == 0 and later["directional_hit_rate"] is None
    assert "later-long" not in calls and _snapshot(database) == original
    if display_filter == "page":
        other = query.cohorts(method_version=METHOD, limit=1, offset=1)["items"][0]
        assert other["action"] == "neutral" and other["fixed_sample_count"] == 1
        assert other["directional_count"] == 0 and other["repeat_count"] == 0


@pytest.mark.parametrize("sample_count", [29, 30])
def test_only_verified_distinct_studies_in_one_recipe_unlock_rate(
    tmp_path, monkeypatch, sample_count,
):
    database = _database(tmp_path)
    profiles: dict[str, dict[str, Any]] = {}
    with database.session() as session:
        for index in range(sample_count):
            identity = f"study-sample-{index:02}"
            _add_record(session, identity, study_id=f"study-{index:02}")
            profiles[identity] = {
                "verified_direction_correct": index < 20,
                "verified_directional_return_pct": 2.0 if index < 20 else -1.0,
            }
    original = _snapshot(database)
    calls = _controlled_assessor(monkeypatch, profiles)
    query = AnalyticalOutlookQuery(database, clock=lambda: QUERY_NOW)
    group = query.cohorts(method_version=METHOD)["items"][0]
    assert group["distinct_studies"] == group["fixed_sample_count"] == sample_count
    assert group["source_verified_count"] == group["label_verified_count"] == sample_count
    assert group["directional_count"] == sample_count and group["unverified_count"] == 0
    assert group["record_correct_count"] == sample_count and group["correct_count"] == 20
    assert group["record_average_directional_return_pct"] == 100.0
    expected_return = (40.0 - (sample_count - 20)) / sample_count
    assert group["average_directional_return_pct"] == pytest.approx(expected_return)
    assert group["method_recipe_hashes"] == [RECIPE_A] and group["assessment_reasons"] == {}
    assert group["minimum_rate_samples"] == 30 and group["calibration_status"] == "not_calibration"
    expected_rate = 20 / sample_count if sample_count >= 30 else None
    assert group["directional_hit_rate"] == expected_rate
    with _client(database, tmp_path) as client:
        response = client.get(URL + "/cohorts", params={"method_version": METHOD})
        assert response.status_code == 200, response.text
        assert response.json()["items"][0]["directional_hit_rate"] == expected_rate
    assert len(calls) == sample_count * 2 and _snapshot(database) == original


def test_different_verified_recipes_are_not_merged_into_certified_rate(tmp_path, monkeypatch):
    database = _database(tmp_path)
    with database.session() as session:
        for index in range(30):
            _add_record(session, f"mixed-{index:02}", study_id=f"study-{index:02}")
    profiles = {f"mixed-{index:02}": {"stable_recipe_hash": RECIPE_B} for index in range(15)}
    _controlled_assessor(monkeypatch, profiles)
    original = _snapshot(database)
    with _client(database, tmp_path) as client:
        response = client.get(URL + "/cohorts", params={"method_version": METHOD})
        assert response.status_code == 200, response.text
        group = response.json()["items"][0]
        assert group["fixed_sample_count"] == group["source_verified_count"] == 30
        assert group["label_verified_count"] == 30 and group["record_directional_count"] == 30
        assert group["method_recipe_hashes"] == [RECIPE_A, RECIPE_B]
        assert group["assessment_reasons"] == {"mixed_stable_recipes": 1}
        assert group["directional_count"] == group["correct_count"] == 0
        assert group["directional_hit_rate"] is None
        assert group["average_directional_return_pct"] is None
    assert _snapshot(database) == original


def test_source_and_label_failures_retry_and_unknown_never_supply_usable_labels(
    tmp_path, monkeypatch,
):
    database = _database(tmp_path)
    with database.session() as session:
        for index in range(7):
            _add_record(
                session, f"state-{index}", study_id=f"study-{index}",
                observation={"status": "retry" if index == 5 else (
                    "pending" if index == 6 else "completed"
                )},
            )
    profiles = {
        "state-1": {"source_status": "invalid", "source_reason": "source_original_invalid",
                    "method_status": "unverifiable", "stable_recipe_hash": None,
                    "label_status": "unverifiable", "label_reason": "source_not_verified"},
        "state-2": {"source_status": "unverifiable",
                    "source_reason": "source_original_unknown", "method_status": "unverifiable",
                    "stable_recipe_hash": None, "label_status": "unverifiable",
                    "label_reason": "source_not_verified"},
        "state-3": {"label_status": "invalid", "label_reason": "label_archive_corrupt"},
        "state-4": {"label_status": "unverifiable", "label_reason": "label_original_unknown"},
        "state-5": {"label_reason": "label_retry_pending"},
        "state-6": {"label_reason": "label_not_recorded"},
    }
    _controlled_assessor(monkeypatch, profiles)
    original = _snapshot(database)
    with _client(database, tmp_path) as client:
        response = client.get(URL + "/cohorts", params={"method_version": METHOD})
        assert response.status_code == 200, response.text
        group = response.json()["items"][0]
        assert group["total"] == group["fixed_sample_count"] == 7
        assert group["completed"] == group["record_directional_count"] == 5
        assert group["retry"] == group["pending"] == 1
        assert group["source_verified_count"] == 5 and group["label_verified_count"] == 1
        assert group["unverified_count"] == 6 and group["directional_count"] == 1
        assert group["directional_hit_rate"] is None
        assert group["assessment_reasons"] == {
            "source_original_invalid": 1, "source_original_unknown": 1,
            "source_not_verified": 2, "stable_recipe_unverifiable": 2,
            "label_archive_corrupt": 1, "label_original_unknown": 1,
            "label_retry_pending": 1, "label_not_recorded": 1,
        }
    assert _snapshot(database) == original


def test_real_unverifiable_old_row_and_missing_read_api_remain_unchanged(tmp_path):
    database = _database(tmp_path)
    with database.session() as session:
        _add_record(session, "old-without-originals", study_id="old-study")
    original = _snapshot(database)
    with _client(database, tmp_path) as client:
        for suffix in ("/missing", "/missing/assessment"):
            response = client.get(URL + suffix)
            assert response.status_code == 404
            assert response.json()["detail"] == "analytical_outlook_not_found"
        detail = client.get(URL + "/old-without-originals")
        assert detail.status_code == 200, detail.text
        assert detail.json()["raw_payload"] == {"synthetic_original": "old-without-originals"}
        assessment = detail.json()["assessment"]
        assert assessment["source_status"] == "unverifiable"
        assert assessment["source_reason"] == "assessment_run_missing"
        assert assessment["stable_recipe_hash"] is None and not assessment["directional_usable"]
        response = client.get(URL + "/cohorts", params={"method_version": METHOD})
        assert response.status_code == 200, response.text
        group = response.json()["items"][0]
        assert group["record_directional_count"] == group["fixed_sample_count"] == 1
        assert group["source_verified_count"] == group["directional_count"] == 0
        assert group["unverified_count"] == 1 and group["directional_hit_rate"] is None
    assert _snapshot(database) == original


@pytest.mark.parametrize("display_filter", ["new_version", "page", "filtered_page"])
def test_new_saved_alias_cannot_replace_invalid_old_alias_or_unlock_rate(
    tmp_path, monkeypatch, display_filter,
):
    database = _database(tmp_path)
    old_version, new_version = METHOD + ":a-old", METHOD + ":z-new"
    profiles: dict[str, dict[str, Any]] = {}
    new_ids = {f"new-alias-{index:02}" for index in range(30)}
    with database.session() as session:
        for index in range(30):
            study = f"study-{index:02}"
            old_id, new_id = f"old-alias-{index:02}", f"new-alias-{index:02}"
            _add_record(session, old_id, study_id=study, outlook={"method_version": old_version})
            _add_record(
                session, new_id, study_id=study,
                outlook={"method_version": new_version, "recorded_at": NOW + timedelta(minutes=1),
                         "window_start_at": NOW + timedelta(minutes=1),
                         "window_end_at": NOW + timedelta(minutes=31)},
            )
            profiles[old_id] = {
                "source_status": "invalid", "source_reason": "source_original_invalid",
                "method_status": "unverifiable", "stable_recipe_hash": None,
                "label_status": "unverifiable", "label_reason": "source_not_verified",
            }
    filters: dict[str, Any] = {
        "new_version": {"method_version": new_version},
        "page": {"limit": 1, "offset": 1},
        "filtered_page": {"method_version": new_version, "action": "long",
                          "status": "completed", "quality_status": "estimated",
                          "since": NOW + timedelta(seconds=30), "limit": 1, "offset": 999},
    }[display_filter]
    original = _snapshot(database)
    calls = _controlled_assessor(monkeypatch, profiles)
    query = AnalyticalOutlookQuery(database, clock=lambda: QUERY_NOW)
    group = query.cohorts(**filters)["items"][0]
    assert group["method_version"] == new_version
    assert group["fixed_sample_count"] == group["record_directional_count"] == 30
    assert group["repeat_count"] == 0
    assert group["source_verified_count"] == group["label_verified_count"] == 30
    assert group["unverified_count"] == 30 and group["method_recipe_hashes"] == [RECIPE_A]
    assert group["assessment_reasons"] == {"method_identity_ambiguous": 30}
    assert group["directional_count"] == group["correct_count"] == 0
    assert group["directional_hit_rate"] is None
    assert group["average_directional_return_pct"] is None
    assert set(calls) == new_ids
    with _client(database, tmp_path) as client:
        response = client.get(URL + "/cohorts", params=_api_filters(filters))
        assert response.status_code == 200, response.text
        item = response.json()["items"][0]
        assert item["method_version"] == new_version and item["fixed_sample_count"] == 30
        assert item["unverified_count"] == 30 and item["directional_count"] == 0
        assert item["assessment_reasons"] == {"method_identity_ambiguous": 30}
        assert item["source_verified_count"] == item["label_verified_count"] == 30
        assert item["directional_hit_rate"] is None and set(calls) == new_ids
        newer = client.get(URL + "/new-alias-00/assessment")
        assert newer.status_code == 200, newer.text
        certified = newer.json()
        assert certified["source_status"] == certified["label_status"] == "verified"
        assert certified["stable_recipe_hash"] == RECIPE_A
        assert certified["selection_status"] == "unchecked" and not certified["directional_usable"]
        assert certified["limitations"].count("method_identity_ambiguous") == 1
        older = client.get(URL + "/old-alias-00")
        assert older.status_code == 200, older.text
        assessment = older.json()["assessment"]
        assert assessment["source_status"] == "invalid"
        assert assessment["selection_status"] == "unchecked"
        assert not assessment["directional_usable"]
        assert "method_identity_ambiguous" in assessment["limitations"]
    assert _snapshot(database) == original


def test_saved_alias_ambiguity_does_not_cross_market_instrument_family_horizon_or_study(
    tmp_path, monkeypatch,
):
    database = _database(tmp_path)
    old_version, new_version = METHOD + ":old", METHOD + ":new"
    with database.session() as session:
        _add_record(session, "scope-base", study_id="base-study",
                    outlook={"method_version": old_version})
        for dimension, different in (
            ("market", "metal"), ("instrument", "ETH-USDT-SWAP"),
            ("method_family", "daily_rule"), ("horizon", "24h"), ("study_id", "other-study"),
        ):
            _add_record(
                session, f"scope-{dimension}", study_id="base-study",
                outlook={"method_version": new_version, dimension: different},
            )
    original = _snapshot(database)
    _controlled_assessor(monkeypatch)
    query = AnalyticalOutlookQuery(database, clock=lambda: QUERY_NOW)
    groups = query.cohorts(method_version=new_version)
    assert groups["total"] == 5
    assert all(item["fixed_sample_count"] == item["directional_count"] == 1
               and item["unverified_count"] == 0 and item["assessment_reasons"] == {}
               for item in groups["items"])
    with _client(database, tmp_path) as client:
        response = client.get(URL + "/scope-instrument/assessment")
        assert response.status_code == 200, response.text
        assert response.json()["selection_status"] == "selected"
        assert response.json()["directional_usable"]
        assert "method_identity_ambiguous" not in response.json()["limitations"]
    assert _snapshot(database) == original


class _BudgetClock:
    def __init__(self) -> None:
        self.seconds = 0.0

    def __call__(self) -> float:
        return self.seconds

    def advance(self, seconds: float) -> None:
        self.seconds += seconds


def test_budget_finishes_current_sample_and_preserves_first_slot_and_raw_counts(
    tmp_path, monkeypatch,
):
    database = _database(tmp_path)
    with database.session() as session:
        _add_record(session, "a-first", study_id="same-study")
        _add_record(session, "a-repeat", study_id="same-study",
                    outlook={"recorded_at": NOW + timedelta(seconds=1)})
        _add_record(session, "b-other", study_id="another-study")
    original = _snapshot(database)
    timer = _BudgetClock()
    calls = _controlled_assessor(
        monkeypatch, {"a-first": {
            "source_status": "invalid", "source_reason": "original_source_changed",
            "method_status": "unverifiable", "stable_recipe_hash": None,
            "label_status": "unverifiable", "label_reason": "original_label_unknown",
        }}, after_assessment=lambda _: timer.advance(5.0),
    )
    query = AnalyticalOutlookQuery(database, clock=lambda: QUERY_NOW, monotonic=timer)
    group = query.cohorts(method_version=METHOD)["items"][0]
    assert calls == ["a-first"] and timer.seconds == 5.0
    assert group["fixed_sample_count"] == 2 and group["repeat_count"] == 1
    assert group["total"] == group["record_directional_count"] == group["record_correct_count"] == 3
    assert group["record_average_directional_return_pct"] == 100.0
    assert group["source_verified_count"] == group["label_verified_count"] == 0
    assert group["unverified_count"] == 2
    assert group["assessment_reasons"] == {
        "original_source_changed": 1, "original_label_unknown": 1,
        "stable_recipe_unverifiable": 1, "assessment_pending": 1,
    }
    assert "assessment_version" not in group
    assert group["directional_count"] == group["correct_count"] == 0
    assert group["directional_hit_rate"] is None and group["average_directional_return_pct"] is None
    # Single-record readers still authenticate completely after the bulk budget.
    assessment = query.assessment("b-other")
    detail = query.get("b-other")
    assert assessment["source_status"] == assessment["label_status"] == "verified"
    assert assessment["selection_status"] == "selected" and assessment["directional_usable"]
    assert detail["assessment"]["source_status"] == "verified"
    assert detail["assessment"]["label_status"] == "verified"
    assert calls == ["a-first", "b-other", "b-other"]
    assert _snapshot(database) == original


def test_partial_recipe_coverage_never_emits_rate_even_after_thirty_verified_samples(
    tmp_path, monkeypatch,
):
    database = _database(tmp_path)
    with database.session() as session:
        for index in range(31):
            _add_record(session, f"budget-{index:02}", study_id=f"study-{index:02}")
    original = _snapshot(database)
    timer = _BudgetClock()
    calls = _controlled_assessor(
        monkeypatch, {"budget-30": {"stable_recipe_hash": RECIPE_B}},
        after_assessment=lambda _: timer.advance(0.067),
    )
    query = AnalyticalOutlookQuery(database, clock=lambda: QUERY_NOW, monotonic=timer)
    group = query.cohorts(method_version=METHOD)["items"][0]
    assert calls == [f"budget-{index:02}" for index in range(30)]
    assert group["fixed_sample_count"] == group["record_directional_count"] == 31
    assert group["source_verified_count"] == group["label_verified_count"] == 30
    assert group["method_recipe_hashes"] == [RECIPE_A] and group["unverified_count"] == 1
    assert group["assessment_reasons"] == {"assessment_pending": 1}
    assert "assessment_version" not in group
    assert group["directional_count"] == group["correct_count"] == 0
    assert group["directional_hit_rate"] is None and group["average_directional_return_pct"] is None

    def factory(
        db: Database, *, archive=None, background_assessments: bool = False,
    ) -> AnalyticalOutlookQuery:
        assert not background_assessments
        return AnalyticalOutlookQuery(db, archive=archive, clock=lambda: QUERY_NOW, monotonic=timer)

    monkeypatch.setattr("apps.hub_api.outlook_routes.AnalyticalOutlookQuery", factory)
    timer.seconds = 0.0
    calls.clear()
    with _client(database, tmp_path) as client:
        response = client.get(URL + "/cohorts", params={"method_version": METHOD})
        assert response.status_code == 200, response.text
        item = response.json()["items"][0]
        assert "assessment_version" not in item
        assert item["source_verified_count"] == item["label_verified_count"] == 30
        assert item["assessment_reasons"] == {"assessment_pending": 1}
        assert item["directional_count"] == item["correct_count"] == 0
        assert "directional_hit_rate" in item and item["directional_hit_rate"] is None
        assert "average_directional_return_pct" in item
        assert item["average_directional_return_pct"] is None
    assert calls == [f"budget-{index:02}" for index in range(30)]
    assert _snapshot(database) == original


def test_budget_starts_before_metadata_and_keeps_unchecked_identity_reason(
    tmp_path, monkeypatch,
):
    database = _database(tmp_path)
    with database.session() as session:
        _add_record(session, "old-alias", study_id="one-study")
        _add_record(session, "new-alias", study_id="one-study",
                    outlook={"method_version": METHOD + ":new"})
    original = _snapshot(database)
    timer = _BudgetClock()
    calls = _controlled_assessor(monkeypatch)
    original_selection = (
        AnalyticalOutlookQuery._selection_in_session  # pyright: ignore[reportPrivateUsage]
    )

    def charged_selection(
        query: AnalyticalOutlookQuery, session: Session, now: datetime,
    ) -> tuple[frozenset[str], frozenset[str]]:
        result = original_selection(query, session, now)
        timer.advance(2.0)
        return result

    monkeypatch.setattr(AnalyticalOutlookQuery, "_selection_in_session", charged_selection)
    query = AnalyticalOutlookQuery(database, clock=lambda: QUERY_NOW, monotonic=timer)
    groups = query.cohorts()["items"]
    assert len(groups) == 2 and not calls
    for group in groups:
        assert group["fixed_sample_count"] == group["record_directional_count"] == 1
        assert group["source_verified_count"] == group["label_verified_count"] == 0
        assert group["unverified_count"] == 1 and "assessment_version" not in group
        assert group["assessment_reasons"] == {
            "method_identity_ambiguous": 1, "assessment_pending": 1,
        }
        assert group["directional_count"] == group["correct_count"] == 0
        assert group["directional_hit_rate"] is None
    assert _snapshot(database) == original


def test_budget_is_shared_between_groups_and_complete_group_keeps_existing_algorithm(
    tmp_path, monkeypatch,
):
    database = _database(tmp_path)
    with database.session() as session:
        _add_record(session, "first-long", study_id="long-study")
        _add_record(session, "first-short", study_id="short-study", outlook={"action": "short"})
        _add_record(session, "second-short", study_id="another-study", outlook={"action": "short"})
    original = _snapshot(database)
    timer = _BudgetClock()
    calls = _controlled_assessor(monkeypatch, after_assessment=lambda _: timer.advance(5.0))
    query = AnalyticalOutlookQuery(database, clock=lambda: QUERY_NOW, monotonic=timer)
    groups = query.cohorts()["items"]
    complete = next(group for group in groups if group["action"] == "long")
    pending = next(group for group in groups if group["action"] == "short")
    assert calls == ["first-long"]
    assert complete["assessment_version"] == "fixed-study-originals.v1"
    assert complete["directional_count"] == complete["correct_count"] == 1
    assert complete["average_directional_return_pct"] == 100.0
    assert complete["source_verified_count"] == complete["label_verified_count"] == 1
    assert complete["unverified_count"] == 0 and complete["assessment_reasons"] == {}
    assert "assessment_version" not in pending
    assert pending["fixed_sample_count"] == pending["record_directional_count"] == 2
    assert pending["source_verified_count"] == pending["label_verified_count"] == 0
    assert pending["unverified_count"] == 2
    assert pending["assessment_reasons"] == {"assessment_pending": 2}
    assert pending["directional_count"] == pending["correct_count"] == 0
    assert pending["directional_hit_rate"] is None
    assert pending["average_directional_return_pct"] is None
    assert _snapshot(database) == original

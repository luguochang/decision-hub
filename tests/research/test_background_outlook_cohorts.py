"""Background cohort integration; controlled readers certify no original fixtures.

SQLite metadata, recipe first selection, filters and the default API route are
real. Only the expensive authenticated reader's typed return values are replaced;
original/basis/archive authenticity has its own genuine fixture gates.
"""

from __future__ import annotations

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
    AnalyticalAssessmentJobRecord as Job,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    AnalyticalObservationRecord as Observation,
)
from packages.kernel.decision_hub_kernel.persistence.db import AnalyticalOutlookRecord as Outlook
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.query_views.analytical_outlooks import AnalyticalOutlookQuery
from tests.research.test_certified_outlook_cohorts import (
    METHOD,
    NOW,
    QUERY_NOW,
    RECIPE_A,
    RECIPE_B,
    URL,
    _add_record,  # pyright: ignore[reportPrivateUsage] # Shared synthetic SQL metadata.
    _snapshot,  # pyright: ignore[reportPrivateUsage] # Compare all original columns.
)
from tests.research.test_fact_store import _database


def _controlled_projection(
    session: Session, identity: str, now: datetime, values: dict[str, Any] | None = None,
) -> AnalyticalAssessment:
    row = session.get(Outlook, identity)
    observation = session.get(Observation, identity)
    assert row is not None and observation is not None
    label = "verified" if observation.status == "completed" else (
        "retry" if observation.status == "retry" else "pending"
    )
    fields: dict[str, Any] = {
        "assessed_at": now, "outlook_id": identity, "run_id": row.run_id,
        "study_id": row.study_id, "source_status": "verified", "method_status": "verified",
        "stable_recipe_hash": RECIPE_A, "label_status": label,
        "verified_direction_correct": observation.direction_correct == 1,
        "verified_directional_return_pct": 2.0,
    }
    fields.update(values or {})
    usable = (
        fields["source_status"] == "verified" and fields["label_status"] == "verified"
        and fields["stable_recipe_hash"] is not None and row.eligibility == "eligible"
        and row.cohort == "prospective" and row.action in {"long", "short"}
    )
    fields["directional_usable"] = usable
    if not usable:
        fields["verified_direction_correct"] = None
        fields["verified_directional_return_pct"] = None
    return AnalyticalAssessment.model_validate(fields)


def _controlled_reads(
    database: Database, monkeypatch: pytest.MonkeyPatch,
    profiles: dict[str, dict[str, Any] | None] | None = None,
) -> list[str]:
    # Pending jobs supply exact real identity properties to the controlled
    # reader. No fabricated completed basis row is presented as a valid proof.
    with database.session() as session:
        for row in session.query(Outlook).all():
            session.add(Job(
                outlook_id=row.outlook_id, status="pending", attempt=0,
                next_attempt_at=NOW, verifier_version="controlled-reader-only",
            ))
    calls: list[str] = []

    def read(session: Session, job: Job | None, *, now: datetime, archive=None, memo=None):
        if job is None:
            return None
        identity = job.outlook_id
        calls.append(identity)
        profile = (profiles or {}).get(identity, {})
        return None if profile is None else _controlled_projection(session, identity, now, profile)

    def forbid_sync(*args, **kwargs):
        raise AssertionError("background_cohorts_called_synchronous_assessor")

    monkeypatch.setattr("packages.query_views.analytical_outlooks.read_valid_assessment", read)
    monkeypatch.setattr(
        "packages.query_views.analytical_outlooks.assess_outlook_in_session", forbid_sync,
    )
    return calls


def _query(database: Database) -> AnalyticalOutlookQuery:
    return AnalyticalOutlookQuery(database, clock=lambda: QUERY_NOW, background_assessments=True)


def _client(database: Database, tmp_path: Path) -> TestClient:
    app = FastAPI()
    # Exercise the production route's default True, without a fixture mode override.
    app.include_router(outlook_router(database, tmp_path))
    return TestClient(app)


def test_same_recipe_across_saved_aliases_reserves_one_first_and_later_is_repeat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    database = _database(tmp_path)
    old_version, new_version = METHOD + ":a-old", METHOD + ":z-new"
    with database.session() as session:
        _add_record(session, "first", study_id="same-study",
                    outlook={"method_version": old_version})
        _add_record(session, "later", study_id="same-study", outlook={
            "method_version": new_version, "recorded_at": NOW + timedelta(seconds=1),
        })
    original = _snapshot(database)
    calls = _controlled_reads(database, monkeypatch)
    groups = _query(database).cohorts()["items"]
    assert len(groups) == 1
    first = groups[0]
    assert first["method_version"] is None
    assert first["saved_method_versions"] == [old_version, new_version]
    assert first["total"] == first["record_directional_count"] == 2
    assert first["fixed_sample_count"] == first["directional_count"] == 1
    assert first["repeat_count"] == 1
    assert first["assessment_version"] == "authenticated-recipe-originals.v2"
    assert set(calls) == {"first", "later"}
    with _client(database, tmp_path) as client:
        response = client.get(URL + "/cohorts", params={"method_version": new_version})
        assert response.status_code == 200, response.text
        item = response.json()["items"][0]
        assert item["fixed_sample_count"] == 0 and item["repeat_count"] == 1
        assert item["sample_selection_version"] == (
            "first-record-study-authenticated-recipe-horizon.v3"
        )
        assert item["assessment_checked_count"] == 1 and item["assessment_pending_count"] == 0
        assert item["directional_hit_rate"] is None
    assert _snapshot(database) == original


@pytest.mark.parametrize("display_filter", ["action", "status", "version", "page", "since"])
def test_complete_recipe_first_selection_precedes_action_status_version_time_and_page_filters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, display_filter: str,
):
    database = _database(tmp_path)
    old_version, new_version = METHOD + ":a-old", METHOD + ":z-new"
    with database.session() as session:
        _add_record(session, "first-neutral", study_id="same-study", outlook={
            "action": "neutral", "method_version": old_version,
        }, observation={"status": "pending"})
        _add_record(session, "later-long", study_id="same-study", outlook={
            "method_version": new_version, "recorded_at": NOW + timedelta(minutes=1),
            "window_start_at": NOW + timedelta(minutes=1),
            "window_end_at": NOW + timedelta(minutes=31),
        })
    original = _snapshot(database)
    calls = _controlled_reads(database, monkeypatch)
    filters: dict[str, Any] = {
        "action": {"action": "long"}, "status": {"status": "completed"},
        "version": {"method_version": new_version}, "page": {"limit": 1, "offset": 0},
        "since": {"since": NOW + timedelta(seconds=30)},
    }[display_filter]
    group = _query(database).cohorts(**filters)["items"][0]
    assert group["method_version"] == new_version and group["record_directional_count"] == 1
    assert group["fixed_sample_count"] == group["directional_count"] == 0
    assert group["repeat_count"] == 1 and group["directional_hit_rate"] is None
    assert set(calls) == {"first-neutral", "later-long"}
    assert _snapshot(database) == original


@pytest.mark.parametrize("unknown", ["missing_projection", "invalid_source"])
def test_unknown_earlier_source_blocks_later_known_first_without_promoting_its_repeat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, unknown: str,
):
    database = _database(tmp_path)
    old_version, new_version = METHOD + ":a-old", METHOD + ":z-new"
    with database.session() as session:
        _add_record(session, "unknown-first", study_id="same-study", outlook={
            "method_version": old_version, "action": "neutral",
        })
        for index, identity in enumerate(("known-first", "known-repeat"), 1):
            _add_record(session, identity, study_id="same-study", outlook={
                "method_version": new_version, "recorded_at": NOW + timedelta(seconds=index),
            })
    original = _snapshot(database)
    profile = None if unknown == "missing_projection" else {
        "source_status": "invalid", "source_reason": "controlled_source_original_invalid",
        "method_status": "unverifiable", "stable_recipe_hash": None,
    }
    calls = _controlled_reads(database, monkeypatch, {"unknown-first": profile})
    with _client(database, tmp_path) as client:
        response = client.get(URL + "/cohorts", params={"method_version": new_version})
        assert response.status_code == 200, response.text
        group = response.json()["items"][0]
        assert group["fixed_sample_count"] == group["directional_count"] == 0
        assert group["repeat_count"] == 1 and group["record_directional_count"] == 2
        assert group["assessment_reasons"]["method_identity_ambiguous"] == 1
        assert group["assessment_version"] == "authenticated-recipe-originals.v2"
        assert group["unverified_count"] == 1
        assert group["assessment_checked_count"] == 2 and group["assessment_pending_count"] == 0
        assert group["directional_hit_rate"] is None
        detail = client.get(URL + "/known-first/assessment")
        assert detail.status_code == 200, detail.text
        assert detail.json()["source_status"] == "verified"
        assert detail.json()["selection_status"] == "unchecked"
        assert not detail.json()["directional_usable"]
    assert set(calls) == {"unknown-first", "known-first", "known-repeat"}
    assert _snapshot(database) == original


def test_same_saved_alias_with_different_recipes_has_separate_groups_and_rates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    database = _database(tmp_path)
    profiles: dict[str, dict[str, Any] | None] = {}
    with database.session() as session:
        for index in range(30):
            for recipe in ("a", "b"):
                identity = f"recipe-{recipe}-{index:02}"
                _add_record(session, identity, study_id=f"study-{index:02}", outlook={
                    "recorded_at": NOW + timedelta(seconds=recipe == "b"),
                })
                if recipe == "b":
                    profiles[identity] = {"stable_recipe_hash": RECIPE_B}
    original = _snapshot(database)
    _controlled_reads(database, monkeypatch, profiles)
    with _client(database, tmp_path) as client:
        response = client.get(URL + "/cohorts")
        assert response.status_code == 200, response.text
        assert response.json()["total"] == 2
        for group in response.json()["items"]:
            assert group["fixed_sample_count"] == group["record_directional_count"] == 30
            assert group["distinct_studies"] == 30 and group["repeat_count"] == 0
            assert group["source_verified_count"] == group["label_verified_count"] == 30
            assert group["method_recipe_hashes"] == [group["stable_recipe_hash"]]
            assert group["assessment_reasons"] == {}
            assert group["directional_count"] == group["correct_count"] == 30
            assert group["directional_hit_rate"] == 1.0
            assert group["average_directional_return_pct"] == 2.0
            assert group["assessment_version"] == "authenticated-recipe-originals.v2"
    assert _snapshot(database) == original


def test_thirty_raw_repeats_cannot_unlock_a_certified_rate_in_background_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    database = _database(tmp_path)
    with database.session() as session:
        for index in range(31):
            _add_record(session, f"repeat-{index:02}", study_id="same-study", outlook={
                "recorded_at": NOW + timedelta(seconds=index),
            })
    original = _snapshot(database)
    _controlled_reads(database, monkeypatch)
    group = _query(database).cohorts()["items"][0]
    assert group["record_directional_count"] == group["record_correct_count"] == 31
    assert group["record_average_directional_return_pct"] == 100.0
    assert group["fixed_sample_count"] == group["directional_count"] == group["correct_count"] == 1
    assert group["repeat_count"] == 30 and group["assessment_checked_count"] == 31
    assert group["directional_hit_rate"] is None and group["average_directional_return_pct"] == 2.0
    assert _snapshot(database) == original


@pytest.mark.parametrize("study_count", [29, 30, 31])
def test_only_thirty_distinct_verified_studies_of_one_recipe_unlock_rate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, study_count: int,
):
    database = _database(tmp_path)
    profiles: dict[str, dict[str, Any] | None] = {}
    with database.session() as session:
        for index in range(study_count):
            identity = f"sample-{index:02}"
            _add_record(session, identity, study_id=f"study-{index:02}")
            profiles[identity] = {
                "verified_direction_correct": index < 20,
                "verified_directional_return_pct": 2.0 if index < 20 else -1.0,
            }
    original = _snapshot(database)
    _controlled_reads(database, monkeypatch, profiles)
    with _client(database, tmp_path) as client:
        response = client.get(URL + "/cohorts")
        assert response.status_code == 200, response.text
        group = response.json()["items"][0]
        assert group["fixed_sample_count"] == group["directional_count"] == study_count
        assert group["assessment_checked_count"] == study_count
        assert group["assessment_pending_count"] == 0
        assert group["correct_count"] == 20 and group["unverified_count"] == 0
        assert group["directional_hit_rate"] == (20 / study_count if study_count >= 30 else None)
        assert group["average_directional_return_pct"] == pytest.approx(
            (40 - (study_count - 20)) / study_count,
        )
        assert group["assessment_version"] == "authenticated-recipe-originals.v2"
    assert _snapshot(database) == original


def test_pending_progress_is_real_and_unknown_recipe_has_its_own_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    database = _database(tmp_path)
    with database.session() as session:
        for index in range(31):
            _add_record(session, f"progress-{index:02}", study_id=f"study-{index:02}")
    original = _snapshot(database)
    _controlled_reads(database, monkeypatch, {"progress-30": None})
    with _client(database, tmp_path) as client:
        response = client.get(URL + "/cohorts")
        assert response.status_code == 200, response.text
        assert response.json()["total"] == 2
        group = next(row for row in response.json()["items"] if row["recipe_status"] == "verified")
        unknown = next(row for row in response.json()["items"]
                       if row["recipe_status"] == "unchecked")
        assert group["total"] == group["record_directional_count"] == 30
        assert group["assessment_checked_count"] == 30 and group["assessment_pending_count"] == 0
        assert group["source_verified_count"] == group["label_verified_count"] == 30
        assert group["unverified_count"] == 0
        assert group["directional_count"] == group["correct_count"] == 30
        assert group["directional_hit_rate"] == 1.0
        assert group["average_directional_return_pct"] == 2.0
        assert unknown["total"] == unknown["unverified_count"] == 1
        assert unknown["assessment_reasons"]["assessment_pending"] == 1
        assert "assessment_version" not in unknown
        assert unknown["directional_count"] == unknown["correct_count"] == 0
        assert unknown["directional_hit_rate"] is None
        assert "average_directional_return_pct" in unknown
        assert unknown["average_directional_return_pct"] is None
    assert _snapshot(database) == original


def test_detail_sync_fallback_cannot_skip_prior_unknown_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    database = _database(tmp_path)
    with database.session() as session:
        _add_record(session, "earlier", study_id="same-study")
        _add_record(session, "current", study_id="same-study", outlook={
            "method_version": METHOD + ":new", "recorded_at": NOW + timedelta(seconds=1),
        })
    original = _snapshot(database)
    _controlled_reads(database, monkeypatch, {"earlier": None, "current": None})
    sync_calls: list[str] = []

    def fallback(session: Session, identity: str, *, assessed_at: datetime, archive=None):
        sync_calls.append(identity)
        return _controlled_projection(session, identity, assessed_at)

    monkeypatch.setattr(
        "packages.query_views.analytical_outlooks.assess_outlook_in_session", fallback,
    )
    with _client(database, tmp_path) as client:
        for suffix in ("/current", "/current/assessment"):
            response = client.get(URL + suffix)
            assert response.status_code == 200, response.text
            assessment = response.json()["assessment"] if suffix == "/current" else response.json()
            assert assessment["source_status"] == assessment["label_status"] == "verified"
            assert assessment["selection_status"] == "unchecked"
            assert not assessment["directional_usable"]
            assert "method_identity_ambiguous" in assessment["limitations"]
    assert sync_calls == ["current", "current"] and _snapshot(database) == original

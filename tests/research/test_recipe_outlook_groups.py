"""Recipe group/API integration over real metadata and controlled typed readers.

These controls do not authenticate research originals. Only the expensive basis
reader is replaced; the independent genuine basis/archive gates cover that proof.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from tests.research.test_background_outlook_cohorts import (
    _client,  # pyright: ignore[reportPrivateUsage]
    _controlled_reads,  # pyright: ignore[reportPrivateUsage]
    _query,  # pyright: ignore[reportPrivateUsage]
)
from tests.research.test_certified_outlook_cohorts import (
    METHOD,
    NOW,
    RECIPE_A,
    RECIPE_B,
    URL,
    _add_record,  # pyright: ignore[reportPrivateUsage]
    _snapshot,  # pyright: ignore[reportPrivateUsage]
)
from tests.research.test_fact_store import _database


def test_recipe_group_merges_saved_aliases_and_deep_link_lists_all_originals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    database = _database(tmp_path)
    aliases = [METHOD + ":old", METHOD + ":new"]
    profiles: dict[str, dict[str, Any] | None] = {}
    with database.session() as session:
        for index in range(30):
            identity = f"sample-{index:02}"
            _add_record(session, identity, study_id=f"study-{index:02}", outlook={
                "method_version": aliases[index % 2],
            })
            profiles[identity] = {"verified_direction_correct": index < 20}
        _add_record(session, "repeat", study_id="study-00", outlook={
            "method_version": METHOD + ":third", "recorded_at": NOW + timedelta(seconds=1),
        })
    original = _snapshot(database)
    _controlled_reads(database, monkeypatch, profiles)
    with _client(database, tmp_path) as client:
        response = client.get(URL + "/cohorts")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total"] == 1
        group = payload["items"][0]
        assert group["recipe_status"] == "verified" and group["stable_recipe_hash"] == RECIPE_A
        assert group["method_version"] is None
        assert group["saved_method_versions"] == sorted(aliases + [METHOD + ":third"])
        assert group["total"] == group["record_directional_count"] == 31
        assert group["fixed_sample_count"] == group["directional_count"] == 30
        assert group["repeat_count"] == 1 and group["distinct_studies"] == 30
        assert group["correct_count"] == 20 and group["directional_hit_rate"] == 20 / 30
        assert group["record_average_directional_return_pct"] == 100.0
        assert group["average_directional_return_pct"] == 2.0
        assert group["assessment_checked_count"] == 31 and group["assessment_pending_count"] == 0
        ids: list[str] = []
        for offset in (0, 12, 24):
            records = client.get(URL, params={
                "stable_recipe_hash": RECIPE_A, "limit": 12, "offset": offset,
            })
            assert records.status_code == 200, records.text
            assert records.json()["total"] == 31
            ids.extend(row["outlook_id"] for row in records.json()["items"])
        assert len(ids) == len(set(ids)) == 31 and "repeat" in ids
        detail = client.get(URL + "/repeat")
        assert detail.status_code == 200, detail.text
        assert detail.json()["outlook"]["method_version"] == METHOD + ":third"
        assert detail.json()["assessment"]["selection_status"] == "repeat"
    assert _snapshot(database) == original


def test_same_saved_alias_different_recipes_group_before_pagination_and_keep_separate_rates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    database = _database(tmp_path)
    profiles: dict[str, dict[str, Any] | None] = {}
    with database.session() as session:
        for index in range(30):
            for suffix, recipe in (("a", RECIPE_A), ("b", RECIPE_B)):
                identity = f"{suffix}-{index:02}"
                _add_record(session, identity, study_id=f"study-{index:02}", outlook={
                    "recorded_at": NOW + timedelta(seconds=suffix == "b"),
                })
                profiles[identity] = {
                    "stable_recipe_hash": recipe, "verified_direction_correct": suffix == "a",
                }
    original = _snapshot(database)
    _controlled_reads(database, monkeypatch, profiles)
    with _client(database, tmp_path) as client:
        recipes = []
        for offset in (0, 1):
            response = client.get(URL + "/cohorts", params={"limit": 1, "offset": offset})
            assert response.status_code == 200, response.text
            assert response.json()["total"] == 2 and response.json()["offset"] == offset
            group = response.json()["items"][0]
            recipe = group["stable_recipe_hash"]
            recipes.append(recipe)
            assert group["method_version"] == METHOD and group["saved_method_versions"] == [METHOD]
            assert group["method_recipe_hashes"] == [recipe]
            assert group["total"] == group["fixed_sample_count"] == group["directional_count"] == 30
            assert group["repeat_count"] == 0
            assert "mixed_stable_recipes" not in group["assessment_reasons"]
            assert group["directional_hit_rate"] == (1.0 if recipe == RECIPE_A else 0.0)
            listed = client.get(URL, params={"stable_recipe_hash": recipe, "limit": 100})
            assert listed.status_code == 200, listed.text
            assert listed.json()["total"] == group["total"]
            prefix = "a-" if recipe == RECIPE_A else "b-"
            assert all(row["outlook_id"].startswith(prefix) for row in listed.json()["items"])
        assert recipes == [RECIPE_A, RECIPE_B]
    assert _snapshot(database) == original


@pytest.mark.parametrize("unknown", ["missing", "invalid"])
def test_unknown_original_keeps_its_alias_and_recipe_filter_cannot_hide_prior_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, unknown: str,
):
    database = _database(tmp_path)
    old_alias, new_alias = METHOD + ":old", METHOD + ":new"
    with database.session() as session:
        _add_record(session, "earlier", study_id="same-study", outlook={
            "method_version": old_alias, "action": "neutral",
        })
        for index, identity in enumerate(("later-first", "later-repeat"), 1):
            _add_record(session, identity, study_id="same-study", outlook={
                "method_version": new_alias, "recorded_at": NOW + timedelta(seconds=index),
            })
    profile = None if unknown == "missing" else {
        "source_status": "invalid", "source_reason": "assessment_original_changed",
        "method_status": "unverifiable", "stable_recipe_hash": None,
        "label_status": "unverifiable", "label_reason": "assessment_label_original_missing",
    }
    original = _snapshot(database)
    calls = _controlled_reads(database, monkeypatch, {"earlier": profile})
    with _client(database, tmp_path) as client:
        response = client.get(URL + "/cohorts")
        assert response.status_code == 200, response.text
        assert response.json()["total"] == 2
        unknown_group = next(row for row in response.json()["items"]
                             if row["recipe_status"] == "unchecked")
        assert unknown_group["stable_recipe_hash"] is None
        assert unknown_group["method_version"] == old_alias
        assert unknown_group["saved_method_versions"] == [old_alias]
        assert unknown_group["directional_count"] == 0
        assert unknown_group["directional_hit_rate"] is None
        if unknown == "invalid":
            assert unknown_group["assessment_reasons"]["assessment_original_changed"] == 1
            assert unknown_group["assessment_reasons"]["assessment_label_original_missing"] == 1
            assert unknown_group["assessment_checked_count"] == 1
        else:
            assert unknown_group["assessment_pending_count"] == 1
        known = client.get(URL + "/cohorts", params={
            "stable_recipe_hash": RECIPE_A, "method_version": new_alias,
        })
        assert known.status_code == 200, known.text
        group = known.json()["items"][0]
        assert known.json()["total"] == 1 and group["total"] == 2
        assert group["fixed_sample_count"] == group["directional_count"] == 0
        assert group["repeat_count"] == 1
        assert group["assessment_reasons"]["method_identity_ambiguous"] == 1
        listed = client.get(URL, params={"recipe_status": "unchecked"})
        assert listed.status_code == 200, listed.text
        assert [row["outlook_id"] for row in listed.json()["items"]] == ["earlier"]
        detail = client.get(URL + "/later-first/assessment")
        assert detail.status_code == 200 and detail.json()["selection_status"] == "unchecked"
        assert not detail.json()["directional_usable"]
    assert set(calls) == {"earlier", "later-first", "later-repeat"}
    assert _snapshot(database) == original


@pytest.mark.parametrize("display_filter", ["action", "status", "version", "run", "time", "page"])
def test_recipe_selection_precedes_every_display_filter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, display_filter: str,
):
    database = _database(tmp_path)
    with database.session() as session:
        _add_record(session, "first-neutral", study_id="same-study", outlook={
            "action": "neutral", "method_version": METHOD + ":old",
        }, observation={"status": "pending"})
        _add_record(session, "later-long", study_id="same-study", outlook={
            "method_version": METHOD + ":new", "recorded_at": NOW + timedelta(minutes=1),
            "window_start_at": NOW + timedelta(minutes=1),
        })
    original = _snapshot(database)
    calls = _controlled_reads(database, monkeypatch)
    filters: dict[str, Any] = {
        "action": {"action": "long"}, "status": {"status": "completed"},
        "version": {"method_version": METHOD + ":new"}, "run": {"run_id": "run-later-long"},
        "time": {"since": NOW + timedelta(seconds=30)}, "page": {"limit": 1, "offset": 0},
    }[display_filter]
    group = _query(database).cohorts(stable_recipe_hash=RECIPE_A, **filters)["items"][0]
    assert group["action"] == "long" and group["record_directional_count"] == 1
    assert group["fixed_sample_count"] == group["directional_count"] == 0
    assert group["repeat_count"] == 1 and group["directional_hit_rate"] is None
    assert set(calls) == {"first-neutral", "later-long"}
    assert _snapshot(database) == original


def test_unknown_other_study_does_not_erase_thirty_genuine_selected_samples(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    database = _database(tmp_path)
    with database.session() as session:
        for index in range(31):
            _add_record(session, f"sample-{index:02}", study_id=f"study-{index:02}")
    original = _snapshot(database)
    _controlled_reads(database, monkeypatch, {"sample-30": None})
    groups = _query(database).cohorts()["items"]
    assert len(groups) == 2
    known = next(group for group in groups if group["recipe_status"] == "verified")
    pending = next(group for group in groups if group["recipe_status"] == "unchecked")
    assert known["total"] == known["assessment_checked_count"] == 30
    assert known["directional_count"] == 30 and known["directional_hit_rate"] == 1.0
    assert known["assessment_pending_count"] == 0
    assert pending["total"] == pending["assessment_pending_count"] == 1
    assert pending["assessment_checked_count"] == pending["directional_count"] == 0
    assert pending["directional_hit_rate"] is None and "assessment_version" not in pending
    assert pending["assessment_reasons"]["assessment_pending"] == 1
    assert _snapshot(database) == original


def test_unchecked_first_and_unmatured_label_do_not_gain_direction_or_veto_other_studies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    database = _database(tmp_path)
    with database.session() as session:
        _add_record(session, "unknown-prior", study_id="blocked-study")
        _add_record(session, "blocked-known", study_id="blocked-study", outlook={
            "recorded_at": NOW + timedelta(seconds=1),
        })
        _add_record(session, "not-due", study_id="future-window-study")
        for index in range(30):
            _add_record(session, f"valid-{index:02}", study_id=f"study-{index:02}")
    original = _snapshot(database)
    _controlled_reads(database, monkeypatch, {
        "unknown-prior": None,
        "not-due": {"label_status": "not_due", "label_reason": "assessment_window_not_due"},
    })
    known = _query(database).cohorts(stable_recipe_hash=RECIPE_A)["items"][0]
    assert known["total"] == 32 and known["fixed_sample_count"] == 31
    assert known["directional_count"] == 30 and known["directional_hit_rate"] == 1.0
    assert known["assessment_reasons"]["method_identity_ambiguous"] == 1
    assert known["assessment_reasons"]["assessment_window_not_due"] == 1
    assert known["unverified_count"] == 2
    assert known["assessment_version"] == "authenticated-recipe-originals.v2"
    assert _snapshot(database) == original


@pytest.mark.parametrize("filters", [
    {"stable_recipe_hash": "not-a-hash"}, {"stable_recipe_hash": "a" * 63},
    {"stable_recipe_hash": "G" * 64}, {"stable_recipe_hash": ""},
    {"recipe_status": "verified"}, {"recipe_status": "anything"},
    {"stable_recipe_hash": RECIPE_A, "recipe_status": "unchecked"},
])
def test_recipe_filters_reject_invalid_or_conflicting_values_before_reading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, filters: dict[str, str],
):
    database = _database(tmp_path)
    with database.session() as session:
        _add_record(session, "one", study_id="study")
    original = _snapshot(database)
    calls = _controlled_reads(database, monkeypatch)
    for read in (_query(database).list, _query(database).cohorts):
        with pytest.raises(ValueError, match="analytical_recipe_"):
            read(**filters)
    with _client(database, tmp_path) as client:
        for suffix in ("", "/cohorts"):
            response = client.get(URL + suffix, params=filters)
            assert response.status_code in {400, 422}, response.text
    assert not calls and _snapshot(database) == original


def test_plain_ledger_list_needs_no_recipe_reader_and_single_alias_filter_is_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    database = _database(tmp_path)
    with database.session() as session:
        _add_record(session, "old", study_id="one", outlook={"method_version": METHOD + ":old"})
        _add_record(session, "new", study_id="two", outlook={"method_version": METHOD + ":new"})
    original = _snapshot(database)
    calls = _controlled_reads(database, monkeypatch)
    assert _query(database).list()["total"] == 2 and not calls
    filtered = _query(database).cohorts(method_version=METHOD + ":new")
    assert filtered["total"] == 1
    group = filtered["items"][0]
    assert group["method_version"] == METHOD + ":new"
    assert group["saved_method_versions"] == [METHOD + ":new"]
    assert group["total"] == group["fixed_sample_count"] == 1
    assert _snapshot(database) == original


def test_verified_recipe_groups_precede_unknown_groups_without_losing_paginated_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    database = _database(tmp_path)
    aliases = {
        "known-crypto": "known-crypto-alias", "known-metal": "known-metal-alias",
        "unknown-a": "a-unknown-alias", "unknown-z": "z-unknown-alias",
    }
    with database.session() as session:
        for identity, alias in aliases.items():
            _add_record(session, identity, study_id=f"study-{identity}", outlook={
                "method_version": alias,
                "market": "metal" if identity == "known-metal" else "crypto",
            })
    original = _snapshot(database)
    _controlled_reads(database, monkeypatch, {
        "known-metal": {"stable_recipe_hash": RECIPE_B}, "unknown-a": None, "unknown-z": None,
    })
    with _client(database, tmp_path) as client:
        first = client.get(URL + "/cohorts", params={"limit": 2, "offset": 0})
        second = client.get(URL + "/cohorts", params={"limit": 2, "offset": 2})
        assert first.status_code == second.status_code == 200
        first_page, second_page = first.json(), second.json()
        assert first_page["total"] == second_page["total"] == 4
        assert first_page["offset"] == 0 and second_page["offset"] == 2
        assert [group["recipe_status"] for group in first_page["items"]] == ["verified"] * 2
        assert [group["stable_recipe_hash"] for group in first_page["items"]] == [
            RECIPE_A, RECIPE_B,
        ]
        assert [group["recipe_status"] for group in second_page["items"]] == ["unchecked"] * 2
        assert [group["method_version"] for group in second_page["items"]] == [
            aliases["unknown-a"], aliases["unknown-z"],
        ]
        all_groups = first_page["items"] + second_page["items"]
        assert {group["method_version"] for group in all_groups} == set(aliases.values())
        assert sum(group["total"] for group in all_groups) == 4
        unknown_only = client.get(URL + "/cohorts", params={"recipe_status": "unchecked"})
        assert unknown_only.status_code == 200 and unknown_only.json()["total"] == 2
        assert unknown_only.json()["items"] == second_page["items"]
    assert _snapshot(database) == original

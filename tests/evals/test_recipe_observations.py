"""Pure selection controls; caller-supplied identities certify no source fixtures."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

import pytest

from packages.evals.recipe_observations import (
    SELECTION_VERSION,
    RecipeObservationCandidate,
    select_recipe_observations,
)

NOW = datetime(2026, 10, 4, 0, 0, tzinfo=UTC)
RECIPE_A = "a" * 64
RECIPE_B = "b" * 64


def _candidate(identity: str, *, second: int = 0, **changes) -> RecipeObservationCandidate:
    return replace(RecipeObservationCandidate(
        outlook_id=identity, recorded_at=NOW + timedelta(seconds=second),
        market="crypto", instrument="BTC-USDT-SWAP", method_family="ai_synthesis",
        horizon="30m", study_id="study-one", stable_recipe_hash=RECIPE_A, source_verified=True,
    ), **changes)


def test_authenticated_recipe_first_positions_are_separate_and_order_independent():
    first = _candidate("first", second=-3)
    repeat = _candidate("repeat", second=-2)
    other = _candidate("other-recipe", second=-1, stable_recipe_hash=RECIPE_B)
    candidates = (repeat, other, first)
    original = tuple(candidates)
    selected = select_recipe_observations(candidates, now=NOW)
    assert selected.selection_version == SELECTION_VERSION
    assert selected.selection_version == "first-record-study-authenticated-recipe-horizon.v3"
    assert dict(selected.statuses) == {
        "first": "selected", "repeat": "repeat", "other-recipe": "selected",
    }
    assert selected == select_recipe_observations(reversed(candidates), now=NOW)
    assert tuple(candidates) == original


@pytest.mark.parametrize("unknown_fields", [
    {"source_verified": False, "stable_recipe_hash": RECIPE_A},
    {"source_verified": True, "stable_recipe_hash": None},
    {"source_verified": True, "stable_recipe_hash": ""},
])
def test_unknown_first_blocks_later_new_recipes_and_never_promotes_the_next_repeat(
    unknown_fields,
):
    candidates = (
        _candidate("unknown", second=-4, **unknown_fields),
        _candidate("first-known-a", second=-3),
        _candidate("repeat-a", second=-2),
        _candidate("first-known-b", second=-1, stable_recipe_hash=RECIPE_B),
        _candidate("repeat-b", stable_recipe_hash=RECIPE_B),
    )
    expected = {
        "unknown": "unchecked", "first-known-a": "unchecked", "repeat-a": "repeat",
        "first-known-b": "unchecked", "repeat-b": "repeat",
    }
    assert dict(select_recipe_observations(candidates, now=NOW).statuses) == expected
    # A fresh invocation has the same immutable first reservations.
    assert dict(select_recipe_observations(iter(candidates), now=NOW).statuses) == expected


def test_known_first_before_unknown_remains_selected_and_all_its_updates_are_repeats():
    candidates = (
        _candidate("first-a", second=-4),
        _candidate("unknown", second=-3, source_verified=False),
        _candidate("repeat-a", second=-2),
        _candidate("first-b", second=-1, stable_recipe_hash=RECIPE_B),
        _candidate("repeat-b", stable_recipe_hash=RECIPE_B),
    )
    assert dict(select_recipe_observations(candidates, now=NOW).statuses) == {
        "first-a": "selected", "unknown": "unchecked", "repeat-a": "repeat",
        "first-b": "unchecked", "repeat-b": "repeat",
    }


@pytest.mark.parametrize("dimension,different", [
    ("market", "metal"), ("instrument", "ETH-USDT-SWAP"),
    ("method_family", "daily_rule"), ("horizon", "24h"), ("study_id", "study-two"),
])
def test_unknown_scope_isolated_by_market_instrument_family_horizon_and_study(
    dimension, different,
):
    candidates = (
        _candidate("unknown", second=-2, source_verified=False),
        _candidate("other-scope", second=-1, **{dimension: different}),
        _candidate("same-scope"),
    )
    assert dict(select_recipe_observations(candidates, now=NOW).statuses) == {
        "unknown": "unchecked", "other-scope": "selected", "same-scope": "unchecked",
    }


@pytest.mark.parametrize("study", [None, ""])
def test_missing_study_is_unkeyed_and_cannot_block_or_supply_a_keyed_first(study):
    candidates = (
        _candidate("unkeyed-unknown", second=-2, study_id=study, source_verified=False),
        _candidate("unkeyed-known", second=-1, study_id=study),
        _candidate("keyed"),
    )
    assert dict(select_recipe_observations(candidates, now=NOW).statuses) == {
        "unkeyed-unknown": "unkeyed", "unkeyed-known": "unkeyed", "keyed": "selected",
    }


def test_equal_recorded_time_uses_outlook_id_even_for_an_unknown_barrier():
    candidates = (
        _candidate("z-known"), _candidate("a-unknown", source_verified=False),
        _candidate("zz-repeat"),
    )
    selected = select_recipe_observations(candidates, now=NOW)
    assert list(selected.statuses.items()) == [
        ("a-unknown", "unchecked"), ("z-known", "unchecked"), ("zz-repeat", "repeat"),
    ]
    assert selected == select_recipe_observations(reversed(candidates), now=NOW)


def test_unknown_record_never_becomes_repeat_merely_from_its_unverified_hash():
    candidates = (
        _candidate("known", second=-1),
        _candidate("unknown", stable_recipe_hash=RECIPE_A, source_verified=False),
    )
    assert dict(select_recipe_observations(candidates, now=NOW).statuses) == {
        "known": "selected", "unknown": "unchecked",
    }


def test_future_records_are_absent_and_the_cutoff_record_is_included():
    candidates = (
        _candidate("cutoff"), _candidate("future-known", second=1),
        _candidate("future-unknown", second=1, source_verified=False),
    )
    assert dict(select_recipe_observations(candidates, now=NOW).statuses) == {"cutoff": "selected"}
    assert dict(
        select_recipe_observations(candidates, now=NOW + timedelta(seconds=1)).statuses,
    ) == {
        "cutoff": "selected", "future-known": "repeat", "future-unknown": "unchecked",
    }


def test_aware_timezone_offsets_preserve_the_instant_and_stable_tie_order():
    local = NOW.astimezone(timezone(timedelta(hours=8)))
    candidates = (_candidate("b-utc"), _candidate("a-local", recorded_at=local))
    assert dict(select_recipe_observations(candidates, now=local).statuses) == {
        "a-local": "selected", "b-utc": "repeat",
    }


def test_naive_cutoff_is_rejected_even_with_no_candidates():
    with pytest.raises(ValueError, match="recipe_selection_clock_timezone_required"):
        select_recipe_observations((), now=NOW.replace(tzinfo=None))


def test_naive_recorded_time_is_rejected_without_assuming_a_local_timezone():
    candidate = _candidate("naive", recorded_at=NOW.replace(tzinfo=None))
    with pytest.raises(ValueError, match="recipe_observation_recorded_at_timezone_required"):
        select_recipe_observations((candidate,), now=NOW)


def test_duplicate_ids_cannot_overwrite_a_frozen_first_status():
    candidates = (_candidate("same-id", second=-1), _candidate("same-id"))
    with pytest.raises(ValueError, match="recipe_selection_duplicate_outlook_id"):
        select_recipe_observations(candidates, now=NOW)


def test_empty_input_still_identifies_the_new_selection_version():
    selected = select_recipe_observations(iter(()), now=NOW)
    assert dict(selected.statuses) == {} and selected.selection_version == SELECTION_VERSION

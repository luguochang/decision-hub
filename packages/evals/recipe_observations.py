"""Pure first-record selection using independently authenticated recipe identities.

The caller supplies complete metadata at the cutoff before display filters,
and selects the intended historical/prospective cohort. This module verifies
no original, assigns no labels, and grants no provenance to supplied hashes.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Literal

SELECTION_VERSION = "first-record-study-authenticated-recipe-horizon.v3"
type RecipeSelectionStatus = Literal["selected", "repeat", "unchecked", "unkeyed"]
type _Scope = tuple[str, str, str, str, str]


@dataclass(frozen=True)
class RecipeObservationCandidate:
    outlook_id: str
    recorded_at: datetime
    market: str
    instrument: str
    method_family: str
    horizon: str
    study_id: str | None
    stable_recipe_hash: str | None
    source_verified: bool


@dataclass(frozen=True)
class RecipeObservationSelection:
    statuses: Mapping[str, RecipeSelectionStatus]
    selection_version: str = SELECTION_VERSION


def _aware_utc(value: datetime, reason: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(reason)
    return value.astimezone(UTC)


def select_recipe_observations(
    candidates: Iterable[RecipeObservationCandidate], *, now: datetime,
) -> RecipeObservationSelection:
    """Reserve first positions before labels, eligibility and display filters.

    An earlier unknown source may belong to any recipe in its Study scope.
    Later first occurrences remain unchecked, but are still reserved: another
    occurrence of that known recipe is always a repeat and cannot be promoted.
    Naive timestamps are rejected; DB callers must normalize their timestamps
    at the persistence boundary. Future records contribute no output or state.
    """
    cutoff = _aware_utc(now, "recipe_selection_clock_timezone_required")
    available: list[tuple[datetime, RecipeObservationCandidate]] = []
    identities: set[str] = set()
    for candidate in candidates:
        recorded_at = _aware_utc(
            candidate.recorded_at, "recipe_observation_recorded_at_timezone_required",
        )
        if recorded_at > cutoff:
            continue
        if candidate.outlook_id in identities:
            raise ValueError("recipe_selection_duplicate_outlook_id")
        identities.add(candidate.outlook_id)
        available.append((recorded_at, candidate))
    available.sort(key=lambda item: (item[0], item[1].outlook_id))
    statuses: dict[str, RecipeSelectionStatus] = {}
    unknown_scopes: set[_Scope] = set()
    seen: dict[_Scope, set[str]] = {}
    for _, candidate in available:
        if not candidate.study_id:
            statuses[candidate.outlook_id] = "unkeyed"
            continue
        scope = (
            candidate.market, candidate.instrument, candidate.method_family,
            candidate.horizon, candidate.study_id,
        )
        recipe = candidate.stable_recipe_hash
        if not candidate.source_verified or not recipe:
            statuses[candidate.outlook_id] = "unchecked"
            unknown_scopes.add(scope)
            continue
        scoped_recipes = seen.setdefault(scope, set())
        if recipe in scoped_recipes:
            statuses[candidate.outlook_id] = "repeat"
        elif scope in unknown_scopes:
            statuses[candidate.outlook_id] = "unchecked"
        else:
            statuses[candidate.outlook_id] = "selected"
        scoped_recipes.add(recipe)
    return RecipeObservationSelection(statuses=MappingProxyType(statuses))

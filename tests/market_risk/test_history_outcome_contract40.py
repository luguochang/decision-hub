"""Synthetic wire controls only; no durable source/financial qualification claim."""

import copy
import json
from pathlib import Path

import pytest
from fastapi import HTTPException
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from apps.hub_api.history_outcome_routes import history_outcome_router
from packages.contracts_py.decision_hub_contracts.generated.history_outcome_v2 import (
    HistoryOutcomeObservationReplyV2,
    HistoryOutcomeScoreReplyV2,
    HistoryOutcomeStatusV2,
)

DEV = Path(__file__).resolve().parents[2]
FIXTURE = DEV / "apps/decision-desk/src/product/__fixtures__/history-outcome-control40.json"


def fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def reject(model, data, path, replacement):
    value = copy.deepcopy(data)
    parent = value
    for key in path[:-1]:
        parent = parent[key]
    parent[path[-1]] = replacement
    with pytest.raises(ValidationError):
        model.model_validate_json(json.dumps(value))


def test_status_fixed_census_clock_and_order():
    value = fixture()["status"]
    canonical = json.loads(
        (DEV / "contracts/schemas/history_outcome_v2.schema.yaml").read_text(encoding="utf-8")
    )
    Draft202012Validator(canonical).validate(value)
    parsed = HistoryOutcomeStatusV2.model_validate_json(json.dumps(value))
    assert parsed.expected_total == 48 and not parsed.current_originals_verified
    for path, replacement in (
        (("financial_qualified",), True),
        (("owner_authenticated",), 1),
        (("publication_counts", "blocked"), 45),
        (("paired_count",), 3),
        (("latest_score", "identifier"), "heval2_" + "e" * 48),
        (("opportunities", 1, "scope_id"), value["opportunities"][0]["scope_id"]),
        (("opportunities", 0, "actual_cutoff_at"), "2026-10-10T11:00:00.000001Z"),
        (("independently_confirmed_registered_at",), None),
        (("actual_as_of",), "2026-10-13T12:00:00"),
        (("unexpected",), True),
    ):
        reject(HistoryOutcomeStatusV2, value, path, replacement)
    value["pagination"]["offset"] = 30
    value["opportunities"] = fixture()["opportunities"][30:]
    assert len(HistoryOutcomeStatusV2.model_validate_json(json.dumps(value)).opportunities) == 18


def test_full_score_pairs_nulls_and_original_bindings():
    value = fixture()["scoreReply"]
    parsed = HistoryOutcomeScoreReplyV2.model_validate_json(json.dumps(value))
    assert parsed.score.cases[0].acceptance is not None
    for path, replacement in (
        (("protocol_id",), "heval2_" + "e" * 48),
        (("score", "cases", 0, "acceptance"), None),
        (("score", "fixture_mode"), True),
        (("score", "paired_count"), 0),
        (("score", "targets", 0, "conditional_brier"), None),
        (("score", "targets", 0, "n_baseline"), 2),
        (("score", "targets", 0, "baseline_brier"), "1.000000000000000000000000001"),
        (("score", "source_files", 0, 1), "invalid"),
        (("score", "publication_counts", "on_time"), 0),
        (("score", "targets", 0, "ci"), [0, 1]),
    ):
        reject(HistoryOutcomeScoreReplyV2, value, path, replacement)
    value["score"]["targets"][0]["paired_gain"] = "1E-28"
    assert (
        HistoryOutcomeScoreReplyV2.model_validate_json(json.dumps(value))
        .score.targets[0]
        .paired_gain
        == "1E-28"
    )


def test_observation_subject_is_not_acceptance_and_default_api_is_closed():
    value = fixture()["observationReply"]
    assert (
        HistoryOutcomeObservationReplyV2.model_validate_json(
            json.dumps(value)
        ).observation.accepted_at
        is None
    )
    for path, replacement in (
        (("observation", "accepted_at"), "2026-10-13T12:00:02Z"),
        (("observation", "observed_count"), 1439),
        (("observation", "classification"), "late_diagnostic"),
        (("observation", "anchor_price"), "-1E-28"),
        (("observation", "label_cutoff_at"), "2026-10-10T11:00:00.000001Z"),
    ):
        reject(HistoryOutcomeObservationReplyV2, value, path, replacement)
    router = history_outcome_router()
    assert len(router.routes) == 3 and all(route.methods == {"GET"} for route in router.routes)
    with pytest.raises(HTTPException) as caught:
        router.routes[0].endpoint(None, value["protocol_id"], 0, 30)
    assert caught.value.status_code == 503

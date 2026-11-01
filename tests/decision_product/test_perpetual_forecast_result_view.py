"""Owner-bound, on-demand view of immutable perpetual forecast outcomes."""

import importlib
import importlib.util
import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import event, select

from tests.decision_product.test_okx_perpetual_original import (
    persisted_original as persisted_original,
)
from tests.decision_product.test_perpetual_advice_query import (
    current,
    persisted_publication,
)
from tests.decision_product.test_perpetual_forecast_experience import future_pages
from tests.decision_product.test_perpetual_multi_horizon import selected_command
from tests.decision_product.test_perpetual_research_binding import OWNER
from tests.decision_product.test_perpetual_research_binding import (
    binding_case as binding_case,
)
from tests.decision_product.test_perpetual_research_entry import BASE, client_for

MODULE = "packages.kernel.decision_hub_kernel.application.perpetual_forecast_result_view"


def enable_pure_publication(monkeypatch):
    """Supply the review-original catalog required by the current pure fixture."""
    from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash
    from tests.decision_product import test_perpetual_action_publication as publication_fixture

    packet_builder = publication_fixture.packet_for

    def with_catalog(request, materials):
        catalog = {
            "originals": [
                {
                    "source_kind": "server_material",
                    "role": "close_window",
                    "source_ref": {
                        "ref": "result-view-pure-close-window",
                        "hash": canonical_hash(materials["close_window"]),
                    },
                }
            ]
        }
        return packet_builder(request, {**materials, "review_originals": catalog})

    monkeypatch.setattr(publication_fixture, "packet_for", with_catalog)


def result_query(case, *, now):
    assert importlib.util.find_spec(MODULE), "on-demand forecast result query is missing"
    return importlib.import_module(MODULE).PerpetualForecastResultQuery(
        case["db"], now=lambda: now
    )


def target_end(derived):
    return datetime.fromisoformat(
        derived.bound.target.window_end.isoformat().replace("Z", "+00:00")
        if isinstance(derived.bound.target.window_end, datetime)
        else derived.bound.target.window_end.replace("Z", "+00:00")
    )


def test_owner_result_states_and_route_are_read_only(binding_case, monkeypatch):
    case = binding_case
    enable_pure_publication(monkeypatch)
    derived = persisted_publication(case)
    end = target_end(derived)

    not_due = result_query(case, now=end - timedelta(seconds=1)).read(
        OWNER, case["run_id"]
    )
    assert not_due["status"] == "not_due"
    assert not_due["reason_code"] == "forecast_target_not_due"
    assert not_due["target"]["window_end"] == derived.bound.target.model_dump(mode="json")[
        "window_end"
    ]
    assert not_due["result"] is None

    due = end + timedelta(seconds=10)
    pending = result_query(case, now=due).read(OWNER, case["run_id"])
    assert pending["status"] == "pending"
    assert pending["reason_code"] == "forecast_result_not_recorded"
    assert pending["result"] is None
    from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash

    assert canonical_hash(pending["target"]) == pending["target_ref"]["hash"]
    result_module = importlib.import_module(MODULE)
    lexical_target = dict(pending["target"])
    for field in ("window_start", "window_end"):
        assert lexical_target[field].endswith("Z") and "." not in lexical_target[field]
        lexical_target[field] = lexical_target[field][:-1] + ".000Z"
    target_hash = canonical_hash(lexical_target)
    target_ref = {"ref": "perp_test_target_" + target_hash, "hash": target_hash}
    lexical_view = {**pending, "target": lexical_target, "target_ref": target_ref}
    if "final_target_ref" in lexical_view:
        lexical_view["final_target_ref"] = target_ref
    lexical_wire = result_module.PerpetualForecastResultView.model_validate(
        lexical_view
    ).model_dump(mode="json")
    assert lexical_wire["target"] == lexical_target
    assert canonical_hash(lexical_wire["target"]) == lexical_wire["target_ref"]["hash"]
    assert "final_target_ref" not in pending

    client, _, collector = client_for(case, clock=lambda: due)
    statements = []

    def record(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement.lstrip().split(None, 1)[0].upper())

    event.listen(case["db"].engine, "before_cursor_execute", record)
    try:
        response = client.get(BASE + "/research/results/" + case["run_id"])
    finally:
        event.remove(case["db"].engine, "before_cursor_execute", record)
    assert response.status_code == 200, response.text
    assert response.json() == pending
    assert not set(statements) & {"INSERT", "UPDATE", "DELETE", "REPLACE"}
    assert collector.calls == 0
    assert current(case, now=due)["status"] == "expired"

    anonymous, _, _ = client_for(case, clock=lambda: due, pair=False)
    assert (
        anonymous.get(BASE + "/research/results/" + case["run_id"]).status_code == 403
    )
    from tests.decision_product import test_perpetual_research_entry as entry_fixture

    monkeypatch.setattr(entry_fixture, "OWNER", "valid-foreign-result-owner")
    foreign, _, _ = entry_fixture.client_for(case, clock=lambda: due)
    denied = foreign.get(BASE + "/research/results/" + case["run_id"])
    assert denied.status_code == 403
    assert denied.json()["detail"] == "perpetual_forecast_result_owner_mismatch"


def test_observed_result_replays_archive_and_corruption_is_not_pending(
    binding_case, monkeypatch
):
    case = binding_case
    enable_pure_publication(monkeypatch)
    derived = persisted_publication(case)
    end = target_end(derived)
    capture_ids = future_pages(case, end, prefix="result_view_")
    now = end + timedelta(seconds=10)
    experience_module = importlib.import_module(
        "packages.kernel.decision_hub_kernel.application.perpetual_forecast_experience"
    )
    original = experience_module.PerpetualForecastExperienceService(
        case["db"], case["source"], clock=lambda: now
    ).record(run_id=case["run_id"], capture_ids=capture_ids)
    body = original.body
    lexical_estimate = dict(body["direction_estimate"])
    assert lexical_estimate["as_of"].endswith("Z") and "." not in lexical_estimate["as_of"]
    lexical_estimate["as_of"] = lexical_estimate["as_of"][:-1] + ".000Z"
    result_module = importlib.import_module(MODULE)
    lexical_wire = result_module.ObservedForecastResult(
        experience_ref=original.ref,
        snapshot_ref=body["snapshot_ref"],
        final_target_ref=body["final_target_ref"],
        frozen_forecast_ref=body["frozen_forecast_ref"],
        market_distribution_ref=body["market_distribution_ref"],
        method_ref=body["method_ref"],
        target_family_ref=body["target_family_ref"],
        label_rule_ref=body["label_rule_ref"],
        direction_estimate=lexical_estimate,
        actual_category=body["category"],
        actual_return_fraction=body["return_fraction"],
        multiclass_brier=body["multiclass_brier"],
        known_at=body["known_at"],
        recorded_at=body["recorded_at"],
        gaps=body["gaps"],
        calculation_method_hash=body["calculation_method_hash"],
        qualification=body["qualification"],
        financial_qualified=body["financial_qualified"],
        skill_consumed=body["skill_consumed"],
        trade_outcome=body["trade_outcome"],
    ).model_dump(mode="json")
    assert lexical_wire["direction_estimate"] == lexical_estimate

    observed = result_query(case, now=now).read(OWNER, case["run_id"])
    assert observed["status"] == "observed" and observed["reason_code"] is None
    result = observed["result"]
    assert result["experience_ref"] == original.ref.model_dump(mode="json")
    assert result["direction_estimate"] == original.body["direction_estimate"]
    assert "final_target_ref" not in observed
    assert result["final_target_ref"] == observed["target_ref"]
    assert result["actual_category"] == original.body["category"]
    assert result["actual_return_fraction"] == original.body["return_fraction"]
    assert result["multiclass_brier"] == original.body["multiclass_brier"]
    assert result["known_at"] == original.body["known_at"]
    assert result["financial_qualified"] is False
    assert result["trade_outcome"] is False

    from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash

    assert canonical_hash(observed["target"]) == observed["target_ref"]["hash"]
    client, _, _ = client_for(case, clock=lambda: now)
    statements = []

    def record(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement.lstrip().split(None, 1)[0].upper())

    event.listen(case["db"].engine, "before_cursor_execute", record)
    try:
        response = client.get(BASE + "/research/results/" + case["run_id"])
    finally:
        event.remove(case["db"].engine, "before_cursor_execute", record)
    assert response.status_code == 200, response.text
    assert response.json() == observed
    assert not set(statements) & {"INSERT", "UPDATE", "DELETE", "REPLACE"}

    from packages.kernel.decision_hub_kernel.persistence.db import RawCaptureRecord

    with case["db"].session() as session:
        digest = session.get(RawCaptureRecord, capture_ids[0]).body_hash
    raw_path = case["archive"].root / digest[:2] / digest
    raw_path.write_bytes(raw_path.read_bytes() + b" ")
    broken = client.get(BASE + "/research/results/" + case["run_id"])
    assert broken.status_code == 409
    assert broken.json()["detail"] == "perpetual_forecast_result_original_unavailable"


def test_observed_query_accepts_authenticated_legacy_target_timestamp_spelling(
    binding_case, monkeypatch
):
    """A strict reader may return an older, hash-bound ``.000Z`` original.

    Exercise the complete observed-query comparison rather than merely the DTO:
    the typed target is for validation and time arithmetic, while identity stays
    bound to the authenticated raw target bytes.
    """
    from copy import deepcopy

    from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash
    from packages.kernel.decision_hub_kernel.ports.perpetual_forecast_experience import (
        experience_original,
    )

    case = binding_case
    enable_pure_publication(monkeypatch)
    derived = persisted_publication(case)
    end = target_end(derived)
    capture_ids = future_pages(case, end, prefix="result_view_lexical_target_")
    now = end + timedelta(seconds=10)
    experience_module = importlib.import_module(
        "packages.kernel.decision_hub_kernel.application.perpetual_forecast_experience"
    )
    experience_module.PerpetualForecastExperienceService(
        case["db"], case["source"], clock=lambda: now
    ).record(run_id=case["run_id"], capture_ids=capture_ids)

    result_module = importlib.import_module(MODULE)
    read_publication = result_module.read_perpetual_published_originals_in_session
    read_experience = result_module.read_forecast_experience_in_session
    lexical = {}

    def publication_with_legacy_spelling(*args, **kwargs):
        value = deepcopy(read_publication(*args, **kwargs))
        target = dict(value["target"])
        for field in ("window_start", "window_end"):
            assert target[field].endswith("Z") and "." not in target[field]
            target[field] = target[field][:-1] + ".000Z"
        target_hash = canonical_hash(target)
        target_ref = {"ref": "perp_test_target_" + target_hash, "hash": target_hash}
        value["target"] = target
        value["snapshot"]["bindings"]["final_target"] = target_ref
        lexical.update(target=target, target_ref=target_ref)
        return value

    def experience_with_legacy_spelling(*args, **kwargs):
        authenticated = read_experience(*args, **kwargs)
        body = authenticated.body
        body["target"] = lexical["target"]
        body["final_target_ref"] = lexical["target_ref"]
        return experience_original(body)

    monkeypatch.setattr(
        result_module,
        "read_perpetual_published_originals_in_session",
        publication_with_legacy_spelling,
    )
    monkeypatch.setattr(
        result_module,
        "read_forecast_experience_in_session",
        experience_with_legacy_spelling,
    )

    observed = result_query(case, now=now).read(OWNER, case["run_id"])
    assert observed["status"] == "observed"
    assert observed["target"] == lexical["target"]
    assert observed["target_ref"] == lexical["target_ref"]
    assert observed["result"]["final_target_ref"] == lexical["target_ref"]


@pytest.mark.parametrize(
    "changed", ["payload", "hash", "bad_json", "foreign_run", "future"]
)
def test_mature_result_never_downgrades_an_invalid_experience_to_pending(
    binding_case, monkeypatch, changed
):
    from packages.kernel.decision_hub_kernel.persistence.db import RunRoleOutputRecord
    from packages.kernel.decision_hub_kernel.ports.perpetual_forecast_experience import (
        ROLE,
        experience_original,
    )

    case = binding_case
    enable_pure_publication(monkeypatch)
    derived = persisted_publication(case)
    end = target_end(derived)
    capture_ids = future_pages(case, end, prefix="result_view_invalid_")
    recorded_at = end + timedelta(seconds=10)
    experience_module = importlib.import_module(
        "packages.kernel.decision_hub_kernel.application.perpetual_forecast_experience"
    )
    experience_module.PerpetualForecastExperienceService(
        case["db"], case["source"], clock=lambda: recorded_at
    ).record(run_id=case["run_id"], capture_ids=capture_ids)
    read_at = recorded_at

    if changed != "future":
        with case["db"].session() as session:
            row = session.scalar(
                select(RunRoleOutputRecord).where(
                    RunRoleOutputRecord.run_id == case["run_id"],
                    RunRoleOutputRecord.role == ROLE,
                )
            )
            assert row is not None
            if changed == "payload":
                body = json.loads(row.payload_json)
                body["gaps"] = ["tampered-experience-payload"]
                row.payload_json = experience_original(body).body_json
            elif changed == "hash":
                row.payload_hash = "0" * 64
            elif changed == "bad_json":
                row.payload_json = "{"
            else:
                body = json.loads(row.payload_json)
                body["run_id"] = "run_foreign_result_binding"
                forged = experience_original(body)
                row.output_id = forged.ref.ref
                row.payload_json = forged.body_json
                row.payload_hash = forged.ref.hash
    else:
        read_at -= timedelta(microseconds=1)

    client, _, _ = client_for(case, clock=lambda: read_at)
    response = client.get(BASE + "/research/results/" + case["run_id"])
    assert response.status_code == 409
    assert response.json()["detail"] == "perpetual_forecast_result_original_unavailable"


def test_verified_non_4h_publication_is_explicitly_unsupported(binding_case, monkeypatch):
    case = binding_case
    enable_pure_publication(monkeypatch)
    client, _, _ = client_for(case)
    command = selected_command(case, "24h")
    accepted = client.post(
        BASE + "/research",
        json=command,
        headers={"Idempotency-Key": command["request_id"]},
    )
    assert accepted.status_code == 200, accepted.text
    receipt = accepted.json()
    selected = {**case, "run_id": receipt["run_id"], "event_id": receipt["event_id"]}
    advice_fixture = importlib.import_module(
        "tests.decision_product.test_perpetual_advice_query"
    )
    monkeypatch.setattr(advice_fixture, "freeze", lambda _case: (None, None))
    derived = advice_fixture.persisted_publication(selected)
    after_target = target_end(derived) + timedelta(seconds=1)

    value = result_query(selected, now=after_target).read(OWNER, selected["run_id"])
    assert value["status"] == "unsupported"
    assert value["reason_code"] == "forecast_horizon_unsupported"
    assert value["horizon"] == value["target"]["horizon"] == "24h"
    assert value["result"] is None

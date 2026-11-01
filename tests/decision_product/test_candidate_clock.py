"""Native transcript components in a private database, never live acceptance."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from packages.contracts_py.decision_hub_contracts import ResearchSessionResult
from packages.kernel.decision_hub_kernel.application.provider_input_receipts import row_reference
from packages.kernel.decision_hub_kernel.application.research_candidate_clock import (
    _native_time,
    candidate_clock_state,
    read_candidate_clock_in_session,
)
from packages.kernel.decision_hub_kernel.application.research_graph_projection import (
    _round_cutoff,
    initial_research_state,
)
from packages.kernel.decision_hub_kernel.application.research_publication_binding import (
    _store,
    observation_json,
    observation_sha,
)
from packages.kernel.decision_hub_kernel.decision.horizon_contract import validate_horizon_contract
from packages.kernel.decision_hub_kernel.persistence.db import Database, RunRoleOutputRecord
from packages.kernel.decision_hub_kernel.ports.research_synthesis_protocol import (
    DECISION_OUTPUT_SCHEMA_REF,
)
from tests.decision_product.test_action_research_native_reader import originals
from tests.decision_product.test_native_action_synthesis import sha, wire_fixture
from tests.orchestration.test_agentic_research_graph import _request, _result
from tests.runtime.test_dsh_research_runtime import NOW


def _saved_native(tmp_path, *, matched=True, mutation=None):
    request, run, _ = wire_fixture(matched=matched, request_override={
        "execution_mode": "live", "deadline_at": NOW + timedelta(minutes=10),
        "pit_cutoff_at": NOW + timedelta(minutes=10),
    })
    notifications = deepcopy(run.notifications)
    for index, item in enumerate(notifications):
        item.payload["event"]["time"] = NOW.timestamp() * 1000 + index * 100
    run = replace(run, notifications=notifications)
    request, raw, mapped = originals(run=run, request=request)
    events = json.loads(raw.reply.events_json)
    if mutation is not None:
        mutation(events)
    encoded = observation_json(events)
    payload = raw.reply.model_copy(update={
        "events_json": encoded, "result_hash": sha(raw.reply.final_response + "\0" + encoded),
    })
    raw = raw.model_copy(update={"reply": payload, "events_json_hash": sha(encoded)})
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'clock.sqlite3'}")
    database.create_all()
    with database.session() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        task_json = observation_json({"request": request.model_dump(mode="json")})
        task = RunRoleOutputRecord(
            output_id="clock-task", run_id=request.run_id, role="research_task_request",
            schema_version="research-task-request.v2", payload_json=task_json,
            payload_hash=sha(task_json), created_at=raw.recorded_at,
        )
        session.add(task)
        session.flush()
        context = raw.context.model_copy(update={"rows": (row_reference(task),)})
        raw = raw.model_copy(update={"context": context})
        raw_ref = _store(session, raw, "research_transport")
        # Mutation controls preserve exact locator/hash integrity. The native
        # semantic authenticator must reject incompatible originals itself.
        witness = mapped.selection.model_copy(update={
            "call_event_hash": observation_sha(events[mapped.selection.call_event_index]),
            "result_event_hash": observation_sha(events[mapped.selection.result_event_index]),
        })
        mapped = mapped.model_copy(update={
            "context": context, "mapping_input_ref": raw_ref, "latest_transport_ref": raw_ref,
            "selection": witness,
        })
        mapped_ref = _store(session, mapped, "research_observed")
    return database, request, mapped_ref, events


@pytest.mark.parametrize("matched", [True, False])
def test_clock_comes_from_exact_native_freeze_or_selected_call(tmp_path, matched):
    database, request, ref, events = _saved_native(tmp_path, matched=matched)
    with database.session() as session:
        session.connection().exec_driver_sql("BEGIN")
        clock = read_candidate_clock_in_session(session, mapped_ref=ref, request=request)
        index = 0 if matched else 4
        assert clock is not None
        assert clock.reference_at == NOW + timedelta(milliseconds=index * 100)
        assert clock.call_event_hash == observation_sha(events[index])
        assert clock.call_event_index == index
        assert clock.basis == ("reviewed_native_freeze" if matched else "unreviewed_native_call")
        assert not session.new and not session.dirty


@pytest.mark.parametrize("change", ["call", "body", "time", "generation", "request"])
def test_clock_rejects_wrong_native_originals(tmp_path, change):
    def mutate(events):
        if change == "call":
            events[0]["payload"]["event"]["data"]["callId"] = "different"
        elif change == "body":
            events[0]["payload"]["event"]["data"]["arguments"]["candidate"][
                "action_intents"][0]["action"] = "wait"
        elif change == "time":
            events[0]["payload"]["event"]["time"] += 60_000
        elif change == "generation":
            events[3]["payload"]["event"]["data"]["result"]["binding"]["generation"] = 2
    database, request, ref, _ = _saved_native(tmp_path, mutation=mutate)
    if change == "request":
        request = request.model_copy(update={
            "deadline_at": request.deadline_at + timedelta(seconds=1),
        })
    with database.session() as session:
        session.connection().exec_driver_sql("BEGIN")
        with pytest.raises(ValueError):
            read_candidate_clock_in_session(session, mapped_ref=ref, request=request)


@pytest.mark.parametrize("value", [True, None, "1791883543440", float("nan"), float("inf"), 1e30])
def test_native_clock_never_invents_missing_or_invalid_time(value):
    with pytest.raises(ValueError, match="native_time_invalid"):
        _native_time({"time": value})


def _real_clock_case():
    freeze = datetime(2026, 10, 13, 9, 25, 43, 440000, UTC)
    resubmit = datetime(2026, 10, 13, 9, 33, 48, 683000, UTC)
    review_due = datetime(2026, 10, 13, 9, 32, 29, 707097, UTC)
    expiry = datetime(2026, 10, 13, 9, 47, 29, 707097, UTC)
    request = _request(execution_mode="live", pit_cutoff_at=expiry, deadline_at=expiry).model_copy(
        update={"output_schema_ref": DECISION_OUTPUT_SCHEMA_REF})
    from tests.orchestration.test_agentic_research_graph import _horizons
    horizons = _horizons("trigger-evidence")
    for index, horizon in enumerate(horizons):
        horizon["next_review_at"] = (review_due + timedelta(hours=index * 12)).isoformat()
        horizon["expires_at"] = (expiry + timedelta(hours=index * 24)).isoformat()
    result = ResearchSessionResult.model_validate(
        _result(request, candidates=[], horizons=horizons)
    )
    result = result.model_copy(update={"finished_at": resubmit})
    return request, result, freeze


def test_real_eth_review_due_keeps_original_horizon_admission_without_moving_dates():
    request, result, freeze = _real_clock_case()
    original = result.model_dump_json()
    state = initial_research_state(request)
    old_cutoff = _round_cutoff(state, request, result)
    assert not validate_horizon_contract(result.horizons, old_cutoff).valid
    cutoff = _round_cutoff(state, request, result, candidate_reference_at=freeze)
    assert cutoff == freeze
    assert validate_horizon_contract(result.horizons, cutoff).valid
    assert result.model_dump_json() == original


@pytest.mark.parametrize("kind", ["legacy", "future", "before_trigger", "naive"])
def test_projection_cannot_apply_reference_outside_new_protocol_order(kind):
    request, result, freeze = _real_clock_case()
    state = initial_research_state(request)
    if kind == "legacy":
        request = request.model_copy(update={"output_schema_ref": "research-session-result.v1"})
    elif kind == "future":
        freeze = result.finished_at + timedelta(seconds=1)
    elif kind == "before_trigger":
        freeze = datetime.fromisoformat(state["decision_cutoff_at"]) - timedelta(seconds=1)
    else:
        freeze = freeze.replace(tzinfo=None)
    with pytest.raises(ValueError, match="research_candidate_reference_invalid"):
        _round_cutoff(state, request, result, candidate_reference_at=freeze)


def test_unavailable_clock_replaces_prior_verified_reference(tmp_path):
    database, request, ref, _ = _saved_native(tmp_path)
    with database.session() as session:
        session.connection().exec_driver_sql("BEGIN")
        clock = read_candidate_clock_in_session(session, mapped_ref=ref, request=request)
    prior = {"candidate_clock": candidate_clock_state(clock, mapped_ref=ref)}
    updated = {**prior, "candidate_clock": candidate_clock_state(None, mapped_ref=ref)}
    assert updated["candidate_clock"]["status"] == "unavailable"
    assert "reference_at" not in updated["candidate_clock"]


def test_v3_threads_native_reference_through_round_and_quality_projection():
    from packages.kernel.decision_hub_kernel.application.research_graph_projection_v3 import (
        project_round_v3,
    )
    request, result, freeze = _real_clock_case()
    state = initial_research_state(request)
    delta, _membership, _quality = project_round_v3(
        state, request, request, result, [], [], [], candidate_reference_at=freeze,
    )
    assert datetime.fromisoformat(delta["decision_cutoff_at"]) == freeze
    assert delta["latest_result"]["finished_at"] == result.model_dump(mode="json")["finished_at"]

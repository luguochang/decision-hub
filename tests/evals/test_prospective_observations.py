import base64
import hashlib
import json
from datetime import timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.hub_api.asset_routes import asset_router
from packages.evals.prospective_observations import (
    SELECTION_VERSION,
    digest,
    export_prospective_inputs,
    first_record_outlooks,
    load_prospective_inputs,
    prospective_summary,
    select_inputs,
)
from packages.kernel.decision_hub_kernel.application.analytical_observation import (
    AnalyticalObservationService,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    AnalyticalObservationRecord as Observation,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    AnalyticalOutlookRecord as Outlook,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    FactVersionRecord,
    ParsedVersionRecord,
    RawCaptureRecord,
    RunInputRefRecord,
    as_utc,
)
from packages.provider_adapters.research.raw_capture import RawBodyArchive
from tests.research.test_analytical_outlooks import NOW, prepared, publish
from tests.scheduler.test_analytical_observation import Market


def _clone_outlook(session, original, identity, **overrides):
    values = {column.name: getattr(original, column.name)
              for column in original.__table__.columns}
    values.update(outlook_id=identity, run_id=f"run-{identity}", **overrides)
    row = Outlook(**values)
    session.add(row)
    return row


def _selection_snapshot(session):
    return tuple(
        {row.outlook_id: {column.name: getattr(row, column.name)
                          for column in row.__table__.columns}
         for row in session.query(model).all()}
        for model in (Outlook, Observation)
    )


@pytest.mark.parametrize("fault,reason", [
    ("excluded", "outlook_excluded"),
    ("abstention", "outlook_action_not_evaluable"),
    ("late_freeze", "outlook_not_frozen_before_window"),
    ("missing_as_of", "outlook_not_frozen_before_window"),
])
def test_first_excluded_record_reserves_sample_position_before_later_success(
    tmp_path, monkeypatch, fault, reason,
):
    db, result = prepared(tmp_path, monkeypatch)
    publish(db, result)
    with db.session() as session:
        first = session.query(Outlook).filter_by(horizon="30m").one()
        first_id = first.outlook_id
        _clone_outlook(
            session, first, "later-success", recorded_at=NOW + timedelta(seconds=2),
            window_start_at=NOW + timedelta(seconds=2),
            window_end_at=NOW + timedelta(minutes=30, seconds=2), action="long",
        )
        session.add(Observation(
            outlook_id="later-success", status="completed", attempt=1,
            next_attempt_at=NOW, evaluated_at=NOW + timedelta(minutes=31),
        ))
        if fault == "excluded":
            first.eligibility = "excluded"
            first.exclusion_reasons_json = '["original_failed_freeze"]'
        elif fault == "abstention":
            first.action = "no_trade"
        elif fault == "late_freeze":
            first.recorded_at = NOW + timedelta(seconds=1)
        else:
            first.data_as_of = None
    with db.session() as session:
        original = _selection_snapshot(session)
        summary, selected, rejected = select_inputs(session, NOW + timedelta(minutes=31))
        assert first_id not in {outlook.outlook_id for outlook, _ in selected}
        assert "later-success" not in {outlook.outlook_id for outlook, _ in selected}
        by_id = {item["outlook_id"]: item for item in rejected}
        assert by_id[first_id]["reason"] == reason
        assert by_id["later-success"] == {
            "outlook_id": "later-success", "reason": "repeat_study_method_horizon",
            "first_outlook_id": first_id, "input_problem": None,
        }
        assert summary.selection_version == SELECTION_VERSION
        assert summary.outlooks == 4 and summary.eligible == 3 and summary.selected == 2
        assert summary.first_records == 3 and summary.first_record_excluded == 1
        assert summary.repeated == summary.excluded == 1 and summary.repeated_ineligible == 0
        assert summary.recorded_completed == 1 and summary.completed == 0
        assert _selection_snapshot(session) == original


def test_first_failed_observation_is_not_replaced_by_completed_update(tmp_path, monkeypatch):
    db, result = prepared(tmp_path, monkeypatch)
    publish(db, result)
    with db.session() as session:
        first = session.query(Outlook).filter_by(horizon="30m").one()
        first_id = first.outlook_id
        observation = session.get(Observation, first_id)
        assert observation is not None
        observation.status, observation.error_code = "failed", "market_unavailable"
        _clone_outlook(
            session, first, "later-completed", recorded_at=NOW + timedelta(seconds=1),
            window_start_at=NOW + timedelta(seconds=1),
            window_end_at=NOW + timedelta(minutes=30, seconds=1),
        )
        session.add(Observation(
            outlook_id="later-completed", status="completed", attempt=1,
            next_attempt_at=NOW, evaluated_at=NOW + timedelta(minutes=31),
            quality_status="estimated",
        ))
    with db.session() as session:
        original = _selection_snapshot(session)
        summary, selected, rejected = select_inputs(session, NOW + timedelta(minutes=31))
        chosen = next(pair for pair in selected if pair[0].horizon == "30m")
        assert chosen[1] is not None
        assert chosen[0].outlook_id == first_id and chosen[1].status == "failed"
        assert rejected[0]["first_outlook_id"] == first_id
        assert summary.selected == 3 and summary.first_records == 3
        assert summary.unknown == 1 and summary.pending == 2 and summary.completed == 0
        assert summary.recorded_completed == 1 and summary.repeated == 1
        assert _selection_snapshot(session) == original


def test_first_neutral_record_keeps_its_completed_denominator(tmp_path, monkeypatch):
    db, result = prepared(tmp_path, monkeypatch, action="neutral")
    publish(db, result)
    with db.session() as session:
        first = session.query(Outlook).filter_by(horizon="30m").one()
        first_id = first.outlook_id
        observation = session.get(Observation, first_id)
        assert observation is not None
        observation.status, observation.evaluated_at = "completed", NOW + timedelta(minutes=31)
        observation.quality_status = "unknown"
        _clone_outlook(
            session, first, "later-long", recorded_at=NOW + timedelta(seconds=1),
            window_start_at=NOW + timedelta(seconds=1),
            window_end_at=NOW + timedelta(minutes=30, seconds=1), action="long",
            research_quality="advisory_only",
        )
        session.add(Observation(
            outlook_id="later-long", status="completed", attempt=1, next_attempt_at=NOW,
            evaluated_at=NOW + timedelta(minutes=31), quality_status="estimated",
            direction_correct=1,
        ))
    with db.session() as session:
        summary, selected, rejected = select_inputs(session, NOW + timedelta(minutes=31))
        chosen = next(outlook for outlook, _ in selected if outlook.horizon == "30m")
        assert chosen.outlook_id == first_id and chosen.action == "neutral"
        assert summary.completed == summary.neutral_completed == 1
        assert summary.directional_completed == 0 and summary.recorded_completed == 2
        assert rejected[0]["first_outlook_id"] == first_id


def test_first_record_order_uses_recorded_time_then_id_without_window_preference(
    tmp_path, monkeypatch,
):
    db, result = prepared(tmp_path, monkeypatch)
    publish(db, result)
    with db.session() as session:
        original = session.query(Outlook).filter_by(horizon="30m").one()
        for identity in ("z-tied-first", "a-tied-first"):
            _clone_outlook(
                session, original, identity, recorded_at=NOW - timedelta(seconds=1),
                window_start_at=NOW + timedelta(minutes=5),
                window_end_at=NOW + timedelta(minutes=35),
            )
    with db.session() as session:
        rows = session.query(Outlook).order_by(Outlook.outlook_id.desc()).all()
        now = NOW + timedelta(minutes=6)
        first = first_record_outlooks(rows, now=now)
        reversed_first = first_record_outlooks(reversed(rows), now=now)
        assert [row.outlook_id for row in first] == [row.outlook_id for row in reversed_first]
        assert next(row for row in first if row.horizon == "30m").outlook_id == "a-tied-first"
        summary, selected, rejected = select_inputs(session, now)
        assert next(row for row, _ in selected if row.horizon == "30m").outlook_id == (
            "a-tied-first"
        )
        assert summary.first_records == summary.selected == 3 and summary.repeated == 2
        assert {item["first_outlook_id"] for item in rejected} == {"a-tied-first"}


@pytest.mark.parametrize("dimension,other", [
    ("market", "metal"), ("instrument", "ETH-USDT-SWAP"),
    ("method_family", "daily_rule"), ("method_version", "another-method"),
    ("horizon", "7d"), ("study_id", "another-study"),
])
def test_first_record_identity_keeps_each_dimension_separate(
    tmp_path, monkeypatch, dimension, other,
):
    db, result = prepared(tmp_path, monkeypatch)
    publish(db, result)
    with db.session() as session:
        original = session.query(Outlook).filter_by(horizon="30m").one()
        _clone_outlook(session, original, "different-identity", **{dimension: other})
    summary = prospective_summary(db, NOW)
    assert summary.first_records == summary.selected == 4 and summary.repeated == 0


def test_future_retrospective_or_missing_study_does_not_supply_first_record_identity(
    tmp_path, monkeypatch,
):
    db, result = prepared(tmp_path, monkeypatch)
    publish(db, result)
    with db.session() as session:
        original = session.query(Outlook).filter_by(horizon="30m").one()
        original_id = original.outlook_id
        _clone_outlook(session, original, "missing-study", study_id=None,
                       recorded_at=NOW - timedelta(seconds=2))
        _clone_outlook(session, original, "retrospective", cohort="retrospective",
                       recorded_at=NOW - timedelta(seconds=3))
        _clone_outlook(
            session, original, "future", method_version="future-method",
            recorded_at=NOW + timedelta(seconds=1), window_start_at=NOW + timedelta(seconds=1),
            window_end_at=NOW + timedelta(minutes=30, seconds=1),
        )
    with db.session() as session:
        rows = session.query(Outlook).all()
        first = first_record_outlooks(rows, now=NOW)
        assert next(row for row in first if row.horizon == "30m").outlook_id == original_id
        assert {row.outlook_id for row in first}.isdisjoint(
            {"missing-study", "retrospective", "future"}
        )
        summary, selected, rejected = select_inputs(session, NOW)
        assert summary.outlooks == 4 and summary.first_records == summary.selected == 3
        assert summary.excluded == 1 and summary.repeated == 0
        assert rejected == [{"outlook_id": "missing-study", "reason": "study_identity_missing",
                             "first_outlook_id": None, "input_problem": "study_identity_missing"}]
        assert original_id in {row.outlook_id for row, _ in selected}
        later = first_record_outlooks(rows, now=NOW + timedelta(seconds=1))
        assert "future" in {row.outlook_id for row in later}


def test_duplicate_exclusion_remains_visible_without_replacing_first_sample(tmp_path, monkeypatch):
    db, result = prepared(tmp_path, monkeypatch)
    publish(db, result)
    with db.session() as session:
        original = session.query(Outlook).filter_by(horizon="30m").one()
        _clone_outlook(session, original, "later-excluded", eligibility="excluded",
                       recorded_at=NOW + timedelta(seconds=1))
    with db.session() as session:
        summary, _, rejected = select_inputs(session, NOW + timedelta(minutes=31))
        assert summary.eligible == summary.selected == 3
        assert summary.repeated == summary.repeated_ineligible == 1
        assert summary.excluded == summary.first_record_excluded == 0
        assert rejected[0]["reason"] == "repeat_study_method_horizon"
        assert rejected[0]["input_problem"] == "outlook_excluded"


def test_selection_clock_requires_timezone_and_normalizes_aware_cutoff(tmp_path, monkeypatch):
    db, result = prepared(tmp_path, monkeypatch)
    publish(db, result)
    with pytest.raises(ValueError, match="prospective_selection_clock_timezone_required"):
        first_record_outlooks((), now=NOW.replace(tzinfo=None))
    with pytest.raises(ValueError, match="prospective_selection_clock_timezone_required"):
        prospective_summary(db, NOW.replace(tzinfo=None))
    offset_now = NOW.astimezone(timezone(timedelta(hours=8)))
    assert prospective_summary(db, offset_now) == prospective_summary(db, NOW)


def test_selection_ignores_later_success_and_preserves_pending_first_sample(tmp_path, monkeypatch):
    db, result = prepared(tmp_path, monkeypatch)
    publish(db, result)
    with db.session() as session:
        first = session.query(Outlook).filter_by(horizon="30m").one()
        values = {column.name: getattr(first, column.name) for column in first.__table__.columns}
        values.update(outlook_id="repeat", run_id="repeat-run",
                      window_start_at=NOW + timedelta(minutes=1),
                      window_end_at=NOW + timedelta(minutes=31))
        session.add(Outlook(**values))
        session.add(Observation(outlook_id="repeat", status="completed", attempt=1,
                                next_attempt_at=NOW, evaluated_at=NOW + timedelta(minutes=32)))
    summary = prospective_summary(db, NOW + timedelta(minutes=32))
    assert summary.outlooks == 4 and summary.selected == 3
    assert summary.recorded_completed == 1 and summary.completed == 0
    assert summary.repeated == 1 and summary.pending == 3 and summary.distinct_studies == 1
    app = FastAPI()
    app.include_router(asset_router(db, tmp_path, clock=lambda: NOW + timedelta(minutes=32)))
    response = TestClient(app).get("/v1/evolution/observation-inputs")
    assert response.status_code == 200 and response.json()["selected"] == 3
    assert "repeat-run" not in response.text
    with db.session() as session:
        assert session.query(Outlook).count() == 4
        assert session.get(Observation, first.outlook_id).status == "pending"


async def archived_case(tmp_path, monkeypatch, action="neutral"):
    db, result = prepared(tmp_path, monkeypatch, action=action)
    # Synthetic original-input archive exists before the synthetic prediction.
    digest, ref = RawBodyArchive(tmp_path / "raw-captures").put(b'{"input":79043}')
    with db.session() as session:
        session.add(RawCaptureRecord(
            capture_id="input", context_id="unit-input", url="https://unit.test/input",
            status_code=200, body_hash=digest, body_ref=ref, byte_count=15,
            media_type="application/json", adapter_version="unit",
            started_at=NOW, completed_at=NOW, recorded_at=NOW,
        ))
        for row in session.query(ParsedVersionRecord).all():
            row.capture_ids_json = '["input"]'
            row.recorded_at = NOW
        for row in session.query(FactVersionRecord).all():
            row.recorded_at = NOW
            row.market, row.scope_instrument = "crypto", "BTC-USDT-SWAP"
    publish(db, result)
    now = NOW + timedelta(minutes=31)
    await AnalyticalObservationService(db, Market(lambda: now), clock=lambda: now).process_due()
    archive = RawBodyArchive(tmp_path / "raw-captures")
    with db.session() as session:
        outlook = session.query(Outlook).filter_by(horizon="30m").one()
        for side, boundary, price in (("entry", outlook.window_start_at, 100),
                                       ("exit", outlook.window_end_at, 102)):
            boundary = as_utc(boundary)
            payload = {"code": "0", "data": [[str(int((boundary - timedelta(minutes=1))
                                                      .timestamp() * 1000)),
                                               "100", "102", "99", str(price), "1", "1", "1", "1"]]}
            body = json.dumps(payload).encode()
            body_hash, ref = archive.put(body)
            session.add(RawCaptureRecord(
                capture_id=side, context_id=f"analytical-observation:{outlook.outlook_id}:1",
                url=("https://www.okx.com/api/v5/market/history-candles?instId=BTC-USDT-SWAP"
                     f"&bar=1m&after={int(boundary.timestamp() * 1000)}&limit=1"),
                status_code=200, body_hash=body_hash, body_ref=ref, byte_count=len(body),
                media_type="application/json", adapter_version="unit", started_at=now,
                completed_at=now, recorded_at=now,
            ))
    return db, now


async def test_valid_market_label_does_not_hide_missing_research_input(tmp_path, monkeypatch):
    from packages.evals.prospective_observations import audit_observation_inputs

    db, now = await archived_case(tmp_path, monkeypatch)
    with db.session() as session:
        for row in session.query(ParsedVersionRecord).all():
            row.capture_ids_json = "[]"
    audit = audit_observation_inputs(db, tmp_path, now=now)
    assert audit["verified_labels"] == 1
    assert audit["verified_inputs"] == audit["usable_observations"] == 0
    assert audit["invalid_inputs"] == 3
    output = tmp_path / "split-status"
    export_prospective_inputs(db, tmp_path, output, now=now)
    manifest, cases = load_prospective_inputs(output)
    assert manifest["schema_version"] == "prospective-observation-dataset.v2"
    labelled = next(c for c in cases if c["label_status"] == "verified")
    assert labelled["status"] == "invalid" and labelled["input_status"] == "invalid"
    assert labelled["label"]["market_return_pct"] == pytest.approx(2)
    app = FastAPI()
    app.include_router(asset_router(db, tmp_path, clock=lambda: now))
    response = TestClient(app).get("/v1/evolution/observation-input-audit")
    assert response.status_code == 200 and response.json()["invalid_inputs"] == 3
    assert "body_ref" not in response.text and "payload" not in response.text


@pytest.mark.parametrize("action,correct", [("neutral", None), ("long", True), ("short", False)])
async def test_export_verifies_archives_without_rerunning_models_and_keeps_future_labels_absent(
    tmp_path, monkeypatch, action, correct,
):
    db, now = await archived_case(tmp_path, monkeypatch, action)
    output = tmp_path / "dataset"
    manifest = export_prospective_inputs(db, tmp_path, output, now=now)
    assert manifest["verified_labels"] == 1 and manifest["invalid_cases"] == 0
    frozen, cases = load_prospective_inputs(output)
    assert not frozen["promotion_eligible"] and frozen["purpose"] == "observational_only"
    completed = next(c for c in cases if c["status"] == "verified")
    assert completed["label"]["direction_correct"] is correct
    assert completed["label"]["market_return_pct"] == pytest.approx(2)
    assert len(completed["label"]["captures"]) == 2
    assert all(json.loads(c["body_text"])["code"] == "0"
               for c in completed["label"]["captures"])
    assert all(c["label"] is None for c in cases if c["status"] == "pending")
    with pytest.raises(ValueError, match="new_output_directory_required"):
        export_prospective_inputs(db, tmp_path, output, now=now)
    (output / manifest["cases"][0]["path"]).write_bytes(b"changed")
    with pytest.raises(ValueError, match="dataset_case_hash_mismatch"):
        load_prospective_inputs(output)


@pytest.mark.parametrize("fault,code", [
    ("archive", "raw_capture_archive_corrupt"),
    ("price", "market_return_mismatch"),
    ("neutral_direction", "direction_label_mismatch"),
    ("future_label", "label_timestamp_invalid"),
])
async def test_invalid_labels_are_recorded_and_never_counted(tmp_path, monkeypatch, fault, code):
    db, now = await archived_case(tmp_path, monkeypatch)
    with db.session() as session:
        observation = session.query(Observation).filter_by(status="completed").one()
        if fault == "archive":
            row = session.get(RawCaptureRecord, "exit")
            (tmp_path / "raw-captures" / row.body_hash[:2] / row.body_hash).write_bytes(b"bad")
        elif fault == "price":
            observation.market_return_pct = 99
        elif fault == "neutral_direction":
            observation.direction_correct = 1
        else:
            observation.evaluated_at = now + timedelta(days=1)
    output = tmp_path / "invalid"
    manifest = export_prospective_inputs(db, tmp_path, output, now=now)
    assert manifest["verified_labels"] == 0 and manifest["invalid_cases"] == 1
    _, cases = load_prospective_inputs(output)
    invalid = next(c for c in cases if c["status"] == "invalid")
    assert invalid["label"] is None and invalid["error_code"] == code


def test_retrospective_and_late_frozen_inputs_do_not_enter_selection(tmp_path, monkeypatch):
    db, result = prepared(tmp_path, monkeypatch)
    publish(db, result)
    with db.session() as session:
        rows = session.query(Outlook).order_by(Outlook.horizon).all()
        rows[0].cohort = "retrospective"
        rows[1].recorded_at = NOW + timedelta(seconds=1)
        rows[2].study_id = None
    summary = prospective_summary(db, NOW + timedelta(minutes=31))
    assert summary.outlooks == 2 and summary.excluded == 2 and summary.selected == 0


async def test_export_rejects_input_adopted_after_prediction_cutoff(tmp_path, monkeypatch):
    db, now = await archived_case(tmp_path, monkeypatch)
    with db.session() as session:
        for row in session.query(RunInputRefRecord).all():
            row.adopted_at = NOW + timedelta(seconds=1)
    output = tmp_path / "late-input"
    manifest = export_prospective_inputs(db, tmp_path, output, now=now)
    assert manifest["verified_labels"] == 1 and manifest["invalid_cases"] == 3
    assert manifest["usable_observations"] == 0
    _, cases = load_prospective_inputs(output)
    assert all(c["error_code"] == "fact_input_after_freeze" for c in cases)


async def test_self_contained_input_archive_detects_tamper_even_after_manifest_rehash(
    tmp_path, monkeypatch,
):
    db, now = await archived_case(tmp_path, monkeypatch)
    output = tmp_path / "input-tamper"
    manifest = export_prospective_inputs(db, tmp_path, output, now=now)
    _, cases = load_prospective_inputs(output)
    assert all(c["input_receipts"] for c in cases)
    entry = manifest["cases"][0]
    case = cases[0]
    receipt = case["input_receipts"][0]
    archived_hash = hashlib.sha256(base64.b64decode(receipt["body_base64"])).hexdigest()
    assert archived_hash == receipt["body_hash"]
    receipt["body_base64"] = base64.b64encode(b"tampered original").decode()
    body = json.dumps(case).encode()
    (output / entry["path"]).write_bytes(body)
    entry["sha256"] = hashlib.sha256(body).hexdigest()
    manifest.pop("content_hash")
    manifest["content_hash"] = digest(manifest)
    (output / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="dataset_input_archive_hash_mismatch"):
        load_prospective_inputs(output)


@pytest.mark.parametrize("fact_instrument,expected", [("BTC-USDT", 3), ("ETH-USDT", 0)])
async def test_daily_target_is_checked_separately_from_parent_study_scope(
    tmp_path, monkeypatch, fact_instrument, expected,
):
    from packages.evals.prospective_observations import audit_observation_inputs

    db, now = await archived_case(tmp_path, monkeypatch)
    with db.session() as session:
        for outlook in session.query(Outlook).all():
            outlook.method_family = "daily_rule"
            outlook.method_version = "daily-trend-v0.1"
            outlook.instrument = "BTC-USDT"
        for fact in session.query(FactVersionRecord).all():
            payload = json.loads(fact.payload_json)
            payload["instrument"] = fact_instrument
            fact.payload_json = json.dumps(payload)
    audit = audit_observation_inputs(db, tmp_path, now=now)
    assert audit["verified_inputs"] == expected


def test_selection_never_merges_instruments_or_methods(tmp_path, monkeypatch):
    db, result = prepared(tmp_path, monkeypatch)
    publish(db, result)
    with db.session() as session:
        original = session.query(Outlook).filter_by(horizon="30m").one()
        for identity, overrides in (
            ("other-method", {"method_version": "another-method"}),
            ("other-instrument", {"market": "metal", "instrument": "XAU-USD"}),
        ):
            values = {c.name: getattr(original, c.name) for c in original.__table__.columns}
            values.update(outlook_id=identity, run_id=identity, **overrides)
            session.add(Outlook(**values))
            session.add(Observation(outlook_id=identity, status="pending", attempt=0,
                                    next_attempt_at=NOW + timedelta(minutes=30)))
    summary = prospective_summary(db, NOW)
    assert summary.selected == 5 and summary.repeated == 0

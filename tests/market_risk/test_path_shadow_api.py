"""Readonly HTTP against isolated SQLite and explicit synthetic capture/clock."""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from apps.hub_api.risk_paths import (
    CurrentTrialView,
    PredictionDetailView,
    PredictionsView,
    ScoreTarget,
    ScoreView,
    TrialDetailView,
    TrialsView,
    risk_paths_router,
)
from packages.kernel.decision_hub_kernel.persistence.path_forecast_records import (
    RiskPathJobRecord,
    RiskPredictionRecord,
    RiskShadowTrialRecord,
)
from tests.market_risk.test_path_shadow_ledger import (
    BTC,
    ETH,
    FIRST,
    first_predictions,
    register,
)
from tests.market_risk.test_path_shadow_ledger import (
    runtime as isolated_runtime_fixture,
)


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    yield from isolated_runtime_fixture.__wrapped__(tmp_path, monkeypatch)


def client_for(runtime):
    db, data, _, now, _, _ = runtime
    app = FastAPI()
    app.include_router(risk_paths_router(db, data.store, clock=lambda: now[0]))
    return TestClient(app)


def test_empty_reads_do_not_register_or_fetch(runtime):
    db, _, transport, _, _, _ = runtime
    client = client_for(runtime)
    calls = len(transport.calls)
    current = CurrentTrialView.model_validate(
        client.get(f"/v1/risk/scopes/{BTC.scope_id}/path-trial").json()
    )
    assert current.trial is None
    assert TrialsView.model_validate(client.get("/v1/risk/path-trials").json()).trials == []
    predictions = PredictionsView.model_validate(
        client.get(f"/v1/risk/scopes/{ETH.scope_id}/predictions").json()
    )
    assert predictions.predictions == []
    with db.session() as session:
        assert session.query(RiskShadowTrialRecord).count() == 0
        assert session.query(RiskPathJobRecord).count() == 0
    assert len(transport.calls) == calls
    assert client.post("/v1/risk/path-trials", json={}).status_code == 405
    assert client.get("/v1/risk/scopes/wrong/path-trial").status_code == 404


def test_real_stored_pairs_read_strict_without_running_jobs(runtime):
    db, _, transport, _, _, _ = runtime
    trial = first_predictions(runtime)
    client = client_for(runtime)
    calls = len(transport.calls)
    current = CurrentTrialView.model_validate(
        client.get(f"/v1/risk/scopes/{BTC.scope_id}/path-trial").json()
    )
    summary = current.trial
    assert summary.expected_prediction_count == 112
    assert summary.published_prediction_count == 4
    assert summary.future_prediction_count == 108
    assert summary.missed_prediction_count == 0
    assert summary.pending_label_count == 56
    detail = TrialDetailView.model_validate(client.get(f"/v1/risk/path-trials/{trial}").json())
    assert len(detail.trial.jobs) == 115
    assert detail.summary == summary
    listing = TrialsView.model_validate(client.get("/v1/risk/path-trials").json())
    assert listing.trials == [summary]
    records = PredictionsView.model_validate(client.get(
        f"/v1/risk/scopes/{BTC.scope_id}/predictions", params={"trial_id": trial},
    ).json())
    assert len(records.predictions) == 2
    prediction = PredictionDetailView.model_validate(client.get(
        f"/v1/risk/predictions/{records.predictions[0].prediction_id}",
    ).json())
    assert prediction.prediction.forecast.feature_delay_seconds == 60
    assert len(prediction.prediction.feature_manifest.originals) == 33
    pending = ScoreView.model_validate(client.get(f"/v1/risk/path-trials/{trial}/score").json())
    assert pending.report is None and not pending.trial.report_available
    with db.session() as session:
        assert session.query(RiskPredictionRecord).count() == 4
    assert len(transport.calls) == calls
    assert client.get(f"/v1/risk/scopes/{BTC.scope_id}/predictions?limit=201").status_code == 422


def test_succeeded_zero_sample_fit_is_unavailable_and_does_not_gain_later_history(runtime):
    """Synthetic raw transport, actual immutable fit and SQLite read boundaries."""
    db, data, transport, now, ledger, jobs = runtime
    trial = register(ledger)
    assert asyncio.run(data.refresh_due()) == 2
    assert jobs.process_due(limit=2) == 2
    client = client_for(runtime)
    first = CurrentTrialView.model_validate(
        client.get(f"/v1/risk/scopes/{BTC.scope_id}/path-trial").json()
    ).trial
    assert all(item.fit_job_state == "succeeded" for item in first.training_readiness)
    for item in first.training_readiness:
        assert item.status == "training_unavailable" and item.frozen_fit
        assert item.baseline_n == item.candidate_training_n == 0
        assert item.low_group_n == item.high_group_n == 0
        assert item.minimum_training_windows == 60 and item.minimum_group_windows == 30
        assert not item.baseline_sample_ready and not item.candidate_sample_ready
        assert not item.probability_qualified and not item.promotion_allowed
        assert item.available_candle_minutes > 0
        assert item.available_history_end_at <= item.read_as_of
        assert item.accepted_history_start_at is None and item.accepted_history_end_at is None
    with db.session() as session:
        before = {
            row.job_id: (row.state, row.result_json, row.result_hash)
            for row in session.query(RiskPathJobRecord).filter_by(trial_id=trial).all()
        }
    now[0] += timedelta(days=2)
    assert asyncio.run(data.refresh_due()) == 2  # Acquired after the frozen read.
    calls = len(transport.calls)
    later = CurrentTrialView.model_validate(
        client.get(f"/v1/risk/scopes/{BTC.scope_id}/path-trial").json()
    ).trial
    for earlier, item in zip(first.training_readiness, later.training_readiness, strict=True):
        assert item.model_dump(exclude={"as_of"}) == earlier.model_dump(exclude={"as_of"})
    with db.session() as session:
        assert {
            row.job_id: (row.state, row.result_json, row.result_hash)
            for row in session.query(RiskPathJobRecord).filter_by(trial_id=trial).all()
        } == before
        assert session.get(RiskShadowTrialRecord, trial).state == first.state
    assert len(transport.calls) == calls


def test_missing_fit_counts_stay_unknown_and_corrupt_saved_fit_fails_closed(runtime):
    db, _, _, _, ledger, jobs = runtime
    trial = register(ledger)
    client = client_for(runtime)
    pending = client.get(f"/v1/risk/scopes/{BTC.scope_id}/path-trial")
    assert pending.status_code == 200
    for item in CurrentTrialView.model_validate(pending.json()).trial.training_readiness:
        assert item.status == "training_pending" and not item.frozen_fit
        assert item.baseline_n is None and item.low_group_n is None
        assert item.available_candle_minutes is None and item.read_as_of is None
    assert jobs.process_due(limit=2) == 2
    with db.session() as session:
        row = session.query(RiskPathJobRecord).filter_by(
            trial_id=trial, kind="fit", scope_id=BTC.scope_id
        ).one()
        payload = json.loads(row.result_json)
        payload["model"]["baseline"]["n"] = 60
        row.result_json = json.dumps(payload)
    response = client.get(f"/v1/risk/scopes/{BTC.scope_id}/path-trial")
    assert response.status_code == 409
    assert response.json()["detail"] == "risk_path_receipt_unverified"


def test_mature_labels_final_score_full_denominator_and_no_read_side_effect(runtime):
    db, data, transport, now, ledger, jobs = runtime
    trial = first_predictions(runtime)
    now[0] = FIRST + timedelta(days=1, minutes=1, seconds=10)
    assert asyncio.run(data.refresh_due()) == 2
    assert jobs.process_due(limit=2) == 2
    now[0] = ledger.trial(trial)["label_deadline_at"]
    assert jobs.process_due(limit=1) == 1
    client = client_for(runtime)
    calls = len(transport.calls)
    response = client.get(f"/v1/risk/path-trials/{trial}/score")
    assert response.status_code == 200, response.text
    view = ScoreView.model_validate(response.json())
    score = view.report.score
    assert view.trial.state == "completed"
    assert score.expected_slot_count == 56
    assert score.expected_prediction_count == 112
    assert score.published_prediction_count == 4
    assert score.unpublished_prediction_count == 108
    assert score.complete_label_count == 2
    assert score.unavailable_label_count == 54
    assert score.independent_paired_day_blocks == 0  # insufficient history, no scored probabilities
    assert score.independent_event_blocks is None
    assert score.alarm_metrics.precision is None
    assert not score.promotion_allowed
    assert all(item.paired_baseline_brier is None for item in score.targets)
    assert len(transport.calls) == calls
    assert client.get(f"/v1/risk/path-trials/{trial}/score").json() == response.json()
    with db.session() as session:
        assert session.query(RiskPredictionRecord).count() == 4


def test_deadline_is_not_completed_by_read_and_missing_remains_visible(runtime):
    _, _, _, now, ledger, _ = runtime
    trial = register(ledger)
    now[0] = ledger.trial(trial)["label_deadline_at"]
    view = ScoreView.model_validate(
        client_for(runtime).get(f"/v1/risk/path-trials/{trial}/score").json()
    )
    assert view.trial.deadline_elapsed and not view.trial.report_available
    assert view.report is None
    assert view.trial.missed_prediction_count == 112
    assert view.trial.unavailable_mature_label_count == 56
    assert ledger.trial(trial)["state"] == "registered"


def test_bad_payload_fails_closed_and_404_not_fake_empty(runtime):
    db, _, _, _, ledger, _ = runtime
    trial = first_predictions(runtime)
    prediction = ledger.predictions(BTC.scope_id, trial_id=trial)[0]
    with db.session() as session:
        row = session.get(RiskPredictionRecord, prediction["prediction_id"])
        value = json.loads(row.payload_json)
        value["promotion_allowed"] = True
        row.payload_json = json.dumps(value)
    client = client_for(runtime)
    assert client.get(f"/v1/risk/predictions/{prediction['prediction_id']}").status_code == 409
    assert client.get(f"/v1/risk/path-trials/{trial}/score").status_code == 409
    assert client.get("/v1/risk/path-trials/missing/score").status_code == 404
    assert client.get("/v1/risk/predictions/missing").status_code == 404


def test_score_dto_rejects_nonfinite_negative_loss_and_unregistered_promotion():
    raw = {
        "scope_id": BTC.scope_id, "direction": "long", "threshold": "0.02", "primary": True,
        "expected_slots": 28, "baseline_scored": 1, "candidate_scored": 1, "paired_scored": 1,
        "baseline_brier": "0.25", "candidate_brier": "0.20", "paired_baseline_brier": "0.25",
        "paired_candidate_brier": "0.20", "paired_delta": "0.05", "independent_day_blocks": 1,
        "ci": None, "ci_status": "not_registered",
    }
    assert ScoreTarget.model_validate(raw).paired_delta == "0.05"
    for value in ("NaN", "Infinity", "-0.1", "1.1"):
        with pytest.raises(ValidationError):
            ScoreTarget.model_validate({**raw, "baseline_brier": value})
    with pytest.raises(ValidationError):
        ScoreTarget.model_validate({**raw, "promotion_allowed": True})

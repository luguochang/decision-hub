"""Strict read DTO seams; synthetic intervals do not claim market performance."""

import asyncio
import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import TypeAdapter, ValidationError

from apps.hub_api.risk_paths import (
    AlarmMetrics,
    PathScore,
    ScoreConfidenceInterval,
    TrialProtocol,
    risk_paths_router,
)
from packages.kernel.decision_hub_kernel.application.forecast_alarm import ForecastAlarmService
from packages.kernel.decision_hub_kernel.decision.path_forecast_score import score_path_forecasts
from packages.kernel.decision_hub_kernel.persistence.db import RunRoleOutputRecord
from packages.kernel.decision_hub_kernel.ports.market_path import spot_scope
from packages.kernel.decision_hub_kernel.ports.path_forecast import (
    PathForecastParameters,
    PathForecastParametersV1,
)
from tests.market_risk.test_path_shadow_api import runtime  # noqa: F401
from tests.market_risk.test_path_shadow_ledger import FIRST, first_predictions

NOW = datetime(2026, 10, 10, tzinfo=UTC)


def scored(parameters):
    return score_path_forecasts(
        (),
        (),
        expected_slots=((spot_scope("BTC-USDT").scope_id, NOW),),
        as_of=NOW + timedelta(days=2),
        parameters=parameters,
    )


def test_exact_v1_read_and_v2_missing_census_do_not_invent_interval():
    adapter = TypeAdapter(PathScore)
    old = scored(PathForecastParametersV1())
    current = scored(PathForecastParameters())
    original = json.dumps(old, sort_keys=True)
    assert set(adapter.validate_python(old).model_dump()) == set(old)
    assert json.dumps(old, sort_keys=True) == original
    assert "inference" not in old and old["ci_status"] == "not_registered"
    parsed = adapter.validate_python(current)
    assert set(parsed.model_dump()) == set(current)
    assert current["schema_version"] == "path-shadow-score.v2"
    assert current["ci_status"] == "insufficient" and current["ci"] is None
    assert current["inference"]["complete_pairs"] == 0
    assert current["inference"]["expected_pairs"] == 1
    assert current["promotion_allowed"] is False
    injected = {**old, "inference": current["inference"]}
    with pytest.raises(ValidationError):
        adapter.validate_python(injected)
    mismatch = deepcopy(current)
    mismatch["ci_status"] = "available"
    with pytest.raises(ValidationError):
        adapter.validate_python(mismatch)


def test_ci_requires_finite_ordered_bounds_and_fixed_qualifying_counts():
    value = dict(
        lower="0.01",
        upper="0.05",
        point_estimate="0.03",
        confidence="0.95",
        protocol="utc-day-paired-percentile-bootstrap.v2",
        protocol_hash="a" * 64,
        seed=271828,
        replicates=2000,
        independent_day_blocks=100,
        event_blocks=30,
        unit="UTC-day",
        estimator="equal_day_mean_baseline_minus_candidate_brier",
    )
    assert ScoreConfidenceInterval.model_validate(value).lower == "0.01"
    for update in (
        {"lower": "NaN"},
        {"upper": "Infinity"},
        {"lower": "0.1"},
        {"independent_day_blocks": 99},
        {"event_blocks": 29},
        {"seed": 1},
    ):
        with pytest.raises(ValidationError):
            ScoreConfidenceInterval.model_validate(value | update)


def test_current_protocol_and_real_metric_shape_preserve_legacy_unknown():
    body = dict(
        schema_version="risk-path-shadow-trial.v1",
        parameters=PathForecastParametersV1().model_dump(mode="json"),
        scopes=[spot_scope("BTC-USDT").model_dump(mode="json")],
        first_cutoff_at=NOW,
        target_version="long-short-mae-2pct-5pct.v1",
        feature_policy="known-closed-31-delay180.v1",
        source_policy="authenticated-market-path-store.v1",
        alarm_policy="not_registered_no_user_dispatch",
        promotion_allowed=False,
    )
    legacy = TrialProtocol.model_validate(body)
    assert legacy.parameters.model_dump(mode="json") == body["parameters"]
    current = TrialProtocol.model_validate(
        body
        | {
            "schema_version": "risk-path-shadow-trial.v2",
            "parameters": PathForecastParameters().model_dump(mode="json"),
            "alarm_policy": "shadow-forecast-ui-receipts.v1",
        }
    )
    assert current.parameters.protocol_version == "path-shadow-24h.v2"
    metrics = dict(
        precision="0.5",
        recall="0.2",
        false_episodes_per_asset_day="1.25",
        lead_time=dict(
            matched_count=2,
            minimum_seconds="0",
            median_seconds="60",
            resolution_seconds=60,
            conservative=True,
        ),
        status="partial",
        reason="matched_forecast_alarm_originals",
    )
    assert AlarmMetrics.model_validate(metrics).lead_time.conservative is True
    for changes in (
        {"precision": "1.1"},
        {"recall": "NaN"},
        {"false_episodes_per_asset_day": "-1"},
    ):
        with pytest.raises(ValidationError):
            AlarmMetrics.model_validate(metrics | changes)


def test_private_v2_http_reverifies_exact_alarm_original_without_get_writes(runtime):  # noqa: F811
    database, data, transport, now, ledger, jobs = runtime
    alarms = ForecastAlarmService(database, data.store, clock=lambda: now[0])
    ledger.forecast_alarm_publisher = alarms.publish_pair_in_session
    ledger.forecast_alarm_score_reader = alarms.score_in_session
    ledger.forecast_alarm_original_reader = alarms.read_score_in_session
    trial = first_predictions(runtime)
    now[0] = FIRST + timedelta(days=1, minutes=1, seconds=10)
    assert asyncio.run(data.refresh_due()) == 2
    assert jobs.process_due(limit=2) == 2
    now[0] = ledger.trial(trial)["label_deadline_at"]
    assert jobs.process_due(limit=1) == 1
    app = FastAPI()
    app.include_router(risk_paths_router(database, data.store, clock=lambda: now[0]))
    client = TestClient(app)
    with database.session() as session:
        before = session.query(RunRoleOutputRecord).count()
    calls = len(transport.calls)
    response = client.get(f"/v1/risk/path-trials/{trial}/score")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["report"]["report_version"] == "path-shadow-evaluation.v2"
    assert payload["report"]["score"]["ci_status"] == "insufficient"
    original = payload["report"]["alarm_score_original"]
    assert original is not None
    assert original in payload["report"]["score"]["alarm_originals"]
    assert len(transport.calls) == calls
    with database.session() as session:
        assert session.query(RunRoleOutputRecord).count() == before
        row = session.get(RunRoleOutputRecord, original["original_ref"])
        row.payload_hash = "0" * 64
    assert client.get(f"/v1/risk/path-trials/{trial}/score").status_code == 409

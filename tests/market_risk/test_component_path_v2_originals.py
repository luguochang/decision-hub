"""Version-aware bridge with actual private alarm reviews, never statistical promotion."""

import inspect
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from apps.hub_worker.market_component_originals import (
    PATH_PROTOCOL,
    PATH_PROTOCOL_HASH,
    PATH_PROTOCOL_V2_HASH,
    ProductionComponentOriginalReader,
)
from apps.hub_worker.market_components import InstalledComponent
from packages.kernel.decision_hub_kernel.application.component_registry import ComponentRegistry
from packages.kernel.decision_hub_kernel.application.forecast_alarm import ForecastAlarmService
from packages.kernel.decision_hub_kernel.application.market_component_research_functions import (
    predict_registered_path_forecast,
)
from packages.kernel.decision_hub_kernel.persistence.db import RunRoleOutputRecord
from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash
from tests.market_risk.test_path_shadow_ledger import BTC, FIRST, first_predictions
from tests.market_risk.test_path_shadow_ledger import runtime as shadow_runtime


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    yield from shadow_runtime.__wrapped__(tmp_path, monkeypatch)


def test_v2_real_review_recomputed_without_writer_and_tamper_rejected(runtime):
    db, data, _, now, ledger, jobs = runtime
    alarms = ForecastAlarmService(db, data.store, clock=lambda: now[0])
    ledger.forecast_alarm_publisher = alarms.publish_pair_in_session
    ledger.forecast_alarm_score_reader = alarms.score_in_session
    ledger.forecast_alarm_original_reader = alarms.read_score_in_session
    callback = predict_registered_path_forecast
    item = InstalledComponent(
        "model",
        "COND_VOL_V1",
        BTC.scope_id,
        "v2-reader-test",
        "path-forecast-consumption-input.v1",
        "risk-path-forecast.v1",
        callback,
        {"actual_callback": Path(inspect.getsourcefile(callback))},
        {},
        {},
    )
    reader = ProductionComponentOriginalReader(
        db, (item,), data.raw_archive, path_ledger=ledger, clock=lambda: now[0]
    )
    registry = ComponentRegistry(db, original_reader=reader, clock=lambda: now[0])
    manifest = registry.register(item.manifest())
    component_trial = registry.shadow(
        "v2-component-trial",
        version_id=item.version_id,
        protocol_hash=PATH_PROTOCOL_V2_HASH,
        baseline_version_id=None,
        start_at=FIRST,
        end_at=FIRST + timedelta(days=30),
    )
    trial = first_predictions(runtime)
    now[0] = FIRST + timedelta(days=29, minutes=1, seconds=1)
    assert jobs.process_due(limit=1) == 1
    report = ledger.score(trial)
    assert report["report_version"] == "path-shadow-evaluation.v2"
    ref = report["alarm_score_original"]
    assert ref and report["score"]["alarm_originals"]
    with db.session() as session:
        before = tuple(
            (r.output_id, r.payload_hash) for r in session.scalars(select(RunRoleOutputRecord))
        )
    read, _, _ = reader._path_report(report["report_id"], manifest, component_trial)
    assert read == report and read["score"]["ci"] is None
    with db.session() as session:
        after = tuple(
            (r.output_id, r.payload_hash) for r in session.scalars(select(RunRoleOutputRecord))
        )
        assert (
            before == after
            and session.connection().exec_driver_sql("PRAGMA query_only").scalar_one() == 0
        )
        row = session.get(RunRoleOutputRecord, ref["original_ref"])
        row.payload_json = row.payload_json.replace('"trial_id":', '"tampered_trial_id":')
    with pytest.raises(ValueError, match="immutable_payload_changed"):
        reader._path_report(report["report_id"], manifest, component_trial)


def test_legacy_bridge_manifest_exact_parameters_not_new_defaults():
    assert PATH_PROTOCOL["parameters"]["protocol_version"] == "path-shadow-24h.v1"
    assert PATH_PROTOCOL["report_version"] == "path-shadow-evaluation.v1"
    assert "ci_seed" not in PATH_PROTOCOL["parameters"]
    assert canonical_hash(PATH_PROTOCOL) == PATH_PROTOCOL_HASH
    assert PATH_PROTOCOL_HASH != PATH_PROTOCOL_V2_HASH

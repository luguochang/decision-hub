"""Private real shadow IO/SQL. Synthetic transport is never market qualification."""

import asyncio
import inspect
import json
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.orm import Session

from apps.hub_worker.market_component_originals import (
    PATH_PROTOCOL_HASH,
    ProductionComponentOriginalReader,
)
from apps.hub_worker.market_components import InstalledComponent
from packages.kernel.decision_hub_kernel.application.component_registry import ComponentRegistry
from packages.kernel.decision_hub_kernel.application.market_component_research_functions import (
    predict_registered_path_forecast,
)
from packages.kernel.decision_hub_kernel.application.risk_path_ledger import PARAMETERS, REPORT
from packages.kernel.decision_hub_kernel.decision.path_forecast_score import score_path_forecasts
from packages.kernel.decision_hub_kernel.persistence.market_case_records import (
    MarketComponentAuditRecord,
)
from packages.kernel.decision_hub_kernel.persistence.path_forecast_records import (
    RiskEvaluationReportRecord,
    RiskPathObservationRecord,
)
from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash
from packages.kernel.decision_hub_kernel.ports.path_forecast import RiskPathForecast
from tests.market_risk.test_path_shadow_ledger import (  # noqa: F401
    BTC,
    ETH,
    FIRST,
    first_predictions,
)
from tests.market_risk.test_path_shadow_ledger import (
    runtime as path_runtime,
)


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    yield from path_runtime.__wrapped__(tmp_path, monkeypatch)


def descriptive(payload, parameters):
    return {"value": payload["value"] + parameters["offset"]}


def setup(runtime, *, model=False):
    db, data, _, now, ledger, _ = runtime
    now[0] = FIRST - timedelta(minutes=20)
    callback = predict_registered_path_forecast if model else descriptive
    item = InstalledComponent(
        "model" if model else "feature",
        "COND_VOL_V1" if model else "description",
        BTC.scope_id,
        "production-original-test",
        "input.v1",
        "output.v1",
        callback,
        {"actual_callback": Path(inspect.getsourcefile(callback))},
        {},
        {} if model else {"offset": 1},
    )
    reader = ProductionComponentOriginalReader(
        db, (item,), data.raw_archive, path_ledger=ledger, clock=lambda: now[0]
    )
    registry = ComponentRegistry(db, original_reader=reader, clock=lambda: now[0])
    manifest = registry.register(item.manifest())
    trial = registry.shadow(
        "component-shadow",
        version_id=item.version_id,
        protocol_hash=PATH_PROTOCOL_HASH if model else "b" * 64,
        baseline_version_id=None,
        start_at=FIRST,
        end_at=FIRST + timedelta(days=30),
    )
    return registry, reader, manifest, trial


def capture(runtime, reader, *, request="capture", payload=None):
    db, data, _, now, _, _ = runtime
    digest, ref = data.raw_archive.put(json.dumps(payload or {"value": 2}).encode())
    with db.session() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        result = reader.record_shadow_in_session(
            session,
            request_id=request,
            trial_id="component-shadow",
            input_ref=ref,
            input_hash=digest,
            source_known_at=FIRST - timedelta(seconds=1),
            cutoff_at=FIRST,
        )
    return result, ref


def test_actual_no_pointer_shadow_replay_and_default_review_remains_blocked(runtime):
    registry, reader, manifest, trial = setup(runtime)
    runtime[3][0] = FIRST + timedelta(seconds=10)
    body, _ = capture(runtime, reader)
    again, _ = capture(runtime, reader)
    assert again == body and body["output"] == {"value": 3}
    assert (
        registry.current(
            kind=manifest.kind, component_ref=manifest.component_ref, scope_id=manifest.scope_id
        )
        is None
    )
    review = registry.review(
        "real-review", trial_id=trial.trial_id, reviewer_ref="ops://manual-review"
    )
    assert review.decision == "not_admitted"
    assert review.qualification.status == "unavailable"
    assert "component_shadow_protocol_unsupported" in review.qualification.reasons
    assert not review.qualification.sample_qualified and not review.qualification.cost_verified
    with pytest.raises(ValueError, match="review_not_admitted"):
        registry.activate("no-bootstrap", review_id=review.review_id, expected_generation=0)


def test_raw_metadata_future_clock_and_actual_readonly_boundary(runtime):
    _, reader, manifest, trial = setup(runtime)
    runtime[3][0] = FIRST + timedelta(seconds=10)
    body, ref = capture(runtime, reader)
    db, data, *_ = runtime
    with db.session() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        row = session.get(MarketComponentAuditRecord, body["audit_id"])
        row.pointer_id = "f" * 64
    assert (
        reader.verify(manifest, trial).status == "unavailable"
    )  # Missing exact trial census, no latest substitute.
    with db.session() as session:
        row = session.get(MarketComponentAuditRecord, body["audit_id"])
        row.pointer_id = reader._pointer(trial.trial_id)
    digest = ref.rsplit("/", 1)[1]
    (data.raw_archive.root / digest[:2] / digest).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="raw_capture_archive_corrupt|raw_original_changed"):
        reader.verify(manifest, trial)
    digest, new_ref = data.raw_archive.put(b'{"value":4}')
    with db.session() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        native = session.connection().connection.driver_connection
        before = native.total_changes
        with pytest.raises(ValueError, match="capture_clock_invalid"):
            reader.record_shadow_in_session(
                session,
                request_id="future",
                trial_id=trial.trial_id,
                input_ref=new_ref,
                input_hash=digest,
                source_known_at=FIRST + timedelta(microseconds=1),
                cutoff_at=FIRST,
            )
        assert native.total_changes == before and native.in_transaction


def test_real_after_flush_audit_mutation_rolls_back_without_pointer_changes(runtime):
    _, reader, _, _ = setup(runtime)
    runtime[3][0] = FIRST + timedelta(seconds=10)
    db = runtime[0]

    def poison(session, _):
        session.execute(
            text("UPDATE market_component_audits SET payload_hash=:bad"), {"bad": "0" * 64}
        )

    event.listen(Session, "after_flush", poison)
    try:
        with pytest.raises(ValueError, match="audit_original_invalid"):
            capture(runtime, reader)
    finally:
        event.remove(Session, "after_flush", poison)
    with db.session() as session:
        assert session.scalars(select(MarketComponentAuditRecord)).all() == []


def test_real_path_originals_mature_denominator_recompute_never_self_reported_pass(runtime):
    registry, reader, manifest, trial = setup(runtime, model=True)
    db, data, _, now, ledger, jobs = runtime
    path_trial = first_predictions(runtime)
    views = [
        ledger.prediction(row["prediction_id"])
        for row in ledger.predictions(BTC.scope_id, trial_id=path_trial)
    ]
    candidate = next(view for view in views if view["forecast"]["model_version"] == "COND_VOL_V1")
    model = ledger.fitted_model(path_trial, BTC.scope_id)
    feature = candidate["feature_manifest"]
    raw_input = {
        "schema_version": "path-forecast-consumption-input.v1",
        "trial_id": path_trial,
        "job_id": "actual-shadow-capture",
        "scope_id": BTC.scope_id,
        "model_version": "COND_VOL_V1",
        "model": model.model_dump(mode="json"),
        "cutoff_at": FIRST.isoformat(),
        "published_at": now[0].isoformat(),
        "features": feature["features"],
        "feature_manifest_hash": feature["payload_hash"],
    }
    recorded, _ = capture(runtime, reader, payload=raw_input)
    assert recorded["output"] == candidate["forecast"]
    now[0] = FIRST + timedelta(days=1, minutes=1, seconds=10)
    assert asyncio.run(data.refresh_due()) == 2
    assert jobs.process_due(limit=2) == 2
    with db.session() as session:
        rows = session.scalars(select(RiskPathObservationRecord)).all()
        refs = [
            {
                "observation_id": row.observation_id,
                "payload_hash": row.payload_hash,
                "complete": True,
            }
            for row in rows
        ]
    labels = tuple(ledger.verified_observation(ref["observation_id"]) for ref in refs)
    all_views = tuple(
        ledger.predictions(scope, trial_id=path_trial) for scope in (BTC.scope_id, ETH.scope_id)
    )
    predictions = [entry for group in all_views for entry in group]
    forecasts = tuple(RiskPathForecast.model_validate(entry["forecast"]) for entry in predictions)
    facts = ledger.trial(path_trial)
    slots = tuple(
        (scope["scope_id"], FIRST + timedelta(days=day))
        for scope in facts["protocol"]["scopes"]
        for day in range(PARAMETERS.shadow_days)
    )
    now[0] = FIRST + timedelta(days=30)
    score = score_path_forecasts(forecasts, labels, expected_slots=slots, as_of=now[0])
    raw = {
        "report_id": "real-original-evaluation",
        "trial_id": path_trial,
        "report_version": REPORT,
        "computed_at": now[0].isoformat(),
        "protocol_hash": facts["protocol_hash"],
        "score": score,
        "prediction_refs": [
            {
                "prediction_id": entry["prediction_id"],
                "payload_hash": entry["forecast"]["payload_hash"],
            }
            for entry in predictions
        ],
        "observation_refs": refs,
    }
    full = {**raw, "payload_hash": canonical_hash(raw)}
    with db.session() as session:
        session.add(
            RiskEvaluationReportRecord(
                report_id=raw["report_id"],
                trial_id=path_trial,
                report_version=REPORT,
                protocol_hash=facts["protocol_hash"],
                computed_at=now[0],
                payload_json=json.dumps(full),
                payload_hash=full["payload_hash"],
            )
        )
    with db.session() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        reader.record_evaluation_in_session(
            session, request_id="real-report", trial_id=trial.trial_id, report_id=raw["report_id"]
        )
    result = reader.verify(manifest, trial)
    assert (
        result.status == "insufficient" and not result.sample_qualified and not result.cost_verified
    )
    assert score["expected_slot_count"] == 2 * PARAMETERS.shadow_days
    assert score["unavailable_label_count"] == score["expected_slot_count"] - 2
    assert "component_shadow_actual_usage_cost_originals_unavailable" in result.reasons
    review = registry.review(
        "cannot-use-pass-flag", trial_id=trial.trial_id, reviewer_ref="ops://real-review"
    )
    assert review.decision == "not_admitted"
    with db.session() as session:
        row = session.get(RiskEvaluationReportRecord, raw["report_id"])
        forged = json.loads(row.payload_json)
        forged["score"]["conclusion"] = "passed"
        forged["payload_hash"] = canonical_hash(
            {key: value for key, value in forged.items() if key != "payload_hash"}
        )
        row.payload_json, row.payload_hash = json.dumps(forged), forged["payload_hash"]
    with pytest.raises(ValueError, match="score_recomputation_failed"):
        reader.verify(manifest, trial)

"""Real isolated capture/store/SQL plumbing with explicit synthetic transport and clock."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from alembic import command
from sqlalchemy import inspect, text

from packages.kernel.decision_hub_kernel.application.market_risk_data import MarketRiskDataService
from packages.kernel.decision_hub_kernel.application.path_shadow_jobs import PathShadowJobService
from packages.kernel.decision_hub_kernel.application.risk_path_ledger import RiskPathLedger
from packages.kernel.decision_hub_kernel.decision.path_forecast import fit_path_forecast
from packages.kernel.decision_hub_kernel.persistence.db import Base, Database, OutboxRecord
from packages.kernel.decision_hub_kernel.persistence.path_forecast_records import (
    RiskEvaluationReportRecord,
    RiskPathJobRecord,
    RiskPathObservationRecord,
    RiskPredictionRecord,
    RiskShadowTrialRecord,
)
from packages.kernel.decision_hub_kernel.persistence.risk_records import MarketCandleRecord
from packages.kernel.decision_hub_kernel.ports.market_path import spot_scope
from packages.provider_adapters.market.okx_path import OKXMarketPathAdapter
from packages.provider_adapters.research.raw_capture import RawBodyArchive
from tests.market_risk.test_market_data import PublicFixture

BTC, ETH = spot_scope("BTC-USDT"), spot_scope("ETH-USDT")
FIRST = datetime(2026, 10, 10, tzinfo=UTC)
TABLES = tuple(
    record.__table__
    for record in (
        RiskShadowTrialRecord,
        RiskPathJobRecord,
        RiskPredictionRecord,
        RiskPathObservationRecord,
        RiskEvaluationReportRecord,
    )
)


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "data"))
    db = Database(f"sqlite+pysqlite:///{tmp_path / 'shadow.sqlite3'}")
    db.create_all()
    now = [FIRST - timedelta(minutes=10)]
    transport = PublicFixture(now)
    data = MarketRiskDataService(
        db,
        RawBodyArchive(tmp_path / "raw"),
        tmp_path / "features",
        adapter=OKXMarketPathAdapter(fetcher=transport.fetch),
        scopes=(BTC, ETH),
        clock=lambda: now[0],
        enabled=True,
    )
    ledger = RiskPathLedger(db, data.store, clock=lambda: now[0])
    jobs = PathShadowJobService(ledger, owner_id="isolated-worker-a")
    yield db, data, transport, now, ledger, jobs
    db.engine.dispose()


def register(ledger):
    return ledger.register(request_id="shadow-request-1", first_cutoff_at=FIRST)


def first_predictions(runtime):
    db, data, transport, now, ledger, jobs = runtime
    trial = register(ledger)
    assert jobs.process_due(limit=2) == 2  # Freeze authenticated, insufficient history.
    now[0] = FIRST - timedelta(seconds=1)
    assert asyncio.run(data.refresh_due()) == 2
    now[0] = FIRST + timedelta(seconds=10)
    assert jobs.process_due(limit=2) == 2
    with db.session() as session:
        assert session.query(RiskPredictionRecord).count() == 4
    return trial


def test_registration_future_only_overlap_idempotence_and_frozen_protocol(runtime):
    _, _, _, now, ledger, _ = runtime
    trial = register(ledger)
    now[0] = FIRST + timedelta(days=40)
    assert register(ledger) == trial
    with pytest.raises(ValueError, match="request_identity_conflict"):
        ledger.register(request_id="shadow-request-1", first_cutoff_at=FIRST + timedelta(days=1))
    with pytest.raises(ValueError, match="must_precede"):
        ledger.register(request_id="past", first_cutoff_at=FIRST)
    now[0] = FIRST - timedelta(minutes=5)
    with pytest.raises(ValueError, match="overlapping_scope_trial"):
        ledger.register(request_id="duplicate-experiment", first_cutoff_at=FIRST)
    facts = ledger.trial(trial)
    assert len(facts["jobs"]) == 115
    assert facts["label_deadline_at"] == FIRST + timedelta(days=29, minutes=1)
    assert facts["protocol"]["parameters"]["maximum_feature_delay_seconds"] == 180


def test_real_capture_feature_receipts_pair_immutability_and_restart(runtime):
    db, _, _, now, ledger, _ = runtime
    trial = first_predictions(runtime)
    records = ledger.predictions(BTC.scope_id, trial_id=trial)
    assert len(records) == 2
    for record in records:
        view = ledger.prediction(record["prediction_id"])
        manifest = view["feature_manifest"]
        assert len(manifest["originals"]) == 33
        assert manifest["feature_delay_seconds"] == 60
        assert (
            datetime.fromisoformat(manifest["features"]["known_at"].replace("Z", "+00:00")) < FIRST
        )
        assert record["forecast"]["feature_delay_seconds"] == 60
        assert record["forecast"]["anchor_price"] is None
        assert all(item["probability"] is None for item in record["forecast"]["frequencies"])
    restarted = PathShadowJobService(ledger, owner_id="isolated-worker-restart")
    assert restarted.process_due(limit=4) == 0
    with db.session() as session:
        assert session.query(RiskPredictionRecord).count() == 4
    assert ledger.fitted_model(trial, BTC.scope_id).baseline.n == 0


def test_concurrent_claim_and_expired_owner_cannot_complete_frozen_fit(runtime):
    db, _, _, now, ledger, jobs = runtime
    trial = register(ledger)
    identity = next(job["job_id"] for job in ledger.trial(trial)["jobs"] if job["kind"] == "fit")
    second = PathShadowJobService(ledger, owner_id="isolated-worker-b")
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda worker: worker.claim(identity), (jobs, second)))
    old = next(claim for claim in claims if claim is not None)
    assert sum(claim is not None for claim in claims) == 1
    read_at = now[0]
    now[0] += timedelta(seconds=121)
    new = second.claim(identity)
    assert new.generation == old.generation + 1
    scope = BTC if new.scope_id == BTC.scope_id else ETH
    model = fit_path_forecast(scope, (), training_cutoff_at=FIRST)
    assert not ledger.save_fit(old, model, read_as_of=read_at)
    assert ledger.save_fit(new, model, read_as_of=read_at)
    assert not ledger.save_fit(old, model, read_as_of=read_at)
    with db.session() as session:
        assert session.get(RiskPathJobRecord, identity).state == "succeeded"


def test_mature_label_complete_fixed_anchor_and_original_tamper_detected(runtime):
    _, data, _, now, ledger, jobs = runtime
    trial = first_predictions(runtime)
    with pytest.raises(ValueError, match="label_not_mature"):
        ledger.inputs.observe(BTC, FIRST)
    assert all(
        job["state"] == "pending" for job in ledger.trial(trial)["jobs"] if job["kind"] == "label"
    )
    now[0] = FIRST + timedelta(days=1, minutes=1, seconds=10)
    assert asyncio.run(data.refresh_due()) == 2
    assert jobs.process_due(limit=2) == 2
    label_jobs = [
        job
        for job in ledger.trial(trial)["jobs"]
        if job["kind"] == "label" and job["cutoff_at"] == FIRST
    ]
    assert all(job["state"] == "succeeded" for job in label_jobs)
    with ledger.database.session() as session:
        row = session.query(RiskPathObservationRecord).filter_by(scope_id=BTC.scope_id).one()
        identity, raw_ref = row.observation_id, None
    receipt = ledger.verified_observation(identity)
    assert receipt.path.anchor_at == FIRST + timedelta(minutes=1)
    assert receipt.path.window_end_at == FIRST + timedelta(days=1, minutes=1)
    assert receipt.path.observed_count == 1440
    assert receipt.label_known_at == now[0]
    raw_ref = receipt.originals[0].capture_ref
    digest = raw_ref.rsplit("/", 1)[1]
    (data.raw_archive.root / digest[:2] / digest).write_bytes(b"tampered original")
    with pytest.raises(ValueError, match="raw_capture_archive_corrupt"):
        ledger.verified_observation(identity)


def test_partial_label_retry_appends_version_and_missing_anchor_does_not_move(runtime):
    db, data, transport, now, ledger, jobs = runtime
    trial = first_predictions(runtime)
    transport.omit_middle = True
    now[0] = FIRST + timedelta(days=1, minutes=1, seconds=10)
    assert asyncio.run(data.refresh_due()) == 0
    assert jobs.process_due(limit=2) == 2
    with db.session() as session:
        partial = session.query(RiskPathObservationRecord).filter_by(scope_id=BTC.scope_id).one()
        partial_id = partial.observation_id
        assert partial.quality == "observed_lower_bound"
    with pytest.raises(ValueError, match="not_complete"):
        ledger.verified_observation(partial_id)
    transport.omit_middle = False
    now[0] += timedelta(minutes=5)
    assert asyncio.run(data.refresh_due()) == 2
    assert jobs.process_due(limit=2) == 2
    with db.session() as session:
        assert (
            session.query(RiskPathObservationRecord).filter_by(scope_id=BTC.scope_id).count() == 2
        )
        old = session.get(RiskPathObservationRecord, partial_id)
        assert old.quality == "observed_lower_bound"
        new = (
            session.query(RiskPathObservationRecord)
            .filter_by(
                scope_id=BTC.scope_id,
                quality="observed",
            )
            .one()
        )
    assert ledger.verified_observation(new.observation_id).path.anchor_at == FIRST + timedelta(
        minutes=1
    )
    now[0] = ledger.trial(trial)["label_deadline_at"]
    assert jobs.process_due(limit=1) == 1
    report = ledger.score(trial)
    assert report["score"]["complete_label_count"] == 2
    assert report["score"]["partial_label_count"] == 0
    assert report["score"]["unavailable_label_count"] == 54
    assert report["score"]["published_prediction_count"] == 4
    assert ledger.verified_report(report["report_id"])["payload_hash"] == report["payload_hash"]


def test_missing_first_bar_keeps_designated_anchor_unknown_and_unscorable(runtime):
    db, data, _, now, ledger, jobs = runtime
    trial = first_predictions(runtime)
    now[0] = FIRST + timedelta(days=1, minutes=1, seconds=10)
    assert asyncio.run(data.refresh_due()) == 2
    with db.session() as session:
        session.query(MarketCandleRecord).filter_by(
            scope_id=BTC.scope_id,
            open_at=FIRST + timedelta(minutes=1),
        ).delete()
    receipt = ledger.inputs.observe(BTC, FIRST)
    assert receipt.path.anchor_at == FIRST + timedelta(minutes=1)
    assert receipt.path.anchor_status == "unknown"
    assert receipt.path.anchor_price is None and receipt.path.mae_long is None
    identity = next(
        job["job_id"]
        for job in ledger.trial(trial)["jobs"]
        if job["kind"] == "label" and job["scope_id"] == BTC.scope_id and job["cutoff_at"] == FIRST
    )
    claim = jobs.claim(identity)
    assert ledger.save_observation(claim, receipt)
    assert (
        next(job["state"] for job in ledger.trial(trial)["jobs"] if job["job_id"] == identity)
        == "pending"
    )


def test_fixed_28_day_deadline_no_backfilled_forecast_and_no_samples_closes(runtime):
    db, _, _, now, ledger, jobs = runtime
    trial = register(ledger)
    deadline = ledger.trial(trial)["label_deadline_at"]
    now[0] = deadline - timedelta(microseconds=1)
    finalize_id = next(
        job["job_id"] for job in ledger.trial(trial)["jobs"] if job["kind"] == "finalize"
    )
    assert jobs.claim(finalize_id) is None
    now[0] = deadline
    assert jobs.process_due(limit=1) == 1
    assert ledger.trial(trial)["state"] == "completed"
    result = ledger.score(trial)
    assert result["score"]["expected_prediction_count"] == 112
    assert result["score"]["unpublished_prediction_count"] == 112
    assert result["score"]["unavailable_label_count"] == 56
    assert result["score"]["conclusion"] == "insufficient_evidence"
    assert result["score"]["ci"] is None
    assert jobs.process_due(limit=100) == 0
    with db.session() as session:
        assert session.query(RiskPredictionRecord).count() == 0
        assert session.query(RiskEvaluationReportRecord).count() == 1


def test_cutoff_after_receipt_cannot_backfill_pit_feature_and_paused_trial_still_closes(runtime):
    db, data, _, now, ledger, jobs = runtime
    trial = register(ledger)
    assert jobs.process_due(limit=2) == 2
    now[0] = FIRST + timedelta(seconds=10)
    assert asyncio.run(data.refresh_due()) == 2
    assert jobs.process_due(limit=2) == 2
    for record in ledger.predictions(BTC.scope_id, trial_id=trial):
        manifest = ledger.prediction(record["prediction_id"])["feature_manifest"]
        assert manifest["originals"] == []
        assert manifest["feature_delay_seconds"] is None
    with db.session() as session:
        session.get(RiskShadowTrialRecord, trial).state = "paused"
    now[0] = ledger.trial(trial)["label_deadline_at"]
    assert jobs.process_due(limit=1) == 1
    assert ledger.trial(trial)["state"] == "completed"
    assert ledger.score(trial)["score"]["published_prediction_count"] == 4


def test_self_hashed_synthetic_fit_is_rejected_without_authenticated_originals(runtime):
    from tests.market_risk.test_path_forecast import window

    _, _, _, now, ledger, jobs = runtime
    trial = register(ledger)
    identity = next(
        job["job_id"]
        for job in ledger.trial(trial)["jobs"]
        if job["kind"] == "fit" and job["scope_id"] == BTC.scope_id
    )
    claim = jobs.claim(identity)
    synthetic = fit_path_forecast(
        BTC,
        tuple(window(i, cutoff=FIRST - timedelta(days=62 - i)) for i in range(60)),
        training_cutoff_at=FIRST,
    )
    with pytest.raises(ValueError, match="training_original_manifest_changed"):
        ledger.save_fit(claim, synthetic, read_as_of=now[0])
    assert ledger.fitted_model(trial, BTC.scope_id) is None


def test_real_alembic_0077_to_0078_preserves_sent_outbox_and_matches_five_table_orm(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "data"))
    db = Database(f"sqlite+pysqlite:///{tmp_path / 'migrated.sqlite3'}")
    command.upgrade(db._alembic_config(), "0077_run_spot_admissions")
    with db.session() as session:
        session.add(
            OutboxRecord(
                artifact_id="legacy-report-original",
                subject_id="legacy-report-original",
                channel="local",
                dedupe_key="legacy-sent-identity",
                attempts=1,
                sent_at=FIRST - timedelta(days=1),
                dispatch_status="legacy_sent",
            )
        )
    with db.engine.connect() as connection:
        before = connection.execute(text("SELECT * FROM outbox")).all()
    command.upgrade(db._alembic_config(), "0078_path_forecast_ledger")
    with db.engine.connect() as connection:
        assert connection.execute(text("SELECT * FROM outbox")).all() == before
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
            "0078_path_forecast_ledger"
        )
    actual = inspect(db.engine)
    for table in TABLES:
        columns = {column["name"]: column for column in actual.get_columns(table.name)}
        assert set(columns) == set(table.columns.keys())
        assert {column["name"] for column in columns.values() if column["nullable"]} == {
            column.name for column in table.columns if column.nullable
        }
        assert {index["name"] for index in actual.get_indexes(table.name)} == {
            index.name for index in table.indexes
        }
        for column in table.columns:
            assert columns[column.name]["type"]._type_affinity is column.type._type_affinity
            if hasattr(column.type, "length"):
                assert columns[column.name]["type"].length == column.type.length
        assert {
            (index["name"], tuple(index["column_names"]))
            for index in actual.get_indexes(table.name)
        } == {
            (index.name, tuple(column.name for column in index.columns)) for index in table.indexes
        }
    assert Base.metadata.tables["risk_predictions"] is RiskPredictionRecord.__table__
    db.engine.dispose()

"""Bounded engineering fixtures; no predictive gain or calibrated odds claimed."""

import asyncio
import json
from datetime import timedelta
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from apps.hub_api.forecast_alarm_routes import forecast_alarm_router
from packages.kernel.decision_hub_kernel.application.forecast_alarm import (
    ALARM_ROLE,
    RECEIPT_ROLE,
    ForecastAlarmService,
)
from packages.kernel.decision_hub_kernel.application.market_risk_data import MarketRiskDataService
from packages.kernel.decision_hub_kernel.application.path_forecast_history import seal_payload
from packages.kernel.decision_hub_kernel.application.risk_path_ledger import (
    RiskPathLedger,
    encoded,
    writer,
)
from packages.kernel.decision_hub_kernel.decision.forecast_alarm_matching import (
    match_forecast_alarms,
    primary_event_clusters,
)
from packages.kernel.decision_hub_kernel.decision.path_forecast import (
    fit_path_forecast,
    predict_path_forecast,
)
from packages.kernel.decision_hub_kernel.persistence.db import Database, RunRoleOutputRecord
from packages.kernel.decision_hub_kernel.persistence.path_forecast_records import (
    RiskPathObservationRecord,
    RiskPredictionRecord,
    RiskShadowTrialRecord,
)
from packages.kernel.decision_hub_kernel.ports.forecast_alarm import (
    ALARM_POLICY,
    AlarmOutcomeV1,
    ForecastAlarmReceiptV1,
    ForecastAlarmV1,
)
from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash
from packages.kernel.decision_hub_kernel.ports.path_forecast import PathFeatureManifest
from packages.provider_adapters.market.okx_path import CapturedMarketPage, OKXMarketPathAdapter
from packages.provider_adapters.research.raw_capture import CURRENT_CAPTURE, RawBodyArchive
from tests.market_risk.test_market_data import PublicFixture
from tests.market_risk.test_path_forecast import BTC, CUTOFF, ETH, feature, window


def alarm(scope=BTC, *, method="COND_VOL_V1", identity="alarm", known=None):
    return seal_payload(
        ForecastAlarmV1(
            alarm_id=identity,
            trial_id="trial",
            prediction_id="prediction",
            prediction_hash="a" * 64,
            scope_id=scope.scope_id,
            method=method,
            cutoff_at=CUTOFF,
            published_at=CUTOFF,
            window_start_at=CUTOFF + timedelta(minutes=1),
            window_end_at=CUTOFF + timedelta(days=1, minutes=1),
            direction="long",
            threshold=Decimal(".02"),
            frequency=Decimal(".5"),
            known_at=known or CUTOFF,
            payload_hash="0" * 64,
        )
    )


def outcome(scope=BTC, *, minute=10, presence=True, firstness="verified", original="label"):
    start = CUTOFF + timedelta(minutes=minute)
    return AlarmOutcomeV1(
        scope_id=scope.scope_id,
        cutoff_at=CUTOFF,
        window_start_at=CUTOFF + timedelta(minutes=1),
        window_end_at=CUTOFF + timedelta(days=1, minutes=1),
        direction="long",
        threshold=Decimal(".02"),
        event_presence=presence,
        firstness=firstness,
        interval_start_at=start if presence else None,
        interval_end_at=start + timedelta(minutes=1) if presence else None,
        label_known_at=CUTOFF + timedelta(days=1, minutes=2),
        original_ref=original,
        original_hash=canonical_hash(original),
    )


def score(alarms, outcomes, receipts=(), slots=None):
    return match_forecast_alarms(
        alarms,
        outcomes,
        receipts,
        expected_slots=slots or ((BTC.scope_id, CUTOFF),),
        as_of=CUTOFF + timedelta(days=2),
    )


def test_hand_precision_recall_unknown_denominator_and_minute_conservative_lead():
    result = score(
        (alarm(),), (outcome(minute=5),), slots=((BTC.scope_id, CUTOFF), (ETH.scope_id, CUTOFF))
    )
    assert result["metrics"]["precision"] == "1"
    assert result["metrics"]["recall"] == "1"
    assert result["metrics"]["lead_time"]["minimum_seconds"] == "300"
    assert result["metrics"]["status"] == "partial"
    assert result["counts"]["candidate_primary_unknown_labels"] == 1
    too_soon = score((alarm(known=CUTOFF + timedelta(seconds=1)),), (outcome(minute=5),))
    assert too_soon["metrics"]["precision"] == "0"
    assert too_soon["metrics"]["recall"] == "0"
    assert too_soon["metrics"]["lead_time"] is None
    unknown = score((alarm(),), (outcome(firstness="unknown"),))
    assert unknown["metrics"]["precision"] is None
    assert unknown["metrics"]["recall"] is None
    assert unknown["exclusions"][0]["reason"] == "label_or_firstness_unknown"


def test_one_to_one_exact_direction_window_and_primary_cross_asset_clusters():
    with pytest.raises(ValueError, match="duplicate_target_window"):
        score((alarm(), alarm(identity="second")), (outcome(),))
    result = score((alarm(),), (outcome().model_copy(update={"direction": "short"}),))
    assert result["metrics"]["precision"] is None
    # Events 29 minutes apart across a bucket boundary remain one global cluster.
    clusters = primary_event_clusters(
        (outcome(minute=29), outcome(ETH, minute=58, original="eth-label"))
    )
    assert len(clusters) == 1 and len(clusters[0]["members"]) == 2
    separated = primary_event_clusters(
        (outcome(minute=1), outcome(ETH, minute=33, original="eth-label"))
    )
    assert len(separated) == 2
    # Event census is retained even if there was no emitted warning.
    assert len(score((), (outcome(),))["event_clusters"]) == 1


def test_delivered_visible_and_missing_receipts_are_separate_from_system_matches():
    warning = alarm()
    shown = seal_payload(
        ForecastAlarmReceiptV1(
            receipt_id="shown",
            alarm_id=warning.alarm_id,
            alarm_hash=warning.payload_hash,
            receipt_kind="displayed",
            owner_id="pc",
            request_id="display",
            known_at=CUTOFF,
            payload_hash="0" * 64,
        )
    )
    visible = seal_payload(
        ForecastAlarmReceiptV1(
            receipt_id="visible",
            alarm_id=warning.alarm_id,
            alarm_hash=warning.payload_hash,
            receipt_kind="visible",
            owner_id="pc",
            request_id="visible",
            known_at=CUTOFF + timedelta(minutes=7),
            parent_receipt_id="shown",
            parent_receipt_hash=shown.payload_hash,
            visible_duration_ms=1000,
            payload_hash="0" * 64,
        )
    )
    target = next(
        x
        for x in score((warning,), (outcome(),), (shown, visible))["by_target"]
        if x["method"] == "COND_VOL_V1" and x["direction"] == "long" and x["threshold"] == "0.02"
    )
    assert target["precision"] == "1" and target["delivery"]["precision"] == "1"
    assert target["exposure"]["precision"] == "0"  # Actual visibility was only 3min early.
    empty = next(
        x
        for x in score((warning,), (outcome(),))["by_target"]
        if x["method"] == "COND_VOL_V1" and x["direction"] == "long" and x["threshold"] == "0.02"
    )
    assert empty["delivery"]["precision"] is None and empty["exposure"]["precision"] is None


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "data"))
    db = Database(f"sqlite+pysqlite:///{tmp_path / 'alarm.sqlite3'}")
    db.create_all()
    now = [CUTOFF - timedelta(minutes=5)]
    data = MarketRiskDataService(
        db,
        RawBodyArchive(tmp_path / "raw"),
        tmp_path / "features",
        enabled=True,
        scopes=(BTC,),
        clock=lambda: now[0],
        adapter=OKXMarketPathAdapter(fetcher=PublicFixture(now).fetch),
    )
    ledger = RiskPathLedger(db, data.store, clock=lambda: now[0])
    trial = ledger.register(request_id="alarm-fixture", first_cutoff_at=CUTOFF, scopes=(BTC,))
    # A prospective, explicitly controlled experimental protocol fixture. Never
    # mutate any retained/live trial; the real new-version producer is Root-owned.
    with db.session() as session:
        row = session.get(RiskShadowTrialRecord, trial)
        protocol = ledger.protocol(row)
        protocol["alarm_policy"] = ALARM_POLICY
        row.protocol_json = encoded(protocol)
        row.protocol_hash = row.registration_hash = canonical_hash(protocol)
    now[0] = CUTOFF
    model = fit_path_forecast(BTC, tuple(window(i) for i in range(60)), training_cutoff_at=CUTOFF)
    features = feature(CUTOFF, rv="3")
    manifest = seal_payload(
        PathFeatureManifest(
            scope=BTC,
            cutoff_at=CUTOFF,
            captured_at=CUTOFF,
            features=features,
            feature_delay_seconds=0,
            originals=(),
            payload_hash="0" * 64,
        )
    )
    refs = []
    with db.session() as session:
        for method in ("BASE_EMPIRICAL_V1", "COND_VOL_V1"):
            forecast = predict_path_forecast(
                model,
                cutoff_at=CUTOFF,
                published_at=CUTOFF,
                features=features,
                model_version=method,
            )
            identity = "fixture_prediction_" + method
            session.add(
                RiskPredictionRecord(
                    prediction_id=identity,
                    trial_id=trial,
                    scope_id=BTC.scope_id,
                    method=method,
                    cutoff_at=CUTOFF,
                    published_at=CUTOFF,
                    horizon_seconds=86400,
                    target_version="long-short-mae-2pct-5pct.v1",
                    model_hash=model.model_hash,
                    feature_manifest_json=encoded(manifest.model_dump(mode="json")),
                    feature_manifest_hash=manifest.payload_hash,
                    payload_json=encoded(forecast.model_dump(mode="json")),
                    payload_hash=forecast.payload_hash,
                )
            )
            refs.append({"prediction_id": identity, "payload_hash": forecast.payload_hash})
    service = ForecastAlarmService(db, data.store, clock=lambda: now[0])
    with db.session() as session:
        writer(session)
        alarms = service.publish_pair_in_session(
            session, request_id="publication-fixture", prediction_refs=tuple(refs), known_at=now[0]
        )
    yield db, now, data, ledger, service, trial, tuple(refs), alarms
    db.engine.dispose()


def test_real_sql_publication_idempotence_pc_owner_api_and_receipt_originals(runtime):
    db, now, _, _, service, _, refs, alarms = runtime
    assert len(alarms) == 2
    with db.session() as session:
        writer(session)
        assert (
            service.publish_pair_in_session(
                session, request_id="publication-fixture", prediction_refs=refs, known_at=now[0]
            )
            == alarms
        )
    app = FastAPI()
    app.include_router(forecast_alarm_router(db, clock=lambda: now[0]))
    client = TestClient(app)
    url = f"/v1/risk/scopes/{BTC.scope_id}/forecast-alarms"
    assert client.get(url).status_code == 403
    listed = client.get(url, headers={"X-Owner-Id": "pc"}).json()
    assert len(listed["items"]) == 2
    with db.session() as session:
        assert (
            session.scalar(
                select(RunRoleOutputRecord.output_id).where(
                    RunRoleOutputRecord.role == RECEIPT_ROLE
                )
            )
            is None
        )
    identity = alarms[0]["alarm_id"]
    prefix = f"/v1/risk/forecast-alarms/{identity}"
    body = {"owner_id": "pc", "request_id": "shown"}
    assert (
        client.post(prefix + "/display", json=body, headers={"X-Owner-Id": "other"}).status_code
        == 403
    )
    assert (
        client.post(
            prefix + "/display", json=body, headers={"X-Owner-Id": "pc", "Idempotency-Key": "other"}
        ).status_code
        == 409
    )
    shown = client.post(prefix + "/display", json=body, headers={"X-Owner-Id": "pc"})
    assert shown.status_code == 200
    now[0] += timedelta(seconds=2)
    assert (
        client.post(prefix + "/display", json=body, headers={"X-Owner-Id": "pc"}).json()
        == shown.json()
    )
    exposure = {
        "owner_id": "pc",
        "request_id": "visible",
        "display_receipt_id": shown.json()["receipt_id"],
        "visible": True,
        "surface": "pc",
        "visible_duration_ms": 1000,
    }
    assert (
        client.post(
            prefix + "/exposure", json={**exposure, "visible": False}, headers={"X-Owner-Id": "pc"}
        ).status_code
        == 422
    )
    visible = client.post(prefix + "/exposure", json=exposure, headers={"X-Owner-Id": "pc"})
    assert visible.status_code == 200
    assert (
        client.post(
            prefix + "/acknowledge",
            json={
                "owner_id": "pc",
                "request_id": "ack",
                "exposure_receipt_id": visible.json()["receipt_id"],
            },
            headers={"X-Owner-Id": "pc"},
        ).status_code
        == 200
    )
    result = client.get(url, headers={"X-Owner-Id": "pc"}).json()
    assert any(x["acknowledged"] and x["exposure_status"] == "visible" for x in result["items"])
    with db.session() as session:
        row = session.scalar(
            select(RunRoleOutputRecord).where(RunRoleOutputRecord.role == ALARM_ROLE)
        )
        row.payload_json += "changed"
    assert client.get(url, headers={"X-Owner-Id": "pc"}).status_code == 409


def test_actual_raw_path_labels_pit_unknown_and_read_only_score_reverification(runtime):
    db, now, data, ledger, service, trial, _, _ = runtime
    early = service.score_originals(trial_id=trial, as_of=now[0])
    assert early["metrics"]["precision"] is None
    assert early["counts"]["candidate_primary_unknown_labels"] == 28
    now[0] = CUTOFF + timedelta(days=1, minutes=1, seconds=30)
    assert asyncio.run(data.refresh_due()) == 1
    label = ledger.inputs.observe(BTC, CUTOFF)
    assert label.path.window_status == "complete"
    with db.session() as session:
        session.add(
            RiskPathObservationRecord(
                observation_id="actual-raw-label",
                scope_id=BTC.scope_id,
                cutoff_at=CUTOFF,
                window_end_at=label.path.window_end_at,
                calculator_version=label.calculator_version,
                path_hash=label.path.manifest_hash,
                label_known_at=label.label_known_at,
                quality=label.path.quality,
                payload_json=encoded(label.model_dump(mode="json")),
                payload_hash=label.payload_hash,
            )
        )
    result = service.score_originals(trial_id=trial, as_of=now[0])
    assert result["metrics"]["precision"] == "0"
    assert result["metrics"]["recall"] is None  # No actual >=2% event in the raw path.
    assert result["counts"]["candidate_primary_unknown_labels"] == 27
    assert result["metrics"]["false_episodes_per_asset_day"] == "1"
    assert result["matched_event_blocks"] == []
    with db.session() as session:
        before = session.query(RunRoleOutputRecord).count()
        ref = result["score_original_ref"]
        verified = service.read_score_in_session(session, **ref)
        assert verified["payload_hash"] == result["payload_hash"]
        assert session.query(RunRoleOutputRecord).count() == before
    # The older point-in-time score cannot leak the now-available label.
    assert (
        service.verify_score_original(**early["score_original_ref"])["metrics"]["precision"] is None
    )
    with db.session() as session:
        row = session.get(RiskPathObservationRecord, "actual-raw-label")
        row.payload_json += "damaged"
    with pytest.raises(ValueError):
        service.verify_score_original(**result["score_original_ref"])


def test_actual_captured_positive_path_cluster_original_and_no_writer_on_read(runtime):
    db, now, data, ledger, service, trial, _, _ = runtime
    now[0] = CUTOFF + timedelta(days=1, minutes=1, seconds=30)
    crossing_stamp = int((CUTOFF + timedelta(minutes=10)).timestamp() * 1000)

    async def positive_raw(url):
        after = int(parse_qs(urlparse(url).query)["after"][0])
        rows = []
        for offset in range(1, 301):
            stamp = after - offset * 60_000
            rows.append(
                [
                    str(stamp),
                    "100",
                    "101",
                    "97" if stamp == crossing_stamp else "99",
                    "100",
                    "1",
                    "100",
                    "100",
                    "1",
                ]
            )
        body = json.dumps({"code": "0", "data": rows}).encode()
        batch = CURRENT_CAPTURE.get()
        assert batch is not None
        batch.record(
            url=url,
            started_at=now[0],
            completed_at=now[0],
            response=httpx.Response(200, content=body, request=httpx.Request("GET", url)),
        )
        return CapturedMarketPage(body, now[0], batch.captures[-1]["body_ref"])

    data.adapter = OKXMarketPathAdapter(fetcher=positive_raw)
    assert asyncio.run(data.refresh_due()) == 1
    label = ledger.inputs.observe(BTC, CUTOFF)
    assert label.path.window_status == "complete"
    assert label.path.crossings[0].interval_start_at == CUTOFF + timedelta(minutes=10)
    with db.session() as session:
        session.add(
            RiskPathObservationRecord(
                observation_id="actual-positive-raw-label",
                scope_id=BTC.scope_id,
                cutoff_at=CUTOFF,
                window_end_at=label.path.window_end_at,
                calculator_version=label.calculator_version,
                path_hash=label.path.manifest_hash,
                label_known_at=label.label_known_at,
                quality=label.path.quality,
                payload_json=encoded(label.model_dump(mode="json")),
                payload_hash=label.payload_hash,
            )
        )
    result = service.score_originals(trial_id=trial, as_of=now[0])
    assert result["metrics"]["precision"] == result["metrics"]["recall"] == "1"
    assert result["metrics"]["lead_time"]["minimum_seconds"] == "600"
    assert len(result["matched_event_blocks"]) == 1
    assert result["matched_event_blocks"][0]["scope_ids"] == [BTC.scope_id]
    with db.session() as session:
        before = session.query(RunRoleOutputRecord).count()
        assert (
            service.read_score_in_session(session, **result["score_original_ref"])["payload_hash"]
            == result["payload_hash"]
        )
        assert session.query(RunRoleOutputRecord).count() == before
        event = session.get(RunRoleOutputRecord, result["matched_event_blocks"][0]["original_ref"])
        event.payload_json += "damaged"
    with pytest.raises(ValueError):
        service.verify_score_original(**result["score_original_ref"])

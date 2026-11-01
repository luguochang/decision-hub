"""One native original chain and necessary activation negatives in an isolated DB.

Pre-event inputs are preserved real captured originals. October result levels
are an explicit engineering fixture because the November release is future.
No fixture result is an assertion about the real October CPI publication.
"""
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sqlite3
from unittest.mock import patch
import uuid

import pytest
from sqlalchemy import func, select

from packages.kernel.decision_hub_kernel.application.event_realization import (
    commit_prepared_event_realization_in_session,
    event_realization_user_view,
    freeze_event_realization_in_session,
    prepare_event_realization_in_session,
    read_event_realization_in_session,
    read_latest_event_realization_for_forecast_in_session,
)
from packages.kernel.decision_hub_kernel.application.event_scenario_autoproduction import (
    EventScenarioAutoproducer, read_event_owned_scenario_in_session,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database, ObservationRecord, RawCaptureRecord, RunRecord, SourcePollAttemptRecord,
)
from packages.kernel.decision_hub_kernel.persistence.event_realization_records import EventRealizationRecord
from packages.kernel.decision_hub_kernel.persistence.event_scenario_records import EventScenarioForecastRecord
from packages.kernel.decision_hub_kernel.ports.bls_cpi import (
    BLS_SOURCE_ID, BLS_URL, BLS_VERSION, parse_bls_cpi_metrics,
)
from packages.kernel.decision_hub_kernel.ports.sources import SourceContentSnapshot
from packages.provider_adapters.research.raw_capture import RawBodyArchive

ROOT = Path(__file__).resolve().parents[2]
SAVED = ROOT / "artifacts/decision-product-v1/normal14/event-scenario-probability-01/private"
REAL_BLS = ROOT / "artifacts/decision-product-v1/cpi-api-feasibility-01/response-body.bin"


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class FixtureArchive:
    def __init__(self, path):
        self.local = RawBodyArchive(path)
        self.saved = RawBodyArchive(SAVED / "raw-captures")

    def put(self, body):
        return self.local.put(body)

    def read(self, ref):
        try:
            return self.local.read(ref)
        except FileNotFoundError:
            return self.saved.read(ref)


def make_realization_case(tmp_path):
    """Preserved source rows only; no copied or invented research Run."""
    path = tmp_path / "event-realization.sqlite3"
    db = Database("sqlite:///" + path.as_posix())
    db.create_all()
    source = SAVED / "scenario-control-01.sqlite3"
    assert source.is_file(), "preserved_real_source_fixture_missing"
    with sqlite3.connect(path.as_uri(), uri=True) as target:
        target.execute("ATTACH DATABASE ? AS preserved", (source.as_uri() + "?mode=ro",))
        for table in ("observations", "source_poll_attempts", "raw_captures"):
            target.execute(f"INSERT INTO main.{table} SELECT * FROM preserved.{table}")
        target.commit()
    archive = FixtureArchive(tmp_path / "engineering-raw-captures")
    producer = EventScenarioAutoproducer(db, archive=archive, owner="isolated-M1")
    # M1 validates the event owner; asset opportunity generation belongs to M2.
    with patch.object(producer, "_opportunity", return_value=None):
        report = producer.tick()
    assert report.published_forecast_ids, report.errors
    forecast_id = report.published_forecast_ids[0]
    with db.session() as session:
        forecast = read_event_owned_scenario_in_session(session, forecast_id,
            archive=archive, cutoff_at=datetime.now(UTC))
        assert forecast.reference_period == "2026-10" and forecast.sample_count == 155
        assert session.scalar(select(func.count()).select_from(RunRecord)) == 0
    return db, archive, forecast


def engineering_actual_body(*, headline_factor="1.003", core_factor="1.002"):
    value = json.loads(REAL_BLS.read_bytes())
    for series in value["Results"]["series"]:
        september = next(p for p in series["data"] if p["year"] == "2026" and p["period"] == "M09")
        factor = Decimal(core_factor if "L1E" in series["seriesID"] else headline_factor)
        series["data"].insert(0, {"year": "2026", "period": "M10", "periodName": "October",
            "value": str(Decimal(september["value"]) * factor),
            "footnotes": [{"code": "ENGINEERING", "text": "isolated future release fixture, not real CPI"}]})
        # Preserve the real sample's unavailable 2025-10 basis. Missing YoY
        # cannot block the published target's complete MoM joint partition.
    return _json(value).encode()


def admit_bls_fixture(session, archive, *, body, period, known_at):
    """Materialize test HTTP capture/admission exactly as the native reader requires."""
    identity = uuid.uuid4().hex
    attempt_id, capture_id = "engineering-attempt:" + identity, "engineering-capture:" + identity
    received = known_at - timedelta(seconds=1)
    started = received - timedelta(seconds=1)
    digest, ref = archive.put(body)
    payloads = parse_bls_cpi_metrics(body, reference_period=period, provenance={
        "capture_id": capture_id, "attempt_id": attempt_id, "received_at": received,
        "request_startyear": 2024, "request_endyear": 2026})
    snapshot = SourceContentSnapshot(http_status=200, parser_version=BLS_VERSION,
        content_hash=digest, parse_status="valid", parsed_count=4)
    session.add(SourcePollAttemptRecord(attempt_id=attempt_id, source_id=BLS_SOURCE_ID,
        trigger_type="engineering_replay", status="succeeded", started_at=started,
        completed_at=known_at, received_count=4, accepted_count=4, duplicate_count=0,
        revision_count=0, content_snapshot_json=snapshot.model_dump_json()))
    session.add(RawCaptureRecord(capture_id=capture_id, context_id="source:" + attempt_id,
        url=BLS_URL, status_code=200, body_hash=digest, body_ref=ref, byte_count=len(body),
        media_type="application/json", adapter_version=BLS_VERSION, error_code=None,
        started_at=started, completed_at=received, recorded_at=received))
    for payload in payloads:
        raw = _json(payload.model_dump(mode="json"))
        session.add(ObservationRecord(observation_id="engineering:" + identity + ":" + payload.metric_id,
            event_id="engineering-fixture:" + identity, source_id=BLS_SOURCE_ID,
            source_type="official_feed", observed_at=received, received_at=received,
            published_at=None, scheduled_at=None, text=raw, language="en",
            event_hint="cpi_index_metric", source_url=BLS_URL,
            content_hash=hashlib.sha256(raw.encode()).hexdigest()))
    session.flush()


def test_real_original_forecast_to_unique_realization_and_required_negatives(tmp_path):
    db, archive, forecast = make_realization_case(tmp_path)
    release = forecast.scheduled_at
    known = release + timedelta(minutes=5)
    cutoff = known + timedelta(seconds=2)
    with db.session() as session:
        before = session.get(EventScenarioForecastRecord, forecast.forecast_id).payload_json
        assert freeze_event_realization_in_session(session, forecast.forecast_id,
            archive=archive, cutoff_at=release - timedelta(seconds=1), recorded_at=cutoff) is None
        assert event_realization_user_view(None, event_key=forecast.event_key,
            scheduled_at=release, cutoff_at=release - timedelta(seconds=1))["state"] == "awaiting_release"
        # A real September BLS original cannot label the October forecast.
        admit_bls_fixture(session, archive, body=REAL_BLS.read_bytes(), period="2026-09", known_at=known)
        assert freeze_event_realization_in_session(session, forecast.forecast_id,
            archive=archive, cutoff_at=cutoff, recorded_at=cutoff) is None
    # Actuals admitted before the certified T0 must not arm anything.
    with db.session() as session:
        admit_bls_fixture(session, archive, body=engineering_actual_body(), period="2026-10",
            known_at=release - timedelta(seconds=2))
        with pytest.raises(ValueError, match="actual_known_before_T0"):
            prepare_event_realization_in_session(session, forecast.forecast_id,
                archive=archive, cutoff_at=cutoff, recorded_at=cutoff)
        session.rollback()
    with db.session() as session:
        admit_bls_fixture(session, archive, body=engineering_actual_body(), period="2026-10", known_at=known)
    with db.session() as session:
        prepared = prepare_event_realization_in_session(session, forecast.forecast_id,
            archive=archive, cutoff_at=cutoff, recorded_at=cutoff)
    with db.session() as session:
        result = commit_prepared_event_realization_in_session(session, prepared)
        assert result.known_at == known and result.recorded_at == cutoff
        assert len(result.metrics) == 4 and result.scenario_id in forecast.spec.scenario_ids
        assert result.metrics[2].value is None and result.metrics[2].missing_reasons == ("actual_basis_missing",)
        assert result.forecast_id == forecast.forecast_id and result.revision_parent_ref is None
    with db.session() as session:
        assert read_event_realization_in_session(session, result.realization_id,
            archive=archive, cutoff_at=cutoff) == result
        assert read_latest_event_realization_for_forecast_in_session(session, forecast.forecast_id,
            archive=archive, cutoff_at=known - timedelta(microseconds=1)) is None
        duplicate_known = cutoff + timedelta(seconds=10)
        admit_bls_fixture(session, archive, body=engineering_actual_body(), period="2026-10", known_at=duplicate_known)
    with db.session() as session:
        repeated = freeze_event_realization_in_session(session, forecast.forecast_id, archive=archive,
            cutoff_at=duplicate_known + timedelta(seconds=2), recorded_at=duplicate_known + timedelta(seconds=2))
        assert repeated == result and repeated.known_at == known
        assert session.scalar(select(func.count()).select_from(EventRealizationRecord)) == 1
        corrected_known = duplicate_known + timedelta(seconds=10)
        admit_bls_fixture(session, archive, body=engineering_actual_body(core_factor="1.008"),
            period="2026-10", known_at=corrected_known)
    with db.session() as session:
        corrected = freeze_event_realization_in_session(session, forecast.forecast_id, archive=archive,
            cutoff_at=corrected_known + timedelta(seconds=2), recorded_at=corrected_known + timedelta(seconds=2))
        assert corrected.revision_parent_ref == result.realization_id
        assert corrected.scenario_id != result.scenario_id and corrected.known_at == corrected_known
        assert session.scalar(select(func.count()).select_from(EventRealizationRecord)) == 2
        assert session.get(EventScenarioForecastRecord, forecast.forecast_id).payload_json == before
        assert read_event_realization_in_session(session, result.realization_id,
            archive=archive, cutoff_at=corrected.recorded_at) == result
        assert read_event_realization_in_session(session, corrected.realization_id,
            archive=archive, cutoff_at=corrected.recorded_at) == corrected
        assert session.scalar(select(func.count()).select_from(RunRecord)) == 0
    db.engine.dispose()

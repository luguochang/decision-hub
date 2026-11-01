"""One isolated native main path; future actuals/prices are engineering fixtures."""

import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import func, select

from packages.kernel.decision_hub_kernel.application import event_outcome as outcome
from packages.kernel.decision_hub_kernel.application import event_owned_opportunity as opportunity
from packages.kernel.decision_hub_kernel.application.event_realization import (
    freeze_event_realization_in_session,
)
from packages.kernel.decision_hub_kernel.application.event_scenario_autoproduction import (
    read_event_owned_scenario_in_session,
)
from packages.kernel.decision_hub_kernel.persistence.db import Database, RawCaptureRecord, RunRecord
from packages.kernel.decision_hub_kernel.persistence.event_outcome_records import EventOutcomeRecord
from packages.kernel.decision_hub_kernel.persistence.event_scenario_records import (
    EventScenarioForecastRecord,
)
from packages.kernel.decision_hub_kernel.persistence.risk_records import MarketCandleRecord
from packages.kernel.decision_hub_kernel.ports.market_path import (
    MarketCandle,
    canonical_hash,
    spot_scope,
)
from packages.provider_adapters.research.raw_capture import RawBodyArchive

from .test_event_opportunity_monitor import _engineered_opportunity, source_guard  # noqa: F401
from .test_event_realization import admit_bls_fixture, engineering_actual_body

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "artifacts/decision-product-v1/normal15/core-delivery-02"


class FixtureArchive:
    def __init__(self, path):
        self.local = RawBodyArchive(path)
        macro = Path(
            json.loads((CORE / "macro-live-20261015T070508.json").read_text("utf-8"))[
                "private_root"
            ]
        )
        self.roots = (
            macro / "raw-captures",
            ROOT
            / "artifacts/decision-product-v1/normal14/event-causal-opportunity-01"
            / "private/raw-captures",
            ROOT
            / "artifacts/decision-product-v1/normal14/event-scenario-probability-01"
            / "private/raw-captures",
            ROOT / "artifacts/decision-product-v1/normal14/cpi-pre-event-01/private/raw-captures",
            ROOT / "data/core-live-20260913/raw-captures",
        )

    def read(self, ref):
        for archive in (self.local, *(RawBodyArchive(root) for root in self.roots)):
            try:
                return archive.read(ref)
            except FileNotFoundError:
                pass
        raise FileNotFoundError(ref)

    def put(self, body):
        return self.local.put(body)


def _admit_path(session, archive, instrument, release, received):
    scope = spot_scope(instrument)
    first = release - timedelta(minutes=20)
    end = release + timedelta(hours=24, seconds=60)
    count = int((end - first).total_seconds() / 60)
    records = []
    price = Decimal(1000)
    for offset in range(count):
        at = first + timedelta(minutes=offset)
        minute = int((at - release).total_seconds() / 60)
        close = (
            Decimal(1000)
            if minute < 0
            else Decimal(1000 + minute)
            if minute <= 120
            else Decimal(1120 - (minute - 120) * 3)
            if minute <= 240
            else Decimal(760) + Decimal(".02") * (minute - 240)
        )
        volume = Decimal(100 if minute < 0 else 400)
        records.append(
            [
                str(int(at.timestamp() * 1000)),
                str(price),
                str(max(price, close)),
                str(min(price, close)),
                str(close),
                str(volume),
                "0",
                str(volume * close),
                "1",
            ]
        )
        price = close
    for page in range(0, len(records), 300):
        raw_rows = records[page : page + 300]
        raw = json.dumps({"code": "0", "msg": "", "data": raw_rows}, separators=(",", ":")).encode()
        digest, ref = archive.put(raw)
        after = int(raw_rows[-1][0]) + 60000
        session.add(
            RawCaptureRecord(
                capture_id=f"engineering-path:{instrument}:{page}",
                context_id="engineering-future-outcome-path",
                url=f"https://www.okx.com/api/v5/market/history-candles?instId={instrument}&bar=1m&after={after}&limit=300",
                status_code=200,
                body_hash=digest,
                body_ref=ref,
                byte_count=len(raw),
                media_type="application/json",
                adapter_version="engineering-future-path.v1",
                started_at=received - timedelta(seconds=1),
                completed_at=received,
                recorded_at=received,
            )
        )
        for index, row in enumerate(raw_rows):
            at = datetime.fromtimestamp(int(row[0]) / 1000, UTC)
            bar = MarketCandle(
                scope_id=scope.scope_id,
                venue="okx",
                instrument=instrument,
                open_at=at,
                close_at=at + timedelta(minutes=1),
                open=row[1],
                high=row[2],
                low=row[3],
                close=row[4],
                volume_base=row[5],
                volume_quote=row[7],
                received_at=received,
                accepted_at=received,
                capture_ref=ref,
                capture_hash=digest,
                row_index=index,
            )
            revision = canonical_hash(bar.semantic_payload())
            session.add(
                MarketCandleRecord(
                    candle_id="candle_" + canonical_hash([bar.scope_id, revision])[:48],
                    scope_id=bar.scope_id,
                    venue=bar.venue,
                    instrument=bar.instrument,
                    price_type=bar.price_type,
                    interval_seconds=60,
                    open_at=bar.open_at,
                    close_at=bar.close_at,
                    received_at=received,
                    known_at=received,
                    revision_hash=revision,
                    candle_json=bar.model_dump_json(),
                    capture_ref=ref,
                    capture_hash=digest,
                )
            )
    session.flush()


@pytest.mark.usefixtures("source_guard")
def test_native_event_score_waiting_missing_then_complete_immutable_paper_outcome(tmp_path):
    retained = (
        Path(
            json.loads((CORE / "integration-v2-20261015T072842.json").read_text("utf-8"))[
                "private_root"
            ]
        )
        / "integration.sqlite3"
    )
    path = tmp_path / "outcome.sqlite3"
    source_hash = hashlib.sha256(retained.read_bytes()).hexdigest()
    with (
        sqlite3.connect(retained.as_uri() + "?mode=ro", uri=True) as old,
        sqlite3.connect(path) as new,
    ):
        old.backup(new)
    db = Database("sqlite:///" + path.as_posix())
    db.create_all()
    archive = FixtureArchive(tmp_path / "engineering-raw")
    with db.session() as session:
        fid = session.scalar(select(EventScenarioForecastRecord.forecast_id))
        forecast = read_event_owned_scenario_in_session(
            session, fid, archive=archive, cutoff_at=datetime.now(UTC)
        )
        original_forecast = session.get(EventScenarioForecastRecord, fid).payload_json
        baseline_runs = session.scalar(select(func.count()).select_from(RunRecord))
    pre = forecast.scheduled_at - timedelta(minutes=10)
    # Publish valid near-release plans through their native preparation/commit.
    with patch.object(opportunity, "utcnow", return_value=pre):
        for instrument in ("BTC-USDT", "ETH-USDT"):
            with db.session() as session:
                prepared = opportunity.prepare_event_owned_opportunity_in_session(
                    session, fid, instrument=instrument, archive=archive
                )
            with db.session() as session:
                opportunity.commit_prepared_event_owned_opportunity_in_session(
                    session, prepared, archive=archive
                )
    known = forecast.scheduled_at + timedelta(minutes=5)
    with db.session() as session:
        admit_bls_fixture(
            session,
            archive,
            body=engineering_actual_body(),
            period=forecast.reference_period,
            known_at=known,
        )
        realization = freeze_event_realization_in_session(
            session,
            fid,
            cutoff_at=known + timedelta(seconds=2),
            recorded_at=known + timedelta(seconds=2),
            archive=archive,
        )
        assert realization is not None
    now = [known + timedelta(seconds=3)]
    service = outcome.EventOutcomeService(db, archive=archive, clock=lambda: now[0])
    waiting = service.process_forecast(fid)
    assert waiting is not None and waiting.event_score.label == realization.scenario_id
    assert waiting.event_score.brier >= 0 and all(
        a.state == "waiting_horizon" for a in waiting.assets
    )
    assert (
        outcome.event_outcome_user_view(
            waiting, scheduled_at=forecast.scheduled_at, cutoff_at=now[0]
        )["state"]
        == "partial"
    )
    waiting_bytes = waiting.model_dump_json()
    now[0] = forecast.scheduled_at + timedelta(hours=24, seconds=120)
    missing = service.process_forecast(fid)
    assert missing is not None and all(
        a.state == "missing_path" and a.return_24h is None for a in missing.assets
    )
    with db.session() as session:
        for instrument in ("BTC-USDT", "ETH-USDT"):
            _admit_path(
                session, archive, instrument, forecast.scheduled_at, now[0] - timedelta(seconds=1)
            )
    now[0] += timedelta(seconds=1)
    complete = service.process_forecast(fid)
    assert complete is not None and all(a.state == "scored" for a in complete.assets)
    assert all(a.strategy_entered and a.strategy_entry_at >= known for a in complete.assets)
    assert all(
        a.strategy_net_return is not None and a.held_long_reduce_at >= known
        for a in complete.assets
    )
    assert complete.revision_parent_ref == missing.outcome_id
    assert (
        outcome.event_outcome_user_view(
            complete, scheduled_at=forecast.scheduled_at, cutoff_at=now[0]
        )["state"]
        == "complete"
    )
    assert service.process_forecast(fid) == complete
    assert service.process_due() == 0
    with db.session() as session:
        assert (
            outcome.read_event_outcome_in_session(
                session, waiting.outcome_id, cutoff_at=now[0], archive=archive
            ).model_dump_json()
            == waiting_bytes
        )
        assert (
            outcome.read_latest_event_outcome_for_forecast_in_session(
                session, fid, cutoff_at=now[0], archive=archive
            )
            == complete
        )
        assert session.scalar(select(func.count()).select_from(EventOutcomeRecord)) == 3
        row = session.get(EventOutcomeRecord, complete.outcome_id)
        row.payload_hash = "0" * 64
        session.flush()
        with pytest.raises(ValueError, match="native_original_invalid"):
            outcome.read_event_outcome_in_session(
                session, complete.outcome_id, cutoff_at=now[0], archive=archive
            )
        session.rollback()
        assert session.get(EventScenarioForecastRecord, fid).payload_json == original_forecast
        assert session.scalar(select(func.count()).select_from(RunRecord)) == baseline_runs
    assert hashlib.sha256(retained.read_bytes()).hexdigest() == source_hash
    db.engine.dispose()


def test_announcement_outcome_needs_only_twenty_pre_event_minutes(tmp_path):
    """Native path admission and shared replay need no rolling 21-hour history."""
    db = Database("sqlite:///" + (tmp_path / "twenty-minute.sqlite3").as_posix())
    db.create_all()
    archive = RawBodyArchive(tmp_path / "raw")
    release = datetime(2026, 11, 10, 13, 30, tzinfo=UTC)
    cutoff = release + timedelta(hours=24, minutes=2)
    with db.session() as session:
        _admit_path(session, archive, "BTC-USDT", release, cutoff - timedelta(seconds=1))
    forecast = SimpleNamespace(forecast_id="engineering-minimal-path", scheduled_at=release)
    realization = SimpleNamespace(known_at=release + timedelta(minutes=5))
    with db.session() as session:
        asset = outcome._asset(
            session, forecast, realization, "BTC-USDT", archive=archive, cutoff=cutoff
        )
        # No native pre-event plan was supplied by this finite path control;
        # its correctly diagnosed absence must not become a false path gap.
        assert asset.state == "missing_plan" and asset.return_24h is not None
        bars = outcome.MarketPathStore(None, archive).load_in_session(
            session,
            spot_scope("BTC-USDT"),
            start_at=release - timedelta(minutes=20),
            end_at=release + timedelta(hours=24, seconds=60),
            as_of=cutoff,
        )
    assert len(bars) == 1461 and bars[0].open_at == release - timedelta(minutes=20)
    replay = outcome._replay(
        _engineered_opportunity().plans[0].policy,
        bars,
        scheduled_at=release,
        activation_known_at=realization.known_at,
    )
    assert replay["strategy_entered"]
    assert replay["strategy_entry_at"] >= realization.known_at
    assert replay["held_long_reduce_at"] > replay["strategy_entry_at"]
    db.engine.dispose()

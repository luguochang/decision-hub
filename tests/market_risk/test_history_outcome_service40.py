"""Private service controls; model qualification is explicitly synthetic.

Real private SQLite, archive SHA, independent connections and sealed batch/raw
readers are exercised. The fake cold model issuer confers no financial proof.
"""

from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from packages.contracts_py.decision_hub_contracts.generated.history_outcome_v2 import (
    HistoryOutcomeStatusV2,
)
from packages.kernel.decision_hub_kernel.application import history_outcome_v2 as service_module
from packages.kernel.decision_hub_kernel.application.history_forecast_dispatch39 import (
    HistoryForecastDispatch39Service,
)
from packages.kernel.decision_hub_kernel.application.history_forecast_write39 import (
    HistoryForecastWrite39Service,
)
from packages.kernel.decision_hub_kernel.application.history_observed_dataset import encoded
from packages.kernel.decision_hub_kernel.persistence.db import Base, RawCaptureRecord
from packages.kernel.decision_hub_kernel.persistence.history_dataset_model_v2_records import (
    HistoryDatasetModelV2Record,
)
from packages.kernel.decision_hub_kernel.persistence.history_hourly_v2_records import (
    HistoryHourlyV2SlotRecord,
)
from packages.kernel.decision_hub_kernel.persistence.history_outcome_v2_records import (
    HistoryOutcomeObservationV2Record,
    HistoryOutcomeOpportunityV2Record,
    HistoryOutcomeProtocolV2Record,
    HistoryOutcomePublicationV2Record,
    HistoryOutcomeScoreV2Record,
)
from packages.kernel.decision_hub_kernel.persistence.risk_records import MarketCandleRecord
from packages.kernel.decision_hub_kernel.ports.market_path import (
    MarketCandle,
    canonical_hash,
    spot_scope,
)
from tests.market_risk.test_history_outcome_forecasts_v2 import setup


def fixture40(tmp_path, monkeypatch):
    env = setup(tmp_path, monkeypatch, count=4, second_model=True)
    Base.metadata.create_all(
        env.env.db.engine,
        tables=[
            cls.__table__
            for cls in (
                HistoryOutcomeProtocolV2Record,
                HistoryOutcomeOpportunityV2Record,
                HistoryOutcomePublicationV2Record,
                HistoryOutcomeObservationV2Record,
                HistoryOutcomeScoreV2Record,
                HistoryHourlyV2SlotRecord,
            )
        ],
    )
    clock = [env.env.cutoff - timedelta(minutes=5)]
    env.env.originals.clock = lambda: clock[0]
    env.env.service = HistoryForecastWrite39Service(
        env.env.models, owner_claim=env.dispatch.owner_claim, grants=dict(env.dispatch.grants)
    )
    env.dispatch = HistoryForecastDispatch39Service(env.env.service)
    physical_reads = []

    class ExplicitSyntheticModelIssuer:
        def __init__(self, writer):
            self.writer = writer

        def issue(self, session, identifier):
            physical_reads.append(id(session.connection().connection.driver_connection))
            value, original_ref, original_hash = env.registry[identifier]
            row = session.get(HistoryDatasetModelV2Record, identifier)
            body = env.env.models.archive.read(original_ref, original_hash)
            assert body == encoded(value.model_dump(mode="json"))
            assert row.payload_hash == value.payload_hash
            return SimpleNamespace(model_bytes=body), value

    # Only the new Outcome40 boundary is replaced, never sealed39 methods.
    monkeypatch.setattr(service_module, "TransferAuthority39", ExplicitSyntheticModelIssuer)
    service = service_module.HistoryOutcomeV2Service(
        env.dispatch, hourly=SimpleNamespace(_read_slot=lambda _session, _identifier: None)
    )
    return SimpleNamespace(
        service=service, env=env, db=env.env.db, clock=clock, physical_reads=physical_reads
    )


def test_actual_registration_48_postsave_confirmation_and_strict_fast_status(tmp_path, monkeypatch):
    env = fixture40(tmp_path, monkeypatch)
    protocol = env.service.register("finite-registration")
    assert len(env.physical_reads) == 4
    assert env.physical_reads[0] != env.physical_reads[2]  # NullPool independent readback
    with env.service.commands._read_session() as session:
        rows = session.scalars(select(HistoryOutcomeOpportunityV2Record)).all()
        assert len(rows) == 48
        stored = session.get(HistoryOutcomeProtocolV2Record, protocol.protocol_id)
        assert stored.confirmation_id is not None
        assert stored.confirmed_at is not None
        status = HistoryOutcomeStatusV2.model_validate_json(
            encoded(env.service.fast_status(session, protocol.protocol_id, offset=27, limit=30))
        )
        assert len(status.opportunities) == 21
        assert status.expected_total == 48 and status.paired_count == 0
        assert not status.current_originals_verified and not status.financial_qualified
    assert env.service.register("finite-registration") == protocol
    assert len(env.physical_reads) == 4  # existing first-save read is immutable


def test_unknown_commit_has_one_attempt_and_durable_fence_no_replay(tmp_path, monkeypatch):
    env = fixture40(tmp_path, monkeypatch)
    original = env.service.commands._write_session
    calls = []

    @contextmanager
    def commit_then_transport_unknown():
        calls.append(1)
        with original() as session:
            yield session
        raise OSError("synthetic post-COMMIT ACK loss")

    monkeypatch.setattr(env.service.commands, "_write_session", commit_then_transport_unknown)
    with pytest.raises(service_module.HistoryOutcomeCommitUnconfirmed):
        env.service.register("unknown-registration")
    fences = tuple((env.service.archive.root / "fences").glob("*.json"))
    assert len(fences) == 1 and calls == [1]
    with env.service.commands._read_session() as session:
        rows = session.scalars(select(HistoryOutcomeProtocolV2Record)).all()
        assert len(rows) == 1 and rows[0].confirmation_id is None


def test_source_archive_and_forged_preparation_rejected(tmp_path, monkeypatch):
    env = fixture40(tmp_path, monkeypatch)
    protocol = env.service.register("source-control")
    with env.service.commands._read_session() as session:
        value, _reference, row = env.service._read(session, "protocol", protocol.protocol_id)
        packet = env.service._prepare(session, (("protocol", value),))
        forged = replace(packet, entries=())
        with pytest.raises(ValueError, match="issued_preparation"):
            env.service._auth(session, forged)
        original = env.service.archive.read(row.original_ref, row.original_hash)
    from pathlib import Path

    Path(row.original_ref).write_bytes(original + b" ")
    with env.service.commands._read_session() as session:
        with pytest.raises(ValueError, match="hash_changed"):
            env.service._read(session, "protocol", protocol.protocol_id)


def test_missing_publication_stays_expected_and_saved_score_is_readable(tmp_path, monkeypatch):
    env = fixture40(tmp_path, monkeypatch)
    protocol = env.service.register("missing-census")
    env.clock[0] = protocol.first_hour + timedelta(minutes=6)
    with env.service.commands._read_session() as session:
        row = session.scalar(
            select(HistoryOutcomeOpportunityV2Record).order_by(
                HistoryOutcomeOpportunityV2Record.hour_at,
                HistoryOutcomeOpportunityV2Record.scope_id,
            )
        )
        packet = env.service.prepare_publication(session, row.opportunity_id)
    env.service.save(packet)
    with env.service.commands._read_session() as session:
        prepared = env.service.prepare_score(session, protocol.protocol_id, "score-missing")
    saved = env.service.save(prepared)[0]
    with env.service.commands._read_session() as session:
        reread = env.service.read_score(session, saved.score_id)
        assert reread == saved
        assert reread.expected_total == 48 and reread.paired_count == 0
        assert reread.publication_counts["missing"] == 1
        assert reread.publication_counts["unconfirmed"] == 1
        assert reread.targets[0].baseline_brier is None


def test_full_read_budget_is_finite_no_archive_write(tmp_path, monkeypatch):
    env = fixture40(tmp_path, monkeypatch)
    protocol = env.service.register("finite-read")
    calls = []

    def denied_put(_value):
        calls.append("archive-write")
        raise AssertionError("GET cannot write originals")

    monkeypatch.setattr(env.service.archive, "put", denied_put)
    with env.service.commands._read_session() as session:
        with pytest.raises(ValueError, match="maximum_120"):
            env.service.read_score(session, "nonexistent", budget_seconds=121)
        with pytest.raises(KeyError):
            env.service.read_score(session, "nonexistent", budget_seconds=120)
        assert env.service.fast_status(session, protocol.protocol_id)["expected_total"] == 48
    assert calls == []


def insert_exact_1440(env, opportunity, cutoff):
    scope = spot_scope(opportunity.scope_id.split(":")[3])
    clock = env.clock[0]
    times = tuple(cutoff + timedelta(minutes=index + 1) for index in range(1440))
    with env.db.engine.begin() as connection:
        for offset in range(0, len(times), 300):
            stamps = times[offset : offset + 300]
            raw = encoded(
                {
                    "code": "0",
                    "data": [
                        [
                            str(int(instant.timestamp() * 1000)),
                            "100",
                            "103",
                            "97",
                            "100",
                            "1",
                            "100",
                            "100",
                            "1",
                        ]
                        for instant in stamps
                    ],
                }
            )
            digest, reference = env.service.originals.raw.put(raw)
            connection.execute(
                RawCaptureRecord.__table__.insert(),
                dict(
                    capture_id="label-capture-" + str(offset),
                    context_id="private-label-1440",
                    url="https://www.okx.com/api/v5/market/history-candles?instId="
                    + scope.instrument
                    + "&bar=1m&limit=300&after="
                    + str(int(clock.timestamp() * 1000)),
                    status_code=200,
                    body_ref=reference,
                    body_hash=digest,
                    byte_count=len(raw),
                    media_type="application/json",
                    adapter_version="synthetic-mechanical",
                    error_code=None,
                    started_at=clock - timedelta(seconds=4),
                    completed_at=clock - timedelta(seconds=3),
                    recorded_at=clock - timedelta(seconds=1),
                ),
            )
            for index, instant in enumerate(stamps):
                bar = MarketCandle(
                    scope_id=scope.scope_id,
                    venue=scope.venue,
                    instrument=scope.instrument,
                    open_at=instant,
                    close_at=instant + timedelta(minutes=1),
                    open=Decimal(100),
                    high=Decimal(103),
                    low=Decimal(97),
                    close=Decimal(100),
                    volume_base=Decimal(1),
                    volume_quote=Decimal(100),
                    received_at=clock - timedelta(seconds=3),
                    accepted_at=clock - timedelta(seconds=1),
                    capture_ref=reference,
                    capture_hash=digest,
                    row_index=index,
                )
                revision = canonical_hash(bar.semantic_payload())
                connection.execute(
                    MarketCandleRecord.__table__.insert(),
                    dict(
                        candle_id="candle_" + canonical_hash([bar.scope_id, revision])[:48],
                        scope_id=bar.scope_id,
                        venue=bar.venue,
                        instrument=bar.instrument,
                        price_type=bar.price_type,
                        interval_seconds=60,
                        open_at=bar.open_at,
                        close_at=bar.close_at,
                        received_at=bar.received_at,
                        known_at=bar.known_at,
                        revision_hash=revision,
                        candle_json=bar.model_dump_json(),
                        capture_ref=bar.capture_ref,
                        capture_hash=bar.capture_hash,
                    ),
                )


@pytest.mark.parametrize("confirmation_is_late", [False, True])
def test_exact_label_actual_S_and_whole_missing_denominator(
    tmp_path, monkeypatch, confirmation_is_late
):
    env = fixture40(tmp_path, monkeypatch)
    protocol = env.service.register("complete-label")
    env.clock[0] = protocol.first_hour + timedelta(minutes=6)
    with env.service.commands._read_session() as session:
        opportunity = session.scalar(
            select(HistoryOutcomeOpportunityV2Record).order_by(
                HistoryOutcomeOpportunityV2Record.hour_at,
                HistoryOutcomeOpportunityV2Record.scope_id,
            )
        )
        opportunity_id = opportunity.opportunity_id
        publication = env.service.prepare_publication(session, opportunity_id)
    value = env.service.save(publication)[0]
    env.clock[0] = value.label_cutoff_at + timedelta(days=1, minutes=3)
    insert_exact_1440(env, opportunity, value.label_cutoff_at)
    with env.db.engine.begin() as connection:
        connection.execute(
            HistoryOutcomeOpportunityV2Record.__table__.update()
            .where(HistoryOutcomeOpportunityV2Record.opportunity_id == opportunity_id)
            .values(attempts=1)
        )
    with env.service.commands._read_session() as session:
        prepared = env.service.prepare_observation(session, opportunity_id, attempt=1)
    subject = env.service.save(prepared)[0]
    assert subject.observed_count == 1440 and subject.accepted_at is None
    assert subject.mae_long == Decimal("0.03") and subject.mae_short == Decimal("0.03")
    if confirmation_is_late:
        env.clock[0] = subject.label_deadline_at + timedelta(seconds=1)
    confirmation = env.service.confirm("observation", subject.observation_id)
    assert confirmation.independently_verified_at == env.clock[0]
    with env.service.commands._read_session() as session:
        assert env.service.read_observation(session, subject.observation_id) == subject
        packet = env.service.prepare_score(session, protocol.protocol_id, "score-label")
    score = env.service.save(packet)[0]
    with env.service.commands._read_session() as session:
        assert env.service.read_score(session, score.score_id) == score
    assert score.expected_total == 48 and score.paired_count == 0  # missing forecast stays missing
    assert score.verified_label_count == (0 if confirmation_is_late else 1)
    assert score.label_counts["late_diagnostic"] == int(confirmation_is_late)
    assert score.ci is None and not score.financial_qualified

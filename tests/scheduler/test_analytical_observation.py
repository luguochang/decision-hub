import asyncio
from datetime import timedelta

import pytest

from packages.kernel.decision_hub_kernel.application.analytical_observation import (
    AnalyticalObservationService,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    AnalyticalObservationAttemptRecord as Attempt,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    AnalyticalObservationRecord as Observation,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    AnalyticalOutlookRecord as Outlook,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    as_utc,
)
from packages.kernel.decision_hub_kernel.ports.sources import MarketQuote, PriceWindow
from tests.research.test_analytical_outlooks import NOW, prepared, publish


class Market:
    def __init__(self, clock, *, issue=None):
        self.clock, self.issue, self.calls = clock, issue, 0

    async def window(self, instrument, start, end):
        self.calls += 1
        if self.issue == "failure":
            raise ConnectionError("secret provider details must not enter the error code")
        if self.issue == "cancel":
            raise asyncio.CancelledError()
        if self.issue == "missing":
            return None

        def quote(price, boundary):
            return MarketQuote(
                instrument="GOLD" if self.issue == "instrument" else instrument,
                observed_at=boundary - timedelta(minutes=2) if self.issue == "time" else boundary,
                received_at=self.clock(),
                last=price,
                source_id="fixture",
                quality_status="estimated",
                benchmark="close_1m",
            )

        return PriceWindow(
            entry=quote(100, start), exit=quote(102, end), quality_status="estimated"
        )


async def test_unopened_market_never_calls_the_btc_observation_provider(tmp_path, monkeypatch):
    db, result = prepared(tmp_path, monkeypatch)
    publish(db, result)
    with db.session() as session:
        outlook = session.query(Outlook).filter_by(horizon="30m").one()
        outlook.market, outlook.instrument = "metal", "XAU-USD"
    def clock():
        return NOW + timedelta(minutes=31)
    market = Market(clock)
    await AnalyticalObservationService(db, market, clock=clock).process_due()
    assert market.calls == 0
    with db.session() as session:
        observation = session.query(Observation).filter_by(status="retry").one()
        assert observation.error_code == "market_scope_not_enabled"
        assert observation.direction_correct is None


@pytest.mark.parametrize(
    "action,signed,correct", [("long", 2, 1), ("short", -2, 0), ("neutral", None, None)]
)
async def test_due_direction_neutral_and_idempotence(
    tmp_path, monkeypatch, action, signed, correct
):
    db, result = prepared(tmp_path, monkeypatch, action=action)
    publish(db, result)

    def clock():
        return NOW + timedelta(minutes=31)

    market = Market(clock)
    service = AnalyticalObservationService(db, market, clock=clock)
    assert await service.process_due(now=NOW) == 0
    assert await service.process_due() == 1
    assert await service.process_due() == 0
    with db.session() as session:
        row = session.query(Observation).filter_by(status="completed").one()
        assert row.market_return_pct == pytest.approx(2)
        assert row.directional_return_pct == (pytest.approx(signed) if signed is not None else None)
        assert row.direction_correct == correct
        assert row.quality_status == "estimated" and row.benchmark == "close_1m"
        assert session.query(Attempt).one().status == "completed"
        assert session.query(Observation).filter_by(status="pending").count() == 2


@pytest.mark.parametrize(
    "issue,code",
    [
        ("failure", "market_provider_ConnectionError"),
        ("missing", "market_window_missing"),
        ("time", "market_timestamp_misaligned"),
        ("instrument", "market_instrument_mismatch"),
    ],
)
async def test_failure_is_durable_backoff_then_recovers(tmp_path, monkeypatch, issue, code):
    db, result = prepared(tmp_path, monkeypatch)
    publish(db, result)
    instant = [NOW + timedelta(minutes=31)]

    def clock():
        return instant[0]

    market = Market(clock, issue=issue)
    service = AnalyticalObservationService(db, market, clock=clock)
    assert await service.process_due() == 0
    assert await service.process_due() == 0
    assert market.calls == 1
    with db.session() as session:
        row = session.query(Observation).filter_by(status="retry").one()
        assert row.error_code == code
        assert as_utc(row.next_attempt_at) == clock() + timedelta(minutes=1)
        assert row.market_return_pct is None
    instant[0] += timedelta(minutes=1)
    market.issue = None
    assert await service.process_due() == 1
    with db.session() as session:
        attempts = session.query(Attempt).order_by(Attempt.attempt).all()
        assert [a.status for a in attempts] == ["failed", "completed"]
        assert attempts[0].error_code == code


async def test_expired_lease_recovery_fences_late_writer(tmp_path, monkeypatch):
    db, result = prepared(tmp_path, monkeypatch)
    publish(db, result)
    instant = [NOW + timedelta(minutes=31)]

    def clock():
        return instant[0]

    service = AnalyticalObservationService(db, Market(clock), clock=clock)
    with db.session() as session:
        outlook = session.query(Outlook).filter_by(horizon="30m").one()
    old = service.claim(outlook.outlook_id, clock())
    assert service.claim(outlook.outlook_id, clock()) is None
    instant[0] += timedelta(minutes=3)
    service.recover_expired(clock())
    new = service.claim(outlook.outlook_id, clock())
    assert new and new != old
    assert service.finish(outlook.outlook_id, *old, error="late") is False
    assert service.finish(outlook.outlook_id, *new, error="temporary") is True
    with db.session() as session:
        attempts = session.query(Attempt).order_by(Attempt.attempt).all()
        assert [a.status for a in attempts] == ["interrupted", "failed"]


async def test_cancelled_attempt_does_not_stay_running(tmp_path, monkeypatch):
    db, result = prepared(tmp_path, monkeypatch)
    publish(db, result)

    def clock():
        return NOW + timedelta(minutes=31)

    service = AnalyticalObservationService(db, Market(clock, issue="cancel"), clock=clock)
    with pytest.raises(asyncio.CancelledError):
        await service.process_due()
    with db.session() as session:
        assert session.query(Observation).filter_by(status="running").count() == 0
        assert session.query(Attempt).one().error_code == "observation_cancelled"

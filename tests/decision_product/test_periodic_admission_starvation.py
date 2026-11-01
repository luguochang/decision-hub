"""Periodic admission must make durable queue progress before heavy background work."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from apps.hub_worker.composition import build_research_admission_tick
from packages.kernel.decision_hub_kernel.application.risk_research_admission import (
    RiskResearchAdmissionService,
)
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.kernel.decision_hub_kernel.persistence.risk_research_records import (
    RiskResearchRequestRecord,
)

AT = datetime(2026, 10, 20, 22, 1, 26, tzinfo=UTC)
PROTOCOL = "test-perpetual-periodic.v1"


class _PeriodicAdmission:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.calls = 0

    async def process_due(self, *, limit: int) -> int:
        assert limit == 1
        self.calls += 1
        self.events.append("periodic-admission")
        return 1


class _BlockingForecast:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.started = asyncio.Event()

    async def tick(self) -> int:
        self.events.append("forecast")
        self.started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


class _BlockingPeriodicAdmission:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.started = asyncio.Event()

    async def process_due(self, *, limit: int) -> int:
        assert limit == 1
        self.events.append("periodic-admission")
        self.started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


class _ImmediateForecast:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.calls = 0

    async def tick(self) -> int:
        self.calls += 1
        self.events.append("forecast")
        return 1


class _FailingPeriodicAdmission:
    async def process_due(self, *, limit: int) -> int:
        assert limit == 1
        raise RuntimeError("periodic-producer-failed")


class _CompletingForecast:
    def __init__(self) -> None:
        self.started = False
        self.completed = False

    async def tick(self) -> int:
        self.started = True
        await asyncio.sleep(0.05)
        self.completed = True
        return 1


class _PerpetualDriver:
    protocol_version = PROTOCOL
    supported_protocols = (PROTOCOL,)

    def __init__(self, database: Database, *, discover_creates: bool) -> None:
        self.database = database
        self.discover_creates = discover_creates
        self.discover_calls = 0
        self.prepared: list[tuple[str, int, str]] = []
        self.admitted: list[tuple[str, int]] = []

    def discover(self) -> None:
        self.discover_calls += 1
        if not self.discover_creates:
            assert self.admitted, "heavy event-motive discovery ran before durable backlog"
            return
        with self.database.session() as session:
            session.add(_request("discovered-request"))

    def claimable_in_session(self, session, row, at) -> bool:
        del session, at
        return row.protocol_version == PROTOCOL

    async def prepare(self, key: str, generation: int, owner: str) -> object:
        self.prepared.append((key, generation, owner))
        return {"request_id": key}

    def admit(self, key: str, generation: int, service, prepared: object) -> bool:
        del service
        assert prepared == {"request_id": key}
        self.admitted.append((key, generation))
        return True


def _database(tmp_path, name: str) -> Database:
    database = Database(f"sqlite+pysqlite:///{(tmp_path / name).as_posix()}")
    database.create_all()
    return database


def _request(key: str) -> RiskResearchRequestRecord:
    return RiskResearchRequestRecord(
        request_id=key,
        protocol_version=PROTOCOL,
        scope_id="crypto:perp:okx:ETH-USDT-SWAP:trade:1m",
        instrument="ETH-USDT-SWAP",
        status="pending_admission",
        created_at=AT,
        updated_at=AT,
        available_at=AT,
        attempts=0,
        lease_generation=0,
    )


@pytest.mark.asyncio
async def test_normal_tick_drains_periodic_admission_before_blocking_forecast() -> None:
    events: list[str] = []
    periodic = _PeriodicAdmission(events)
    forecast = _BlockingForecast(events)
    tick = build_research_admission_tick(
        perpetual_forecasts=forecast,
        periodic_admission=periodic,
        previous_research_tick=None,
    )

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(tick(), timeout=0.05)

    assert forecast.started.is_set()
    assert periodic.calls == 1
    assert sorted(events) == ["forecast", "periodic-admission"]


@pytest.mark.asyncio
async def test_blocking_periodic_discovery_does_not_starve_forecast() -> None:
    events: list[str] = []
    periodic = _BlockingPeriodicAdmission(events)
    forecast = _ImmediateForecast(events)
    tick = build_research_admission_tick(
        perpetual_forecasts=forecast,
        periodic_admission=periodic,
        previous_research_tick=None,
    )

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(tick(), timeout=0.05)

    assert periodic.started.is_set()
    assert forecast.calls == 1
    assert sorted(events) == ["forecast", "periodic-admission"]


@pytest.mark.asyncio
async def test_one_producer_error_does_not_cancel_other_valid_producer() -> None:
    forecast = _CompletingForecast()
    tick = build_research_admission_tick(
        perpetual_forecasts=forecast,
        periodic_admission=_FailingPeriodicAdmission(),
        previous_research_tick=None,
    )

    with pytest.raises(RuntimeError, match="periodic-producer-failed"):
        await tick()

    assert forecast.started is True
    assert forecast.completed is True


@pytest.mark.asyncio
async def test_existing_periodic_request_is_claimed_before_heavy_discovery(tmp_path) -> None:
    database = _database(tmp_path, "existing.sqlite3")
    with database.session() as session:
        session.add(_request("existing-request"))
    driver = _PerpetualDriver(database, discover_creates=False)
    service = RiskResearchAdmissionService(
        database,
        enabled=True,
        clock=lambda: AT,
        owner_id="risk-research:" + "a" * 32,
        perpetual_driver=driver,
    )

    assert await service.process_due(limit=1) == 1
    assert driver.discover_calls == 1
    assert driver.prepared[0][:2] == ("existing-request", 1)
    assert driver.admitted == [("existing-request", 1)]
    with database.session() as session:
        row = session.get(RiskResearchRequestRecord, "existing-request")
        assert row is not None and row.attempts == 1 and row.lease_generation == 1


@pytest.mark.asyncio
async def test_empty_periodic_queue_discovers_and_claims_in_same_tick(tmp_path) -> None:
    database = _database(tmp_path, "empty.sqlite3")
    driver = _PerpetualDriver(database, discover_creates=True)
    service = RiskResearchAdmissionService(
        database,
        enabled=True,
        clock=lambda: AT,
        owner_id="risk-research:" + "b" * 32,
        perpetual_driver=driver,
    )

    assert await service.process_due(limit=1) == 1
    assert driver.discover_calls == 1
    assert driver.prepared[0][:2] == ("discovered-request", 1)
    assert driver.admitted == [("discovered-request", 1)]

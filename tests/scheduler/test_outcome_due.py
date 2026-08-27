from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from packages.contracts_py.decision_hub_contracts.models import ObservationCreate
from packages.kernel.decision_hub_kernel.application.outcome_due import DueOutcomeService
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    EvaluationRecord,
    ForecastRecord,
    OutcomeRecord,
)
from packages.kernel.decision_hub_kernel.ports.sources import MarketQuote, PriceWindow
from packages.orchestration.langgraph import build_analyze_text_service
from packages.runtime_adapters.fake_runtime.runtime import FakeAgentRuntime


class FixtureWindowProvider:
    def __init__(
        self,
        *,
        entry: float = 101,
        exit: float = 102,
        quality_status: str = "observed",
    ) -> None:
        self.entry = entry
        self.exit = exit
        self.quality_status = quality_status

    async def window(
        self, instrument: str, emitted_at: datetime, expires_at: datetime
    ) -> PriceWindow | None:
        now = datetime.now(UTC)
        return PriceWindow(
            entry=MarketQuote(
                instrument=instrument,
                observed_at=emitted_at,
                received_at=now,
                bid=self.entry - 1,
                ask=self.entry,
                last=self.entry - 0.5,
                source_id="fixture-market",
            ),
            exit=MarketQuote(
                instrument=instrument,
                observed_at=expires_at,
                received_at=now,
                bid=self.exit,
                ask=self.exit + 1,
                last=self.exit + 0.5,
                source_id="fixture-market",
            ),
            quality_status=self.quality_status,
        )


class MissingWindowProvider:
    async def window(
        self, instrument: str, emitted_at: datetime, expires_at: datetime
    ) -> PriceWindow | None:
        return None


def _expired_forecast(database: Database, text: str) -> ForecastRecord:
    service = build_analyze_text_service(database, FakeAgentRuntime())
    asyncio.run(service.submit_and_run(ObservationCreate(text=text)))
    with database.session() as session:
        forecast = session.query(ForecastRecord).order_by(ForecastRecord.horizon.asc()).first()
        assert forecast is not None
        forecast.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        return forecast


def test_due_outcome_is_idempotent(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'due.sqlite3'}")
    database.create_all()
    forecast_id = _expired_forecast(database, "Powell says higher for longer.").forecast_id

    due = DueOutcomeService(database, FixtureWindowProvider())
    first = asyncio.run(due.process_due())
    second = asyncio.run(due.process_due())
    assert first == 1
    assert second == 0
    assert due.evaluation_for(forecast_id) is not None


@pytest.mark.parametrize(
    ("text", "entry", "exit", "expected_return", "expected_correct"),
    [
        ("The committee may cut rates to support growth.", 100, 102, 2.0, True),
        ("Powell says higher for longer.", 100, 102, -2.0, False),
    ],
)
def test_due_outcome_scores_long_and_short_from_executable_prices(
    tmp_path: Path,
    text: str,
    entry: float,
    exit: float,
    expected_return: float,
    expected_correct: bool,
) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'direction.sqlite3'}")
    database.create_all()
    forecast = _expired_forecast(database, text)

    processed = asyncio.run(
        DueOutcomeService(database, FixtureWindowProvider(entry=entry, exit=exit)).process_due()
    )

    assert processed == 1
    with database.session() as session:
        outcome = session.query(OutcomeRecord).filter_by(forecast_id=forecast.forecast_id).one()
        evaluation = (
            session.query(EvaluationRecord).filter_by(forecast_id=forecast.forecast_id).one()
        )
    assert outcome.return_pct == pytest.approx(expected_return)
    assert outcome.direction_correct is expected_correct
    assert evaluation.net_return_pct == pytest.approx(expected_return)


def test_no_trade_and_missing_market_window_do_not_fabricate_outcomes(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'missing.sqlite3'}")
    database.create_all()
    _expired_forecast(database, "The statement is ambiguous.")

    assert asyncio.run(DueOutcomeService(database, FixtureWindowProvider()).process_due()) == 0
    with database.session() as session:
        assert session.query(OutcomeRecord).count() == 0

    _expired_forecast(database, "Powell says higher for longer.")
    assert asyncio.run(DueOutcomeService(database, MissingWindowProvider()).process_due()) == 0
    with database.session() as session:
        assert session.query(OutcomeRecord).count() == 0


def test_estimated_market_window_is_labeled_explicitly(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'estimated.sqlite3'}")
    database.create_all()
    forecast = _expired_forecast(database, "Powell says higher for longer.")

    assert (
        asyncio.run(
            DueOutcomeService(
                database,
                FixtureWindowProvider(quality_status="estimated"),
            ).process_due()
        )
        == 1
    )
    with database.session() as session:
        outcome = session.query(OutcomeRecord).filter_by(forecast_id=forecast.forecast_id).one()
        evaluation = (
            session.query(EvaluationRecord).filter_by(forecast_id=forecast.forecast_id).one()
        )
    assert outcome.quality_status == "estimated"
    assert evaluation.label_status == "estimated"

from __future__ import annotations

from datetime import UTC, datetime

from packages.contracts_py.decision_hub_contracts.models import OutcomeCreate
from packages.kernel.decision_hub_kernel.application.outcome import OutcomeService
from packages.kernel.decision_hub_kernel.persistence.db import (
    ArtifactRecord,
    Database,
    ForecastRecord,
    OutcomeRecord,
)
from packages.kernel.decision_hub_kernel.ports.sources import MarketWindowPort


class DueOutcomeService:
    def __init__(self, database: Database, market: MarketWindowPort) -> None:
        self.database = database
        self.market = market
        self.outcomes = OutcomeService(database)

    async def process_due(self, *, now: datetime | None = None, limit: int = 100) -> int:
        cutoff = now or datetime.now(UTC)
        with self.database.session() as session:
            forecasts = (
                session.query(ForecastRecord, ArtifactRecord)
                .join(ArtifactRecord, ForecastRecord.artifact_id == ArtifactRecord.artifact_id)
                .outerjoin(OutcomeRecord, OutcomeRecord.forecast_id == ForecastRecord.forecast_id)
                .filter(ForecastRecord.expires_at <= cutoff, OutcomeRecord.forecast_id.is_(None))
                .order_by(ForecastRecord.expires_at.asc())
                .limit(limit)
                .all()
            )
            targets = [
                (
                    forecast.forecast_id,
                    forecast.instrument,
                    forecast.expires_at,
                    forecast.direction,
                    artifact.created_at,
                )
                for forecast, artifact in forecasts
            ]
        processed = 0
        for forecast_id, instrument, expires_at, direction, emitted_at in targets:
            if direction not in {"long", "short"}:
                continue
            window = await self.market.window(instrument, emitted_at, expires_at)
            if window is None:
                continue
            entry = window.entry.ask if window.entry.ask is not None else window.entry.last
            exit_price = window.exit.bid if window.exit.bid is not None else window.exit.last
            if entry is None or exit_price is None or entry <= 0:
                continue
            raw_return_pct = (exit_price - entry) / entry * 100
            return_pct = raw_return_pct if direction == "long" else -raw_return_pct
            evaluation = self.outcomes.record(
                OutcomeCreate(
                    forecast_id=forecast_id,
                    return_pct=return_pct,
                    direction_correct=return_pct > 0,
                    fees=window.fees,
                    slippage=window.slippage,
                    quality_status=window.quality_status,
                )
            )
            if evaluation:
                processed += 1
        return processed

    def evaluation_for(self, forecast_id: str):
        with self.database.session() as session:
            row = session.query(OutcomeRecord).filter_by(forecast_id=forecast_id).first()
            if not row:
                return None
            return row

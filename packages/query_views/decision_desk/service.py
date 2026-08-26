from __future__ import annotations

from packages.contracts_py.decision_hub_contracts.models import DecisionDeskSummary, InboxView
from packages.kernel.decision_hub_kernel.persistence.db import Database, RunRecord


class DecisionDeskQueryService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def summary(self) -> DecisionDeskSummary:
        with self.database.session() as session:
            pending = session.query(RunRecord).filter(RunRecord.status == "admitted").count()
            running = session.query(RunRecord).filter(RunRecord.status == "running").count()
            published = (
                session.query(RunRecord)
                .filter(RunRecord.status.in_(["completed", "degraded"]))
                .count()
            )
            forecast_count = session.execute(
                __import__("sqlalchemy").text("SELECT count(*) FROM forecasts")
            ).scalar_one()
            evaluated_count = session.execute(
                __import__("sqlalchemy").text("SELECT count(*) FROM outcomes")
            ).scalar_one()
        return DecisionDeskSummary(
            inbox=InboxView(
                pending_count=pending, running_count=running, latest=self.database.latest_runs()
            ),
            published_count_30d=published,
            forecast_count=int(forecast_count),
            evaluated_count=int(evaluated_count),
            health_status="ok",
            active_strategy="baseline.v1",
            active_pack="crypto_macro.v1",
        )

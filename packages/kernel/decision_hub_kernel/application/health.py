from __future__ import annotations

from packages.contracts_py.decision_hub_contracts.models import ProductHealth
from packages.kernel.decision_hub_kernel.persistence.db import Database, RunRecord


class HealthService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def status(self) -> ProductHealth:
        with self.database.session() as session:
            session.execute(__import__("sqlalchemy").text("SELECT 1"))
            running = session.query(RunRecord).filter(RunRecord.status == "running").count()
            failed = session.query(RunRecord).filter(RunRecord.status == "failed").count()
        sources = self.database.list_source_health()
        return ProductHealth(
            status=(
                "degraded"
                if failed or any(source.status == "degraded" for source in sources)
                else "ok"
            ),
            running_runs=running,
            failed_runs=failed,
            sources=sources,
        )

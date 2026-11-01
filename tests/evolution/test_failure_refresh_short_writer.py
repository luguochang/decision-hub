from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import event

from packages.kernel.decision_hub_kernel.application.evolution import EvolutionAssetService
from packages.kernel.decision_hub_kernel.persistence.db import (
    Base,
    Database,
    FailurePatternRecord,
    FeedbackRecord,
    RunRecord,
)

NOW = datetime(2026, 10, 13, tzinfo=UTC)


def _database(tmp_path: Path) -> Database:
    db = Database(f"sqlite+pysqlite:///{(tmp_path / 'refresh.sqlite3').as_posix()}")
    Base.metadata.create_all(
        db.engine,
        tables=[
            RunRecord.__table__,
            FeedbackRecord.__table__,
            FailurePatternRecord.__table__,
        ],
    )
    with db.session() as session:
        session.add_all(
            [
                RunRecord(
                    run_id=f"run-{index}",
                    event_id=f"event-{index}",
                    status="failed",
                    error_code=f"error-{index}",
                    created_at=NOW,
                    updated_at=NOW,
                )
                for index in range(2)
            ]
        )
    return db


def test_unchanged_refresh_does_not_write_timestamp(tmp_path: Path) -> None:
    db = _database(tmp_path)
    before = EvolutionAssetService(db, clock=lambda: NOW).refresh_failure_patterns()
    writes = []

    def capture(_conn, _cursor, statement, _params, _context, _many):
        if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
            writes.append(statement)

    event.listen(db.engine, "before_cursor_execute", capture)
    try:
        after = EvolutionAssetService(
            db, clock=lambda: NOW + timedelta(minutes=1)
        ).refresh_failure_patterns()
        assert after == before
        assert writes == []
    finally:
        event.remove(db.engine, "before_cursor_execute", capture)
        db.engine.dispose()


def test_refresh_reads_before_mutations_and_preserves_regression(tmp_path: Path) -> None:
    db = _database(tmp_path)
    EvolutionAssetService(db, clock=lambda: NOW).refresh_failure_patterns()
    with db.session() as session:
        first = session.get(FailurePatternRecord, "failure:error-0")
        assert first is not None
        first.status = "closed"
        session.add(
            RunRecord(
                run_id="new-run",
                event_id="new-event",
                status="failed",
                error_code="error-0",
                created_at=NOW,
                updated_at=NOW,
            )
        )
    statements = []

    def capture(_conn, _cursor, statement, _params, _context, _many):
        statements.append(statement.lstrip().upper().split()[0])

    event.listen(db.engine, "before_cursor_execute", capture)
    try:
        values = EvolutionAssetService(
            db, clock=lambda: NOW + timedelta(minutes=1)
        ).refresh_failure_patterns()
        first = next(item for item in values if item.failure_code == "error-0")
        assert first.status == "regressed" and first.occurrence_count == 2
        mutation = next(
            index for index, kind in enumerate(statements) if kind in {"UPDATE", "INSERT"}
        )
        assert "SELECT" not in statements[mutation + 1 :]
    finally:
        event.remove(db.engine, "before_cursor_execute", capture)
        db.engine.dispose()

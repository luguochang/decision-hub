from __future__ import annotations

import json
import os
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import DateTime, Float, Integer, String, Text, create_engine, event, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from packages.contracts_py.decision_hub_contracts.models import (
    ArtifactView,
    Direction,
    EvaluationView,
    Forecast,
    GateDecision,
    GateStatus,
    RunStatus,
    RunView,
)


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class EventRecord(Base):
    __tablename__ = "events"
    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(128), default="macro_event")
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    generation: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(32), default="admitted")


class ObservationRecord(Base):
    __tablename__ = "observations"
    observation_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(128), index=True)
    source_id: Mapped[str] = mapped_column(String(128))
    source_type: Mapped[str] = mapped_column(String(32))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    language: Mapped[str] = mapped_column(String(16))
    text: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64), unique=True)
    source_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    event_hint: Mapped[str | None] = mapped_column(String(128), nullable=True)
    revision_of: Mapped[str | None] = mapped_column(String(128), nullable=True)


class SnapshotRecord(Base):
    __tablename__ = "snapshots"
    snapshot_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(128), index=True)
    cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    snapshot_hash: Mapped[str] = mapped_column(String(64))
    evidence_json: Mapped[str] = mapped_column(Text)
    pack_version: Mapped[str] = mapped_column(String(64), default="crypto_macro.v1")


class RunRecord(Base):
    __tablename__ = "runs"
    run_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(128), index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(256), unique=True, nullable=True)
    snapshot_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default=RunStatus.admitted.value)
    strategy_version: Mapped[str] = mapped_column(String(64), default="baseline.v1")
    runtime_version: Mapped[str] = mapped_column(String(64), default="fake.v1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[float] = mapped_column(Float, default=0)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    artifact_id: Mapped[str | None] = mapped_column(String(128), nullable=True)


class RunEventRecord(Base):
    __tablename__ = "run_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(128), index=True)
    sequence_no: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(128))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload_json: Mapped[str] = mapped_column(Text, default="{}")


class ArtifactRecord(Base):
    __tablename__ = "artifacts"
    artifact_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(128), index=True)
    event_id: Mapped[str] = mapped_column(String(128), index=True)
    gate_status: Mapped[str] = mapped_column(String(32))
    headline: Mapped[str] = mapped_column(String(512))
    summary: Mapped[str] = mapped_column(Text)
    payload_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ForecastRecord(Base):
    __tablename__ = "forecasts"
    forecast_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    artifact_id: Mapped[str] = mapped_column(String(128), index=True)
    instrument: Mapped[str] = mapped_column(String(128))
    horizon: Mapped[str] = mapped_column(String(16))
    direction: Mapped[str] = mapped_column(String(16))
    probability: Mapped[float] = mapped_column(Float)
    trigger: Mapped[str] = mapped_column(Text)
    invalidation: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class GateDecisionRecord(Base):
    __tablename__ = "gate_decisions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(128), index=True)
    artifact_id: Mapped[str] = mapped_column(String(128), index=True)
    rule_id: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32))
    reason_code: Mapped[str] = mapped_column(String(128))
    input_hash: Mapped[str] = mapped_column(String(64))


class OutcomeRecord(Base):
    __tablename__ = "outcomes"
    outcome_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    forecast_id: Mapped[str] = mapped_column(String(128), index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    direction_correct: Mapped[bool] = mapped_column(default=False)
    return_pct: Mapped[float] = mapped_column(Float, default=0)
    fees: Mapped[float] = mapped_column(Float, default=0)
    slippage: Mapped[float] = mapped_column(Float, default=0)
    quality_status: Mapped[str] = mapped_column(String(32), default="estimated")


class EvaluationRecord(Base):
    __tablename__ = "evaluations"
    evaluation_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    forecast_id: Mapped[str] = mapped_column(String(128), index=True)
    brier_score: Mapped[float] = mapped_column(Float)
    net_return_pct: Mapped[float] = mapped_column(Float)
    direction_correct: Mapped[bool] = mapped_column(default=False)
    label_status: Mapped[str] = mapped_column(String(32))
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class OutboxRecord(Base):
    __tablename__ = "outbox"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    artifact_id: Mapped[str] = mapped_column(String(128), index=True)
    channel: Mapped[str] = mapped_column(String(64), default="local")
    dedupe_key: Mapped[str] = mapped_column(String(256), unique=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Database:
    def __init__(self, url: str | None = None) -> None:
        data_dir = Path(os.getenv("DECISION_HUB_DATA_DIR", "data/decision-hub"))
        data_dir.mkdir(parents=True, exist_ok=True)
        db_path = data_dir / "db" / "decision_hub.sqlite3"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(
            url or f"sqlite+pysqlite:///{db_path}", connect_args={"check_same_thread": False}
        )
        if self.engine.dialect.name == "sqlite":
            event.listen(self.engine, "connect", _configure_sqlite)
        self.factory = sessionmaker(self.engine, expire_on_commit=False)

    def create_all(self) -> None:
        Base.metadata.create_all(self.engine)

    def initialize(self) -> None:
        """Apply the checked-in Alembic head for an application startup."""
        from alembic import command
        from alembic.config import Config

        config = Config(str(Path(__file__).resolve().parents[4] / "alembic.ini"))
        config.set_main_option(
            "sqlalchemy.url",
            self.engine.url.render_as_string(hide_password=False),
        )
        command.upgrade(config, "head")

    @contextmanager
    def session(self) -> Generator[Session, None, None]:
        session = self.factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get_run_view(self, run_id: str) -> RunView | None:
        with self.session() as session:
            run = session.get(RunRecord, run_id)
            if not run:
                return None
            artifact = session.get(ArtifactRecord, run.artifact_id) if run.artifact_id else None
            return RunView(
                run_id=run.run_id,
                event_id=run.event_id,
                status=RunStatus(run.status),
                strategy_version=run.strategy_version,
                snapshot_id=run.snapshot_id,
                artifact_id=run.artifact_id,
                created_at=run.created_at,
                updated_at=run.updated_at,
                finished_at=run.finished_at,
                latency_ms=run.latency_ms,
                cost_usd=run.cost_usd,
                error_code=run.error_code,
                headline=artifact.headline if artifact else None,
                gate_status=GateStatus(artifact.gate_status) if artifact else None,
            )

    def get_artifact_view(self, artifact_id: str) -> ArtifactView | None:
        with self.session() as session:
            artifact = session.get(ArtifactRecord, artifact_id)
            if not artifact:
                return None
            forecasts = session.scalars(
                select(ForecastRecord).where(ForecastRecord.artifact_id == artifact_id)
            ).all()
            gates = session.scalars(
                select(GateDecisionRecord).where(GateDecisionRecord.artifact_id == artifact_id)
            ).all()
            payload = json.loads(artifact.payload_json)
            return ArtifactView(
                artifact_id=artifact.artifact_id,
                run_id=artifact.run_id,
                event_id=artifact.event_id,
                gate_status=GateStatus(artifact.gate_status),
                headline=artifact.headline,
                summary=artifact.summary,
                facts=payload.get("facts", []),
                inferences=payload.get("inferences", []),
                counter_thesis=payload.get("counter_thesis", ""),
                uncertainty=payload.get("uncertainty", []),
                transmission_chain=payload.get("transmission_chain", []),
                citations=payload.get("citations", []),
                forecasts=[
                    Forecast(
                        forecast_id=f.forecast_id,
                        artifact_id=f.artifact_id,
                        instrument=f.instrument,
                        horizon=f.horizon,
                        direction=Direction(f.direction),
                        probability=f.probability,
                        trigger=f.trigger,
                        invalidation=f.invalidation,
                        expires_at=f.expires_at,
                    )
                    for f in forecasts
                ],
                gate_decisions=[
                    GateDecision(
                        rule_id=g.rule_id,
                        status=g.status,
                        reason_code=g.reason_code,
                        input_hash=g.input_hash,
                    )
                    for g in gates
                ],
                created_at=artifact.created_at,
            )

    def latest_runs(self, limit: int = 20) -> list[RunView]:
        with self.session() as session:
            rows = session.scalars(
                select(RunRecord).order_by(RunRecord.created_at.desc()).limit(limit)
            ).all()
            artifacts = {
                artifact.artifact_id: artifact
                for artifact in session.scalars(select(ArtifactRecord)).all()
            }
            return [
                RunView(
                    run_id=row.run_id,
                    event_id=row.event_id,
                    status=RunStatus(row.status),
                    strategy_version=row.strategy_version,
                    snapshot_id=row.snapshot_id,
                    artifact_id=row.artifact_id,
                    created_at=row.created_at,
                    updated_at=row.updated_at,
                    finished_at=row.finished_at,
                    latency_ms=row.latency_ms,
                    cost_usd=row.cost_usd,
                    error_code=row.error_code,
                    headline=artifacts[row.artifact_id].headline
                    if row.artifact_id in artifacts
                    else None,
                    gate_status=(
                        GateStatus(artifacts[row.artifact_id].gate_status)
                        if row.artifact_id in artifacts
                        else None
                    ),
                )
                for row in rows
            ]

    def get_evaluation(self, evaluation_id: str) -> EvaluationView | None:
        with self.session() as session:
            evaluation = session.get(EvaluationRecord, evaluation_id)
            if not evaluation:
                return None
            return EvaluationView(
                evaluation_id=evaluation.evaluation_id,
                forecast_id=evaluation.forecast_id,
                brier_score=evaluation.brier_score,
                net_return_pct=evaluation.net_return_pct,
                direction_correct=evaluation.direction_correct,
                label_status=evaluation.label_status,
                evaluated_at=evaluation.evaluated_at,
            )

    def get_timeline(self, run_id: str) -> list[dict[str, object]]:
        with self.session() as session:
            rows = session.scalars(
                select(RunEventRecord)
                .where(RunEventRecord.run_id == run_id)
                .order_by(RunEventRecord.sequence_no.asc())
            ).all()
            return [
                {
                    "sequence_no": row.sequence_no,
                    "event_type": row.event_type,
                    "occurred_at": row.occurred_at.isoformat(),
                    "payload": json.loads(row.payload_json),
                }
                for row in rows
            ]


def _configure_sqlite(dbapi_connection: Any, _connection_record: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()

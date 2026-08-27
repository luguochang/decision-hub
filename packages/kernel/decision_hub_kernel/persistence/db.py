from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import (
    DateTime,
    Float,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from packages.contracts_py.decision_hub_contracts.models import (
    ArtifactView,
    CallView,
    Direction,
    EvaluationView,
    Forecast,
    GateDecision,
    GateStatus,
    RunInspectorView,
    RunStatus,
    RunView,
    StepView,
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
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
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


class RunCallRecord(Base):
    __tablename__ = "run_calls"
    call_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(128), index=True)
    role: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="running")
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    runtime_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    runtime_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    api_mode: Mapped[str | None] = mapped_column(String(32), nullable=True)
    schema_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_status: Mapped[str] = mapped_column(String(32), default="unknown")
    pricing_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    retryable: Mapped[bool] = mapped_column(default=False)


class RunStepRecord(Base):
    __tablename__ = "run_steps"
    step_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(128), index=True)
    step_name: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="running")
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)


class RunRoleOutputRecord(Base):
    __tablename__ = "run_role_outputs"
    __table_args__ = (UniqueConstraint("run_id", "role", name="uq_run_role_output"),)
    output_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(128), index=True)
    role: Mapped[str] = mapped_column(String(64))
    payload_json: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(String(64))
    schema_version: Mapped[str] = mapped_column(String(64), default="agent-payload.v1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


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

    def get_run_record(self, run_id: str) -> RunRecord | None:
        with self.session() as session:
            return session.get(RunRecord, run_id)

    @property
    def sqlite_path(self) -> Path | None:
        database = self.engine.url.database
        if self.engine.dialect.name != "sqlite" or not database or database == ":memory:":
            return None
        return Path(database)

    def get_snapshot_evidence(self, snapshot_id: str) -> list[dict[str, object]]:
        with self.session() as session:
            snapshot = session.get(SnapshotRecord, snapshot_id)
            if not snapshot:
                raise KeyError(snapshot_id)
            payload = json.loads(snapshot.evidence_json)
        if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
            raise ValueError("snapshot evidence is invalid")
        return payload

    def save_role_output(
        self, run_id: str, role: str, payload: dict[str, object], schema_version: str
    ) -> str:
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        payload_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        with self.session() as session:
            existing = (
                session.query(RunRoleOutputRecord)
                .filter_by(run_id=run_id, role=role)
                .first()
            )
            if existing:
                return existing.output_id
            output_id = f"out_{__import__('uuid').uuid4().hex}"
            session.add(
                RunRoleOutputRecord(
                    output_id=output_id,
                    run_id=run_id,
                    role=role,
                    payload_json=serialized,
                    payload_hash=payload_hash,
                    schema_version=schema_version,
                    created_at=utcnow(),
                )
            )
            return output_id

    def get_role_output(self, output_id: str) -> dict[str, object]:
        with self.session() as session:
            output = session.get(RunRoleOutputRecord, output_id)
            if not output:
                raise KeyError(output_id)
            payload = json.loads(output.payload_json)
        if not isinstance(payload, dict):
            raise ValueError("role output is invalid")
        return payload

    def find_role_output(
        self, run_id: str, role: str
    ) -> tuple[str, dict[str, object]] | None:
        with self.session() as session:
            output = (
                session.query(RunRoleOutputRecord)
                .filter_by(run_id=run_id, role=role)
                .first()
            )
            if not output:
                return None
            payload = json.loads(output.payload_json)
        if not isinstance(payload, dict):
            raise ValueError("role output is invalid")
        return output.output_id, payload

    def record_run_event(
        self,
        run_id: str,
        sequence_no: int,
        event_type: str,
        payload: dict[str, object] | None = None,
    ) -> None:
        with self.session() as session:
            existing = (
                session.query(RunEventRecord)
                .filter_by(run_id=run_id, event_type=event_type)
                .first()
            )
            if existing:
                return
            session.add(
                RunEventRecord(
                    run_id=run_id,
                    sequence_no=sequence_no,
                    event_type=event_type,
                    occurred_at=utcnow(),
                    payload_json=json.dumps(payload or {}, ensure_ascii=False),
                )
            )

    def recovery_candidates(self) -> list[str]:
        with self.session() as session:
            rows = session.scalars(
                select(RunRecord)
                .where(RunRecord.status.in_([RunStatus.admitted.value, RunStatus.running.value]))
                .order_by(RunRecord.created_at.asc())
            ).all()
            return [row.run_id for row in rows]

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

    def get_run_calls(self, run_id: str) -> list[CallView]:
        with self.session() as session:
            rows = session.scalars(
                select(RunCallRecord)
                .where(RunCallRecord.run_id == run_id)
                .order_by(RunCallRecord.started_at.asc())
            ).all()
            return [
                CallView(
                    call_id=row.call_id,
                    run_id=row.run_id,
                    role=row.role,
                    status=row.status,
                    attempt=row.attempt,
                    started_at=row.started_at,
                    finished_at=row.finished_at,
                    latency_ms=row.latency_ms,
                    runtime_id=row.runtime_id,
                    runtime_version=row.runtime_version,
                    provider_id=row.provider_id,
                    model=row.model,
                    api_mode=row.api_mode,
                    schema_version=row.schema_version,
                    prompt_tokens=row.prompt_tokens,
                    completion_tokens=row.completion_tokens,
                    total_tokens=row.total_tokens,
                    cost_usd=row.cost_usd,
                    cost_status=row.cost_status,
                    pricing_version=row.pricing_version,
                    error_code=row.error_code,
                    retryable=row.retryable,
                )
                for row in rows
            ]

    def get_run_steps(self, run_id: str) -> list[StepView]:
        with self.session() as session:
            rows = session.scalars(
                select(RunStepRecord)
                .where(RunStepRecord.run_id == run_id)
                .order_by(RunStepRecord.started_at.asc(), RunStepRecord.attempt.asc())
            ).all()
            return [
                StepView(
                    step_id=row.step_id,
                    run_id=row.run_id,
                    step_name=row.step_name,
                    status=row.status,
                    attempt=row.attempt,
                    started_at=row.started_at,
                    finished_at=row.finished_at,
                    latency_ms=row.latency_ms,
                    error_code=row.error_code,
                )
                for row in rows
            ]

    def get_run_inspector(self, run_id: str) -> RunInspectorView | None:
        run = self.get_run_view(run_id)
        if not run:
            return None
        artifact = self.get_artifact_view(run.artifact_id) if run.artifact_id else None
        with self.session() as session:
            evaluations = session.scalars(
                select(EvaluationRecord)
                .join(ForecastRecord, EvaluationRecord.forecast_id == ForecastRecord.forecast_id)
                .join(ArtifactRecord, ForecastRecord.artifact_id == ArtifactRecord.artifact_id)
                .where(ArtifactRecord.run_id == run_id)
                .order_by(EvaluationRecord.evaluated_at.asc())
            ).all()
            snapshot = session.get(SnapshotRecord, run.snapshot_id) if run.snapshot_id else None
        evaluation_views = [
            EvaluationView(
                evaluation_id=item.evaluation_id,
                forecast_id=item.forecast_id,
                brier_score=item.brier_score,
                net_return_pct=item.net_return_pct,
                direction_correct=item.direction_correct,
                label_status=item.label_status,
                evaluated_at=item.evaluated_at,
            )
            for item in evaluations
        ]
        return RunInspectorView(
            run=run,
            timeline=self.get_timeline(run_id),
            steps=self.get_run_steps(run_id),
            calls=self.get_run_calls(run_id),
            artifact=artifact,
            evaluation_count=len(evaluation_views),
            evaluations=evaluation_views,
            snapshot_cutoff_at=snapshot.cutoff_at if snapshot else None,
            snapshot_hash=snapshot.snapshot_hash if snapshot else None,
        )


def _configure_sqlite(dbapi_connection: Any, _connection_record: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()

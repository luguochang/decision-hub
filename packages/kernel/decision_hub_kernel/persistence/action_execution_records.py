"""Private W6 SQL draft; current-session repositories own typed validation.

No production Base registration/migration is performed by importing this file.
Money/quantity remain canonical strings in immutable payloads. Mutable episode
heads index only scope/due/open/remaining-presence, never approximate float money.
"""

from datetime import datetime

from packages.kernel.decision_hub_kernel.persistence.action_paper_records import DraftBase
from sqlalchemy import (
    DDL,
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import Mapped, mapped_column


class ExecutionProtocolRecord(DraftBase):
    __tablename__ = "action_execution_protocols"
    protocol_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    trial_id: Mapped[str] = mapped_column(String(128), index=True)
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload_json: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(String(64), unique=True)
    __table_args__ = (
        CheckConstraint(
            "registered_at <= start_at AND start_at < end_at", name="ck_execution_protocol_window"
        ),
    )


class PaperOpeningRecord(DraftBase):
    __tablename__ = "action_paper_openings"
    opening_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    protocol_id: Mapped[str] = mapped_column(String(128))
    scope_id: Mapped[str] = mapped_column(String(200))
    payload_json: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(String(64))
    __table_args__ = (
        UniqueConstraint("protocol_id", "scope_id", name="uq_paper_opening_protocol_scope"),
    )


class ExecutionIntentRecord(DraftBase):
    __tablename__ = "action_execution_intents"
    intent_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    portfolio_id: Mapped[str] = mapped_column(String(128))
    protocol_id: Mapped[str] = mapped_column(String(128))
    scope_id: Mapped[str] = mapped_column(String(200))
    event_key: Mapped[str] = mapped_column(String(200))
    plan_id: Mapped[str] = mapped_column(String(128))
    revision_id: Mapped[str] = mapped_column(String(128))
    phase: Mapped[str] = mapped_column(String(16))
    eligible_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    quote_deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload_json: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(String(64))
    __table_args__ = (
        UniqueConstraint("portfolio_id", "event_key", name="uq_execution_portfolio_event"),
        CheckConstraint("phase IN ('entry','reduce','exit')", name="ck_execution_intent_phase"),
        CheckConstraint("quote_deadline > eligible_at", name="ck_execution_intent_window"),
        Index("ix_execution_intent_due", "eligible_at", "quote_deadline", "scope_id"),
        Index("ix_execution_intent_revision", "plan_id", "revision_id"),
    )


class ExecutionReceiptRecord(DraftBase):
    __tablename__ = "action_execution_receipts"
    receipt_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    intent_id: Mapped[str] = mapped_column(String(128), unique=True)
    portfolio_id: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16))
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload_json: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(String(64))
    __table_args__ = (
        CheckConstraint(
            "status IN ('filled','no_fill','rejected','unknown')",
            name="ck_execution_receipt_status",
        ),
        Index("ix_execution_receipt_portfolio_time", "portfolio_id", "decided_at"),
    )


class ExecutionFillRecord(DraftBase):
    __tablename__ = "action_execution_fills"
    fill_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    intent_id: Mapped[str] = mapped_column(String(128), unique=True)
    portfolio_id: Mapped[str] = mapped_column(String(128))
    episode_id: Mapped[str] = mapped_column(String(128))
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload_json: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(String(64))
    __table_args__ = (Index("ix_execution_fill_episode_time", "episode_id", "executed_at"),)


class ExecutionEpisodeRecord(DraftBase):
    __tablename__ = "action_execution_episodes"
    episode_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    portfolio_id: Mapped[str] = mapped_column(String(128))
    payload_json: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(String(64))
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_execution_episode_version"),
        Index("ix_execution_episode_portfolio", "portfolio_id", "episode_id", "version"),
    )


class ExecutionEpisodeHeadRecord(DraftBase):
    __tablename__ = "action_execution_episode_heads"
    portfolio_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    episode_id: Mapped[str] = mapped_column(String(128))
    version: Mapped[int] = mapped_column(Integer)
    payload_hash: Mapped[str] = mapped_column(String(64))
    scope_id: Mapped[str] = mapped_column(String(200))
    active: Mapped[bool] = mapped_column(Boolean(create_constraint=True))
    remaining_nonzero: Mapped[bool] = mapped_column(Boolean(create_constraint=True))
    next_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    quote_cursor_available_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    quote_cursor_received_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    quote_cursor_capture_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_execution_episode_head_version"),
        CheckConstraint(
            "active = 0 OR remaining_nonzero = 1", name="ck_execution_episode_open_quantity"
        ),
        CheckConstraint(
            "active = 0 OR next_due_at IS NOT NULL", name="ck_execution_episode_open_due"
        ),
        CheckConstraint(
            "(quote_cursor_available_at IS NULL AND quote_cursor_received_at IS NULL "
            "AND quote_cursor_capture_id IS NULL) OR (quote_cursor_available_at IS NOT NULL "
            "AND quote_cursor_received_at IS NOT NULL AND quote_cursor_capture_id IS NOT NULL)",
            name="ck_execution_episode_quote_cursor_complete",
        ),
        Index("ix_execution_episode_due", "active", "next_due_at", "scope_id"),
        Index("ix_execution_episode_remaining", "scope_id", "remaining_nonzero", "next_due_at"),
    )


class ExecutionObservationRecord(DraftBase):
    __tablename__ = "action_execution_observations"
    observation_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    portfolio_id: Mapped[str] = mapped_column(String(128))
    source_kind: Mapped[str] = mapped_column(String(16))
    source_id: Mapped[str] = mapped_column(String(128))
    opportunity_id: Mapped[str] = mapped_column(String(128))
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload_json: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(String(64))
    __table_args__ = (
        UniqueConstraint(
            "portfolio_id",
            "source_kind",
            "source_id",
            "version",
            name="uq_execution_observation_version",
        ),
        CheckConstraint("version >= 1", name="ck_execution_observation_version"),
        CheckConstraint(
            "source_kind IN ('evaluation','opportunity')", name="ck_execution_observation_source"
        ),
        CheckConstraint(
            "status IN ('intent_created','not_actionable','waiting_display',"
            "'blocked','unknown','no_plan')",
            name="ck_execution_observation_status",
        ),
        Index(
            "ix_execution_observation_denominator", "portfolio_id", "opportunity_id", "observed_at"
        ),
    )


class ExecutionObservationHeadRecord(DraftBase):
    __tablename__ = "action_execution_observation_heads"
    portfolio_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    source_kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    version: Mapped[int] = mapped_column(Integer)
    observation_id: Mapped[str] = mapped_column(String(128))
    payload_hash: Mapped[str] = mapped_column(String(64))
    next_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_execution_observation_head_version"),
        Index("ix_execution_observation_due", "next_due_at", "portfolio_id"),
    )


class ExecutionPortfolioCursorRecord(DraftBase):
    __tablename__ = "action_execution_portfolio_cursors"
    portfolio_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    polled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_observation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    __table_args__ = (Index("ix_execution_portfolio_cursor_fair", "polled_at", "portfolio_id"),)


IMMUTABLE_TABLES = tuple(
    record.__table__
    for record in (
        ExecutionProtocolRecord,
        PaperOpeningRecord,
        ExecutionIntentRecord,
        ExecutionReceiptRecord,
        ExecutionFillRecord,
        ExecutionEpisodeRecord,
        ExecutionObservationRecord,
    )
)

for table in IMMUTABLE_TABLES:
    keys = [tuple(column.name for column in table.primary_key.columns)]
    keys.extend(
        tuple(column.name for column in key.columns)
        for key in table.constraints
        if isinstance(key, UniqueConstraint)
    )
    conflict = " OR ".join(
        "(" + " AND ".join(f"{name}=NEW.{name}" for name in key) + ")" for key in keys
    )
    event.listen(
        table,
        "after_create",
        DDL(
            f"CREATE TRIGGER {table.name}_immutable_insert BEFORE INSERT ON {table.name} "
            f"WHEN EXISTS (SELECT 1 FROM {table.name} WHERE {conflict}) "
            "BEGIN SELECT RAISE(ABORT, 'action_execution_original_immutable'); END"
        ).execute_if(dialect="sqlite"),
    )
    for operation in ("UPDATE", "DELETE"):
        event.listen(
            table,
            "after_create",
            DDL(
                f"CREATE TRIGGER {table.name}_immutable_{operation.lower()} "
                f"BEFORE {operation} ON {table.name} "
                "BEGIN SELECT RAISE(ABORT, 'action_execution_original_immutable'); END"
            ).execute_if(dialect="sqlite"),
        )

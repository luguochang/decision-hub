"""Immutable settings originals and fenced mutable pending heads."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DDL,
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

from .db import Base


class DecisionSettingsRevisionRecord(Base):
    __tablename__ = "decision_settings_revisions"
    __table_args__ = (
        UniqueConstraint("settings_key", "revision_no", name="uq_settings_revision_number"),
        CheckConstraint("revision_no >= 1", name="ck_settings_revision_number"),
        CheckConstraint(
            "account_kind IN ('user_declared','paper')", name="ck_settings_revision_kind"
        ),
        CheckConstraint("length(trim(account_id)) > 0", name="ck_settings_revision_account"),
        Index("ix_settings_revision_owner_created", "owner_id", "created_at"),
    )
    revision_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    settings_key: Mapped[str] = mapped_column(String(128))
    owner_id: Mapped[str] = mapped_column(String(128))
    account_kind: Mapped[str] = mapped_column(String(16))
    account_id: Mapped[str] = mapped_column(String(128))
    instrument_id: Mapped[str] = mapped_column(String(192))
    profile_ref: Mapped[str] = mapped_column(String(256))
    revision_no: Mapped[int] = mapped_column(Integer)
    parent_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload_json: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DecisionSettingsHeadRecord(Base):
    __tablename__ = "decision_settings_heads"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "account_kind",
            "account_id",
            "instrument_id",
            "profile_ref",
            name="uq_settings_partition",
        ),
        CheckConstraint("account_kind IN ('user_declared','paper')", name="ck_settings_head_kind"),
        CheckConstraint("length(trim(account_id)) > 0", name="ck_settings_head_account"),
        CheckConstraint("state_version >= 1 AND scan_generation >= 1", name="ck_settings_versions"),
        CheckConstraint("reevaluation_pending IN (0,1)", name="ck_settings_pending_flag"),
        CheckConstraint(
            "(reevaluation_pending = 1 AND pending_revision_id IS NOT NULL "
            "AND pending_state_version IS NOT NULL AND pending_revision_id = current_revision_id "
            "AND pending_state_version = state_version AND next_due_at IS NOT NULL) OR "
            "(reevaluation_pending = 0 AND pending_revision_id IS NULL "
            "AND pending_state_version IS NULL AND next_due_at IS NULL)",
            name="ck_settings_pending_binding",
        ),
        Index("ix_settings_pending_due", "reevaluation_pending", "next_due_at", "settings_key"),
        Index("ix_settings_owner_partition", "owner_id", "account_kind", "account_id"),
    )
    settings_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(128))
    account_kind: Mapped[str] = mapped_column(String(16))
    account_id: Mapped[str] = mapped_column(String(128))
    instrument_id: Mapped[str] = mapped_column(String(192))
    profile_ref: Mapped[str] = mapped_column(String(256))
    current_revision_id: Mapped[str] = mapped_column(String(128))
    state_version: Mapped[int] = mapped_column(Integer)
    reevaluation_pending: Mapped[int] = mapped_column(Integer)
    pending_revision_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    pending_state_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    scan_generation: Mapped[int] = mapped_column(Integer)
    scan_after_plan_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    next_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# Reuse the existing action_records append-only trigger pattern, including
# INSERT conflict rejection so SQLite REPLACE cannot silently replace originals.
_table = DecisionSettingsRevisionRecord.__table__
_keys = [tuple(column.name for column in _table.primary_key.columns)]
_keys.extend(
    tuple(column.name for column in constraint.columns)
    for constraint in _table.constraints
    if isinstance(constraint, UniqueConstraint)
)
_conflicts = " OR ".join(
    "(" + " AND ".join(f"{name} = NEW.{name}" for name in key) + ")" for key in _keys
)
event.listen(
    _table,
    "after_create",
    DDL(
        f"CREATE TRIGGER trg_{_table.name}_immutable_insert BEFORE INSERT ON {_table.name} "
        f"WHEN EXISTS (SELECT 1 FROM {_table.name} WHERE {_conflicts}) "
        f"BEGIN SELECT RAISE(ABORT, 'UNIQUE immutable identity: {_table.name}'); END"
    ).execute_if(dialect="sqlite"),
)
for _operation in ("UPDATE", "DELETE"):
    event.listen(
        _table,
        "after_create",
        DDL(
            f"CREATE TRIGGER trg_{_table.name}_immutable_{_operation.lower()} "
            f"BEFORE {_operation} ON {_table.name} "
            f"BEGIN SELECT RAISE(ABORT, '{_table.name}_immutable'); END"
        ).execute_if(dialect="sqlite"),
    )

"""Artifact-only shared scope sampling lease and immutable slot receipts."""

from __future__ import annotations

from datetime import datetime

from packages.kernel.decision_hub_kernel.persistence.action_quote_repository import QuoteBase
from sqlalchemy import DDL, CheckConstraint, DateTime, Index, Integer, String, Text, event
from sqlalchemy.orm import Mapped, mapped_column


class QuoteSamplingHead(QuoteBase):
    __tablename__ = "action_quote_sampling_heads"
    __table_args__ = (
        CheckConstraint("generation >= 1", name="ck_quote_sampling_generation"),
        CheckConstraint("status IN ('ready','blocked')", name="ck_quote_sampling_status"),
        CheckConstraint(
            "(owner_id IS NULL AND lease_expires_at IS NULL) OR "
            "(owner_id IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="ck_quote_sampling_lease_pair",
        ),
        Index("ix_quote_sampling_due", "status", "next_due_at", "lease_expires_at"),
    )
    scope_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    status: Mapped[str] = mapped_column(String(16))
    blocked_reason: Mapped[str | None] = mapped_column(String(160), nullable=True)
    slot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    next_due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    owner_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    generation: Mapped[int] = mapped_column(Integer)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class QuoteSamplingReceipt(QuoteBase):
    __tablename__ = "action_quote_sampling_receipts"
    __table_args__ = (
        Index("ix_quote_sampling_receipt_scope_slot", "scope_id", "slot_at"),
        CheckConstraint("generation >= 1", name="ck_quote_sampling_receipt_generation"),
        CheckConstraint(
            "event IN ('claimed','captured','rejected','failed')",
            name="ck_quote_sampling_receipt_event",
        ),
    )
    receipt_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    scope_id: Mapped[str] = mapped_column(String(200))
    slot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    generation: Mapped[int] = mapped_column(Integer)
    event: Mapped[str] = mapped_column(String(16))
    payload_json: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(String(64))


for operation in ("UPDATE", "DELETE"):
    event.listen(
        QuoteSamplingReceipt.__table__,
        "after_create",
        DDL(
            f"CREATE TRIGGER action_quote_sampling_receipts_immutable_{operation.lower()} "
            f"BEFORE {operation} ON action_quote_sampling_receipts BEGIN "
            "SELECT RAISE(ABORT, 'action_quote_sampling_receipt_immutable'); END"
        ).execute_if(dialect="sqlite"),
    )

event.listen(
    QuoteSamplingReceipt.__table__,
    "after_create",
    DDL(
        "CREATE TRIGGER action_quote_sampling_receipts_immutable_replace BEFORE INSERT "
        "ON action_quote_sampling_receipts WHEN EXISTS "
        "(SELECT 1 FROM action_quote_sampling_receipts "
        "WHERE receipt_id = NEW.receipt_id) BEGIN "
        "SELECT RAISE(ABORT, 'action_quote_sampling_receipt_immutable'); END"
    ).execute_if(dialect="sqlite"),
)

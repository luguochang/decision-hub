"""Strict quote originals, availability and bounded shared sampling ownership.

Draft only. Explicit DDL; no current ORM imports, no 0093/outbox rewrites.
"""

import sqlalchemy as sa
from alembic import op

revision = "0094_action_quotes"
down_revision = "0093_decision_product_v1"
branch_labels = None
depends_on = None

TABLES = (
    "action_quotes",
    "action_quote_availability",
    "action_quote_sampling_heads",
    "action_quote_sampling_receipts",
)
IMMUTABLE = {
    "action_quotes": "quote_id = NEW.quote_id OR capture_id = NEW.capture_id",
    "action_quote_availability": "quote_id = NEW.quote_id",
    "action_quote_sampling_receipts": "receipt_id = NEW.receipt_id",
}


def upgrade() -> None:
    op.create_table(
        "action_quotes",
        sa.Column("quote_id", sa.String(128), primary_key=True),
        sa.Column("capture_id", sa.String(128), nullable=False),
        sa.Column("scope_id", sa.String(200), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("admitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("semantic_hash", sa.String(64), nullable=False),
        sa.Column("payload_json", sa.Text, nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.UniqueConstraint("capture_id", name="uq_action_quote_capture"),
    )
    op.create_index("ix_action_quote_scope_observed", "action_quotes", ["scope_id", "observed_at"])
    op.create_index("ix_action_quote_semantic", "action_quotes", ["semantic_hash"])
    op.create_table(
        "action_quote_availability",
        sa.Column("quote_id", sa.String(128), primary_key=True),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload_json", sa.Text, nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
    )
    op.create_index(
        "ix_action_quote_availability_available_at", "action_quote_availability", ["available_at"]
    )
    op.create_table(
        "action_quote_sampling_heads",
        sa.Column("scope_id", sa.String(200), primary_key=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("blocked_reason", sa.String(160), nullable=True),
        sa.Column("slot_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("next_due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("owner_id", sa.String(128), nullable=True),
        sa.Column("generation", sa.Integer, nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("generation >= 1", name="ck_quote_sampling_generation"),
        sa.CheckConstraint("status IN ('ready','blocked')", name="ck_quote_sampling_status"),
        sa.CheckConstraint(
            "(owner_id IS NULL AND lease_expires_at IS NULL) OR "
            "(owner_id IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="ck_quote_sampling_lease_pair",
        ),
    )
    op.create_index(
        "ix_quote_sampling_due",
        "action_quote_sampling_heads",
        ["status", "next_due_at", "lease_expires_at"],
    )
    op.create_table(
        "action_quote_sampling_receipts",
        sa.Column("receipt_id", sa.String(128), primary_key=True),
        sa.Column("scope_id", sa.String(200), nullable=False),
        sa.Column("slot_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("generation", sa.Integer, nullable=False),
        sa.Column("event", sa.String(16), nullable=False),
        sa.Column("payload_json", sa.Text, nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.CheckConstraint("generation >= 1", name="ck_quote_sampling_receipt_generation"),
        sa.CheckConstraint(
            "event IN ('claimed','captured','rejected','failed')",
            name="ck_quote_sampling_receipt_event",
        ),
    )
    op.create_index(
        "ix_quote_sampling_receipt_scope_slot",
        "action_quote_sampling_receipts",
        ["scope_id", "slot_at"],
    )
    if op.get_bind().dialect.name == "sqlite":
        for table, identity in IMMUTABLE.items():
            error_code = (
                "action_quote_sampling_receipt_immutable"
                if table == "action_quote_sampling_receipts"
                else "action_quote_immutable"
            )
            for operation in ("UPDATE", "DELETE"):
                op.execute(
                    sa.text(
                        f"CREATE TRIGGER {table}_immutable_{operation.lower()} "
                        f"BEFORE {operation} ON {table} "
                        f"BEGIN SELECT RAISE(ABORT, '{error_code}'); END"
                    )
                )
            op.execute(
                sa.text(
                    f"CREATE TRIGGER {table}_immutable_replace BEFORE INSERT ON {table} "
                    f"WHEN EXISTS (SELECT 1 FROM {table} WHERE {identity}) "
                    f"BEGIN SELECT RAISE(ABORT, '{error_code}'); END"
                )
            )


def downgrade() -> None:
    connection = op.get_bind()
    for table in TABLES:
        if connection.execute(sa.text(f"SELECT 1 FROM {table} LIMIT 1")).first() is not None:
            raise RuntimeError("action_quote_downgrade_has_evidence")
    for table in reversed(TABLES):
        op.drop_table(table)

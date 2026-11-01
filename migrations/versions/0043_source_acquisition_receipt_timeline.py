"""Separate browser request, observation and Hub receipt clocks."""

import sqlalchemy as sa
from alembic import op

revision = "0043_source_acquisition_receipt_timeline"
down_revision = "0042_source_poll_attempts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("source_acquisition_receipts") as batch:
        batch.add_column(sa.Column("request_started_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(
            sa.Column("request_completed_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.add_column(sa.Column("received_at", sa.DateTime(timezone=True), nullable=True))
        batch.alter_column(
            "observed_at", existing_type=sa.DateTime(timezone=True), nullable=True
        )
    op.execute(
        sa.text("UPDATE source_acquisition_receipts SET received_at = created_at")
    )
    op.create_index(
        "ix_source_acquisition_receipts_received_at",
        "source_acquisition_receipts",
        ["received_at"],
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE source_acquisition_receipts "
            "SET observed_at = "
            "COALESCE(observed_at, request_completed_at, received_at, created_at) "
            "WHERE observed_at IS NULL"
        )
    )
    op.drop_index(
        "ix_source_acquisition_receipts_received_at",
        table_name="source_acquisition_receipts",
    )
    with op.batch_alter_table("source_acquisition_receipts") as batch:
        batch.alter_column(
            "observed_at", existing_type=sa.DateTime(timezone=True), nullable=False
        )
        batch.drop_column("received_at")
        batch.drop_column("request_completed_at")
        batch.drop_column("request_started_at")

"""Add the durable source acquisition receipt ledger."""

import sqlalchemy as sa
from alembic import op

revision = "0038_source_acquisition_receipts"
down_revision = "0037_review_schedule_ledger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "source_acquisition_receipts",
        sa.Column("receipt_id", sa.String(128), primary_key=True),
        sa.Column("source_id", sa.String(128), nullable=False),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("access_mode", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("artifacts_json", sa.Text(), nullable=False),
        sa.Column("error_code", sa.String(200), nullable=True),
        sa.Column("receipt_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("receipt_hash", name="uq_source_acquisition_receipt_hash"),
    )
    op.create_index(
        "ix_source_acquisition_receipts_source_id",
        "source_acquisition_receipts",
        ["source_id"],
    )
    op.create_index(
        "ix_source_acquisition_receipts_status",
        "source_acquisition_receipts",
        ["status"],
    )
    op.create_index(
        "ix_source_acquisition_receipts_observed_at",
        "source_acquisition_receipts",
        ["observed_at"],
    )
    op.create_index(
        "ix_source_acquisition_receipts_receipt_hash",
        "source_acquisition_receipts",
        ["receipt_hash"],
    )


def downgrade() -> None:
    op.drop_table("source_acquisition_receipts")

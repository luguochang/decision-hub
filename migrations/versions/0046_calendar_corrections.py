"""Append parser-correction receipts, retaining original events and vintages."""

import sqlalchemy as sa
from alembic import op

revision = "0046_calendar_corrections"
down_revision = "0045_source_content_snapshot"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "macro_events", sa.Column("invalidated_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("macro_events", sa.Column("correction_ref", sa.String(128), nullable=True))
    op.create_table(
        "calendar_corrections",
        sa.Column("correction_id", sa.String(128), primary_key=True),
        sa.Column("source_id", sa.String(128), nullable=False),
        sa.Column("parser_version", sa.String(128), nullable=False),
        sa.Column("source_content_hash", sa.String(64), nullable=False),
        sa.Column("source_content_ref", sa.String(2048), nullable=False),
        sa.Column("reason", sa.String(256), nullable=False),
        sa.Column("before_json", sa.Text(), nullable=False),
        sa.Column("invalidated_ids_json", sa.Text(), nullable=False),
        sa.Column("verified_ids_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_calendar_corrections_source_id", "calendar_corrections", ["source_id"])


def downgrade() -> None:
    op.drop_table("calendar_corrections")
    with op.batch_alter_table("macro_events") as batch:
        batch.drop_column("correction_ref")
        batch.drop_column("invalidated_at")

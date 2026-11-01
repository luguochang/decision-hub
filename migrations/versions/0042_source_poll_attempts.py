"""Persist each source polling attempt, including empty and failed polls."""

import sqlalchemy as sa
from alembic import op

revision = "0042_source_poll_attempts"
down_revision = "0041_document_privacy_mode"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "source_poll_attempts",
        sa.Column("attempt_id", sa.String(128), primary_key=True),
        sa.Column("source_id", sa.String(128), nullable=False),
        sa.Column("trigger_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cursor_before", sa.Text(), nullable=True),
        sa.Column("cursor_after", sa.Text(), nullable=True),
        sa.Column("received_count", sa.Integer(), nullable=False),
        sa.Column("accepted_count", sa.Integer(), nullable=False),
        sa.Column("duplicate_count", sa.Integer(), nullable=False),
        sa.Column("revision_count", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(128), nullable=True),
    )
    op.create_index(
        "ix_source_poll_attempts_source_started",
        "source_poll_attempts",
        ["source_id", "started_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_source_poll_attempts_source_started", table_name="source_poll_attempts")
    op.drop_table("source_poll_attempts")

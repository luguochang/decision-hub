"""Add durable source cursor and health state."""

import sqlalchemy as sa
from alembic import op

revision = "0008_source_states"
down_revision = "0007_run_cost_nullable"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "source_states",
        sa.Column("source_id", sa.String(length=128), primary_key=True),
        sa.Column("source_type", sa.String(length=64), nullable=False),
        sa.Column("manifest_version", sa.String(length=64), nullable=False),
        sa.Column("cursor", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="unknown"),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("source_states")

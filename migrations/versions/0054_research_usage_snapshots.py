"""Preserve immutable model usage reconciliation metadata."""

import sqlalchemy as sa
from alembic import op

revision = "0054_research_usage_snapshots"
down_revision = "0053_library_context_consent"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "research_usage_snapshots",
        sa.Column("snapshot_id", sa.String(80), primary_key=True),
        sa.Column("run_id", sa.String(128), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
    )
    op.create_index("ix_research_usage_snapshots_run_id", "research_usage_snapshots", ["run_id"])


def downgrade():
    op.drop_index("ix_research_usage_snapshots_run_id", "research_usage_snapshots")
    op.drop_table("research_usage_snapshots")

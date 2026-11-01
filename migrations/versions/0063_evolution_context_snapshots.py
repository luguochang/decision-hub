"""Freeze resolved planning inputs and their complete disposition ledger."""

import sqlalchemy as sa
from alembic import op

revision = "0063_evolution_context_snapshots"
down_revision = "0062_evolution_planning_calls"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "evolution_context_snapshots",
        sa.Column("job_id", sa.String(128), primary_key=True),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("content_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("evolution_context_snapshots")

"""Preserve each evolution planning call before dispatch and after return."""

import sqlalchemy as sa
from alembic import op

revision = "0062_evolution_planning_calls"
down_revision = "0061_evaluation_stage_receipts"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "evolution_planning_calls",
        sa.Column("call_id", sa.String(64), primary_key=True),
        sa.Column("job_id", sa.String(128), nullable=False),
        sa.Column("job_attempt", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("details_json", sa.Text(), nullable=False),
    )
    op.create_index("ix_evolution_planning_calls_job_id", "evolution_planning_calls", ["job_id"])


def downgrade():
    op.drop_table("evolution_planning_calls")

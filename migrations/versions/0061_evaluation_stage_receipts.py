"""Pin evaluation stages and preserve completed results across worker recovery."""

import sqlalchemy as sa
from alembic import op

revision = "0061_evaluation_stage_receipts"
down_revision = "0060_evolution_execution_scope"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "evaluation_stage_receipts",
        sa.Column("experiment_id", sa.String(128), primary_key=True),
        sa.Column("binding_hash", sa.String(64), nullable=False),
        sa.Column("binding_json", sa.Text(), nullable=False),
        sa.Column("completion_hash", sa.String(64), nullable=True),
        sa.Column("completion_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade():
    op.drop_table("evaluation_stage_receipts")

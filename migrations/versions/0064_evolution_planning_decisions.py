"""Persist supported proposals and operational/no-candidate decisions separately."""
import sqlalchemy as sa
from alembic import op

revision = "0064_evolution_planning_decisions"
down_revision = "0063_evolution_context_snapshots"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "evolution_planning_decisions",
        sa.Column("job_id", sa.String(128), primary_key=True),
        sa.Column("context_hash", sa.String(64), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("content_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("evolution_planning_decisions")

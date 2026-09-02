"""Add scheduled child-run lineage for durable research rechecks."""

import sqlalchemy as sa
from alembic import op

revision = "0019_scheduled_research_runs"
down_revision = "0018_research_run_leases"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("runs") as batch:
        batch.add_column(sa.Column("available_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("parent_run_id", sa.String(128), nullable=True))
        batch.create_index("ix_runs_available_at", ["available_at"])
        batch.create_index("ix_runs_parent_run_id", ["parent_run_id"])


def downgrade() -> None:
    with op.batch_alter_table("runs") as batch:
        batch.drop_index("ix_runs_parent_run_id")
        batch.drop_index("ix_runs_available_at")
        batch.drop_column("parent_run_id")
        batch.drop_column("available_at")

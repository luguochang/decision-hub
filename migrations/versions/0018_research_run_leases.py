"""Add durable leases to research and other long-running Runs."""

import sqlalchemy as sa
from alembic import op

revision = "0018_research_run_leases"
down_revision = "0017_agentic_research_evidence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("runs") as batch:
        batch.add_column(sa.Column("lease_owner", sa.String(128), nullable=True))
        batch.add_column(sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_index("ix_runs_lease_owner", ["lease_owner"])


def downgrade() -> None:
    with op.batch_alter_table("runs") as batch:
        batch.drop_index("ix_runs_lease_owner")
        batch.drop_column("lease_expires_at")
        batch.drop_column("lease_owner")

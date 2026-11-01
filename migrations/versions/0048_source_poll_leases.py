"""Serialize each source cursor across API and worker processes."""

import sqlalchemy as sa
from alembic import op

revision = "0048_source_poll_leases"
down_revision = "0047_service_runtime_configs"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("source_states", sa.Column("poll_owner", sa.String(64), nullable=True))
    op.add_column(
        "source_states",
        sa.Column("poll_lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade():
    with op.batch_alter_table("source_states") as batch:
        batch.drop_column("poll_lease_expires_at")
        batch.drop_column("poll_owner")

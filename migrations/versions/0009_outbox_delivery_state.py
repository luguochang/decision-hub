"""Add observable notification retry and terminal failure state."""

import sqlalchemy as sa
from alembic import op

revision = "0009_outbox_delivery_state"
down_revision = "0008_source_states"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("outbox") as batch:
        batch.add_column(sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("last_error_code", sa.String(length=128), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("outbox") as batch:
        batch.drop_column("last_error_code")
        batch.drop_column("failed_at")
        batch.drop_column("next_attempt_at")

"""Add the durable next poll time for source scheduling.

The original local R1 development database had already applied 0008 before
`next_poll_at` was added to the ORM model. Keeping this as a forward migration
makes both that database and a fresh R1 install converge on the same schema.
"""

import sqlalchemy as sa
from alembic import op

revision = "0010_source_poll_schedule"
down_revision = "0009_outbox_delivery_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("source_states") as batch:
        batch.add_column(sa.Column("next_poll_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("source_states") as batch:
        batch.drop_column("next_poll_at")

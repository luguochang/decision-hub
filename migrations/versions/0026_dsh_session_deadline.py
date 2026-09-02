"""Persist the accepted DSH run deadline for live capability PIT enforcement."""

import sqlalchemy as sa
from alembic import op

revision = "0026_dsh_session_deadline"
down_revision = "0025_run_admission_priority"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nullable preserves historical links whose original host submission did
    # not persist a deadline. The gateway fails closed until a new admission
    # supplies one; it never guesses from created_at.
    with op.batch_alter_table("dsh_session_links") as batch:
        batch.add_column(sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("dsh_session_links") as batch:
        batch.drop_column("deadline_at")

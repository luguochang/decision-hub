"""Persist HTTP, parsing and calendar coverage independently from new-item counts."""

import sqlalchemy as sa
from alembic import op

revision = "0045_source_content_snapshot"
down_revision = "0044_calendar_time_precision"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "source_poll_attempts", sa.Column("content_snapshot_json", sa.Text(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("source_poll_attempts", "content_snapshot_json")

"""Keep source date precision and immutable schedule revisions without guessing old times."""

import sqlalchemy as sa
from alembic import op

revision = "0044_calendar_time_precision"
down_revision = "0043_source_acquisition_receipt_timeline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("macro_events", "macro_event_vintages"):
        op.add_column(table, sa.Column("scheduled_date", sa.String(10), nullable=True))
        op.add_column(table, sa.Column("source_timezone", sa.String(64), nullable=True))
        op.add_column(
            table,
            sa.Column("time_precision", sa.String(16), nullable=False, server_default="unknown"),
        )
    op.add_column(
        "macro_event_vintages", sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True)
    )
    # No backfill from current macro_events: that would rewrite what historical
    # vintages knew. Legacy records remain explicitly unknown until reconciled.


def downgrade() -> None:
    op.drop_column("macro_event_vintages", "scheduled_at")
    for table in ("macro_events", "macro_event_vintages"):
        with op.batch_alter_table(table) as batch:
            batch.drop_column("scheduled_date")
            batch.drop_column("source_timezone")
            batch.drop_column("time_precision")

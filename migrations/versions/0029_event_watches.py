"""Add durable future event watches and idempotent window sample slots."""

import sqlalchemy as sa
from alembic import op

revision = "0029_event_watches"
down_revision = "0028_research_fact_envelopes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("observations") as batch:
        batch.add_column(sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "event_watches",
        sa.Column("watch_id", sa.String(128), primary_key=True),
        sa.Column("event_id", sa.String(128), nullable=False),
        sa.Column("source_id", sa.String(128), nullable=False),
        sa.Column("event_family", sa.String(128), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("window_offsets_json", sa.Text(), nullable=False),
        sa.Column("baseline_status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("next_tick_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("event_id", name="uq_event_watches_event_id"),
    )
    op.create_index("ix_event_watches_event_id", "event_watches", ["event_id"])
    op.create_index("ix_event_watches_source_id", "event_watches", ["source_id"])
    op.create_index("ix_event_watches_scheduled_at", "event_watches", ["scheduled_at"])
    op.create_index("ix_event_watches_status", "event_watches", ["status"])
    op.create_index("ix_event_watches_next_tick_at", "event_watches", ["next_tick_at"])
    op.create_table(
        "event_window_samples",
        sa.Column("sample_id", sa.String(160), primary_key=True),
        sa.Column("watch_id", sa.String(128), nullable=False),
        sa.Column("event_id", sa.String(128), nullable=False),
        sa.Column("offset", sa.String(32), nullable=False),
        sa.Column("target_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_id", sa.String(128), nullable=True),
        sa.Column("payload_ref", sa.String(2048), nullable=True),
        sa.Column("payload_hash", sa.String(64), nullable=True),
        sa.Column("error_code", sa.String(128), nullable=True),
        sa.UniqueConstraint("watch_id", "offset", name="uq_event_window_samples_offset"),
    )
    op.create_index("ix_event_window_samples_watch_id", "event_window_samples", ["watch_id"])
    op.create_index("ix_event_window_samples_event_id", "event_window_samples", ["event_id"])
    op.create_index("ix_event_window_samples_target_at", "event_window_samples", ["target_at"])
    op.create_index("ix_event_window_samples_status", "event_window_samples", ["status"])


def downgrade() -> None:
    op.drop_table("event_window_samples")
    op.drop_table("event_watches")
    with op.batch_alter_table("observations") as batch:
        batch.drop_column("scheduled_at")

"""Add point-in-time macro event and vintage ledgers."""

import sqlalchemy as sa
from alembic import op

revision = "0034_macro_event_ledger"
down_revision = "0033_research_tool_query_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "macro_events",
        sa.Column("macro_event_id", sa.String(128), primary_key=True),
        sa.Column("event_id", sa.String(128), nullable=False),
        sa.Column("external_event_id", sa.String(256), nullable=False),
        sa.Column("family", sa.String(128), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_id", sa.String(128), nullable=False),
        sa.Column("source_url", sa.String(2048), nullable=True),
        sa.Column("source_authority", sa.String(32), nullable=False),
        sa.Column("unit", sa.String(32), nullable=True),
        sa.Column("prior_value", sa.Float(), nullable=True),
        sa.Column("consensus_value", sa.Float(), nullable=True),
        sa.Column("actual_value", sa.Float(), nullable=True),
        sa.Column("vintage_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revision_flag", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("surprise", sa.Float(), nullable=True),
        sa.Column("surprise_z", sa.Float(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "source_id", "external_event_id", name="uq_macro_events_source_external"
        ),
    )
    op.create_index("ix_macro_events_event_id", "macro_events", ["event_id"])
    op.create_index("ix_macro_events_family", "macro_events", ["family"])
    op.create_index("ix_macro_events_scheduled_at", "macro_events", ["scheduled_at"])
    op.create_index("ix_macro_events_source_id", "macro_events", ["source_id"])
    op.create_index("ix_macro_events_status", "macro_events", ["status"])

    op.create_table(
        "macro_event_vintages",
        sa.Column("vintage_id", sa.String(128), primary_key=True),
        sa.Column("macro_event_id", sa.String(128), nullable=False),
        sa.Column("event_id", sa.String(128), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("prior_value", sa.Float(), nullable=True),
        sa.Column("consensus_value", sa.Float(), nullable=True),
        sa.Column("actual_value", sa.Float(), nullable=True),
        sa.Column("surprise", sa.Float(), nullable=True),
        sa.Column("surprise_z", sa.Float(), nullable=True),
        sa.Column("revision_flag", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "macro_event_id", "payload_hash", name="uq_macro_event_vintages_payload"
        ),
    )
    op.create_index(
        "ix_macro_event_vintages_macro_event_id",
        "macro_event_vintages",
        ["macro_event_id"],
    )
    op.create_index(
        "ix_macro_event_vintages_event_id", "macro_event_vintages", ["event_id"]
    )
    op.create_index(
        "ix_macro_event_vintages_observed_at", "macro_event_vintages", ["observed_at"]
    )
    op.create_index(
        "ix_macro_event_vintages_payload_hash", "macro_event_vintages", ["payload_hash"]
    )


def downgrade() -> None:
    op.drop_table("macro_event_vintages")
    op.drop_table("macro_events")

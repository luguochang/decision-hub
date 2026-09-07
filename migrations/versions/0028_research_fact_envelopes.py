"""Add immutable typed research facts without rewriting historical evidence."""

import sqlalchemy as sa
from alembic import op

revision = "0028_research_fact_envelopes"
down_revision = "0027_durable_tool_budget"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "research_facts",
        sa.Column("fact_id", sa.String(128), primary_key=True),
        sa.Column("run_id", sa.String(128), nullable=False, index=True),
        sa.Column("capability_id", sa.String(128), nullable=False, index=True),
        sa.Column("evidence_id", sa.String(128), nullable=False, index=True),
        sa.Column("requirement_id", sa.String(128), nullable=False, index=True),
        sa.Column("metric_family", sa.String(128), nullable=False, index=True),
        sa.Column("field", sa.String(128), nullable=False, index=True),
        sa.Column("instrument", sa.String(128), nullable=True),
        sa.Column("venue", sa.String(128), nullable=True),
        sa.Column("value_json", sa.Text(), nullable=False),
        sa.Column("unit", sa.String(64), nullable=False),
        sa.Column("window_start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("window_end_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("event_offset", sa.String(64), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_id", sa.String(256), nullable=False, index=True),
        sa.Column("independence_group", sa.String(256), nullable=False, index=True),
        sa.Column("quality", sa.String(32), nullable=False),
        sa.Column("delay_class", sa.String(32), nullable=False),
        sa.Column("payload_schema_ref", sa.String(256), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False, index=True),
        sa.Column("attributes_json", sa.Text(), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("research_facts")

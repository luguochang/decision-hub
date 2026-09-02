"""Persist normalized research results, traces, and owner commands."""

import sqlalchemy as sa
from alembic import op

revision = "0020_research_observability"
down_revision = "0019_scheduled_research_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "research_results",
        sa.Column("run_id", sa.String(128), primary_key=True),
        sa.Column("schema_version", sa.String(64), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "research_trace_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(128), nullable=False),
        sa.Column("research_session_id", sa.String(256), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stage", sa.String(64), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("reference_type", sa.String(64), nullable=True),
        sa.Column("reference_id", sa.String(256), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("error_code", sa.String(128), nullable=True),
        sa.Column("event_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id", "sequence_no", name="uq_research_trace_sequence"),
        sa.UniqueConstraint("run_id", "event_hash", name="uq_research_trace_hash"),
    )
    op.create_index("ix_research_trace_events_run_id", "research_trace_events", ["run_id"])
    op.create_index(
        "ix_research_trace_events_research_session_id",
        "research_trace_events",
        ["research_session_id"],
    )
    op.create_table(
        "research_commands",
        sa.Column("request_id", sa.String(256), primary_key=True),
        sa.Column("run_id", sa.String(128), nullable=False),
        sa.Column("command", sa.String(32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("target_run_id", sa.String(128), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_research_commands_run_id", "research_commands", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_research_commands_run_id", table_name="research_commands")
    op.drop_table("research_commands")
    op.drop_index(
        "ix_research_trace_events_research_session_id",
        table_name="research_trace_events",
    )
    op.drop_index("ix_research_trace_events_run_id", table_name="research_trace_events")
    op.drop_table("research_trace_events")
    op.drop_table("research_results")

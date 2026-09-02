"""Persist the immutable tool budget and atomic capability reservations."""

import sqlalchemy as sa
from alembic import op

revision = "0027_durable_tool_budget"
down_revision = "0026_dsh_session_deadline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("dsh_session_links") as batch:
        batch.add_column(sa.Column("max_tool_calls", sa.Integer(), nullable=True))
        batch.add_column(
            sa.Column(
                "tool_calls_started",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
    # Preserve the already-audited execution count without modifying history.
    # Gateway-owned capability traces are the only entries counted here; DSH
    # notification mirrors use a different reference_type.
    op.execute(
        "UPDATE dsh_session_links SET tool_calls_started = ("
        "SELECT COUNT(DISTINCT reference_id) FROM research_trace_events "
        "WHERE research_trace_events.run_id = dsh_session_links.run_id "
        "AND event_type = 'tool_started' "
        "AND reference_type = 'capability_call'"
        ")"
    )
    op.create_table(
        "research_tool_call_reservations",
        sa.Column("run_id", sa.String(128), nullable=False),
        sa.Column("request_id", sa.String(256), nullable=False),
        sa.Column("research_session_id", sa.String(256), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("capability_id", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("error_json", sa.Text(), nullable=True),
        sa.Column("reserved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("run_id", "request_id"),
    )
    op.create_index(
        "ix_research_tool_call_reservations_run_status",
        "research_tool_call_reservations",
        ["run_id", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_research_tool_call_reservations_run_status",
        table_name="research_tool_call_reservations",
    )
    op.drop_table("research_tool_call_reservations")
    with op.batch_alter_table("dsh_session_links") as batch:
        batch.drop_column("tool_calls_started")
        batch.drop_column("max_tool_calls")
